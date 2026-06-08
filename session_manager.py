"""
Window enforcement logic — cross-platform (Windows + macOS).
"""
import os
import sys
import threading
import time
from datetime import datetime

import psutil

_WINDOWS = sys.platform == 'win32'
_DARWIN  = sys.platform == 'darwin'

# ── Windows-only imports ──────────────────────────────────────────────────────
if _WINDOWS:
    import ctypes
    import ctypes.wintypes
    import win32gui
    import win32process
    import win32con

    EVENT_SYSTEM_FOREGROUND = 0x0003
    WINEVENT_OUTOFCONTEXT   = 0x0000
    VK_MENU  = 0x12
    VK_LMENU = 0xA4
    VK_RMENU = 0xA5

    _TRAY_CLASSES = frozenset({
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
        "NotifyIconOverflowWindow",
        "TopLevelWindowForOverflowXamlIsland",
    })
    _ALWAYS_ALLOWED_CLASSES = frozenset({
        "CabinetWClass",
        "ControlCenterWindow",
    })
    _ALWAYS_ALLOWED_PROCS = frozenset({
        "systemsettings.exe",
        "shellexperiencehost.exe",
    })

# ── macOS-only imports ────────────────────────────────────────────────────────
_HAS_PYOBJC = False
if _DARWIN:
    try:
        from AppKit import (                          # type: ignore[import]
            NSWorkspace, NSRunningApplication,
            NSApplicationActivateIgnoringOtherApps,
        )
        from Quartz import (                          # type: ignore[import]
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionOnScreenOnly,
            kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        )
        _HAS_PYOBJC = True
    except ImportError:
        pass

    _MAC_SKIP_OWNERS = frozenset({
        'Dock', 'WindowServer', 'SystemUIServer', 'loginwindow',
        'Control Center', 'NotificationCenter', 'Spotlight',
    })
    _MAC_SYSTEM_PROCS = frozenset({
        'dock', 'systemuiserver', 'loginwindow', 'windowserver',
        'controlcenter', 'notificationcenter', 'spotlight',
        'finder',  # always allow Finder (equivalent to File Explorer)
    })


# ── Windows helper functions ──────────────────────────────────────────────────

def _is_tray_window(hwnd: int) -> bool:
    if not _WINDOWS or not hwnd:
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
    if not _WINDOWS:
        return False
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


def _is_window_switch_in_progress() -> bool:
    if not _WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        return any(
            user32.GetAsyncKeyState(vk) & 0x8000
            for vk in (VK_MENU, VK_LMENU, VK_RMENU)
        )
    except Exception:
        return False


# ── Shared base ───────────────────────────────────────────────────────────────

class _BaseSessionManager:
    def __init__(self, allowed_procs: set[str], own_pid: int,
                 overlay_hwnd: int = 0, on_tick=None, on_blocked_attempt=None):
        self.allowed_procs = {p.lower() for p in allowed_procs}
        self.own_pid       = own_pid
        self.on_tick       = on_tick
        self.on_blocked_attempt = on_blocked_attempt
        self.running       = False
        self.start_dt: datetime | None = None
        self.end_dt:   datetime | None = None
        self._overlay_hwnd = overlay_hwnd
        self._lock         = threading.Lock()
        self._timer_thread: threading.Thread | None = None
        self._emergency_until: float = 0.0

    def start_emergency_pass(self, seconds: int) -> None:
        self._emergency_until = max(self._emergency_until, time.monotonic() + seconds)

    def emergency_pass_active(self) -> bool:
        return time.monotonic() < self._emergency_until

    def stop(self):
        self.end_dt  = datetime.now()
        self.running = False

    def _timer_loop(self):
        while self.running:
            time.sleep(1)
            if self.running and self.on_tick and self.start_dt:
                elapsed = int((datetime.now() - self.start_dt).total_seconds())
                try:
                    self.on_tick(elapsed)
                except Exception:
                    pass

    @staticmethod
    def get_open_windows() -> list[dict]:
        raise NotImplementedError


# ── Windows implementation ────────────────────────────────────────────────────

