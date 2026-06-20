# my--father-mother API Reference

This document is the customer-facing reference for the local HTTP API exposed by
`python3 main.py serve`. The API is intended for local automations, browser and
editor integrations, private dashboards, and AI sidecars that need access to a
customer's clipboard memory.

## Quick Start

Initialize the local database, start capture, and start the API:

```bash
python3 main.py init
python3 main.py watch --cap 5000
python3 main.py serve --port 8765
```

The default base URL is:

```text
http://127.0.0.1:8765
```

The server tries the requested port and the next two ports if the first port is
busy. For example, `--port 8765` may bind to `8765`, `8766`, or `8767`; check
the startup log for the exact URL.

Verify the server:

```bash
curl -s http://127.0.0.1:8765/health
```

```json
{"ok": true}
```

## Authentication and Network Security

The local API does not require a bearer token, API key, session cookie, or basic
auth header. It is designed to bind to `127.0.0.1` by default and to be consumed
by software running on the same Mac.

Security rules for production customer use:

- Keep the API bound to `127.0.0.1` unless you have put it behind your own
  authenticated reverse proxy, firewall, or private network control.
- Do not expose the API directly to a LAN or the public internet. Responses can
  contain clipboard contents.
- Browser CORS headers allow `Access-Control-Allow-Origin: *`, which is useful
  for local browser extensions and bookmarklets. Treat the API as trusted-local
  infrastructure.
- The only built-in signed endpoint is `POST /webhooks/gumroad`. It verifies an
  `X-Gumroad-Signature` HMAC-SHA256 signature over the raw request body.
- Clipboard secret detection is enabled by default for capture and ingest
  endpoints. Payloads that look like common secrets are rejected unless
  `allow_secrets` is enabled.

Recommended command for local-only serving:

```bash
python3 main.py serve --host 127.0.0.1 --port 8765
```

If you intentionally bind to another interface:

```bash
python3 main.py serve --host 0.0.0.0 --port 8765
```

you are responsible for adding authentication and network access controls
outside my--father-mother.

## Request and Response Conventions

All JSON endpoints return UTF-8 JSON unless noted otherwise.

Common headers:

```http
Content-Type: application/json
Access-Control-Allow-Origin: *
```

Use `Content-Type: application/json` for JSON request bodies:

```bash
curl -s \
  -H 'Content-Type: application/json' \
  -d '{"id": 42, "pinned": true}' \
  http://127.0.0.1:8765/pin
```

Dates are ISO 8601 strings. Stored clip timestamps usually include a UTC offset,
for example `2026-06-19T14:22:10.123456+00:00`.

Boolean query parameters accept `1`, `true`, `yes`, and `on` as true values.
Everything else is treated as false.

Typical error shape:

```json
{"error": "not found"}
```

Common status codes:

| Status | Meaning |
| --- | --- |
| `200` | Request succeeded |
| `204` | CORS preflight succeeded |
| `400` | Invalid input or missing required field |
| `401` | Gumroad webhook signature failed |
| `402` | Requested Pro-only feature without an active license |
| `404` | Endpoint or resource not found |
| `503` | Gumroad webhook secret is not configured |
| `500` | Insert failed unexpectedly |

There is no API-level pagination token. Use `limit`, time filters, app filters,
and tags to narrow result sets.

## Data Models

### Clip

Most retrieval endpoints return a list of clip objects:

```json
{
  "id": 42,
  "created_at": "2026-06-19T14:22:10.123456+00:00",
  "source_app": "Terminal",
  "window_title": "zsh",
  "content": "docker compose up api",
  "pinned": false,
  "title": "Local API command",
  "file_path": null,
  "lang": "en",
  "tags": ["dev", "api"],
  "notes": [
    {
      "note": "Used for local customer demo",
      "created_at": "2026-06-19T14:25:01.000000+00:00"
    }
  ]
}
```

Field notes:

