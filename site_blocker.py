"""
Site blocking during Focus sessions.

Two layers:
1. Hosts file — appends 127.0.0.1 entries at session start and removes them at
   session end.  Works for every browser.  Requires admin; silently skipped if
   the process is not elevated.

2. Title watcher — hooks EVENT_OBJECT_NAMECHANGE via a WinEvent hook and checks
   whether a Chromium window navigated to a blocked domain (by matching the
   domain's second-level name against the window title).  When a match is found
   and a Chromium window is still in the foreground, Ctrl+W closes the active
   tab.  Works for Chrome, Edge, Brave — no admin required.
"""
import ctypes
import ctypes.wintypes
import re
import subprocess
import threading
import time

import keyboard
import psutil
import win32gui
import win32process

HOSTS_FILE = r"C:\Windows\System32\drivers\etc\hosts"
_SENTINEL_START = "# <<Focus-block-start>>"
_SENTINEL_END   = "# <<Focus-block-end>>"

EVENT_OBJECT_NAMECHANGE = 0x800C
WINEVENT_OUTOFCONTEXT   = 0x0000

_CHROMIUM_CLASS = "Chrome_WidgetWin_1"
_CHROMIUM_PROCS = {"chrome.exe", "msedge.exe", "brave.exe", "opera.exe", "vivaldi.exe"}


# ── helpers ───────────────────────────────────────────────────────────────────

def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _write_hosts(domains: list[str]) -> bool:
    """Append blocked-domain entries wrapped in sentinel comments. Returns True on success."""
    lines = [_SENTINEL_START]
    for d in domains:
        lines.append(f"127.0.0.1 {d}")
        if not d.startswith("www."):
            lines.append(f"127.0.0.1 www.{d}")
    lines.append(_SENTINEL_END)
    try:
        with open(HOSTS_FILE, "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(lines) + "\n")
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, check=False)
        return True
    except Exception:
        return False


def _remove_hosts() -> None:
    """Strip all Focus-injected sentinel blocks from the hosts file."""
    try:
        with open(HOSTS_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        cleaned = re.sub(
            rf"\n?{re.escape(_SENTINEL_START)}.*?{re.escape(_SENTINEL_END)}\n?",
            "",
            content,
            flags=re.DOTALL,
        )
        with open(HOSTS_FILE, "w", encoding="utf-8") as f:
            f.write(cleaned)
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, check=False)
    except Exception:
        pass


def _domain_in_title(title: str, domains: list[str]) -> bool:
    """
    Return True when the window title suggests a blocked domain is active.

    Maps each domain to its second-level label (e.g. "youtube.com" → "youtube")
    and checks whether that label appears anywhere in the title (case-insensitive).
    Short labels (≤2 chars, like "x" or "fb") are skipped to avoid false positives;
    the hosts-file layer still blocks those domains.
    """
    title_lower = title.lower()
    for domain in domains:
        parts = domain.split(".")
        sld = parts[-2] if len(parts) >= 2 else domain
        if len(sld) > 2 and sld in title_lower:
            return True
    return False


def _is_chromium_foreground() -> bool:
    """Return True when the current foreground window belongs to a Chromium browser."""
    try:
        fg = win32gui.GetForegroundWindow()
        if not fg:
            return False
        _, pid = win32process.GetWindowThreadProcessId(fg)
        return psutil.Process(pid).name().lower() in _CHROMIUM_PROCS
    except Exception:
        return False


# ── main class ────────────────────────────────────────────────────────────────

class SiteBlocker:
    def __init__(self, domains: list[str]):
        self._domains: list[str] = [self._normalise(d) for d in domains if d.strip()]
        self._running        = False
        self._hosts_written  = False
        self._hook           = None
        self._hook_tid: int  = 0
        self._hook_thread: threading.Thread | None = None

        WinEventProc = ctypes.WINFUNCTYPE(
            None,
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.HWND,
            ctypes.wintypes.LONG,
            ctypes.wintypes.LONG,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.DWORD,
        )
        self._callback = WinEventProc(self._win_event_cb)

    @staticmethod
    def _normalise(domain: str) -> str:
        """Strip scheme, www prefix, and trailing path/slash; lowercase."""
        d = domain.lower().strip()
        for prefix in ("https://", "http://", "www."):
            if d.startswith(prefix):
                d = d[len(prefix):]
        return d.rstrip("/").split("/")[0]

    def start(self) -> bool:
        """
        Start blocking.  Returns True if the hosts file was written successfully
        (requires admin).  The title-watcher hook is always started regardless.
        """
        if not self._domains:
            return True
        if _is_admin():
            self._hosts_written = _write_hosts(self._domains)
        self._running = True
        self._hook_thread = threading.Thread(target=self._hook_loop, daemon=True)
        self._hook_thread.start()
        return self._hosts_written

    def stop(self) -> None:
        self._running = False
        if self._hosts_written:
            _remove_hosts()
            self._hosts_written = False
        # Unblock the GetMessageW call so the hook thread can exit cleanly
        if self._hook_tid:
            ctypes.windll.user32.PostThreadMessageW(self._hook_tid, 0x0012, 0, 0)  # WM_QUIT

    # ── WinEvent callback ─────────────────────────────────────────────────────

    def _win_event_cb(self, hHook, event, hwnd, idObject, idChild, dwThread, dwTime):
        if not self._running or not hwnd:
            return
        if idObject != 0:  # Only OBJID_WINDOW (window title) changes
            return
        try:
            if win32gui.GetClassName(hwnd) != _CHROMIUM_CLASS:
                return
            title = win32gui.GetWindowText(hwnd)
            if title and _domain_in_title(title, self._domains):
                threading.Thread(
                    target=self._close_active_tab, args=(hwnd,), daemon=True
                ).start()
        except Exception:
            pass

    def _close_active_tab(self, hwnd: int) -> None:
        """After a brief settling delay, close the tab if the blocked site is still active."""
        time.sleep(0.1)
        if not self._running:
            return
        try:
            # Verify the title still matches (user may have navigated away in 100 ms)
            current_title = win32gui.GetWindowText(hwnd)
            if not _domain_in_title(current_title, self._domains):
                return
            # Only send Ctrl+W if a Chromium window is still in the foreground
            if _is_chromium_foreground():
                keyboard.send("ctrl+w")
        except Exception:
            pass

    # ── hook message loop ─────────────────────────────────────────────────────

    def _hook_loop(self) -> None:
        self._hook_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        user32 = ctypes.windll.user32
        hook = user32.SetWinEventHook(
            EVENT_OBJECT_NAMECHANGE,
            EVENT_OBJECT_NAMECHANGE,
            0,
            self._callback,
            0, 0,
            WINEVENT_OUTOFCONTEXT,
        )
        self._hook = hook
        msg = ctypes.wintypes.MSG()
        while self._running:
            bRet = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if bRet == 0 or bRet == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if hook:
            user32.UnhookWinEvent(hook)
