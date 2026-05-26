"""
Window enforcement logic.

Hooks into foreground-window change events via a WinEvent hook and
forces focus back to the last allowed window if the user switches
to a disallowed one.
"""
import os
import threading
import ctypes
import ctypes.wintypes
import win32gui
import win32process
import win32con
import psutil
import time
from datetime import datetime


# ── WinEvent constants ──────────────────────────────────────────────────────
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT   = 0x0000


class SessionManager:
    def __init__(self, allowed_procs: set[str], own_pid: int,
                 overlay_hwnd: int = 0, on_tick=None):
        """
        allowed_procs: set of lowercased process names that are allowed
                       (e.g. {"brave.exe", "chrome.exe"}). Any window belonging
                       to one of these processes is permitted, so every tab and
                       every window of an allowed app stays usable.
        own_pid:       pid of the Focus app itself; its windows (main window and
                       exit dialog) are always allowed so the session can be ended.
        overlay_hwnd:  the timer overlay's window handle; never tracked as a
                       refocus target since it is click-through and topmost.
        on_tick:       callback(elapsed_seconds) called every second.
        """
        self.allowed_procs    = {p.lower() for p in allowed_procs}
        self.own_pid          = own_pid
        self.on_tick          = on_tick
        self.running          = False
        self.start_dt: datetime | None = None
        self.end_dt:   datetime | None = None

        self._overlay_hwnd       = overlay_hwnd

        self._last_allowed_hwnd: int = 0
        self._hook               = None
        self._hook_thread: threading.Thread | None = None
        self._timer_thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # WinEvent callback must be kept alive
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

    # ── public API ────────────────────────────────────────────────────────────

    def start(self):
        self.running  = True
        self.start_dt = datetime.now()

        # The Focus app's own windows are always allowed (via own_pid), so the
        # user can reach the app and exit dialog to end the session.

        # Seed last_allowed_hwnd with the current foreground window if allowed
        hwnd = win32gui.GetForegroundWindow()
        if self._is_allowed(hwnd):
            self._last_allowed_hwnd = hwnd

        self._hook_thread = threading.Thread(target=self._hook_loop, daemon=True)
        self._hook_thread.start()

        self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self._timer_thread.start()

    def stop(self):
        self.end_dt  = datetime.now()
        self.running = False
        # Unhook happens naturally when the message loop exits

    # ── internal ──────────────────────────────────────────────────────────────

    def _proc_for(self, hwnd: int) -> tuple[int, str]:
        """Resolve a window handle to (pid, lowercased process name)."""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid, psutil.Process(pid).name().lower()

    def _is_allowed(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        try:
            pid, proc_name = self._proc_for(hwnd)
        except Exception:
            return False

        return pid == self.own_pid or proc_name in self.allowed_procs

    def _win_event_cb(self, hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
        if not self.running:
            return
        if not hwnd:
            return

        if self._is_allowed(hwnd):
            # Never make the click-through overlay or any hidden window
            # (e.g. the phantom tk.Tk() root) a refocus target.
            if hwnd != self._overlay_hwnd and win32gui.IsWindowVisible(hwnd):
                with self._lock:
                    self._last_allowed_hwnd = hwnd
        else:
            # Give the OS a moment to finish its transition, then yank focus back
            threading.Thread(target=self._refocus, daemon=True).start()

    def _refocus(self):
        time.sleep(0.05)
        if not self.running:
            return
        with self._lock:
            target = self._last_allowed_hwnd
        if not target or not win32gui.IsWindow(target):
            return
        if not win32gui.IsWindowVisible(target):
            # Target became hidden (e.g. Focus window withdrawn to tray).
            # Clear the stale reference; the next allowed-window focus will refresh it.
            with self._lock:
                self._last_allowed_hwnd = 0
            return
        try:
            cur_tid    = ctypes.windll.kernel32.GetCurrentThreadId()
            fg_hwnd    = win32gui.GetForegroundWindow()
            fg_tid, _  = win32process.GetWindowThreadProcessId(fg_hwnd)
            tgt_tid, _ = win32process.GetWindowThreadProcessId(target)

            # Borrow foreground rights from the current foreground thread
            ctypes.windll.user32.AttachThreadInput(cur_tid, fg_tid, True)
            ctypes.windll.user32.AttachThreadInput(cur_tid, tgt_tid, True)

            win32gui.ShowWindow(target, win32con.SW_SHOW)
            win32gui.SetForegroundWindow(target)
            win32gui.BringWindowToTop(target)

            ctypes.windll.user32.AttachThreadInput(cur_tid, fg_tid, False)
            ctypes.windll.user32.AttachThreadInput(cur_tid, tgt_tid, False)
        except Exception:
            pass

    def _hook_loop(self):
        """Run a Windows message loop with the WinEvent hook."""
        user32 = ctypes.windll.user32
        hook = user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND,
            EVENT_SYSTEM_FOREGROUND,
            0,
            self._callback,
            0,
            0,
            WINEVENT_OUTOFCONTEXT,
        )
        self._hook = hook

        msg = ctypes.wintypes.MSG()
        while self.running:
            bRet = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if bRet == 0 or bRet == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if hook:
            user32.UnhookWinEvent(hook)

    def _timer_loop(self):
        while self.running:
            time.sleep(1)
            if self.running and self.on_tick and self.start_dt:
                elapsed = int((datetime.now() - self.start_dt).total_seconds())
                try:
                    self.on_tick(elapsed)
                except Exception:
                    pass

    # ── utility ───────────────────────────────────────────────────────────────

    @staticmethod
    def get_open_windows() -> list[dict]:
        """Return list of {hwnd, title, pid, process} for visible, titled windows."""
        results = []

        def enum_cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc = psutil.Process(pid)
                name = proc.name()
            except Exception:
                name = "unknown"
            results.append({"hwnd": hwnd, "title": title, "pid": pid, "process": name})

        win32gui.EnumWindows(enum_cb, None)
        # Deduplicate by title (keep first occurrence)
        seen = set()
        unique = []
        for w in results:
            key = w["title"]
            if key not in seen:
                seen.add(key)
                unique.append(w)
        return sorted(unique, key=lambda x: x["title"].lower())