| Field | Type | Notes |
| --- | --- | --- |
| `id` | integer | Stable local clip ID |
| `created_at` | string | ISO timestamp |
| `source_app` | string or null | Captured frontmost app, ingest source, or helper source |
| `window_title` | string or null | Captured title or ingest title |
| `content` | string | Full clip text |
| `pinned` | boolean | Pinned clips are protected by tiered eviction |
| `title` | string or null | Optional short title |
| `file_path` | string or null | Present on `/clip` and federation export responses |
| `lang` | string | Language code or `unk` |
| `tags` | array | Tag names |
| `notes` | array | Note objects with `note` and `created_at` |
| `score` | number | Present only on true semantic search responses |

### Topic Group

`GET /topics` returns groups:

```json
{
  "name": "api",
  "kind": "tag",
  "count": 7,
  "latest": "2026-06-19T14:22:10.123456+00:00",
  "items": []
}
```

`kind` is `tag` when grouped by tag and `app` when the clip has no tags and is
bucketed by source application.

## Endpoint Summary

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` or `/ui` | Built-in web interface |
| `GET` | `/health` | Health check |
| `GET` | `/stats` | Minimal counts and DB size |
| `GET` | `/status` | Operational status and limits |
| `GET` | `/settings` | Customer settings snapshot |
| `GET` | `/config` | Runtime config subset |
| `POST` | `/config` | Update runtime config |
| `GET` | `/recent` | Recent clips with filters |
| `GET` | `/search` | SQLite FTS5 keyword search |
| `GET` | `/semantic_search` | Hash similarity or Pro e5 semantic search |
| `GET` | `/context` | LLM-ready context bundle |
| `GET` | `/topics` | Recent clips grouped by tag/app |
| `GET` | `/clip` | Fetch a single clip by ID |
| `GET` | `/recap` | Recent activity rows |
| `GET` | `/export_md` | Markdown outline export |
| `GET` | `/tags` | List tags |
| `GET` | `/blocklist` | List blocked apps |
| `POST` | `/blocklist` | Add a blocked app |
| `DELETE` | `/blocklist` | Remove a blocked app |
| `POST` | `/pin` | Pin, unpin, or toggle a clip |
| `POST` | `/notes` | Add a note to a clip |
| `POST` | `/pause` | Pause capture |
| `POST` | `/resume` | Resume capture |
| `POST` | `/ingest_url` | Bookmarklet URL ingest |
| `POST` | `/dropper` | Browser extension ingest |
| `GET` | `/federate_export` | Export clips as JSON |
| `POST` | `/federate_import` | Import clips from JSON |
| `POST` | `/helper` | Run rewrite/shorten/extract helper |
| `POST` | `/ai` | Run recall/fill helper over recent clips |
| `POST` | `/purge` | Delete clips by policy |
| `POST` | `/webhooks/gumroad` | Signed Gumroad license webhook |

## Health and Status

### GET /health

Returns a minimal liveness response.

```bash
curl -s http://127.0.0.1:8765/health
```

Response:

```json
{"ok": true}
```

### GET /stats

Returns clip count and database size.

```bash
curl -s http://127.0.0.1:8765/stats | python3 -m json.tool
```

Response:

```json
{
  "count": 1284,
  "latest": "2026-06-19T14:22:10.123456+00:00",
  "db_size_bytes": 7340032,
  "db_size_mb": 7.0
}
```

### GET /status

Returns operational settings, tier state, and local DB usage.

```bash
curl -s http://127.0.0.1:8765/status | python3 -m json.tool
```

Response fields include:

```json
{
  "paused": false,
  "allow_secrets": false,
  "notify": false,
  "pro_enabled": true,
  "embedder": "hash",
  "max_bytes": 16384,
  "max_db_mb": 512,
  "cap_by_app": {},
  "cap_by_tag": {},
  "evict_mode": "fifo",
  "ltm_enabled": true,
  "ml_context_level": "medium",
  "ml_processing_mode": "blended",
  "count": 1284,
  "latest": "2026-06-19T14:22:10.123456+00:00",
  "db_size_mb": 7.0,
  "blocklist_size": 2,
  "sync_target": "",
  "sync_interval": 60.0,
  "license_type": "pro",
  "device_count": 1,
  "upgrade_url": "https://gumroad.com/l/my-father-mother-pro"
}
```

### GET /settings

Returns a customer-facing settings snapshot grouped by account, cloud, copilot,
machine learning, MCP URLs, UI settings, support, and about metadata.

```bash
curl -s http://127.0.0.1:8765/settings | python3 -m json.tool
```

Use this endpoint for dashboards and account/settings screens. For automation
that needs only runtime controls, prefer `/config`.

## Configuration

### GET /config

Returns the runtime configuration subset used by integrations.

```bash
curl -s http://127.0.0.1:8765/config | python3 -m json.tool
```

Response:

```json
{
  "max_bytes": 16384,
  "paused": false,
  "allow_secrets": false,
  "notify": false,
  "max_db_mb": 512,
  "pro_enabled": true,
  "embedder": "hash",
  "cap_by_app": {},
  "cap_by_tag": {},
  "evict_mode": "fifo",
  "sync_target": "",
  "sync_interval": 60.0,
  "ai_recall_cmd": "",
  "ai_fill_cmd": "",
  "helper_rewrite_cmd": "",
  "helper_shorten_cmd": "",
  "helper_extract_cmd": ""
}
```

### POST /config

Updates one or more runtime settings. Send only the keys you want to change.

Accepted fields:

| Field | Type | Notes |
| --- | --- | --- |
| `max_bytes` | integer | Max bytes accepted per clip/ingest |
| `allow_secrets` | boolean | Allow payloads matching built-in secret patterns |
| `notify` | boolean | macOS notification setting |
| `max_db_mb` | integer | Local database size cap |
| `pro_enabled` | boolean/string | Local Pro feature state |
| `license_key` | string | Alias for storing a customer Pro license key |
| `embedder` | string | `hash` or Pro-only `e5-small` |
| `cap_by_app` | object | App-specific clip caps, keys normalized to lowercase |
| `cap_by_tag` | object | Tag-specific clip caps, keys normalized to lowercase |
| `evict_mode` | string | `fifo` or `tiered` |
| `helper_rewrite_cmd` | string | Shell command used by `/helper` kind `rewrite` |
| `helper_shorten_cmd` | string | Shell command used by `/helper` kind `shorten` |
| `helper_extract_cmd` | string | Shell command used by `/helper` kind `extract` |
| `sync_target` | string | Sync target path or `icloud` |
| `sync_interval` | number | Sync interval in seconds, minimum `1.0` |
| `ai_recall_cmd` | string | Shell command used by `/ai` kind `recall` |
| `ai_fill_cmd` | string | Shell command used by `/ai` kind `fill` |
| `gumroad_webhook_secret` | string | Stored secret for webhook signature checks |
| `gumroad_permalink` | string | Gumroad product permalink |
| `gumroad_license_key` | string | Stored customer license key |

Example:

```bash
curl -s \
  -H 'Content-Type: application/json' \
  -d '{"pro_enabled": true, "embedder": "hash", "max_bytes": 32768}' \
  http://127.0.0.1:8765/config | python3 -m json.tool
