"""Session history storage using SQLite."""
import sqlite3, os, json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "focus_history.db")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                allowed_windows TEXT NOT NULL
            )
        """)
        c.commit()


def save_session(start_dt: datetime, end_dt: datetime, allowed_windows: list[str]):
    duration = int((end_dt - start_dt).total_seconds())
    with _conn() as c:
        c.execute(
            "INSERT INTO sessions (date, start_time, end_time, duration_seconds, allowed_windows) VALUES (?,?,?,?,?)",
            (
                start_dt.strftime("%Y-%m-%d"),
                start_dt.strftime("%H:%M:%S"),
                end_dt.strftime("%H:%M:%S"),
                duration,
                json.dumps(allowed_windows),
            ),
        )
        c.commit()


def get_sessions() -> list[dict]:
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM sessions ORDER BY id DESC").fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["allowed_windows"] = json.loads(d["allowed_windows"])
        result.append(d)
    return result


def fmt_duration(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


init_db()
