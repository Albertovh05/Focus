"""
Always-on-top draggable timer overlay shown during a session.

Click-through is on by default (Ctrl+Shift+M toggles drag mode).
"""
import ctypes
import tkinter as tk
from tkinter import font as tkfont


_GWL_EXSTYLE       = -20
_WS_EX_LAYERED     = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_LWA_ALPHA         = 0x00000002


class OverlayWindow:
    BG         = "#0d1117"
    FG_TIME    = "#63b3ed"
    FG_REACHED = "#3fb950"
    FG_LABEL   = "#8b949e"
    FG_MODE    = "#f0883e"
    ALPHA      = 0.85

    def __init__(self, root: tk.Tk):
        self._click_through = True
        self._goal_seconds  = 0
        self._drag_x        = 0
        self._drag_y        = 0

        self.win = tk.Toplevel(root)
        self.win.title("Overlay")   # must not contain "focus" — keeps it out of _last_allowed_hwnd
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", self.ALPHA)
        self.win.configure(bg=self.BG)
        self.win.geometry("+30+30")

        self._frame = tk.Frame(self.win, bg="#1c2230", padx=12, pady=8)
        self._frame.pack()

        self._label_lbl = tk.Label(
            self._frame, text="F O C U S", bg="#1c2230", fg=self.FG_LABEL,
            font=("Segoe UI", 7, "bold"),
        )
        self._label_lbl.pack()

        time_font = tkfont.Font(family="Courier New", size=20, weight="bold")
        self._time_lbl = tk.Label(
            self._frame, text="00:00:00", bg="#1c2230", fg=self.FG_TIME,
            font=time_font,
        )
        self._time_lbl.pack()

        # Shown only when a goal is active
        self._goal_lbl = tk.Label(
            self._frame, text="", bg="#1c2230", fg=self.FG_LABEL,
            font=("Segoe UI", 8),
        )

        # Shown only when drag mode is unlocked
        self._mode_lbl = tk.Label(
            self._frame, text="drag mode  —  Ctrl+Shift+M to lock",
            bg="#1c2230", fg=self.FG_MODE,
            font=("Segoe UI", 7),
        )

        for widget in (self.win, self._frame, self._label_lbl, self._time_lbl,
                       self._goal_lbl, self._mode_lbl):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>",     self._drag_motion)

        self.win.withdraw()

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
        if self._goal_seconds:
            h, m = divmod(minutes, 60)
            goal_str = f"{h}:{m:02d}:00" if h else f"{m:02d}:00"
            self._goal_lbl.config(text=f"/ {goal_str}", fg=self.FG_LABEL)
            self._refresh_extra_labels()
        else:
            self._refresh_extra_labels()

    def update_time(self, elapsed_seconds: int) -> None:
        h = elapsed_seconds // 3600
        m = (elapsed_seconds % 3600) // 60
        s = elapsed_seconds % 60
        reached = self._goal_seconds and elapsed_seconds >= self._goal_seconds
        self._time_lbl.config(
            text=f"{h:02d}:{m:02d}:{s:02d}",
            fg=self.FG_REACHED if reached else self.FG_TIME,
        )
        if reached and self._goal_seconds:
            self._goal_lbl.config(fg=self.FG_REACHED)

    def toggle_click_through(self) -> None:
        self._click_through = not self._click_through
        self._apply_click_through()

    # ── click-through ─────────────────────────────────────────────────────────

    def _apply_click_through(self) -> None:
        try:
            hwnd = self.win.winfo_id()
            cur  = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            if self._click_through:
                new = cur | _WS_EX_LAYERED | _WS_EX_TRANSPARENT
            else:
                new = (cur | _WS_EX_LAYERED) & ~_WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, new)
            # SetWindowLongW resets layered attributes — restore alpha explicitly
            ctypes.windll.user32.SetLayeredWindowAttributes(
                hwnd, 0, int(self.ALPHA * 255), _LWA_ALPHA
            )
        except Exception:
            pass
        self._refresh_extra_labels()

    def _refresh_extra_labels(self) -> None:
        """Repack goal and mode labels in the correct order."""
        self._goal_lbl.pack_forget()
        self._mode_lbl.pack_forget()
        if self._goal_seconds:
            self._goal_lbl.pack()
        if not self._click_through:
            self._mode_lbl.pack()

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