```

Response:

```json
{
  "ok": true,
  "max_bytes": 32768,
  "pro_enabled": true,
  "embedder": "hash"
}
```

Secret handling:

- `gumroad_webhook_secret` is never echoed back; the response value is `***`.
- Other secret-like values are accepted as provided. Do not send them over an
  exposed network connection.

## Retrieval Endpoints

### GET /recent

Returns recent clips in reverse chronological order.

Query parameters:

| Parameter | Type | Default | Notes |
| --- | --- | --- | --- |
| `limit` | integer | `10` | Max rows |
| `app` | string | none | Exact source app match, case-insensitive |
| `contains` | string | none | SQL substring match against clip content |
| `tag` | string | none | Exact tag match, case-insensitive |
| `pins_only` | boolean | false | Only pinned clips |
| `since` | ISO string | none | Lower timestamp bound |
| `until` | ISO string | none | Upper timestamp bound |
| `hours` | number | none | Overrides `since` with "now minus hours" |

Example:

```bash
curl -s 'http://127.0.0.1:8765/recent?limit=5&app=Terminal&hours=24' \
  | python3 -m json.tool
```

Response:

```json
{
  "items": [
    {
      "id": 42,
      "created_at": "2026-06-19T14:22:10.123456+00:00",
      "source_app": "Terminal",
      "window_title": "zsh",
      "content": "docker compose up api",
      "pinned": false,
      "title": null,
      "lang": "en",
      "tags": [],
      "notes": []
    }
  ]
}
```

### GET /search

Runs SQLite FTS5 keyword search over clip content.

Query parameters:

| Parameter | Type | Default | Notes |
| --- | --- | --- | --- |
| `q` | string | required | FTS5 query text |
| `limit` | integer | `10` | Max rows |
| `app` | string | none | Exact app filter, case-insensitive |
| `tag` | string | none | Exact tag filter, case-insensitive |
| `pins_only` | boolean | false | Only pinned clips |
| `since` | ISO string | none | Lower timestamp bound |
| `until` | ISO string | none | Upper timestamp bound |
| `hours` | number | none | Overrides `since` |

Examples:

```bash
curl -s --get http://127.0.0.1:8765/search \
  --data-urlencode 'q=auth token' \
  --data-urlencode 'limit=10' \
  | python3 -m json.tool
