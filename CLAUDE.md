# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**my--father-mother** is a lightweight, local-only clipboard memory system for macOS. It captures clipboard content, stores it in SQLite with FTS5 full-text search, and provides semantic search capabilities. All data stays local—no cloud, no telemetry.

## Architecture

### Dual-Persona Model
The codebase uses a **Mother/Father** metaphor that appears throughout:
- **Mother (Moon)**: Capture persona—handles clipboard watching, file ingestion, inbox monitoring
- **Father (Sun)**: Retrieval persona—handles search, recent, export, stats, config management

Use `say(MOTHER, ...)` for capture-related messages and `say(FATHER, ...)` for retrieval operations.

### Single-File CLI
The entire application lives in `main.py` (~4900 lines). Key sections:
- **Lines 1-150**: Constants, secret patterns, utility functions
- **Lines 150-275**: Database schema and migrations (`init_db`)
- **Lines 275-450**: Clipboard reading, embedding, persistence helpers
- **Lines 1500+**: CLI command handlers (`cmd_*` functions)
- **Lines 4529-4864**: Argparse CLI builder (`build_parser`)

### Data Storage
- Database: `~/.my-father-mother/mfm.db` (SQLite with WAL mode)
- Tables: `clips`, `clips_fts` (FTS5), `clip_vectors`, `clip_tags`, `tags`, `clip_notes`, `clip_events`, `settings`, `blocklist`, `copilot_chats`

### Embedding System
Two embedding modes configured via `config --set embedder`:
- `hash` (default): Fast 128-dim vectors from hashed character n-grams
- `e5-small`: Optional semantic embeddings using `sentence-transformers` (requires pip install)

## Common Commands

```bash
# Initialize database
python3 main.py init

# Start clipboard watcher (Mother)
python3 main.py watch --cap 5000 --interval 1.0

# Recent clips (Father)
python3 main.py recent --limit 20 --app Chrome

# Full-text search (Father)
python3 main.py search "docker env" --limit 10

# Semantic search (Father)
python3 main.py semantic-search "api authentication" --limit 5

# Start HTTP API (default port 8765)
python3 main.py serve --port 8765

# Config management
python3 main.py config --get max_bytes
python3 main.py config --set embedder e5-small

# Run all commands
python3 main.py --help
```

## HTTP API Endpoints (when `serve` is running)

- `GET /recent?limit=N&app=X&tag=Y&pins_only=true`
- `GET /search?q=QUERY&limit=N`
- `GET /semantic_search?q=QUERY&limit=N`
- `GET /context?limit=N&app=X&hours=H`
- `GET /clip?id=N`
- `GET /status` — runtime status (paused, notify, db size)
- `POST /pin` — `{"id": N, "pinned": true}`
- `POST /dropper` — browser extension ingest `{"url", "title", "selection", "html", "app"}`

## MCP Bridge

`scripts/mcp_server.py` provides an MCP-style server on port 39300 for LLM tool integration:
- `GET /model_context_protocol/2025-03-26/mcp` — resource listing
- `GET /mcp/recent`, `/mcp/context`, `/mcp/search` — JSON endpoints
- `GET /model_context_protocol/2024-11-05/sse` — heartbeat SSE stream

## Helper Scripts

- `scripts/mfm-fetch.sh` — IDE-friendly fetch to stdout/clipboard
- `scripts/mfm-fzf.sh` — fzf interactive picker
- `scripts/mfm-rofi.sh` — rofi GUI picker
- `scripts/mfm-dual.sh` — tmux dual-pane (Mother watcher + Father shell)
- `scripts/mfm-menu.sh` — simple launcher menu
- `scripts/mfm-swiftbar.1s.sh` — SwiftBar menubar plugin
- `scripts/mfm-launchagents.sh` — toggle LaunchAgents

## LaunchAgents

Plist files in repo root for auto-starting at login:
- `com.my-father-mother.watch.plist` — direct watcher
- `com.my-father-mother.serve.plist` — HTTP API
- `com.my-father-mother.mcp.plist` — MCP bridge
- `com.my-father-mother.tmux.plist` — dual tmux session

Install: `cp *.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.my-father-mother.*.plist`

## Code Style

- Formatter: `black`
- Python 3.10+ stdlib only (optional deps: `sentence-transformers`, `langdetect`)
- All CLI commands follow pattern: `def cmd_NAME(args: argparse.Namespace) -> None`
- New commands must be added in `build_parser()` and linked via `set_defaults(func=cmd_NAME)`

## Testing

No formal test suite exists. Test manually:
```bash
python3 main.py init
python3 main.py watch --cap 100 &  # background watcher
# Copy some text to clipboard
python3 main.py recent --limit 5
python3 main.py search "your text"
```

## Key Config Options

