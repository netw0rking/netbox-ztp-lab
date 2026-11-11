import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config_renders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            serial_number TEXT NOT NULL,
            device_name TEXT NOT NULL,
            platform TEXT NOT NULL,
            rendered_at TEXT NOT NULL,
            config_text TEXT NOT NULL
        )
        """
    )
    return conn


def record_render(db_path, serial_number, device_name, platform, config_text):
    """Persist a copy of every config actually served, so a device that
    booted wrong can be diagnosed against what it was really handed at
    that moment - render_config() re-renders live on every request and
    keeps no history of its own."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO config_renders "
            "(serial_number, device_name, platform, rendered_at, config_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                serial_number,
                device_name,
                platform,
                datetime.now(timezone.utc).isoformat(),
                config_text,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_renders(db_path, serial_number=None, limit=50):
    """Most-recent-first history of served configs. Returns an empty list
    if the DB file doesn't exist yet (nothing rendered since deploy) -
    same "optional, absent means empty" contract as users.load_users."""
    if not Path(db_path).exists():
        return []
    conn = _connect(db_path)
    try:
        if serial_number is not None:
            cursor = conn.execute(
                "SELECT serial_number, device_name, platform, rendered_at, config_text "
                "FROM config_renders WHERE serial_number = ? "
                "ORDER BY id DESC LIMIT ?",
                (serial_number, limit),
            )
        else:
            cursor = conn.execute(
                "SELECT serial_number, device_name, platform, rendered_at, config_text "
                "FROM config_renders ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        conn.close()