```

FTS5 phrase and boolean queries are supported:

```bash
curl -s --get http://127.0.0.1:8765/search \
  --data-urlencode 'q="refresh token" OR oauth' \
  --data-urlencode 'tag=api' \
  | python3 -m json.tool
```

Response:

```json
{"items": []}
```

### GET /semantic_search

Returns similarity-ranked clips. Hash similarity is available on Free. The
`e5-small` embedder is Pro-only; requesting it without an active license returns
`402` with an `upgrade_url`.

Query parameters:

| Parameter | Type | Default | Notes |
| --- | --- | --- | --- |
| `q` | string | required | Search prompt |
| `limit` | integer | `10` | Max returned rows |
| `pool` | integer | `2000` | Candidate pool scored by vector search |
| `embedder` | string | configured value | `hash` or `e5-small` |
| `app` | string | none | Exact app filter, case-insensitive |
| `tag` | string | none | Exact tag filter, case-insensitive |
| `pins_only` | boolean | false | Only pinned clips |
| `since` | ISO string | none | Lower timestamp bound |
| `until` | ISO string | none | Upper timestamp bound |
| `hours` | number | none | Overrides `since` |

Example:

```bash
curl -s --get http://127.0.0.1:8765/semantic_search \
  --data-urlencode 'q=API authentication flow' \
  --data-urlencode 'limit=5' \
  --data-urlencode 'pool=1000' \
  | python3 -m json.tool
```

Response when vector search is active:

```json
{
  "items": [
    {
      "id": 51,
      "created_at": "2026-06-19T13:58:44.000000+00:00",
      "source_app": "Safari",
      "window_title": "Auth docs",
      "content": "OAuth credential rotation notes...",
      "pinned": true,
      "title": "Auth docs",
      "lang": "en",
      "tags": ["api"],
      "notes": [],
      "score": 0.8123
    }
  ]
}
```

### GET /context

Returns an LLM-ready bundle of recent clips. It uses the same item shape as
`/recent`.

Query parameters:

| Parameter | Type | Default | Notes |
| --- | --- | --- | --- |
| `limit` | integer | `20` | Max rows |
| `app` | string | none | Exact app filter |
| `tag` | string | none | Exact tag filter |
| `hours` | number | none | Lookback window |
| `pins_only` | boolean | false | Only pinned clips |

Example:

```bash
curl -s 'http://127.0.0.1:8765/context?limit=20&tag=customer-a&hours=8' \
  | python3 -m json.tool
