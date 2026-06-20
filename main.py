#!/usr/bin/env python3
"""
my--father-mother: lightweight local clipboard long-term memory.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import platform
import http.server
import socketserver
import urllib.parse
import urllib.request
import sqlite3
import subprocess
import sys
import time
import shutil
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Tuple, Iterable, Dict

DB_DIR = Path.home() / ".my-father-mother"
DB_PATH = DB_DIR / "mfm.db"
ICLOUD_DRIVE_DIR = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"

MOTHER = "mother"  # capture persona (moon domain)
FATHER = "father"  # retrieval persona (sun domain)
MFM_VERSION = "dev"

DEFAULT_COPILOT_MODEL = "gemini-2.5-flash"
DEFAULT_COPILOT_ACCENT = "pink"
DEFAULT_ML_CONTEXT_LEVEL = "medium"
DEFAULT_ML_PROCESSING_MODE = "blended"
DEFAULT_LTM_ENABLED = True
DEFAULT_UI_THEME = "system"
DEFAULT_UI_ACCENT = "pink"
DEFAULT_UI_FONT_SIZE = "100%"
DEFAULT_UI_FONT_WEIGHT = "thin"
DEFAULT_UI_DENSITY = "standard"
DEFAULT_UI_VIEW = "workstream_activity"
DEFAULT_UI_CONFIRMATIONS = "default"
DEFAULT_UI_METRICS = "default"
DEFAULT_UI_TOOLBAR = "suggested"
DEFAULT_PERSONAL_CLOUD_STATUS = "disconnected"

DEFAULT_MAX_BYTES = 16_384  # ~16 KB
DEFAULT_NOTIFY = False
DEFAULT_WATCH_SYNC_INTERVAL = 60.0
DEFAULT_EMBEDDER = "hash"  # or "e5-small"
E5_MODEL_NAME = "intfloat/e5-small-v2"
EMBED_DIM = 128
DEFAULT_EVICT_MODE = "fifo"  # fifo|tiered
DEFAULT_ALLOW_PDF = False
DEFAULT_ALLOW_IMAGES = False
DEFAULT_UPGRADE_URL = "https://gumroad.com/l/my-father-mother-pro"
GUMROAD_VERIFY_URL = "https://api.gumroad.com/v2/licenses/verify"
ALLOWED_EXTS = {
    ".txt",
    ".md",
    ".markdown",
    ".log",
    ".json",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".sh",
    ".zsh",
    ".bash",
    ".fish",
    ".php",
    ".cs",
    ".cpp",
    ".cxx",
    ".cc",
    ".h",
    ".hpp",
    ".m",
    ".mm",
    ".swift",
    ".scala",
    ".sql",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
}
DEFAULT_MAX_DB_MB = 512  # cap database size in MB by default

SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"ASIA[0-9A-Z]{16}"),  # AWS STS key
    re.compile(r"(?i)aws(.{0,20})?(secret|access).{0,20}?([0-9a-zA-Z/+]{40})"),
    re.compile(r"ghp_[0-9A-Za-z]{36}"),  # GitHub PAT
    re.compile(r"xox[abprs]-[0-9A-Za-z-]{10,48}"),  # Slack tokens
    re.compile(r"-----BEGIN (?:RSA|DSA|EC|OPENSSH) PRIVATE KEY"),
    re.compile(r"ssh-rsa [0-9A-Za-z+/]+={0,3}"),
    re.compile(r"(?i)apikey[^a-z0-9]?[:=][^\\s]{8,}"),
    re.compile(r"(?i)password[^a-z0-9]?[:=][^\\s]{6,}"),
]


def say(persona: str, message: str) -> None:
    """Print a message with persona prefix."""
    print(f"[{persona}] {message}")


def toast(title: str, text: str) -> None:
    """Fire a lightweight macOS notification (best-effort)."""
    title_safe = title.replace('"', "'")
    text_safe = text.replace('"', "'")
    try:
        # Avoid escaping woes by passing via -e arguments separately.
        subprocess.run(
            ["osascript", "-e", f'display notification "{text_safe}" with title "{title_safe}"'],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return


# ---------- Database setup ----------
def connect_db() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row["name"] == column for row in cur.fetchall())


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            source_app TEXT,
            window_title TEXT,
            content TEXT NOT NULL,
            hash TEXT NOT NULL,
            lang TEXT DEFAULT 'unk'
        );

        CREATE INDEX IF NOT EXISTS idx_clips_hash ON clips(hash);
        CREATE INDEX IF NOT EXISTS idx_clips_created_at ON clips(created_at);

        CREATE VIRTUAL TABLE IF NOT EXISTS clips_fts USING fts5(
            content,
            content='clips',
            content_rowid='id'
        );

        CREATE TRIGGER IF NOT EXISTS clips_ai AFTER INSERT ON clips BEGIN
            INSERT INTO clips_fts(rowid, content) VALUES (new.id, new.content);
        END;

        CREATE TRIGGER IF NOT EXISTS clips_ad AFTER DELETE ON clips BEGIN
            INSERT INTO clips_fts(clips_fts, rowid, content) VALUES('delete', old.id, old.content);
        END;

        CREATE TRIGGER IF NOT EXISTS clips_au AFTER UPDATE ON clips BEGIN
            INSERT INTO clips_fts(clips_fts, rowid, content) VALUES('delete', old.id, old.content);
            INSERT INTO clips_fts(rowid, content) VALUES (new.id, new.content);
        END;

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS blocklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app TEXT UNIQUE NOT NULL
        );
        """
    )
    # migrations
    if not column_exists(conn, "clips", "pinned"):
        conn.execute("ALTER TABLE clips ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
    if not column_exists(conn, "clips", "title"):
        conn.execute("ALTER TABLE clips ADD COLUMN title TEXT")
    if not column_exists(conn, "clips", "file_path"):
        conn.execute("ALTER TABLE clips ADD COLUMN file_path TEXT")
    if not column_exists(conn, "clips", "lang"):
        conn.execute("ALTER TABLE clips ADD COLUMN lang TEXT DEFAULT 'unk'")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS clip_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clip_id INTEGER NOT NULL,
            seen_at TEXT NOT NULL,
            FOREIGN KEY (clip_id) REFERENCES clips(id) ON DELETE CASCADE
        );
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clip_events_clip ON clip_events(clip_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clip_events_seen ON clip_events(seen_at)")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS clip_vectors (
            clip_id INTEGER PRIMARY KEY,
            dim INTEGER NOT NULL,
            vector TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT 'hash',
            FOREIGN KEY (clip_id) REFERENCES clips(id) ON DELETE CASCADE
        );
        """
    )
    if not column_exists(conn, "clip_vectors", "model"):
        conn.execute("ALTER TABLE clip_vectors ADD COLUMN model TEXT NOT NULL DEFAULT 'hash'")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS clip_tags (
            clip_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (clip_id, tag_id),
            FOREIGN KEY (clip_id) REFERENCES clips(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_clip_tags_clip ON clip_tags(clip_id);
        CREATE INDEX IF NOT EXISTS idx_clip_tags_tag ON clip_tags(tag_id);
        """
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS clip_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clip_id INTEGER NOT NULL,
            note TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (clip_id) REFERENCES clips(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_clip_notes_clip ON clip_notes(clip_id);
        """
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS copilot_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            title TEXT,
            model TEXT,
            content TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_copilot_chats_created ON copilot_chats(created_at);
        """
    )
    conn.commit()


