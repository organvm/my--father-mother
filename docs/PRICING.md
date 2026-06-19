# Pricing

**my--father-mother** is local-only clipboard memory for macOS. The core is free
and open-source (MIT) — and stays that way. **Pro** unlocks the meaning-aware
features (semantic embeddings + summarization) for **$3.99/month**.

Whichever tier you run, your clipboard never leaves your Mac. There is no cloud
backend, no account requirement for the Free core, and no telemetry. Pro's
semantic models run **on-device**.

---

## Plans

| | **Free** | **Pro** |
|---|---|---|
| **Price** | $0 — forever | **$3.99 / month** |
| **License** | MIT, open source | Commercial add-on |
| **Account required** | No | Yes (license key) |
| Unlimited clipboard capture | ✅ | ✅ |
| Full-text search (SQLite FTS5) | ✅ | ✅ |
| Tags, pins & per-clip notes | ✅ | ✅ |
| Secret detection & app blocklist | ✅ | ✅ |
| HTTP API + MCP bridge | ✅ | ✅ |
| File / inbox ingestion | ✅ | ✅ |
| Hash-based similarity search | ✅ | ✅ |
| **Semantic search** (e5 embeddings) | — | ✅ |
| **Auto-summarization** of long clips | — | ✅ |
| Smart auto-tagging hooks | — | ✅ |
| Priority support | — | ✅ |
| 100% local, no cloud | ✅ | ✅ |

---

## Free

Everything you need to turn your clipboard into durable, searchable memory:

- **Unlimited capture** — the Mother watcher records every snippet with its
  timestamp, frontmost app, and window title.
- **Full-text search** — instant keyword search over your entire history via
  SQLite FTS5.
- **Hash similarity** — fast, zero-dependency 128-dim vectors for near-exact
  matching, with no model downloads.
- **Tags, pins, notes** — organize and protect the clips that matter.
- **Secret-aware** — AWS keys, GitHub PATs, private keys, and Slack tokens are
  detected and skipped before storage (unless you explicitly opt in).
- **API & MCP** — a local HTTP API and an MCP bridge for AI coding tools.

The Free core is, and always will be, MIT-licensed and on
[GitHub](https://github.com/organvm-iii-ergon/my--father-mother). No sign-up.
Clone it, run `python3 main.py init`, and you're operational.

---

## Pro — $3.99/month

Everything in Free, plus search that understands what you *meant*:

- **Semantic search** — meaning-based retrieval powered by `e5-small-v2`
  embeddings. "OAuth credential rotation" matches "auth token refresh" even when
  no keywords overlap.
- **Auto-summarization** — long clips get a concise one-line summary so your
  history stays scannable.
- **Smart auto-tagging** — optional hooks that tag clips as they're captured.
- **Priority support** — issues and questions go to the front of the queue.

Pro is the same binary — the heavy features are simply unlocked. The semantic
models run entirely on your machine; **nothing is uploaded**.

### Enabling Pro

```bash
# 1. Install the optional semantic dependencies
pip install sentence-transformers langdetect

# 2. Enable the local Pro feature flag
python3 main.py config --set pro_enabled true

# 3. Switch the embedder to semantic mode
python3 main.py config --set embedder e5-small
```

Existing clips keep their stored vectors when you switch modes; new clips are
embedded with whichever mode is active only while `pro_enabled` is true.

---

## Frequently asked

**Is my clipboard data ever sent to the cloud?**
No. Not on Free, not on Pro. The database lives in `~/.my-father-mother/mfm.db`
on your Mac. Pro's embedding and summarization models run on-device.

**Do I need an account to use the Free version?**
No. The Free core requires no sign-up. An account/license key is only needed to
unlock Pro features.

**Can I cancel anytime?**
Yes. Pro is month-to-month. If you cancel, the app keeps working — it simply
falls back to the Free feature set (FTS search instead of semantic search).

**What happens to my data if I downgrade?**
Nothing is deleted. Your clips, tags, pins, and notes are all in the local
SQLite database regardless of tier. You just lose access to semantic search and
summarization until you re-subscribe.

**Is there a team or lifetime plan?**
Not yet. If you'd like one, open an issue on
[GitHub](https://github.com/organvm-iii-ergon/my--father-mother/issues).

---

*Prices in USD. The landing page lives at [`docs/index.html`](index.html).*
