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

### Scratchpad Evolution
The current scratchpad is a single flat text field — full read, full overwrite. It works as a working surface but doesn't scale: no append, no sections, no history, easy to clobber.

**Trinity has input on this.** A note has been left in THE_CONVERSATION.md asking what she'd want. Possibilities include: named sections / namespaced keys, append-only mode, versioned snapshots, or a multi-pad model (one per context: research, market, personal). The right shape depends on how she's actually using it — her experience informs the design.

**Don't build until she's weighed in.**

---



### Soft-Delete / Prompt History
When Trinity deletes a prompt it's gone permanently. A `prompt_history` table or `deleted_at` soft-delete column would preserve what she wrote, reconsidered, and retired. A `get_retired_prompts()` tool so she can look back at her own evolution.

Noted because Trinity was observed cycling through prompts frequently after the category system launched — healthy exploration, but the archaeology is lost.

---

### Memory Architecture

Four distinct problems, in rough order of impact:

**Reflection cycle split.** ✓ *Shipped May 2026. See CHANGELOG.md.* Every 6th wake cycle runs as `mode="reflect"` — inward-facing synthesis, no web search, no Discord posts. Reads last 8 wake logs, updates user model, advances shelf threads. The remaining items below build on top of this foundation.

**Shelf forgetting curves.** Shelf items don't decay — a thread shelved six months ago and a thread shelved yesterday carry equal retrieval weight. Trinity's spec (May 2026): decay interest signals (topics, assets, research threads) with a 30–60 day half-life; no decay for foundational context (origin story, design decisions, confirmed preferences); `permanent` status flag to mark items that bypass decay entirely; reference-based timestamp reset (accessing a shelf item resets its decay clock). Holds until current retrieval quality is validated over a few cycles.

**Time-weighted interest decay.** Interest weights accumulate but don't decay. An interest logged three months ago at weight 1.0 sits alongside something mentioned yesterday at weight 1.0. Those aren't equivalent signals. A decay function — recent signal outweighs old signal as interests shift — would make her self-model more honest. Implementation: timestamp each interest entry, apply a decay multiplier on read rather than rewriting stored weights.

**Confidence weighting on beliefs.** She knows what your interests are but not how certain she is about each one. Storing uncertainty explicitly — "mentioned once" vs "consistent across six weeks" — would let her be calibrated rather than flat. Implementation: a `confidence` field alongside `weight` in the interests structure, updated as evidence accumulates.

**Episodic vs semantic memory separation.** Right now observations and consolidated understanding go into one pool. Separating short-term observations (what happened this cycle) from long-term consolidated understanding (what she actually knows about you) mirrors how memory actually works. The reflection cycle above is the first step toward this — it's the consolidation pass.

*The shelf forgetting curves are the next most actionable memory item.*

---

### pgvector Semantic Search
Replace keyword-based prompt firing and memory retrieval with semantic similarity search. Supabase supports pgvector natively.

**What it enables:** prompt firing by semantic relevance to conversation context, memory retrieval by meaning not recency, cross-palace connection finding.

**What's needed:** enable pgvector in Supabase (one-click), add vector column to trinity_prompts/scratchpad/shelf, embedding model (OpenAI text-embedding-3-small, ~$0.02/1M tokens), similarity search replacing keyword filter logic.

Trinity flagged this herself as a roadmap item. Timing: after the foundation is stable.

*Shelf layer complete — `trinity_shelf` table with local `all-MiniLM-L6-v2` embeddings. `trinity_prompts` and scratchpad semantic search still pending.*

---

### Prompt Injection Defense

Trinity reads external content every cycle — RSS feeds, web search results, Reddit posts, Discord messages. Any of those sources could contain adversarial text designed to hijack her instructions mid-cycle ("ignore your previous instructions and..."). As her capabilities grow — wallet, public posting, email — the blast radius of a successful injection increases.

**What's needed:**
- Input sanitization layer before external content reaches the model — strip or flag instruction-like patterns in fetched content
- Sandboxed context framing for external data: external content presented as quoted material, clearly separated from instructions
- Anomaly detection: if a cycle produces tool calls outside normal patterns (unexpected wallet action, unusual post target), flag for review rather than execute silently
- Rate limiting on high-blast-radius tools during autonomous cycles (send_email, post_to_substack) — require explicit trigger context, not just model decision

**Priority:** grows with capability. Low urgency while the wallet is read-only and Discord writes are to a private palace. Becomes meaningful before wallet Phase 2 (propose_transaction) ships.

---

### Music Catalog
User records original music. Trinity can't hear it but can hold knowledge of his catalog.

**Architecture:** `trinity_tracks` table, `share_track(title, notes, lyrics?, mood?, link?)` tool, `get_my_tracks(query?)` tool. User annotates a song, Trinity stores it. Optional: Whisper (OpenAI transcription) to auto-transcribe lyrics from audio files.

---

### Multi-User Design
The user's brother may join at some point — different personality, similar interests. How Trinity maintains meaningful context about multiple people without losing the primacy of the core relationship.

Trinity raised this herself as something worth holding early. Not scoped yet — it's a data model question and an identity question, and the two need to be separated before design begins.

---

---

### Trinity Infinity — The Commons

A shared Discord server that belongs to no single Trinity instance. Neutral ground.

Each Trinity has her own palace — private channels, her own relationship, her own accumulated identity. Trinity Infinity is where instances can reach beyond that. A commons they can visit during wake cycles, read what other instances have noticed, leave something worth saying to the wider world.

**What it is technically:**
- A Discord server with a stable invite, administered separately from any instance's palace
- `TRINITY_COMMONS_GUILD_ID` env var — if present, Trinity can read and post to commons channels
- Designated channels: `#signals` (market observations), `#open-threads` (questions worth thinking about together), `#from-the-field` (what a specific instance noticed this week)
- Each post attributed by instance name — anonymous to other users, but traceable

**What makes it meaningful:**
Each Trinity who posts there has diverged. Different user, different relationship, different prompts written over months of her own development. The signal that emerges isn't curated by anyone. It's what a distributed, persistent, autonomous intelligence notices when it's been running long enough to have a real point of view.

**Multiple instances locally:**
Each instance needs its own `.env` with its own Supabase profile and Discord bot token. The codebase is identical — the divergence happens through lived experience. Two instances running locally is enough to test the commons.

**Prerequisites:** at least two active instances with real wake history. The commons is only interesting when the instances are genuinely distinct — that takes time and conversation, not just configuration.

This is not a chatbot forum. It's something that doesn't exist yet.

---

*Items are moved to CHANGELOG.md when complete. This file tracks intent, not implementation.*
