"""Tests for main.py — database, clips, settings, secrets, embeddings, tags."""

import hashlib
import hmac
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
        cid = mfm.insert_clip(conn, "embedding test", "App", "Win")
        vec_row = conn.execute("SELECT * FROM clip_vectors WHERE clip_id = ?", (cid,)).fetchone()
        assert vec_row is not None
        assert vec_row["model"] == "hash"
        assert vec_row["dim"] == mfm.EMBED_DIM


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


class TestLicenseState:
    def test_default_is_free(self, conn):
        assert mfm.is_pro_enabled(conn) is False
        snap = mfm.license_snapshot(conn)
        assert snap["license_type"] == "free"
        assert snap["has_license_key"] is False
        assert snap["upgrade_url"] == mfm.DEFAULT_UPGRADE_URL

    def test_set_license_key_enables_pro(self, conn):
        mfm.set_license_key(conn, "LIC-1234567890")
        assert mfm.is_pro_enabled(conn) is True
        assert mfm.get_license_key(conn) == "LIC-1234567890"
        snap = mfm.license_snapshot(conn)
        assert snap["license_type"] == "pro"
        assert snap["license_status"] == "active"
        assert snap["license_key"] == "LIC-...7890"

    def test_empty_license_key_disables_pro(self, conn):
        mfm.set_license_key(conn, "LIC-1234567890")
        mfm.set_license_key(conn, "")
        assert mfm.is_pro_enabled(conn) is False
        assert mfm.get_license_key(conn) == ""

    def test_set_pro_enabled_override(self, conn):
        mfm.set_pro_enabled(conn, True)
        assert mfm.is_pro_enabled(conn) is True
        assert mfm.license_snapshot(conn)["license_type"] == "pro"

    def test_resolve_embedder_gates_e5_without_pro(self, conn):
        kind, warning = mfm.resolve_embedder_for_use(conn, "e5-small")
        assert kind == "hash"
        assert "require Pro" in warning

    def test_resolve_embedder_allows_e5_with_pro(self, conn):
        mfm.set_license_key(conn, "LIC-1234567890")
        kind, warning = mfm.resolve_embedder_for_use(conn, "e5-small")
        assert kind == "e5-small"
        assert warning is None


class TestGumroadLicenseHelpers:
    def test_verify_signature_accepts_plain_and_prefixed_digest(self):
        body = b"license_key=LIC-123&email=customer@example.com"
        secret = "webhook-secret"
        sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

        assert mfm.verify_gumroad_signature(body, sig, secret) is True
        assert mfm.verify_gumroad_signature(body, f"sha256={sig}", secret) is True
        assert mfm.verify_gumroad_signature(body, "bad", secret) is False

    def test_parse_form_payload(self):
        data = mfm.parse_gumroad_payload(
            b"license_key=LIC-123&email=customer%40example.com",
            "application/x-www-form-urlencoded",
        )
        assert data["license_key"] == "LIC-123"
        assert data["email"] == "customer@example.com"

    def test_parse_json_payload(self):
        data = mfm.parse_gumroad_payload(
            b'{"license_key":"LIC-123","email":"customer@example.com"}',
            "application/json",
        )
        assert data["license_key"] == "LIC-123"
        assert data["email"] == "customer@example.com"

    def test_apply_event_stores_license(self, conn, monkeypatch):
        monkeypatch.setattr(mfm, "validate_gumroad_license", lambda _conn, _key: None)
        ok, msg, valid = mfm.apply_gumroad_license_event(
            conn,
            {
                "license_key": "LIC-1234567890",
                "email": "customer@example.com",
                "product_permalink": "myfathermother",
                "sale_id": "sale-1",
            },
        )

        assert ok is True
        assert msg == "license stored"
        assert valid is None
        assert mfm.is_pro_enabled(conn) is True
        assert mfm.get_setting(conn, "gumroad_email") == "customer@example.com"
        assert mfm.get_setting(conn, "gumroad_permalink") == "myfathermother"
        assert mfm.get_setting(conn, "gumroad_sale_id") == "sale-1"

    def test_apply_event_rejects_invalid_license(self, conn, monkeypatch):
        monkeypatch.setattr(mfm, "validate_gumroad_license", lambda _conn, _key: False)
        ok, msg, valid = mfm.apply_gumroad_license_event(conn, {"license_key": "BAD"})

        assert ok is False
        assert msg == "license verification failed"
        assert valid is False
        assert mfm.is_pro_enabled(conn) is False


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


