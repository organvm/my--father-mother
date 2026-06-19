"""Tests for main.py — database, clips, settings, secrets, embeddings, tags."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone, timedelta

import main as mfm


# ──────────────────────────────────────────────
# Database + Schema
# ──────────────────────────────────────────────

class TestInitDb:
    def test_creates_tables(self, conn):
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "clips" in tables
        assert "clips_fts" in tables
        assert "settings" in tables
        assert "blocklist" in tables
        assert "clip_vectors" in tables
        assert "tags" in tables
        assert "clip_tags" in tables
        assert "clip_notes" in tables
        assert "clip_events" in tables
        assert "copilot_chats" in tables

    def test_idempotent(self, conn):
        # Second call should not raise
        mfm.init_db(conn)

    def test_clips_columns(self, conn):
        cur = conn.execute("PRAGMA table_info(clips)")
        cols = {row["name"] for row in cur.fetchall()}
        assert "id" in cols
        assert "content" in cols
        assert "hash" in cols
        assert "pinned" in cols
        assert "title" in cols
        assert "file_path" in cols
        assert "lang" in cols


class TestColumnExists:
    def test_existing_column(self, conn):
        assert mfm.column_exists(conn, "clips", "content") is True

    def test_nonexistent_column(self, conn):
        assert mfm.column_exists(conn, "clips", "nonexistent") is False


# ──────────────────────────────────────────────
# Clip CRUD
# ──────────────────────────────────────────────

class TestClipExists:
    def test_missing(self, conn):
        assert mfm.clip_exists(conn, "deadbeef") is False

    def test_found(self, populated_db):
        assert mfm.clip_exists(populated_db, "hash_a") is True


class TestGetClipIdByHash:
    def test_missing(self, conn):
        assert mfm.get_clip_id_by_hash(conn, "nope") is None

    def test_found(self, populated_db):
        cid = mfm.get_clip_id_by_hash(populated_db, "hash_b")
        assert cid is not None
        assert isinstance(cid, int)


class TestInsertClip:
    def test_inserts_new_clip(self, conn):
        cid = mfm.insert_clip(conn, "test content", "Terminal", "zsh")
        assert cid is not None
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (cid,)).fetchone()
        assert row["content"] == "test content"
        assert row["source_app"] == "Terminal"

    def test_rejects_empty(self, conn):
        assert mfm.insert_clip(conn, "", "App", "Win") is None
        assert mfm.insert_clip(conn, "   ", "App", "Win") is None

    def test_deduplicates(self, conn):
        id1 = mfm.insert_clip(conn, "duplicate content", "App", "Win")
        id2 = mfm.insert_clip(conn, "duplicate content", "App", "Win")
        assert id1 is not None
        assert id2 is None

    def test_stores_embedding(self, conn):
        mfm.set_pro_enabled(conn, True)
        cid = mfm.insert_clip(conn, "embedding test", "App", "Win")
        vec_row = conn.execute("SELECT * FROM clip_vectors WHERE clip_id = ?", (cid,)).fetchone()
        assert vec_row is not None
        assert vec_row["model"] == "hash"
        assert vec_row["dim"] == mfm.EMBED_DIM

    def test_without_pro_skips_embedding_and_keeps_fts(self, conn):
        cid = mfm.insert_clip(conn, "fts fallback content", "App", "Win")
        assert cid is not None

        vec_row = conn.execute("SELECT * FROM clip_vectors WHERE clip_id = ?", (cid,)).fetchone()
        assert vec_row is None

        fts_row = conn.execute(
            "SELECT rowid FROM clips_fts WHERE clips_fts MATCH ?",
            ("fallback",),
        ).fetchone()
        assert fts_row is not None
        assert fts_row["rowid"] == cid


class TestInsertEvent:
    def test_inserts_event(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        mfm.insert_event(populated_db, cid)
        events = populated_db.execute("SELECT * FROM clip_events WHERE clip_id = ?", (cid,)).fetchall()
        assert len(events) == 1


class TestPrune:
    def test_prune_removes_oldest(self, conn):
        for i in range(5):
            ts = f"2026-01-{10+i:02d}T00:00:00+00:00"
            conn.execute(
                "INSERT INTO clips (created_at, source_app, window_title, content, hash, pinned, lang) VALUES (?,?,?,?,?,0,'unk')",
                (ts, "App", "Win", f"content {i}", f"hash_{i}"),
            )
        conn.commit()
        removed = mfm.prune(conn, cap=3)
        assert removed == 2
        remaining = conn.execute("SELECT COUNT(*) as c FROM clips").fetchone()["c"]
        assert remaining == 3


# ──────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────

class TestSettings:
    def test_get_default(self, conn):
        assert mfm.get_setting(conn, "nonexistent", "fallback") == "fallback"

    def test_set_and_get(self, conn):
        mfm.set_setting(conn, "mykey", "myval")
        assert mfm.get_setting(conn, "mykey") == "myval"

    def test_upsert(self, conn):
        mfm.set_setting(conn, "key", "v1")
        mfm.set_setting(conn, "key", "v2")
        assert mfm.get_setting(conn, "key") == "v2"


class TestBoolSettings:
    def test_get_default_true(self, conn):
        assert mfm.get_bool_setting(conn, "flag", True) is True

    def test_get_default_false(self, conn):
        assert mfm.get_bool_setting(conn, "flag", False) is False

    def test_set_and_get(self, conn):
        mfm.set_bool_setting(conn, "flag", True)
        assert mfm.get_bool_setting(conn, "flag", False) is True
        mfm.set_bool_setting(conn, "flag", False)
        assert mfm.get_bool_setting(conn, "flag", True) is False


class TestParseBoolValue:
    def test_truthy(self):
        for val in ("1", "true", "True", "TRUE", "yes", "on", "ON"):
            assert mfm.parse_bool_value(val) is True

    def test_falsy(self):
        for val in ("0", "false", "False", "no", "off", "OFF"):
            assert mfm.parse_bool_value(val) is False

    def test_none(self):
        assert mfm.parse_bool_value(None) is None

    def test_garbage(self):
        assert mfm.parse_bool_value("maybe") is None


# ──────────────────────────────────────────────
# Secrets
# ──────────────────────────────────────────────

class TestSecrets:
    def test_aws_key_detected(self):
        assert mfm.looks_like_secret("AKIAIOSFODNN7EXAMPLE") is True  # allow-secret

    def test_github_pat_detected(self):
        assert mfm.looks_like_secret("ghp_ABCDEFghijklmnopqrstuvwxyz0123456789") is True  # allow-secret

    def test_private_key_detected(self):
        assert mfm.looks_like_secret("-----BEGIN RSA PRIVATE KEY") is True

    def test_normal_text_safe(self):
        assert mfm.looks_like_secret("just some normal text") is False

    def test_redact_replaces(self):
        text = "key: AKIAIOSFODNN7EXAMPLE data"  # allow-secret
        redacted = mfm.redact_secrets(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in redacted  # allow-secret
        assert "[REDACTED]" in redacted

    def test_redact_preserves_safe_text(self):
        text = "hello world"
        assert mfm.redact_secrets(text) == text


class TestAllowSecrets:
    def test_default_disallowed(self, conn):
        assert mfm.get_allow_secrets(conn, None) is False

    def test_override_true(self, conn):
        assert mfm.get_allow_secrets(conn, True) is True

    def test_set_and_get(self, conn):
        mfm.set_allow_secrets(conn, True)
        assert mfm.get_allow_secrets(conn, None) is True


# ──────────────────────────────────────────────
# Tags
# ──────────────────────────────────────────────

class TestTags:
    def test_get_or_create(self, conn):
        tag_id = mfm.get_or_create_tag(conn, "work")
        assert isinstance(tag_id, int)
        # Same name returns same id
        assert mfm.get_or_create_tag(conn, "work") == tag_id

    def test_assign_tag(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        result = mfm.assign_tag(populated_db, cid, "important")
        assert result is True
        tags = mfm.tags_for_clip(populated_db, cid)
        assert "important" in tags

    def test_assign_tag_idempotent(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        mfm.assign_tag(populated_db, cid, "work")
        mfm.assign_tag(populated_db, cid, "work")
        tags = mfm.tags_for_clip(populated_db, cid)
        assert tags.count("work") == 1

    def test_remove_tag(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        mfm.assign_tag(populated_db, cid, "temp")
        assert mfm.remove_tag(populated_db, cid, "temp") is True
        assert "temp" not in mfm.tags_for_clip(populated_db, cid)

    def test_remove_nonexistent_tag(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        assert mfm.remove_tag(populated_db, cid, "nope") is False

    def test_clear_tags(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        mfm.assign_tag(populated_db, cid, "a")
        mfm.assign_tag(populated_db, cid, "b")
        count = mfm.clear_tags(populated_db, cid)
        assert count == 2
        assert mfm.tags_for_clip(populated_db, cid) == []

    def test_list_tags(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        mfm.assign_tag(populated_db, cid, "alpha")
        mfm.assign_tag(populated_db, cid, "beta")
        all_tags = mfm.list_tags(populated_db)
        assert "alpha" in all_tags
        assert "beta" in all_tags

    def test_tags_for_clips_batch(self, populated_db):
        ids = [r["id"] for r in populated_db.execute("SELECT id FROM clips").fetchall()]
        mfm.assign_tag(populated_db, ids[0], "x")
        mfm.assign_tag(populated_db, ids[1], "y")
        tag_map = mfm.tags_for_clips(populated_db, ids)
        assert "x" in tag_map[ids[0]]
        assert "y" in tag_map[ids[1]]


# ──────────────────────────────────────────────
# Notes
# ──────────────────────────────────────────────

class TestNotes:
    def test_add_note(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        assert mfm.add_note(populated_db, cid, "This is important") is True

    def test_notes_for_clips(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        mfm.add_note(populated_db, cid, "Note A")
        mfm.add_note(populated_db, cid, "Note B")
        notes_map = mfm.notes_for_clips(populated_db, [cid])
        assert len(notes_map[cid]) == 2
        note_texts = [n["note"] for n in notes_map[cid]]
        assert "Note A" in note_texts


# ──────────────────────────────────────────────
# Blocklist
# ──────────────────────────────────────────────

class TestBlocklist:
    def test_empty_by_default(self, conn):
        assert mfm.get_blocklist(conn) == set()

    def test_add_and_get(self, conn):
        mfm.add_blocked_app(conn, "Slack")
        # App names are normalized to lowercase
        assert "slack" in mfm.get_blocklist(conn)

    def test_add_idempotent(self, conn):
        # add_blocked_app always returns True (uses INSERT OR IGNORE)
        assert mfm.add_blocked_app(conn, "Slack") is True
        assert mfm.add_blocked_app(conn, "Slack") is True
        # But only one entry exists
        assert len(mfm.get_blocklist(conn)) == 1

    def test_remove(self, conn):
        mfm.add_blocked_app(conn, "Discord")
        assert mfm.remove_blocked_app(conn, "Discord") is True
        assert "Discord" not in mfm.get_blocklist(conn)

    def test_remove_nonexistent(self, conn):
        assert mfm.remove_blocked_app(conn, "Nope") is False


# ──────────────────────────────────────────────
# Embeddings
# ──────────────────────────────────────────────

class TestTokenize:
    def test_basic(self):
        assert mfm.tokenize("Hello World") == ["hello", "world"]

    def test_strips_punctuation(self):
        assert mfm.tokenize("foo.bar, baz!") == ["foo", "bar", "baz"]

    def test_empty(self):
        assert mfm.tokenize("") == []

    def test_underscores(self):
        assert mfm.tokenize("my_var") == ["my_var"]


class TestHashEmbed:
    def test_returns_correct_dim(self):
        vec = mfm.hash_embed("hello world")
        assert len(vec) == mfm.EMBED_DIM

    def test_normalized(self):
        vec = mfm.hash_embed("test text")
        norm = sum(v * v for v in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-6

    def test_empty_returns_zeros(self):
        vec = mfm.hash_embed("")
        assert all(v == 0.0 for v in vec)

    def test_deterministic(self):
        a = mfm.hash_embed("same input")
        b = mfm.hash_embed("same input")
        assert a == b

    def test_different_inputs_differ(self):
        a = mfm.hash_embed("input one")
        b = mfm.hash_embed("input two")
        assert a != b


class TestCosine:
    def test_identical(self):
        a = [1.0, 0.0, 0.0]
        assert abs(mfm.cosine(a, a) - 1.0) < 1e-6

    def test_orthogonal(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(mfm.cosine(a, b)) < 1e-6

    def test_opposite(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(mfm.cosine(a, b) - (-1.0)) < 1e-6


class TestKnn:
    def test_returns_sorted(self):
        query = [1.0, 0.0, 0.0]
        ids = [1, 2, 3]
        vecs = [
            [0.1, 0.9, 0.0],  # low similarity
            [0.9, 0.1, 0.0],  # high similarity
            [0.5, 0.5, 0.0],  # medium
        ]
        results = mfm.knn(query, ids, vecs, limit=2)
        assert len(results) == 2
        # First result should have highest similarity
        assert results[0][1] == 2  # id=2 is most similar
        assert results[0][0] > results[1][0]

    def test_limit(self):
        query = [1.0]
        ids = [1, 2, 3, 4, 5]
        vecs = [[0.1], [0.5], [0.9], [0.3], [0.7]]
        results = mfm.knn(query, ids, vecs, limit=3)
        assert len(results) == 3


class TestStoreAndLoadEmbedding:
    def test_round_trip(self, conn):
        conn.execute(
            "INSERT INTO clips (created_at, source_app, window_title, content, hash, pinned, lang) VALUES (?,?,?,?,?,0,'unk')",
            ("2026-01-01", "App", "Win", "test", "abc", ),
        )
        conn.commit()
        cid = conn.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        vec = [0.1, 0.2, 0.3]
        mfm.store_embedding(conn, cid, vec, "hash")
        row = conn.execute("SELECT * FROM clip_vectors WHERE clip_id = ?", (cid,)).fetchone()
        loaded = mfm.load_embedding(row)
        assert loaded == vec


# ──────────────────────────────────────────────
# Utility helpers
# ──────────────────────────────────────────────

class TestParseIsoDt:
    def test_valid(self):
        result = mfm.parse_iso_dt("2026-01-15T10:00:00")
        assert result is not None
        assert "2026-01-15" in result

    def test_invalid(self):
        assert mfm.parse_iso_dt("not a date") is None

    def test_with_timezone(self):
        result = mfm.parse_iso_dt("2026-01-15T10:00:00+00:00")
        assert result is not None


class TestIsoHoursAgo:
    def test_none_returns_none(self):
        assert mfm.iso_hours_ago(None) is None

    def test_returns_iso(self):
        result = mfm.iso_hours_ago(24)
        assert result is not None
        # Should be a valid ISO datetime
        datetime.fromisoformat(result)

    def test_invalid_returns_none(self):
        assert mfm.iso_hours_ago("not_a_number") is None


class TestResolveSyncTarget:
    def test_file_path(self, tmp_path):
        result = mfm.resolve_sync_target(str(tmp_path / "backup.db"))
        assert result.name == "backup.db"

    def test_directory_path(self, tmp_path):
        result = mfm.resolve_sync_target(str(tmp_path) + "/")
        assert result.name == "mfm.db"

    def test_icloud_alias(self, tmp_path, monkeypatch):
        icloud_dir = tmp_path / "CloudDocs"
        monkeypatch.setattr(mfm, "ICLOUD_DRIVE_DIR", icloud_dir)
        result = mfm.resolve_sync_target("icloud")
        assert result == icloud_dir / "mfm.db"

    def test_icloud_alias_is_trimmed_and_case_insensitive(self, tmp_path, monkeypatch):
        icloud_dir = tmp_path / "CloudDocs"
        monkeypatch.setattr(mfm, "ICLOUD_DRIVE_DIR", icloud_dir)
        result = mfm.resolve_sync_target("  iCloud  ")
        assert result == icloud_dir / "mfm.db"


class TestSyncPush:
    def test_icloud_push_writes_sqlite_snapshot(self, conn, tmp_path, monkeypatch):
        icloud_dir = tmp_path / "CloudDocs"
        monkeypatch.setattr(mfm, "ICLOUD_DRIVE_DIR", icloud_dir)
        mfm.insert_clip(conn, "synced content", "Terminal", "zsh")

        ok, msg = mfm.sync_push("icloud", conn)

        assert ok is True
        assert "pushed db snapshot" in msg
        synced = sqlite3.connect(icloud_dir / "mfm.db")
        try:
            count = synced.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        finally:
            synced.close()
        assert count == 1


class TestStats:
    def test_empty_db(self, conn):
        s = mfm.stats(conn)
        assert s["count"] == 0
        assert s["latest"] is None

    def test_with_clips(self, populated_db):
        s = mfm.stats(populated_db)
        assert s["count"] == 3


class TestMaxBytes:
    def test_default(self, conn):
        assert mfm.get_max_bytes(conn, None) == mfm.DEFAULT_MAX_BYTES

    def test_override(self, conn):
        assert mfm.get_max_bytes(conn, 32768) == 32768


class TestEvictMode:
    def test_default(self, conn):
        assert mfm.get_evict_mode(conn) == mfm.DEFAULT_EVICT_MODE

    def test_set_valid(self, conn):
        mfm.set_evict_mode(conn, "tiered")
        assert mfm.get_evict_mode(conn) == "tiered"

    def test_set_invalid_uses_default(self, conn):
        mfm.set_evict_mode(conn, "invalid")
        assert mfm.get_evict_mode(conn) == mfm.DEFAULT_EVICT_MODE


class TestPaused:
    def test_not_paused_by_default(self, conn):
        assert mfm.is_paused(conn) is False

    def test_set_paused(self, conn):
        mfm.set_paused(conn, True)
        assert mfm.is_paused(conn) is True
        mfm.set_paused(conn, False)
        assert mfm.is_paused(conn) is False


class TestNotify:
    def test_default(self, conn):
        assert mfm.get_notify(conn, None) == mfm.DEFAULT_NOTIFY

    def test_override(self, conn):
        assert mfm.get_notify(conn, True) is True

    def test_set_and_get(self, conn):
        mfm.set_notify(conn, True)
        assert mfm.get_notify(conn, None) is True


class TestProEnabled:
    def test_default_off(self, conn):
        assert mfm.get_pro_enabled(conn) is False

    def test_set_and_get(self, conn):
        mfm.set_pro_enabled(conn, True)
        assert mfm.get_pro_enabled(conn) is True
        mfm.set_pro_enabled(conn, False)
        assert mfm.get_pro_enabled(conn) is False


class TestEmbedder:
    def test_default_hash(self, conn):
        assert mfm.get_embedder(conn, None) == "hash"

    def test_override(self, conn):
        assert mfm.get_embedder(conn, "e5-small") == "e5-small"
        assert mfm.get_embedder(conn, "e5") == "e5-small"

    def test_set_and_get(self, conn):
        mfm.set_embedder(conn, "e5-small")
        assert mfm.get_embedder(conn, None) == "e5-small"
        mfm.set_embedder(conn, "hash")
        assert mfm.get_embedder(conn, None) == "hash"

    def test_unknown_defaults_to_hash(self, conn):
        assert mfm.get_embedder(conn, "unknown") == "hash"


class TestEmbedFromKind:
    def test_hash_returns_vector(self):
        vec, model = mfm.embed_from_kind("hash", "test text")
        assert model == "hash"
        assert len(vec) == mfm.EMBED_DIM

    def test_e5_falls_back_to_hash(self):
        """Without sentence-transformers installed, e5 falls back to hash."""
        vec, model = mfm.embed_from_kind("e5-small", "test text")
        assert model == "hash"
        assert len(vec) == mfm.EMBED_DIM


class TestCopilotChats:
    def test_add_and_list(self, conn):
        mfm.add_copilot_chat(conn, "Hello AI", "Test Chat", "gemini-2.5-flash")
        chats = mfm.list_copilot_chats(conn, limit=10)
        assert len(chats) == 1
        # list_copilot_chats returns id, created_at, title, model (not content)
        assert chats[0]["title"] == "Test Chat"
        assert chats[0]["model"] == "gemini-2.5-flash"

    def test_add_returns_true(self, conn):
        assert mfm.add_copilot_chat(conn, "Hello", None, None) is True

    def test_add_rejects_empty(self, conn):
        assert mfm.add_copilot_chat(conn, "", None, None) is False
        assert mfm.add_copilot_chat(conn, "   ", None, None) is False

    def test_list_empty(self, conn):
        assert mfm.list_copilot_chats(conn, limit=10) == []

    def test_count(self, conn):
        assert mfm.copilot_chat_count(conn) == 0
        mfm.add_copilot_chat(conn, "A", None, None)
        mfm.add_copilot_chat(conn, "B", None, None)
        assert mfm.copilot_chat_count(conn) == 2

    def test_clear(self, conn):
        mfm.add_copilot_chat(conn, "A", None, None)
        mfm.add_copilot_chat(conn, "B", None, None)
        cleared = mfm.clear_copilot_chats(conn)
        assert cleared == 2
        assert mfm.copilot_chat_count(conn) == 0