```

### GET /topics

Groups recent clips by tag. Untagged clips are grouped by source application.

Query parameters:

| Parameter | Type | Default | Notes |
| --- | --- | --- | --- |
| `limit` | integer | `8` | Max groups |
| `per_group` | integer | `5` | Max clips per group |
| `app` | string | none | Exact app filter before grouping |
| `tag` | string | none | Exact tag filter before grouping |
| `pins_only` | boolean | false | Only pinned clips |
| `since` | ISO string | none | Lower timestamp bound |
| `until` | ISO string | none | Upper timestamp bound |
| `hours` | number | none | Overrides `since` |

Example:

```bash
curl -s 'http://127.0.0.1:8765/topics?limit=6&per_group=3&hours=24' \
  | python3 -m json.tool
```

Response:

```json
{
  "groups": [
    {
      "name": "api",
      "kind": "tag",
      "count": 7,
      "latest": "2026-06-19T14:22:10.123456+00:00",
      "items": []
    }
  ]
}
```

### GET /clip

Fetches a single clip by ID.

Query parameters:

| Parameter | Type | Default | Notes |
| --- | --- | --- | --- |
| `id` | integer | required | Clip ID |

Example:

```bash
curl -s 'http://127.0.0.1:8765/clip?id=42' | python3 -m json.tool
```

`404` response:

```json
{"error": "not found"}
```

### GET /recap

Returns recent activity rows within a minute window. This is a lightweight
activity feed; it does not include tags, pins, or notes.

Query parameters:

| Parameter | Type | Default |
| --- | --- | --- |
| `minutes` | integer | `60` |
| `limit` | integer | `200` |

Example:

```bash
curl -s 'http://127.0.0.1:8765/recap?minutes=90&limit=100' \
  | python3 -m json.tool
```

### GET /export_md

Returns a Markdown outline of recent clips.

Query parameters:

| Parameter | Type | Default |
| --- | --- | --- |
| `hours` | number | none |
| `limit` | integer | `200` |

Example:

```bash
curl -s -D /tmp/mfm-export.headers \
  'http://127.0.0.1:8765/export_md?hours=24&limit=100' \
  -o /tmp/mfm-export.md
```

Response headers include:

```http
Content-Type: text/markdown; charset=utf-8
X-Clip-Count: 37
```

## Tags, Pins, Notes, and Blocklist

### GET /tags

Lists all known tags.

```bash
curl -s http://127.0.0.1:8765/tags
```

```json
{"tags": ["api", "customer-a", "dev"]}
```

### POST /pin

Pins, unpins, or toggles a clip. If `pinned` is omitted, the endpoint toggles
the current state.

Request:

```json
{"id": 42, "pinned": true}
```

Example:

```bash
curl -s \
  -H 'Content-Type: application/json' \
  -d '{"id": 42, "pinned": true}' \
  http://127.0.0.1:8765/pin
```

Response:

```json
{"ok": true, "pinned": true}
```

### POST /notes

Adds a note to a clip.

Request:

```json
{"id": 42, "note": "Customer-facing example for onboarding."}
```

Example:

```bash
curl -s \
  -H 'Content-Type: application/json' \
  -d '{"id": 42, "note": "Customer-facing example for onboarding."}' \
  http://127.0.0.1:8765/notes | python3 -m json.tool
```

Response:

```json
{
  "ok": true,
  "notes": [
    {
      "note": "Customer-facing example for onboarding.",
      "created_at": "2026-06-19T14:25:01.000000+00:00"
    }
  ]
}
```

### GET /blocklist

Lists source applications excluded from capture.

```bash
curl -s http://127.0.0.1:8765/blocklist
```

```json
{"blocklist": ["1Password", "Keychain Access"]}
```

### POST /blocklist

Adds an app to the capture blocklist.

Request:

```json
{"app": "1Password"}
```

`add` is accepted as an alias for `app`.

Example:

```bash
curl -s \
  -H 'Content-Type: application/json' \
  -d '{"app": "1Password"}' \
  http://127.0.0.1:8765/blocklist
