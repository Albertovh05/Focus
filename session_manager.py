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

# Shell window classes that are always permitted so the system tray remains
# accessible during a session. These are the taskbar containers only.
_TRAY_CLASSES = frozenset({
    "Shell_TrayWnd",             # main Windows taskbar
    "Shell_SecondaryTrayWnd",    # taskbar on secondary monitors
    "NotifyIconOverflowWindow",  # "^" notification overflow popup
    "TopLevelWindowForOverflowXamlIsland",  # Windows 11 tray overflow host
})

# Windows that are always allowed and hidden from the app selector.
_ALWAYS_ALLOWED_CLASSES = frozenset({
    "CabinetWClass",        # File Explorer
    "ControlCenterWindow",  # Quick Settings panel (WiFi, Bluetooth, battery saver)
})
_ALWAYS_ALLOWED_PROCS   = frozenset({
    "systemsettings.exe",       # Windows Settings app
    "shellexperiencehost.exe",  # Windows shell experience host
})


def _is_tray_window(hwnd: int) -> bool:
    """Return True when hwnd belongs to the taskbar/tray window tree."""
    if not hwnd:
        return False

    candidates = [hwnd]

    try:
        for ancestor in (
            win32gui.GetAncestor(hwnd, win32con.GA_PARENT),
            win32gui.GetAncestor(hwnd, win32con.GA_ROOT),
            win32gui.GetAncestor(hwnd, win32con.GA_ROOTOWNER),
        ):
            if ancestor and ancestor not in candidates:
                candidates.append(ancestor)
    except Exception:
        pass

    try:
        owner = win32gui.GetWindow(hwnd, win32con.GW_OWNER)
        while owner and owner not in candidates:
            candidates.append(owner)
            owner = win32gui.GetWindow(owner, win32con.GW_OWNER)
    except Exception:
        pass

    try:
        parent = win32gui.GetParent(hwnd)
        while parent and parent not in candidates:
            candidates.append(parent)
            parent = win32gui.GetParent(parent)
    except Exception:
        pass

    for candidate in candidates:
        try:
            if win32gui.GetClassName(candidate) in _TRAY_CLASSES:
                return True
        except Exception:
            continue
    return False


def _is_always_allowed_window(hwnd: int) -> bool:
    """Return True for File Explorer and Windows Settings windows."""
    try:
        if win32gui.GetClassName(hwnd) in _ALWAYS_ALLOWED_CLASSES:
            return True
    except Exception:
        pass
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if psutil.Process(pid).name().lower() in _ALWAYS_ALLOWED_PROCS:
            return True
    except Exception:
        pass
    return False


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
        self._hook_thread:   threading.Thread | None = None
        self._timer_thread:  threading.Thread | None = None
        self._poll_thread:   threading.Thread | None = None
        self._lock = threading.Lock()
        self._refocus_active: bool = False

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
        self._remember_allowed_window(hwnd)

        self._hook_thread = threading.Thread(target=self._hook_loop, daemon=True)
        self._hook_thread.start()

        self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self._timer_thread.start()

        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

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
        if _is_tray_window(hwnd):
            return True
        if _is_always_allowed_window(hwnd):
            return True
        return self._is_allowed_app_window(hwnd)

    def _is_allowed_app_window(self, hwnd: int) -> bool:
        """Return True for Focus windows or user-selected app windows."""
        if not hwnd or _is_tray_window(hwnd):
            return False
        try:
            pid, proc_name = self._proc_for(hwnd)
        except Exception:
            return False
        return pid == self.own_pid or proc_name in self.allowed_procs

    def _remember_allowed_window(self, hwnd: int) -> None:
        """Track only real app windows as refocus targets, never taskbar/tray."""
        if hwnd == self._overlay_hwnd:
            return
        if not win32gui.IsWindowVisible(hwnd):
            return
        if not self._is_allowed_app_window(hwnd):
            return
        with self._lock:
            self._last_allowed_hwnd = hwnd

    def _find_allowed_hwnd(self) -> int:
        """Find a visible allowed app window to use when the tracked target is stale."""
        found: list[int] = []

        def enum_cb(hwnd, _):
            if found:
                return
            if hwnd == self._overlay_hwnd:
                return
            if not win32gui.IsWindowVisible(hwnd):
                return
            if self._is_allowed_app_window(hwnd):
                found.append(hwnd)

        try:
            win32gui.EnumWindows(enum_cb, None)
        except Exception:
            return 0
        return found[0] if found else 0

    def _win_event_cb(self, hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
        if not self.running:
            return
        if not hwnd:
            return

        if self._is_allowed(hwnd):
            # Never make the click-through overlay or any hidden window
            # (e.g. the phantom tk.Tk() root) a refocus target. Tray/taskbar
            # windows are allowed for clicks but must not become the target.
            self._remember_allowed_window(hwnd)
        else:
            # Give the OS a moment to finish its transition, then yank focus back.
            # Use _maybe_refocus to avoid stacking concurrent refocus threads.
            self._maybe_refocus()

    def _maybe_refocus(self):
        """Spawn a refocus thread only when one is not already running."""
        with self._lock:
            if self._refocus_active:
                return
            self._refocus_active = True
        threading.Thread(target=self._refocus, daemon=True).start()

    def _refocus(self):
        try:
            time.sleep(0.05)
            if not self.running:
                return
            with self._lock:
                target = self._last_allowed_hwnd
            if (
                not target
                or not win32gui.IsWindow(target)
                or not win32gui.IsWindowVisible(target)
                or not self._is_allowed_app_window(target)
            ):
                # Target became hidden (e.g. Focus window withdrawn to tray).
                # Fall back to another visible allowed window if one exists.
                target = self._find_allowed_hwnd()
                with self._lock:
                    self._last_allowed_hwnd = target
                if not target:
                    return
            self._do_refocus(target)
        finally:
            with self._lock:
                self._refocus_active = False

    def _do_refocus(self, target: int) -> None:
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

    def _poll_loop(self):
        """Backup enforcer: polls the foreground window every 250 ms.

        Newly-launched apps can call SetForegroundWindow during their own
        initialisation and steal focus back after our WinEvent-triggered
        refocus has already run. This poll catches those cases that the
        event hook misses or that occur between hook deliveries.
        """
        while self.running:
            time.sleep(0.25)
            if not self.running:
                break
            try:
                fg = win32gui.GetForegroundWindow()
            except Exception:
                continue
            if not fg:
                continue
            if self._is_allowed(fg):
                self._remember_allowed_window(fg)
            else:
                self._maybe_refocus()

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
            # Tray/taskbar, File Explorer, and Settings are always allowed and
            # should not appear in the user-facing app selector.
            if _is_tray_window(hwnd) or _is_always_allowed_window(hwnd):
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
