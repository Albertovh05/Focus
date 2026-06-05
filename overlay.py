"""
Always-on-top draggable timer overlay shown during a session.

Click-through is on by default (Ctrl+Shift+M toggles drag mode).
"""
import sys
import ctypes
import tkinter as tk
from tkinter import font as tkfont

_WINDOWS = sys.platform == 'win32'
_DARWIN  = sys.platform == 'darwin'

# Windows click-through constants
_GWL_EXSTYLE       = -20
_WS_EX_LAYERED     = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_LWA_ALPHA         = 0x00000002

# macOS pyobjc availability
_HAS_PYOBJC_OVERLAY = False
if _DARWIN:
    try:
        from AppKit import NSApp  # type: ignore[import]
        _HAS_PYOBJC_OVERLAY = True
    except ImportError:
        pass

# Platform UI font
_UI_FONT = "SF Pro Display" if _DARWIN else "Segoe UI"


class OverlayWindow:
    BG         = "#090B14"
    SURFACE    = "#111827"
    SURFACE_2  = "#182033"
    BORDER     = "#334155"
    FG_TIME    = "#38BDF8"
    FG_ACCENT  = "#A78BFA"
    FG_REACHED = "#34D399"
    FG_LABEL   = "#9CA3AF"
    FG_MODE    = "#FBBF24"
    ALPHA      = 0.90

    def __init__(self, root: tk.Tk):
        self._click_through = True
        self._goal_seconds  = 0
        self._elapsed       = 0
        self._drag_x        = 0
        self._drag_y        = 0

        self.win = tk.Toplevel(root)
        self.win.title("Overlay")   # must not contain "focus" — keeps it out of _last_allowed_hwnd
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", self.ALPHA)
        self.win.configure(bg=self.BG)
        self.win.geometry("+30+30")

        self._canvas = tk.Canvas(self.win, width=220, height=112, bg=self.BG,
                                 highlightthickness=0, bd=0)
        self._canvas.pack()
        self._time_font  = tkfont.Font(family=self._timer_family(), size=28, weight="bold")
        self._label_font = tkfont.Font(family=_UI_FONT, size=8, weight="bold")
        self._small_font = tkfont.Font(family=_UI_FONT, size=8)

        for widget in (self.win, self._canvas):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>",     self._drag_motion)

        self.win.withdraw()
        self._draw()

    # ── public ───────────────────────────────────────────────────────────────

    def show(self) -> None:
        self.win.deiconify()
        self.win.lift()
        self.win.update_idletasks()
        self._apply_click_through()

    def hide(self) -> None:
        self.win.withdraw()

    def set_goal(self, minutes: int) -> None:
        self._goal_seconds = minutes * 60
        self._draw()

    def update_time(self, elapsed_seconds: int) -> None:
        self._elapsed = elapsed_seconds
        self._draw()

    def toggle_click_through(self) -> None:
        self._click_through = not self._click_through
        self._apply_click_through()

    # ── click-through ─────────────────────────────────────────────────────────

    def _apply_click_through(self) -> None:
        if _WINDOWS:
            self._apply_click_through_windows()
        elif _DARWIN:
            self._apply_click_through_mac()
        self._draw()

    def _apply_click_through_windows(self) -> None:
        try:
            hwnd = self.win.winfo_id()
            cur  = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            if self._click_through:
                new = cur | _WS_EX_LAYERED | _WS_EX_TRANSPARENT
            else:
                new = (cur | _WS_EX_LAYERED) & ~_WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, new)
            ctypes.windll.user32.SetLayeredWindowAttributes(
                hwnd, 0, int(self.ALPHA * 255), _LWA_ALPHA
            )
        except Exception:
            pass

    def _apply_click_through_mac(self) -> None:
        if not _HAS_PYOBJC_OVERLAY:
            return
        try:
            for ns_win in NSApp.windows():
                if ns_win.title() == "Overlay":
                    ns_win.setIgnoresMouseEvents_(self._click_through)
                    break
        except Exception:
            pass

    def _timer_family(self) -> str:
        families = set(tkfont.families())
        if _DARWIN:
            for f in ("SF Mono", "Menlo", "Monaco"):
                if f in families:
                    return f
            return "Courier New"
        return "Cascadia Code" if "Cascadia Code" in families else "Consolas"

    def _round_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self._canvas.create_polygon(points, smooth=True, splinesteps=18, **kwargs)

    def _fmt(self, seconds: int) -> str:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _draw(self) -> None:
        c = self._canvas
        c.delete("all")
        w, h = 220, 112
        reached     = bool(self._goal_seconds and self._elapsed >= self._goal_seconds)
        border      = self.FG_REACHED if reached else self.FG_ACCENT
        time_color  = self.FG_REACHED if reached else self.FG_TIME

        self._round_rect(3, 3, w - 3, h - 3, 18, fill=self.SURFACE, outline=border, width=1)
        self._round_rect(9, 9, w - 9, h - 9, 14, fill=self.SURFACE_2, outline=self.BORDER, width=1)
        c.create_text(w // 2, 25, text="FOCUS MODE" if not reached else "SESSION COMPLETE",
                      fill=self.FG_LABEL, font=self._label_font)
        c.create_text(w // 2, 61, text=self._fmt(self._elapsed),
                      fill=time_color, font=self._time_font)

        if self._goal_seconds:
            label = self._fmt(self._goal_seconds)
        else:
            label = ""
        if not self._click_through:
            label      = "DRAG MODE  Ctrl+Shift+M"
            time_color = self.FG_MODE
        c.create_text(w // 2, 96, text=label,
                      fill=self.FG_LABEL if self._click_through else time_color,
                      font=self._small_font)

    # ── drag ─────────────────────────────────────────────────────────────────

    def _drag_start(self, event) -> None:
        if self._click_through:
            return
        self._drag_x = event.x_root - self.win.winfo_x()
        self._drag_y = event.y_root - self.win.winfo_y()

    def _drag_motion(self, event) -> None:
        if self._click_through:
            return
        self.win.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")