```

Response:

```json
{"ok": true, "blocklist": ["1Password"]}
```

### DELETE /blocklist

Removes an app from the capture blocklist.

Request:

```json
{"app": "1Password"}
```

`remove` is accepted as an alias for `app`.

Example:

```bash
curl -s -X DELETE \
  -H 'Content-Type: application/json' \
  -d '{"app": "1Password"}' \
  http://127.0.0.1:8765/blocklist
```

Response:

```json
{"ok": true, "blocklist": []}
```

## Capture Control

### POST /pause

Pauses clipboard capture. Retrieval endpoints remain available.

```bash
curl -s -X POST http://127.0.0.1:8765/pause
```

```json
{"paused": true}
```

### POST /resume

Resumes clipboard capture.

```bash
curl -s -X POST http://127.0.0.1:8765/resume
```

```json
{"paused": false}
```

## Ingest Endpoints

### POST /ingest_url

Saves a bookmarklet-style URL payload as a clip.

Request fields:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `url` | string | yes | URL to save |
| `title` | string | no | Page title |
| `selection` | string | no | Selected page text |

Stored behavior:

- `source_app` is `bookmarklet`.
- `window_title` and `title` are `title` or `url`.
- `file_path` is the URL.
- Duplicate content returns the existing clip ID and records a repeat event.
- Oversized payloads and detected secrets return `400`.

Example:

```bash
curl -s \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/docs","title":"API docs","selection":"Use Bearer tokens."}' \
  http://127.0.0.1:8765/ingest_url
```

Response:

```json
{"ok": true, "id": 77, "existing": false}
```

### POST /dropper

Saves a browser-extension payload as a clip.

Request fields:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `url` | string | no | Active tab URL |
| `title` | string | no | Active tab title |
| `selection` | string | no | Selected text |
| `html` | string | no | Optional selected HTML |
| `app` | string | no | Defaults to `browser-dropper` |

At least one of `url`, `title`, `selection`, or `html` must be non-empty.

Example:

```bash
curl -s \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/pricing","title":"Pricing","selection":"Pro plan details","app":"chrome-extension"}' \
  http://127.0.0.1:8765/dropper
```

Response:

```json
{"ok": true, "id": 78, "existing": false}
```

## Federation and Export

### GET /federate_export

Exports clips as JSON for import into another local instance.

Query parameters:

| Parameter | Type | Default | Notes |
| --- | --- | --- | --- |
| `limit` | integer | `200` | Max rows |
| `app` | string | none | Exact app filter |
| `tag` | string | none | Exact tag filter |
| `pins_only` | boolean | false | Only pinned clips |
| `hours` | number | none | Lookback window |

Example:

```bash
curl -s 'http://127.0.0.1:8765/federate_export?limit=500&tag=project-x' \
  -o mfm-project-x.json
```

Response shape:

```json
{"items": []}
```

### POST /federate_import

Imports clips from a JSON export.

Request:

```json
{"items": []}
```

Example:

```bash
curl -s \
  -H 'Content-Type: application/json' \
  -d @mfm-project-x.json \
  http://127.0.0.1:8765/federate_import | python3 -m json.tool
```

Response:

```json
{
  "ok": true,
  "inserted": 25,
  "existing": 3,
  "failed": 0
}
```

## Helper and AI Endpoints

Helper endpoints execute locally configured shell commands. They are powerful
and should only be enabled with trusted commands.

### POST /helper

Runs a configured helper over a single clip. Supported kinds are `rewrite`,
`shorten`, and `extract`.

Before using this endpoint, configure the matching command:

```bash
python3 main.py config --set helper_rewrite_cmd '/path/to/rewrite-script'
```

Request fields:

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `kind` | string | required | `rewrite`, `shorten`, or `extract` |
| `id` | integer | latest clip | Source clip ID |
| `timeout` | number | `8.0` | Seconds |

Example:

```bash
curl -s \
  -H 'Content-Type: application/json' \
  -d '{"kind":"rewrite","id":42,"timeout":10}' \
  http://127.0.0.1:8765/helper | python3 -m json.tool
