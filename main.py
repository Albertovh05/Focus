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

from db import (save_session, get_sessions, fmt_duration,
                get_blocked_domains, add_blocked_domain, remove_blocked_domain)
from session_manager import SessionManager
from overlay import OverlayWindow
from site_blocker import SiteBlocker, _is_admin

# ── Aurora theme tokens ──────────────────────────────────────────────────────
THEME = {
    "colors": {
        "bg": "#090B14",
        "bg_purple": "#120A24",
        "bg_blue": "#0B1730",
        "surface": "#111827",
        "surface_elevated": "#182033",
        "surface_muted": "#0F172A",
        "border": "#2A3350",
        "border_soft": "#334155",
        "text": "#E5E7EB",
        "text_secondary": "#9CA3AF",
        "text_muted": "#64748B",
        "accent_violet": "#A78BFA",
        "accent_cyan": "#38BDF8",
        "accent_blue": "#60A5FA",
        "success": "#34D399",
        "warning": "#FBBF24",
        "danger": "#F87171",
        "danger_dark": "#7F1D1D",
        "danger_surface": "#2A1118",
    },
    "fonts": {
        "title": ("Segoe UI", 26, "bold"),
        "subtitle": ("Segoe UI", 11),
        "section": ("Segoe UI", 14, "bold"),
        "body": ("Segoe UI", 10),
        "body_bold": ("Segoe UI", 10, "bold"),
        "small": ("Segoe UI", 9),
        "badge": ("Segoe UI", 8, "bold"),
    },
    "spacing": {
        "page": 22,
        "card_pad": 16,
        "gap": 14,
        "tight": 8,
    },
    "radius": {
        "card": 8,
        "pill": 999,
    },
    "states": {
        "normal": "#182033",
        "hover": "#202B44",
        "selected": "#26335A",
        "disabled": "#334155",
        "danger": "#7F1D1D",
        "success": "#0F3B32",
    },
}

COLORS = THEME["colors"]
FONTS = THEME["fonts"]
SPACE = THEME["spacing"]

C_BG      = COLORS["bg"]
C_SURFACE = COLORS["surface"]
C_ELEVATED = COLORS["surface_elevated"]
C_MUTED_SURFACE = COLORS["surface_muted"]
C_BORDER  = COLORS["border"]
C_TEXT    = COLORS["text"]
C_MUTED   = COLORS["text_secondary"]
C_DIM     = COLORS["text_muted"]
C_ACCENT  = COLORS["accent_violet"]
C_ACCENT2 = COLORS["accent_cyan"]
C_BLUE    = COLORS["accent_blue"]
C_SUCCESS = COLORS["success"]
C_DANGER  = COLORS["danger"]
C_DANGER_DARK = COLORS["danger_dark"]
C_HEADER  = C_ELEVATED

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


def _mix(c1: str, c2: str, t: float) -> str:
    a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{int(x + (y - x) * t):02x}" for x, y in zip(a, b))


def _draw_round_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int,
                     r: int, **kwargs) -> int:
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=18, **kwargs)


class AuroraCard(tk.Frame):
    def __init__(self, parent, title: str, description: str = ""):
        super().__init__(parent, bg=C_BORDER, padx=1, pady=1)
        self.body = tk.Frame(self, bg=C_SURFACE, padx=SPACE["card_pad"], pady=SPACE["card_pad"])
        self.body.pack(fill="both", expand=True)
        header = tk.Frame(self.body, bg=C_SURFACE)
        header.pack(fill="x", pady=(0, 10))
        tk.Label(header, text=title, bg=C_SURFACE, fg=C_TEXT,
                 font=FONTS["section"]).pack(anchor="w")
        if description:
            tk.Label(header, text=description, bg=C_SURFACE, fg=C_MUTED,
                     font=FONTS["small"], wraplength=720, justify="left").pack(anchor="w", pady=(2, 0))


