"""Shared fixtures for my--father-mother tests."""

import sqlite3
import sys
from pathlib import Path

import pytest

# Add project root to path so we can import main
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main as mfm


@pytest.fixture
def conn():
    """In-memory SQLite connection with full schema initialized."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON;")
    mfm.init_db(c)
    return c


@pytest.fixture
def populated_db(conn):
    """DB with a few clips already inserted."""
    now = "2026-01-15T10:00:00+00:00"
    clips = [
        ("hello world", "Terminal", "zsh", now, "hash_a"),
        ("def main():\n    pass", "VSCode", "main.py", now, "hash_b"),
        ("SELECT * FROM users", "DataGrip", "query.sql", now, "hash_c"),
    ]
    for content, app, window, ts, h in clips:
        conn.execute(
            "INSERT INTO clips (created_at, source_app, window_title, content, hash, pinned, lang) VALUES (?,?,?,?,?,0,'unk')",
            (ts, app, window, content, h),
        )
    conn.commit()
    # Manually populate FTS (triggers only fire on real inserts through the trigger)
    for row in conn.execute("SELECT id, content FROM clips").fetchall():
        conn.execute(
            "INSERT INTO clips_fts(rowid, content) VALUES (?, ?)",
            (row["id"], row["content"]),
        )
    conn.commit()
    return conn
