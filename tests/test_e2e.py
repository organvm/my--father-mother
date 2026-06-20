import os
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

import main as mfm


def test_end_to_end_flow(tmp_path, monkeypatch, capsys):
    """
    End-to-end test exercising the main user flow:
    1. Initialize DB
    2. Start watcher (mocked clipboard & loop termination)
    3. Run search to verify capture
    4. Tag the clip
    5. Retrieve by tag
    """
    # 1. Isolate the database to tmp_path
    monkeypatch.setattr(mfm, "DB_DIR", tmp_path)
    monkeypatch.setattr(mfm, "DB_PATH", tmp_path / "mfm.db")

    # Override ICLOUD_DRIVE_DIR to avoid syncing issues during tests
    monkeypatch.setattr(mfm, "ICLOUD_DRIVE_DIR", tmp_path / "CloudDocs")

    # 2. Init DB
    assert mfm.main(["init"]) == 0
    assert (tmp_path / "mfm.db").exists()

    # 3. Mock clipboard and active app
    def mock_check_output(args, **kwargs):
        if args[0] == "pbpaste":
            return b"Integration test snippet!"
        elif args[0] == "osascript":
            return b"MockApp"
        raise ValueError(f"Unexpected subprocess call: {args}")

    monkeypatch.setattr(mfm.subprocess, "check_output", mock_check_output)

    # 4. Mock time.sleep to break the infinite watch loop after one iteration
    def mock_sleep(seconds):
        raise KeyboardInterrupt("Stop loop")

    monkeypatch.setattr(mfm.time, "sleep", mock_sleep)

    # 5. Run the watcher
    try:
        mfm.main(["watch", "--interval", "0.1"])
    except KeyboardInterrupt:
        # Loop correctly terminated
        pass

    # Clear captured output from watch
    capsys.readouterr()

    # 6. Verify capture using 'search'
    assert mfm.main(["search", "Integration"]) == 0
    out, err = capsys.readouterr()
    assert "Integration test snippet!" in out
    assert "MockApp" in out

    # 7. Get the clip ID from the database to test tagging
    conn = mfm.connect_db()
    row = conn.execute("SELECT id FROM clips LIMIT 1").fetchone()
    assert row is not None, "Clip was not saved to database"
    clip_id = row["id"]
    conn.close()

    # 8. Add a tag
    assert mfm.main(["tags", "--id", str(clip_id), "--add", "e2e-tag"]) == 0
    out, err = capsys.readouterr()
    assert "e2e-tag" in out

    # 9. Verify retrieval by tag via 'recent'
    assert mfm.main(["recent", "--tag", "e2e-tag"]) == 0
    out, err = capsys.readouterr()
    assert "Integration test snippet!" in out
    assert "MockApp" in out

    # 10. Verify stats command
    assert mfm.main(["stats"]) == 0
    out, err = capsys.readouterr()
    assert "clips: 1" in out

    # 11. Test deletion
    assert mfm.main(["delete", "--id", str(clip_id)]) == 0

    # 12. Verify deletion
    assert mfm.main(["recent"]) == 0
    out, err = capsys.readouterr()
    assert "Integration test snippet!" not in out
