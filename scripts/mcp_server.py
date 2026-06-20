#!/usr/bin/env python3
"""
Minimal MCP-style server that exposes my--father-mother context over two endpoints:
- /model_context_protocol/2025-03-26/mcp   (resource listing + metadata)
- /model_context_protocol/2024-11-05/sse   (simple SSE heartbeat with clip count + latest)
- /mcp/recent, /mcp/context, /mcp/search   (JSON responses)

This is a lightweight bridge, not a full MCP spec implementation. Point clients at:
  http://127.0.0.1:39300/model_context_protocol/2025-03-26/mcp
and use the resource URIs for context-aware tools (Cursor, Copilot, etc.).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Import app internals
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import main as mfm  # type: ignore

HOST = os.environ.get("MFM_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MFM_MCP_PORT", "39300"))


def json_bytes(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def recent_items(limit: int = 20) -> list[dict]:
    conn = mfm.connect_db()
    mfm.init_db(conn)
    rows, tag_map = mfm.filtered_rows(
        conn,
        limit,
        app=None,
        contains=None,
        tag=None,
        pins_only=False,
        since_iso=None,
        until_iso=None,
    )
    notes_map = mfm.notes_for_clips(conn, [row["id"] for row in rows])
    items = []
    for row in rows:
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
            )
        )
    return items


def context_items(
    limit: int = 20,
    app: str | None = None,
    tag: str | None = None,
    hours: float | None = None,
    pins_only: bool = False,
) -> list[dict]:
    conn = mfm.connect_db()
    mfm.init_db(conn)
    return mfm.context_bundle(
        conn, app=app, tag=tag, limit=limit, hours=hours, pins_only=pins_only
    )


def search_items(
    q: str,
    limit: int = 20,
    app: str | None = None,
    tag: str | None = None,
    pins_only: bool = False,
) -> list[dict]:
    conn = mfm.connect_db()
    mfm.init_db(conn)
    clauses = ["clips_fts MATCH ?"]
    params: list = [q]
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
    tag_map = mfm.tags_for_clips(conn, [row["id"] for row in rows_db])
    notes_map = mfm.notes_for_clips(conn, [row["id"] for row in rows_db])
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
                lang=row["lang"],
                tags=tag_map.get(row["id"], []),
                notes=notes_map.get(row["id"], []),
            )
        )
    return items


class MCPHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, status: int, payload: dict) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        qs = urllib.parse.parse_qs(query)
        if path == "/health":
            self._send_json(200, {"ok": True})
            return
        if path == "/model_context_protocol/2025-03-26/mcp":
            resources = [
                {
                    "uri": f"http://{HOST}:{PORT}/mcp/recent",
                    "name": "recent",
                    "description": "Recent clips from my--father-mother",
                },
                {
                    "uri": f"http://{HOST}:{PORT}/mcp/context",
                    "name": "context",
                    "description": "Context bundle with tags/notes (filter by app/tag/hours/pins)",
                },
                {
                    "uri": f"http://{HOST}:{PORT}/mcp/search",
                    "name": "search",
                    "description": "FTS search over clips (q param)",
                },
                {
                    "uri": f"http://{HOST}:{PORT}/model_context_protocol/2024-11-05/sse",
                    "name": "sse",
                    "description": "Heartbeat SSE with count/latest",
                },
            ]
            self._send_json(200, {"resources": resources, "version": "2025-03-26"})
            return
        if path == "/mcp/recent":
            try:
                limit = int(qs.get("limit", [20])[0])
            except Exception:
                limit = 20
            items = recent_items(limit=limit)
            self._send_json(200, {"items": items})
            return
        if path == "/mcp/context":
            try:
                limit = int(qs.get("limit", [20])[0])
            except Exception:
                limit = 20
            app = qs.get("app", [None])[0]
            tag = qs.get("tag", [None])[0]
            pins_only = qs.get("pins_only", ["false"])[0].lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            hours = None
            if "hours" in qs:
                try:
                    hours = float(qs["hours"][0])
                except Exception:
                    hours = None
            items = context_items(
                limit=limit, app=app, tag=tag, hours=hours, pins_only=pins_only
            )
            self._send_json(200, {"items": items})
            return
        if path == "/mcp/search":
            q = qs.get("q", [""])[0]
            try:
                limit = int(qs.get("limit", [20])[0])
            except Exception:
                limit = 20
            app = qs.get("app", [None])[0]
            tag = qs.get("tag", [None])[0]
            pins_only = qs.get("pins_only", ["false"])[0].lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            items = search_items(q, limit=limit, app=app, tag=tag, pins_only=pins_only)
            self._send_json(200, {"items": items})
            return
        if path == "/model_context_protocol/2024-11-05/sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            for _ in range(60):  # ~60 seconds of heartbeats
                stats = mfm.status_snapshot(mfm.connect_db())
                payload = json.dumps(
                    {"count": stats.get("count"), "latest": stats.get("latest")}
                )
                try:
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    break
                time.sleep(1.0)
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        self._send_json(404, {"error": "not found"})


def serve() -> None:
    server = HTTPServer((HOST, PORT), MCPHandler)
    print(
        f"[mcp] serving on http://{HOST}:{PORT}/model_context_protocol/2025-03-26/mcp (Ctrl+C to stop)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[mcp] shutting down")
        server.server_close()


if __name__ == "__main__":
    serve()
