# Focus — Project Handout

A Windows productivity app that locks you into a set of allowed windows during a timed focus session. It enforces focus by intercepting foreground-window changes and yanking focus back to the last allowed window whenever the user tries to switch to a disallowed one.

---

## What it does

1. User opens the app from the system tray, selects which open windows are allowed, and picks a session duration via preset chips: 10, 15, 20, 25, 30, 45, or 60 min.
2. Hitting "Start Session" activates the enforcer — any window not in the allowed list is immediately defocused.
3. An always-on-top timer overlay shows elapsed time vs. the goal; the timer turns green when the goal is reached.
4. When the goal time is hit, a "Session Complete!" dialog pops up automatically — the user can choose to keep going or end the session cleanly (no friction at this point).
5. Stopping the session *before* the goal requires clicking "End Session" (or `Ctrl+Shift+J`) and reading through 3 motivational messages, each with a 5-second countdown before advancing — deliberate friction to prevent impulsive quitting.
6. Sessions are saved to a local SQLite database; the History tab shows all past sessions (date, start/end times, duration, allowed windows) with a running total.

---

## Architecture

```
main.py            — App entry point, tkinter UI (Session tab + History tab)
session_manager.py — Window enforcement via WinEvent hook + timer thread
overlay.py         — Always-on-top draggable timer widget (click-through by default)
db.py              — SQLite persistence (focus_history.db, written next to the exe)
icon_gen.py        — Generates focus_icon.ico at startup (or build time) if missing
```

### Threading model

| Thread | What it does |
|--------|-------------|
| Main (tkinter) | UI event loop, driven by `self._phantom.mainloop()` |
| `_hook_thread` | Windows message loop that receives `EVENT_SYSTEM_FOREGROUND` WinEvent callbacks |
| `_timer_thread` | 1-second tick loop; calls `on_tick(elapsed)` which posts to the UI via `root.after()` |
| pystray thread | Daemon thread running the system tray icon message loop |

### Taskbar hiding — phantom root pattern

The app uses a hidden `tk.Tk()` phantom root that stays withdrawn for the entire lifetime of the process. The actual main window is a `tk.Toplevel` child of it. On Windows, `Toplevel` windows whose parent is withdrawn never appear in the Windows taskbar. This is how the app lives only in the system tray.

```
self._phantom = tk.Tk()          # stays withdrawn forever, drives mainloop()
self.root     = tk.Toplevel(...)  # real window — no taskbar entry
self._overlay.win = tk.Toplevel(self.root)  # timer overlay
```

### Window enforcement (SessionManager)

- Registers a `SetWinEventHook` for `EVENT_SYSTEM_FOREGROUND` events.
- On each foreground change: checks if the new window's process name is in `allowed_procs` or belongs to the Focus app's own PID.
- If disallowed: spawns a short-lived thread that sleeps 50 ms then calls `SetForegroundWindow` on the last known allowed HWND, using `AttachThreadInput` to borrow foreground rights.
- The overlay's HWND is explicitly excluded as a refocus target (it's click-through and topmost).

---

## Files

| File | Purpose |
|------|---------|
| `main.py` | UI, tray icon, session wiring |
| `session_manager.py` | Enforcement logic, WinEvent hook |
| `overlay.py` | Timer overlay widget |
| `db.py` | SQLite read/write |
| `icon_gen.py` | ICO generation — called at startup if `focus_icon.ico` is missing |
| `focus_icon.ico` | App icon (clock face, dark theme) |
| `focus_history.db` | Created at runtime next to the exe |
| `requirements.txt` | Python dependencies |
| `build.bat` | Build script (calls PyInstaller) |
| `Focus.spec` | PyInstaller spec for reproducible builds |
| `dist/Focus.exe` | Distributable single-file exe (~168 MB) |

---

## Dependencies

```
pywin32       — win32gui, win32process, win32con (WinEvent hook, HWND operations)
keyboard      — Global hotkey registration (Ctrl+Shift+J, Ctrl+Shift+M)
Pillow        — Icon generation and loading the ICO for the tray
psutil        — Resolving window HWNDs to process names
pystray       — System tray icon and menu
pyinstaller   — Building the standalone exe
```

---

## Building

```bat
build.bat          # release: produces dist\Focus.exe (single file)
build.bat fast     # dev: produces dist\Focus\Focus.exe (faster, uses cache)
```

Before rebuilding, kill any running `Focus.exe` first — PyInstaller cannot overwrite a locked file.

---

## Runtime behavior

| Action | Result |
|--------|--------|
| Launch `Focus.exe` | Silently starts in system tray, no window, no taskbar entry |
| Left-click / double-click tray icon | Opens the main window |
| Right-click tray icon | Menu: "Open Focus" / "Exit" |
| Close (X) the main window | Hides back to tray; active session keeps running |
| Tray → Exit (no session) | Quits immediately |
| Tray → Exit (session active, goal not reached) | Opens main window, shows motivational dialog |
| Tray → Exit (session active, goal reached) | Ends session and quits immediately — no motivational dialog |
| Goal time reached | "Session Complete!" dialog auto-opens; choose "Keep Going" or "End Session" |
| `Ctrl+Shift+J` (goal not yet reached) | Triggers motivational exit dialog |
| `Ctrl+Shift+J` (goal already reached) | Ends session immediately |
| `Ctrl+Shift+M` | Toggles overlay between click-through and draggable |

---

## Key design decisions

- **Process-level allow list, not window-level** — every window of an allowed app (e.g. every Chrome tab) is permitted. This avoids the need to re-allow windows when tabs change.
- **Motivational friction on exit** — the user must read 3 messages before ending a session early, each with a 5-second countdown before the "next" button unlocks. This is intentional, not a bug.
- **Session continues when window is hidden** — closing the main window only hides the UI; the enforcer and overlay keep running until the session is explicitly ended.
- **SQLite next to the exe** — `focus_history.db` is written to the same directory as the running script/exe so data persists between version updates without any config.
- **`AttachThreadInput` for reliable refocus** — plain `SetForegroundWindow` is blocked by Windows unless the calling thread owns foreground rights; borrowing them from the current foreground thread via `AttachThreadInput` is what makes enforcement reliable.

---

## Color tokens (defined at top of main.py)

```python
C_BG      = "#0d1117"   # near-black background
C_SURFACE = "#161b22"   # slightly lighter surface
C_BORDER  = "#30363d"
C_TEXT    = "#e6edf3"
C_MUTED   = "#8b949e"
C_ACCENT  = "#63b3ed"   # blue (primary accent)
C_ACCENT2 = "#388bfd"   # brighter blue (buttons, selection)
C_SUCCESS = "#3fb950"   # green
C_DANGER  = "#f85149"   # red
C_HEADER  = "#1c2230"   # dark blue-grey (treeview headings, overlay frame)
```

The dark theme is GitHub-inspired and intentionally non-configurable — the app has one look.