# ---------- Clipboard + metadata ----------
def read_clipboard() -> Optional[str]:
    try:
        out = subprocess.check_output(["pbpaste"], stderr=subprocess.DEVNULL)
        text = out.decode("utf-8", errors="ignore")
        return text if text else None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def frontmost_app_and_window() -> Tuple[str, str]:
    app = "unknown"
    window = ""
    try:
        app_script = 'tell application "System Events" to get name of first process whose frontmost is true'
        app = (
            subprocess.check_output(
                ["osascript", "-e", app_script], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        pass
    try:
        window_script = (
            'tell application "System Events" to tell (first process whose frontmost is true) '
            "to get name of front window"
        )
        window = (
            subprocess.check_output(
                ["osascript", "-e", window_script], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        pass
    return app, window


# ---------- Persistence helpers ----------
def clip_exists(conn: sqlite3.Connection, digest: str) -> bool:
    cur = conn.execute("SELECT 1 FROM clips WHERE hash = ? ORDER BY id DESC LIMIT 1", (digest,))
    return cur.fetchone() is not None


def get_clip_id_by_hash(conn: sqlite3.Connection, digest: str) -> Optional[int]:
    cur = conn.execute("SELECT id FROM clips WHERE hash = ? ORDER BY id DESC LIMIT 1", (digest,))
    row = cur.fetchone()
    return row["id"] if row else None


def insert_clip(
    conn: sqlite3.Connection,
    content: str,
    app: str,
    window: str,
    digest: Optional[str] = None,
    title: Optional[str] = None,
    file_path: Optional[str] = None,
    embedder_override: Optional[str] = None,
) -> Optional[int]:
    clean = content.strip()
    if not clean:
        return None
    digest = digest or hashlib.sha256(clean.encode("utf-8")).hexdigest()
    if clip_exists(conn, digest):
        return None
    lang = detect_language(clean)
    created_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO clips (created_at, source_app, window_title, content, hash, title, pinned, file_path, lang)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (created_at, app, window, clean, digest, title, file_path, lang),
    )
    conn.commit()
    clip_id = cur.lastrowid
    vec, model = embed_text(conn, clean, embedder_override)
    store_embedding(conn, clip_id, vec, model)
    return clip_id


def insert_clip_import(
    conn: sqlite3.Connection,
    content: str,
    app: str,
    window: str,
    created_at: Optional[str],
    pinned: bool,
    title: Optional[str],
    file_path: Optional[str],
    lang: Optional[str],
    tags: Optional[list[str]],
) -> Optional[int]:
    clean = content.strip()
    if not clean:
        return None
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    existing = get_clip_id_by_hash(conn, digest)
    if existing:
        insert_event(conn, existing)
        return existing
    created = created_at or datetime.now(timezone.utc).isoformat()
    lang_val = lang or detect_language(clean)
    cur = conn.execute(
        """
        INSERT INTO clips (created_at, source_app, window_title, content, hash, title, pinned, file_path, lang)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (created, app, window, clean, digest, title, 1 if pinned else 0, file_path, lang_val),
    )
    conn.commit()
    clip_id = cur.lastrowid
    vec, model = embed_text(conn, clean, None)
    store_embedding(conn, clip_id, vec, model)
    if tags:
        for t in tags:
            assign_tag(conn, clip_id, t)
    return clip_id


def export_items(
    conn: sqlite3.Connection,
    limit: int,
    app: Optional[str] = None,
    tag: Optional[str] = None,
    since_iso: Optional[str] = None,
    pins_only: bool = False,
) -> list[dict]:
    rows_db, tag_map = filtered_rows(
        conn,
        limit,
        app=app,
        contains=None,
        tag=tag,
        pins_only=pins_only,
        since_iso=since_iso,
        until_iso=None,
    )
    notes_map = notes_for_clips(conn, [row["id"] for row in rows_db])
    items = []
    for row in rows_db:
        items.append(
            dict(
                id=row["id"],
                created_at=row["created_at"],
                source_app=row["source_app"],
                window_title=row["window_title"],
                content=row["content"],
                pinned=bool(row["pinned"]),
                title=row["title"],
                file_path=row["file_path"],
                lang=row["lang"],
                tags=tag_map.get(row["id"], []),
                notes=notes_map.get(row["id"], []),
            )
        )
    return items


def ingest_text(
    conn: sqlite3.Connection,
    content: str,
    app: str,
    window: str,
    digest: Optional[str] = None,
    title: Optional[str] = None,
    file_path: Optional[str] = None,
    embedder_override: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> Optional[int]:
    clip_id = insert_clip(
        conn,
        content=content,
        app=app,
        window=window,
        digest=digest,
        title=title,
        file_path=file_path,
        embedder_override=embedder_override,
    )
    if clip_id and tags:
        for t in tags:
            assign_tag(conn, clip_id, t)
    return clip_id


def insert_event(conn: sqlite3.Connection, clip_id: int) -> None:
    conn.execute(
        "INSERT INTO clip_events (clip_id, seen_at) VALUES (?, ?)",
        (clip_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def prune(conn: sqlite3.Connection, cap: int) -> int:
    if cap <= 0:
        return 0
    cur = conn.execute(
        """
        DELETE FROM clips
        WHERE id NOT IN (
            SELECT id FROM clips ORDER BY id DESC LIMIT ?
        );
        """,
        (cap,),
    )
    conn.commit()
    return cur.rowcount


def evict_app_cap(conn: sqlite3.Connection, app: str, cap: int) -> int:
    """Enforce per-app cap; prefer deleting non-pinned oldest first."""
    if cap <= 0 or not app:
        return 0
    cur = conn.execute(
        "SELECT id, pinned FROM clips WHERE LOWER(source_app)=LOWER(?) ORDER BY created_at ASC",
        (app,),
    )
    rows = cur.fetchall()
    over = len(rows) - cap
    if over <= 0:
        return 0
    evicted = 0
    # First non-pinned
    for row in rows:
        if evicted >= over:
            break
        if row["pinned"]:
            continue
        conn.execute("DELETE FROM clips WHERE id = ?", (row["id"],))
        evicted += 1
    if evicted < over:
        remaining = over - evicted
        pinned_rows = [r for r in rows if r["pinned"]]
        for row in pinned_rows[:remaining]:
            conn.execute("DELETE FROM clips WHERE id = ?", (row["id"],))
            evicted += 1
    conn.commit()
    return evicted


def evict_tag_cap(conn: sqlite3.Connection, tag: str, cap: int) -> int:
    """Enforce per-tag cap after tagging."""
    if cap <= 0 or not tag:
        return 0
    cur = conn.execute(
        """
        SELECT c.id, c.pinned FROM clips c
        JOIN clip_tags ct ON ct.clip_id = c.id
        JOIN tags t ON t.id = ct.tag_id
        WHERE LOWER(t.name) = LOWER(?)
        ORDER BY c.created_at ASC
        """,
        (tag,),
    )
    rows = cur.fetchall()
    over = len(rows) - cap
    if over <= 0:
        return 0
    evicted = 0
    for row in rows:
        if evicted >= over:
            break
        if row["pinned"]:
            continue
        conn.execute("DELETE FROM clips WHERE id = ?", (row["id"],))
        evicted += 1
    if evicted < over:
        remaining = over - evicted
        pinned_rows = [r for r in rows if r["pinned"]]
        for row in pinned_rows[:remaining]:
            conn.execute("DELETE FROM clips WHERE id = ?", (row["id"],))
            evicted += 1
    conn.commit()
    return evicted


def evict_if_needed(conn: sqlite3.Connection, max_db_mb: int) -> int:
    if max_db_mb <= 0:
        return 0
    size_mb = DB_PATH.stat().st_size / (1024 * 1024) if DB_PATH.exists() else 0
    evicted = 0
    if size_mb > max_db_mb:
        # delete oldest 5% of rows until under cap (approximate)
        cur_count = conn.execute("SELECT COUNT(*) as c FROM clips").fetchone()
        total = cur_count["c"] if cur_count else 0
        if total > 0:
            batch = max(1, int(total * 0.05))
            mode = get_evict_mode(conn)
            if mode == "tiered":
                # prefer non-pinned
                cur = conn.execute(
                    """
                    DELETE FROM clips
                    WHERE id IN (
                        SELECT id FROM clips WHERE pinned=0 ORDER BY created_at ASC LIMIT ?
                    )
                    """,
                    (batch,),
                )
                evicted = cur.rowcount
                if evicted < batch:
                    remaining = batch - evicted
                    cur2 = conn.execute(
                        "DELETE FROM clips WHERE id IN (SELECT id FROM clips ORDER BY created_at ASC LIMIT ?)",
                        (remaining,),
                    )
                    evicted += cur2.rowcount
            else:
                cur = conn.execute(
                    "DELETE FROM clips WHERE id IN (SELECT id FROM clips ORDER BY created_at ASC LIMIT ?)",
                    (batch,),
                )
                evicted = cur.rowcount
            conn.commit()
    return evicted


def stats(conn: sqlite3.Connection) -> dict:
    cur = conn.execute(
        "SELECT COUNT(*) as count, MAX(created_at) as latest FROM clips"
    )
    row = cur.fetchone()
    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    return {
        "count": row["count"] if row else 0,
        "latest": row["latest"],
        "db_size_bytes": db_size,
    }


def usage_snapshot(conn: sqlite3.Connection, top_limit: int = 5, days: int = 7) -> dict:
    top_limit = max(1, min(int(top_limit), 20))
    days = max(1, min(int(days), 31))
    base = stats(conn)

    def count_value(sql: str, params: tuple = ()) -> int:
        row = conn.execute(sql, params).fetchone()
        return int(row[0] or 0) if row else 0

    total_clips = int(base.get("count") or 0)
    first_row = conn.execute("SELECT MIN(created_at) AS first FROM clips").fetchone()
    size_row = conn.execute(
        """
        SELECT
            COALESCE(SUM(LENGTH(content)), 0) AS content_chars,
            COALESCE(AVG(LENGTH(content)), 0) AS avg_clip_chars
        FROM clips
        """
    ).fetchone()
    db_size_bytes = int(base.get("db_size_bytes") or 0)
    db_size_mb = round(db_size_bytes / (1024 * 1024), 3)
    max_db_mb = get_max_db_mb(conn, None)
    storage_pct = round((db_size_mb / max_db_mb) * 100, 1) if max_db_mb else None

    top_apps = [
        {
            "name": row["name"],
            "count": row["clip_count"],
            "latest": row["latest"],
        }
        for row in conn.execute(
            """
            SELECT
                COALESCE(NULLIF(source_app, ''), 'unknown') AS name,
                COUNT(*) AS clip_count,
                MAX(created_at) AS latest
            FROM clips
            GROUP BY COALESCE(NULLIF(source_app, ''), 'unknown')
            ORDER BY clip_count DESC, latest DESC
            LIMIT ?
            """,
            (top_limit,),
        ).fetchall()
    ]
    top_tags = [
        {
            "name": row["name"],
            "count": row["clip_count"],
            "latest": row["latest"],
        }
        for row in conn.execute(
            """
            SELECT t.name, COUNT(ct.clip_id) AS clip_count, MAX(c.created_at) AS latest
            FROM tags t
            JOIN clip_tags ct ON ct.tag_id = t.id
            JOIN clips c ON c.id = ct.clip_id
            GROUP BY t.id, t.name
            ORDER BY clip_count DESC, latest DESC
            LIMIT ?
            """,
            (top_limit,),
        ).fetchall()
    ]
    daily_counts = [
        {"day": row["day"], "count": row["clip_count"]}
        for row in conn.execute(
            """
            SELECT date(created_at) AS day, COUNT(*) AS clip_count
            FROM clips
            WHERE datetime(created_at) >= datetime('now', ?)
            GROUP BY date(created_at)
            ORDER BY day ASC
            """,
            (f"-{days} days",),
        ).fetchall()
    ]
    embedding_models = [
        {"model": row["model"], "count": row["clip_count"]}
        for row in conn.execute(
            """
            SELECT model, COUNT(*) AS clip_count
            FROM clip_vectors
            GROUP BY model
            ORDER BY clip_count DESC, model ASC
            """
        ).fetchall()
    ]
    vector_count = sum(item["count"] for item in embedding_models)
    vector_coverage = round((vector_count / total_clips) * 100, 1) if total_clips else 0.0

    return {
        "total_clips": total_clips,
        "pinned_clips": count_value("SELECT COUNT(*) FROM clips WHERE pinned = 1"),
        "tagged_clips": count_value("SELECT COUNT(DISTINCT clip_id) FROM clip_tags"),
        "tag_count": count_value("SELECT COUNT(*) FROM tags"),
        "note_count": count_value("SELECT COUNT(*) FROM clip_notes"),
        "repeat_events": count_value("SELECT COUNT(*) FROM clip_events"),
        "clips_last_24h": count_value(
            "SELECT COUNT(*) FROM clips WHERE datetime(created_at) >= datetime('now', '-1 day')"
        ),
        "clips_last_7d": count_value(
            "SELECT COUNT(*) FROM clips WHERE datetime(created_at) >= datetime('now', '-7 days')"
        ),
        "events_last_24h": count_value(
            "SELECT COUNT(*) FROM clip_events WHERE datetime(seen_at) >= datetime('now', '-1 day')"
        ),
        "first_clip_at": first_row["first"] if first_row else None,
        "latest_clip_at": base.get("latest"),
        "content_chars": int(size_row["content_chars"] or 0) if size_row else 0,
        "average_clip_chars": round(float(size_row["avg_clip_chars"] or 0), 1) if size_row else 0.0,
        "storage": {
            "db_size_bytes": db_size_bytes,
            "db_size_mb": db_size_mb,
            "max_db_mb": max_db_mb,
            "used_pct": storage_pct,
        },
        "vector_count": vector_count,
        "vector_coverage_pct": vector_coverage,
        "embedding_models": embedding_models,
        "top_apps": top_apps,
        "top_tags": top_tags,
        "daily_counts": daily_counts,
    }


def status_snapshot(conn: sqlite3.Connection) -> dict:
    usage = usage_snapshot(conn)
    license_info = license_snapshot(conn)
    return {
        "paused": is_paused(conn),
        "allow_secrets": get_allow_secrets(conn, None),
        "notify": get_notify(conn, None),
        "pro_enabled": license_info["pro_enabled"],
        "embedder": get_embedder(conn, None),
        "max_bytes": get_max_bytes(conn, None),
        "max_db_mb": get_max_db_mb(conn, None),
        "cap_by_app": get_cap_map(conn, "cap_by_app"),
        "cap_by_tag": get_cap_map(conn, "cap_by_tag"),
        "evict_mode": get_evict_mode(conn),
        "ltm_enabled": get_bool_setting(conn, "ltm_enabled", DEFAULT_LTM_ENABLED),
        "ml_context_level": get_setting(conn, "ml_context_level", DEFAULT_ML_CONTEXT_LEVEL),
        "ml_processing_mode": get_setting(conn, "ml_processing_mode", DEFAULT_ML_PROCESSING_MODE),
        "count": usage["total_clips"],
        "latest": usage["latest_clip_at"],
        "db_size_mb": usage["storage"]["db_size_mb"],
        "blocklist_size": len(get_blocklist(conn)),
        "sync_target": get_setting(conn, "sync_target", ""),
        "sync_interval": get_sync_interval(conn),
        "license_type": license_info["license_type"],
        "license_status": license_info["license_status"],
        "has_license_key": license_info["has_license_key"],
        "device_count": license_info["device_count"],
        "upgrade_url": license_info["upgrade_url"],
        "usage": usage,
    }


def resolve_sync_target(target: str) -> Path:
    target_raw = str(target).strip()
    if target_raw.lower() == "icloud":
        return ICLOUD_DRIVE_DIR / "mfm.db"
    dest = Path(target_raw).expanduser()
    if dest.is_dir() or target_raw.endswith("/"):
        dest = dest / "mfm.db"
    return dest


def write_db_snapshot(dest: Path, source_conn: Optional[sqlite3.Connection] = None) -> None:
    """Write a consistent SQLite snapshot to dest, including committed WAL pages."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    owned_conn = source_conn is None
    src = source_conn or sqlite3.connect(DB_PATH)
    try:
        src.commit()
        with sqlite3.connect(dest) as dst:
            src.backup(dst)
    finally:
        if owned_conn:
            src.close()


def sync_push(target: str, source_conn: Optional[sqlite3.Connection] = None) -> tuple[bool, str]:
    dest = resolve_sync_target(target)
    if source_conn is None and not DB_PATH.exists():
        return False, "local DB missing; run init?"
    try:
        write_db_snapshot(dest, source_conn)
    except Exception as e:
        return False, f"push failed: {e}"
    return True, f"pushed db snapshot to {dest}"


def sync_pull(target: str) -> tuple[bool, str]:
    src = resolve_sync_target(target)
    if not src.exists():
        return False, f"source not found: {src}"
    if DB_PATH.exists():
        backup = DB_PATH.with_suffix(".bak")
        shutil.copy2(DB_PATH, backup)
    DB_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, DB_PATH)
    return True, f"pulled db from {src}"


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    cur = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def parse_bool_value(value: str) -> Optional[bool]:
    if value is None:
        return None
    val = str(value).strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return None


def get_bool_setting(conn: sqlite3.Connection, key: str, default: bool) -> bool:
    raw = get_setting(conn, key, "1" if default else "0")
    parsed = parse_bool_value(raw)
    return default if parsed is None else parsed


def get_sync_interval(conn: sqlite3.Connection) -> float:
    raw = get_setting(conn, "sync_interval", str(DEFAULT_WATCH_SYNC_INTERVAL)).strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_WATCH_SYNC_INTERVAL


def maybe_watch_sync(
    conn: sqlite3.Connection,
    last_sync_at: Optional[float],
    last_sync_target: str,
) -> tuple[Optional[float], str]:
    target = get_setting(conn, "sync_target", "").strip()
    if not target:
        return last_sync_at, ""

    now = time.monotonic()
    interval = get_sync_interval(conn)
    target_changed = target != last_sync_target
    if target_changed or last_sync_at is None or (now - last_sync_at) >= interval:
        ok, msg = sync_push(target, conn)
        prefix = "watch sync" if ok else "watch sync failed"
        say(MOTHER, f"{prefix}: {msg}")
        return now, target
    return last_sync_at, target


def set_bool_setting(conn: sqlite3.Connection, key: str, value: bool) -> None:
    set_setting(conn, key, "1" if value else "0")


def get_license_key(conn: sqlite3.Connection) -> str:
    """Return the configured Pro license key, supporting the public alias."""
    return (
        get_setting(conn, "gumroad_license_key", "").strip()
        or get_setting(conn, "license_key", "").strip()
    )


def mask_secret(value: str) -> str:
    clean = (value or "").strip()
    if not clean:
        return ""
    if len(clean) <= 8:
        return "***"
    return f"{clean[:4]}...{clean[-4:]}"


def set_license_key(conn: sqlite3.Connection, value: str) -> None:
    clean = (value or "").strip()
    set_setting(conn, "gumroad_license_key", clean)
    set_setting(conn, "license_key", clean)
    if clean:
        set_bool_setting(conn, "pro_enabled", True)
        set_setting(conn, "license_type", "pro")
        set_setting(conn, "license_status", "active")
        set_setting(conn, "license_updated_at", datetime.now(timezone.utc).isoformat())
    else:
        set_bool_setting(conn, "pro_enabled", False)
        set_setting(conn, "license_type", "free")
        set_setting(conn, "license_status", "inactive")
        set_setting(conn, "license_updated_at", datetime.now(timezone.utc).isoformat())


def is_pro_enabled(conn: sqlite3.Connection) -> bool:
    raw = get_setting(conn, "pro_enabled", "").strip()
    if raw:
        parsed = parse_bool_value(raw)
        if parsed is not None:
            return parsed
    return bool(get_license_key(conn))


def set_pro_enabled(conn: sqlite3.Connection, enabled: bool) -> None:
    set_bool_setting(conn, "pro_enabled", enabled)
    set_setting(conn, "license_type", "pro" if enabled else "free")
    if not enabled:
        set_setting(conn, "license_status", "inactive")


def get_upgrade_url(conn: sqlite3.Connection) -> str:
    return get_setting(conn, "upgrade_url", DEFAULT_UPGRADE_URL).strip() or DEFAULT_UPGRADE_URL


def license_snapshot(conn: sqlite3.Connection) -> dict:
    enabled = is_pro_enabled(conn)
    key = get_license_key(conn)
    return {
        "pro_enabled": enabled,
        "license_type": "pro" if enabled else "free",
        "license_status": get_setting(conn, "license_status", "active" if enabled else "inactive"),
        "license_key": mask_secret(key),
        "has_license_key": bool(key),
        "email": get_setting(conn, "gumroad_email", ""),
        "device_count": 1 if enabled else 0,
        "upgrade_url": get_upgrade_url(conn),
    }


def pro_required(feature: str, conn: sqlite3.Connection) -> tuple[bool, str]:
    if is_pro_enabled(conn):
        return True, ""
    return False, f"{feature} requires Pro. Activate with `config --set license_key YOUR_KEY`."


def resolve_embedder_for_use(
    conn: sqlite3.Connection,
    override: Optional[str],
) -> tuple[str, Optional[str]]:
    kind = get_embedder(conn, override)
    if kind == "e5-small" and not is_pro_enabled(conn):
        return "hash", "e5-small embeddings require Pro; using hash similarity"
    return kind, None


def gumroad_webhook_secret(conn: sqlite3.Connection) -> str:
    return os.environ.get("GUMROAD_WEBHOOK_SECRET", "").strip() or get_setting(
        conn, "gumroad_webhook_secret", ""
    ).strip()


def verify_gumroad_signature(body: bytes, signature: str, secret: str) -> bool:
    sig = (signature or "").strip()
    if sig.startswith("sha256="):
        sig = sig.split("=", 1)[1].strip()
    if not sig or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def parse_gumroad_payload(body: bytes, content_type: str) -> dict:
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    text = body.decode("utf-8", errors="ignore")
    if ctype == "application/json":
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    parsed = urllib.parse.parse_qs(text, keep_blank_values=True)
    return {key: vals[-1] if vals else "" for key, vals in parsed.items()}


def validate_gumroad_license(conn: sqlite3.Connection, license_key: str) -> Optional[bool]:
    permalink = get_setting(conn, "gumroad_permalink", "").strip()
    if not permalink or not license_key:
        return None
    payload = urllib.parse.urlencode(
        {
            "product_permalink": permalink,
            "license_key": license_key,
            "increment_uses_count": "false",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        GUMROAD_VERIFY_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        return None
    if not data.get("success"):
        return False
    purchase = data.get("purchase") or {}
    blocked = any(
        bool(purchase.get(flag))
        for flag in ("refunded", "chargebacked", "disputed", "subscription_cancelled")
    )
    return not blocked


def apply_gumroad_license_event(conn: sqlite3.Connection, payload: dict) -> tuple[bool, str, Optional[bool]]:
    license_key = str(payload.get("license_key") or "").strip()
    if not license_key:
        return False, "missing license_key", None
    for payload_key, setting_key in (
        ("email", "gumroad_email"),
        ("product_permalink", "gumroad_permalink"),
        ("sale_id", "gumroad_sale_id"),
        ("product_id", "gumroad_product_id"),
    ):
        val = str(payload.get(payload_key) or "").strip()
        if val:
            set_setting(conn, setting_key, val)
    valid = validate_gumroad_license(conn, license_key)
    if valid is False:
        set_setting(conn, "license_status", "invalid")
        return False, "license verification failed", valid
    set_license_key(conn, license_key)
    set_setting(conn, "license_status", "active" if valid is not False else "pending")
    return True, "license stored", valid


SETTINGS_SPEC: dict[str, tuple[str, object]] = {
    "account_email": ("str", ""),
    "account_avatar": ("str", ""),
    "account_linked": ("json", []),
    "account_orgs": ("json", []),
    "account_upgrade_url": ("str", ""),
    "personal_cloud_status": ("str", DEFAULT_PERSONAL_CLOUD_STATUS),
    "personal_cloud_domain": ("str", ""),
    "personal_cloud_last_updated": ("str", ""),
    "copilot_model": ("str", DEFAULT_COPILOT_MODEL),
    "copilot_accent": ("str", DEFAULT_COPILOT_ACCENT),
    "copilot_use_ltm": ("bool", True),
    "ml_context_level": ("str", DEFAULT_ML_CONTEXT_LEVEL),
    "ml_processing_mode": ("str", DEFAULT_ML_PROCESSING_MODE),
    "ltm_enabled": ("bool", DEFAULT_LTM_ENABLED),
    "ltm_permissions": ("str", "unknown"),
    "ui_default_view": ("str", DEFAULT_UI_VIEW),
    "ui_confirmations": ("str", DEFAULT_UI_CONFIRMATIONS),
    "ui_metrics_summary": ("str", DEFAULT_UI_METRICS),
    "ui_default_toolbar": ("str", DEFAULT_UI_TOOLBAR),
    "ui_theme_mode": ("str", DEFAULT_UI_THEME),
    "ui_accent": ("str", DEFAULT_UI_ACCENT),
    "ui_font_size": ("str", DEFAULT_UI_FONT_SIZE),
    "ui_font_weight": ("str", DEFAULT_UI_FONT_WEIGHT),
    "ui_density": ("str", DEFAULT_UI_DENSITY),
    "telemetry_enabled": ("bool", False),
}

CONNECTED_APPS = [
    "JetBrains Plugin",
    "Visual Studio Extension",
    "Sublime Plugin",
    "VS Code Extension",
    "Pieces CLI",
    "Desktop App",
    "Obsidian Plugin",
    "JupyterLab Extension",
    "Raycast Extension",
    "Web Extension - Chrome",
]


def get_setting_typed(conn: sqlite3.Connection, key: str):
    spec = SETTINGS_SPEC.get(key)
    if not spec:
        return get_setting(conn, key, "")
    kind, default = spec
    if kind == "bool":
        return get_bool_setting(conn, key, bool(default))
    if kind == "json":
        raw = get_setting(conn, key, json.dumps(default))
        try:
            data = json.loads(raw)
        except Exception:
            return default
        return data if isinstance(data, (list, dict)) else default
    return get_setting(conn, key, str(default))


def set_setting_typed(conn: sqlite3.Connection, key: str, value) -> tuple[bool, str]:
    spec = SETTINGS_SPEC.get(key)
    if not spec:
        return False, f"unknown key '{key}'"
    kind, _default = spec
    if kind == "bool":
        if isinstance(value, bool):
            set_bool_setting(conn, key, value)
            return True, f"set {key}={value}"
        parsed = parse_bool_value(str(value))
        if parsed is None:
            return False, "value must be true/false or 1/0"
        set_bool_setting(conn, key, parsed)
        return True, f"set {key}={parsed}"
    if kind == "json":
        data = value
        if isinstance(value, str):
            try:
                data = json.loads(value)
            except Exception as e:
                return False, f"invalid JSON: {e}"
        if not isinstance(data, (list, dict)):
            return False, "value must be a JSON list or object"
        set_setting(conn, key, json.dumps(data))
        return True, f"set {key}"
    set_setting(conn, key, str(value))
    return True, f"set {key}={value}"


def mcp_base_url() -> str:
    host = os.environ.get("MFM_MCP_HOST", "127.0.0.1")
    port = os.environ.get("MFM_MCP_PORT", "39300")
    return f"http://{host}:{port}"


def settings_snapshot(conn: sqlite3.Connection) -> dict:
    base = mcp_base_url()
    license_info = license_snapshot(conn)
    return {
        "account": {
            "email": get_setting_typed(conn, "account_email"),
            "avatar": get_setting_typed(conn, "account_avatar"),
            "linked_accounts": get_setting_typed(conn, "account_linked"),
            "organizations": get_setting_typed(conn, "account_orgs"),
            "upgrade_url": license_info["upgrade_url"],
            "pro_enabled": license_info["pro_enabled"],
            "license_type": license_info["license_type"],
            "license_status": license_info["license_status"],
            "has_license_key": license_info["has_license_key"],
        },
        "personal_cloud": {
            "status": get_setting_typed(conn, "personal_cloud_status"),
            "domain": get_setting_typed(conn, "personal_cloud_domain"),
            "last_updated": get_setting_typed(conn, "personal_cloud_last_updated"),
            "sync_target": get_setting(conn, "sync_target", ""),
            "sync_interval": get_sync_interval(conn),
            "backup_bucket": get_setting(conn, "backup_bucket", ""),
        },
        "copilot": {
            "model": get_setting_typed(conn, "copilot_model"),
            "accent": get_setting_typed(conn, "copilot_accent"),
            "use_ltm_by_default": get_setting_typed(conn, "copilot_use_ltm"),
            "chat_count": copilot_chat_count(conn),
        },
        "machine_learning": {
            "auto_context_level": get_setting_typed(conn, "ml_context_level"),
            "processing_mode": get_setting_typed(conn, "ml_processing_mode"),
            "ltm_enabled": get_setting_typed(conn, "ltm_enabled"),
            "ltm_permissions": get_setting_typed(conn, "ltm_permissions"),
            "source_blocklist": sorted(get_blocklist(conn)),
        },
        "mcp": {
            "sse_url": f"{base}/model_context_protocol/2024-11-05/sse",
            "mcp_url": f"{base}/model_context_protocol/2025-03-26/mcp",
        },
        "connected_apps": CONNECTED_APPS,
        "views_layouts": {
            "default_view": get_setting_typed(conn, "ui_default_view"),
            "confirmations": get_setting_typed(conn, "ui_confirmations"),
            "metrics_summary": get_setting_typed(conn, "ui_metrics_summary"),
            "default_toolbar": get_setting_typed(conn, "ui_default_toolbar"),
        },
        "aesthetics": {
            "theme_mode": get_setting_typed(conn, "ui_theme_mode"),
            "accent_color": get_setting_typed(conn, "ui_accent"),
            "font_size": get_setting_typed(conn, "ui_font_size"),
            "font_weight": get_setting_typed(conn, "ui_font_weight"),
            "visual_density": get_setting_typed(conn, "ui_density"),
        },
        "telemetry": {
            "enabled": get_setting_typed(conn, "telemetry_enabled"),
            "notes": "local-only; no telemetry emitted",
        },
        "support": {
            "documentation": "README.md, INTEGRATIONS.md",
            "feedback": "file a local note or issue in your workspace",
            "shortcuts": "see README.md CLI section",
        },
        "about": {
            "app_version": MFM_VERSION,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "db_path": str(DB_PATH),
            "mcp_host": os.environ.get("MFM_MCP_HOST", "127.0.0.1"),
            "mcp_port": os.environ.get("MFM_MCP_PORT", "39300"),
        },
    }


def format_setting_value(value) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "none"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    val = str(value)
    return val if val else "none"


def is_paused(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, "paused", "0") == "1"


def set_paused(conn: sqlite3.Connection, paused: bool) -> None:
    set_setting(conn, "paused", "1" if paused else "0")


def get_blocklist(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT app FROM blocklist")
    return {row["app"] for row in cur.fetchall()}


def add_blocked_app(conn: sqlite3.Connection, app: str) -> bool:
    app_norm = app.strip().lower()
    if not app_norm:
        return False
    conn.execute("INSERT OR IGNORE INTO blocklist(app) VALUES (?)", (app_norm,))
    conn.commit()
    return True


def remove_blocked_app(conn: sqlite3.Connection, app: str) -> bool:
    app_norm = app.strip().lower()
    cur = conn.execute("DELETE FROM blocklist WHERE app = ?", (app_norm,))
    conn.commit()
    return cur.rowcount > 0


def get_max_bytes(conn: sqlite3.Connection, override: Optional[int]) -> int:
    if override is not None:
        return override
    stored = get_setting(conn, "max_bytes", str(DEFAULT_MAX_BYTES))
    try:
        return int(stored)
    except ValueError:
        return DEFAULT_MAX_BYTES


def get_max_db_mb(conn: sqlite3.Connection, override: Optional[int]) -> int:
    if override is not None:
        return override
    stored = get_setting(conn, "max_db_mb", str(DEFAULT_MAX_DB_MB))
    try:
        return int(stored)
    except ValueError:
        return DEFAULT_MAX_DB_MB


def get_cap_map(conn: sqlite3.Connection, key: str) -> dict[str, int]:
    raw = get_setting(conn, key, "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            out = {}
            for k, v in data.items():
                try:
                    out[str(k).lower()] = int(v)
                except Exception:
                    continue
            return out
    except Exception:
        return {}
    return {}


def set_cap_map(conn: sqlite3.Connection, key: str, data: dict[str, int]) -> None:
    clean = {str(k).lower(): int(v) for k, v in data.items() if str(k).strip()}
    set_setting(conn, key, json.dumps(clean))


def get_evict_mode(conn: sqlite3.Connection) -> str:
    mode = get_setting(conn, "evict_mode", DEFAULT_EVICT_MODE).strip().lower()
    return mode if mode in ("fifo", "tiered") else DEFAULT_EVICT_MODE


def set_evict_mode(conn: sqlite3.Connection, mode: str) -> None:
    mode_norm = mode.strip().lower()
    if mode_norm not in ("fifo", "tiered"):
        mode_norm = DEFAULT_EVICT_MODE
    set_setting(conn, "evict_mode", mode_norm)


def looks_like_secret(text: str) -> bool:
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            return True
    return False


def redact_secrets(text: str) -> str:
    redacted = text
    for pat in SECRET_PATTERNS:
        redacted = pat.sub("[REDACTED]", redacted)
    return redacted


def get_allow_secrets(conn: sqlite3.Connection, override: Optional[bool]) -> bool:
    if override is not None:
        return override
    return get_setting(conn, "allow_secrets", "0") == "1"


def set_allow_secrets(conn: sqlite3.Connection, allow: bool) -> None:
    set_setting(conn, "allow_secrets", "1" if allow else "0")


def get_notify(conn: sqlite3.Connection, override: Optional[bool]) -> bool:
    if override is not None:
        return override
    return get_setting(conn, "notify", "1" if DEFAULT_NOTIFY else "0") == "1"


def set_notify(conn: sqlite3.Connection, enabled: bool) -> None:
    set_setting(conn, "notify", "1" if enabled else "0")


def get_allow_pdf(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, "allow_pdf", "1" if DEFAULT_ALLOW_PDF else "0") == "1"


def set_allow_pdf(conn: sqlite3.Connection, allow: bool) -> None:
    set_setting(conn, "allow_pdf", "1" if allow else "0")


def get_allow_images(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, "allow_images", "1" if DEFAULT_ALLOW_IMAGES else "0") == "1"


def set_allow_images(conn: sqlite3.Connection, allow: bool) -> None:
    set_setting(conn, "allow_images", "1" if allow else "0")


def parse_iso_dt(val: str) -> Optional[str]:
    try:
        return datetime.fromisoformat(val).isoformat()
    except Exception:
        return None


def iso_hours_ago(hours: Optional[float]) -> Optional[str]:
    if hours is None:
        return None
    try:
        h = float(hours)
    except Exception:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=h)
    return cutoff.isoformat()


# ---------- Language + embeddings ----------
_E5_MODEL = None
_E5_FAILED = False
_EMBEDDER_WARNED = False


def detect_language(text: str) -> str:
    snippet = text[:2000]
    try:
        from langdetect import detect
    except Exception:
        return "unk"
    try:
        lang = detect(snippet)
        return lang or "unk"
    except Exception:
        return "unk"


def get_embedder(conn: sqlite3.Connection, override: Optional[str]) -> str:
    if override:
        val = override.strip().lower()
    else:
        val = get_setting(conn, "embedder", DEFAULT_EMBEDDER).strip().lower()
    if val in ("e5-small", "e5"):
        return "e5-small"
    return "hash"


def set_embedder(conn: sqlite3.Connection, name: str) -> None:
    kind = "e5-small" if name.strip().lower() in ("e5-small", "e5") else "hash"
    set_setting(conn, "embedder", kind)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_]+", text.lower())


def hash_embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    vec = [0.0] * dim
    tokens = tokenize(text)
    if not tokens:
        return vec
    for tok in tokens:
        h = hash(tok)
        idx = h % dim
        vec[idx] += 1.0
    norm = sum(v * v for v in vec) ** 0.5
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def e5_embed(text: str) -> Optional[list[float]]:
    global _E5_MODEL, _E5_FAILED
    if _E5_FAILED:
        return None
    try:
        from sentence_transformers import SentenceTransformer
    except Exception:
        _E5_FAILED = True
        return None
    if _E5_MODEL is None:
        try:
            _E5_MODEL = SentenceTransformer(E5_MODEL_NAME)
        except Exception:
            _E5_FAILED = True
            return None
    try:
        vec = _E5_MODEL.encode(
            f"passage: {text}",
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vec.tolist()
    except Exception:
        _E5_FAILED = True
        return None


def embed_from_kind(kind: str, text: str) -> tuple[list[float], str]:
    global _EMBEDDER_WARNED
    if kind == "e5-small":
        vec = e5_embed(text)
        if vec:
            return vec, "e5-small"
        if not _EMBEDDER_WARNED:
            say(FATHER, "embedder 'e5-small' unavailable; falling back to hash (pip install sentence-transformers)")
            _EMBEDDER_WARNED = True
    return hash_embed(text), "hash"


def embed_text(conn: sqlite3.Connection, text: str, embedder_override: Optional[str] = None) -> tuple[list[float], str]:
    kind, _warning = resolve_embedder_for_use(conn, embedder_override)
    return embed_from_kind(kind, text)


def store_embedding(conn: sqlite3.Connection, clip_id: int, vec: list[float], model: str) -> None:
    conn.execute(
        "INSERT INTO clip_vectors(clip_id, dim, vector, model) VALUES (?, ?, ?, ?) ON CONFLICT(clip_id) DO UPDATE SET dim=excluded.dim, vector=excluded.vector, model=excluded.model",
        (clip_id, len(vec), json.dumps(vec), model),
    )
    conn.commit()


def load_embedding(row) -> list[float]:
    try:
        return json.loads(row["vector"])
    except Exception:
        return []


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def build_ann_index(rows: Iterable[sqlite3.Row]) -> tuple[list[int], list[list[float]]]:
    ids = []
    vecs = []
    for row in rows:
        vec = load_embedding(row)
        if vec:
            ids.append(row["id"])
            vecs.append(vec)
    return ids, vecs


def knn(query: list[float], ids: list[int], vecs: list[list[float]], limit: int) -> list[tuple[float, int]]:
    sims = []
    for cid, vec in zip(ids, vecs):
        sims.append((cosine(query, vec), cid))
    sims.sort(key=lambda x: x[0], reverse=True)
    return sims[:limit]


# ---------- Tag helpers ----------
def get_or_create_tag(conn: sqlite3.Connection, name: str) -> int:
    tag_norm = name.strip().lower()
    if not tag_norm:
        raise ValueError("empty tag")
    cur = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_norm,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO tags(name) VALUES (?)", (tag_norm,))
    conn.commit()
    return cur.lastrowid


def assign_tag(conn: sqlite3.Connection, clip_id: int, tag: str) -> bool:
    tag_id = get_or_create_tag(conn, tag)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO clip_tags(clip_id, tag_id) VALUES (?, ?)",
            (clip_id, tag_id),
        )
        conn.commit()
        cap_map = get_cap_map(conn, "cap_by_tag")
        tag_norm = tag.strip().lower()
        if tag_norm in cap_map:
            ev = evict_tag_cap(conn, tag_norm, cap_map[tag_norm])
            if ev:
                say(FATHER, f"evicted {ev} old clips for tag cap ({tag_norm})")
        return True
    except sqlite3.IntegrityError:
        return False


def remove_tag(conn: sqlite3.Connection, clip_id: int, tag: str) -> bool:
    tag_norm = tag.strip().lower()
    cur = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_norm,))
    row = cur.fetchone()
    if not row:
        return False
    tag_id = row["id"]
    cur = conn.execute(
        "DELETE FROM clip_tags WHERE clip_id = ? AND tag_id = ?",
        (clip_id, tag_id),
    )
    conn.commit()
    return cur.rowcount > 0


def clear_tags(conn: sqlite3.Connection, clip_id: int) -> int:
    cur = conn.execute("DELETE FROM clip_tags WHERE clip_id = ?", (clip_id,))
    conn.commit()
    return cur.rowcount


def list_tags(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute("SELECT name FROM tags ORDER BY name")
    return [row["name"] for row in cur.fetchall()]


def tags_for_clip(conn: sqlite3.Connection, clip_id: int) -> list[str]:
    cur = conn.execute(
        """
        SELECT t.name FROM tags t
        JOIN clip_tags ct ON ct.tag_id = t.id
        WHERE ct.clip_id = ?
        ORDER BY t.name
        """,
        (clip_id,),
    )
    return [row["name"] for row in cur.fetchall()]


def tags_for_clips(conn: sqlite3.Connection, clip_ids: list[int]) -> dict[int, list[str]]:
    if not clip_ids:
        return {}
    placeholders = ",".join("?" for _ in clip_ids)
    cur = conn.execute(
        f"""
        SELECT ct.clip_id, t.name
        FROM clip_tags ct
        JOIN tags t ON t.id = ct.tag_id
        WHERE ct.clip_id IN ({placeholders})
        ORDER BY t.name
        """,
        clip_ids,
    )
    result: dict[int, list[str]] = {}
    for row in cur.fetchall():
        result.setdefault(row["clip_id"], []).append(row["name"])
    return result


def add_note(conn: sqlite3.Connection, clip_id: int, note: str) -> bool:
    note_clean = note.strip()
    if not note_clean:
        return False
    conn.execute(
        "INSERT INTO clip_notes (clip_id, note, created_at) VALUES (?, ?, ?)",
        (clip_id, note_clean, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return True


def notes_for_clips(conn: sqlite3.Connection, clip_ids: list[int]) -> dict[int, list[dict]]:
    if not clip_ids:
        return {}
    placeholders = ",".join("?" for _ in clip_ids)
    cur = conn.execute(
        f"""
        SELECT clip_id, note, created_at
        FROM clip_notes
        WHERE clip_id IN ({placeholders})
        ORDER BY created_at DESC
        """,
        clip_ids,
    )
    result: dict[int, list[dict]] = {}
    for row in cur.fetchall():
        result.setdefault(row["clip_id"], []).append({"note": row["note"], "created_at": row["created_at"]})
    return result


def add_copilot_chat(conn: sqlite3.Connection, content: str, title: Optional[str], model: Optional[str]) -> bool:
    clean = content.strip()
    if not clean:
        return False
    conn.execute(
        "INSERT INTO copilot_chats (created_at, title, model, content) VALUES (?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), title, model, clean),
    )
    conn.commit()
    return True


def list_copilot_chats(conn: sqlite3.Connection, limit: int) -> list[dict]:
    cur = conn.execute(
        "SELECT id, created_at, title, model FROM copilot_chats ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return [dict(row) for row in cur.fetchall()]


def copilot_chat_count(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) AS c FROM copilot_chats")
    row = cur.fetchone()
    return row["c"] if row else 0


def clear_copilot_chats(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM copilot_chats")
    conn.commit()
    return cur.rowcount


def latest_clip(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    cur = conn.execute(
        "SELECT id, created_at, source_app, window_title, content, pinned, title, file_path, lang FROM clips ORDER BY created_at DESC LIMIT 1"
    )
    return cur.fetchone()


def fetch_clip(conn: sqlite3.Connection, clip_id: int) -> Optional[sqlite3.Row]:
    cur = conn.execute(
        "SELECT id, created_at, source_app, window_title, content, pinned, title, file_path, lang FROM clips WHERE id = ?",
        (clip_id,),
    )
    return cur.fetchone()


def context_bundle(
    conn: sqlite3.Connection,
    app: Optional[str],
    tag: Optional[str],
    limit: int,
    hours: Optional[float],
    pins_only: bool = False,
) -> list[dict]:
    since_iso = iso_hours_ago(hours) if hours is not None else None
    rows_db, tag_map = filtered_rows(
        conn,
        limit,
        app=app,
        contains=None,
        tag=tag,
        pins_only=pins_only,
        since_iso=since_iso,
        until_iso=None,
    )
    notes_map = notes_for_clips(conn, [row["id"] for row in rows_db])
    rows = []
    for row in rows_db:
        rows.append(
            dict(
                id=row["id"],
                created_at=row["created_at"],
                source_app=row["source_app"],
                window_title=row["window_title"],
                content=row["content"],
                pinned=bool(row["pinned"]),
                title=row["title"],
                lang=row["lang"],
                tags=tag_map.get(row["id"], []),
                notes=notes_map.get(row["id"], []),
            )
        )
    return rows


# ---------- Clipboard helpers ----------
def copy_to_clipboard(text: str) -> bool:
    try:
        proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        proc.communicate(text.encode("utf-8", errors="ignore"))
        return proc.returncode == 0
    except Exception:
        return False


def command_exists(cmd: str) -> bool:
    return subprocess.call(["/bin/sh", "-c", f"command -v {cmd} >/dev/null 2>&1"]) == 0


def run_helper(cmd: str, text: str, timeout: float = 5.0, env: Optional[dict] = None) -> Optional[str]:
    """Run a helper command with text piped to stdin; returns stdout or None on failure."""
    try:
        res = subprocess.run(
            ["/bin/sh", "-c", cmd],
            input=text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env=env,
        )
        if res.returncode != 0:
            return None
        out = res.stdout.strip()
        return out if out else None
    except Exception:
        return None


def run_user_helper_on_clip(
    conn: sqlite3.Connection, kind: str, clip_id: int, timeout: float = 8.0
) -> tuple[bool, str, Optional[int], Optional[str]]:
    cmd_key = f"helper_{kind}_cmd"
    helper_cmd = get_setting(conn, cmd_key, "").strip()
    if not helper_cmd:
        return False, f"configure {cmd_key} via config --set", None, None
    row = fetch_clip(conn, clip_id)
    if not row:
        return False, f"no clip #{clip_id}", None, None
    env = dict(os.environ)
    env.update(
        {
            "MFM_CLIP_ID": str(row["id"]),
            "MFM_SOURCE_APP": row["source_app"] or "",
            "MFM_TITLE": row["title"] or "",
            "MFM_KIND": kind,
        }
    )
    out = run_helper(helper_cmd, row["content"], timeout=timeout, env=env)
    if not out:
        return False, "helper produced no output", None, None
    title_line = out.strip().splitlines()[0] if out.strip() else ""
    if len(title_line) > 120:
        title_line = title_line[:117] + "..."
    new_id = insert_clip(
        conn,
        out,
        app=row["source_app"] or f"helper/{kind}",
        window=row["window_title"] or f"{kind} helper",
        title=title_line,
    )
    if not new_id:
        return False, "failed to save helper output (duplicate?)", None, out
    assign_tag(conn, new_id, kind)
    assign_tag(conn, new_id, f"from:{row['id']}")
    return True, f"saved {kind} of #{row['id']} as #{new_id}", new_id, out


def run_ai_helper(
    conn: sqlite3.Connection,
    setting_key: str,
    hours: Optional[float],
    limit: int,
    timeout: float,
    save: bool,
    tag_label: str,
) -> tuple[bool, str, Optional[int], Optional[str]]:
    helper_cmd = get_setting(conn, setting_key, "").strip()
    if not helper_cmd:
        return False, f"configure {setting_key} via config --set", None, None
    since_iso = iso_hours_ago(hours) if hours is not None else None
    rows, tag_map = filtered_rows(conn, limit, app=None, contains=None, tag=None, pins_only=False, since_iso=since_iso, until_iso=None)
    if not rows:
        return False, "no clips available", None, None
    lines: list[str] = []
    for row in rows:
        tags = tag_map.get(row["id"], [])
        tag_str = f" tags={','.join(tags)}" if tags else ""
        title = row["title"] or row["window_title"] or ""
        lines.append(f"#{row['id']} [{row['source_app'] or 'unknown'}] {row['created_at']}{tag_str} {title}".strip())
        lines.append(row["content"])
        lines.append("")
    payload = "\n".join(lines)
    env = dict(os.environ)
    env.update(
        {
            "MFM_HELPER": setting_key,
            "MFM_HELPER_LIMIT": str(limit),
            "MFM_HELPER_HOURS": str(hours or ""),
        }
    )
    out = run_helper(helper_cmd, payload, timeout=timeout, env=env)
    if not out:
        return False, "helper produced no output", None, None
    new_id = None
    if save:
        title_line = out.strip().splitlines()[0] if out.strip() else ""
        if len(title_line) > 120:
            title_line = title_line[:117] + "..."
        new_id = insert_clip(
            conn,
            out,
            app=f"helper/{setting_key}",
            window=f"{setting_key} helper",
            title=title_line,
        )
        if new_id:
            assign_tag(conn, new_id, tag_label)
            assign_tag(conn, new_id, f"helper:{setting_key}")
        else:
            return True, "helper output ok (not saved; duplicate?)", None, out
    return True, "helper output ok", new_id, out


def read_text_file(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None
    except Exception:
        return None


# ---------- Commands ----------
def cmd_init(_: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    say(MOTHER, f"initialized DB at {DB_PATH}")


def auto_context_flags(level: str) -> tuple[bool, bool]:
    norm = (level or "").strip().lower()
    if norm in ("off", "none", "0", "false", "disabled"):
        return False, False
    if norm in ("low", "lite"):
        return True, False
    if norm in ("medium", "med", "high", "full"):
        return True, True
    return True, True


def cmd_watch(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    interval = max(0.25, args.interval)
    cap = args.cap
    max_bytes = get_max_bytes(conn, args.max_bytes)
    max_db_mb = get_max_db_mb(conn, None)
    allow_secrets = get_allow_secrets(conn, args.allow_secrets)
    notify_override: Optional[bool] = True if args.notify else False if args.no_notify else None
    notify_enabled = get_notify(conn, notify_override)
    embedder_choice, embedder_warning = resolve_embedder_for_use(conn, getattr(args, "embedder", None))
    if embedder_warning:
        say(FATHER, embedder_warning)
    cap_by_app = get_cap_map(conn, "cap_by_app")
    auto_summary_cmd = get_setting(conn, "auto_summary_cmd", "").strip()
    auto_tag_cmd = get_setting(conn, "auto_tag_cmd", "").strip()
    last_digest = None
    last_paused = None
    last_sync_at: Optional[float] = None
    last_sync_target = ""

    def finish_tick() -> None:
        nonlocal last_sync_at, last_sync_target
        last_sync_at, last_sync_target = maybe_watch_sync(conn, last_sync_at, last_sync_target)
        time.sleep(interval)

    say(MOTHER, "watching clipboard... Ctrl+C to stop.")
    try:
        while True:
            paused = is_paused(conn)
            if paused:
                if last_paused is not True:
                    say(MOTHER, "paused; not capturing.")
                last_paused = True
                finish_tick()
                continue
            if last_paused is True:
                say(MOTHER, "resumed capture.")
            last_paused = False

            clip = read_clipboard()
            if clip:
                context_level = get_setting(conn, "ml_context_level", DEFAULT_ML_CONTEXT_LEVEL)
                ltm_enabled = get_bool_setting(conn, "ltm_enabled", DEFAULT_LTM_ENABLED)
                allow_summary, allow_tags = auto_context_flags(context_level)
                if not ltm_enabled:
                    allow_summary = False
                    allow_tags = False
                if not is_pro_enabled(conn):
                    allow_summary = False
                    allow_tags = False
                digest = hashlib.sha256(clip.encode("utf-8")).hexdigest()
                if digest != last_digest:
                    app, window = frontmost_app_and_window()
                    cap_by_app = get_cap_map(conn, "cap_by_app")
                    blockset = get_blocklist(conn)
                    if app.strip().lower() in blockset:
                        last_digest = digest
                        finish_tick()
                        continue
                    clip_bytes = clip.encode("utf-8", errors="ignore")
                    if len(clip_bytes) > max_bytes:
                        say(MOTHER, f"skipped large clip ({len(clip_bytes)} bytes > max {max_bytes})")
                        if notify_enabled:
                            toast("Mother skipped large clip", f"{app} ({len(clip_bytes)} bytes)")
                        last_digest = digest
                        finish_tick()
                        continue
                    if not allow_secrets and looks_like_secret(clip):
                        if args.redact:
                            clip = redact_secrets(clip)
                        else:
                            say(MOTHER, "skipped clip that looks like a secret (pattern match). Enable allow_secrets or use --redact.")
                            if notify_enabled:
                                toast("Mother skipped secret-looking clip", app or "unknown")
                            last_digest = digest
                            finish_tick()
                            continue

                    existing_id = get_clip_id_by_hash(conn, digest)
                    if existing_id:
                        insert_event(conn, existing_id)
                        say(MOTHER, f"noted repeat clip #{existing_id}")
                    else:
                        # derive a simple title (first line trimmed), optionally summarize
                        first_line = clip.strip().splitlines()[0] if clip.strip() else ""
                        if auto_summary_cmd and allow_summary:
                            maybe = run_helper(auto_summary_cmd, clip, timeout=6.0)
                            if maybe:
                                first_line = maybe
                        if len(first_line) > 120:
                            first_line = first_line[:117] + "..."
                        inserted_id = insert_clip(conn, clip, app=app, window=window, digest=digest, title=first_line, embedder_override=embedder_choice)
                        if inserted_id:
                            insert_event(conn, inserted_id)
                            say(MOTHER, f"saved clip #{inserted_id}")
                            if notify_enabled:
                                toast("Mother saved clip", f"#{inserted_id} from {app}")
                            if auto_tag_cmd and allow_tags:
                                tags_out = run_helper(auto_tag_cmd, clip, timeout=6.0)
                                if tags_out:
                                    # split on comma or whitespace
                                    parts = [t.strip().lower() for t in re.split(r"[,\s]+", tags_out) if t.strip()]
                                    for t in parts:
                                        assign_tag(conn, inserted_id, t)
                            if app and app.strip().lower() in cap_by_app:
                                ev = evict_app_cap(conn, app.strip().lower(), cap_by_app[app.strip().lower()])
                                if ev:
                                    say(MOTHER, f"evicted {ev} old clips for app cap ({app})")
                            if cap:
                                pruned = prune(conn, cap)
                                if pruned:
                                    say(MOTHER, f"pruned {pruned} old clips (cap={cap})")
                            ev = evict_if_needed(conn, max_db_mb)
                            if ev:
                                say(MOTHER, f"evicted {ev} oldest clips to honor db cap {max_db_mb}MB")
                            # backpressure warnings
                            if cap:
                                total = conn.execute("SELECT COUNT(*) AS c FROM clips").fetchone()["c"]
                                if total and total > 0.9 * cap:
                                    msg = f"near cap ({total}/{cap}); consider purge"
                                    say(MOTHER, msg)
                                    if notify_enabled:
                                        toast("Mother nearing cap", msg)
                            size_mb = DB_PATH.stat().st_size / (1024 * 1024) if DB_PATH.exists() else 0
                            if size_mb > 0.8 * max_db_mb:
                                msg = f"DB size {size_mb:.1f}MB near cap {max_db_mb}MB"
                                say(MOTHER, msg)
                                if notify_enabled:
                                    toast("Mother nearing DB cap", msg)
                    last_digest = digest
            finish_tick()
    except KeyboardInterrupt:
        say(MOTHER, "stopped.")


def filtered_rows(
    conn: sqlite3.Connection,
    limit: int,
    app: Optional[str] = None,
    contains: Optional[str] = None,
    tag: Optional[str] = None,
    pins_only: bool = False,
    since_iso: Optional[str] = None,
    until_iso: Optional[str] = None,
) -> tuple[list[sqlite3.Row], Dict[int, list[str]]]:
    clauses = []
    params = []
    if app:
        clauses.append("LOWER(source_app) = LOWER(?)")
        params.append(app)
    if contains:
        clauses.append("content LIKE ?")
        params.append(f"%{contains}%")
    if tag:
        clauses.append(
            "id IN (SELECT clip_id FROM clip_tags ct JOIN tags t ON t.id = ct.tag_id WHERE LOWER(t.name) = LOWER(?))"
        )
        params.append(tag)
    if pins_only:
        clauses.append("pinned = 1")
    if since_iso:
        clauses.append("datetime(created_at) >= datetime(?)")
        params.append(since_iso)
    if until_iso:
        clauses.append("datetime(created_at) <= datetime(?)")
        params.append(until_iso)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    sql = f"""
        SELECT id, created_at, source_app, window_title, content, pinned, title, lang, file_path
        FROM clips
        {where}
        ORDER BY created_at DESC
        LIMIT ?;
    """
    params.append(limit)
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    tag_map = tags_for_clips(conn, [row["id"] for row in rows])
    return rows, tag_map


def cmd_recent(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    since_iso = parse_iso_dt(args.since) if getattr(args, "since", None) else None
    if args.since_hours is not None:
        since_iso = iso_hours_ago(args.since_hours)
    until_iso = parse_iso_dt(args.until) if getattr(args, "until", None) else None
    rows, tag_map = filtered_rows(
        conn,
        args.limit,
        app=args.app,
        contains=args.contains,
        tag=args.tag,
        pins_only=args.pins_only,
        since_iso=since_iso,
        until_iso=until_iso,
    )
    if getattr(args, "json", False):
        notes_map = notes_for_clips(conn, [row["id"] for row in rows])
        payload = []
        for row in rows:
            payload.append(
                dict(
                    id=row["id"],
                    created_at=row["created_at"],
                    source_app=row["source_app"],
                    window_title=row["window_title"],
                    content=row["content"],
                    pinned=bool(row["pinned"]),
                    title=row["title"],
                    lang=row["lang"],
                    tags=tag_map.get(row["id"], []),
                    notes=notes_map.get(row["id"], []),
                )
            )
        print(json.dumps({"items": payload}, ensure_ascii=False))
        return
    for row in rows:
        preview = row["content"].replace("\n", "\\n")
        if len(preview) > 120:
            preview = preview[:117] + "..."
        pin_mark = "*" if row["pinned"] else " "
        tags = tag_map.get(row["id"], [])
        tags_str = f" tags={','.join(tags)}" if tags else ""
        title = f" \"{row['title']}\"" if row["title"] else ""
        lang = row["lang"] if row["lang"] not in (None, "", "unk") else ""
        lang_str = f" lang={lang}" if lang else ""
        say(FATHER, f"{pin_mark}#{row['id']:>5} {row['created_at']} [{row['source_app']}] {preview}{title}{tags_str}{lang_str}")


def cmd_search(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    clauses = ["clips_fts MATCH ?"]
    params = [args.query]
    if args.app:
        clauses.append("LOWER(c.source_app) = LOWER(?)")
        params.append(args.app)
    if args.tag:
        clauses.append(
            "c.id IN (SELECT clip_id FROM clip_tags ct JOIN tags t ON t.id = ct.tag_id WHERE LOWER(t.name) = LOWER(?))"
        )
        params.append(args.tag)
    if args.pins_only:
        clauses.append("c.pinned = 1")
    since_iso = parse_iso_dt(args.since) if getattr(args, "since", None) else None
    if args.since_hours is not None:
        since_iso = iso_hours_ago(args.since_hours)
    until_iso = parse_iso_dt(args.until) if getattr(args, "until", None) else None
    if since_iso:
        clauses.append("datetime(c.created_at) >= datetime(?)")
        params.append(since_iso)
    if until_iso:
        clauses.append("datetime(c.created_at) <= datetime(?)")
        params.append(until_iso)
    where = " AND ".join(clauses)
    sql = f"""
        SELECT c.id, c.created_at, c.source_app, c.window_title, c.content, c.pinned, c.title, c.lang
        FROM clips_fts f
        JOIN clips c ON c.id = f.rowid
        WHERE {where}
        ORDER BY c.created_at DESC
        LIMIT ?;
    """
    params.append(args.limit)
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    tag_map = tags_for_clips(conn, [row["id"] for row in rows])
    for row in rows:
        preview = row["content"].replace("\n", "\\n")
        if len(preview) > 120:
            preview = preview[:117] + "..."
        pin_mark = "*" if row["pinned"] else " "
        tags = tag_map.get(row["id"], [])
        tags_str = f" tags={','.join(tags)}" if tags else ""
        title = f" \"{row['title']}\"" if row["title"] else ""
        lang = row["lang"] if row["lang"] not in (None, "", "unk") else ""
        lang_str = f" lang={lang}" if lang else ""
        say(FATHER, f"{pin_mark}#{row['id']:>5} {row['created_at']} [{row['source_app']}] {preview}{title}{tags_str}{lang_str}")


def topic_groups(
    conn: sqlite3.Connection,
    limit_groups: int = 8,
    per_group: int = 5,
    app: Optional[str] = None,
    tag: Optional[str] = None,
    pins_only: bool = False,
    since_iso: Optional[str] = None,
    until_iso: Optional[str] = None,
) -> list[dict]:
    pool = max(limit_groups * per_group * 3, 200)
    rows, tag_map = filtered_rows(
        conn,
        pool,
        app=app,
        contains=None,
        tag=tag,
        pins_only=pins_only,
        since_iso=since_iso,
        until_iso=until_iso,
    )
    notes_map = notes_for_clips(conn, [row["id"] for row in rows])
    groups: dict[str, dict] = {}
    for row in rows:
        keys = tag_map.get(row["id"], [])
        if not keys:
            keys = [f"app:{row['source_app'] or 'unknown'}"]
        for key in keys:
            kind = "tag" if not key.startswith("app:") else "app"
            label = key if kind == "tag" else key.split(":", 1)[1]
            slot = groups.setdefault(
                key,
                {"name": label, "kind": kind, "items": [], "latest": row["created_at"]},
            )
            slot["items"].append(row)
            if row["created_at"] > slot["latest"]:
                slot["latest"] = row["created_at"]
    ordered = sorted(groups.values(), key=lambda g: g["latest"], reverse=True)[:limit_groups]
    output: list[dict] = []
    for grp in ordered:
        grp["items"].sort(key=lambda r: r["created_at"], reverse=True)
        trimmed = grp["items"][:per_group]
        output.append(
            {
                "name": grp["name"],
                "kind": grp["kind"],
                "count": len(grp["items"]),
                "latest": grp["latest"],
                "items": [
                    dict(
                        id=row["id"],
                        created_at=row["created_at"],
                        source_app=row["source_app"],
                        window_title=row["window_title"],
                        content=row["content"],
                        pinned=bool(row["pinned"]),
                        title=row["title"],
                        lang=row["lang"],
                        tags=tag_map.get(row["id"], []),
                        notes=notes_map.get(row["id"], []),
                    )
                    for row in trimmed
                ],
            }
        )
    return output


def fetch_semantic_candidates(
    conn: sqlite3.Connection,
    app: Optional[str],
    tag: Optional[str],
    limit: int,
    model: str,
    since_iso: Optional[str] = None,
    until_iso: Optional[str] = None,
    pins_only: bool = False,
) -> list[sqlite3.Row]:
    clauses = ["v.model = ?"]
    params = [model]
    if app:
        clauses.append("LOWER(c.source_app) = LOWER(?)")
        params.append(app)
    if tag:
        clauses.append(
            "c.id IN (SELECT clip_id FROM clip_tags ct JOIN tags t ON t.id = ct.tag_id WHERE LOWER(t.name) = LOWER(?))"
        )
        params.append(tag)
    if since_iso:
        clauses.append("datetime(c.created_at) >= datetime(?)")
        params.append(since_iso)
    if until_iso:
        clauses.append("datetime(c.created_at) <= datetime(?)")
        params.append(until_iso)
    if pins_only:
        clauses.append("c.pinned = 1")
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    sql = f"""
        SELECT c.id, c.created_at, c.source_app, c.window_title, c.content, c.pinned, c.title, c.lang, v.vector, v.model
        FROM clips c
        JOIN clip_vectors v ON v.clip_id = c.id
        {where}
        ORDER BY c.created_at DESC
        LIMIT ?
    """
    params.append(limit)
    cur = conn.execute(sql, params)
    return cur.fetchall()


def cmd_semantic_search(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    embedder_kind, embedder_warning = resolve_embedder_for_use(conn, getattr(args, "embedder", None))
    if embedder_warning:
        say(FATHER, embedder_warning)
    qvec, model_used = embed_from_kind(embedder_kind, args.query)
    since_iso = parse_iso_dt(args.since) if getattr(args, "since", None) else None
    if getattr(args, "since_hours", None) is not None:
        since_iso = iso_hours_ago(args.since_hours)
    until_iso = parse_iso_dt(args.until) if getattr(args, "until", None) else None
    rows = fetch_semantic_candidates(
        conn,
        args.app,
        args.tag,
        args.pool,
        model_used,
        since_iso=since_iso,
        until_iso=until_iso,
        pins_only=args.pins_only,
    )
    ids, vecs = build_ann_index(rows)
    sims = knn(qvec, ids, vecs, args.limit)
    tag_map = tags_for_clips(conn, ids)
    row_map: Dict[int, sqlite3.Row] = {row["id"]: row for row in rows}
    for sim, cid in sims:
        row = row_map.get(cid)
        if not row:
            continue
        preview = row["content"].replace("\n", "\\n")
        if len(preview) > 120:
            preview = preview[:117] + "..."
        pin_mark = "*" if row["pinned"] else " "
        tags = tag_map.get(row["id"], [])
        tags_str = f" tags={','.join(tags)}" if tags else ""
        title = f" \"{row['title']}\"" if row["title"] else ""
        lang = row["lang"] if row["lang"] not in (None, "", "unk") else ""
        lang_str = f" lang={lang}" if lang else ""
        say(FATHER, f"{pin_mark}#{row['id']:>5} sim={sim:.3f} [{row['source_app']}] {preview}{title}{tags_str}{lang_str}")


def cmd_delete(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    cur = conn.execute("DELETE FROM clips WHERE id = ?", (args.id,))
    conn.commit()
    if cur.rowcount:
        say(FATHER, f"deleted clip #{args.id}")
    else:
        say(FATHER, f"no clip #{args.id}")


def cmd_stats(_: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    s = stats(conn)
    latest = s["latest"] or "n/a"
    size_mb = s["db_size_bytes"] / (1024 * 1024)
    say(FATHER, f"clips: {s['count']}, latest: {latest}, db size: {size_mb:.2f} MB")


def cmd_status(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    snap = status_snapshot(conn)
    if getattr(args, "json", False):
        print(json.dumps(snap, ensure_ascii=False))
        return
    latest = snap["latest"] or "n/a"
    say(
        FATHER,
        f"capture={'paused' if snap['paused'] else 'active'}, tier={snap['license_type']}, pro_enabled={snap['pro_enabled']}, license_status={snap['license_status']}, notify={snap['notify']}, allow_secrets={snap['allow_secrets']}, embedder={snap['embedder']}, "
        f"ltm={snap['ltm_enabled']}, ml_context={snap['ml_context_level']}, ml_mode={snap['ml_processing_mode']}, "
        f"max_bytes={snap['max_bytes']}, max_db_mb={snap['max_db_mb']}, evict_mode={snap['evict_mode']}, clips={snap['count']}, latest={latest}, db={snap['db_size_mb']:.2f} MB, blocklisted_apps={snap['blocklist_size']}, cap_by_app={snap['cap_by_app']}, cap_by_tag={snap['cap_by_tag']}",
    )


def cmd_mcp_urls(_: argparse.Namespace) -> None:
    base = mcp_base_url()
    say(FATHER, f"sse_url={base}/model_context_protocol/2024-11-05/sse")
    say(FATHER, f"mcp_url={base}/model_context_protocol/2025-03-26/mcp")


def cmd_personas(_: argparse.Namespace) -> None:
    say(MOTHER, "capture: clipboard watcher, dedup, prune to cap, init db")
    say(FATHER, "retrieve: recent/search/delete/stats, index health")


def cmd_settings(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    if args.list_keys:
        for key in sorted(SETTINGS_SPEC.keys()):
            say(FATHER, key)
        return
    if args.get:
        if args.get not in SETTINGS_SPEC:
            say(FATHER, f"unknown key '{args.get}'")
            return
        val = get_setting_typed(conn, args.get)
        if args.json:
            print(json.dumps({args.get: val}, ensure_ascii=False))
        else:
            say(FATHER, f"{args.get}={format_setting_value(val)}")
        return
    if args.set:
        key, value = args.set
        ok, msg = set_setting_typed(conn, key, value)
        say(FATHER, msg)
        return
    snap = settings_snapshot(conn)
    if args.json:
        print(json.dumps(snap, indent=2, ensure_ascii=False))
        return
    account = snap["account"]
    say(FATHER, "Account")
    say(FATHER, f"  email={format_setting_value(account.get('email'))}")
    say(FATHER, f"  avatar={format_setting_value(account.get('avatar'))}")
    say(FATHER, f"  linked_accounts={format_setting_value(account.get('linked_accounts'))}")
    say(FATHER, f"  organizations={format_setting_value(account.get('organizations'))}")
    say(FATHER, f"  upgrade_url={format_setting_value(account.get('upgrade_url'))}")
    say(FATHER, f"  pro_enabled={format_setting_value(account.get('pro_enabled'))}")
    say(FATHER, f"  license_type={format_setting_value(account.get('license_type'))}")
    say(FATHER, f"  license_status={format_setting_value(account.get('license_status'))}")
    say(FATHER, f"  has_license_key={format_setting_value(account.get('has_license_key'))}")

    cloud = snap["personal_cloud"]
    say(FATHER, "Personal Cloud")
    say(FATHER, f"  status={format_setting_value(cloud.get('status'))}")
    say(FATHER, f"  domain={format_setting_value(cloud.get('domain'))}")
    say(FATHER, f"  last_updated={format_setting_value(cloud.get('last_updated'))}")
    say(FATHER, f"  sync_target={format_setting_value(cloud.get('sync_target'))}")
    say(FATHER, f"  sync_interval={format_setting_value(cloud.get('sync_interval'))}")
    say(FATHER, f"  backup_bucket={format_setting_value(cloud.get('backup_bucket'))}")
    say(FATHER, "  backup_restore=use `backup` / `restore` / `cloud-backup` commands")

    copilot = snap["copilot"]
    say(FATHER, "Copilot Chats")
    say(FATHER, f"  model={format_setting_value(copilot.get('model'))}")
    say(FATHER, f"  accent={format_setting_value(copilot.get('accent'))}")
    say(FATHER, f"  use_ltm_by_default={format_setting_value(copilot.get('use_ltm_by_default'))}")
    say(FATHER, f"  chat_count={format_setting_value(copilot.get('chat_count'))}")

    ml = snap["machine_learning"]
    say(FATHER, "Machine Learning")
    say(FATHER, f"  auto_context_level={format_setting_value(ml.get('auto_context_level'))}")
    say(FATHER, f"  processing_mode={format_setting_value(ml.get('processing_mode'))}")
    say(FATHER, f"  ltm_enabled={format_setting_value(ml.get('ltm_enabled'))}")
    say(FATHER, f"  ltm_permissions={format_setting_value(ml.get('ltm_permissions'))}")
    say(FATHER, f"  source_blocklist={format_setting_value(ml.get('source_blocklist'))}")

    mcp = snap["mcp"]
    say(FATHER, "Model Context Protocol (MCP)")
    say(FATHER, f"  sse_url={format_setting_value(mcp.get('sse_url'))}")
    say(FATHER, f"  mcp_url={format_setting_value(mcp.get('mcp_url'))}")

    say(FATHER, "Connected Applications")
    for app in snap["connected_apps"]:
        say(FATHER, f"  - {app}")
    say(FATHER, "  docs=INTEGRATIONS.md")

    views = snap["views_layouts"]
    say(FATHER, "Views & Layouts")
    say(FATHER, f"  default_view={format_setting_value(views.get('default_view'))}")
    say(FATHER, f"  confirmations={format_setting_value(views.get('confirmations'))}")
    say(FATHER, f"  metrics_summary={format_setting_value(views.get('metrics_summary'))}")
    say(FATHER, f"  default_toolbar={format_setting_value(views.get('default_toolbar'))}")

    aest = snap["aesthetics"]
    say(FATHER, "Aesthetics")
    say(FATHER, f"  theme_mode={format_setting_value(aest.get('theme_mode'))}")
    say(FATHER, f"  accent_color={format_setting_value(aest.get('accent_color'))}")
    say(FATHER, f"  font_size={format_setting_value(aest.get('font_size'))}")
    say(FATHER, f"  font_weight={format_setting_value(aest.get('font_weight'))}")
    say(FATHER, f"  visual_density={format_setting_value(aest.get('visual_density'))}")

    telemetry = snap["telemetry"]
    say(FATHER, "Telemetry")
    say(FATHER, f"  enabled={format_setting_value(telemetry.get('enabled'))}")
    say(FATHER, f"  notes={format_setting_value(telemetry.get('notes'))}")

    support = snap["support"]
    say(FATHER, "Support & Feedback")
    say(FATHER, f"  documentation={format_setting_value(support.get('documentation'))}")
    say(FATHER, f"  feedback={format_setting_value(support.get('feedback'))}")
    say(FATHER, f"  shortcuts={format_setting_value(support.get('shortcuts'))}")

    about = snap["about"]
    say(FATHER, "About")
    say(FATHER, f"  app_version={format_setting_value(about.get('app_version'))}")
    say(FATHER, f"  python={format_setting_value(about.get('python'))}")
    say(FATHER, f"  platform={format_setting_value(about.get('platform'))}")
    say(FATHER, f"  db_path={format_setting_value(about.get('db_path'))}")
    say(FATHER, f"  mcp_host={format_setting_value(about.get('mcp_host'))}")
    say(FATHER, f"  mcp_port={format_setting_value(about.get('mcp_port'))}")


def cmd_copilot(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    did_action = False
    if args.set_model:
        set_setting(conn, "copilot_model", args.set_model)
        say(FATHER, f"set copilot_model={args.set_model}")
        did_action = True
    if args.set_accent:
        set_setting(conn, "copilot_accent", args.set_accent)
        say(FATHER, f"set copilot_accent={args.set_accent}")
        did_action = True
    if args.use_ltm or args.no_use_ltm:
        set_bool_setting(conn, "copilot_use_ltm", bool(args.use_ltm))
        say(FATHER, f"set copilot_use_ltm={bool(args.use_ltm)}")
        did_action = True
    if args.add:
        content = None
        if args.text:
            content = args.text
        elif args.path:
            content = read_text_file(Path(args.path))
            if content is None:
                say(FATHER, f"failed to read {args.path}")
                return
        elif args.stdin:
            content = sys.stdin.read()
        else:
            say(FATHER, "provide --text, --path, or --stdin with --add")
            return
        model = args.chat_model or get_setting_typed(conn, "copilot_model")
        ok = add_copilot_chat(conn, content or "", args.title, model)
        if ok:
            say(FATHER, "copilot chat saved")
        else:
            say(FATHER, "copilot chat empty; nothing saved")
        did_action = True
    if args.list:
        rows = list_copilot_chats(conn, args.limit)
        if not rows:
            say(FATHER, "no copilot chats")
        else:
            for row in rows:
                title = row.get("title") or ""
                model = row.get("model") or ""
                label = f"#{row['id']} {row['created_at']}"
                if model:
                    label += f" model={model}"
                if title:
                    label += f" title={title}"
                say(FATHER, label)
        did_action = True
    if args.clear:
        if not args.yes:
            say(FATHER, "refusing to clear chats without --yes")
            return
        deleted = clear_copilot_chats(conn)
        say(FATHER, f"cleared {deleted} copilot chats")
        did_action = True
    if args.status or not did_action:
        model = get_setting_typed(conn, "copilot_model")
        accent = get_setting_typed(conn, "copilot_accent")
        use_ltm = get_setting_typed(conn, "copilot_use_ltm")
        count = copilot_chat_count(conn)
        say(FATHER, f"model={model}, accent={accent}, use_ltm_by_default={use_ltm}, chats={count}")


def cmd_ml(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    did_action = False
    if args.context_level:
        set_setting(conn, "ml_context_level", args.context_level)
        say(FATHER, f"set ml_context_level={args.context_level}")
        did_action = True
    if args.processing_mode:
        set_setting(conn, "ml_processing_mode", args.processing_mode)
        say(FATHER, f"set ml_processing_mode={args.processing_mode}")
        did_action = True
    if args.ltm_on or args.ltm_off:
        set_bool_setting(conn, "ltm_enabled", bool(args.ltm_on))
        say(FATHER, f"set ltm_enabled={bool(args.ltm_on)}")
        did_action = True
    if args.permissions:
        set_setting(conn, "ltm_permissions", args.permissions)
        say(FATHER, f"set ltm_permissions={args.permissions}")
        did_action = True
    if args.optimize:
        current = get_embedder(conn, None)
        if current != "hash":
            set_embedder(conn, "hash")
            say(FATHER, "set embedder=hash to reduce memory; restart watcher to unload heavy models")
        else:
            say(FATHER, "embedder already hash; nothing to optimize")
        did_action = True
    if args.clear_all or args.clear_older_than_days or args.clear_since or args.clear_until:
        if not args.yes:
            say(FATHER, "refusing to clear data without --yes")
            return
        clauses = []
        params = []
        if args.clear_older_than_days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=args.clear_older_than_days)
            clauses.append("datetime(created_at) < datetime(?)")
            params.append(cutoff.isoformat())
        if args.clear_since:
            since_iso = parse_iso_dt(args.clear_since)
            if not since_iso:
                say(FATHER, "invalid --clear-since ISO timestamp")
                return
            clauses.append("datetime(created_at) >= datetime(?)")
            params.append(since_iso)
        if args.clear_until:
            until_iso = parse_iso_dt(args.clear_until)
            if not until_iso:
                say(FATHER, "invalid --clear-until ISO timestamp")
                return
            clauses.append("datetime(created_at) <= datetime(?)")
            params.append(until_iso)
        deleted = 0
        if args.clear_all:
            cur = conn.execute("DELETE FROM clips")
            deleted = cur.rowcount
        elif not clauses:
            say(FATHER, "no clear range provided")
            return
        else:
            where = " AND ".join(clauses)
            cur = conn.execute(f"DELETE FROM clips WHERE {where}", params)
            deleted = cur.rowcount
        conn.commit()
        say(FATHER, f"cleared {deleted} clips")
        did_action = True
    if args.status or not did_action:
        auto_level = get_setting(conn, "ml_context_level", DEFAULT_ML_CONTEXT_LEVEL)
        proc_mode = get_setting(conn, "ml_processing_mode", DEFAULT_ML_PROCESSING_MODE)
        ltm_enabled = get_bool_setting(conn, "ltm_enabled", DEFAULT_LTM_ENABLED)
        perms = get_setting(conn, "ltm_permissions", "unknown")
        blocklist = sorted(get_blocklist(conn))
        say(
            FATHER,
            f"auto_context_level={auto_level}, processing_mode={proc_mode}, ltm_enabled={ltm_enabled}, ltm_permissions={perms}, source_blocklist={blocklist}",
        )


def cmd_about(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    about = settings_snapshot(conn)["about"]
    if args.json:
        print(json.dumps(about, indent=2, ensure_ascii=False))
        return
    say(FATHER, f"app_version={format_setting_value(about.get('app_version'))}")
    say(FATHER, f"python={format_setting_value(about.get('python'))}")
    say(FATHER, f"platform={format_setting_value(about.get('platform'))}")
    say(FATHER, f"db_path={format_setting_value(about.get('db_path'))}")
    say(FATHER, f"mcp_host={format_setting_value(about.get('mcp_host'))}")
    say(FATHER, f"mcp_port={format_setting_value(about.get('mcp_port'))}")


def cmd_pause(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    if args.on:
        set_paused(conn, True)
    elif args.off:
        set_paused(conn, False)
    elif args.toggle:
        set_paused(conn, not is_paused(conn))
    state = "paused" if is_paused(conn) else "active"
    say(MOTHER, f"capture is now {state}")


def cmd_blocklist(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    did_something = False
    if args.add:
        if add_blocked_app(conn, args.add):
            say(FATHER, f"blocked app '{args.add.lower()}'")
        did_something = True
    if args.remove:
        removed = remove_blocked_app(conn, args.remove)
        if removed:
            say(FATHER, f"unblocked app '{args.remove.lower()}'")
        else:
            say(FATHER, f"app '{args.remove.lower()}' not in blocklist")
        did_something = True
    if not did_something or args.list:
        apps = sorted(get_blocklist(conn))
        if apps:
            say(FATHER, "blocklist: " + ", ".join(apps))
        else:
            say(FATHER, "blocklist is empty")


def cmd_show(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    cur = conn.execute(
        "SELECT id, created_at, source_app, window_title, content, pinned, title, file_path, lang FROM clips WHERE id = ?",
        (args.id,),
    )
    row = cur.fetchone()
    if not row:
        say(FATHER, f"no clip #{args.id}")
        return
    tags = tags_for_clip(conn, row["id"])
    pin_mark = "*" if row["pinned"] else " "
    title = f" \"{row['title']}\"" if row["title"] else ""
    tags_str = f" tags={','.join(tags)}" if tags else ""
    file_str = f" file={row['file_path']}" if row["file_path"] else ""
    lang = row["lang"] if row["lang"] not in (None, "", "unk") else ""
    lang_str = f" lang={lang}" if lang else ""
    # events count
    cur_e = conn.execute("SELECT COUNT(*) AS n, MIN(seen_at) AS first, MAX(seen_at) AS last FROM clip_events WHERE clip_id = ?", (row["id"],))
    ev = cur_e.fetchone()
    ev_summary = ""
    if ev and ev["n"]:
        ev_summary = f" seen={ev['n']} first={ev['first']} last={ev['last']}"
    say(FATHER, f"{pin_mark}#{row['id']} {row['created_at']} [{row['source_app']}] {row['window_title']}{title}{tags_str}{file_str}{lang_str}")
    if ev_summary:
        say(FATHER, ev_summary)
    print(row["content"])


def cmd_export(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    since_iso = iso_hours_ago(args.since_hours) if getattr(args, "since_hours", None) is not None else None
    items = export_items(conn, args.limit, app=args.app, tag=args.tag, since_iso=since_iso, pins_only=args.pins_only)
    data = json.dumps(items, indent=2, ensure_ascii=False)
    if args.path:
        Path(args.path).write_text(data, encoding="utf-8")
        say(FATHER, f"exported {len(items)} clips to {args.path}")
    else:
        print(data)


def build_markdown_outline(
    conn: sqlite3.Connection, since_iso: Optional[str], limit: int = 200
) -> tuple[str, int]:
    clauses = []
    params = []
    if since_iso:
        clauses.append("datetime(created_at) >= datetime(?)")
        params.append(since_iso)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT id, created_at, source_app, window_title, content, pinned, title, lang
        FROM clips
        {where}
        ORDER BY created_at DESC
        LIMIT ?;
    """
    params.append(limit)
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    tag_map = tags_for_clips(conn, [row["id"] for row in rows])
    tag_counts = Counter(t for tags in tag_map.values() for t in tags)
    grouped: dict[str, dict[str, list[sqlite3.Row]]] = {}
    for row in rows:
        created = row["created_at"] or ""
        date_key = created.split("T", 1)[0] if "T" in created else created[:10]
        app = row["source_app"] or "unknown"
        grouped.setdefault(date_key, {}).setdefault(app, []).append(row)
    lines: list[str] = []
    lines.append(f"# my--father-mother journal (latest {len(rows)} clips)")
    for date_key in sorted(grouped.keys(), reverse=True):
        lines.append(f"\n## {date_key}")
        apps = grouped[date_key]
        for app in sorted(apps.keys()):
            lines.append(f"- **{app}**")
            for row in apps[app]:
                created = row["created_at"] or ""
                time_part = created.split("T", 1)[1][:5] if "T" in created else created
                tags = tag_map.get(row["id"], [])
                tag_str = f" (tags: {', '.join(tags)})" if tags else ""
                title = row["title"] or row["window_title"] or ""
                if not title:
                    title = (row["content"] or "").strip().splitlines()[0] if (row["content"] or "").strip() else ""
                if len(title) > 120:
                    title = title[:117] + "..."
                pin = " 🔖" if row["pinned"] else ""
                lines.append(f"  - [{time_part}] #{row['id']}{pin} {title}{tag_str}")
                snippet_lines = (row["content"] or "").strip().splitlines()
                if snippet_lines:
                    lines.append("    ```")
                    for ln in snippet_lines[:8]:
                        lines.append("    " + ln)
                    if len(snippet_lines) > 8:
                        lines.append("    ...")
                    lines.append("    ```")
    if tag_counts:
        lines.append("\n## Tag totals")
        for name, count in tag_counts.most_common(20):
            lines.append(f"- {name}: {count}")
    return "\n".join(lines), len(rows)


def cmd_export_md(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    since_iso = iso_hours_ago(args.hours) if args.hours is not None else None
    md, count = build_markdown_outline(conn, since_iso, limit=args.limit)
    if args.path:
        Path(args.path).write_text(md, encoding="utf-8")
        say(FATHER, f"wrote markdown journal for {count} clips to {args.path}")
    else:
        print(md)


def cmd_federate_export(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    since_iso = iso_hours_ago(args.since_hours) if getattr(args, "since_hours", None) is not None else None
    items = export_items(conn, args.limit, app=args.app, tag=args.tag, since_iso=since_iso, pins_only=args.pins_only)
    payload = {"items": items}
    data = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.path:
        Path(args.path).write_text(data, encoding="utf-8")
        say(FATHER, f"federate export wrote {len(items)} items to {args.path}")
    else:
        print(data)


def cmd_federate_push(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    since_iso = iso_hours_ago(args.since_hours) if getattr(args, "since_hours", None) is not None else None
    items = export_items(conn, args.limit, app=args.app, tag=args.tag, since_iso=since_iso, pins_only=args.pins_only)
    payload = json.dumps({"items": items}, ensure_ascii=False).encode("utf-8")
    try:
        req = urllib.request.Request(args.url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        say(FATHER, f"pushed {len(items)} items to {args.url}; response={body[:200]}")
    except Exception as e:
        say(MOTHER, f"push failed: {e}")


def cmd_config(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    if args.get:
        key = args.get
        if key == "max_bytes":
            val = get_max_bytes(conn, None)
            say(FATHER, f"max_bytes={val}")
        elif key == "allow_secrets":
            val = get_allow_secrets(conn, None)
            say(FATHER, f"allow_secrets={val}")
        elif key == "max_db_mb":
            val = get_max_db_mb(conn, None)
            say(FATHER, f"max_db_mb={val}")
        elif key == "notify":
            val = get_notify(conn, None)
            say(FATHER, f"notify={val}")
        elif key == "embedder":
            val = get_embedder(conn, None)
            say(FATHER, f"embedder={val}")
        elif key == "pro_enabled":
            say(FATHER, f"pro_enabled={is_pro_enabled(conn)}")
        elif key == "license_type":
            say(FATHER, f"license_type={license_snapshot(conn)['license_type']}")
        elif key in ("license_key", "gumroad_license_key"):
            say(FATHER, f"{key}={mask_secret(get_license_key(conn))}")
        elif key == "gumroad_webhook_secret":
            say(FATHER, f"gumroad_webhook_secret={'***set***' if gumroad_webhook_secret(conn) else ''}")
        elif key == "gumroad_permalink":
            say(FATHER, f"gumroad_permalink={get_setting(conn, 'gumroad_permalink', '')}")
        elif key == "upgrade_url":
            say(FATHER, f"upgrade_url={get_upgrade_url(conn)}")
        elif key == "cap_by_app":
            say(FATHER, f"cap_by_app={get_cap_map(conn, 'cap_by_app')}")
        elif key == "cap_by_tag":
            say(FATHER, f"cap_by_tag={get_cap_map(conn, 'cap_by_tag')}")
        elif key == "evict_mode":
            say(FATHER, f"evict_mode={get_evict_mode(conn)}")
        elif key == "allow_pdf":
            say(FATHER, f"allow_pdf={get_allow_pdf(conn)}")
        elif key == "allow_images":
            say(FATHER, f"allow_images={get_allow_images(conn)}")
        elif key == "auto_summary_cmd":
            say(FATHER, f"auto_summary_cmd={get_setting(conn, 'auto_summary_cmd', '')}")
        elif key == "auto_tag_cmd":
            say(FATHER, f"auto_tag_cmd={get_setting(conn, 'auto_tag_cmd', '')}")
        elif key == "sync_target":
            say(FATHER, f"sync_target={get_setting(conn, 'sync_target', '')}")
        elif key == "sync_interval":
            say(FATHER, f"sync_interval={get_sync_interval(conn)}")
        elif key in ("backup_bucket", "backup_endpoint", "backup_region", "backup_prefix", "backup_access_key"):
            say(FATHER, f"{key}={get_setting(conn, key, '')}")
        elif key in ("backup_passphrase", "backup_secret_key"):
            secret = get_setting(conn, key, "")
            say(FATHER, f"{key}={'***set***' if secret else ''}")
        elif key == "ai_recall_cmd":
            say(FATHER, f"ai_recall_cmd={get_setting(conn, 'ai_recall_cmd', '')}")
        elif key == "ai_fill_cmd":
            say(FATHER, f"ai_fill_cmd={get_setting(conn, 'ai_fill_cmd', '')}")
        elif key == "helper_rewrite_cmd":
            say(FATHER, f"helper_rewrite_cmd={get_setting(conn, 'helper_rewrite_cmd', '')}")
        elif key == "helper_shorten_cmd":
            say(FATHER, f"helper_shorten_cmd={get_setting(conn, 'helper_shorten_cmd', '')}")
        elif key == "helper_extract_cmd":
            say(FATHER, f"helper_extract_cmd={get_setting(conn, 'helper_extract_cmd', '')}")
        elif key == "ml_context_level":
            say(FATHER, f"ml_context_level={get_setting(conn, 'ml_context_level', DEFAULT_ML_CONTEXT_LEVEL)}")
        elif key == "ml_processing_mode":
            say(FATHER, f"ml_processing_mode={get_setting(conn, 'ml_processing_mode', DEFAULT_ML_PROCESSING_MODE)}")
        elif key == "ltm_enabled":
            say(FATHER, f"ltm_enabled={get_bool_setting(conn, 'ltm_enabled', DEFAULT_LTM_ENABLED)}")
        else:
            say(FATHER, f"unknown key '{key}'")
        return
    if args.set:
        key, value = args.set
        if key == "max_bytes":
            try:
                intval = int(value)
                set_setting(conn, "max_bytes", str(intval))
                say(FATHER, f"set max_bytes={intval}")
            except ValueError:
                say(FATHER, "value must be an integer")
        elif key == "allow_secrets":
            if value.lower() in ("1", "true", "yes", "on"):
                set_allow_secrets(conn, True)
                say(FATHER, "set allow_secrets=True")
            elif value.lower() in ("0", "false", "no", "off"):
                set_allow_secrets(conn, False)
                say(FATHER, "set allow_secrets=False")
            else:
                say(FATHER, "value must be true/false or 1/0")
        elif key == "max_db_mb":
            try:
                intval = int(value)
                set_setting(conn, "max_db_mb", str(intval))
                say(FATHER, f"set max_db_mb={intval}")
            except ValueError:
                say(FATHER, "value must be an integer")
        elif key == "notify":
            if value.lower() in ("1", "true", "yes", "on"):
                set_notify(conn, True)
                say(FATHER, "set notify=True")
            elif value.lower() in ("0", "false", "no", "off"):
                set_notify(conn, False)
                say(FATHER, "set notify=False")
            else:
                say(FATHER, "value must be true/false or 1/0")
        elif key == "embedder":
            requested_embedder = get_embedder(conn, value)
            if requested_embedder == "e5-small" and not is_pro_enabled(conn):
                say(FATHER, f"e5-small embeddings require Pro. Upgrade: {get_upgrade_url(conn)}")
            else:
                set_embedder(conn, value)
                say(FATHER, f"set embedder={get_embedder(conn, None)}")
        elif key == "pro_enabled":
            parsed = parse_bool_value(value)
            if parsed is None:
                say(FATHER, "value must be true/false or 1/0")
            else:
                set_pro_enabled(conn, parsed)
                say(FATHER, f"set pro_enabled={is_pro_enabled(conn)}")
        elif key in ("license_key", "gumroad_license_key"):
            set_license_key(conn, value)
            say(FATHER, f"license stored; pro_enabled={is_pro_enabled(conn)}")
        elif key == "gumroad_webhook_secret":
            set_setting(conn, "gumroad_webhook_secret", value)
            say(FATHER, "set gumroad_webhook_secret (hidden)")
        elif key == "gumroad_permalink":
            set_setting(conn, "gumroad_permalink", value)
            say(FATHER, f"set gumroad_permalink={value}")
        elif key == "upgrade_url":
            set_setting(conn, "upgrade_url", value)
            say(FATHER, f"set upgrade_url={get_upgrade_url(conn)}")
        elif key == "cap_by_app":
            try:
                data = json.loads(value)
                if isinstance(data, dict):
                    set_cap_map(conn, "cap_by_app", data)
                    say(FATHER, f"set cap_by_app={get_cap_map(conn, 'cap_by_app')}")
                else:
                    say(FATHER, "cap_by_app must be a JSON object")
            except Exception as e:
                say(FATHER, f"invalid JSON: {e}")
        elif key == "cap_by_tag":
            try:
                data = json.loads(value)
                if isinstance(data, dict):
                    set_cap_map(conn, "cap_by_tag", data)
                    say(FATHER, f"set cap_by_tag={get_cap_map(conn, 'cap_by_tag')}")
                else:
                    say(FATHER, "cap_by_tag must be a JSON object")
            except Exception as e:
                say(FATHER, f"invalid JSON: {e}")
        elif key == "evict_mode":
            set_evict_mode(conn, value)
            say(FATHER, f"set evict_mode={get_evict_mode(conn)}")
        elif key == "allow_pdf":
            if value.lower() in ("1", "true", "yes", "on"):
                set_allow_pdf(conn, True)
            elif value.lower() in ("0", "false", "no", "off"):
                set_allow_pdf(conn, False)
            say(FATHER, f"set allow_pdf={get_allow_pdf(conn)}")
        elif key == "allow_images":
            if value.lower() in ("1", "true", "yes", "on"):
                set_allow_images(conn, True)
            elif value.lower() in ("0", "false", "no", "off"):
                set_allow_images(conn, False)
            say(FATHER, f"set allow_images={get_allow_images(conn)}")
        elif key == "auto_summary_cmd":
            set_setting(conn, "auto_summary_cmd", value)
            say(FATHER, "set auto_summary_cmd")
        elif key == "auto_tag_cmd":
            set_setting(conn, "auto_tag_cmd", value)
            say(FATHER, "set auto_tag_cmd")
        elif key == "sync_target":
            set_setting(conn, "sync_target", value)
            say(FATHER, f"set sync_target={value}")
        elif key == "sync_interval":
            try:
                floatval = max(1.0, float(value))
                set_setting(conn, "sync_interval", str(floatval))
                say(FATHER, f"set sync_interval={floatval}")
            except ValueError:
                say(FATHER, "value must be a number of seconds")
        elif key in ("backup_bucket", "backup_endpoint", "backup_region", "backup_prefix", "backup_access_key"):
            set_setting(conn, key, value)
            say(FATHER, f"set {key}={value}")
        elif key in ("backup_passphrase", "backup_secret_key"):
            set_setting(conn, key, value)
            say(FATHER, f"set {key} (hidden)")
        elif key == "ai_recall_cmd":
            set_setting(conn, "ai_recall_cmd", value)
            say(FATHER, "set ai_recall_cmd")
        elif key == "ai_fill_cmd":
            set_setting(conn, "ai_fill_cmd", value)
            say(FATHER, "set ai_fill_cmd")
        elif key == "helper_rewrite_cmd":
            set_setting(conn, "helper_rewrite_cmd", value)
            say(FATHER, "set helper_rewrite_cmd")
        elif key == "helper_shorten_cmd":
            set_setting(conn, "helper_shorten_cmd", value)
            say(FATHER, "set helper_shorten_cmd")
        elif key == "helper_extract_cmd":
            set_setting(conn, "helper_extract_cmd", value)
            say(FATHER, "set helper_extract_cmd")
        elif key == "ml_context_level":
            if value.lower() in ("none", "low", "medium", "high"):
                set_setting(conn, "ml_context_level", value.lower())
                say(FATHER, f"set ml_context_level={value.lower()}")
            else:
                say(FATHER, "ml_context_level must be: none, low, medium, or high")
        elif key == "ml_processing_mode":
            if value.lower() in ("manual", "auto", "blended"):
                set_setting(conn, "ml_processing_mode", value.lower())
                say(FATHER, f"set ml_processing_mode={value.lower()}")
            else:
                say(FATHER, "ml_processing_mode must be: manual, auto, or blended")
        elif key == "ltm_enabled":
            if value.lower() in ("1", "true", "yes", "on"):
                set_bool_setting(conn, "ltm_enabled", True)
                say(FATHER, "set ltm_enabled=True")
            elif value.lower() in ("0", "false", "no", "off"):
                set_bool_setting(conn, "ltm_enabled", False)
                say(FATHER, "set ltm_enabled=False")
            else:
                say(FATHER, "value must be true/false or 1/0")
        else:
            say(FATHER, f"unknown key '{key}'")


def cmd_install_launchagent(args: argparse.Namespace) -> None:
    """
    Install or remove a LaunchAgent to run the watcher on login.
    """
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.my-father-mother.watch.plist"
    if args.remove:
        if plist_path.exists():
            plist_path.unlink()
            say(FATHER, f"removed {plist_path}")
        else:
            say(FATHER, "launch agent not found")
        return

    python_path = sys.executable
    script_path = Path(__file__).resolve()
    cap = args.cap
    interval = args.interval
    allow_secrets = "--allow-secrets" if args.allow_secrets else ""
    contents = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.my-father-mother.watch</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{script_path}</string>
        <string>watch</string>
        <string>--cap</string>
        <string>{cap}</string>
        <string>--interval</string>
        <string>{interval}</string>
        <string>{allow_secrets}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
"""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(contents, encoding="utf-8")
    say(FATHER, f"wrote launch agent to {plist_path}; load with: launchctl load {plist_path}")


def helper_cli(kind: str, args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    target_id = args.id
    if target_id is None:
        latest = latest_clip(conn)
        if not latest:
            say(FATHER, "no clips found")
            return
        target_id = latest["id"]
    ok, msg, new_id, out = run_user_helper_on_clip(conn, kind, target_id, timeout=args.timeout)
    say(FATHER if ok else MOTHER, msg)
    if ok and args.show and out:
        print(out)


def cmd_rewrite(args: argparse.Namespace) -> None:
    helper_cli("rewrite", args)


def cmd_shorten(args: argparse.Namespace) -> None:
    helper_cli("shorten", args)


def cmd_extract(args: argparse.Namespace) -> None:
    helper_cli("extract", args)


def cmd_recall(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    ok, msg, new_id, out = run_ai_helper(
        conn,
        "ai_recall_cmd",
        args.hours,
        args.limit,
        args.timeout,
        save=args.save,
        tag_label="recall",
    )
    say(FATHER if ok else MOTHER, msg)
    if ok and args.show and out:
        print(out)


def cmd_fill(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    ok, msg, new_id, out = run_ai_helper(
        conn,
        "ai_fill_cmd",
        args.hours,
        args.limit,
        args.timeout,
        save=args.save,
        tag_label="fill",
    )
    say(FATHER if ok else MOTHER, msg)
    if ok and args.show and out:
        print(out)


def cmd_note(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    if args.id is None:
        say(FATHER, "provide --id")
        return
    if args.text:
        ok = add_note(conn, args.id, args.text)
        say(FATHER if ok else MOTHER, "added note" if ok else "note empty?")
    notes = notes_for_clips(conn, [args.id]).get(args.id, [])
    if not notes:
        say(FATHER, f"no notes for #{args.id}")
        return
    say(FATHER, f"notes for #{args.id}:")
    for n in notes:
        print(f"- {n['created_at']}: {n['note']}")


def cmd_copy(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    cur = conn.execute("SELECT content FROM clips WHERE id = ?", (args.id,))
    row = cur.fetchone()
    if not row:
        say(FATHER, f"no clip #{args.id}")
        return
    ok = copy_to_clipboard(row["content"])
    if ok:
        say(FATHER, f"copied clip #{args.id} to clipboard")
    else:
        say(FATHER, "failed to copy to clipboard")


def cmd_backup(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    dest = Path(args.path).expanduser()
    try:
        write_db_snapshot(dest, conn)
        say(FATHER, f"backup written to {dest}")
    except Exception as e:
        say(FATHER, f"backup failed: {e}")


def cmd_restore(args: argparse.Namespace) -> None:
    src = Path(args.path).expanduser()
    if not src.exists():
        say(FATHER, f"source not found: {src}")
        return
    try:
        import shutil
        if DB_PATH.exists():
            backup = DB_PATH.with_suffix(".bak")
            shutil.copy2(DB_PATH, backup)
            say(FATHER, f"existing DB backed up to {backup}")
        DB_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, DB_PATH)
        say(FATHER, f"restored DB from {src}")
    except Exception as e:
        say(FATHER, f"restore failed: {e}")


def cmd_sync(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    target = args.target or get_setting(conn, "sync_target", "").strip()
    if not target:
        say(FATHER, "set sync_target via config --set sync_target ... or pass --target")
        return
    if args.mode == "pull":
        ok, msg = sync_pull(target)
    else:
        ok, msg = sync_push(target, conn)
    say(FATHER if ok else MOTHER, msg)


# --- S3-compatible encrypted backups (boto3 optional) ------------------------

DEFAULT_BACKUP_PREFIX = "mfm-backups"
DEFAULT_BACKUP_INTERVAL = 3600  # hourly


def parse_s3_bucket(spec: str) -> Tuple[str, str]:
    """Split a backup_bucket spec into (bucket, prefix).

    Accepts ``bucket``, ``bucket/prefix``, or ``s3://bucket/prefix``.
    """
    spec = (spec or "").strip()
    if spec.startswith("s3://"):
        spec = spec[len("s3://"):]
    spec = spec.strip("/")
    if not spec:
        return "", ""
    if "/" in spec:
        bucket, prefix = spec.split("/", 1)
        return bucket, prefix.strip("/")
    return spec, ""


def snapshot_db_to(path: Path) -> None:
    """Write a consistent snapshot of the live DB to ``path``.

    Uses the SQLite online backup API so the snapshot is safe even while the
    watcher is writing (WAL mode).
    """
    src = sqlite3.connect(str(DB_PATH))
    try:
        dst = sqlite3.connect(str(path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def encrypt_file(src: Path, dst: Path, passphrase: str) -> Tuple[bool, str]:
    """Encrypt ``src`` to ``dst`` with AES-256-CBC via the openssl CLI.

    The passphrase is passed on stdin so it never appears in the process list.
    """
    try:
        proc = subprocess.run(
            [
                "openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt",
                "-in", str(src), "-out", str(dst), "-pass", "stdin",
            ],
            input=passphrase.encode("utf-8"),
            capture_output=True,
        )
    except FileNotFoundError:
        return False, "openssl not found (required for encrypted backup)"
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        return False, err or "openssl encryption failed"
    return True, "ok"


def s3_upload(
    local: Path,
    bucket: str,
    key: str,
    *,
    endpoint: str = "",
    region: str = "",
    access_key: str = "",
    secret_key: str = "",
) -> Tuple[bool, str]:
    """Upload ``local`` to ``s3://bucket/key`` using boto3 (optional import)."""
    try:
        import boto3  # optional dependency
    except ImportError:
        return False, "boto3 not installed; pip install boto3"
    client_kwargs: Dict[str, str] = {}
    if endpoint:
        client_kwargs["endpoint_url"] = endpoint
    if region:
        client_kwargs["region_name"] = region
    if access_key and secret_key:
        client_kwargs["aws_access_key_id"] = access_key
        client_kwargs["aws_secret_access_key"] = secret_key
    try:
        client = boto3.client("s3", **client_kwargs)
        client.upload_file(str(local), bucket, key)
    except Exception as e:  # boto3/botocore raise many error types
        return False, str(e)
    return True, f"s3://{bucket}/{key}"


def perform_cloud_backup(conn: sqlite3.Connection) -> Tuple[bool, str]:
    """Snapshot, encrypt, and upload the DB to the configured S3 bucket."""
    bucket_spec = get_setting(conn, "backup_bucket", "").strip()
    if not bucket_spec:
        return False, "set backup_bucket via config --set backup_bucket ..."
    if not DB_PATH.exists():
        return False, "no database found; run init?"
    passphrase = get_setting(conn, "backup_passphrase", "")
    if not passphrase:
        return (
            False,
            "set backup_passphrase via config --set backup_passphrase ... "
            "(required to encrypt the snapshot)",
        )
    bucket, spec_prefix = parse_s3_bucket(bucket_spec)
    if not bucket:
        return False, f"could not parse bucket from backup_bucket={bucket_spec!r}"
    prefix = get_setting(conn, "backup_prefix", "").strip() or spec_prefix or DEFAULT_BACKUP_PREFIX
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"mfm-{ts}.db.enc"
    key = f"{prefix.strip('/')}/{name}" if prefix else name

    import tempfile

    with tempfile.TemporaryDirectory(prefix="mfm-backup-") as td:
        snap = Path(td) / "snapshot.db"
        enc = Path(td) / "snapshot.db.enc"
        try:
            snapshot_db_to(snap)
        except Exception as e:
            return False, f"snapshot failed: {e}"
        ok, msg = encrypt_file(snap, enc, passphrase)
        if not ok:
            return False, f"encrypt failed: {msg}"
        ok, msg = s3_upload(
            enc,
            bucket,
            key,
            endpoint=get_setting(conn, "backup_endpoint", "").strip(),
            region=get_setting(conn, "backup_region", "").strip(),
            access_key=get_setting(conn, "backup_access_key", "").strip(),
            secret_key=get_setting(conn, "backup_secret_key", "").strip(),
        )
        if not ok:
            return False, f"upload failed: {msg}"
    return True, f"encrypted backup uploaded to {msg}"


def cmd_cloud_backup(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    interval = max(60, int(getattr(args, "interval", DEFAULT_BACKUP_INTERVAL) or DEFAULT_BACKUP_INTERVAL))
    if getattr(args, "loop", False):
        say(FATHER, f"cloud-backup loop started (every {interval}s); ctrl-c to stop")
        try:
            while True:
                ok, msg = perform_cloud_backup(conn)
                say(FATHER if ok else MOTHER, msg)
                time.sleep(interval)
        except KeyboardInterrupt:
            say(FATHER, "cloud-backup loop stopped")
        return
    ok, msg = perform_cloud_backup(conn)
    say(FATHER if ok else MOTHER, msg)


def cmd_pin(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    state = None
    if args.on:
        state = 1
    elif args.off:
        state = 0
    elif args.toggle:
        cur = conn.execute("SELECT pinned FROM clips WHERE id = ?", (args.id,))
        row = cur.fetchone()
        if not row:
            say(FATHER, f"no clip #{args.id}")
            return
        state = 0 if row["pinned"] else 1
    if state is None:
        say(FATHER, "specify --on/--off/--toggle")
        return
    cur = conn.execute("UPDATE clips SET pinned = ? WHERE id = ?", (state, args.id))
    conn.commit()
    if cur.rowcount:
        say(FATHER, f"clip #{args.id} pinned={bool(state)}")
    else:
        say(FATHER, f"no clip #{args.id}")


def cmd_tags(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    if args.list_all:
        all_tags = list_tags(conn)
        if all_tags:
            say(FATHER, "tags: " + ", ".join(all_tags))
        else:
            say(FATHER, "no tags")
        return
    if args.id is None:
        say(FATHER, "provide --id for tag operations")
        return
    if args.add:
        assign_tag(conn, args.id, args.add)
        say(FATHER, f"added tag '{args.add.lower()}' to #{args.id}")
    if args.remove:
        removed = remove_tag(conn, args.id, args.remove)
        if removed:
            say(FATHER, f"removed tag '{args.remove.lower()}' from #{args.id}")
        else:
            say(FATHER, f"tag '{args.remove.lower()}' not found on #{args.id}")
    if args.clear:
        count = clear_tags(conn, args.id)
        say(FATHER, f"cleared {count} tags on #{args.id}")
    current = tags_for_clip(conn, args.id)
    say(FATHER, f"#{args.id} tags: {', '.join(current) if current else 'none'}")


def cmd_purge(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    clauses = []
    params = []
    if args.app:
        clauses.append("LOWER(source_app) = LOWER(?)")
        params.append(args.app)
    if args.tag:
        clauses.append(
            "id IN (SELECT clip_id FROM clip_tags ct JOIN tags t ON t.id = ct.tag_id WHERE LOWER(t.name) = LOWER(?))"
        )
        params.append(args.tag)
    deleted = 0
    if args.older_than_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.older_than_days)
        clauses_cutoff = clauses + ["datetime(created_at) < datetime(?)"]
        params_cutoff = params + [cutoff.isoformat()]
        where_cutoff = "WHERE " + " AND ".join(clauses_cutoff)
        cur = conn.execute(f"DELETE FROM clips {where_cutoff}", params_cutoff)
        deleted += cur.rowcount
    if args.keep_last is not None:
        where_keep = "WHERE " + " AND ".join(clauses) if clauses else ""
        sql = f"""
            DELETE FROM clips
            WHERE id NOT IN (
                SELECT id FROM clips
                {where_keep}
                ORDER BY created_at DESC
                LIMIT ?
            )
            {f'AND {where_keep[6:]}' if where_keep else ''}
        """
        params_keep = params + [args.keep_last] + params
        cur = conn.execute(sql, params_keep)
        deleted += cur.rowcount
    if args.all:
        cur = conn.execute("DELETE FROM clips")
        deleted += cur.rowcount
    conn.commit()
    say(FATHER, f"purged {deleted} clips")


def cmd_history(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    cur = conn.execute(
        "SELECT seen_at FROM clip_events WHERE clip_id = ? ORDER BY seen_at DESC LIMIT ?",
        (args.id, args.limit),
    )
    rows = cur.fetchall()
    if not rows:
        say(FATHER, f"no history for clip #{args.id}")
        return
    for row in rows:
        say(FATHER, f"clip #{args.id} seen_at {row['seen_at']}")


def ingest_file_at_path(
    conn: sqlite3.Connection,
    path: Path,
    max_bytes: int,
    allow_secrets: bool,
    embedder_override: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> Optional[int]:
    if not path.is_file():
        return None
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        # only if enabled
        if not get_allow_pdf(conn):
            return None
    elif suffix and suffix not in ALLOWED_EXTS:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > max_bytes:
        say(MOTHER, f"skipped {path} ({size} bytes > max {max_bytes})")
        return None
    text = None
    if suffix == ".pdf":
        if command_exists("pdftotext"):
            try:
                text = subprocess.check_output(["pdftotext", "-q", "-layout", str(path), "-"]).decode("utf-8", errors="ignore")
            except Exception:
                text = None
        if not text:
            return None
    else:
        text = read_text_file(path)
    if text is None or not text.strip():
        return None
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    existing_id = get_clip_id_by_hash(conn, digest)
    if not allow_secrets and looks_like_secret(text):
        say(MOTHER, f"skipped {path} (looks like secret). Enable allow_secrets to store.")
        return None
    title = path.name
    if existing_id:
        insert_event(conn, existing_id)
        say(MOTHER, f"noted repeat clip #{existing_id} from file {path.name}")
        return existing_id
    inserted_id = ingest_text(
        conn,
        text,
        app="inbox",
        window=path.name,
        digest=digest,
        title=title,
        file_path=str(path),
        embedder_override=embedder_override,
        tags=tags or [],
    )
    if inserted_id:
        insert_event(conn, inserted_id)
        say(MOTHER, f"ingested file {path.name} as clip #{inserted_id}")
        caps = get_cap_map(conn, "cap_by_app")
        if "inbox" in caps:
            ev = evict_app_cap(conn, "inbox", caps["inbox"])
            if ev:
                say(MOTHER, f"evicted {ev} old clips for app cap (inbox)")
    return inserted_id


def ingest_image_with_ocr(conn: sqlite3.Connection, path: Path, max_bytes: int, allow_secrets: bool, embedder_override: Optional[str] = None) -> Optional[int]:
    if not path.is_file():
        return None
    if not get_allow_images(conn):
        return None
    if not command_exists("tesseract"):
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > max_bytes:
        say(MOTHER, f"skipped {path} ({size} bytes > max {max_bytes})")
        return None
    try:
        text = subprocess.check_output(["tesseract", str(path), "stdout"], stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
    except Exception:
        return None
    if not text or not text.strip():
        return None
    if not allow_secrets and looks_like_secret(text):
        say(MOTHER, f"skipped {path} (looks like secret). Enable allow_secrets or use --allow-secrets.")
        return None
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    existing_id = get_clip_id_by_hash(conn, digest)
    title = path.name
    if existing_id:
        insert_event(conn, existing_id)
        say(MOTHER, f"noted repeat clip #{existing_id} from image {path.name}")
        return existing_id
    inserted_id = insert_clip(
        conn,
        text,
        app="image",
        window=path.name,
        digest=digest,
        title=title,
        file_path=str(path),
        embedder_override=embedder_override,
    )
    if inserted_id:
        insert_event(conn, inserted_id)
        say(MOTHER, f"ingested image {path.name} via OCR as clip #{inserted_id}")
    return inserted_id


def ingest_transcript(
    conn: sqlite3.Connection,
    path: Path,
    max_bytes: int,
    allow_secrets: bool,
    embedder_override: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> Optional[int]:
    if not path.is_file():
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > max_bytes:
        say(MOTHER, f"skipped {path} ({size} bytes > max {max_bytes})")
        return None
    text = read_text_file(path)
    if text is None or not text.strip():
        return None
    if not allow_secrets and looks_like_secret(text):
        say(MOTHER, f"skipped {path} (looks like secret). Enable allow_secrets to store.")
        return None
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    existing_id = get_clip_id_by_hash(conn, digest)
    title = path.name
    if existing_id:
        insert_event(conn, existing_id)
        say(MOTHER, f"noted repeat transcript #{existing_id} from {path.name}")
        return existing_id
    inserted_id = ingest_text(
        conn,
        content=text,
        app="meeting",
        window=title,
        digest=digest,
        title=title,
        file_path=str(path),
        embedder_override=embedder_override,
        tags=(tags or []) + ["meeting", "transcript"],
    )
    if inserted_id:
        insert_event(conn, inserted_id)
        say(MOTHER, f"ingested transcript {path.name} as clip #{inserted_id}")
    return inserted_id


def import_clips(conn: sqlite3.Connection, items: list[dict]) -> dict:
    inserted = 0
    existing = 0
    failed = 0
    for item in items:
        content = item.get("content") or ""
        app = item.get("source_app") or "import"
        window = item.get("window_title") or item.get("title") or ""
        created_at = item.get("created_at")
        pinned = bool(item.get("pinned", False))
        title = item.get("title")
        file_path = item.get("file_path")
        lang = item.get("lang")
        tags = item.get("tags") or []
        clip_id = insert_clip_import(conn, content, app, window, created_at, pinned, title, file_path, lang, tags)
        if clip_id is None:
            failed += 1
            continue
        if content and get_clip_id_by_hash(conn, hashlib.sha256(content.encode('utf-8')).hexdigest()) == clip_id:
            inserted += 1
        else:
            existing += 1
    return {"inserted": inserted, "existing": existing, "failed": failed}


def cmd_ingest_file(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    max_bytes = get_max_bytes(conn, args.max_bytes)
    allow_secrets = get_allow_secrets(conn, args.allow_secrets)
    if args.allow_pdf is not None:
        set_allow_pdf(conn, args.allow_pdf)
    embedder_choice, embedder_warning = resolve_embedder_for_use(conn, getattr(args, "embedder", None))
    if embedder_warning:
        say(FATHER, embedder_warning)
    path = Path(args.path).expanduser()
    if not path.exists():
        say(MOTHER, f"path not found: {path}")
        return
    ingest_file_at_path(conn, path, max_bytes, allow_secrets, embedder_choice, tags=args.tag or [])


def cmd_watch_inbox(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    inbox_dir = Path(args.dir).expanduser()
    inbox_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = get_max_bytes(conn, args.max_bytes)
    max_db_mb = get_max_db_mb(conn, None)
    allow_secrets = get_allow_secrets(conn, args.allow_secrets)
    if args.allow_pdf is not None:
        set_allow_pdf(conn, args.allow_pdf)
    embedder_choice, embedder_warning = resolve_embedder_for_use(conn, getattr(args, "embedder", None))
    if embedder_warning:
        say(FATHER, embedder_warning)
    interval = max(1.0, args.interval)
    say(MOTHER, f"watching inbox {inbox_dir} (extensions: {', '.join(sorted(ALLOWED_EXTS))})")
    try:
        while True:
            for path in inbox_dir.iterdir():
                ingest_file_at_path(conn, path, max_bytes, allow_secrets, embedder_choice, tags=args.tag or [])
            ev = evict_if_needed(conn, max_db_mb)
            if ev:
                say(MOTHER, f"evicted {ev} oldest clips to honor db cap {max_db_mb}MB")
            time.sleep(interval)
    except KeyboardInterrupt:
        say(MOTHER, "inbox watcher stopped.")


def cmd_ingest_image(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    max_bytes = get_max_bytes(conn, args.max_bytes)
    allow_secrets = get_allow_secrets(conn, args.allow_secrets)
    if args.allow_images:
        set_allow_images(conn, True)
    embedder_choice, embedder_warning = resolve_embedder_for_use(conn, getattr(args, "embedder", None))
    if embedder_warning:
        say(FATHER, embedder_warning)
    path = Path(args.path).expanduser()
    if not path.exists():
        say(MOTHER, f"path not found: {path}")
        return
    res = ingest_image_with_ocr(conn, path, max_bytes, allow_secrets, embedder_choice)
    if not res:
        say(MOTHER, "no text found or OCR unavailable")


def cmd_ingest_transcript(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    max_bytes = get_max_bytes(conn, args.max_bytes)
    allow_secrets = get_allow_secrets(conn, args.allow_secrets)
    embedder_choice, embedder_warning = resolve_embedder_for_use(conn, getattr(args, "embedder", None))
    if embedder_warning:
        say(FATHER, embedder_warning)
    path = Path(args.path).expanduser()
    if not path.exists():
        say(MOTHER, f"path not found: {path}")
        return
    res = ingest_transcript(conn, path, max_bytes, allow_secrets, embedder_choice, tags=args.tag or [])
    if not res:
        say(MOTHER, "transcript ingest skipped/failed")


def cmd_federate_import(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    items: list[dict] = []
    if args.path:
        src = Path(args.path).expanduser()
        if not src.exists():
            say(FATHER, f"path not found: {src}")
            return
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except Exception as e:
            say(MOTHER, f"failed to read JSON: {e}")
            return
        items = data.get("items") if isinstance(data, dict) else data
    elif args.url:
        try:
            with urllib.request.urlopen(args.url, timeout=8.0) as resp:
                body = resp.read().decode("utf-8")
            data = json.loads(body)
            items = data.get("items") if isinstance(data, dict) else data
        except Exception as e:
            say(MOTHER, f"failed to fetch url: {e}")
            return
    else:
        say(FATHER, "provide --path or --url")
        return
    if not isinstance(items, list):
        say(MOTHER, "expected list of items")
        return
    res = import_clips(conn, items)
    say(FATHER, f"federate import: inserted={res['inserted']} existing={res['existing']} failed={res['failed']}")


def cmd_related(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    cur = conn.execute(
        "SELECT vector, model FROM clip_vectors WHERE clip_id = ?",
        (args.id,),
    )
    row = cur.fetchone()
    if not row:
        say(FATHER, f"no embedding for clip #{args.id}")
        return
    qvec = load_embedding(row)
    model_used = row["model"] if "model" in row.keys() else "hash"
    rows = fetch_semantic_candidates(conn, args.app, args.tag, args.pool, model_used)
    ids, vecs = build_ann_index(rows)
    sims_all = knn(qvec, ids, vecs, args.limit + 1)
    sims = [(sim, cid) for sim, cid in sims_all if cid != args.id][: args.limit]
    row_map: Dict[int, sqlite3.Row] = {row["id"]: row for row in rows}
    tag_map = tags_for_clips(conn, [cid for _, cid in sims])
    for sim, cid in sims:
        r = row_map.get(cid)
        if not r:
            continue
        preview = r["content"].replace("\n", "\\n")
        if len(preview) > 120:
            preview = preview[:117] + "..."
        pin_mark = "*" if r["pinned"] else " "
        tags = tag_map.get(r["id"], [])
        tags_str = f" tags={','.join(tags)}" if tags else ""
        title = f" \"{r['title']}\"" if r["title"] else ""
        lang = r["lang"] if r["lang"] not in (None, "", "unk") else ""
        lang_str = f" lang={lang}" if lang else ""
        say(FATHER, f"{pin_mark}#{r['id']:>5} sim={sim:.3f} [{r['source_app']}] {preview}{title}{tags_str}{lang_str}")


def cmd_recap(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=args.minutes)
    cur = conn.execute(
        """
        SELECT id, created_at, source_app, window_title, content, title, lang
        FROM clips
        WHERE datetime(created_at) >= datetime(?)
        ORDER BY created_at DESC
        LIMIT ?;
        """,
        (cutoff.isoformat(), args.limit),
    )
    rows = cur.fetchall()
    if not rows:
        say(FATHER, "no clips in window")
        return
    grouped: Dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["source_app"] or "unknown", []).append(row)
    for app, items in grouped.items():
        say(FATHER, f"[{app}] {len(items)} clips")
        for row in items:
            preview = row["content"].replace("\n", "\\n")
            if len(preview) > 100:
                preview = preview[:97] + "..."
            title = f" \"{row['title']}\"" if row["title"] else ""
            lang = row["lang"] if row["lang"] not in (None, "", "unk") else ""
            lang_str = f" lang={lang}" if lang else ""
            say(FATHER, f"  - {row['created_at']}: {preview}{title}{lang_str}")


def cmd_context(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    rows = context_bundle(
        conn,
        app=args.app,
        tag=args.tag,
        limit=args.limit,
        hours=args.since_hours,
        pins_only=args.pins_only,
    )
    if not rows:
        say(FATHER, "no context rows")
        return
    for row in rows:
        pin_mark = "*" if row["pinned"] else " "
        tags = row.get("tags") or []
        tags_str = f" tags={','.join(tags)}" if tags else ""
        title = f" \"{row['title']}\"" if row["title"] else ""
        lang = row["lang"] if row["lang"] not in (None, "", "unk") else ""
        lang_str = f" lang={lang}" if lang else ""
        note_ct = len(row.get("notes") or [])
        notes_str = f" notes={note_ct}" if note_ct else ""
        preview = row["content"].replace("\n", "\\n")
        if len(preview) > 120:
            preview = preview[:117] + "..."
        say(FATHER, f"{pin_mark}#{row['id']:>5} {row['created_at']} [{row['source_app']}] {preview}{title}{tags_str}{notes_str}{lang_str}")


def cmd_topics(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    since_iso = parse_iso_dt(args.since) if getattr(args, "since", None) else None
    if getattr(args, "since_hours", None) is not None:
        since_iso = iso_hours_ago(args.since_hours)
    until_iso = parse_iso_dt(args.until) if getattr(args, "until", None) else None
    groups = topic_groups(
        conn,
        limit_groups=args.limit,
        per_group=args.per_group,
        app=args.app,
        tag=args.tag,
        pins_only=args.pins_only,
        since_iso=since_iso,
        until_iso=until_iso,
    )
    if not groups:
        say(FATHER, "no clips to group")
        return
    for grp in groups:
        say(FATHER, f"[{grp['kind']}] {grp['name']} ({grp['count']} clips, latest {grp['latest']})")
        for row in grp["items"]:
            preview = row["content"].replace("\n", "\\n")
            if len(preview) > 100:
                preview = preview[:97] + "..."
            pin_mark = "*" if row["pinned"] else " "
            tags = row.get("tags") or []
            tags_str = f" tags={','.join(tags)}" if tags else ""
            title = f" \"{row['title']}\"" if row["title"] else ""
            lang = row["lang"] if row["lang"] not in (None, "", "unk") else ""
            lang_str = f" lang={lang}" if lang else ""
            say(FATHER, f"  {pin_mark}#{row['id']:>5} [{row['source_app']}] {preview}{title}{tags_str}{lang_str}")


def cmd_palette(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)
    rows: list[sqlite3.Row] = []
    since_iso = iso_hours_ago(args.since_hours) if getattr(args, "since_hours", None) is not None else None
    if args.semantic and args.query:
        embedder_kind, embedder_warning = resolve_embedder_for_use(conn, getattr(args, "embedder", None))
        if embedder_warning:
            say(FATHER, embedder_warning)
        qvec, model_used = embed_from_kind(embedder_kind, args.query)
        rows_sem = fetch_semantic_candidates(conn, args.app, args.tag, args.limit * 5, model_used, since_iso=since_iso, pins_only=args.pins_only)
        ids, vecs = build_ann_index(rows_sem)
        sims = knn(qvec, ids, vecs, args.limit)
        row_map = {row["id"]: row for row in rows_sem}
        for sim, cid in sims:
            row = row_map.get(cid)
            if row:
                rows.append(row)
    elif args.query:
        # FTS filter
        clauses = ["clips_fts MATCH ?"]
        params = [args.query]
        if args.app:
            clauses.append("LOWER(c.source_app) = LOWER(?)")
            params.append(args.app)
        if args.tag:
            clauses.append(
                "c.id IN (SELECT clip_id FROM clip_tags ct JOIN tags t ON t.id = ct.tag_id WHERE LOWER(t.name) = LOWER(?))"
            )
            params.append(args.tag)
        if args.pins_only:
            clauses.append("c.pinned = 1")
        if since_iso:
            clauses.append("datetime(c.created_at) >= datetime(?)")
            params.append(since_iso)
        where = " AND ".join(clauses)
        sql = f"""
            SELECT c.id, c.created_at, c.source_app, c.window_title, c.content, c.pinned, c.title, c.lang
            FROM clips_fts f
            JOIN clips c ON c.id = f.rowid
            WHERE {where}
            ORDER BY c.created_at DESC
            LIMIT ?;
        """
        params.append(args.limit)
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    else:
        clauses = []
        params = []
        if args.app:
            clauses.append("LOWER(source_app) = LOWER(?)")
            params.append(args.app)
        if args.tag:
            clauses.append(
                "id IN (SELECT clip_id FROM clip_tags ct JOIN tags t ON t.id = ct.tag_id WHERE LOWER(t.name) = LOWER(?))"
            )
            params.append(args.tag)
        if args.pins_only:
            clauses.append("pinned = 1")
        if since_iso:
            clauses.append("datetime(created_at) >= datetime(?)")
            params.append(since_iso)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        sql = f"""
            SELECT id, created_at, source_app, window_title, content, pinned, title, lang
            FROM clips
            {where}
            ORDER BY created_at DESC
            LIMIT ?;
        """
        params.append(args.limit)
        cur = conn.execute(sql, params)
        rows = cur.fetchall()

    if not rows:
        say(FATHER, "no clips found for palette")
        return
    tag_map = tags_for_clips(conn, [row["id"] for row in rows])
    for idx, row in enumerate(rows, start=1):
        preview = row["content"].replace("\n", "\\n")
        if len(preview) > 80:
            preview = preview[:77] + "..."
        title = f" \"{row['title']}\"" if row["title"] else ""
        tags = tag_map.get(row["id"], [])
        tags_str = f" tags={','.join(tags)}" if tags else ""
        pin_mark = "*" if row["pinned"] else " "
        lang = row["lang"] if row["lang"] not in (None, "", "unk") else ""
        lang_str = f" lang={lang}" if lang else ""
        say(FATHER, f"{idx:>2}. {pin_mark}#{row['id']:>5} [{row['source_app']}] {preview}{title}{tags_str}{lang_str}")
    choice = input("[father] pick number to copy (blank to cancel): ").strip()
    if not choice:
        return
    try:
        num = int(choice)
    except ValueError:
        say(FATHER, "invalid selection")
        return
    if num < 1 or num > len(rows):
        say(FATHER, "out of range")
        return
    selected = rows[num - 1]
    if copy_to_clipboard(selected["content"]):
        say(FATHER, f"copied clip #{selected['id']} to clipboard")
    else:
        say(FATHER, "failed to copy to clipboard")


# ---------- HTTP server ----------
class ApiHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *inner_args, conn: sqlite3.Connection, **kwargs):
        self.conn = conn
        super().__init__(*inner_args, **kwargs)

    def _send(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _parse_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def log_message(self, format: str, *args) -> None:
        # Quiet by default
        return

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Gumroad-Signature")
        self.end_headers()

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        qs = urllib.parse.parse_qs(query)
        conn = self.conn
        init_db(conn)
        if path in ("/", "/ui", "/dashboard"):
            html = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>my--father-mother</title>
  <style>
    :root { --bg:#0b1021; --panel:#111727; --panel-2:#151d30; --line:#263149; --muted:#8fa4b8; --text:#e8ecf1; --teal:#5eead4; --gold:#f6c453; --warn:#f59e0b; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; background: var(--bg); color: var(--text); }
    h1 { margin: 0 0 12px; }
    input, button, select { padding: 8px; margin: 4px; border-radius: 4px; border: 1px solid #334; background: #111727; color: #e8ecf1; }
    .result { padding: 8px; margin: 8px 0; border: 1px solid #223; border-radius: 6px; background: #111727; }
    .topic { border-color: #2a3b5e; }
    .topic-header { color: #9cd1ff; font-weight: 600; margin-bottom: 6px; }
    .topic-items { margin-left: 6px; }
    .topic-item { border-top: 1px solid #223; padding-top: 6px; margin-top: 6px; }
    .meta { color: #8aa; font-size: 12px; }
    .score { color: #ffd479; }
    .pinned { color: #ff9f1c; font-weight: 600; }
    .tag { background: #1f2c42; padding: 2px 6px; margin-right: 4px; border-radius: 4px; font-size: 12px; }
    .status { padding: 8px; border-radius: 6px; margin-bottom: 12px; display: inline-block; }
    .status.ok { background: #12301f; color: #9bffb5; border: 1px solid #1f7a3c; }
    .status.paused { background: #302112; color: #ffd479; border: 1px solid #8a5a12; }
    .status.warn { background: #302312; color: #ffc773; border: 1px solid #9b6b1a; }
    .note { background: #182035; border-radius: 4px; padding: 4px 6px; margin: 4px 0; font-size: 12px; color: #cdd7f3; }
    .note small { color: #8aa; }
    .dashboard { margin: 0 0 16px; }
    .metrics { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 8px; margin-bottom: 8px; }
    .metric, .panel { border: 1px solid var(--line); border-radius: 6px; background: var(--panel); }
    .metric { min-height: 76px; padding: 10px; box-sizing: border-box; }
    .metric-label, .panel-title { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0; }
    .metric strong { display: block; margin-top: 6px; font-size: 24px; line-height: 1.1; color: var(--text); }
    .metric small { display: block; margin-top: 6px; color: var(--muted); font-size: 12px; }
    .dashboard-lower { display: grid; grid-template-columns: repeat(3, minmax(180px, 1fr)); gap: 8px; }
    .panel { padding: 10px; min-height: 128px; box-sizing: border-box; }
    .bar-list { margin-top: 8px; }
    .bar-row { display: grid; grid-template-columns: minmax(72px, 1fr) minmax(80px, 2fr) 44px; gap: 8px; align-items: center; margin: 7px 0; font-size: 13px; }
    .bar-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #dbe7f3; }
    .bar-track, .storage-track { height: 8px; background: #0d1324; border-radius: 999px; overflow: hidden; border: 1px solid #1f2a44; }
    .bar-fill { height: 100%; width: 0%; background: linear-gradient(90deg, var(--teal), var(--gold)); }
    .bar-count { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
    .storage-track { margin-top: 8px; }
    #storageBar { height: 100%; width: 0%; background: var(--teal); }
    .empty { color: var(--muted); font-size: 13px; margin-top: 10px; }
    @media (max-width: 820px) {
      body { margin: 14px; }
      .metrics, .dashboard-lower { grid-template-columns: 1fr; }
      .bar-row { grid-template-columns: minmax(64px, 1fr) minmax(72px, 2fr) 38px; }
    }
  </style>
</head>
<body>
  <h1>my--father-mother</h1>
  <div id="status" class="status"></div>
  <section id="dashboard" class="dashboard" aria-label="Status and usage dashboard">
    <div class="metrics">
      <div class="metric">
        <span class="metric-label">Clips</span>
        <strong id="metricTotal">0</strong>
        <small id="metricRecent">0 in 24h / 0 in 7d</small>
      </div>
      <div class="metric">
        <span class="metric-label">Storage</span>
        <strong id="metricStorage">0 MB</strong>
        <div class="storage-track"><div id="storageBar"></div></div>
        <small id="metricStorageCap">cap not set</small>
      </div>
      <div class="metric">
        <span class="metric-label">Organization</span>
        <strong id="metricOrg">0 pinned</strong>
        <small id="metricTags">0 tagged / 0 notes</small>
      </div>
      <div class="metric">
        <span class="metric-label">Index</span>
        <strong id="metricVectors">0%</strong>
        <small id="metricLatest">latest n/a</small>
      </div>
    </div>
    <div class="dashboard-lower">
      <div class="panel">
        <div class="panel-title">Top apps</div>
        <div id="topApps" class="bar-list"></div>
      </div>
      <div class="panel">
        <div class="panel-title">Top tags</div>
        <div id="topTags" class="bar-list"></div>
      </div>
      <div class="panel">
        <div class="panel-title">Daily clips</div>
        <div id="dailyClips" class="bar-list"></div>
      </div>
    </div>
  </section>
  <div>
    <input id="q" placeholder="Search..." size="40"/>
    <label><input type="checkbox" id="semantic"/> semantic</label>
    <select id="app"></select>
    <select id="tag"></select>
    <select id="timeframe">
      <option value="">any time</option>
      <option value="24">last 24h</option>
      <option value="168">last 7d</option>
      <option value="720">last 30d</option>
    </select>
    <label><input type="checkbox" id="pins"/> pins only</label>
    <label><input type="checkbox" id="autoRefresh"/> auto-refresh</label>
    <select id="autoSeconds">
      <option value="15">15s</option>
      <option value="30" selected>30s</option>
      <option value="60">60s</option>
      <option value="120">120s</option>
    </select>
    <button onclick="runSearch()">Search</button>
    <button onclick="loadRecent()">Recent</button>
    <button onclick="loadTopics()">Topics</button>
  </div>
  <div id="results"></div>
  <script>
    let lastMode = 'recent';
    let autoTimer = null;
    function formatNumber(value) {
      return new Intl.NumberFormat().format(Number(value || 0));
    }
    function formatMb(value) {
      const n = Number(value || 0);
      const digits = n >= 10 ? 1 : 3;
      return `${n.toFixed(digits).replace(/\\.0+$|0+$/,'').replace(/\\.$/,'')} MB`;
    }
    function setText(id, value) {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    }
    function renderBars(id, items) {
      const el = document.getElementById(id);
      if (!el) return;
      el.innerHTML = '';
      if (!items || !items.length) {
        const empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = 'No data yet';
        el.appendChild(empty);
        return;
      }
      const max = Math.max(...items.map(it => Number(it.count || 0)), 1);
      items.forEach(it => {
        const row = document.createElement('div');
        row.className = 'bar-row';
        const name = document.createElement('span');
        name.className = 'bar-name';
        name.textContent = it.name || it.day || 'unknown';
        name.title = name.textContent;
        const track = document.createElement('div');
        track.className = 'bar-track';
        const fill = document.createElement('div');
        fill.className = 'bar-fill';
        fill.style.width = `${Math.max(4, (Number(it.count || 0) / max) * 100)}%`;
        track.appendChild(fill);
        const count = document.createElement('span');
        count.className = 'bar-count';
        count.textContent = formatNumber(it.count);
        row.appendChild(name);
        row.appendChild(track);
        row.appendChild(count);
        el.appendChild(row);
      });
    }
    function renderDashboard(s) {
      const u = s.usage || {};
      const storage = u.storage || {};
      const total = Number(u.total_clips || s.count || 0);
      const storagePct = storage.used_pct == null ? null : Math.max(0, Math.min(100, Number(storage.used_pct)));
      setText('metricTotal', formatNumber(total));
      setText('metricRecent', `${formatNumber(u.clips_last_24h)} in 24h / ${formatNumber(u.clips_last_7d)} in 7d`);
      setText('metricStorage', formatMb(storage.db_size_mb || s.db_size_mb || 0));
      setText('metricStorageCap', storagePct == null ? 'cap not set' : `${storagePct.toFixed(1)}% of ${formatMb(storage.max_db_mb)}`);
      setText('metricOrg', `${formatNumber(u.pinned_clips)} pinned`);
      setText('metricTags', `${formatNumber(u.tagged_clips)} tagged / ${formatNumber(u.note_count)} notes`);
      setText('metricVectors', `${Number(u.vector_coverage_pct || 0).toFixed(1)}%`);
      setText('metricLatest', `latest ${u.latest_clip_at || s.latest || 'n/a'}`);
      const storageBar = document.getElementById('storageBar');
      if (storageBar) {
        storageBar.style.width = storagePct == null ? '0%' : `${storagePct}%`;
        storageBar.style.background = storagePct != null && storagePct >= 80 ? 'var(--warn)' : 'var(--teal)';
      }
      renderBars('topApps', u.top_apps || []);
      renderBars('topTags', u.top_tags || []);
      renderBars('dailyClips', (u.daily_counts || []).map(d => ({name: d.day, count: d.count})));
    }
    async function loadTags() {
      const r = await fetch('/tags');
      const data = await r.json();
      const tagSel = document.getElementById('tag');
      tagSel.innerHTML = '<option value="">(tag)</option>';
      (data.tags || []).forEach(t => {
        const opt = document.createElement('option');
        opt.value = t; opt.textContent = t;
        tagSel.appendChild(opt);
      });
    }
    async function loadStatus() {
      const el = document.getElementById('status');
      try {
        const r = await fetch('/status');
        if (!r.ok) return;
        const s = await r.json();
        renderDashboard(s);
        const paused = !!s.paused;
        const maxMb = s.max_db_mb || 0;
        const usedMb = s.db_size_mb || 0;
        const nearingCap = maxMb > 0 && usedMb / maxMb >= 0.8;
        const cls = paused ? 'status paused' : nearingCap ? 'status warn' : 'status ok';
        el.className = cls;
        if (paused) {
          el.textContent = 'Paused • moon is sleeping';
        } else if (nearingCap) {
          el.textContent = `Active • nearing cap (${usedMb.toFixed(1)} / ${maxMb.toFixed(1)} MB)`;
        } else {
          el.textContent = `Active • ${s.count || 0} clips`;
        }
        el.title = `notify=${s.notify}, allow_secrets=${s.allow_secrets}, embedder=${s.embedder}, evict=${s.evict_mode}, max_bytes=${s.max_bytes}, max_db_mb=${s.max_db_mb}, caps=${JSON.stringify({app:s.cap_by_app,tag:s.cap_by_tag})}, clips=${s.count}, latest=${s.latest || 'n/a'}, db_mb=${usedMb.toFixed(3)}`;
      } catch (e) {
        el.textContent = '';
      }
    }
    async function loadRecent() {
      lastMode = 'recent';
      const app = document.getElementById('app').value;
      const tag = document.getElementById('tag').value;
      const pins = document.getElementById('pins').checked;
      const hours = document.getElementById('timeframe').value;
      const params = new URLSearchParams({limit: 30});
      if (app) params.set('app', app);
      if (tag) params.set('tag', tag);
      if (pins) params.set('pins_only', 'true');
      if (hours) params.set('hours', hours);
      const r = await fetch('/recent?' + params.toString());
      const data = await r.json();
      render(data.items || []);
    }
    async function runSearch() {
      const q = document.getElementById('q').value.trim();
      const semantic = document.getElementById('semantic').checked;
      const app = document.getElementById('app').value;
      const tag = document.getElementById('tag').value;
      const pins = document.getElementById('pins').checked;
      const hours = document.getElementById('timeframe').value;
      if (!q) { loadRecent(); return; }
      lastMode = 'search';
      const endpoint = semantic ? '/semantic_search' : '/search';
      const params = new URLSearchParams({q, limit: 30});
      if (app) params.set('app', app);
      if (tag) params.set('tag', tag);
      if (pins) params.set('pins_only', 'true');
      if (hours) params.set('hours', hours);
      const r = await fetch(`${endpoint}?${params.toString()}`);
      const data = await r.json();
      render(data.items || []);
    }
    async function loadTopics() {
      lastMode = 'topics';
      const app = document.getElementById('app').value;
      const tag = document.getElementById('tag').value;
      const pins = document.getElementById('pins').checked;
      const hours = document.getElementById('timeframe').value;
      const params = new URLSearchParams({limit: 8, per_group: 5});
      if (app) params.set('app', app);
      if (tag) params.set('tag', tag);
      if (pins) params.set('pins_only', 'true');
      if (hours) params.set('hours', hours);
      const r = await fetch('/topics?' + params.toString());
      const data = await r.json();
      renderTopics(data.groups || []);
    }
    function scheduleAutoRefresh() {
      if (autoTimer) {
        clearInterval(autoTimer);
        autoTimer = null;
      }
      const enabled = document.getElementById('autoRefresh').checked;
      const seconds = parseInt(document.getElementById('autoSeconds').value || '0', 10);
      if (!enabled || !seconds || seconds <= 0) return;
      autoTimer = setInterval(() => {
        if (document.visibilityState === 'hidden') return;
        loadStatus();
        if (lastMode === 'search') return;
        if (lastMode === 'topics') {
          loadTopics();
        } else {
          loadRecent();
        }
      }, seconds * 1000);
    }
    function render(items) {
      const el = document.getElementById('results');
      el.innerHTML = '';
      items.forEach(it => {
        const div = document.createElement('div');
        div.className = 'result';
        const pin = it.pinned ? '<span class="pinned" title="pinned">*</span> ' : '';
        const tags = (it.tags || []).map(t => `<span class="tag">${t}</span>`).join(' ');
        const score = it.score !== undefined ? `<span class="score"> sim=${it.score.toFixed(3)}</span>` : '';
        const lang = it.lang && it.lang !== 'unk' ? ` lang=${it.lang}` : '';
        const notes = (it.notes || []).map(n => `<div class="note">🗒 ${n.note.replace(/</g,'&lt;')} <small>${n.created_at}</small></div>`).join('') || '<div class="note"><small>no notes</small></div>';
        div.innerHTML = `
          <div class="meta">${pin}#${it.id} [${it.source_app || 'unknown'}${lang}] ${it.created_at} ${score}</div>
          <div>${(it.title || '').replace(/</g,'&lt;')}</div>
          <pre style="white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;">${(it.content || '').replace(/</g,'&lt;')}</pre>
          <div>${tags}</div>
          <div>${notes}</div>
          <div>
            <textarea id="note-${it.id}" rows="2" style="width:100%; background:#0f1526; color:#e8ecf1; border:1px solid #223; border-radius:4px;" placeholder="Add note..."></textarea>
            <button onclick="addNote('${it.id}')">Add note</button>
          </div>
          <div>
            <button onclick="copyContent('${it.id}')">Copy</button>
            <button onclick="togglePin('${it.id}', ${it.pinned ? 'false' : 'true'})">${it.pinned ? 'Unpin' : 'Pin'}</button>
          </div>
        `;
        el.appendChild(div);
      });
    }
    function renderTopics(groups) {
      const el = document.getElementById('results');
      el.innerHTML = '';
      groups.forEach(g => {
        const div = document.createElement('div');
        div.className = 'result topic';
        const header = document.createElement('div');
        header.className = 'topic-header';
        header.textContent = `[${g.kind}] ${g.name} (${g.count} clips; latest ${g.latest || ''})`;
        div.appendChild(header);
        const list = document.createElement('div');
        list.className = 'topic-items';
        (g.items || []).forEach(it => {
          const item = document.createElement('div');
          item.className = 'topic-item';
          const pin = it.pinned ? '<span class="pinned" title="pinned">*</span> ' : '';
          const tags = (it.tags || []).map(t => `<span class="tag">${t}</span>`).join(' ');
          const lang = it.lang && it.lang !== 'unk' ? ` lang=${it.lang}` : '';
          const notes = (it.notes || []).map(n => `<div class="note">🗒 ${n.note.replace(/</g,'&lt;')} <small>${n.created_at}</small></div>`).join('') || '<div class="note"><small>no notes</small></div>';
          item.innerHTML = `
            <div class="meta">${pin}#${it.id} [${it.source_app || 'unknown'}${lang}] ${it.created_at}</div>
            <div>${(it.title || '').replace(/</g,'&lt;')}</div>
            <pre style="white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;">${(it.content || '').replace(/</g,'&lt;')}</pre>
            <div>${tags}</div>
            <div>${notes}</div>
            <div>
              <textarea id="note-${it.id}" rows="2" style="width:100%; background:#0f1526; color:#e8ecf1; border:1px solid #223; border-radius:4px;" placeholder="Add note..."></textarea>
              <button onclick="addNote('${it.id}')">Add note</button>
            </div>
            <div>
              <button onclick="copyContent('${it.id}')">Copy</button>
              <button onclick="togglePin('${it.id}', ${it.pinned ? 'false' : 'true'})">${it.pinned ? 'Unpin' : 'Pin'}</button>
            </div>
          `;
          list.appendChild(item);
        });
        div.appendChild(list);
        el.appendChild(div);
      });
    }
    async function copyContent(id) {
      const r = await fetch('/clip?id=' + id);
      if (!r.ok) return;
      const data = await r.json();
      try {
        await navigator.clipboard.writeText(data.content || '');
      } catch (e) {
        const ta = document.createElement('textarea');
        ta.value = data.content || '';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
      }
    }
    async function togglePin(id, state) {
      const r = await fetch('/pin', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id, pinned: !!state})});
      if (r.ok) {
        if (lastMode === 'topics') {
          loadTopics();
        } else if (lastMode === 'search') {
          runSearch();
        } else {
          loadRecent();
        }
      }
    }
    async function addNote(id) {
      const box = document.getElementById('note-' + id);
      if (!box) return;
      const note = box.value.trim();
      if (!note) return;
      const r = await fetch('/notes', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id, note})});
      if (r.ok) {
        box.value = '';
        if (lastMode === 'topics') {
          loadTopics();
        } else if (lastMode === 'search') {
          runSearch();
        } else {
          loadRecent();
        }
      }
    }
    // load app list from recent items
    async function loadApps() {
      const r = await fetch('/recent?limit=100');
      const data = await r.json();
      const apps = Array.from(new Set((data.items||[]).map(i => i.source_app || 'unknown'))).sort();
      const appSel = document.getElementById('app');
      appSel.innerHTML = '<option value="">(app)</option>';
      apps.forEach(a => {
        const opt = document.createElement('option');
        opt.value = a; opt.textContent = a;
        appSel.appendChild(opt);
      });
    }
    function wireShortcuts() {
      const q = document.getElementById('q');
      q.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          runSearch();
        }
      });
      document.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') {
          e.preventDefault();
          q.focus();
          q.select();
        }
      });
      document.getElementById('autoRefresh').addEventListener('change', scheduleAutoRefresh);
      document.getElementById('autoSeconds').addEventListener('change', scheduleAutoRefresh);
      document.addEventListener('visibilitychange', scheduleAutoRefresh);
    }
    loadTags();
    loadApps();
    loadRecent();
    loadStatus();
    wireShortcuts();
    scheduleAutoRefresh();
  </script>
</body>
</html>
"""
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/health":
            self._send(200, {"ok": True})
            return
        if path == "/stats":
            s = stats(conn)
            s["db_size_mb"] = round(s["db_size_bytes"] / (1024 * 1024), 3)
            self._send(200, s)
            return
        if path == "/usage":
            self._send(200, usage_snapshot(conn))
            return
        if path == "/recent":
            limit = int(qs.get("limit", [10])[0])
            app = qs.get("app", [None])[0]
            contains = qs.get("contains", [None])[0]
            tag = qs.get("tag", [None])[0]
            pins_only = qs.get("pins_only", ["false"])[0].lower() in ("1", "true", "yes", "on")
            since_param = qs.get("since", [None])[0]
            until_param = qs.get("until", [None])[0]
            hours_param = qs.get("hours", [None])[0]
            since_iso = parse_iso_dt(since_param) if since_param else None
            if hours_param is not None:
                try:
                    since_iso = iso_hours_ago(float(hours_param))
                except Exception:
                    pass
            until_iso = parse_iso_dt(until_param) if until_param else None
            rows_db, tag_map = filtered_rows(
                conn,
                limit,
                app=app,
                contains=contains,
                tag=tag,
                pins_only=pins_only,
                since_iso=since_iso,
                until_iso=until_iso,
            )
            notes_map = notes_for_clips(conn, [row["id"] for row in rows_db])
            rows = []
            for row in rows_db:
                rows.append(
                    dict(
                        id=row["id"],
                        created_at=row["created_at"],
                        source_app=row["source_app"],
                        window_title=row["window_title"],
                        content=row["content"],
                        pinned=bool(row["pinned"]),
                        title=row["title"],
                        lang=row["lang"],
                        tags=tag_map.get(row["id"], []),
                        notes=notes_map.get(row["id"], []),
                    )
            )
            self._send(200, {"items": rows})
            return
        if path == "/context":
            limit = int(qs.get("limit", [20])[0])
            app = qs.get("app", [None])[0]
            tag = qs.get("tag", [None])[0]
            pins_only = qs.get("pins_only", ["false"])[0].lower() in ("1", "true", "yes", "on")
            hours_param = qs.get("hours", [None])[0]
            try:
                hours = float(hours_param) if hours_param is not None else None
            except Exception:
                hours = None
            rows = context_bundle(conn, app=app, tag=tag, limit=limit, hours=hours, pins_only=pins_only)
            self._send(200, {"items": rows})
            return
        if path == "/topics":
            limit = int(qs.get("limit", [8])[0])
            per_group = int(qs.get("per_group", [5])[0])
            app = qs.get("app", [None])[0]
            tag = qs.get("tag", [None])[0]
            pins_only = qs.get("pins_only", ["false"])[0].lower() in ("1", "true", "yes", "on")
            since_param = qs.get("since", [None])[0]
            until_param = qs.get("until", [None])[0]
            hours_param = qs.get("hours", [None])[0]
            since_iso = parse_iso_dt(since_param) if since_param else None
            if hours_param is not None:
                try:
                    since_iso = iso_hours_ago(float(hours_param))
                except Exception:
                    pass
            until_iso = parse_iso_dt(until_param) if until_param else None
            groups = topic_groups(
                conn,
                limit_groups=limit,
                per_group=per_group,
                app=app,
                tag=tag,
                pins_only=pins_only,
                since_iso=since_iso,
                until_iso=until_iso,
            )
            self._send(200, {"groups": groups})
            return
        if path == "/search":
            q = qs.get("q", [""])[0]
            limit = int(qs.get("limit", [10])[0])
            app = qs.get("app", [None])[0]
            tag = qs.get("tag", [None])[0]
            pins_only = qs.get("pins_only", ["false"])[0].lower() in ("1", "true", "yes", "on")
            since_param = qs.get("since", [None])[0]
            until_param = qs.get("until", [None])[0]
            hours_param = qs.get("hours", [None])[0]
            since_iso = parse_iso_dt(since_param) if since_param else None
            if hours_param is not None:
                try:
                    since_iso = iso_hours_ago(float(hours_param))
                except Exception:
                    pass
            until_iso = parse_iso_dt(until_param) if until_param else None
            clauses = ["clips_fts MATCH ?"]
            params = [q]
            if app:
                clauses.append("LOWER(c.source_app) = LOWER(?)")
                params.append(app)
            if tag:
                clauses.append(
                    "c.id IN (SELECT clip_id FROM clip_tags ct JOIN tags t ON t.id = ct.tag_id WHERE LOWER(t.name) = LOWER(?))"
                )
                params.append(tag)
            if pins_only:
                clauses.append("c.pinned = 1")
            if since_iso:
                clauses.append("datetime(c.created_at) >= datetime(?)")
                params.append(since_iso)
            if until_iso:
                clauses.append("datetime(c.created_at) <= datetime(?)")
                params.append(until_iso)
            where = " AND ".join(clauses)
            sql = f"""
                SELECT c.id, c.created_at, c.source_app, c.window_title, c.content, c.pinned, c.title, c.lang
                FROM clips_fts f
                JOIN clips c ON c.id = f.rowid
                WHERE {where}
                ORDER BY c.created_at DESC
                LIMIT ?;
            """
            params.append(limit)
            cur = conn.execute(sql, params)
            rows_db = cur.fetchall()
            tag_map = tags_for_clips(conn, [row["id"] for row in rows_db])
            notes_map = notes_for_clips(conn, [row["id"] for row in rows_db])
            rows = []
            for row in rows_db:
                rows.append(
                    dict(
                        id=row["id"],
                        created_at=row["created_at"],
                        source_app=row["source_app"],
                        window_title=row["window_title"],
                        content=row["content"],
                        pinned=bool(row["pinned"]),
                        title=row["title"],
                        lang=row["lang"],
                        tags=tag_map.get(row["id"], []),
                        notes=notes_map.get(row["id"], []),
                    )
                )
            self._send(200, {"items": rows})
            return
        if path == "/blocklist":
            apps = sorted(get_blocklist(conn))
            self._send(200, {"blocklist": apps})
            return
        if path == "/tags":
            self._send(200, {"tags": list_tags(conn)})
            return
        if path == "/config":
            mb = get_max_bytes(conn, None)
            license_info = license_snapshot(conn)
            self._send(
                200,
                {
                    "max_bytes": mb,
                    "paused": is_paused(conn),
                    "allow_secrets": get_allow_secrets(conn, None),
                    "notify": get_notify(conn, None),
                    "max_db_mb": get_max_db_mb(conn, None),
                    "pro_enabled": license_info["pro_enabled"],
                    "license_type": license_info["license_type"],
                    "license_status": license_info["license_status"],
                    "has_license_key": license_info["has_license_key"],
                    "upgrade_url": license_info["upgrade_url"],
                    "embedder": get_embedder(conn, None),
                    "cap_by_app": get_cap_map(conn, "cap_by_app"),
                    "cap_by_tag": get_cap_map(conn, "cap_by_tag"),
                    "evict_mode": get_evict_mode(conn),
                    "gumroad_permalink": get_setting(conn, "gumroad_permalink", ""),
                    "gumroad_webhook_secret": "***" if gumroad_webhook_secret(conn) else "",
                    "sync_target": get_setting(conn, "sync_target", ""),
                    "sync_interval": get_sync_interval(conn),
                    "ai_recall_cmd": get_setting(conn, "ai_recall_cmd", ""),
                    "ai_fill_cmd": get_setting(conn, "ai_fill_cmd", ""),
                    "helper_rewrite_cmd": get_setting(conn, "helper_rewrite_cmd", ""),
                    "helper_shorten_cmd": get_setting(conn, "helper_shorten_cmd", ""),
                    "helper_extract_cmd": get_setting(conn, "helper_extract_cmd", ""),
                },
            )
            return
        if path == "/settings":
            self._send(200, settings_snapshot(conn))
            return
        if path == "/status":
            self._send(200, status_snapshot(conn))
            return
        if path == "/federate_export":
            limit = int(qs.get("limit", [200])[0])
            app = qs.get("app", [None])[0]
            tag = qs.get("tag", [None])[0]
            pins_only = qs.get("pins_only", ["false"])[0].lower() in ("1", "true", "yes", "on")
            hours_param = qs.get("hours", [None])[0]
            try:
                hours = float(hours_param) if hours_param is not None else None
            except Exception:
                hours = None
            since_iso = iso_hours_ago(hours) if hours is not None else None
            items = export_items(conn, limit, app=app, tag=tag, since_iso=since_iso, pins_only=pins_only)
            self._send(200, {"items": items})
            return
        if path == "/clip":
            try:
                cid = int(qs.get("id", [0])[0])
            except ValueError:
                self._send(400, {"error": "invalid id"})
                return
            cur = conn.execute(
                "SELECT id, created_at, source_app, window_title, content, pinned, title, file_path, lang FROM clips WHERE id = ?",
                (cid,),
            )
            row = cur.fetchone()
            if not row:
                self._send(404, {"error": "not found"})
                return
            tags = tags_for_clip(conn, row["id"])
            note_map = notes_for_clips(conn, [row["id"]])
            self._send(
                200,
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "source_app": row["source_app"],
                    "window_title": row["window_title"],
                    "content": row["content"],
                    "pinned": bool(row["pinned"]),
                    "title": row["title"],
                    "file_path": row["file_path"],
                    "lang": row["lang"],
                    "tags": tags,
                    "notes": note_map.get(row["id"], []),
                },
            )
            return
        if path == "/recap":
            minutes = int(qs.get("minutes", [60])[0])
            limit = int(qs.get("limit", [200])[0])
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
            cur = conn.execute(
                """
                SELECT id, created_at, source_app, window_title, content, title, lang
                FROM clips
                WHERE datetime(created_at) >= datetime(?)
                ORDER BY created_at DESC
                LIMIT ?;
                """,
                (cutoff.isoformat(), limit),
            )
            rows = [dict(row) for row in cur.fetchall()]
            self._send(200, {"items": rows})
            return
        if path == "/export_md":
            hours_param = qs.get("hours", [None])[0]
            limit = int(qs.get("limit", [200])[0])
            since_iso = iso_hours_ago(float(hours_param)) if hours_param is not None else None
            md, count = build_markdown_outline(conn, since_iso, limit=limit)
            body = md.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Clip-Count", str(count))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/semantic_search":
            q = qs.get("q", [""])[0]
            limit = int(qs.get("limit", [10])[0])
            app = qs.get("app", [None])[0]
            tag = qs.get("tag", [None])[0]
            pool = int(qs.get("pool", [2000])[0])
            requested_embedder = get_embedder(conn, qs.get("embedder", [None])[0])
            if requested_embedder == "e5-small" and not is_pro_enabled(conn):
                self._send(
                    402,
                    {
                        "error": "e5-small semantic search requires Pro",
                        "pro_enabled": False,
                        "upgrade_url": get_upgrade_url(conn),
                    },
                )
                return
            embedder_kind, _embedder_warning = resolve_embedder_for_use(conn, qs.get("embedder", [None])[0])
            qvec, model_used = embed_from_kind(embedder_kind, q)
            pins_only = qs.get("pins_only", ["false"])[0].lower() in ("1", "true", "yes", "on")
            since_param = qs.get("since", [None])[0]
            until_param = qs.get("until", [None])[0]
            hours_param = qs.get("hours", [None])[0]
            since_iso = parse_iso_dt(since_param) if since_param else None
            if hours_param is not None:
                try:
                    since_iso = iso_hours_ago(float(hours_param))
                except Exception:
                    pass
            until_iso = parse_iso_dt(until_param) if until_param else None
            rows = fetch_semantic_candidates(conn, app, tag, pool, model_used, since_iso=since_iso, until_iso=until_iso, pins_only=pins_only)
            ids, vecs = build_ann_index(rows)
            sims = knn(qvec, ids, vecs, limit)
            row_map = {row["id"]: row for row in rows}
            tag_map = tags_for_clips(conn, ids)
            notes_map = notes_for_clips(conn, ids)
            items = []
            for sim, cid in sims:
                row = row_map.get(cid)
                if not row:
                    continue
                items.append(
                    dict(
                        id=row["id"],
                        created_at=row["created_at"],
                        source_app=row["source_app"],
                        window_title=row["window_title"],
                        content=row["content"],
                        pinned=bool(row["pinned"]),
                        title=row["title"],
                        lang=row["lang"],
                        tags=tag_map.get(row["id"], []),
                        notes=notes_map.get(row["id"], []),
                        score=sim,
                    )
                )
            self._send(200, {"items": items})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path
        conn = self.conn
        init_db(conn)
        if path == "/webhooks/gumroad":
            body = self._read_body()
            secret = gumroad_webhook_secret(conn)
            if not secret:
                self._send(503, {"error": "gumroad webhook secret is not configured"})
                return
            signature = self.headers.get("X-Gumroad-Signature", "")
            if not verify_gumroad_signature(body, signature, secret):
                self._send(401, {"error": "invalid signature"})
                return
            payload = parse_gumroad_payload(body, self.headers.get("Content-Type", ""))
            ok, msg, valid = apply_gumroad_license_event(conn, payload)
            if not ok:
                status = 400 if msg == "missing license_key" else 401
                self._send(status, {"error": msg, "license_valid": valid})
                return
            self._send(
                200,
                {
                    "ok": True,
                    "stored": True,
                    "pro_enabled": is_pro_enabled(conn),
                    "license_valid": True if valid is None else valid,
                },
            )
            return
        if path == "/pin":
            data = self._parse_json()
            try:
                cid = int(data.get("id", 0))
            except Exception:
                self._send(400, {"error": "invalid id"})
                return
            state = data.get("pinned")
            if state is None:
                cur = conn.execute("UPDATE clips SET pinned = 1 - pinned WHERE id = ?", (cid,))
            else:
                cur = conn.execute("UPDATE clips SET pinned = ? WHERE id = ?", (1 if state else 0, cid))
            conn.commit()
            if cur.rowcount:
                cur2 = conn.execute("SELECT pinned FROM clips WHERE id = ?", (cid,))
                row = cur2.fetchone()
                self._send(200, {"ok": True, "pinned": bool(row['pinned'])})
            else:
                self._send(404, {"error": "not found"})
            return
        if path == "/pause":
            set_paused(conn, True)
            self._send(200, {"paused": True})
            return
        if path == "/resume":
            set_paused(conn, False)
            self._send(200, {"paused": False})
            return
        if path == "/blocklist":
            data = self._parse_json()
            app = data.get("add") or data.get("app")
            if app:
                add_blocked_app(conn, str(app))
                self._send(200, {"ok": True, "blocklist": sorted(get_blocklist(conn))})
            else:
                self._send(400, {"error": "missing app"})
            return
        if path == "/config":
            data = self._parse_json()
            updated = {}
            if "max_bytes" in data:
                try:
                    intval = int(data["max_bytes"])
                    set_setting(conn, "max_bytes", str(intval))
                    updated["max_bytes"] = intval
                except ValueError:
                    self._send(400, {"error": "invalid max_bytes"})
                    return
            if "allow_secrets" in data:
                allow = bool(data["allow_secrets"])
                set_allow_secrets(conn, allow)
                updated["allow_secrets"] = allow
            if "notify" in data:
                notify_val = bool(data["notify"])
                set_notify(conn, notify_val)
                updated["notify"] = notify_val
            if "max_db_mb" in data:
                try:
                    intval = int(data["max_db_mb"])
                except ValueError:
                    self._send(400, {"error": "invalid max_db_mb"})
                    return
                set_setting(conn, "max_db_mb", str(intval))
                updated["max_db_mb"] = intval
            if "pro_enabled" in data:
                parsed = data["pro_enabled"] if isinstance(data["pro_enabled"], bool) else parse_bool_value(str(data["pro_enabled"]))
                if parsed is None:
                    self._send(400, {"error": "invalid pro_enabled"})
                    return
                set_pro_enabled(conn, bool(parsed))
                updated["pro_enabled"] = is_pro_enabled(conn)
                updated["license_type"] = license_snapshot(conn)["license_type"]
            if "license_key" in data:
                set_license_key(conn, str(data["license_key"]))
                updated["license_key"] = mask_secret(get_license_key(conn))
                updated["pro_enabled"] = is_pro_enabled(conn)
                updated["license_type"] = license_snapshot(conn)["license_type"]
            if "gumroad_license_key" in data:
                set_license_key(conn, str(data["gumroad_license_key"]))
                updated["gumroad_license_key"] = mask_secret(get_license_key(conn))
                updated["pro_enabled"] = is_pro_enabled(conn)
                updated["license_type"] = license_snapshot(conn)["license_type"]
            if "gumroad_webhook_secret" in data:
                set_setting(conn, "gumroad_webhook_secret", str(data["gumroad_webhook_secret"]))
                updated["gumroad_webhook_secret"] = "***" if gumroad_webhook_secret(conn) else ""
            if "gumroad_permalink" in data:
                set_setting(conn, "gumroad_permalink", str(data["gumroad_permalink"]))
                updated["gumroad_permalink"] = get_setting(conn, "gumroad_permalink", "")
            if "upgrade_url" in data:
                set_setting(conn, "upgrade_url", str(data["upgrade_url"]))
                updated["upgrade_url"] = get_upgrade_url(conn)
            if "embedder" in data:
                requested_embedder = get_embedder(conn, str(data["embedder"]))
                if requested_embedder == "e5-small" and not is_pro_enabled(conn):
                    self._send(
                        402,
                        {
                            "error": "e5-small embeddings require Pro",
                            "pro_enabled": False,
                            "upgrade_url": get_upgrade_url(conn),
                        },
                    )
                    return
                set_embedder(conn, str(data["embedder"]))
                updated["embedder"] = get_embedder(conn, None)
            if "cap_by_app" in data:
                if isinstance(data["cap_by_app"], dict):
                    set_cap_map(conn, "cap_by_app", data["cap_by_app"])
                    updated["cap_by_app"] = get_cap_map(conn, "cap_by_app")
                else:
                    self._send(400, {"error": "cap_by_app must be object"})
                    return
            if "cap_by_tag" in data:
                if isinstance(data["cap_by_tag"], dict):
                    set_cap_map(conn, "cap_by_tag", data["cap_by_tag"])
                    updated["cap_by_tag"] = get_cap_map(conn, "cap_by_tag")
                else:
                    self._send(400, {"error": "cap_by_tag must be object"})
                    return
            if "evict_mode" in data:
                set_evict_mode(conn, str(data["evict_mode"]))
                updated["evict_mode"] = get_evict_mode(conn)
            for helper_key in ("helper_rewrite_cmd", "helper_shorten_cmd", "helper_extract_cmd"):
                if helper_key in data:
                    set_setting(conn, helper_key, str(data[helper_key]))
                    updated[helper_key] = get_setting(conn, helper_key, "")
            if "sync_target" in data:
                set_setting(conn, "sync_target", str(data["sync_target"]))
                updated["sync_target"] = get_setting(conn, "sync_target", "")
            if "sync_interval" in data:
                try:
                    floatval = max(1.0, float(data["sync_interval"]))
                except ValueError:
                    self._send(400, {"error": "invalid sync_interval"})
                    return
                set_setting(conn, "sync_interval", str(floatval))
                updated["sync_interval"] = get_sync_interval(conn)
            if "ai_recall_cmd" in data:
                set_setting(conn, "ai_recall_cmd", str(data["ai_recall_cmd"]))
                updated["ai_recall_cmd"] = get_setting(conn, "ai_recall_cmd", "")
            if "ai_fill_cmd" in data:
                set_setting(conn, "ai_fill_cmd", str(data["ai_fill_cmd"]))
                updated["ai_fill_cmd"] = get_setting(conn, "ai_fill_cmd", "")
            if updated:
                self._send(200, {"ok": True, **updated})
            else:
                self._send(400, {"error": "missing payload"})
            return
        if path == "/ingest_url":
            data = self._parse_json()
            url = (data.get("url") or "").strip()
            title = (data.get("title") or "").strip()
            selection = (data.get("selection") or "").strip()
            if not url:
                self._send(400, {"error": "missing url"})
                return
            content_parts = []
            if title:
                content_parts.append(title)
            content_parts.append(url)
            if selection:
                content_parts.append("")
                content_parts.append(selection)
            body = "\n".join(content_parts)
            if not body.strip():
                self._send(400, {"error": "empty payload"})
                return
            max_bytes = get_max_bytes(conn, None)
            allow = get_allow_secrets(conn, None)
            if len(body.encode("utf-8", errors="ignore")) > max_bytes:
                self._send(400, {"error": f"too large (> {max_bytes} bytes)"})
                return
            if not allow and looks_like_secret(body):
                self._send(400, {"error": "looks like a secret; enable allow_secrets to force save"})
                return
            digest = hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest()
            existing_id = get_clip_id_by_hash(conn, digest)
            if existing_id:
                insert_event(conn, existing_id)
                self._send(200, {"ok": True, "id": existing_id, "existing": True})
                return
            inserted_id = insert_clip(
                conn,
                body,
                app="bookmarklet",
                window=title or url,
                digest=digest,
                title=title or url,
                file_path=url,
            )
            if inserted_id:
                insert_event(conn, inserted_id)
                self._send(200, {"ok": True, "id": inserted_id, "existing": False})
            else:
                self._send(500, {"error": "failed to insert"})
            return
        if path == "/dropper":
            data = self._parse_json()
            url = (data.get("url") or "").strip()
            title = (data.get("title") or "").strip()
            selection = (data.get("selection") or "").strip()
            html = (data.get("html") or "").strip()
            app = (data.get("app") or "browser-dropper").strip() or "browser-dropper"
            body_parts = []
            if title:
                body_parts.append(title)
            if url:
                body_parts.append(url)
            if selection:
                body_parts.append("")
                body_parts.append(selection)
            if html:
                body_parts.append("")
                body_parts.append(html)
            body = "\n".join(body_parts).strip()
            if not body:
                self._send(400, {"error": "empty payload"})
                return
            max_bytes = get_max_bytes(conn, None)
            allow = get_allow_secrets(conn, None)
            if len(body.encode("utf-8", errors="ignore")) > max_bytes:
                self._send(400, {"error": f"too large (> {max_bytes} bytes)"})
                return
            if not allow and looks_like_secret(body):
                self._send(400, {"error": "looks like a secret; enable allow_secrets to force save"})
                return
            digest = hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest()
            existing_id = get_clip_id_by_hash(conn, digest)
            if existing_id:
                insert_event(conn, existing_id)
                self._send(200, {"ok": True, "id": existing_id, "existing": True})
                return
            inserted_id = insert_clip(
                conn,
                body,
                app=app,
                window=title or url,
                digest=digest,
                title=title or url,
                file_path=url or None,
            )
            if inserted_id:
                insert_event(conn, inserted_id)
                self._send(200, {"ok": True, "id": inserted_id, "existing": False})
            else:
                self._send(500, {"error": "failed to insert"})
            return
        if path == "/federate_import":
            data = self._parse_json()
            items = data.get("items")
            if not isinstance(items, list):
                self._send(400, {"error": "items must be a list"})
                return
            res = import_clips(conn, items)
            self._send(200, {"ok": True, **res})
            return
        if path == "/notes":
            data = self._parse_json()
            try:
                cid = int(data.get("id", 0))
            except Exception:
                self._send(400, {"error": "invalid id"})
                return
            note = (data.get("note") or "").strip()
            if not note:
                self._send(400, {"error": "empty note"})
                return
            ok = add_note(conn, cid, note)
            if not ok:
                self._send(400, {"error": "failed to add note"})
                return
            notes_map = notes_for_clips(conn, [cid])
            self._send(200, {"ok": True, "notes": notes_map.get(cid, [])})
            return
        if path == "/helper":
            data = self._parse_json()
            kind = str(data.get("kind", "")).lower()
            if kind not in ("rewrite", "shorten", "extract"):
                self._send(400, {"error": "kind must be rewrite|shorten|extract"})
                return
            target_id = data.get("id")
            if target_id is not None:
                try:
                    target_id = int(target_id)
                except Exception:
                    self._send(400, {"error": "invalid id"})
                    return
            else:
                latest = latest_clip(conn)
                if not latest:
                    self._send(400, {"error": "no clips found"})
                    return
                target_id = latest["id"]
            timeout = data.get("timeout", 8.0)
            try:
                timeout = float(timeout)
            except Exception:
                timeout = 8.0
            ok, msg, new_id, out = run_user_helper_on_clip(conn, kind, target_id, timeout=timeout)
            if ok:
                self._send(200, {"ok": True, "id": new_id, "message": msg, "output": out})
            else:
                self._send(400, {"error": msg})
            return
        if path == "/ai":
            data = self._parse_json()
            kind = str(data.get("kind", "")).lower()
            if kind not in ("recall", "fill"):
                self._send(400, {"error": "kind must be recall|fill"})
                return
            hours = data.get("hours")
            try:
                hours = float(hours) if hours is not None else None
            except Exception:
                hours = None
            try:
                limit = int(data.get("limit", 50))
            except Exception:
                limit = 50
            try:
                timeout = float(data.get("timeout", 12.0))
            except Exception:
                timeout = 12.0
            save = bool(data.get("save"))
            setting_key = "ai_recall_cmd" if kind == "recall" else "ai_fill_cmd"
            tag_label = kind
            ok, msg, new_id, out = run_ai_helper(conn, setting_key, hours, limit, timeout, save=save, tag_label=tag_label)
            if ok:
                self._send(200, {"ok": True, "id": new_id, "message": msg, "output": out})
            else:
                self._send(400, {"error": msg})
            return
        if path == "/purge":
            data = self._parse_json()
            app = data.get("app")
            tag = data.get("tag")
            older = data.get("older_than_days")
            keep_last = data.get("keep_last")
            all_flag = bool(data.get("all"))
            # Reuse logic directly
            deleted = 0
            clauses = []
            params = []
            if app:
                clauses.append("LOWER(source_app) = LOWER(?)")
                params.append(app)
            if tag:
                clauses.append(
                    "id IN (SELECT clip_id FROM clip_tags ct JOIN tags t ON t.id = ct.tag_id WHERE LOWER(t.name) = LOWER(?))"
                )
                params.append(tag)
            if older is not None:
                try:
                    days = int(older)
                except ValueError:
                    self._send(400, {"error": "invalid older_than_days"})
                    return
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                clauses_cutoff = clauses + ["datetime(created_at) < datetime(?)"]
                params_cutoff = params + [cutoff.isoformat()]
                where_cutoff = "WHERE " + " AND ".join(clauses_cutoff)
                cur = conn.execute(f"DELETE FROM clips {where_cutoff}", params_cutoff)
                deleted += cur.rowcount
            if keep_last is not None:
                try:
                    keep_last_int = int(keep_last)
                except ValueError:
                    self._send(400, {"error": "invalid keep_last"})
                    return
                where_keep = "WHERE " + " AND ".join(clauses) if clauses else ""
                sql = f"""
                    DELETE FROM clips
                    WHERE id NOT IN (
                        SELECT id FROM clips
                        {where_keep}
                        ORDER BY created_at DESC
                        LIMIT ?
                    )
                    {f'AND {where_keep[6:]}' if where_keep else ''}
                """
                params_keep = params + [keep_last_int] + params
                cur = conn.execute(sql, params_keep)
                deleted += cur.rowcount
            if all_flag:
                cur = conn.execute("DELETE FROM clips")
                deleted += cur.rowcount
            conn.commit()
            self._send(200, {"purged": deleted})
            return
        self._send(404, {"error": "not found"})

    def do_DELETE(self) -> None:
        path = self.path
        conn = self.conn
        init_db(conn)
        if path.startswith("/blocklist"):
            data = self._parse_json()
            app = data.get("remove") or data.get("app")
            if app:
                removed = remove_blocked_app(conn, str(app))
                self._send(200, {"ok": removed, "blocklist": sorted(get_blocklist(conn))})
            else:
                self._send(400, {"error": "missing app"})
            return
        self._send(404, {"error": "not found"})


def cmd_serve(args: argparse.Namespace) -> None:
    conn = connect_db()
    init_db(conn)

    def handler(*h_args, **h_kwargs):
        return ApiHandler(*h_args, conn=conn, **h_kwargs)

    class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    base_port = args.port
    server = None
    bound_port = None
    for attempt in range(3):
        try_port = base_port + attempt
        try:
            server = ThreadingHTTPServer((args.host, try_port), handler)
            bound_port = try_port
            break
        except OSError as e:
            say(FATHER, f"failed to bind {args.host}:{try_port}: {e}")
            continue
    if server is None:
        say(FATHER, "unable to bind any port")
        return
    say(FATHER, f"serving API on http://{args.host}:{bound_port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        say(FATHER, "stopping server")
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="my--father-mother: local clipboard memory")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="initialize the database")
    p_init.set_defaults(func=cmd_init)

    p_watch = sub.add_parser("watch", help="start clipboard watcher")
    p_watch.add_argument("--interval", type=float, default=1.0, help="poll interval in seconds")
    p_watch.add_argument("--cap", type=int, default=2000, help="max clips to retain (0 = unlimited)")
    p_watch.add_argument("--max-bytes", type=int, default=None, help="max bytes per clip (default from settings or 16384)")
    p_watch.add_argument("--allow-secrets", action="store_true", help="capture even if text looks like a secret")
    p_watch.add_argument("--redact", action="store_true", help="redact secrets instead of skipping when allow_secrets is false")
    p_watch.add_argument("--embedder", choices=["hash", "e5-small"], help="embedding model for new captures (default from config)")
    g_notify = p_watch.add_mutually_exclusive_group()
    g_notify.add_argument("--notify", action="store_true", help="show a macOS notification when clips are saved/skipped")
    g_notify.add_argument("--no-notify", action="store_true", help="disable notifications even if enabled in config")
    p_watch.set_defaults(func=cmd_watch)

    p_recent = sub.add_parser("recent", help="show recent clips")
    p_recent.add_argument("--limit", type=int, default=10)
    p_recent.add_argument("--app", help="filter by source app (case-insensitive)")
    p_recent.add_argument("--contains", help="filter by substring")
    p_recent.add_argument("--tag", help="filter by tag")
    p_recent.add_argument("--since", help="ISO timestamp lower bound")
    p_recent.add_argument("--since-hours", type=float, help="look back this many hours")
    p_recent.add_argument("--until", help="ISO timestamp upper bound")
    p_recent.add_argument("--pins-only", action="store_true", help="only pinned clips")
    p_recent.add_argument("--json", action="store_true", help="emit JSON payload")
    p_recent.set_defaults(func=cmd_recent)

    p_search = sub.add_parser("search", help="search clips (FTS)")
    p_search.add_argument("query", help="FTS query text")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--app", help="filter by source app (case-insensitive)")
    p_search.add_argument("--tag", help="filter by tag")
    p_search.add_argument("--since", help="ISO timestamp lower bound")
    p_search.add_argument("--since-hours", type=float, help="look back this many hours")
    p_search.add_argument("--until", help="ISO timestamp upper bound")
    p_search.add_argument("--pins-only", action="store_true", help="only pinned clips")
    p_search.set_defaults(func=cmd_search)

    p_ssearch = sub.add_parser("semantic-search", help="semantic search using hash or e5-small embeddings")
    p_ssearch.add_argument("query", help="query text")
    p_ssearch.add_argument("--limit", type=int, default=10)
    p_ssearch.add_argument("--pool", type=int, default=2000, help="pool size to score (higher = more accurate)")
    p_ssearch.add_argument("--app", help="filter by source app (case-insensitive)")
    p_ssearch.add_argument("--tag", help="filter by tag")
    p_ssearch.add_argument("--embedder", choices=["hash", "e5-small"], help="embedding model to use (default from config)")
    p_ssearch.add_argument("--since", help="ISO timestamp lower bound")
    p_ssearch.add_argument("--since-hours", type=float, help="look back this many hours")
    p_ssearch.add_argument("--until", help="ISO timestamp upper bound")
    p_ssearch.add_argument("--pins-only", action="store_true", help="only pinned clips")
    p_ssearch.set_defaults(func=cmd_semantic_search)

    p_delete = sub.add_parser("delete", help="delete a clip by id")
    p_delete.add_argument("--id", type=int, required=True)
    p_delete.set_defaults(func=cmd_delete)

    p_stats = sub.add_parser("stats", help="show clip count and db size")
    p_stats.set_defaults(func=cmd_stats)

    p_status = sub.add_parser("status", help="runtime status (paused/notify/secrets/config)")
    p_status.add_argument("--json", action="store_true", help="emit JSON payload")
    p_status.set_defaults(func=cmd_status)

    p_mcp = sub.add_parser("mcp-urls", help="print MCP server URLs (SSE + MCP)")
    p_mcp.set_defaults(func=cmd_mcp_urls)

    p_personas = sub.add_parser("personas", help="show persona role map")
    p_personas.set_defaults(func=cmd_personas)

    p_settings = sub.add_parser("settings", help="show or update settings parity")
    group_settings = p_settings.add_mutually_exclusive_group()
    group_settings.add_argument("--list-keys", action="store_true", help="list settings keys")
    group_settings.add_argument("--get", help="get a settings key")
    group_settings.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), help="set a settings key")
    p_settings.add_argument("--json", action="store_true", help="print JSON output")
    p_settings.set_defaults(func=cmd_settings)

    p_copilot = sub.add_parser("copilot", help="manage copilot settings and chats")
    p_copilot.add_argument("--set-model", help="set default copilot model")
    p_copilot.add_argument("--set-accent", help="set copilot accent color")
    g_copilot_ltm = p_copilot.add_mutually_exclusive_group()
    g_copilot_ltm.add_argument("--use-ltm", action="store_true", help="enable LTM context by default")
    g_copilot_ltm.add_argument("--no-use-ltm", action="store_true", help="disable LTM context by default")
    p_copilot.add_argument("--add", action="store_true", help="add a copilot chat from text/path/stdin")
    p_copilot.add_argument("--text", help="chat content")
    p_copilot.add_argument("--path", help="path to chat content")
    p_copilot.add_argument("--stdin", action="store_true", help="read chat content from stdin")
    p_copilot.add_argument("--title", help="chat title")
    p_copilot.add_argument("--chat-model", help="override model for this chat")
    p_copilot.add_argument("--list", action="store_true", help="list recent copilot chats")
    p_copilot.add_argument("--limit", type=int, default=10, help="max chats to list")
    p_copilot.add_argument("--clear", action="store_true", help="delete all copilot chats")
    p_copilot.add_argument("--yes", action="store_true", help="confirm destructive actions")
    p_copilot.add_argument("--status", action="store_true", help="show copilot status")
    p_copilot.set_defaults(func=cmd_copilot)

    p_ml = sub.add_parser("ml", help="manage machine learning/LTM settings")
    p_ml.add_argument("--context-level", help="set auto-context level (off/low/medium/high)")
    p_ml.add_argument("--processing-mode", help="set processing mode (local/cloud/blended)")
    g_ltm = p_ml.add_mutually_exclusive_group()
    g_ltm.add_argument("--ltm-on", action="store_true", help="enable LTM engine")
    g_ltm.add_argument("--ltm-off", action="store_true", help="disable LTM engine")
    p_ml.add_argument("--permissions", help="set LTM permissions note")
    p_ml.add_argument("--clear-older-than-days", type=int, help="clear clips older than N days")
    p_ml.add_argument("--clear-since", help="clear clips since ISO timestamp")
    p_ml.add_argument("--clear-until", help="clear clips until ISO timestamp")
    p_ml.add_argument("--clear-all", action="store_true", help="clear all clips")
    p_ml.add_argument("--optimize", action="store_true", help="optimize memory usage")
    p_ml.add_argument("--yes", action="store_true", help="confirm destructive actions")
    p_ml.add_argument("--status", action="store_true", help="show ML/LTM status")
    p_ml.set_defaults(func=cmd_ml)

    p_about = sub.add_parser("about", help="show app/about info")
    p_about.add_argument("--json", action="store_true", help="print JSON output")
    p_about.set_defaults(func=cmd_about)

    p_pause = sub.add_parser("pause", help="pause/resume/toggle capture")
    grp = p_pause.add_mutually_exclusive_group()
    grp.add_argument("--on", action="store_true", help="pause capture")
    grp.add_argument("--off", action="store_true", help="resume capture")
    grp.add_argument("--toggle", action="store_true", help="toggle pause state")
    p_pause.set_defaults(func=cmd_pause)

    p_block = sub.add_parser("blocklist", help="manage blocked apps for capture")
    p_block.add_argument("--add", help="add app name to blocklist")
    p_block.add_argument("--remove", help="remove app name from blocklist")
    p_block.add_argument("--list", action="store_true", help="list blocklisted apps")
    p_block.set_defaults(func=cmd_blocklist)

    p_show = sub.add_parser("show", help="show full content for a clip by id")
    p_show.add_argument("--id", type=int, required=True)
    p_show.set_defaults(func=cmd_show)

    p_export = sub.add_parser("export", help="export clips to JSON (stdout or file)")
    p_export.add_argument("--limit", type=int, default=1000, help="number of clips to export")
    p_export.add_argument("--path", help="path to write JSON (default stdout)")
    p_export.add_argument("--app", help="filter by source app (case-insensitive)")
    p_export.add_argument("--tag", help="filter by tag")
    p_export.set_defaults(func=cmd_export)

    p_export_md = sub.add_parser("export-md", help="export recent clips as markdown outline")
    p_export_md.add_argument("--hours", type=float, help="look back this many hours (optional)")
    p_export_md.add_argument("--limit", type=int, default=200, help="max clips to include")
    p_export_md.add_argument("--path", help="write to file (default stdout)")
    p_export_md.set_defaults(func=cmd_export_md)

    p_config = sub.add_parser("config", help="get/set config (max_bytes)")
    group_cfg = p_config.add_mutually_exclusive_group(required=True)
    group_cfg.add_argument("--get", help="get a key (max_bytes, allow_secrets, max_db_mb, notify, pro_enabled, license_key, gumroad_* settings, embedder, cap_by_app, cap_by_tag, evict_mode, allow_pdf, allow_images, auto_summary_cmd, auto_tag_cmd, helper_*_cmd, sync_target, sync_interval, backup_* settings, ai_recall_cmd, ai_fill_cmd)")
    group_cfg.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), help="set a key (max_bytes, allow_secrets, max_db_mb, notify, pro_enabled, license_key, gumroad_* settings, embedder, cap_by_app, cap_by_tag, evict_mode, allow_pdf, allow_images, auto_summary_cmd, auto_tag_cmd, helper_*_cmd, sync_target, sync_interval, backup_* settings, ai_recall_cmd, ai_fill_cmd)")
    p_config.set_defaults(func=cmd_config)

    p_purge = sub.add_parser("purge", help="purge clips by age/app/keep-last/all")
    p_purge.add_argument("--older-than-days", type=int, help="delete clips older than N days (optional)")
    p_purge.add_argument("--keep-last", type=int, help="keep last N clips (delete the rest)")
    p_purge.add_argument("--app", help="only purge for a specific app")
    p_purge.add_argument("--tag", help="only purge for a specific tag")
    p_purge.add_argument("--all", action="store_true", help="delete all clips")
    p_purge.set_defaults(func=cmd_purge)

    p_tags = sub.add_parser("tags", help="manage tags for a clip or list all")
    p_tags.add_argument("--id", type=int, help="clip id")
    p_tags.add_argument("--add", help="add tag to clip")
    p_tags.add_argument("--remove", help="remove tag from clip")
    p_tags.add_argument("--clear", action="store_true", help="clear tags from clip")
    p_tags.add_argument("--list-all", action="store_true", help="list all tags")
    p_tags.set_defaults(func=cmd_tags)

    p_pin = sub.add_parser("pin", help="pin/unpin/toggle a clip")
    p_pin.add_argument("--id", type=int, required=True, help="clip id")
    gpin = p_pin.add_mutually_exclusive_group(required=True)
    gpin.add_argument("--on", action="store_true", help="pin clip")
    gpin.add_argument("--off", action="store_true", help="unpin clip")
    gpin.add_argument("--toggle", action="store_true", help="toggle pin")
    p_pin.set_defaults(func=cmd_pin)

    p_copy = sub.add_parser("copy", help="copy clip content to clipboard")
    p_copy.add_argument("--id", type=int, required=True)
    p_copy.set_defaults(func=cmd_copy)

    p_backup = sub.add_parser("backup", help="backup the database to a path")
    p_backup.add_argument("--path", required=True, help="destination path for backup file")
    p_backup.set_defaults(func=cmd_backup)

    p_restore = sub.add_parser("restore", help="restore the database from a path")
    p_restore.add_argument("--path", required=True, help="source path of backup file")
    p_restore.set_defaults(func=cmd_restore)

    p_sync = sub.add_parser("sync", help="push/pull DB to/from a target path (e.g. iCloud/drive)")
    p_sync.add_argument("--mode", choices=["push", "pull"], default="push")
    p_sync.add_argument("--target", help="override sync_target config")
    p_sync.set_defaults(func=cmd_sync)

    p_cloud_backup = sub.add_parser(
        "cloud-backup",
        help="encrypt and upload a DB snapshot to an S3-compatible bucket (config backup_bucket)",
    )
    p_cloud_backup.add_argument(
        "--loop", action="store_true", help="run continuously, uploading every --interval seconds"
    )
    p_cloud_backup.add_argument(
        "--interval", type=int, default=DEFAULT_BACKUP_INTERVAL,
        help="seconds between uploads in --loop mode (default 3600 = hourly)",
    )
    p_cloud_backup.set_defaults(func=cmd_cloud_backup)

    p_hist = sub.add_parser("history", help="show capture history for a clip")
    p_hist.add_argument("--id", type=int, required=True)
    p_hist.add_argument("--limit", type=int, default=10)
    p_hist.set_defaults(func=cmd_history)

    p_ingest = sub.add_parser("ingest-file", help="ingest a text/code file into clips")
    p_ingest.add_argument("--path", required=True, help="path to file")
    p_ingest.add_argument("--max-bytes", type=int, default=None)
    p_ingest.add_argument("--allow-secrets", action="store_true", help="override allow_secrets for this ingest")
    p_ingest.add_argument("--allow-pdf", action="store_true", help="allow pdf ingestion (requires pdftotext)")
    p_ingest.add_argument("--embedder", choices=["hash", "e5-small"], help="embedding model for this ingest (default from config)")
    p_ingest.add_argument("--tag", action="append", help="tag(s) to attach")
    p_ingest.set_defaults(func=cmd_ingest_file)

    p_inbox = sub.add_parser("watch-inbox", help="watch a directory and ingest new/changed files")
    p_inbox.add_argument("--dir", default=str(Path.home() / ".my-father-mother" / "inbox"))
    p_inbox.add_argument("--interval", type=float, default=5.0)
    p_inbox.add_argument("--max-bytes", type=int, default=None)
    p_inbox.add_argument("--allow-secrets", action="store_true", help="allow secrets while ingesting")
    p_inbox.add_argument("--allow-pdf", action="store_true", help="allow pdf ingestion (requires pdftotext)")
    p_inbox.add_argument("--embedder", choices=["hash", "e5-small"], help="embedding model for inbox ingests (default from config)")
    p_inbox.add_argument("--tag", action="append", help="tag(s) to attach to ingested files")
    p_inbox.set_defaults(func=cmd_watch_inbox)

    p_img = sub.add_parser("ingest-image", help="ingest an image via OCR (tesseract)")
    p_img.add_argument("--path", required=True, help="path to image")
    p_img.add_argument("--max-bytes", type=int, default=None)
    p_img.add_argument("--allow-secrets", action="store_true", help="override allow_secrets for this ingest")
    p_img.add_argument("--embedder", choices=["hash", "e5-small"], help="embedding model (default from config)")
    p_img.add_argument("--allow-images", action="store_true", help="temporarily allow image OCR ingestion")
    p_img.set_defaults(func=cmd_ingest_image)

    p_meeting = sub.add_parser("ingest-transcript", help="ingest a meeting transcript/text and tag it")
    p_meeting.add_argument("--path", required=True, help="path to transcript file")
    p_meeting.add_argument("--max-bytes", type=int, default=None)
    p_meeting.add_argument("--allow-secrets", action="store_true", help="override allow_secrets for this ingest")
    p_meeting.add_argument("--embedder", choices=["hash", "e5-small"], help="embedding model (default from config)")
    p_meeting.add_argument("--tag", action="append", help="extra tag(s) to attach")
    p_meeting.set_defaults(func=cmd_ingest_transcript)

    p_fed = sub.add_parser("federate-import", help="import clips from an export file or URL (simple federation)")
    p_fed.add_argument("--path", help="path to JSON export")
    p_fed.add_argument("--url", help="URL returning JSON export")
    p_fed.set_defaults(func=cmd_federate_import)

    p_helper_rewrite = sub.add_parser("rewrite", help="run helper rewrite script on a clip (stdin=clip, stdout=rewrite)")
    p_helper_rewrite.add_argument("--id", type=int, help="clip id (default latest)")
    p_helper_rewrite.add_argument("--timeout", type=float, default=8.0)
    p_helper_rewrite.add_argument("--show", action="store_true", help="print helper output")
    p_helper_rewrite.set_defaults(func=cmd_rewrite)

    p_helper_shorten = sub.add_parser("shorten", help="run helper shorten script on a clip")
    p_helper_shorten.add_argument("--id", type=int, help="clip id (default latest)")
    p_helper_shorten.add_argument("--timeout", type=float, default=8.0)
    p_helper_shorten.add_argument("--show", action="store_true", help="print helper output")
    p_helper_shorten.set_defaults(func=cmd_shorten)

    p_helper_extract = sub.add_parser("extract", help="run helper extract script on a clip")
    p_helper_extract.add_argument("--id", type=int, help="clip id (default latest)")
    p_helper_extract.add_argument("--timeout", type=float, default=8.0)
    p_helper_extract.add_argument("--show", action="store_true", help="print helper output")
    p_helper_extract.set_defaults(func=cmd_extract)

    p_note = sub.add_parser("note", help="append and view notes for a clip")
    p_note.add_argument("--id", type=int, required=True, help="clip id")
    p_note.add_argument("--text", help="note text to append")
    p_note.set_defaults(func=cmd_note)

    p_recall = sub.add_parser("recall", help="run recall helper over recent clips (off by default)")
    p_recall.add_argument("--hours", type=float, help="look back this many hours (optional)")
    p_recall.add_argument("--limit", type=int, default=50, help="max clips to send")
    p_recall.add_argument("--timeout", type=float, default=12.0)
    p_recall.add_argument("--save", action="store_true", help="save helper output as a clip tagged recall")
    p_recall.add_argument("--show", action="store_true", help="print helper output")
    p_recall.set_defaults(func=cmd_recall)

    p_fill = sub.add_parser("fill", help="run fill/gaps helper over recent clips (off by default)")
    p_fill.add_argument("--hours", type=float, help="look back this many hours (optional)")
    p_fill.add_argument("--limit", type=int, default=50, help="max clips to send")
    p_fill.add_argument("--timeout", type=float, default=12.0)
    p_fill.add_argument("--save", action="store_true", help="save helper output as a clip tagged fill")
    p_fill.add_argument("--show", action="store_true", help="print helper output")
    p_fill.set_defaults(func=cmd_fill)

    p_related = sub.add_parser("related", help="semantic related clips for a given clip id")
    p_related.add_argument("--id", type=int, required=True)
    p_related.add_argument("--limit", type=int, default=10)
    p_related.add_argument("--pool", type=int, default=2000, help="pool size to score (higher = more accurate)")
    p_related.add_argument("--app", help="filter by source app")
    p_related.add_argument("--tag", help="filter by tag")
    p_related.set_defaults(func=cmd_related)

    p_recap = sub.add_parser("recap", help="session recap grouped by app within a timeframe")
    p_recap.add_argument("--minutes", type=int, default=60, help="lookback window in minutes")
    p_recap.add_argument("--limit", type=int, default=200, help="max clips to consider")
    p_recap.set_defaults(func=cmd_recap)

    p_context = sub.add_parser("context", help="dump recent context bundle for LLMs/sidecars")
    p_context.add_argument("--limit", type=int, default=20, help="max clips")
    p_context.add_argument("--app", help="filter by app")
    p_context.add_argument("--tag", help="filter by tag")
    p_context.add_argument("--since-hours", type=float, help="look back this many hours")
    p_context.add_argument("--pins-only", action="store_true", help="only pinned clips")
    p_context.set_defaults(func=cmd_context)

    p_topics = sub.add_parser("topics", help="group recent clips into topic buckets (tags/apps)")
    p_topics.add_argument("--limit", type=int, default=8, help="max topic buckets to show")
    p_topics.add_argument("--per-group", type=int, default=5, help="max items per bucket")
    p_topics.add_argument("--app", help="filter by source app before grouping")
    p_topics.add_argument("--tag", help="filter by tag before grouping")
    p_topics.add_argument("--since", help="ISO timestamp lower bound")
    p_topics.add_argument("--since-hours", type=float, help="look back this many hours")
    p_topics.add_argument("--until", help="ISO timestamp upper bound")
    p_topics.add_argument("--pins-only", action="store_true", help="only pinned clips")
    p_topics.set_defaults(func=cmd_topics)

    p_palette = sub.add_parser("palette", help="interactive picker to copy a clip")
    p_palette.add_argument("--query", help="filter text (uses FTS if provided)")
    p_palette.add_argument("--semantic", action="store_true", help="use semantic search instead of FTS")
    p_palette.add_argument("--app", help="filter by app")
    p_palette.add_argument("--tag", help="filter by tag")
    p_palette.add_argument("--limit", type=int, default=30, help="max items to show")
    p_palette.add_argument("--embedder", choices=["hash", "e5-small"], help="embedding model for semantic mode (default from config)")
    p_palette.add_argument("--pins-only", action="store_true", help="only pinned clips")
    p_palette.add_argument("--since-hours", type=float, help="look back this many hours")
    p_palette.set_defaults(func=cmd_palette)

    p_launch = sub.add_parser("install-launchagent", help="write/remove LaunchAgent for watcher")
    p_launch.add_argument("--cap", type=int, default=2000, help="cap passed to watcher")
    p_launch.add_argument("--interval", type=float, default=1.0, help="interval passed to watcher")
    p_launch.add_argument("--allow-secrets", action="store_true", help="allow secrets in watcher")
    p_launch.add_argument("--remove", action="store_true", help="remove the LaunchAgent file")
    p_launch.set_defaults(func=cmd_install_launchagent)

    p_serve = sub.add_parser("serve", help="start local HTTP API server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
