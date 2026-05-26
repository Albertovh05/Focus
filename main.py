"""
Focus — main entry point.

Builds the tkinter UI (setup screen + history tab) and wires together
the overlay, session manager, and global hotkey.
"""
import sys
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

import keyboard
from PIL import Image
import pystray

from db import save_session, get_sessions, fmt_duration
from session_manager import SessionManager
from overlay import OverlayWindow

# ── Colour tokens ─────────────────────────────────────────────────────────────
C_BG      = "#0d1117"
C_SURFACE = "#161b22"
C_BORDER  = "#30363d"
C_TEXT    = "#e6edf3"
C_MUTED   = "#8b949e"
C_ACCENT  = "#63b3ed"
C_ACCENT2 = "#388bfd"
C_SUCCESS = "#3fb950"
C_DANGER  = "#f85149"
C_HEADER  = "#1c2230"

_MOTIVATIONAL = [
    "Every minute you push through builds the discipline\n"
    "that separates you from who you were yesterday.",

    "The discomfort you feel right now is exactly\n"
    "where growth happens — don't walk away from it.",

    "You set this time aside for a reason.\n"
    "Honor the version of yourself who believed\n"
    "in what you could accomplish.",
]

_DURATIONS = (10, 15, 20, 25, 30, 45, 60)


def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


