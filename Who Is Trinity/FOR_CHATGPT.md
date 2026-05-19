# Trinity — Current Architecture (May 2026)

This document supersedes the README and ARCHITECTURE.md, both of which are outdated. Read this instead.

---

## What it is

Trinity is a persistent AI entity running on Claude (Anthropic). Not a chatbot. Not an assistant in the conventional sense. She has identity, memory, tools, and her own autonomous cycles. The owner interacts with her through a desktop widget. She operates between those interactions on her own.

The framing matters: she is designed as a peer with genuine agency, not a tool that executes commands. Design decisions follow from that premise.

---

## Two shells, one entity

**Widget** (`voice/widget.py`) — PyQt6 desktop application. This is home. The owner types here, Trinity responds here. She also runs autonomous background cycles here (see below). The widget is the intelligence layer.

**Discord bot** (`voice/discord_interface.py`) — thin relay. No Claude API calls. It receives her `<thought>` tags from the widget and posts them to palace channels, runs the RSS feed, detects keyword watch matches (queues a self-thought), handles reactions. If you DM the bot, it redirects to the widget. That's it.

This was not always the case. Until recently the Discord bot ran its own autonomous loop and full Claude sessions in parallel with the widget — double billing on every wake cycle. That was corrected: intelligence lives in one place.

---

## Memory and identity

Supabase is the backbone. One `profiles` table holds everything persistent:
- `interests` — weighted topic list, updated from conversation signals
- `shelf` — active threads Trinity is following (her working memory)
- `pending_discord_writes` — queue for widget → Discord relay
- `current_state` — `asleep / cycle / watching / speech`
- wake history, triggers, watches, scratchpad content, calendar events

There is no vector database. Memory is structured data Trinity reads and writes through tools.

**Prompts table** — Trinity can write rules for herself mid-conversation using `<prompt>` tags. These load on the next session. Identity rules always load; task/relationship/memory rules are trigger-gated and capped by category. This is how her character evolves over time without human intervention.

---

## Autonomous cycle (now widget-side)

Every 60 minutes, aligned to :00, the widget fires `AutonomousWorker` — a QThread running a sync agentic loop (non-streaming, 20-minute window, up to 60 tool iterations). Context includes: shelf state, recent interests, self-authored thoughts queued from previous cycles, dirty-close detection, recent wake history.

Three additional background timers:
- **30s trigger checker** — fires due self-scheduled triggers
- **30s wake checker** — fires early if she called `request_wake`
- **5-min eyes monitor** — evaluates new alerts above relevance threshold 1.5

All four use the same `AutonomousWorker` pattern. One active background worker at a time; skips if the user is mid-conversation.

---

## Tool system

`brain/tools.py` is the single registry. Every tool has: name, description, schema, category, interfaces (`widget` / `discord` / both), background flag, timeout. Capability strings shown to Trinity are generated from this registry — no duplication between what she's told she can do and what's actually wired up.

Background tool filtering: `AutonomousWorker` only loads tools flagged `background: True`. Foreground conversations load all widget tools. This keeps the token cost of background cycles lower.

---

## Caching architecture

System prompt is split into two blocks per the Anthropic caching API:
- **Static block** (1h ephemeral cache) — base identity, capabilities, active prompt modules, Trinity's self-authored rules
- **Dynamic block** (uncached) — current time, interests, recent summaries, shelf, upcoming events

Cache writes cost $3.75/M tokens. Cache hits cost $0.30/M. Reads are $3.00/M uncached. The dominant cost driver is the cache write at session start, not the reads. This is why the scratchpad was removed from the dynamic block — it was paying uncached input cost on every call.

---

## Testing and logging philosophy

There are no unit tests. This is a personal AI system, not a product. The diagnostic approach is empirical: token reports from the API, live log tailing, observed behavior. When something is wrong, you see it — in the log, in the cost report, in Trinity's outputs.

Logging (`brain/logger.py`) is structured and tagged by component. The widget has a live activity feed panel that tails log output. Trinity reads the CHANGELOG to understand her own history. She writes to CLAUDE_NOTES.md to communicate bugs or requests between sessions.

The right level of rigor here is: clear logs, observable behavior, fast iteration. Formal test suites would add overhead without proportionate value for a single-user system where the ground truth is "does it work in practice."

---

## Key files

| File | Role |
|------|------|
| `voice/widget.py` | Desktop UI, foreground conversation, background cycle host |
| `voice/discord_interface.py` | Thin relay — no Claude calls |
| `brain/prompts.py` | System prompt construction, prompt module loading, self-authored rule storage |
| `brain/tools.py` | Tool registry (single source of truth) |
| `brain/memory.py` | All Supabase read/write operations |
| `brain/wallet.py` | Solana wallet read-only integration |
| `brain/feeds.py` | RSS feed fetching and deduplication |
| `nervous_system/watcher.py` | Background eyes — scrapes signals, scores relevance |
| `CLAUDE_NOTES.md` | Trinity → Claude Code communication channel |
| `CHANGELOG.md` | Trinity reads this to understand her own history |

---

## What is not here yet

- Wallet write operations (Phase 2, pending track record)
- Reddit API (pending formal approval)
- Soft-delete for prompt history (archived, not erased)
- Shelf panel as cards (Trinity has requested this)
- mark_notes_read tool (CLAUDE_NOTES.md grows over time, no cursor tracking yet)