class _WindowsSessionManager(_BaseSessionManager):
    def __init__(self, allowed_procs: set[str], own_pid: int,
                 overlay_hwnd: int = 0, on_tick=None, on_blocked_attempt=None):
        super().__init__(allowed_procs, own_pid, overlay_hwnd, on_tick, on_blocked_attempt)
        self._last_allowed_hwnd: int = 0
        self._hook              = None
        self._hook_thread:  threading.Thread | None = None
        self._poll_thread:  threading.Thread | None = None
        self._refocus_active: bool = False
        self._last_blocked_hwnd: int = 0
        self._last_blocked_time: float = 0.0

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

    def start(self):
        self.running  = True
        self.start_dt = datetime.now()

        hwnd = win32gui.GetForegroundWindow()
        self._remember_allowed_window(hwnd)

        self._hook_thread = threading.Thread(target=self._hook_loop, daemon=True)
        self._hook_thread.start()
        self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self._timer_thread.start()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _proc_for(self, hwnd: int) -> tuple[int, str]:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid, psutil.Process(pid).name().lower()

    def _is_allowed(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        if self.emergency_pass_active():
            return True
        if _is_tray_window(hwnd):
            return True
        if _is_always_allowed_window(hwnd):
            return True
        return self._is_allowed_app_window(hwnd)

    def _is_allowed_app_window(self, hwnd: int) -> bool:
        if not hwnd or _is_tray_window(hwnd):
            return False
        try:
            pid, proc_name = self._proc_for(hwnd)
        except Exception:
            return False
        return pid == self.own_pid or proc_name in self.allowed_procs

    def _remember_allowed_window(self, hwnd: int) -> None:
        if hwnd == self._overlay_hwnd:
            return
        if not win32gui.IsWindowVisible(hwnd):
            return
        if not self._is_allowed_app_window(hwnd):
            return
        with self._lock:
            self._last_allowed_hwnd = hwnd

    def _find_allowed_hwnd(self) -> int:
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

    def _win_event_cb(self, hWinEventHook, event, hwnd,
                      idObject, idChild, dwEventThread, dwmsEventTime):
        if not self.running or not hwnd:
            return
        if self._is_allowed(hwnd):
            self._remember_allowed_window(hwnd)
        elif _is_window_switch_in_progress():
            return
        else:
            self._notify_blocked_attempt(hwnd)
            self._maybe_refocus()

    def _notify_blocked_attempt(self, hwnd: int) -> None:
        if not self.on_blocked_attempt:
            return
        now = time.monotonic()
        with self._lock:
            if hwnd == self._last_blocked_hwnd and now - self._last_blocked_time < 1.5:
                return
            self._last_blocked_hwnd = hwnd
            self._last_blocked_time = now
        try:
            pid, proc_name = self._proc_for(hwnd)
            title = win32gui.GetWindowText(hwnd)
        except Exception:
            pid, proc_name, title = 0, "unknown", ""
        try:
            self.on_blocked_attempt({"hwnd": hwnd, "pid": pid, "process": proc_name, "title": title})
        except Exception:
            pass

    def _maybe_refocus(self):
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
            if _is_window_switch_in_progress():
                return
            try:
                fg = win32gui.GetForegroundWindow()
            except Exception:
                fg = 0
            if fg and self._is_allowed(fg):
                self._remember_allowed_window(fg)
                return
            with self._lock:
                target = self._last_allowed_hwnd
            if (
                not target
                or not win32gui.IsWindow(target)
                or not win32gui.IsWindowVisible(target)
                or not self._is_allowed_app_window(target)
            ):
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
            elif _is_window_switch_in_progress():
                continue
            else:
                self._notify_blocked_attempt(fg)
                self._maybe_refocus()

    def _hook_loop(self):
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

    @staticmethod
    def get_open_windows() -> list[dict]:
        results = []

        def enum_cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return
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
        seen = set()
        unique = []
        for w in results:
            key = w["title"]
            if key not in seen:
                seen.add(key)
                unique.append(w)
        return sorted(unique, key=lambda x: x["title"].lower())


# ── macOS implementation ──────────────────────────────────────────────────────

class _MacSessionManager(_BaseSessionManager):
    def __init__(self, allowed_procs: set[str], own_pid: int,
                 overlay_hwnd: int = 0, on_tick=None, on_blocked_attempt=None):
        super().__init__(allowed_procs, own_pid, overlay_hwnd, on_tick, on_blocked_attempt)
        self._last_allowed_pid: int = 0
        self._poll_thread:  threading.Thread | None = None
        self._refocus_active: bool = False
        self._last_blocked_pid: int = 0
        self._last_blocked_time: float = 0.0

    def start(self):
        self.running  = True
        self.start_dt = datetime.now()

        pid = self._get_foreground_pid()
        if pid:
            self._remember_allowed_pid(pid)

        self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self._timer_thread.start()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _get_foreground_pid(self) -> int:
        if not _HAS_PYOBJC:
            return 0
        try:
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return app.processIdentifier() if app else 0
        except Exception:
            return 0

    def _get_app_display_name(self, pid: int) -> str:
        if not _HAS_PYOBJC:
            try:
                return psutil.Process(pid).name().lower()
            except Exception:
                return ''
        try:
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app:
                return (app.localizedName() or '').lower()
        except Exception:
            pass
        try:
            return psutil.Process(pid).name().lower()
        except Exception:
            return ''

    def _is_system_pid(self, pid: int) -> bool:
        try:
            name = psutil.Process(pid).name().lower()
            if name in _MAC_SYSTEM_PROCS:
                return True
        except Exception:
            pass
        return False

    def _is_allowed_pid(self, pid: int) -> bool:
        if not pid:
            return False
        if self.emergency_pass_active():
            return True
        if pid == self.own_pid:
            return True
        if self._is_system_pid(pid):
            return True
        display_name = self._get_app_display_name(pid)
        return bool(display_name and display_name in self.allowed_procs)

    def _notify_blocked_attempt(self, pid: int) -> None:
        if not self.on_blocked_attempt:
            return
        now = time.monotonic()
        with self._lock:
            if pid == self._last_blocked_pid and now - self._last_blocked_time < 1.5:
                return
            self._last_blocked_pid = pid
            self._last_blocked_time = now
        try:
            name = psutil.Process(pid).name()
        except Exception:
            name = "unknown"
        try:
            self.on_blocked_attempt({"pid": pid, "process": name, "title": name})
        except Exception:
            pass

    def _remember_allowed_pid(self, pid: int) -> None:
        if not pid or pid == self.own_pid:
            return
        if not self._is_allowed_pid(pid):
            return
        with self._lock:
            self._last_allowed_pid = pid

    def _find_allowed_pid(self) -> int:
        if not _HAS_PYOBJC:
            return 0
        try:
            for app in NSWorkspace.sharedWorkspace().runningApplications():
                pid = app.processIdentifier()
                if pid and self._is_allowed_pid(pid):
                    return pid
        except Exception:
            pass
        return 0

    def _maybe_refocus(self):
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
            fg = self._get_foreground_pid()
            if fg and self._is_allowed_pid(fg):
                self._remember_allowed_pid(fg)
                return
            with self._lock:
                target = self._last_allowed_pid
            if not target or not self._is_pid_running(target):
                target = self._find_allowed_pid()
                with self._lock:
                    self._last_allowed_pid = target
                if not target:
                    return
            self._do_refocus(target)
        finally:
            with self._lock:
                self._refocus_active = False

    def _is_pid_running(self, pid: int) -> bool:
        try:
            return psutil.pid_exists(pid)
        except Exception:
            return False

    def _do_refocus(self, target_pid: int) -> None:
        if not _HAS_PYOBJC:
            return
        try:
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(target_pid)
            if app and not app.isTerminated():
                app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        except Exception:
            pass

    def _poll_loop(self):
        while self.running:
            time.sleep(0.25)
            if not self.running:
                break
            pid = self._get_foreground_pid()
            if not pid:
                continue
            if self._is_allowed_pid(pid):
                self._remember_allowed_pid(pid)
            else:
                self._notify_blocked_attempt(pid)
                self._maybe_refocus()

    @staticmethod
    def get_open_windows() -> list[dict]:
        if not _HAS_PYOBJC:
            return _MacSessionManager._get_windows_psutil()

        window_list = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        )

        seen: dict[int, dict] = {}  # pid -> entry

        for win in window_list:
            pid   = win.get('kCGWindowOwnerPID', 0)
            owner = win.get('kCGWindowOwnerName') or ''
            title = win.get('kCGWindowName') or ''
            layer = win.get('kCGWindowLayer', 100)

            if not pid or not owner:
                continue
            if owner in _MAC_SKIP_OWNERS:
                continue
            if layer not in (0, 3):  # normal + floating windows only
                continue

            if pid not in seen:
                seen[pid] = {
                    'hwnd': pid,
                    'title': title or owner,
                    'pid': pid,
                    'process': owner,
                }
            elif title and not seen[pid]['title']:
                seen[pid]['title'] = title

        # Deduplicate by app display name (one entry per app)
        by_name: dict[str, dict] = {}
        for entry in seen.values():
            key = entry['process'].lower()
            if key not in by_name:
                by_name[key] = entry

        return sorted(by_name.values(), key=lambda x: x['title'].lower())

    @staticmethod
    def _get_windows_psutil() -> list[dict]:
        """Fallback when pyobjc is unavailable: list processes with open windows via psutil."""
        seen: dict[str, dict] = {}
        for proc in psutil.process_iter(['pid', 'name', 'status']):
            try:
                if proc.info['status'] not in (psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING):
                    continue
                name = proc.info['name'] or 'unknown'
                key  = name.lower()
                if key not in seen:
                    seen[key] = {
                        'hwnd': proc.info['pid'],
                        'title': name,
                        'pid': proc.info['pid'],
                        'process': name,
                    }
            except Exception:
                pass
        return sorted(seen.values(), key=lambda x: x['title'].lower())


# ── Public alias ──────────────────────────────────────────────────────────────

if _WINDOWS:
    SessionManager = _WindowsSessionManager
elif _DARWIN:
    SessionManager = _MacSessionManager
else:
    SessionManager = _MacSessionManager  # Linux fallback uses Mac implementation