```

Success response:

```json
{
  "ok": true,
  "id": 99,
  "message": "saved rewrite of #42 as #99",
  "output": "Rewritten text..."
}
```

The helper receives the source clip on stdin and these environment variables:

| Variable | Meaning |
| --- | --- |
| `MFM_CLIP_ID` | Source clip ID |
| `MFM_SOURCE_APP` | Source application |
| `MFM_TITLE` | Clip title |
| `MFM_KIND` | Helper kind |

### POST /ai

Runs a configured AI helper over a recent clip bundle. Supported kinds are
`recall` and `fill`.

Before using this endpoint, configure the matching command:

```bash
python3 main.py config --set ai_recall_cmd '/path/to/recall-script'
python3 main.py config --set ai_fill_cmd '/path/to/fill-script'
```

Request fields:

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `kind` | string | required | `recall` or `fill` |
| `hours` | number | none | Optional lookback window |
| `limit` | integer | `50` | Max clips sent to helper |
| `timeout` | number | `12.0` | Seconds |
| `save` | boolean | false | Save helper output as a new clip |

Example:

```bash
curl -s \
  -H 'Content-Type: application/json' \
  -d '{"kind":"recall","hours":4,"limit":50,"save":true}' \
  http://127.0.0.1:8765/ai | python3 -m json.tool
```

Success response:

```json
{
  "ok": true,
  "id": 100,
  "message": "helper output ok",
  "output": "Summary..."
}
```

The helper receives a text bundle on stdin and these environment variables:

| Variable | Meaning |
| --- | --- |
| `MFM_HELPER` | `ai_recall_cmd` or `ai_fill_cmd` |
| `MFM_HELPER_LIMIT` | Limit used for the request |
| `MFM_HELPER_HOURS` | Hours used for the request, if any |

## Destructive Operations

### POST /purge

Deletes clips. Use carefully; this endpoint is not reversible unless you have a
backup.

Request fields:

| Field | Type | Notes |
| --- | --- | --- |
| `app` | string | Optional exact app filter |
| `tag` | string | Optional exact tag filter |
| `older_than_days` | integer | Delete rows older than N days |
| `keep_last` | integer | Keep newest N rows matching filters, delete the rest |
| `all` | boolean | Delete all clips |

Examples:

```bash
curl -s \
  -H 'Content-Type: application/json' \
  -d '{"tag":"scratch","older_than_days":30}' \
  http://127.0.0.1:8765/purge
```

```json
{"purged": 18}
```

Delete everything:

```bash
curl -s \
  -H 'Content-Type: application/json' \
  -d '{"all": true}' \
  http://127.0.0.1:8765/purge
```

## Gumroad License Webhook

### POST /webhooks/gumroad

Receives Gumroad sale/license events, verifies the request signature, stores the
license key, and performs a best-effort online validation.

This is the only endpoint with built-in request authentication.

Configuration:

```bash
python3 main.py config --set gumroad_permalink myfathermother
python3 main.py config --set gumroad_webhook_secret 'replace-with-shared-secret'
```

Prefer the `GUMROAD_WEBHOOK_SECRET` environment variable for production so the
secret does not need to live in SQLite:

```bash
export GUMROAD_WEBHOOK_SECRET='replace-with-shared-secret'
python3 main.py serve --port 8765
```

Signature:

- Header: `X-Gumroad-Signature`
- Algorithm: HMAC-SHA256 hex digest of the raw request body
- Prefixes such as `sha256=` are tolerated

Accepted body formats:

- `application/x-www-form-urlencoded`
- `application/json`

Required body field:

| Field | Required | Notes |
| --- | --- | --- |
| `license_key` | yes | Stored as `gumroad_license_key` |

Optional stored fields:

| Field | Stored setting |
| --- | --- |
| `email` | `gumroad_email` |
| `product_permalink` | `gumroad_permalink` |
| `sale_id` | `gumroad_sale_id` |
| `product_id` | `gumroad_product_id` |

Successful response:

```json
{
  "ok": true,
  "stored": true,
  "license_valid": true
}
```

Invalid signature response:

```json
{"error": "invalid signature"}
```

Webhook test helper:

```bash
body='license_key=TEST-LICENSE&email=customer@example.com&product_permalink=myfathermother'
sig=$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$GUMROAD_WEBHOOK_SECRET" -hex | awk '{print $2}')

