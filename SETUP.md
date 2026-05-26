# Focus — Setup & Build Guide

## Quick Start (pre-built .exe)

If you have `dist/Focus.exe` already:
1. Double-click `Focus.exe` to launch.
2. Optionally create a desktop shortcut by right-clicking the .exe → *Send to → Desktop (create shortcut)*.

---

## First-Time Build from Source

### Prerequisites

| Requirement | Version |
|-------------|---------|
| Python      | 3.11 or newer |
| pip         | included with Python |
| Windows     | 10 or 11 (64-bit) |

> **Important:** Use the standard Python installer from python.org. The Microsoft Store version of Python may cause issues with `pywin32`.

### Steps

```
1. Open a terminal (cmd or PowerShell) in this project folder.
2. Run:   build.bat
3. When finished, find the executable at:   dist\Focus.exe
```

The build script will:
- Install all Python dependencies from `requirements.txt`
- Generate the app icon (`focus_icon.ico`)
- Run PyInstaller to produce a standalone single-file `.exe`

---

## Running from Source (no build needed)

```
pip install -r requirements.txt
python main.py
```

---

## Usage

### Session Tab
1. Click **↻ Refresh Windows** to list all currently open windows.
2. Check the boxes next to windows you want to stay allowed.
3. Click **▶ Start Session**.
   - An always-on-top timer overlay appears in the corner — drag it anywhere.
   - If you switch to a disallowed window, Focus immediately returns you to the last allowed one.

### Ending a Session
- Click **■ End Session** in the app, or press **Ctrl+Shift+J** from anywhere.
- Confirm the prompt (press **Y** or click the button).
- The session is saved automatically.

### History Tab
- Switch to the **History** tab to see all past sessions.
- Total accumulated study time is shown at the bottom.

---

## File Locations

| File                  | Purpose |
|-----------------------|---------|
| `main.py`             | Main application |
| `session_manager.py`  | Window enforcement logic |
| `overlay.py`          | Always-on-top timer widget |
| `db.py`               | SQLite session storage |
| `icon_gen.py`         | Generates `focus_icon.ico` |
| `requirements.txt`    | Python dependencies |
| `build.bat`           | Builds `dist/Focus.exe` |
| `focus_history.db`    | Created on first run; stores session history |

---

## Troubleshooting

**Window switching not enforced?**  
Run Focus as Administrator (right-click Focus.exe → *Run as administrator*). Some windows (Task Manager, elevated apps) require admin rights to force focus.

**Build fails with "win32api not found"?**  
Run `pip install pywin32` separately, then `python Scripts/pywin32_postinstall.py -install` from your Python install directory.

**Hotkey Ctrl+Shift+J not working?**  
Another application may have registered that hotkey. Close other apps and retry, or modify `HOTKEY = "ctrl+shift+j"` in `main.py` to a different combination.