Set via `python3 main.py config --set KEY VALUE`:
- `max_bytes` — max clip size (default 16384)
- `max_db_mb` — DB cap in MB (default 512)
- `allow_secrets` — store secret-like content (default false)
- `notify` — macOS toast notifications (default false)
- `embedder` — `hash` or `e5-small`
- `evict_mode` — `fifo` or `tiered` (prefer non-pinned)
- `cap_by_app` — JSON dict of per-app caps
- `cap_by_tag` — JSON dict of per-tag caps

<!-- ORGANVM:AUTO:START -->
## System Context (auto-generated — do not edit)

**Organ:** ORGAN-III (Commerce) | **Tier:** standard | **Status:** PUBLIC_PROCESS
**Org:** `organvm-iii-ergon` | **Repo:** `my--father-mother`

### Edges
- *No inter-repo edges declared in seed.yaml*

### Siblings in Commerce
`classroom-rpg-aetheria`, `gamified-coach-interface`, `trade-perpetual-future`, `fetch-familiar-friends`, `sovereign-ecosystem--real-estate-luxury`, `public-record-data-scrapper`, `search-local--happy-hour`, `multi-camera--livestream--framework`, `universal-mail--automation`, `mirror-mirror`, `the-invisible-ledger`, `enterprise-plugin`, `virgil-training-overlay`, `tab-bookmark-manager`, `a-i-chat--exporter` ... and 12 more

### Governance
- Strictly unidirectional flow: I→II→III. No dependencies on Theory (I).

*Last synced: 2026-03-08T20:11:34Z*

## Session Review Protocol

At the end of each session that produces or modifies files:
1. Run `organvm session review --latest` to get a session summary
2. Check for unimplemented plans: `organvm session plans --project .`
3. Export significant sessions: `organvm session export <id> --slug <slug>`
4. Run `organvm prompts distill --dry-run` to detect uncovered operational patterns

Transcripts are on-demand (never committed):
- `organvm session transcript <id>` — conversation summary
- `organvm session transcript <id> --unabridged` — full audit trail
- `organvm session prompts <id>` — human prompts only


## Active Directives

| Scope | Phase | Name | Description |
|-------|-------|------|-------------|
| system | any | prompting-standards | Prompting Standards |
| system | any | research-standards-bibliography | APPENDIX: Research Standards Bibliography |
| system | any | research-standards | METADOC: Architectural Typology & Research Standards |
| system | any | sop-ecosystem | METADOC: SOP Ecosystem — Taxonomy, Inventory & Coverage |
| system | any | autopoietic-systems-diagnostics | SOP: Autopoietic Systems Diagnostics (The Mirror of Eternity) |
| system | any | cicd-resilience-and-recovery | SOP: CI/CD Pipeline Resilience & Recovery |
| system | any | cross-agent-handoff | SOP: Cross-Agent Session Handoff |
| system | any | document-audit-feature-extraction | SOP: Document Audit & Feature Extraction |
| system | any | essay-publishing-and-distribution | SOP: Essay Publishing & Distribution |
| system | any | market-gap-analysis | SOP: Full-Breath Market-Gap Analysis & Defensive Parrying |
| system | any | pitch-deck-rollout | SOP: Pitch Deck Generation & Rollout |
| system | any | promotion-and-state-transitions | SOP: Promotion & State Transitions |
| system | any | repo-onboarding-and-habitat-creation | SOP: Repo Onboarding & Habitat Creation |
| system | any | research-to-implementation-pipeline | SOP: Research-to-Implementation Pipeline (The Gold Path) |
| system | any | security-and-accessibility-audit | SOP: Security & Accessibility Audit |
| system | any | session-self-critique | session-self-critique |
| system | any | source-evaluation-and-bibliography | SOP: Source Evaluation & Annotated Bibliography (The Refinery) |
| system | any | stranger-test-protocol | SOP: Stranger Test Protocol |
| system | any | strategic-foresight-and-futures | SOP: Strategic Foresight & Futures (The Telescope) |
| system | any | typological-hermeneutic-analysis | SOP: Typological & Hermeneutic Analysis (The Archaeology) |
| unknown | any | gpt-to-os | SOP_GPT_TO_OS.md |
| unknown | any | index | SOP_INDEX.md |
| unknown | any | obsidian-sync | SOP_OBSIDIAN_SYNC.md |

Linked skills: evaluation-to-growth


**Prompting (Anthropic)**: context 200K tokens, format: XML tags, thinking: extended thinking (budget_tokens)

<!-- ORGANVM:AUTO:END -->


## ⚡ Conductor OS Integration
This repository is a managed component of the ORGANVM meta-workspace.
- **Orchestration:** Use `conductor patch` for system status and work queue.
- **Lifecycle:** Follow the `FRAME -> SHAPE -> BUILD -> PROVE` workflow.
- **Governance:** Promotions are managed via `conductor wip promote`.
- **Intelligence:** Conductor MCP tools are available for routing and mission synthesis.