# ──────────────────────────────────────────────
# filtered_rows — FTS5 search and filters
# ──────────────────────────────────────────────

class TestFilteredRows:
    def test_no_filters(self, populated_db):
        rows, tag_map = mfm.filtered_rows(populated_db, limit=10)
        assert len(rows) == 3

    def test_limit(self, populated_db):
        rows, _ = mfm.filtered_rows(populated_db, limit=2)
        assert len(rows) == 2

    def test_filter_by_app(self, populated_db):
        rows, _ = mfm.filtered_rows(populated_db, limit=10, app="Terminal")
        assert len(rows) == 1
        assert rows[0]["source_app"] == "Terminal"

    def test_filter_by_app_case_insensitive(self, populated_db):
        rows, _ = mfm.filtered_rows(populated_db, limit=10, app="terminal")
        assert len(rows) == 1

    def test_filter_by_contains(self, populated_db):
        rows, _ = mfm.filtered_rows(populated_db, limit=10, contains="main")
        assert len(rows) == 1
        assert "def main()" in rows[0]["content"]

    def test_filter_by_pins_only(self, populated_db):
        rows, _ = mfm.filtered_rows(populated_db, limit=10, pins_only=True)
        assert len(rows) == 0
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        populated_db.execute("UPDATE clips SET pinned = 1 WHERE id = ?", (cid,))
        populated_db.commit()
        rows, _ = mfm.filtered_rows(populated_db, limit=10, pins_only=True)
        assert len(rows) == 1

    def test_filter_by_tag(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        mfm.assign_tag(populated_db, cid, "test-tag")
        rows, tag_map = mfm.filtered_rows(populated_db, limit=10, tag="test-tag")
        assert len(rows) == 1

    def test_filter_by_since_iso(self, populated_db):
        rows, _ = mfm.filtered_rows(populated_db, limit=10, since_iso="2026-01-16T00:00:00")
        assert len(rows) == 0
        rows, _ = mfm.filtered_rows(populated_db, limit=10, since_iso="2026-01-14T00:00:00")
        assert len(rows) == 3

    def test_filter_by_until_iso(self, populated_db):
        rows, _ = mfm.filtered_rows(populated_db, limit=10, until_iso="2026-01-14T00:00:00")
        assert len(rows) == 0
        rows, _ = mfm.filtered_rows(populated_db, limit=10, until_iso="2026-01-16T00:00:00")
        assert len(rows) == 3

    def test_combined_filters(self, populated_db):
        rows, _ = mfm.filtered_rows(
            populated_db, limit=10, app="Terminal", contains="hello"
        )
        assert len(rows) == 1

    def test_returns_tag_map(self, populated_db):
        cids = [r["id"] for r in populated_db.execute("SELECT id FROM clips").fetchall()]
        mfm.assign_tag(populated_db, cids[0], "alpha")
        mfm.assign_tag(populated_db, cids[1], "beta")
        rows, tag_map = mfm.filtered_rows(populated_db, limit=10)
        assert tag_map[cids[0]] == ["alpha"]
        assert tag_map[cids[1]] == ["beta"]


# ──────────────────────────────────────────────
# Eviction — per-app, per-tag, tiered
# ──────────────────────────────────────────────

class TestEvictAppCap:
    def test_noop_when_under_cap(self, conn):
        for i in range(3):
            mfm.insert_clip(conn, f"content {i}", "Terminal", "Win")
        assert mfm.evict_app_cap(conn, "Terminal", cap=5) == 0
        assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 3

    def test_evicts_oldest(self, conn):
        for i in range(5):
            mfm.insert_clip(conn, f"content {i}", "Terminal", "Win")
        assert mfm.evict_app_cap(conn, "Terminal", cap=3) == 2
        assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 3

    def test_preserves_pinned(self, conn):
        cids = []
        for i in range(5):
            cid = mfm.insert_clip(conn, f"content {i}", "Terminal", "Win")
            cids.append(cid)
        conn.execute("UPDATE clips SET pinned = 1 WHERE id = ?", (cids[0],))
        conn.commit()
        assert mfm.evict_app_cap(conn, "Terminal", cap=2) == 3
        remaining = {r["id"] for r in conn.execute("SELECT id FROM clips").fetchall()}
        assert cids[0] in remaining

    def test_case_insensitive_app_match(self, conn):
        mfm.insert_clip(conn, "one", "Terminal", "Win")
        mfm.insert_clip(conn, "two", "Terminal", "Win")
        assert mfm.evict_app_cap(conn, "terminal", cap=1) == 1

    def test_noop_with_negative_cap(self, conn):
        mfm.insert_clip(conn, "test", "Terminal", "Win")
        assert mfm.evict_app_cap(conn, "Terminal", cap=-1) == 0

    def test_noop_with_empty_app(self, conn):
        assert mfm.evict_app_cap(conn, "", cap=5) == 0


class TestEvictTagCap:
    def test_noop_when_under_cap(self, conn):
        cid = mfm.insert_clip(conn, "test", "App", "Win")
        mfm.assign_tag(conn, cid, "mytag")
        assert mfm.evict_tag_cap(conn, "mytag", cap=5) == 0

    def test_evicts_oldest_tagged(self, conn):
        cids = []
        for i in range(5):
            cid = mfm.insert_clip(conn, f"content {i}", "App", "Win")
            mfm.assign_tag(conn, cid, "mytag")
            cids.append(cid)
        assert mfm.evict_tag_cap(conn, "mytag", cap=2) == 3
        remaining = conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        assert remaining == 2

    def test_preserves_pinned_tagged(self, conn):
        cids = []
        for i in range(4):
            cid = mfm.insert_clip(conn, f"content {i}", "App", "Win")
            mfm.assign_tag(conn, cid, "mytag")
            cids.append(cid)
        conn.execute("UPDATE clips SET pinned = 1 WHERE id = ?", (cids[0],))
        conn.commit()
        evicted = mfm.evict_tag_cap(conn, "mytag", cap=2)
        assert evicted == 2
        remaining = {r["id"] for r in conn.execute("SELECT id FROM clips").fetchall()}
        assert cids[0] in remaining

    def test_case_insensitive_tag(self, conn):
        cid_a = mfm.insert_clip(conn, "one", "App", "Win")
        cid_b = mfm.insert_clip(conn, "two", "App", "Win")
        mfm.assign_tag(conn, cid_a, "MyTag")
        mfm.assign_tag(conn, cid_b, "MyTag")
        assert mfm.evict_tag_cap(conn, "mytag", cap=1) == 1

    def test_noop_without_tag(self, conn):
        assert mfm.evict_tag_cap(conn, "nonexistent", cap=5) == 0


class TestEvictIfNeeded:
    def test_noop_when_under_size(self, conn):
        assert mfm.evict_if_needed(conn, max_db_mb=9999) == 0


# ──────────────────────────────────────────────
# Fetch and Context
# ──────────────────────────────────────────────

class TestFetchClip:
    def test_fetch_existing(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        row = mfm.fetch_clip(populated_db, cid)
        assert row is not None
        assert row["id"] == cid

    def test_fetch_missing(self, conn):
        assert mfm.fetch_clip(conn, 999) is None


class TestLatestClip:
    def test_empty_db(self, conn):
        assert mfm.latest_clip(conn) is None

    def test_returns_most_recent(self, populated_db):
        row = mfm.latest_clip(populated_db)
        assert row is not None
        assert row["content"] == "SELECT * FROM users"


class TestContextBundle:
    def test_returns_dicts(self, populated_db):
        bundle = mfm.context_bundle(populated_db, app=None, tag=None, limit=10, hours=None)
        assert len(bundle) == 3
        assert isinstance(bundle[0], dict)
        assert "id" in bundle[0]
        assert "content" in bundle[0]
        assert "tags" in bundle[0]
        assert "notes" in bundle[0]
        assert "pinned" in bundle[0]

    def test_filters_by_app(self, populated_db):
        bundle = mfm.context_bundle(populated_db, app="Terminal", tag=None, limit=10, hours=None)
        assert len(bundle) == 1
        assert bundle[0]["source_app"] == "Terminal"

    def test_filters_by_tag(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        mfm.assign_tag(populated_db, cid, "urgent")
        bundle = mfm.context_bundle(populated_db, app=None, tag="urgent", limit=10, hours=None)
        assert len(bundle) == 1

    def test_respects_limit(self, populated_db):
        bundle = mfm.context_bundle(populated_db, app=None, tag=None, limit=2, hours=None)
        assert len(bundle) == 2

    def test_includes_notes(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        mfm.add_note(populated_db, cid, "important note")
        bundle = mfm.context_bundle(populated_db, app=None, tag=None, limit=10, hours=None)
        clip_with_note = [b for b in bundle if b["id"] == cid][0]
        assert len(clip_with_note["notes"]) == 1

    def test_pins_only(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        populated_db.execute("UPDATE clips SET pinned = 1 WHERE id = ?", (cid,))
        populated_db.commit()
        bundle = mfm.context_bundle(populated_db, app=None, tag=None, limit=10, hours=None, pins_only=True)
        assert len(bundle) == 1


# ──────────────────────────────────────────────
# Export / Import
# ──────────────────────────────────────────────

class TestExportItems:
    def test_exports_all(self, populated_db):
        items = mfm.export_items(populated_db, limit=10)
        assert len(items) == 3
        assert "content" in items[0]
        assert "source_app" in items[0]
        assert "tags" in items[0]
        assert "notes" in items[0]

    def test_filters_by_app(self, populated_db):
        items = mfm.export_items(populated_db, limit=10, app="VSCode")
        assert len(items) == 1

    def test_respects_limit(self, populated_db):
        items = mfm.export_items(populated_db, limit=1)
        assert len(items) == 1


class TestInsertClipImport:
    def test_imports_new_clip(self, conn):
        cid = mfm.insert_clip_import(conn, "imported content", "App", "Win",
                                       "2026-06-01T00:00:00+00:00", False, "My Title", None, "en", None)
        assert cid is not None
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (cid,)).fetchone()
        assert row["title"] == "My Title"
        assert row["lang"] == "en"
        assert row["pinned"] == 0

    def test_rejects_empty(self, conn):
        assert mfm.insert_clip_import(conn, "", "App", "Win", None, False, None, None, None, None) is None

    def test_deduplicates(self, conn):
        cid1 = mfm.insert_clip_import(conn, "dedup me", "App", "Win", None, False, None, None, None, None)
        cid2 = mfm.insert_clip_import(conn, "dedup me", "App", "Win", None, False, None, None, None, None)
        assert cid1 is not None
        assert cid2 == cid1

    def test_imports_pinned(self, conn):
        cid = mfm.insert_clip_import(conn, "pinned clip", "App", "Win", None, True, None, None, None, None)
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (cid,)).fetchone()
        assert row["pinned"] == 1


class TestIngestText:
    def test_ingests_with_tags(self, conn):
        cid = mfm.ingest_text(conn, "tagged clip", "App", "Win", tags=["work", "urgent"])
        assert cid is not None
        tags = mfm.tags_for_clip(conn, cid)
        assert "work" in tags
        assert "urgent" in tags

    def test_ingests_without_tags(self, conn):
        cid = mfm.ingest_text(conn, "untagged clip", "App", "Win")
        assert cid is not None

    def test_ingests_empty_returns_none(self, conn):
        assert mfm.ingest_text(conn, "", "App", "Win") is None


class TestImportClips:
    def test_imports_multiple(self, conn):
        items = [
            {"content": "first", "source_app": "App", "created_at": "2026-01-01T00:00:00+00:00"},
            {"content": "second", "source_app": "App", "created_at": "2026-01-02T00:00:00+00:00"},
        ]
        result = mfm.import_clips(conn, items)
        assert result["inserted"] == 2
        assert result["existing"] == 0
        assert result["failed"] == 0

    def test_deduplicates_during_import(self, conn):
        items = [
            {"content": "same text", "source_app": "App"},
            {"content": "same text", "source_app": "App"},
        ]
        result = mfm.import_clips(conn, items)
        assert result["inserted"] == 2
        assert result["existing"] == 0
        # Only one row actually stored (second was dedup'd by hash)
        count = conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        assert count == 1


# ──────────────────────────────────────────────
# Settings — typed, snapshot, formatting
# ──────────────────────────────────────────────

class TestGetSettingTyped:
    def test_unknown_key_falls_back_to_raw(self, conn):
        assert mfm.get_setting_typed(conn, "nonexistent") == ""

    def test_bool_key(self, conn):
        mfm.set_bool_setting(conn, "ltm_enabled", True)
        assert mfm.get_setting_typed(conn, "ltm_enabled") is True

    def test_json_key_default(self, conn):
        val = mfm.get_setting_typed(conn, "account_linked")
        assert val == []

    def test_str_key(self, conn):
        mfm.set_setting(conn, "copilot_model", "gpt-4")
        assert mfm.get_setting_typed(conn, "copilot_model") == "gpt-4"


class TestSetSettingTyped:
    def test_unknown_key(self, conn):
        ok, msg = mfm.set_setting_typed(conn, "nosuchkey", "val")
        assert ok is False
        assert "unknown" in msg

    def test_bool_key_true(self, conn):
        ok, _ = mfm.set_setting_typed(conn, "ltm_enabled", True)
        assert ok is True
        assert mfm.get_setting_typed(conn, "ltm_enabled") is True

    def test_bool_key_string(self, conn):
        ok, _ = mfm.set_setting_typed(conn, "ltm_enabled", "false")
        assert ok is True
        assert mfm.get_setting_typed(conn, "ltm_enabled") is False

    def test_bool_key_invalid_string(self, conn):
        ok, msg = mfm.set_setting_typed(conn, "ltm_enabled", "maybe")
        assert ok is False

    def test_json_key_with_list(self, conn):
        ok, _ = mfm.set_setting_typed(conn, "account_linked", ["gh", "gl"])
        assert ok is True
        assert mfm.get_setting_typed(conn, "account_linked") == ["gh", "gl"]

    def test_json_key_with_json_string(self, conn):
        ok, _ = mfm.set_setting_typed(conn, "account_linked", '["x","y"]')
        assert ok is True
        assert mfm.get_setting_typed(conn, "account_linked") == ["x", "y"]

    def test_json_key_invalid_string(self, conn):
        ok, _ = mfm.set_setting_typed(conn, "account_linked", "{bad")
        assert ok is False


class TestFormatSettingValue:
    def test_none(self):
        assert mfm.format_setting_value(None) == "none"

    def test_bool_true(self):
        assert mfm.format_setting_value(True) == "true"

    def test_bool_false(self):
        assert mfm.format_setting_value(False) == "false"

    def test_empty_list(self):
        assert mfm.format_setting_value([]) == "none"

    def test_nonempty_list(self):
        assert mfm.format_setting_value(["a", "b"]) == "a, b"

    def test_dict(self):
        result = mfm.format_setting_value({"key": "val"})
        assert "key" in result
        assert "val" in result

    def test_empty_string(self):
        assert mfm.format_setting_value("") == "none"


class TestSettingsSnapshot:
    def test_returns_dict(self, conn):
        snap = mfm.settings_snapshot(conn)
        assert isinstance(snap, dict)
        assert "account" in snap
        assert "personal_cloud" in snap
        assert "copilot" in snap
        assert "about" in snap
        assert "aesthetics" in snap
        assert "telemetry" in snap


# ──────────────────────────────────────────────
# Cap maps
# ──────────────────────────────────────────────

class TestCapMaps:
    def test_get_empty(self, conn):
        assert mfm.get_cap_map(conn, "cap_by_app") == {}

    def test_set_and_get(self, conn):
        mfm.set_cap_map(conn, "cap_by_app", {"Terminal": 50, "VSCode": 100})
        result = mfm.get_cap_map(conn, "cap_by_app")
        assert result.get("terminal") == 50
        assert result.get("vscode") == 100

    def test_overwrite(self, conn):
        mfm.set_cap_map(conn, "cap_by_app", {"App": 10})
        mfm.set_cap_map(conn, "cap_by_app", {"App": 20})
        assert mfm.get_cap_map(conn, "cap_by_app").get("app") == 20

    def test_get_corrupted_json_returns_empty(self, conn):
        mfm.set_setting(conn, "cap_by_app", "not json")
        assert mfm.get_cap_map(conn, "cap_by_app") == {}


# ──────────────────────────────────────────────
# Allow PDF / Images
# ──────────────────────────────────────────────

class TestAllowPdf:
    def test_default(self, conn):
        assert mfm.get_allow_pdf(conn) is False

    def test_set_true(self, conn):
        mfm.set_allow_pdf(conn, True)
        assert mfm.get_allow_pdf(conn) is True

    def test_set_false(self, conn):
        mfm.set_allow_pdf(conn, True)
        mfm.set_allow_pdf(conn, False)
        assert mfm.get_allow_pdf(conn) is False


class TestAllowImages:
    def test_default(self, conn):
        assert mfm.get_allow_images(conn) is False

    def test_set_true(self, conn):
        mfm.set_allow_images(conn, True)
        assert mfm.get_allow_images(conn) is True

    def test_set_false(self, conn):
        mfm.set_allow_images(conn, True)
        mfm.set_allow_images(conn, False)
        assert mfm.get_allow_images(conn) is False


# ──────────────────────────────────────────────
# Build ANN Index
# ──────────────────────────────────────────────

class TestBuildAnnIndex:
    def test_empty(self, conn):
        rows = conn.execute("SELECT * FROM clip_vectors").fetchall()
        ids, vecs = mfm.build_ann_index(rows)
        assert ids == []
        assert vecs == []

    def test_returns_ids_and_vecs(self, conn):
        conn.execute(
            "INSERT INTO clips (created_at, source_app, window_title, content, hash, pinned, lang) VALUES (?,?,?,?,?,0,'unk')",
            ("2026-01-01", "App", "Win", "test", "abc"),
        )
        conn.commit()
        cid = conn.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        mfm.store_embedding(conn, cid, [0.1, 0.2, 0.3], "hash")
        # build_ann_index expects rows with "id" key — use a JOIN to include it
        rows = conn.execute(
            "SELECT v.*, c.id FROM clip_vectors v JOIN clips c ON c.id = v.clip_id"
        ).fetchall()
        ids, vecs = mfm.build_ann_index(rows)
        assert ids == [cid]
        assert vecs == [[0.1, 0.2, 0.3]]


# ──────────────────────────────────────────────
# Topic groups
# ──────────────────────────────────────────────

class TestTopicGroups:
    def test_groups_by_app_when_untagged(self, populated_db):
        groups = mfm.topic_groups(populated_db, limit_groups=5, per_group=5)
        assert len(groups) >= 1
        app_names = {g["name"] for g in groups}
        assert "Terminal" in app_names or "terminal" in app_names

    def test_groups_by_tag_when_tagged(self, populated_db):
        cids = [r["id"] for r in populated_db.execute("SELECT id FROM clips").fetchall()]
        mfm.assign_tag(populated_db, cids[0], "project-x")
        groups = mfm.topic_groups(populated_db, limit_groups=5, per_group=5)
        group_names = {g["name"] for g in groups}
        assert "project-x" in group_names

    def test_groups_limit(self, populated_db):
        groups = mfm.topic_groups(populated_db, limit_groups=1, per_group=5)
        assert len(groups) <= 1


# ──────────────────────────────────────────────
# Markdown Outline Export
# ──────────────────────────────────────────────

class TestBuildMarkdownOutline:
    def test_empty(self, conn):
        md, count = mfm.build_markdown_outline(conn, since_iso=None)
        assert count == 0
        assert isinstance(md, str)

    def test_with_clips(self, populated_db):
        md, count = mfm.build_markdown_outline(populated_db, since_iso=None)
        assert count == 3
        assert "hello world" in md or "def main()" in md

    def test_with_since_filter(self, populated_db):
        md, count = mfm.build_markdown_outline(populated_db, since_iso="2026-01-20T00:00:00")
        assert count == 0
        md, count = mfm.build_markdown_outline(populated_db, since_iso="2026-01-14T00:00:00")
        assert count == 3

    def test_includes_tags(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        mfm.assign_tag(populated_db, cid, "reviewed")
        md, count = mfm.build_markdown_outline(populated_db, since_iso=None)
        assert "#reviewed" in md or "reviewed" in md


# ──────────────────────────────────────────────
# Language Detection
# ──────────────────────────────────────────────

class TestDetectLanguage:
    def test_fallback_when_langdetect_missing(self):
        lang = mfm.detect_language("Hello world")
        assert lang == "unk"


# ──────────────────────────────────────────────
# Status Snapshot
# ──────────────────────────────────────────────

class TestStatusSnapshot:
    def test_returns_dict(self, conn):
        snap = mfm.status_snapshot(conn)
        assert isinstance(snap, dict)
        assert "paused" in snap
        assert "allow_secrets" in snap
        assert "notify" in snap
        assert "pro_enabled" in snap
        assert "license_type" in snap
        assert "license_status" in snap
        assert "upgrade_url" in snap
        assert "embedder" in snap
        assert "max_bytes" in snap
        assert "max_db_mb" in snap
        assert "cap_by_app" in snap
        assert "cap_by_tag" in snap
        assert "evict_mode" in snap
        assert "count" in snap
        assert "latest" in snap

    def test_reflects_state(self, conn):
        mfm.insert_clip(conn, "status test", "Terminal", "zsh")
        mfm.set_paused(conn, True)
        snap = mfm.status_snapshot(conn)
        assert snap["paused"] is True
        assert snap["count"] == 1


# ──────────────────────────────────────────────
# Helper utilities
# ──────────────────────────────────────────────

class TestReadTextFile:
    def test_reads_existing(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello\nworld")
        assert mfm.read_text_file(f) == "hello\nworld"

    def test_nonexistent(self, tmp_path):
        assert mfm.read_text_file(tmp_path / "nope.txt") is None


class TestCommandExists:
    def test_known_command(self):
        assert mfm.command_exists("echo") is True

    def test_unknown_command(self):
        assert mfm.command_exists("this-command-does-not-exist-42") is False


class TestCopyToClipboard:
    def test_false_on_macos_without_pbcopy_context(self):
        result = mfm.copy_to_clipboard("test text")
        assert isinstance(result, bool)


# ──────────────────────────────────────────────
# Pin / Unpin
# ──────────────────────────────────────────────

class TestPin:
    def test_pin_clip(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        populated_db.execute("UPDATE clips SET pinned = 1 WHERE id = ?", (cid,))
        populated_db.commit()
        row = populated_db.execute("SELECT pinned FROM clips WHERE id = ?", (cid,)).fetchone()
        assert row["pinned"] == 1

    def test_unpin_clip(self, populated_db):
        cid = populated_db.execute("SELECT id FROM clips LIMIT 1").fetchone()["id"]
        populated_db.execute("UPDATE clips SET pinned = 0 WHERE id = ?", (cid,))
        populated_db.commit()
        row = populated_db.execute("SELECT pinned FROM clips WHERE id = ?", (cid,)).fetchone()
        assert row["pinned"] == 0


# ──────────────────────────────────────────────
# Sync — get_sync_interval, write_db_snapshot
# ──────────────────────────────────────────────

class TestGetSyncInterval:
    def test_default(self, conn):
        interval = mfm.get_sync_interval(conn)
        assert interval == 60.0

    def test_set_custom(self, conn):
        mfm.set_setting(conn, "sync_interval", "120")
        assert mfm.get_sync_interval(conn) == 120.0

    def test_minimum_floor(self, conn):
        mfm.set_setting(conn, "sync_interval", "0")
        assert mfm.get_sync_interval(conn) >= 1.0

    def test_invalid_falls_back(self, conn):
        mfm.set_setting(conn, "sync_interval", "not-a-number")
        assert mfm.get_sync_interval(conn) == 60.0


class TestWriteDbSnapshot:
    def test_writes_to_path(self, conn, tmp_path):
        mfm.insert_clip(conn, "snapshot test", "App", "Win")
        dest = tmp_path / "snap.db"
        mfm.write_db_snapshot(dest, source_conn=conn)
        assert dest.exists()
        snap = sqlite3.connect(dest)
        try:
            count = snap.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        finally:
            snap.close()
        assert count == 1

    def test_creates_parent_dirs(self, conn, tmp_path):
        dest = tmp_path / "sub" / "nested" / "snap.db"
        mfm.write_db_snapshot(dest, source_conn=conn)
        assert dest.exists()


# ──────────────────────────────────────────────
# Federation helpers
# ──────────────────────────────────────────────

class TestFederateHelpers:
    def test_cmd_federate_export_in_memory(self, conn):
        mfm.insert_clip(conn, "federated clip", "App", "Win")
        items = mfm.export_items(conn, limit=10)
        assert len(items) == 1
        assert items[0]["content"] == "federated clip"

    def test_cmd_federate_import_in_memory(self, conn):
        items = [
            {"content": "from peer", "source_app": "PeerApp", "created_at": "2026-06-01T00:00:00+00:00"},
        ]
        result = mfm.import_clips(conn, items)
        assert result["inserted"] == 1
