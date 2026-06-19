[![ORGAN-III: Ergon](https://img.shields.io/badge/ORGAN--III-Ergon-1b5e20?style=flat-square)](https://github.com/organvm-iii-ergon)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](https://opensource.org/licenses/MIT)
[![Local Only](https://img.shields.io/badge/Data-Local%20Only-2e7d32?style=flat-square)](https://github.com/organvm-iii-ergon/my--father-mother)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-6a1b9a?style=flat-square)](https://github.com/organvm-iii-ergon/my--father-mother#mcp-bridge)

# my--father-mother

[![CI](https://github.com/organvm-iii-ergon/my--father-mother/actions/workflows/ci.yml/badge.svg)](https://github.com/organvm-iii-ergon/my--father-mother/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-pending-lightgrey)](https://github.com/organvm-iii-ergon/my--father-mother)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/organvm-iii-ergon/my--father-mother/blob/main/LICENSE)
[![Organ III](https://img.shields.io/badge/Organ-III%20Ergon-F59E0B)](https://github.com/organvm-iii-ergon)
[![Status](https://img.shields.io/badge/status-active-brightgreen)](https://github.com/organvm-iii-ergon/my--father-mother)
[![Python](https://img.shields.io/badge/lang-Python-informational)](https://github.com/organvm-iii-ergon/my--father-mother)


**Local-only clipboard long-term memory for macOS.**

Your clipboard is a river of context — code snippets, URLs, API keys, meeting notes, half-formed ideas — and it all vanishes the moment you copy something new. my--father-mother sits beside that river and remembers. It listens to the same clipboard stream your system sees, stores every snippet in a local SQLite database with full-text and semantic search, and gives it back to you by keyword, meaning, or recency. No cloud. No telemetry. No account. Everything stays in `~/.my-father-mother/mfm.db` on your machine.

The tool is built around a dual-persona metaphor drawn from alchemical and mythological imagery:

- **Mother (Moon)** — the watcher. She runs the capture loop, records metadata (timestamp, frontmost app, window title), deduplicates, prunes to cap, and ingests files. She is the receptive, lunar function: ever-listening, ever-recording.
- **Father (Sun)** — the retriever. He runs search, recent, export, stats, configuration, and index maintenance. He is the active, solar function: queried on demand, always ready with answers.

This division is not merely cosmetic. Every CLI command, every log line, every API endpoint is assigned to one persona. The architecture enforces a clean separation between data ingestion (Mother) and data retrieval (Father), making the system easier to reason about, extend, and debug.

---

## Table of Contents

- [Why This Exists](#why-this-exists)
- [Technical Architecture](#technical-architecture)
- [Installation and Quick Start](#installation-and-quick-start)
- [Core Features](#core-features)
- [CLI Reference](#cli-reference)
- [HTTP API and Web UI](#http-api-and-web-ui)
- [MCP Bridge](#mcp-bridge)
- [Browser and IDE Integration](#browser-and-ide-integration)
- [LaunchAgents and Autostart](#launchagents-and-autostart)
- [Configuration](#configuration)
- [Security Model](#security-model)
- [Cross-Organ Context](#cross-organ-context)
- [Roadmap](#roadmap)
- [Related Work](#related-work)
- [Contributing](#contributing)
- [License](#license)
- [Author](#author)

---

## Why This Exists

Clipboard managers exist. Paste (macOS), CopyQ, Ditto (Windows) — they all maintain a scrollable history. But they are optimized for *recent recall*, not *long-term memory*. They do not let you search semantically ("that thing about authentication I copied last Tuesday"), they do not let you tag and annotate clips, they do not expose an API for programmatic access, and they do not bridge into the emerging Model Context Protocol ecosystem.

my--father-mother fills a specific gap: it turns transient clipboard data into a durable, searchable, programmable knowledge layer that sits entirely on your local machine. It is designed for developers, researchers, and knowledge workers who want to treat their clipboard as a first-class data source rather than a volatile buffer.

The core thesis is straightforward: **if you copied it, you cared about it, and you might need it again.** The tool ensures you can find it when you do.

---

## Technical Architecture

### Single-File Runtime

The entire application lives in `main.py` — approximately 4,900 lines of Python 3.10+ using only the standard library for its core functionality. This is a deliberate architectural choice. A single-file CLI tool has zero dependency friction: you clone the repo, run `python3 main.py init`, and you are operational. No virtual environments, no package resolution, no build step.

Optional dependencies (`sentence-transformers`, `langdetect`) unlock enhanced semantic search and language detection but are never required for core operation.

### Storage Layer

```
~/.my-father-mother/
  mfm.db          # SQLite database (WAL mode)
```

The database uses SQLite in WAL (Write-Ahead Logging) mode for concurrent read/write access. The schema includes:

| Table | Purpose |
|-------|---------|
| `clips` | Primary clip storage: content, timestamp, source app, window title, pinned flag, title, language code |
| `clips_fts` | FTS5 virtual table for full-text keyword search |
| `clip_vectors` | 128-dimensional embedding vectors for semantic search |
| `clip_tags` | Many-to-many join table for clip-tag relationships |
| `tags` | Tag name registry |
| `clip_notes` | Per-clip session notes (user annotations) |
| `clip_events` | Capture history: repeat sightings, lifecycle events |
| `settings` | Key-value configuration store |
| `blocklist` | App names excluded from capture |
| `copilot_chats` | Copilot conversation history |

### Embedding System

Two embedding modes are available, switchable at runtime via `config --set embedder`:

**Hash embeddings (default):** Fast, zero-dependency 128-dimensional vectors generated from character n-gram hashing. These provide reasonable similarity matching for exact and near-exact content without requiring any model downloads. This is the default because it works instantly with no setup.

**E5-small embeddings (opt-in):** Semantic embeddings using the `intfloat/e5-small-v2` model from the `sentence-transformers` library. These provide genuine meaning-based similarity — "authentication token refresh" will match "OAuth credential rotation" even when no keywords overlap. Enable with:

```bash
pip install sentence-transformers langdetect
python3 main.py config --set embedder e5-small
```

Existing clips retain their stored vectors when you switch modes. New clips are embedded using whichever mode is active. Both modes produce 128-dimensional vectors stored in the `clip_vectors` table, so the search interface is identical regardless of backend.

### Capture Pipeline

The Mother watcher loop follows this pipeline on each tick (default: 1-second interval):

1. **Read clipboard** via `pbpaste` (macOS system utility)
2. **Deduplication check** — skip if content hash matches the last captured clip
3. **Size check** — skip if content exceeds `max_bytes` (default 16 KB, configurable)
4. **Secret detection** — skip clips matching common secret patterns (AWS keys, GitHub PATs, private keys, Slack tokens) unless `allow_secrets` is enabled
5. **Blocklist check** — skip if the frontmost app is in the blocklist
6. **Metadata extraction** — capture frontmost app name and window title via `osascript`
7. **Persistence** — insert into `clips` table, update FTS5 index, generate and store embedding vector
8. **Smart hooks** — optionally run user-provided `auto_summary_cmd` and `auto_tag_cmd` shell commands
9. **Cap enforcement** — if clip count exceeds cap, evict oldest (FIFO) or oldest non-pinned (tiered) clips
10. **Notification** — optionally fire a macOS toast via `osascript` displaying the saved clip

### HTTP Server

The `serve` command starts a local HTTP server (default port 8765) that exposes the full retrieval API plus a minimal web UI at the root path. The server tries up to 3 consecutive ports if the requested port is busy. Endpoints mirror CLI commands: `/recent`, `/search`, `/semantic_search`, `/context`, `/clip`, `/status`, `/pin`, `/tags`, `/dropper`, `/ingest_url`, `/recap`, `/topics`, `/federate_export`, `/federate_import`.

### MCP Bridge

A separate lightweight server (`scripts/mcp_server.py`, port 39300) implements a subset of the Model Context Protocol, exposing clipboard context as MCP resources for LLM tool integration with editors like Cursor, Copilot, and other MCP-aware clients.

---

## Installation and Quick Start

### Prerequisites

- macOS with `pbpaste` and `osascript` available (ships with every Mac)
- Python 3.10 or later (stdlib only for core features)

### Setup

```bash
# Clone the repository
git clone https://github.com/organvm-iii-ergon/my--father-mother.git
cd my--father-mother

# Initialize the database
python3 main.py init
# => creates ~/.my-father-mother/mfm.db

# Start the clipboard watcher (Mother)
python3 main.py watch --cap 5000
# => [mother|moon] watching clipboard (cap=5000, interval=1.0s)

# In another terminal, query your clips (Father)
python3 main.py recent --limit 10
python3 main.py search "docker env" --limit 5
```

### Optional Enhancements

```bash
# Semantic search and language detection
pip install sentence-transformers langdetect
python3 main.py config --set embedder e5-small

# PDF and image ingestion
brew install poppler tesseract
python3 main.py config --set allow_pdf true
python3 main.py config --set allow_images true

# macOS toast notifications on clip save/skip
python3 main.py config --set notify true
```

### Dual-Pane Tmux Session

The recommended way to run my--father-mother during a work session:

```bash
./scripts/mfm-dual.sh
```

This opens a tmux session with Mother (watcher, teal) on the left pane and Father (interactive shell, gold) on the right pane. The color coding reinforces the persona metaphor — moon-teal for the receptive watcher, sun-gold for the active retriever.

---

## Core Features

### Clipboard Capture and Deduplication

Mother captures clipboard text at a configurable interval (default 1 second), recording the timestamp, frontmost application, and window title for each clip. Identical clips are deduplicated by content hash. Repeat sightings are logged in the capture history for forensic review without bloating the primary store.

### Full-Text Search (FTS5)

Every clip is indexed in an SQLite FTS5 virtual table. Search queries support standard FTS5 syntax including boolean operators, phrase matching, and prefix queries:

```bash
python3 main.py search "docker AND env" --limit 10
python3 main.py search "auth token" --app Terminal --since "2025-01-01"
```

### Semantic Search

Beyond keyword matching, semantic search finds clips by meaning. With hash embeddings (default), this catches near-duplicates and variations. With e5-small embeddings, it performs genuine conceptual matching:

```bash
python3 main.py semantic-search "API authentication flow" --limit 5
python3 main.py related --id 42 --limit 5  # find neighbors of a specific clip
```

### Tags, Pins, and Notes

Clips can be tagged, pinned (protected from eviction), and annotated with session notes:

```bash
python3 main.py tags --id 12 --add projectx --add auth
python3 main.py pin --id 12 --on
python3 main.py note --id 12 --text "this is the OAuth config from the staging env"
```

Tags drive the topic-bucketing system and per-tag cap enforcement. Pins protect important clips from eviction when the database reaches its size cap.

### Context Bundles

The `context` command dumps a structured bundle of recent clips — filtered by app, tag, time window, or pin status — formatted for consumption by LLM sidecars and AI coding assistants:

```bash
python3 main.py context --app "Slack" --limit 15 --hours 4
```

This is the bridge between clipboard history and AI-assisted workflows: feed your recent context into a prompt without manual copy-paste assembly.

### Topic Bucketing

The `topics` command groups recent clips by tag (or by source app when untagged), providing a quick overview of your recent clipboard activity organized by domain:

```bash
python3 main.py topics --limit 6 --per-group 3 --since-hours 24
```

### Helper Transforms

Configurable shell-out helpers let you pipe clips through your own scripts for rewriting, shortening, or extracting structured data:

```bash
python3 main.py config --set helper_rewrite_cmd "your-rewrite-script"
python3 main.py rewrite --id 12 --show
python3 main.py shorten --show  # operates on latest clip
python3 main.py extract --id 42 --timeout 12
```

Results are saved as new tagged clips, preserving the original.

### File and Media Ingestion

Beyond clipboard capture, Mother can ingest files directly:

```bash
python3 main.py ingest-file --path ~/Desktop/snippet.txt
python3 main.py ingest-transcript --path ~/meeting.txt  # auto-tagged meeting/transcript
python3 main.py watch-inbox --dir ~/.my-father-mother/inbox --interval 5
python3 main.py ingest-file --path ~/doc.pdf --allow-pdf  # requires pdftotext
python3 main.py ingest-image --path ~/pic.png --allow-images  # requires tesseract OCR
```

### Markdown Journal Export

Export your clipboard history as a structured Markdown outline, grouped by date, application, and tags — ready to drop into Obsidian, Logseq, or any daily-notes workflow:

```bash
python3 main.py export-md --hours 24 --path /tmp/clips.md
```

### Federation

Simple multi-device clipboard sharing via JSON export/import:

```bash
# Export recent clips for another device
python3 main.py federate-export --limit 200 --since-hours 24 --path /tmp/peer.json

# Import from a peer's export or live serve endpoint
python3 main.py federate-import --url http://peer:8765/federate_export

# Push directly to a peer
python3 main.py federate-push --url http://peer:8765/federate_import --limit 100
```

Federation uses content-hash deduplication to merge without duplicates.

### Backup and Sync

```bash
python3 main.py backup --path ~/mfm-backup.db
python3 main.py restore --path ~/mfm-backup.db
python3 main.py sync --mode push --target ~/Library/Mobile\ Documents/com~apple~CloudDocs/mfm.db
```

---

## CLI Reference

The CLI is organized by persona. Every command is assigned to either Mother (capture/ingestion) or Father (retrieval/configuration).

### Mother (Moon) Commands — Capture and Ingestion

| Command | Description |
|---------|-------------|
| `init` | Create the database at `~/.my-father-mother/mfm.db` |
| `watch` | Start the clipboard capture loop (`--cap`, `--interval`, `--notify`) |
| `pause` | Pause, resume, or toggle capture (`--on`, `--off`, `--toggle`) |
| `ingest-file` | Ingest a single file into the store |
| `ingest-transcript` | Ingest a meeting transcript with auto-tagging |
| `watch-inbox` | Watch a directory and ingest new files as they appear |

### Father (Sun) Commands — Retrieval and Management

| Command | Description |
|---------|-------------|
| `recent` | List recent clips with filters (`--limit`, `--app`, `--tag`, `--since-hours`, `--pins-only`) |
| `search` | Full-text keyword search (`--limit`, `--app`, `--tag`, `--since`, `--until`) |
| `semantic-search` | Meaning-based search using embeddings |
| `related` | Find semantic neighbors of a specific clip |
| `show` | Print a specific clip by ID |
| `copy` | Copy a stored clip back to the system clipboard |
| `history` | View capture history for a clip |
| `stats` | Show counts, database size, latest timestamp |
| `status` | Show runtime status (paused, notify, limits, DB size) |
| `settings` | Settings parity snapshot |
| `config` | Get/set configuration values |
| `tags` | Add, remove, or list tags on clips |
| `note` | Append or list session notes for a clip |
| `pin` | Pin or unpin clips (protect from eviction) |
| `delete` | Remove entries by ID |
| `purge` | Delete by age, app, tag, or keep-last-N |
| `export` | Export clips to JSON |
| `export-md` | Export as Markdown journal outline |
| `recap` | Summarize recent clips by app over a time window |
| `topics` | Bucket recent clips by tag or app |
| `palette` | Interactive picker with copy (`--query`, `--limit`) |
| `context` | Dump a context bundle for LLM sidecars |
| `rewrite` / `shorten` / `extract` | Run helper transforms on a clip |
| `recall` / `fill` | Run AI helper scripts over recent clips (opt-in) |
| `blocklist` | Add, remove, or list blocked apps |
| `sync` | Push/pull database to a path |
| `federate-export` / `federate-import` / `federate-push` | Multi-device handoff |
| `backup` / `restore` | Database backup and restore |
| `serve` | Start the local HTTP API and web UI |
| `install-launchagent` | Install or remove the login LaunchAgent |
| `personas` | Print the role/domain map |
| `copilot` | Manage copilot settings and chats |
| `ml` | Manage machine-learning and LTM settings |
| `mcp-urls` | Print MCP server URLs |
| `about` | Show app and runtime details |

Run `python3 main.py --help` for full option details on every command.

---

## HTTP API and Web UI

Start the server:

```bash
python3 main.py serve --port 8765
```

### Web Interface

Navigate to `http://127.0.0.1:8765/` for a minimal but functional web UI with search, recent clips, topic bucketing, tag/pin management, and a status indicator showing paused/active state plus notification and secret-storage flags. The UI includes keyboard shortcuts (Enter to search, Cmd/Ctrl+F to focus the search input) and optional auto-refresh.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/recent` | Recent clips (params: `limit`, `app`, `tag`, `pins_only`, `since`, `until`) |
| GET | `/search` | FTS5 keyword search (params: `q`, `limit`, `app`, `tag`) |
| GET | `/semantic_search` | Semantic search (params: `q`, `limit`) |
| GET | `/context` | Context bundle for LLM sidecars (params: `limit`, `app`, `tag`, `hours`, `pins_only`) |
| GET | `/clip` | Single clip by ID (params: `id`) |
| GET | `/status` | Runtime status (paused, notify, DB size, caps) |
| GET | `/recap` | Recent activity summary (params: `minutes`) |
| GET | `/topics` | Topic buckets (params: `limit`, `per_group`, `since_hours`) |
| GET | `/tags` | List all tags |
| POST | `/pin` | Pin/unpin a clip (`{"id": N, "pinned": true}`) |
| POST | `/dropper` | Browser extension ingest (`{"url", "title", "selection", "html", "app"}`) |
| POST | `/ingest_url` | Bookmarklet ingest (`{"url", "title", "selection"}`) |
| GET | `/federate_export` | Export clips as JSON for federation |
| POST | `/federate_import` | Import clips from a peer |

---

## MCP Bridge

The Model Context Protocol bridge (`scripts/mcp_server.py`) exposes clipboard context to MCP-aware LLM tools and editors on a separate port:

```bash
python3 scripts/mcp_server.py
# => [mcp] serving on http://127.0.0.1:39300/model_context_protocol/2025-03-26/mcp
```

### MCP Resources

| Endpoint | Description |
|----------|-------------|
| `/model_context_protocol/2025-03-26/mcp` | Resource listing and metadata |
| `/mcp/recent` | Recent clips as JSON |
| `/mcp/context` | Context bundle (filter by app, tag, hours, pins) |
| `/mcp/search` | FTS search over clips |
| `/model_context_protocol/2024-11-05/sse` | Server-Sent Events heartbeat (clip count + latest) |
| `/health` | Health check |

Point your MCP-compatible editor (Cursor, GitHub Copilot, etc.) at the resource listing URL. The SSE endpoint provides a live heartbeat with current clip count and latest clip timestamp, suitable for status indicators and auto-refresh triggers.

A LaunchAgent plist (`com.my-father-mother.mcp.plist`) is provided for auto-starting the MCP bridge at login.

---

## Browser and IDE Integration

### Browser Bookmarklet

With the API server running, create a bookmark with this URL to save the current page into my--father-mother:

```javascript
javascript:(()=>{fetch('http://127.0.0.1:8765/ingest_url',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:location.href,title:document.title,selection:window.getSelection().toString()})}).catch(()=>{});})();
```

### Chrome Extension (MV3)

A sample Manifest V3 extension lives in `scripts/extension-dropper/`. It sends the active tab's title, URL, selected text, and highlighted HTML to the `/dropper` endpoint. Load it as an unpacked extension in Chrome to get a toolbar button and right-click context menu item ("Send to my--father-mother").

### VS Code

A sample `tasks.json` task is provided in `scripts/mfm-vscode-task.json`:

```jsonc
{
  "label": "mfm semantic fetch",
  "type": "shell",
  "command": "./scripts/mfm-fetch.sh",
  "args": ["--query", "${selectedText}", "--semantic", "--copy"]
}
```

Bind to a keybinding via `workbench.action.tasks.runTask` with argument `"mfm semantic fetch"`. Select text in your editor, press the hotkey, and the most semantically similar clip is copied to your clipboard.

### JetBrains (External Tool)

Configure an External Tool with:
- **Program:** `/bin/zsh`
- **Parameters:** `-lc "./scripts/mfm-fetch.sh --query \"$SELECTION\" --semantic --copy"`
- **Working directory:** your clone path

Bind to a keymap for instant semantic lookup from any JetBrains IDE.

### Sublime Text (Build System)

```json
{
  "cmd": ["/bin/zsh", "-lc", "./scripts/mfm-fetch.sh --query \"$TM_SELECTED_TEXT\" --semantic --copy"],
  "shell": true,
  "selector": "text"
}
```

### Obsidian (Templater)

```javascript
const res = await request({url: 'http://127.0.0.1:8765/context?limit=10'});
const data = JSON.parse(res);
return data.items.map(i => `- #${i.id} [${i.source_app}] ${i.title || ''}`).join('\n');
```

### Terminal Pickers

- **fzf picker:** `./scripts/mfm-fzf.sh --query "auth" --semantic` — interactive fuzzy finder in your terminal
- **rofi picker:** `./scripts/mfm-rofi.sh --query "auth"` — GUI picker (requires `rofi`)

### SwiftBar Menubar Plugin

Place `scripts/mfm-swiftbar.1s.sh` in your SwiftBar plugins directory for a menubar widget showing status, recent clips, and quick copy/pin/pause actions. If HTTP is blocked by your firewall, set `MFM_FORCE_CLI=1` and `MFM_REPO_DIR` to use the CLI directly.

See `INTEGRATIONS.md` for the full cookbook covering JupyterLab, Raycast, and additional editor configurations.

---

## LaunchAgents and Autostart

Five LaunchAgent plists are provided for auto-starting components at login:

| Plist | Component | Default |
|-------|-----------|---------|
| `com.my-father-mother.watch.plist` | Direct clipboard watcher (Mother) | cap=5000, interval=1.0s |
| `com.my-father-mother.serve.plist` | HTTP API and web UI (Father) | port 8765 |
| `com.my-father-mother.mcp.plist` | MCP bridge server | port 39300 |
| `com.my-father-mother.tmux.plist` | Dual-pane tmux session | Mother + Father |
| `com.my-father-mother.menu.plist` | Menu launcher script | — |

### Quick Install

```bash
# Install individual agents
cp com.my-father-mother.watch.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.my-father-mother.watch.plist

# Or use the helper script to toggle multiple agents at once
./scripts/mfm-launchagents.sh --on watch,serve,mcp --off tmux,menu
./scripts/mfm-launchagents.sh --status

# Unload
launchctl unload ~/Library/LaunchAgents/com.my-father-mother.watch.plist
```

The MCP plist respects `MFM_MCP_HOST` and `MFM_MCP_PORT` environment variables (defaults: `127.0.0.1:39300`).

---

## Configuration

All configuration is stored in the SQLite database's `settings` table and managed via the `config` command:

```bash
python3 main.py config --get max_bytes       # read a value
python3 main.py config --set max_bytes 32768  # write a value
```

### Key Configuration Options

| Key | Default | Description |
|-----|---------|-------------|
| `max_bytes` | `16384` | Maximum clip size in bytes |
| `max_db_mb` | `512` | Database size cap in MB |
| `allow_secrets` | `false` | Store clips matching secret patterns |
| `notify` | `false` | macOS toast notifications on save/skip |
| `embedder` | `hash` | Embedding backend: `hash` or `e5-small` |
| `evict_mode` | `fifo` | Eviction strategy: `fifo` or `tiered` (prefer non-pinned) |
| `cap_by_app` | `{}` | JSON dict of per-app clip caps |
| `cap_by_tag` | `{}` | JSON dict of per-tag clip caps |
| `allow_pdf` | `false` | Enable PDF ingestion (requires `pdftotext`) |
| `allow_images` | `false` | Enable OCR image ingestion (requires `tesseract`) |
| `auto_summary_cmd` | — | Shell command for auto-summarization on capture |
| `auto_tag_cmd` | — | Shell command for auto-tagging on capture |
| `helper_rewrite_cmd` | — | Shell command for clip rewriting |
| `helper_shorten_cmd` | — | Shell command for clip shortening |
| `helper_extract_cmd` | — | Shell command for structured extraction |

---

## Security Model

my--father-mother takes a defense-in-depth approach to clipboard security:

1. **Local-only by default.** The database lives at `~/.my-father-mother/mfm.db`. No data leaves your machine unless you explicitly configure sync or federation.

2. **Secret filtering.** By default, clips matching common secret patterns — AWS access keys, GitHub Personal Access Tokens, SSH private keys, Slack tokens, and similar high-entropy credential formats — are silently skipped. This prevents accidental long-term storage of secrets you copy during development. Override with `config --set allow_secrets true` if your workflow requires it.

3. **App blocklist.** Sensitive applications (password managers, banking apps) can be excluded from capture entirely via `blocklist --add "1Password"`.

4. **Size caps.** The `max_bytes` setting (default 16 KB) prevents ingestion of unusually large clipboard payloads that could indicate binary data or data dumps.

5. **Database caps with backpressure.** The `max_db_mb` setting (default 512 MB) enforces a hard cap on database size. When approached, the system warns via `/status` and evicts according to the configured eviction mode.

6. **No network listeners by default.** The HTTP API (`serve`) and MCP bridge (`mcp_server.py`) only start when explicitly invoked and bind to `127.0.0.1` (localhost only).

---

## Cross-Organ Context

my--father-mother is part of [ORGAN-III (Ergon)](https://github.com/organvm-iii-ergon), the commerce and product organ of the eight-organ creative-institutional system. It sits within a broader ecosystem:

- **ORGAN-I (Theoria)** — The theoretical foundation. The dual-persona architecture (Mother/Moon, Father/Sun) draws on alchemical and archetypal patterns explored in ORGAN-I's epistemological work, particularly the [recursive-engine](https://github.com/organvm-i-theoria/recursive-engine--generative-entity) framework for self-referential systems. The clipboard-as-memory metaphor — treating transient data as a knowledge substrate — reflects ORGAN-I's investigation of how recursive observation creates durable structure.

- **ORGAN-IV (Taxis)** — Orchestration and governance. The MCP bridge in my--father-mother is designed to integrate with ORGAN-IV's [agentic-titan](https://github.com/organvm-iv-taxis/agentic-titan) orchestration layer, providing clipboard context as a resource for AI agent workflows. The LaunchAgent-based autostart system mirrors the governance patterns that Taxis applies at the organizational level.

- **ORGAN-V (Logos)** — Public process. The design decisions behind my--father-mother — local-first philosophy, dual-persona architecture, the choice to build a single-file CLI tool rather than a cloud SaaS product — are documented in ORGAN-V's [public-process](https://github.com/organvm-v-logos/public-process) essays as part of the building-in-public methodology.

The tool demonstrates ORGAN-III's product philosophy: build useful, opinionated tools that solve real problems for the author first, then make them available as portfolio artifacts that communicate engineering values — privacy, locality, composability, and zero-dependency operation.

---

## Roadmap

### Completed (v1 Scope)

- Full clipboard capture with metadata, deduplication, and configurable caps
- FTS5 full-text search and optional semantic search (hash + e5-small)
- Tags, pins, session notes, capture history
- Topic bucketing and context bundles for LLM integration
- Helper transforms (rewrite/shorten/extract) via shell-out
- HTTP API with 15+ endpoints and minimal web UI
- MCP bridge for editor integration
- Browser bookmarklet, Chrome MV3 extension sample
- IDE integration scripts for VS Code, JetBrains, Sublime, Obsidian
- Terminal pickers (fzf, rofi), SwiftBar menubar plugin
- LaunchAgent plists for all components
- Federation (JSON export/import/push between devices)
- PDF and OCR image ingestion (opt-in)
- Markdown journal export
- AI helper hooks (recall/fill, opt-in)

### Planned

- **Data encryption at rest** — encrypt the SQLite database for additional security on shared machines
- **Full browser extension** — richer than the current sample; persistent sidebar, highlight capture, page annotation
- **Menubar mini-UI** — native SwiftUI or Electron app beyond the current SwiftBar stub
- **Cloud sync targets** — S3-compatible and iCloud Drive with encryption, explicit opt-in only
- **Advanced AI helpers** — retrieval-augmented generation (RAG) over clipboard history using local models
- **Cross-platform support** — Linux (`xclip`/`xsel`) and Windows (`clip.exe`/PowerShell) clipboard backends

All planned features follow the project's layering principle: light features are on by default, medium features are opt-in via config flags, heavy features are off by default and require explicit enablement. The always-on capture loop must remain fast, private, and dependency-free.

---

## Related Work

- **[Paste](https://pasteapp.io/)** — macOS clipboard manager with visual history. Commercial, cloud-optional. my--father-mother runs alongside Paste without interference; it reads the same clipboard stream.
- **[CopyQ](https://hluk.github.io/CopyQ/)** — Cross-platform clipboard manager with scripting. More feature-rich UI but no semantic search or MCP integration.
- **[Pieces](https://pieces.app/)** — AI-powered snippet manager with IDE plugins. Cloud-connected. my--father-mother takes the opposite approach: local-only, stdlib-only, API-first.
- **[Clipboard History Pro](https://clipboardhistorypro.com/)** — macOS utility focused on recent recall. No search, no tagging, no API.
- **[Raycast Clipboard History](https://www.raycast.com/)** — Built into Raycast launcher. Excellent for recent recall but limited search depth and no programmatic API.

my--father-mother differentiates on three axes: (1) semantic search over long-term history, (2) MCP-native integration with AI coding tools, and (3) zero-dependency local-only operation with no account or cloud requirement.

---

## Contributing

Contributions are welcome. The codebase is a single Python file (`main.py`) plus helper scripts — straightforward to read and extend.

**To add a new CLI command:**

1. Write a `cmd_your_command(args: argparse.Namespace) -> None` function
2. Add a subparser in `build_parser()` with `set_defaults(func=cmd_your_command)`
3. Assign the command to a persona (Mother for capture/ingestion, Father for retrieval/management)
4. If the command exposes data, add a corresponding HTTP endpoint in the `serve` handler

**Code style:** Format with `black`. Python 3.10+ stdlib only for core features. Optional dependencies must be guarded with try/except imports.

**Testing:** No formal test suite exists yet. Test manually by running `init`, `watch`, and verifying with `recent`/`search`. Contributions adding a test harness are especially welcome.

Please open an issue or pull request on this repository. For questions about the broader ORGAN system, see [meta-organvm](https://github.com/meta-organvm).

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

## Author

Built by [@4444j99](https://github.com/4444J99) as part of the [ORGAN-III (Ergon)](https://github.com/organvm-iii-ergon) product organ.

Part of the [eight-organ creative-institutional system](https://github.com/meta-organvm) — a coordinated network of theory, art, commerce, orchestration, public process, community, and distribution.