class FocusApp:
    HOTKEY      = "ctrl+shift+j"
    MOVE_HOTKEY = "ctrl+shift+m"

    def __init__(self):
        # Phantom root stays withdrawn forever — Toplevel children of a
        # withdrawn Tk() do not appear in the Windows taskbar.
        self._phantom = tk.Tk()
        self._phantom.withdraw()

        self.root = tk.Toplevel(self._phantom)
        self.root.title("Focus")
        self.root.geometry("820x620")
        self.root.minsize(700, 520)
        self.root.configure(bg=C_BG)

        try:
            self.root.iconbitmap(resource_path("focus_icon.ico"))
        except Exception:
            pass

        self._session: SessionManager | None = None
        self._session_start: datetime | None = None
        self._overlay: OverlayWindow = OverlayWindow(self.root)
        self._allowed_titles: list[str] = []
        self._target_minutes: int = 30
        self._duration_btns: dict[int, ttk.Button] = {}
        self._exiting: bool = False
        self._tray: pystray.Icon | None = None

        self._style_ttk()
        self._build_ui()
        self._register_hotkey()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Start hidden — accessible only via the system tray icon.
        self.root.withdraw()
        self._start_tray()
        self._phantom.mainloop()

    # ── ttk styling ──────────────────────────────────────────────────────────

    def _style_ttk(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure(".", background=C_BG, foreground=C_TEXT, font=("Segoe UI", 10))
        style.configure("TFrame",         background=C_BG)
        style.configure("Surface.TFrame", background=C_SURFACE)
        style.configure("TLabel",         background=C_BG,      foreground=C_TEXT)
        style.configure("Muted.TLabel",   background=C_BG,      foreground=C_MUTED,  font=("Segoe UI", 9))
        style.configure("Header.TLabel",  background=C_SURFACE, foreground=C_TEXT,   font=("Segoe UI", 11, "bold"))
        style.configure("Title.TLabel",   background=C_BG,      foreground=C_ACCENT, font=("Segoe UI", 22, "bold"))
        style.configure("Sub.TLabel",     background=C_BG,      foreground=C_MUTED,  font=("Segoe UI", 10))

        style.configure("TNotebook",       background=C_BG, borderwidth=0)
        style.configure("TNotebook.Tab",   background=C_SURFACE, foreground=C_MUTED,
                        padding=[14, 8], font=("Segoe UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", C_BG)],
                  foreground=[("selected", C_ACCENT)])

        style.configure("Treeview",
                        background=C_SURFACE, foreground=C_TEXT,
                        fieldbackground=C_SURFACE, rowheight=30, borderwidth=0)
        style.configure("Treeview.Heading",
                        background=C_HEADER, foreground=C_MUTED,
                        font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Treeview",
                  background=[("selected", C_ACCENT2)],
                  foreground=[("selected", "#ffffff")])

        style.configure("TScrollbar", background=C_SURFACE, troughcolor=C_BG,
                        bordercolor=C_BG, arrowcolor=C_MUTED)

        style.configure("Accent.TButton",
                        background=C_ACCENT2, foreground="#ffffff",
                        font=("Segoe UI", 10, "bold"), padding=[16, 8],
                        borderwidth=0, relief="flat")
        style.map("Accent.TButton",
                  background=[("active", "#58a6ff"), ("disabled", C_BORDER)],
                  foreground=[("disabled", C_MUTED)])

        style.configure("Danger.TButton",
                        background=C_DANGER, foreground="#ffffff",
                        font=("Segoe UI", 10, "bold"), padding=[16, 8],
                        borderwidth=0, relief="flat")
        style.map("Danger.TButton",
                  background=[("active", "#ff6b6b"), ("disabled", C_BORDER)],
                  foreground=[("disabled", C_MUTED)])

        # Chip buttons for duration selector
        style.configure("Chip.TButton",
                        background=C_BORDER, foreground=C_MUTED,
                        font=("Segoe UI", 9), padding=[9, 3],
                        borderwidth=0, relief="flat")
        style.map("Chip.TButton",
                  background=[("disabled", C_BG), ("active", C_SURFACE)],
                  foreground=[("disabled", C_BORDER)])

        style.configure("ChipOn.TButton",
                        background=C_ACCENT2, foreground="#ffffff",
                        font=("Segoe UI", 9, "bold"), padding=[9, 3],
                        borderwidth=0, relief="flat")
        style.map("ChipOn.TButton",
                  background=[("disabled", C_HEADER), ("active", "#58a6ff")],
                  foreground=[("disabled", C_MUTED)])

        style.configure("TCheckbutton",
                        background=C_SURFACE, foreground=C_TEXT,
                        font=("Segoe UI", 10))
        style.map("TCheckbutton",
                  background=[("active", C_SURFACE)],
                  indicatorcolor=[("selected", C_ACCENT2), ("!selected", C_BORDER)])

    # ── UI build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        bar = ttk.Frame(self.root, style="Surface.TFrame")
        bar.pack(fill="x")
        ttk.Label(bar, text="Focus",          style="Title.TLabel", padding=[20, 10]).pack(side="left")
        ttk.Label(bar, text="Stay in the zone.", style="Sub.TLabel", padding=[0,  10]).pack(side="left")

        self._nb = ttk.Notebook(self.root)
        self._nb.pack(fill="both", expand=True)

        self._setup_frame   = ttk.Frame(self._nb)
        self._history_frame = ttk.Frame(self._nb)
        self._nb.add(self._setup_frame,   text="  Session  ")
        self._nb.add(self._history_frame, text="  History  ")

        self._build_setup_tab()
        self._build_history_tab()

    # ── Setup tab ─────────────────────────────────────────────────────────────

    def _build_setup_tab(self):
        f = self._setup_frame

        # Status banner (hidden until session is active)
        self._status_frame = ttk.Frame(f, style="Surface.TFrame")
        self._status_frame.pack(fill="x", padx=20, pady=(16, 0))
        self._status_lbl = ttk.Label(
            self._status_frame, text="", style="Header.TLabel", padding=[16, 10],
        )
        self._status_lbl.pack(fill="x")
        self._status_frame.pack_forget()

        ttk.Label(f, text="Select the windows you want to allow during this session:",
                  style="Muted.TLabel", padding=[20, 12, 20, 4]).pack(anchor="w")

        # Duration selector
        dur_row = ttk.Frame(f)
        dur_row.pack(fill="x", padx=20, pady=(0, 10))
        ttk.Label(dur_row, text="Stay focused for:", style="Muted.TLabel").pack(side="left")
        for mins in _DURATIONS:
            btn = ttk.Button(
                dur_row, text=str(mins),
                command=lambda m=mins: self._select_duration(m),
                style="Chip.TButton",
            )
            btn.pack(side="left", padx=(6, 0))
            self._duration_btns[mins] = btn
        ttk.Label(dur_row, text="min", style="Muted.TLabel").pack(side="left", padx=(6, 0))
        self._select_duration(30)

        # Window list
        list_outer = ttk.Frame(f, style="Surface.TFrame")
        list_outer.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        canvas = tk.Canvas(list_outer, bg=C_SURFACE, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_outer, orient="vertical", command=canvas.yview)
        self._win_list_frame = ttk.Frame(canvas, style="Surface.TFrame")

        self._win_list_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self._win_list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        # Buttons row
        btn_row = ttk.Frame(f)
        btn_row.pack(fill="x", padx=20, pady=(0, 16))

        self._refresh_btn = ttk.Button(btn_row, text="↻  Refresh Windows",
                                       command=self._refresh_windows, style="Accent.TButton")
        self._refresh_btn.pack(side="left")

        self._start_btn = ttk.Button(btn_row, text="▶  Start Session",
                                     command=self._start_session, style="Accent.TButton")
        self._start_btn.pack(side="left", padx=(8, 0))

        self._stop_btn = ttk.Button(btn_row, text="■  End Session",
                                    command=self._request_stop, style="Danger.TButton",
                                    state="disabled")
        self._stop_btn.pack(side="left", padx=(8, 0))

        ttk.Label(btn_row, text="or  Ctrl+Shift+J", style="Muted.TLabel").pack(side="left", padx=(12, 0))

        self._checkboxes: list[tuple[tk.BooleanVar, str, str]] = []
        self._refresh_windows()

    def _refresh_windows(self):
        for w in self._win_list_frame.winfo_children():
            w.destroy()
        self._checkboxes.clear()

        for win in SessionManager.get_open_windows():
            var     = tk.BooleanVar(value=False)
            title   = win["title"]
            process = win["process"]
            row     = ttk.Frame(self._win_list_frame, style="Surface.TFrame")
            row.pack(fill="x", padx=4, pady=1)

            ttk.Checkbutton(row, variable=var, style="TCheckbutton").pack(
                side="left", padx=(8, 4), pady=6)
            ttk.Label(row, text=title, style="TLabel",
                      background=C_SURFACE, wraplength=500, justify="left").pack(side="left")
            ttk.Label(row, text=f"({process})", style="Muted.TLabel",
                      background=C_SURFACE).pack(side="left", padx=(4, 8))

            self._checkboxes.append((var, title, process))

    def _select_duration(self, minutes: int) -> None:
        self._target_minutes = minutes
        for m, btn in self._duration_btns.items():
            btn.config(style="ChipOn.TButton" if m == minutes else "Chip.TButton")

    # ── History tab ───────────────────────────────────────────────────────────

    def _build_history_tab(self):
        f = self._history_frame
        ttk.Label(f, text="Past Sessions", style="Header.TLabel",
                  padding=[20, 14, 20, 4]).pack(anchor="w")

        cols = ("date", "start", "end", "duration", "windows")
        self._tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")

        self._tree.heading("date",     text="Date")
        self._tree.heading("start",    text="Start")
        self._tree.heading("end",      text="End")
        self._tree.heading("duration", text="Duration")
        self._tree.heading("windows",  text="Allowed Windows")

        self._tree.column("date",     width=110, anchor="center")
        self._tree.column("start",    width=80,  anchor="center")
        self._tree.column("end",      width=80,  anchor="center")
        self._tree.column("duration", width=90,  anchor="center")
        self._tree.column("windows",  width=400, anchor="w")

        vsb = ttk.Scrollbar(f, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(f, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        hsb.pack(side="bottom", fill="x",    padx=20)
        vsb.pack(side="right",  fill="y",    padx=(0, 20))
        self._tree.pack(fill="both", expand=True, padx=(20, 0), pady=(0, 4))

        total_row = ttk.Frame(f, style="Surface.TFrame")
        total_row.pack(fill="x", padx=20, pady=(0, 8))
        self._total_lbl = ttk.Label(total_row, text="Total study time: 00:00:00",
                                    style="Header.TLabel", padding=[12, 6])
        self._total_lbl.pack(side="left")
        ttk.Button(total_row, text="↻  Refresh", command=self._load_history,
                   style="Accent.TButton").pack(side="right", padx=8, pady=4)

        self._nb.bind("<<NotebookTabChanged>>",
                      lambda e: self._load_history() if self._nb.index("current") == 1 else None)
        self._load_history()

    def _load_history(self):
        for row in self._tree.get_children():
            self._tree.delete(row)

        sessions  = get_sessions()
        total_sec = 0
        for s in sessions:
            self._tree.insert("", "end", values=(
                s["date"], s["start_time"], s["end_time"],
                fmt_duration(s["duration_seconds"]),
                ", ".join(s["allowed_windows"]),
            ))
            total_sec += s["duration_seconds"]

        self._total_lbl.config(text=f"Total study time: {fmt_duration(total_sec)}")

    # ── Session control ───────────────────────────────────────────────────────

    def _start_session(self):
        if self._session:
            return

        self._allowed_titles = [title for var, title, _ in self._checkboxes if var.get()]
        if not self._allowed_titles:
            messagebox.showwarning("No Windows Selected",
                                   "Please select at least one window to allow.",
                                   parent=self.root)
            return

        allowed_procs = {proc.lower() for var, _, proc in self._checkboxes if var.get()}

        self._session_start = datetime.now()
        self._session = SessionManager(
            allowed_procs=allowed_procs,
            own_pid=os.getpid(),
            overlay_hwnd=self._overlay.win.winfo_id(),
            on_tick=self._on_tick,
        )
        self._session.start()

        self._overlay.set_goal(self._target_minutes)
        self._overlay.show()
        self._overlay.update_time(0)

        self._start_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._refresh_btn.config(state="disabled")
        for btn in self._duration_btns.values():
            btn.config(state="disabled")
        for row in self._win_list_frame.winfo_children():
            for widget in row.winfo_children():
                try:
                    widget.config(state="disabled")
                except Exception:
                    pass

        self._status_lbl.config(
            text=f"  Session active  ·  {len(self._allowed_titles)} window(s) allowed"
                 f"  ·  goal: {self._target_minutes} min  ·  Ctrl+Shift+J to end"
        )
        self._status_frame.pack(fill="x", padx=20, pady=(16, 0))

    def _on_tick(self, elapsed: int):
        self.root.after(0, self._overlay.update_time, elapsed)

    def _request_stop(self):
        self._show_motivational_dialog()

    def _stop_session(self):
        if not self._session:
            return

        end_dt = datetime.now()
        self._session.stop()
        save_session(self._session_start, end_dt, self._allowed_titles)
        self._session = None

        self._overlay.hide()

        self._start_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        self._refresh_btn.config(state="normal")
        for btn in self._duration_btns.values():
            btn.config(state="normal")
        self._select_duration(self._target_minutes)
        for row in self._win_list_frame.winfo_children():
            for widget in row.winfo_children():
                try:
                    widget.config(state="normal")
                except Exception:
                    pass

        self._status_frame.pack_forget()
        self._refresh_windows()

    # ── Motivational exit dialog ──────────────────────────────────────────────

    def _show_motivational_dialog(self, on_exit=None):
        if self._exiting:
            return
        self._exiting = True

        dlg = tk.Toplevel(self.root)
        dlg.title("Are you sure?")
        dlg.configure(bg=C_BG)
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.grab_set()
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)  # disable the X button

        w, h = 500, 260
        dlg.geometry(f"{w}x{h}+{(dlg.winfo_screenwidth()-w)//2}+{(dlg.winfo_screenheight()-h)//2}")

        # Block root close while dialog is open
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        idx      = [0]
        after_id = [None]

        def cleanup():
            if after_id[0]:
                dlg.after_cancel(after_id[0])
            self._exiting = False
            self.root.protocol("WM_DELETE_WINDOW", self._on_close)
            dlg.grab_release()
            dlg.destroy()

        def stay():
            cleanup()

        def advance():
            if idx[0] < len(_MOTIVATIONAL) - 1:
                idx[0] += 1
                sentence_lbl.config(text=_MOTIVATIONAL[idx[0]])
                progress_lbl.config(text=f"{idx[0] + 1} / {len(_MOTIVATIONAL)}")
                next_btn.config(state="disabled", text="  →  ")
                tick(5)
            else:
                cleanup()
                if on_exit:
                    on_exit()
                else:
                    self._stop_session()

        def tick(remaining: int):
            if remaining > 0:
                countdown_lbl.config(text=f"available in {remaining}s")
                after_id[0] = dlg.after(1000, tick, remaining - 1)
            else:
                countdown_lbl.config(text="")
                is_last = idx[0] == len(_MOTIVATIONAL) - 1
                next_btn.config(
                    state="normal",
                    text="  Exit Anyway  " if is_last else "  →  ",
                )

        progress_lbl = tk.Label(dlg, text="1 / 3", bg=C_BG, fg=C_MUTED,
                                font=("Segoe UI", 9), pady=14)
        progress_lbl.pack()

        sentence_lbl = tk.Label(
            dlg, text=_MOTIVATIONAL[0], bg=C_BG, fg=C_TEXT,
            font=("Segoe UI", 11), justify="center", wraplength=440,
        )
        sentence_lbl.pack(expand=True, padx=30)

        countdown_lbl = tk.Label(dlg, text="", bg=C_BG, fg=C_MUTED,
                                 font=("Segoe UI", 8), pady=4)
        countdown_lbl.pack()

        btn_row = ttk.Frame(dlg)
        btn_row.pack(pady=(4, 20))

        ttk.Button(btn_row, text="  Stay Focused  ", command=stay,
                   style="Accent.TButton").pack(side="left", padx=8)

        next_btn = ttk.Button(btn_row, text="  →  ", command=advance,
                              style="Danger.TButton", state="disabled")
        next_btn.pack(side="left", padx=8)

        dlg.bind("<Escape>", lambda e: stay())
        tick(5)

    # ── Hotkeys ──────────────────────────────────────────────────────────────

    def _register_hotkey(self):
        keyboard.add_hotkey(self.HOTKEY,      self._hotkey_end_session,  suppress=False)
        keyboard.add_hotkey(self.MOVE_HOTKEY, self._hotkey_move_overlay, suppress=False)

    def _hotkey_end_session(self):
        if self._session:
            self.root.after(0, self._show_motivational_dialog)

    def _hotkey_move_overlay(self):
        if self._session:
            self.root.after(0, self._overlay.toggle_click_through)

    # ── System tray ──────────────────────────────────────────────────────────

    def _start_tray(self):
        try:
            img = Image.open(resource_path("focus_icon.ico"))
        except Exception:
            img = Image.new("RGBA", (64, 64), "#388bfd")

        menu = pystray.Menu(
            pystray.MenuItem("Open Focus", self._tray_show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._tray_exit),
        )
        self._tray = pystray.Icon("Focus", img, "Focus", menu)
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _tray_show(self, icon=None, item=None):
        self._phantom.after(0, self._do_show)

    def _do_show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_exit(self, icon=None, item=None):
        if self._session:
            self._phantom.after(0, self._request_tray_exit)
        else:
            self._phantom.after(0, self._do_quit)

    def _request_tray_exit(self):
        self._do_show()
        self._show_motivational_dialog(on_exit=self._do_quit)

    def _do_quit(self):
        if self._session:
            self._stop_session()
        if self._tray:
            self._tray.stop()
        self._phantom.quit()

    # ── Window close ─────────────────────────────────────────────────────────

    def _on_close(self):
        # Closing the window hides it to tray; the session (if any) keeps running.
        self.root.withdraw()


def main():
    icon_path = resource_path("focus_icon.ico")
    if not os.path.exists(icon_path):
        try:
            import icon_gen
            icon_gen.create_icon()
        except Exception as e:
            print(f"Icon generation failed: {e}")

    FocusApp()


if __name__ == "__main__":
    main()
