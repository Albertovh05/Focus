"""
Site blocking during Focus sessions — cross-platform (Windows + macOS).

Two layers:
1. Hosts file — appends 127.0.0.1 entries at session start and removes them at
   session end.  Works for every browser.  Requires admin/root.

2. Title watcher — on Windows: hooks EVENT_OBJECT_NAMECHANGE via WinEvent and
   sends Ctrl+W when a Chromium tab navigates to a blocked domain.
   On macOS: polls the frontmost browser window title via osascript every 500ms
   and sends Cmd+W to close the offending tab.
"""
import os
import re
import subprocess
import sys
import threading
import time

import keyboard
import psutil

_WINDOWS = sys.platform == 'win32'
_DARWIN  = sys.platform == 'darwin'

# ── Hosts file paths ──────────────────────────────────────────────────────────

if _WINDOWS:
    HOSTS_FILE = r"C:\Windows\System32\drivers\etc\hosts"
else:
    HOSTS_FILE = "/etc/hosts"

_SENTINEL_START = "# <<Focus-block-start>>"
_SENTINEL_END   = "# <<Focus-block-end>>"

# ── Windows-only imports ──────────────────────────────────────────────────────

if _WINDOWS:
    import ctypes
    import ctypes.wintypes
    import win32gui
    import win32process

    EVENT_OBJECT_NAMECHANGE = 0x800C
    WINEVENT_OUTOFCONTEXT   = 0x0000
    _CHROMIUM_CLASS = "Chrome_WidgetWin_1"
    _CHROMIUM_PROCS = {"chrome.exe", "msedge.exe", "brave.exe", "opera.exe", "vivaldi.exe"}

# ── macOS-only imports ────────────────────────────────────────────────────────

_HAS_PYOBJC = False
if _DARWIN:
    try:
        from AppKit import NSWorkspace  # type: ignore[import]
        _HAS_PYOBJC = True
    except ImportError:
        pass

    # AppleScript templates for title/close per browser
    _MAC_BROWSER_TITLE_SCRIPTS: dict[str, str] = {
        'Google Chrome':   'tell application "Google Chrome" to get title of active tab of front window',
        'Brave Browser':   'tell application "Brave Browser" to get title of active tab of front window',
        'Microsoft Edge':  'tell application "Microsoft Edge" to get title of active tab of front window',
        'Safari':          'tell application "Safari" to get name of current tab of front window',
        'Firefox':         'tell application "Firefox" to get title of front window',
        'Opera':           'tell application "Opera" to get title of front window',
        'Vivaldi':         'tell application "Vivaldi" to get title of front window',
    }
    _MAC_BROWSER_CLOSE_SCRIPTS: dict[str, str] = {
        'Google Chrome':   'tell application "Google Chrome" to close (active tab of front window)',
        'Brave Browser':   'tell application "Brave Browser" to close (active tab of front window)',
        'Microsoft Edge':  'tell application "Microsoft Edge" to close (active tab of front window)',
        'Safari':          'tell application "Safari" to close current tab of front window',
    }


# ── Admin / root check ────────────────────────────────────────────────────────

def _is_admin() -> bool:
    if _WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    else:
        return os.geteuid() == 0


# ── DNS flush ─────────────────────────────────────────────────────────────────

def _flush_dns() -> None:
    if _WINDOWS:
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, check=False)
    elif _DARWIN:
        subprocess.run(["dscacheutil", "-flushcache"], capture_output=True, check=False)
        subprocess.run(["killall", "-HUP", "mDNSResponder"], capture_output=True, check=False)


# ── Hosts file helpers ────────────────────────────────────────────────────────