curl -s \
  -H "X-Gumroad-Signature: $sig" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data "$body" \
  http://127.0.0.1:8765/webhooks/gumroad
```

## Web Interface

`GET /` and `GET /ui` return the built-in web UI. It uses the same local API
endpoints documented above for status, recent clips, search, semantic search,
topics, tags, notes, pins, and clip copy actions.

```bash
open http://127.0.0.1:8765/
```

## MCP Bridge

The MCP bridge is a separate lightweight server:

```bash
python3 scripts/mcp_server.py
```

Default MCP base URL:

```text
http://127.0.0.1:39300
```

MCP endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/model_context_protocol/2025-03-26/mcp` | Resource listing |
| `GET` | `/mcp/recent` | Recent clips JSON |
| `GET` | `/mcp/context` | Context bundle |
| `GET` | `/mcp/search` | FTS search |
| `GET` | `/model_context_protocol/2024-11-05/sse` | SSE heartbeat |
| `GET` | `/health` | Health check |

The MCP bridge follows the same localhost security model: it does not implement
API keys and should not be exposed directly to untrusted networks.

## Customer Integration Examples

### Fetch the latest clip

```bash
curl -s 'http://127.0.0.1:8765/recent?limit=1' | python3 -m json.tool
```

### Search and copy the first match to the macOS clipboard

```bash
curl -s --get http://127.0.0.1:8765/search \
  --data-urlencode 'q=customer onboarding' \
  --data-urlencode 'limit=1' \
  | python3 -c 'import json,sys,subprocess; d=json.load(sys.stdin); subprocess.run(["pbcopy"], input=(d["items"][0]["content"] if d["items"] else ""), text=True)'
```

### Save the current browser page with a bookmarklet

Create a browser bookmark with this URL:

```javascript
javascript:(async()=>{await fetch('http://127.0.0.1:8765/ingest_url',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:location.href,title:document.title,selection:String(getSelection())})});})();
```

### Build an LLM context file

```bash
curl -s 'http://127.0.0.1:8765/context?limit=30&hours=4' \
  | python3 -m json.tool > mfm-context.json
```

### Export and import between two local profiles

```bash
curl -s 'http://127.0.0.1:8765/federate_export?limit=1000' -o mfm-export.json

curl -s \
  -H 'Content-Type: application/json' \
  -d @mfm-export.json \
  http://127.0.0.1:8765/federate_import
```

## Operational Notes

- The API shares the same SQLite database as the CLI and watcher:
  `~/.my-father-mother/mfm.db`.
- The HTTP server uses threaded request handling and SQLite WAL mode.
- There is no built-in rate limiting.
- Retrieval endpoints return full clip content. Use filters and `limit` to keep
  responses small.
- `semantic_search` only scores clips that already have vectors for the selected
  embedder. Free hash vectors are created by default. e5 vectors are created
  only for clips ingested after enabling Pro and `embedder=e5-small`, unless you
  re-ingest older content.
- Ingest endpoints enforce `max_bytes` and secret detection. Increase
  `max_bytes` or enable `allow_secrets` only when the local security tradeoff is
  acceptable.
- Back up the database before using `/purge` or large federation imports.