class AuroraButton(tk.Canvas):
    def __init__(self, parent, text: str, command=None, kind: str = "secondary",
                 width: int = 150, height: int = 38):
        super().__init__(parent, width=width, height=height, highlightthickness=0,
                         bg=parent.cget("bg"), cursor="hand2")
        self.text = text
        self.command = command
        self.kind = kind
        self._state = "normal"
        self._hover = False
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._click)
        self._paint()

    def config(self, cnf=None, **kw):
        if "state" in kw:
            self._state = kw.pop("state")
            self.configure(cursor="" if self._state == "disabled" else "hand2")
            self._paint()
        if "text" in kw:
            self.text = kw.pop("text")
            self._paint()
        if "command" in kw:
            self.command = kw.pop("command")
        if kw:
            super().config(cnf, **kw)

    configure = config

    def _palette(self):
        if self._state == "disabled":
            return C_BORDER, C_BORDER, C_DIM
        if self.kind == "primary":
            fill = COLORS["accent_violet"] if not self._hover else "#BCA7FF"
            return fill, COLORS["accent_cyan"], "#080A13"
        if self.kind == "danger":
            fill = COLORS["danger_dark"] if not self._hover else "#9F2626"
            return fill, COLORS["danger"], C_TEXT
        fill = C_MUTED_SURFACE if not self._hover else THEME["states"]["hover"]
        return fill, C_BORDER if not self._hover else C_BLUE, C_TEXT

    def _paint(self):
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        fill, outline, fg = self._palette()
        _draw_round_rect(self, 1, 1, w - 1, h - 1, min(16, h // 2),
                         fill=fill, outline=outline, width=1)
        self.create_text(w // 2, h // 2, text=self.text, fill=fg,
                         font=FONTS["body_bold"])

    def _enter(self, _):
        self._hover = True
        self._paint()

    def _leave(self, _):
        self._hover = False
        self._paint()

    def _click(self, _):
        if self._state != "disabled" and self.command:
            self.command()


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
        self.root.geometry("940x720")
        self.root.minsize(780, 620)
        self.root.configure(bg=C_BG)

        try:
            self.root.iconbitmap(resource_path("focus_icon.ico"))
        except Exception:
            pass

        self._session: SessionManager | None = None
        self._session_start: datetime | None = None
        self._overlay: OverlayWindow = OverlayWindow(self.root)
        self._allowed_titles: list[str] = []
        self._target_minutes: int | None = None
        self._duration_btns: dict[int, ttk.Button] = {}
        self._mutable_controls: list[tk.Widget] = []
        self._exiting: bool = False
        self._goal_reached: bool = False
        self._tray: pystray.Icon | None = None
        self._site_blocker: SiteBlocker | None = None

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

        style.configure(".", background=C_BG, foreground=C_TEXT, font=FONTS["body"])
        style.configure("TFrame",         background=C_BG)
        style.configure("Surface.TFrame", background=C_SURFACE)
        style.configure("Elevated.TFrame", background=C_ELEVATED)
        style.configure("TLabel",         background=C_BG,      foreground=C_TEXT)
        style.configure("Muted.TLabel",   background=C_BG,      foreground=C_MUTED,  font=FONTS["small"])
        style.configure("Header.TLabel",  background=C_SURFACE, foreground=C_TEXT,   font=FONTS["section"])
        style.configure("Title.TLabel",   background=C_BG,      foreground=C_TEXT, font=FONTS["title"])
        style.configure("Sub.TLabel",     background=C_BG,      foreground=C_MUTED,  font=FONTS["subtitle"])

        style.configure("TNotebook",       background=C_BG, borderwidth=0)
        style.configure("TNotebook.Tab",   background=C_MUTED_SURFACE, foreground=C_MUTED,
                        padding=[18, 9], font=FONTS["body_bold"], borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", C_ELEVATED), ("active", C_SURFACE)],
                  foreground=[("selected", C_ACCENT2), ("active", C_TEXT)])

        style.configure("Treeview",
                        background=C_MUTED_SURFACE, foreground=C_TEXT,
                        fieldbackground=C_MUTED_SURFACE, rowheight=32, borderwidth=0,
                        font=FONTS["body"])
        style.configure("Treeview.Heading",
                        background=C_ELEVATED, foreground=C_MUTED,
                        font=FONTS["badge"], relief="flat")
        style.map("Treeview",
                  background=[("selected", THEME["states"]["selected"])],
                  foreground=[("selected", C_TEXT)])

        style.configure("TScrollbar", background=C_SURFACE, troughcolor=C_BG,
                        bordercolor=C_BG, arrowcolor=C_MUTED)

        style.configure("TEntry",
                        fieldbackground=C_MUTED_SURFACE, foreground=C_TEXT,
                        insertcolor=C_TEXT, selectbackground=THEME["states"]["selected"],
                        selectforeground="#ffffff", bordercolor=C_BORDER,
                        lightcolor=C_BORDER, darkcolor=C_BORDER)

        style.configure("Accent.TButton",
                        background=C_ACCENT, foreground="#080A13",
                        font=FONTS["body_bold"], padding=[18, 9],
                        borderwidth=0, relief="flat")
        style.map("Accent.TButton",
                  background=[("active", "#BCA7FF"), ("disabled", C_BORDER)],
                  foreground=[("active", "#080A13"), ("!disabled", "#080A13"), ("disabled", C_DIM)])

        style.configure("Danger.TButton",
                        background=C_DANGER_DARK, foreground=C_TEXT,
                        font=FONTS["body_bold"], padding=[18, 9],
                        borderwidth=0, relief="flat")
        style.map("Danger.TButton",
                  background=[("active", "#9F2626"), ("disabled", C_BORDER)],
                  foreground=[("disabled", C_DIM)])

        # Chip buttons for duration selector
        style.configure("Chip.TButton",
                        background=C_MUTED_SURFACE, foreground=C_MUTED,
                        font=FONTS["small"], padding=[12, 6],
                        borderwidth=1, relief="flat")
        style.map("Chip.TButton",
                  background=[("disabled", C_BG), ("active", C_ELEVATED)],
                  foreground=[("disabled", C_BORDER)])

        style.configure("ChipOn.TButton",
                        background=THEME["states"]["selected"], foreground=C_TEXT,
                        font=FONTS["badge"], padding=[12, 6],
                        borderwidth=0, relief="flat")
        style.map("ChipOn.TButton",
                  background=[("disabled", C_HEADER), ("active", "#30416F")],
                  foreground=[("disabled", C_MUTED)])

        style.configure("TCheckbutton",
                        background=C_SURFACE, foreground=C_TEXT,
                        font=("Segoe UI", 10))
        style.map("TCheckbutton",
                  background=[("active", C_SURFACE)],
                  indicatorcolor=[("selected", C_ACCENT2), ("!selected", C_BORDER)])

    # ── UI build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._bg_canvas = tk.Canvas(self.root, bg=C_BG, highlightthickness=0)
        self._bg_canvas.pack(fill="both", expand=True)
        self._content_frame = tk.Frame(self._bg_canvas, bg=C_BG)
        self._content_window = self._bg_canvas.create_window(
            0, 0, anchor="nw", window=self._content_frame
        )
        self._bg_canvas.bind("<Configure>", self._resize_background)

        bar = tk.Frame(self._content_frame, bg=C_BG)
        bar.pack(fill="x", padx=SPACE["page"], pady=(22, 12))
        title_block = tk.Frame(bar, bg=C_BG)
        title_block.pack(side="left")
        tk.Label(title_block, text="Focus", bg=C_BG, fg=C_TEXT,
                 font=FONTS["title"]).pack(anchor="w")
        tk.Label(title_block, text="Deep work, enforced.", bg=C_BG, fg=C_MUTED,
                 font=FONTS["subtitle"]).pack(anchor="w")

        self._top_badges = tk.Frame(bar, bg=C_BG)
        self._top_badges.pack(side="right", anchor="n", pady=(6, 0))
        self._ready_badge = self._make_badge(self._top_badges, "Ready", C_SUCCESS)
        self._apps_badge = self._make_badge(self._top_badges, "0 apps", C_BLUE)
        self._sites_badge = self._make_badge(self._top_badges, "0 sites", C_ACCENT)
        self._ready_badge.pack(side="left", padx=(0, 8))
        self._apps_badge.pack(side="left", padx=(0, 8))
        self._sites_badge.pack(side="left")

        self._nb = ttk.Notebook(self._content_frame)
        self._nb.pack(fill="both", expand=True, padx=SPACE["page"], pady=(0, SPACE["page"]))

        self._setup_frame   = ttk.Frame(self._nb)
        self._history_frame = ttk.Frame(self._nb)
        self._nb.add(self._setup_frame,   text="  Session  ")
        self._nb.add(self._history_frame, text="  History  ")

        self._build_setup_tab()
        self._build_history_tab()

    def _resize_background(self, event) -> None:
        self._bg_canvas.delete("aurora")
        self._bg_canvas.itemconfigure(self._content_window, width=event.width, height=event.height)
        steps = 80
        for i in range(steps):
            t = i / max(steps - 1, 1)
            color = _mix(COLORS["bg_purple"], COLORS["bg_blue"], t)
            y1 = int(event.height * i / steps)
            y2 = int(event.height * (i + 1) / steps) + 1
            self._bg_canvas.create_rectangle(0, y1, event.width, y2, outline="", fill=color, tags="aurora")
        self._bg_canvas.create_oval(-180, -120, 420, 220, fill="#17103A", outline="", tags="aurora")
        self._bg_canvas.create_oval(event.width - 360, 40, event.width + 180, 300,
                                    fill="#082B3E", outline="", tags="aurora")
        self._bg_canvas.create_rectangle(0, 0, event.width, event.height, outline="", fill=C_BG,
                                         stipple="gray75", tags="aurora")
        self._bg_canvas.tag_lower("aurora")

    def _make_badge(self, parent, text: str, color: str) -> tk.Frame:
        badge = tk.Frame(parent, bg=C_BORDER, padx=1, pady=1)
        inner = tk.Frame(badge, bg=C_MUTED_SURFACE, padx=9, pady=5)
        inner.pack()
        dot = tk.Canvas(inner, width=8, height=8, bg=C_MUTED_SURFACE, highlightthickness=0)
        dot.create_oval(1, 1, 7, 7, fill=color, outline=color)
        dot.pack(side="left", padx=(0, 6))
        label = tk.Label(inner, text=text, bg=C_MUTED_SURFACE, fg=C_MUTED, font=FONTS["badge"])
        label.pack(side="left")
        badge._label = label
        badge._dot = dot
        return badge

    def _set_badge(self, badge: tk.Frame, text: str, color: str) -> None:
        badge._label.config(text=text)
        badge._dot.delete("all")
        badge._dot.create_oval(1, 1, 7, 7, fill=color, outline=color)

    def _make_scrollable_tab(self, parent: tk.Widget) -> tk.Frame:
        canvas = tk.Canvas(parent, bg=C_BG, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas, bg=C_BG)
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")

        content.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._setup_canvas = canvas
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", self._on_setup_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        return content

    def _is_descendant(self, widget: tk.Widget, ancestor: tk.Widget) -> bool:
        while widget:
            if widget == ancestor:
                return True
            parent = widget.winfo_parent()
            if not parent:
                return False
            try:
                widget = widget._nametowidget(parent)
            except KeyError:
                return False
        return False

    def _on_setup_mousewheel(self, event) -> None:
        delta = -1 * (event.delta // 120)
        win_canvas = getattr(self, "_win_canvas", None)
        bl_canvas = getattr(self, "_bl_canvas", None)
        if win_canvas and (
            event.widget == win_canvas
            or self._is_descendant(event.widget, getattr(self, "_win_list_frame", win_canvas))
        ):
            win_canvas.yview_scroll(delta, "units")
            return "break"
        if bl_canvas and (
            event.widget == bl_canvas
            or self._is_descendant(event.widget, getattr(self, "_blocklist_frame", bl_canvas))
        ):
            bl_canvas.yview_scroll(delta, "units")
            return "break"
        self._setup_canvas.yview_scroll(delta, "units")
        return "break"

    # ── Setup tab ─────────────────────────────────────────────────────────────

    def _build_setup_tab(self):
        f = self._make_scrollable_tab(self._setup_frame)

        self._status_card = AuroraCard(
            f, "Ready", "Configure your allowed apps and blocked websites, then start a session."
        )
        self._status_card.pack(fill="x", padx=20, pady=(18, 12))
        self._status_lbl = tk.Label(self._status_card.body, text="Ready",
                                    bg=C_SURFACE, fg=C_SUCCESS, font=FONTS["body_bold"])
        self._status_lbl.pack(anchor="w")

        duration_card = AuroraCard(
            f, "Session Length", "Choose the amount of time you want locked in."
        )
        duration_card.pack(fill="x", padx=20, pady=(0, 12))
        dur_row = tk.Frame(duration_card.body, bg=C_SURFACE)
        dur_row.pack(fill="x")
        for mins in _DURATIONS:
            btn = ttk.Button(
                dur_row, text=f"{mins} min",
                command=lambda m=mins: self._select_duration(m),
                style="Chip.TButton",
            )
            btn.pack(side="left", padx=(0, 8), pady=(2, 0))
            self._duration_btns[mins] = btn

        # Window list
        self._apps_card = AuroraCard(
            f, "Allowed Apps", "Only checked apps can stay in focus during a session."
        )
        self._apps_card.pack(fill="x", expand=False, padx=20, pady=(0, 12))

        self._win_canvas = tk.Canvas(self._apps_card.body, bg=C_MUTED_SURFACE, highlightthickness=0, height=185)
        scrollbar = ttk.Scrollbar(self._apps_card.body, orient="vertical", command=self._win_canvas.yview)
        self._win_list_frame = tk.Frame(self._win_canvas, bg=C_MUTED_SURFACE)

        self._win_list_frame.bind(
            "<Configure>",
            lambda e: self._win_canvas.configure(scrollregion=self._win_canvas.bbox("all")),
        )
        win_window = self._win_canvas.create_window((0, 0), window=self._win_list_frame, anchor="nw")
        self._win_canvas.configure(yscrollcommand=scrollbar.set)
        self._win_canvas.bind("<Configure>", lambda e: self._win_canvas.itemconfigure(win_window, width=e.width))

        scrollbar.pack(side="right", fill="y")
        self._win_canvas.pack(side="left", fill="both", expand=True)
        self._win_canvas.bind("<MouseWheel>", self._on_setup_mousewheel)

        # Buttons row
        btn_row = tk.Frame(f, bg=C_BG)
        btn_row.pack(fill="x", padx=20, pady=(0, 16))

        self._refresh_btn = AuroraButton(btn_row, text="Refresh Windows",
                                         command=self._refresh_windows, kind="secondary", width=154)
        self._refresh_btn.pack(side="left")

        self._start_btn = AuroraButton(btn_row, text="Start Focus Session",
                                       command=self._start_session, kind="primary", width=190)
        self._start_btn.pack(side="left", padx=(8, 0))

        self._stop_btn = AuroraButton(btn_row, text="Break Focus",
                                      command=self._request_stop, kind="danger", width=130)
        self._stop_btn.config(state="disabled")
        self._stop_btn.pack(side="left", padx=(8, 0))

        tk.Label(btn_row, text="Ctrl+Shift+J", bg=C_BG, fg=C_DIM,
                 font=FONTS["small"]).pack(side="left", padx=(12, 0))

        self._build_blocklist_section(f)

        self._checkboxes: list[tuple[tk.BooleanVar, str, str]] = []
        self._refresh_windows()

    def _refresh_windows(self):
        for w in self._win_list_frame.winfo_children():
            w.destroy()
        self._checkboxes.clear()

        own_pid = os.getpid()
        windows = SessionManager.get_open_windows()
        if not windows:
            tk.Label(self._win_list_frame, text="No allowed apps yet",
                     bg=C_MUTED_SURFACE, fg=C_MUTED, font=FONTS["body_bold"]).pack(anchor="w", padx=14, pady=(14, 2))
            tk.Label(self._win_list_frame, text="Open the tools you need, then refresh this list.",
                     bg=C_MUTED_SURFACE, fg=C_DIM, font=FONTS["small"]).pack(anchor="w", padx=14, pady=(0, 14))
            self._set_badge(self._apps_badge, "0 apps", C_BLUE)
            return
        for win in windows:
            is_own  = win["pid"] == own_pid
            var     = tk.BooleanVar(value=is_own)
            title   = win["title"]
            process = win["process"]
            row     = tk.Frame(self._win_list_frame, bg=C_MUTED_SURFACE)
            row.pack(fill="x", padx=8, pady=2)

            cb = ttk.Checkbutton(row, variable=var, style="TCheckbutton", command=self._update_status_badges)
            cb.pack(side="left", padx=(8, 4), pady=6)
            if is_own:
                cb.config(state="disabled")
            tk.Label(row, text=title, bg=C_MUTED_SURFACE, fg=C_TEXT,
                     font=FONTS["body"], wraplength=560, justify="left").pack(side="left")
            tk.Label(row, text=f"({process})", bg=C_MUTED_SURFACE, fg=C_DIM,
                     font=FONTS["small"]).pack(side="left", padx=(6, 8))
            if is_own:
                tk.Label(row, text="ALWAYS ALLOWED", bg=C_MUTED_SURFACE, fg=C_ACCENT2,
                         font=FONTS["badge"]).pack(side="left", padx=(0, 8))

            self._checkboxes.append((var, title, process))
        self._update_status_badges()

    def _select_duration(self, minutes: int | None) -> None:
        self._target_minutes = minutes
        for m, btn in self._duration_btns.items():
            btn.config(style="ChipOn.TButton" if m == minutes else "Chip.TButton")

    # ── Blocklist section ─────────────────────────────────────────────────────

    def _build_blocklist_section(self, parent: ttk.Frame) -> None:
        outer = AuroraCard(
            parent, "Blocked Websites", "Distracting sites are removed while focus mode is active."
        )
        outer.pack(fill="x", padx=20, pady=(0, 12))

        header_row = tk.Frame(outer.body, bg=C_SURFACE)
        header_row.pack(fill="x", pady=(0, 8))
        if not _is_admin():
            tk.Label(header_row,
                     text="Website Blocking: title watcher enabled. Run as Admin for hosts-file blocking.",
                     bg=C_SURFACE, fg=C_DIM, font=FONTS["small"]).pack(side="left")
        else:
            tk.Label(header_row, text="Website Blocking: hosts file and browser title watcher enabled.",
                     bg=C_SURFACE, fg=C_SUCCESS, font=FONTS["small"]).pack(side="left")

        input_row = tk.Frame(outer.body, bg=C_SURFACE)
        input_row.pack(fill="x", pady=(0, 10))

        self._domain_entry = ttk.Entry(input_row, width=28,
                                       font=("Segoe UI", 10))
        self._domain_entry.pack(side="left")
        self._domain_entry.bind("<Return>", lambda e: self._add_blocked_domain())

        self._add_btn = AuroraButton(input_row, text="Add Website",
                                     command=self._add_blocked_domain, kind="secondary",
                                     width=124, height=34)
        self._add_btn.pack(side="left", padx=(6, 0))

        bl_outer = tk.Frame(outer.body, bg=C_SURFACE)
        bl_outer.pack(fill="x")

        self._bl_canvas = tk.Canvas(bl_outer, bg=C_MUTED_SURFACE, highlightthickness=0, height=96)
        bl_sb = ttk.Scrollbar(bl_outer, orient="vertical", command=self._bl_canvas.yview)
        self._blocklist_frame = tk.Frame(self._bl_canvas, bg=C_MUTED_SURFACE)

        self._blocklist_frame.bind(
            "<Configure>",
            lambda e: self._bl_canvas.configure(scrollregion=self._bl_canvas.bbox("all")),
        )
        bl_window = self._bl_canvas.create_window((0, 0), window=self._blocklist_frame, anchor="nw")
        self._bl_canvas.configure(yscrollcommand=bl_sb.set)
        self._bl_canvas.bind("<Configure>", lambda e: self._bl_canvas.itemconfigure(bl_window, width=e.width))
        self._bl_canvas.bind(
            "<MouseWheel>",
            self._on_setup_mousewheel,
        )
        bl_sb.pack(side="right", fill="y")
        self._bl_canvas.pack(side="left", fill="both", expand=True)

        self._refresh_blocklist()

    def _refresh_blocklist(self) -> None:
        for w in self._blocklist_frame.winfo_children():
            w.destroy()
        domains = get_blocked_domains()
        if not domains:
            tk.Label(self._blocklist_frame, text="No blocked websites",
                     bg=C_MUTED_SURFACE, fg=C_MUTED,
                     font=FONTS["body_bold"]).pack(anchor="w", padx=14, pady=(12, 2))
            tk.Label(self._blocklist_frame, text="Add distracting sites you want removed during focus.",
                     bg=C_MUTED_SURFACE, fg=C_DIM,
                     font=FONTS["small"]).pack(anchor="w", padx=14, pady=(0, 12))
            self._update_status_badges()
            return
        for domain in domains:
            row = tk.Frame(self._blocklist_frame, bg=C_MUTED_SURFACE)
            row.pack(fill="x", padx=10, pady=2)
            tk.Label(row, text=domain, bg=C_MUTED_SURFACE, fg=C_TEXT,
                     font=FONTS["body"]).pack(side="left", padx=(4, 8), pady=5)
            tk.Button(row, text="🗑",
                      command=lambda d=domain: self._remove_blocked_domain(d),
                      bg=C_MUTED_SURFACE, fg=C_DANGER,
                      activebackground=C_BORDER, activeforeground=C_DANGER,
                      font=("Segoe UI Emoji", 12),
                      relief="flat", bd=0, padx=4, pady=2,
                      cursor="hand2").pack(side="right")
        self._update_status_badges()

    def _add_blocked_domain(self) -> None:
        raw = self._domain_entry.get().strip()
        if not raw:
            return
        domain = SiteBlocker._normalise(raw)
        if not domain:
            return
        add_blocked_domain(domain)
        self._domain_entry.delete(0, tk.END)
        self._refresh_blocklist()

    def _remove_blocked_domain(self, domain: str) -> None:
        remove_blocked_domain(domain)
        self._refresh_blocklist()

    def _update_status_badges(self) -> None:
        allowed = sum(1 for var, _, _ in getattr(self, "_checkboxes", []) if var.get())
        blocked = len(get_blocked_domains())
        active = self._session is not None
        self._set_badge(
            self._ready_badge,
            "Focus Active" if active else "Ready",
            C_ACCENT2 if active else C_SUCCESS,
        )
        self._set_badge(self._apps_badge, f"{allowed} app{'s' if allowed != 1 else ''}", C_BLUE)
        self._set_badge(self._sites_badge, f"{blocked} site{'s' if blocked != 1 else ''}", C_ACCENT)

    def _set_locked_mode(self, locked: bool) -> None:
        state = "disabled" if locked else "normal"
        self._start_btn.config(state="disabled" if locked else "normal")
        self._stop_btn.config(state="normal" if locked else "disabled")
        self._refresh_btn.config(state=state)
        for btn in self._duration_btns.values():
            btn.config(state=state)
        for row in self._win_list_frame.winfo_children():
            for widget in row.winfo_children():
                try:
                    widget.config(state=state)
                except Exception:
                    pass
        self._domain_entry.config(state=state)
        self._add_btn.config(state=state)
        for row in self._blocklist_frame.winfo_children():
            for widget in row.winfo_children():
                try:
                    widget.config(state=state)
                except Exception:
                    pass
        self._update_status_badges()

    # ── History tab ───────────────────────────────────────────────────────────

    def _build_history_tab(self):
        f = self._history_frame
        card = AuroraCard(f, "Session History", "A running record of completed and ended sessions.")
        card.pack(fill="both", expand=True, padx=20, pady=18)

        cols = ("date", "start", "end", "duration", "windows")
        self._tree = ttk.Treeview(card.body, columns=cols, show="headings", selectmode="browse")

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

        vsb = ttk.Scrollbar(card.body, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(card.body, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        hsb.pack(side="bottom", fill="x")
        vsb.pack(side="right",  fill="y")
        self._tree.pack(fill="both", expand=True, pady=(0, 8))

        total_row = tk.Frame(card.body, bg=C_SURFACE)
        total_row.pack(fill="x", pady=(8, 0))
        self._total_lbl = tk.Label(total_row, text="Total study time: 00:00:00",
                                   bg=C_SURFACE, fg=C_ACCENT2, font=FONTS["body_bold"])
        self._total_lbl.pack(side="left")
        AuroraButton(total_row, text="Refresh", command=self._load_history,
                     kind="secondary", width=104, height=34).pack(side="right")

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
        self._goal_reached = False

        if self._target_minutes is None:
            messagebox.showwarning(
                "No Duration Selected",
                "Please select a session duration before starting.",
                parent=self.root,
            )
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

        blocked = get_blocked_domains()
        if blocked:
            self._site_blocker = SiteBlocker(blocked)
            self._site_blocker.start()

        self._overlay.set_goal(self._target_minutes)
        self._overlay.show()
        self._overlay.update_time(0)

        self._set_locked_mode(True)
        self._status_lbl.config(
            text=f"Focus Mode Active\nOnly approved apps are allowed until the timer ends.\n"
                 f"{len(self._allowed_titles)} app(s) allowed  ·  {len(blocked)} website(s) blocked  ·  {self._target_minutes} min goal",
            fg=C_ACCENT2,
            justify="left",
        )

        # Bring Focus window to the front so the user starts on an allowed window.
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_tick(self, elapsed: int):
        self.root.after(0, self._tick_ui, elapsed)

    def _tick_ui(self, elapsed: int):
        self._overlay.update_time(elapsed)
        if not self._goal_reached and elapsed >= self._target_minutes * 60:
            self._goal_reached = True
            self._status_lbl.config(
                text="Session Complete\nYou reached the target. You can end this session when ready.",
                fg=C_SUCCESS,
                justify="left",
            )

    def _request_stop(self):
        if self._goal_reached:
            self._stop_session()
        else:
            self._show_motivational_dialog()

    def _stop_session(self):
        if not self._session:
            return

        end_dt = datetime.now()
        self._session.stop()
        was_complete = self._goal_reached
        target_minutes = self._target_minutes or 0
        allowed_count = len(self._allowed_titles)
        blocked_count = len(get_blocked_domains())
        save_session(self._session_start, end_dt, self._allowed_titles)
        self._session = None

        self._overlay.hide()

        self._set_locked_mode(False)
        self._select_duration(None)
        self._status_lbl.config(
            text="Session Complete\nYou stayed locked in." if was_complete else
                 "Ready\nConfigure your allowed apps and blocked websites, then start a session.",
            fg=C_SUCCESS if was_complete else C_SUCCESS,
            justify="left",
        )

        if self._site_blocker:
            self._site_blocker.stop()
            self._site_blocker = None
        self._refresh_blocklist()

        self._refresh_windows()
        if was_complete:
            self._show_completion_dialog(target_minutes, allowed_count, blocked_count)

    def _show_completion_dialog(self, minutes: int, allowed_count: int, blocked_count: int) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Session Complete")
        dlg.configure(bg=C_BG)
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.transient(self.root)
        w, h = 430, 260
        dlg.geometry(f"{w}x{h}+{(dlg.winfo_screenwidth()-w)//2}+{(dlg.winfo_screenheight()-h)//2}")

        card = AuroraCard(dlg, "Session Complete", "You stayed locked in.")
        card.pack(fill="both", expand=True, padx=18, pady=18)
        tk.Label(card.body, text=f"{minutes} minutes focused",
                 bg=C_SURFACE, fg=C_SUCCESS, font=("Segoe UI", 20, "bold")).pack(anchor="w", pady=(0, 10))
        tk.Label(card.body, text=f"{allowed_count} apps allowed",
                 bg=C_SURFACE, fg=C_TEXT, font=FONTS["body"]).pack(anchor="w", pady=2)
        tk.Label(card.body, text=f"{blocked_count} websites blocked",
                 bg=C_SURFACE, fg=C_TEXT, font=FONTS["body"]).pack(anchor="w", pady=2)
        tk.Label(card.body, text="Session history has been saved.",
                 bg=C_SURFACE, fg=C_DIM, font=FONTS["small"]).pack(anchor="w", pady=(10, 16))
        AuroraButton(card.body, text="Continue", command=dlg.destroy,
                     kind="primary", width=120, height=36).pack(anchor="e")

    # ── Motivational exit dialog ──────────────────────────────────────────────

    def _show_motivational_dialog(self, on_exit=None):
        if self._exiting:
            return
        self._exiting = True

        dlg = tk.Toplevel(self.root)
        dlg.title("Break focus session?")
        dlg.configure(bg=C_BG)
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.grab_set()
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)  # disable the X button

        w, h = 540, 400
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
                next_btn.config(state="disabled", text="Continue")
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
                    text="Break Focus" if is_last else "Continue",
                )

        card = AuroraCard(dlg, "Break focus session?", "")
        card.pack(fill="both", expand=True, padx=18, pady=18)

        remaining = ""
        if self._session_start and self._target_minutes:
            elapsed = int((datetime.now() - self._session_start).total_seconds())
            left = max(self._target_minutes * 60 - elapsed, 0)
            remaining = fmt_duration(left)
        body = "Ending now will save this as an incomplete session."
        if remaining:
            body = f"You still have {remaining} remaining.\n{body}"
        tk.Label(card.body, text=body, bg=C_SURFACE, fg=C_MUTED,
                 font=FONTS["body"], justify="left").pack(anchor="w", pady=(0, 10))

        progress_lbl = tk.Label(card.body, text="1 / 3", bg=C_SURFACE, fg=C_DIM,
                                font=FONTS["badge"])
        progress_lbl.pack(anchor="w")

        sentence_lbl = tk.Label(
            card.body, text=_MOTIVATIONAL[0], bg=C_SURFACE, fg=C_TEXT,
            font=("Segoe UI", 12), justify="left", wraplength=460,
        )
        sentence_lbl.pack(expand=True, fill="x", pady=8)

        countdown_lbl = tk.Label(card.body, text="", bg=C_SURFACE, fg=C_DIM,
                                 font=FONTS["small"])
        countdown_lbl.pack(anchor="w", pady=(0, 8))

        btn_row = tk.Frame(card.body, bg=C_SURFACE)
        btn_row.pack(anchor="e")

        AuroraButton(btn_row, text="Stay Focused", command=stay,
                     kind="primary", width=132, height=36).pack(side="left", padx=(0, 8))

        next_btn = AuroraButton(btn_row, text="Break Focus", command=advance,
                                kind="danger", width=128, height=36)
        next_btn.config(state="disabled")
        next_btn.pack(side="left", padx=8)

        dlg.bind("<Escape>", lambda e: stay())
        tick(5)

    # ── Hotkeys ──────────────────────────────────────────────────────────────

    def _register_hotkey(self):
        keyboard.add_hotkey(self.HOTKEY,      self._hotkey_end_session,  suppress=False)
        keyboard.add_hotkey(self.MOVE_HOTKEY, self._hotkey_move_overlay, suppress=False)

    def _hotkey_end_session(self):
        if self._session:
            if self._goal_reached:
                self.root.after(0, self._stop_session)
            else:
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
        if self._goal_reached:
            self._do_quit()
        else:
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
