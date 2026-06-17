import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "/data/mailtrack_hunter.db")


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS processed_emails (
                message_id TEXT PRIMARY KEY,
                folder     TEXT NOT NULL,
                processed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tracker_candidates (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                domain           TEXT NOT NULL UNIQUE,
                url_example      TEXT NOT NULL,
                source_message_id TEXT,
                source_sender    TEXT,
                source_subject   TEXT,
                found_at         TEXT NOT NULL,
                claude_reasoning TEXT,
                status           TEXT NOT NULL DEFAULT 'pending'
            );

            CREATE TABLE IF NOT EXISTS scan_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at      TEXT NOT NULL,
                folder          TEXT NOT NULL,
                emails_checked  INTEGER NOT NULL DEFAULT 0,
                new_candidates  INTEGER NOT NULL DEFAULT 0
            );
        """)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def is_initial_run() -> bool:
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0]
    return count == 0
