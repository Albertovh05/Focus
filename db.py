"""Session history storage using SQLite."""
import sqlite3, os, json
from contextlib import contextmanager
from datetime import datetime

DEFAULT_DOMAIN_SUGGESTIONS = (
    "youtube.com",
    "instagram.com",
    "x.com",
    "tiktok.com",
    "chess.com",
)


def _db_path() -> str:
    import sys
    if sys.platform == 'win32':
        app_data = os.environ.get("LOCALAPPDATA")
        if app_data:
            return os.path.join(app_data, "Focus", "focus_history.db")
    elif sys.platform == 'darwin':
        home = os.path.expanduser("~")
        return os.path.join(home, "Library", "Application Support", "Focus", "focus_history.db")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "focus_history.db")


DB_PATH = _db_path()


@contextmanager
def _conn():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
        _ensure_column(c, "sessions", "blocked_app_attempts", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(c, "sessions", "blocked_site_attempts", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(c, "sessions", "emergency_pass_seconds", "INTEGER NOT NULL DEFAULT 0")
        c.execute("""
            CREATE TABLE IF NOT EXISTS blocked_domains (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL UNIQUE
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS domain_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL UNIQUE
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                duration_minutes INTEGER,
                allowed_processes TEXT NOT NULL,
                blocked_domains TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        c.executemany(
            "INSERT OR IGNORE INTO domain_suggestions (domain) VALUES (?)",
            [(domain,) for domain in DEFAULT_DOMAIN_SUGGESTIONS],
        )
        c.execute("""
            INSERT OR IGNORE INTO domain_suggestions (domain)
            SELECT domain FROM blocked_domains
        """)
        c.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, spec: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if column not in {row["name"] for row in rows}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")


def save_session(
    start_dt: datetime,
    end_dt: datetime,
    allowed_windows: list[str],
    blocked_app_attempts: int = 0,
    blocked_site_attempts: int = 0,
    emergency_pass_seconds: int = 0,
):
    duration = int((end_dt - start_dt).total_seconds())
    with _conn() as c:
        c.execute(
            """
            INSERT INTO sessions (
                date, start_time, end_time, duration_seconds, allowed_windows,
                blocked_app_attempts, blocked_site_attempts, emergency_pass_seconds
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                start_dt.strftime("%Y-%m-%d"),
                start_dt.strftime("%H:%M:%S"),
                end_dt.strftime("%H:%M:%S"),
                duration,
                json.dumps(allowed_windows),
                int(blocked_app_attempts),
                int(blocked_site_attempts),
                int(emergency_pass_seconds),
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


def get_blocked_domains() -> list[str]:
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT domain FROM blocked_domains ORDER BY domain").fetchall()
    return [row["domain"] for row in rows]


def add_blocked_domain(domain: str) -> None:
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO blocked_domains (domain) VALUES (?)", (domain,))
        c.commit()


def get_domain_suggestions() -> list[str]:
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT domain FROM domain_suggestions ORDER BY id").fetchall()
    return [row["domain"] for row in rows]


def add_domain_suggestion(domain: str) -> None:
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO domain_suggestions (domain) VALUES (?)", (domain,))
        c.commit()


def remove_blocked_domain(domain: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM blocked_domains WHERE domain = ?", (domain,))
        c.commit()


def get_profiles() -> list[dict]:
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM profiles ORDER BY name COLLATE NOCASE").fetchall()
    profiles = []
    for row in rows:
        item = dict(row)
        item["allowed_processes"] = json.loads(item["allowed_processes"])
        item["blocked_domains"] = json.loads(item["blocked_domains"])
        profiles.append(item)
    return profiles


def get_profile(name: str) -> dict | None:
    init_db()
    with _conn() as c:
        row = c.execute("SELECT * FROM profiles WHERE name = ?", (name,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["allowed_processes"] = json.loads(item["allowed_processes"])
    item["blocked_domains"] = json.loads(item["blocked_domains"])
    return item


def save_profile(
    name: str,
    duration_minutes: int | None,
    allowed_processes: list[str],
    blocked_domains: list[str],
) -> None:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Profile name is required")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    processes = sorted({p.lower() for p in allowed_processes if p.strip()})
    domains = sorted({d.lower() for d in blocked_domains if d.strip()})
    with _conn() as c:
        c.execute(
            """
            INSERT INTO profiles (
                name, duration_minutes, allowed_processes, blocked_domains, created_at, updated_at
            ) VALUES (?,?,?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
                duration_minutes = excluded.duration_minutes,
                allowed_processes = excluded.allowed_processes,
                blocked_domains = excluded.blocked_domains,
                updated_at = excluded.updated_at
            """,
            (
                clean_name,
                duration_minutes,
                json.dumps(processes),
                json.dumps(domains),
                now,
                now,
            ),
        )
        c.commit()


def delete_profile(name: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM profiles WHERE name = ?", (name.strip(),))
        c.commit()


def fmt_duration(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


init_db()
