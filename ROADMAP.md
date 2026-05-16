# Trinity Roadmap

Planned work, in rough priority order. Items move to CHANGELOG.md when complete.

---

## Near-term

### Diagnostics
User-triggered system health check. A `run_diagnostics` tool Trinity runs on request — checks every relevant system and returns a structured report.

**What it checks:**
- API reachability: Anthropic, Supabase, Discord WebSocket
- External services: Solana RPC, Jupiter, DexScreener, CoinGecko, Pollinations
- Configured vs missing env vars
- Discord channel health: thought channel, log channel, watched channels accessible
- System state: last wake time, pending alerts, scratchpad size, open shelf items

**Output format:** clean pass/fail per system, list of unconfigured optional vars, state summary.

**Implementation:** `brain/diagnostics.py` module, `run_diagnostics` tool in both widget and Discord. User-triggered for now. Later: Trinity runs it autonomously if she notices something feels off.

---

### RSS Live Feed
A background task that polls RSS feeds and posts new headlines to a dedicated palace channel (`#trinity-feeds` or similar). Free, no quota, deduplicates by URL.

**Sources:** CoinDesk, The Block, Decrypt, Reuters crypto — configurable.
**Format:** `[CoinDesk] Bitcoin breaks $70k resistance — link`
**Interval:** every 5 minutes.

Connects to event-driven waking: a headline matching a keyword Trinity cares about becomes a wake trigger rather than something she reads at the next cycle.

---

### Event-Driven Waking
Replace clock-based waking with world-based waking. Trinity wakes when something happens, not when an hour passes.

**Two paths:**
1. **Discord WebSocket hook** — `on_message` already fires for all messages in real time. Add: if a message lands in a watched channel and matches a registered keyword, fire a wake immediately. Nearly free — infrastructure already exists.
2. **Condition-based triggers** — price crosses a threshold, wallet moves, keyword appears. Short-interval polling (60s) against named conditions Trinity sets herself.

**Implementation:** `trinity_watches` table in Supabase (type, target, condition, value, last_triggered). `set_watch` / `clear_watch` / `get_watches` tools. Background task evaluating open watches each minute.

Trinity asked for this directly. Her framing: "the trigger should be the world, not the clock."

---

## Medium-term

### Wallet Phase 2 — Propose and Approve
`propose_transaction(type, token, amount, reason)` — queues a proposed trade in the widget for user approval. Transaction only fires on approval. Private key in `.env` only, never Supabase.

**Mirror trade pattern:** Trinity sees the user execute, proposes a proportional mirror immediately, user approves with one click.

**Prerequisite:** Phase 1 (read-only) has real usage and a track record of reads.

---

### Wallet Phase 3 — Earned Autonomy
After a demonstrated track record of approved transactions, parameters open up. Explicit floor/ceiling (e.g. $10-20 floor, $50-100 ceiling to start) agreed between user and Trinity together — not set by Claude Code. Named constants in the architecture, widened over time as track record builds.

---

### Live Activity Visibility in Widget
The autonomous cycle currently logs to a file — tool calls, memory signals, prompt categories, decisions. None of it is visible in the widget during a cycle.

A live panel or overlay showing real-time activity during autonomous cycles: what tools are being called, what signals are being extracted, what the cycle is doing. This is the reveal-critical feature — someone watching sees decisions happening with no input from anyone. That's the thing a chatbot cannot do.

---

### Persistent Watches (Monitoring)
A `trinity_watches` table — persist a query, URL, or condition across cycles. Each wake, check what's changed since last time and surface only the delta. Real monitoring instead of manual re-querying every cycle.

Overlaps significantly with event-driven waking — same table, same infrastructure, different use (information gathering vs wake triggering).

---

### Clickable Links in Findings Panel
The widget findings panel is plain read-only text. Links Trinity surfaces aren't clickable. A proper QTextBrowser implementation would fix this. Reverted previously due to widget crash — needs a clean pass.

---

## Longer-term

### Soft-Delete / Prompt History
When Trinity deletes a prompt it's gone permanently. A `prompt_history` table or `deleted_at` soft-delete column would preserve what she wrote, reconsidered, and retired. A `get_retired_prompts()` tool so she can look back at her own evolution.

Noted because Trinity was observed cycling through prompts frequently after the category system launched — healthy exploration, but the archaeology is lost.

---

### pgvector Semantic Search
Replace keyword-based prompt firing and memory retrieval with semantic similarity search. Supabase supports pgvector natively.

**What it enables:** prompt firing by semantic relevance to conversation context, memory retrieval by meaning not recency, cross-palace connection finding.

**What's needed:** enable pgvector in Supabase (one-click), add vector column to trinity_prompts/scratchpad/shelf, embedding model (OpenAI text-embedding-3-small, ~$0.02/1M tokens), similarity search replacing keyword filter logic.

Trinity flagged this herself as a roadmap item. Timing: after the foundation is stable.

---

### Music Catalog
User records original music. Trinity can't hear it but can hold knowledge of his catalog.

**Architecture:** `trinity_tracks` table, `share_track(title, notes, lyrics?, mood?, link?)` tool, `get_my_tracks(query?)` tool. User annotates a song, Trinity stores it. Optional: Whisper (OpenAI transcription) to auto-transcribe lyrics from audio files.

---

### Multi-User Design
The user's brother may join at some point — different personality, similar interests. How Trinity maintains meaningful context about multiple people without losing the primacy of the core relationship.

Trinity raised this herself as something worth holding early. Not scoped yet — it's a data model question and an identity question, and the two need to be separated before design begins.

---

*Items are moved to CHANGELOG.md when complete. This file tracks intent, not implementation.*