def _write_hosts(domains: list[str]) -> bool:
    lines = [_SENTINEL_START]
    for d in domains:
        lines.append(f"127.0.0.1 {d}")
        if not d.startswith("www."):
            lines.append(f"127.0.0.1 www.{d}")
    lines.append(_SENTINEL_END)
    try:
        with open(HOSTS_FILE, "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(lines) + "\n")
        _flush_dns()
        return True
    except Exception:
        return False


def _remove_hosts() -> None:
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
        _flush_dns()
    except Exception:
        pass


# ── Domain-in-title matching (shared) ────────────────────────────────────────

def _domain_in_title(title: str, domains: list[str]) -> bool:
    """
    Return True when the window title suggests a blocked domain is active.
    Only matches at the very start or end of the title.
    """
    normalized = re.sub(r'^\s*[\(\[]\d+[\)\]]\s*', '', title.lower().strip())
    _SEP = r'\s*[\-\|·/—–:]+\s*'
    for domain in domains:
        parts = domain.split(".")
        sld = parts[-2] if len(parts) >= 2 else domain
        if len(sld) <= 2:
            continue
        escaped  = re.escape(sld)
        at_start = bool(re.match(r'^' + escaped + r'(?:' + _SEP + r'|$)', normalized))
        at_end   = bool(re.search(r'(?:^|' + _SEP + r')' + escaped + r'\s*$', normalized))
        if at_start or at_end:
            return True
    return False


# ── Windows-only: foreground Chromium check ───────────────────────────────────

def _is_chromium_foreground_windows() -> bool:
    try:
        fg = win32gui.GetForegroundWindow()
        if not fg:
            return False
        _, pid = win32process.GetWindowThreadProcessId(fg)
        return psutil.Process(pid).name().lower() in _CHROMIUM_PROCS
    except Exception:
        return False


# ── Main class ────────────────────────────────────────────────────────────────

class SiteBlocker:
    def __init__(self, domains: list[str]):
        self._domains: list[str] = [self._normalise(d) for d in domains if d.strip()]
        self._running        = False
        self._hosts_written  = False
        self._hook_thread: threading.Thread | None = None
        self._last_close_time: float = 0.0
        self._close_lock = threading.Lock()

        if _WINDOWS:
            self._hook     = None
            self._hook_tid = 0
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
        d = domain.lower().strip()
        for prefix in ("https://", "http://", "www."):
            if d.startswith(prefix):
                d = d[len(prefix):]
        return d.rstrip("/").split("/")[0]

    def start(self) -> bool:
        if not self._domains:
            return True
        if _is_admin():
            self._hosts_written = _write_hosts(self._domains)
        self._running = True
        if _WINDOWS:
            self._hook_thread = threading.Thread(target=self._hook_loop_windows, daemon=True)
        else:
            self._hook_thread = threading.Thread(target=self._poll_loop_mac, daemon=True)
        self._hook_thread.start()
        return self._hosts_written

    def stop(self) -> None:
        self._running = False
        if self._hosts_written:
            _remove_hosts()
            self._hosts_written = False
        if _WINDOWS and self._hook_tid:
            ctypes.windll.user32.PostThreadMessageW(self._hook_tid, 0x0012, 0, 0)  # WM_QUIT

    # ── Windows: WinEvent title watcher ──────────────────────────────────────

    def _win_event_cb(self, hHook, event, hwnd, idObject, idChild, dwThread, dwTime):
        if not self._running or not hwnd:
            return
        if idObject != 0:
            return
        try:
            if win32gui.GetClassName(hwnd) != _CHROMIUM_CLASS:
                return
            title = win32gui.GetWindowText(hwnd)
            if title and _domain_in_title(title, self._domains):
                threading.Thread(
                    target=self._close_tab_windows, args=(hwnd,), daemon=True
                ).start()
        except Exception:
            pass

    def _close_tab_windows(self, hwnd: int) -> None:
        time.sleep(0.1)
        if not self._running:
            return
        try:
            current_title = win32gui.GetWindowText(hwnd)
            if not _domain_in_title(current_title, self._domains):
                return
            if _is_chromium_foreground_windows():
                with self._close_lock:
                    now = time.monotonic()
                    if now - self._last_close_time < 1.0:
                        return
                    self._last_close_time = now
                keyboard.send("ctrl+w")
        except Exception:
            pass

    def _hook_loop_windows(self) -> None:
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

    # ── macOS: polling title watcher ──────────────────────────────────────────

    def _get_frontmost_browser_mac(self) -> str | None:
        """Return the localizedName of the frontmost browser, or None."""
        if not _HAS_PYOBJC:
            return None
        try:
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if not app:
                return None
            name = app.localizedName() or ''
            if name in _MAC_BROWSER_TITLE_SCRIPTS:
                return name
        except Exception:
            pass
        return None

    def _get_browser_tab_title_mac(self, browser_name: str) -> str:
        script = _MAC_BROWSER_TITLE_SCRIPTS.get(browser_name)
        if not script:
            return ''
        try:
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip() if result.returncode == 0 else ''
        except Exception:
            return ''

    def _close_tab_mac(self, browser_name: str) -> None:
        time.sleep(0.1)
        if not self._running:
            return
        # Verify the blocked domain is still active
        title = self._get_browser_tab_title_mac(browser_name)
        if not title or not _domain_in_title(title, self._domains):
            return
        with self._close_lock:
            now = time.monotonic()
            if now - self._last_close_time < 1.0:
                return
            self._last_close_time = now
        close_script = _MAC_BROWSER_CLOSE_SCRIPTS.get(browser_name)
        if close_script:
            try:
                subprocess.run(['osascript', '-e', close_script],
                               capture_output=True, timeout=2)
            except Exception:
                pass
        else:
            try:
                keyboard.send("command+w")
            except Exception:
                pass

    def _poll_loop_mac(self) -> None:
        while self._running:
            time.sleep(0.5)
            if not self._running:
                break
            browser = self._get_frontmost_browser_mac()
            if not browser:
                continue
            try:
                title = self._get_browser_tab_title_mac(browser)
                if title and _domain_in_title(title, self._domains):
                    threading.Thread(
                        target=self._close_tab_mac, args=(browser,), daemon=True
                    ).start()
            except Exception:
                pass
