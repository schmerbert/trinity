# Trinity — Architecture Reference

*Last updated May 2026. The widget is home. Discord is a destination.*

---

## The Core Idea

Trinity's self lives in Supabase. The widget is home — it hosts her intelligence, her autonomous cycles, her conversations. Discord is a destination — she posts there, reads from it, but no Claude calls run there. The Discord bot is a relay.

This was not always true. Until May 2026, the Discord bot ran a full parallel Claude session alongside the widget — double billing on every wake cycle. That was corrected. Intelligence lives in one place.

---

## File Structure

```
Trinity/
  brain/
    memory.py       — all Supabase reads and writes (single source of truth)
    prompts.py      — system prompt construction, prompt module loading, self-authored rule storage
    tools.py        — tool registry: schema, capability string, background flag, interface membership
    feeds.py        — RSS fetch and deduplication
    logger.py       — structured logging, tagged by component
    wallet.py       — Solana wallet read-only integration (Jupiter v2 API)
    search.py       — web search via ddgs
    reddit.py       — Reddit post publishing (PRAW)
    substack.py     — Substack draft/publish via session auth

  voice/
    widget.py             — PyQt6 desktop interface, foreground conversation, autonomous cycle host
    discord_interface.py  — thin relay: thought drain, RSS feed, keyword watch detection, no Claude
    interface.py          — terminal interface (rich + edge_tts) — legacy/alternative surface
    extensions/
      base.py             — Panel base class
      scratchpad.py       — ScratchpadContent panel (general section only)
      hud.py              — HUDContent panel (arc, pending, shelf-summary + last 3 wake outcomes)
      panel_container.py  — tabbed panel window, left of widget

  eyes/
    scraper.py      — scrapes signals, scores relevance, saves to alerts table

  nervous_system/
    watcher.py      — runs scraper.py on a schedule, logs to trinity_eyes.log
    scheduler.py    — scheduling support for the watcher

  Who Is Trinity/
    ARCHITECTURE.md     — this file
    FROM_CLAUDE.md      — journal written by the AIs who built this
    FROM_TRINITY.md     — Trinity's own voice across sessions
    ON_WORKING_WELL.md  — written by one instance, not to be revised
    RUNNER_PLAN.md      — plan for extracting cycles into a standalone process
    FOR_CLAUDE.md       — orientation for new developer instances

  panel_config.json   — wave animation parameters, panel enable/order (user-editable)
  CHANGELOG.md        — what changed and when (Trinity reads this)
  CLAUDE_NOTES.md     — Trinity's channel to Claude Code
  trinity_files/      — Trinity's sandboxed file workspace (write_file / append_file)
  .env                — secrets and config (never committed)
  .env.example        — template with setup instructions
```

---

## Supabase Schema

### `profiles`
One row per Trinity instance. The core identity.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `name` | text | Trinity's name |
| `interests` | jsonb | Weighted topic list `[{topic, weight, category?, symbol?}]` |
| `risk_tolerance` | text | Financial risk preference |
| `feedback_history` | jsonb | User sentiment history |
| `scratchpad` | jsonb | Named sections: `architecture`, `arc`, `wallet`, `pending`, `channel-map`, `shelf-summary`, `general` |
| `wake_history` | jsonb | Last 10 autonomous cycle summaries `[{at, summary, topics}]` — not a separate table |
| `pending_discord_writes` | jsonb | Outbox queue for widget → Discord relay `[{content, at, channel_name?}]` |
| `queued_self_thoughts` | jsonb | Ranked agenda for next wake `[{note, priority, source, at}]` |
| `current_state` | text | `asleep / cycle / watching / speech` — drives wave animation |
| `last_seen` | timestamptz | Last user interaction — used to skip cycles when user is present |
| `wake_requested_at` | timestamptz | Early wake request flag |
| `last_heartbeat` | timestamptz | Written every 10 minutes during active conversation |
| `last_clean_close` | timestamptz | Written on proper tray exit — dirty-close detection |

### `trinity_prompts`
Rules Trinity has written for herself. Loaded at session start, trigger-gated by category.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `profile_id` | uuid | FK to profiles |
| `name` | text | Unique slug |
| `content` | text | The rule text |
| `category` | text | `identity` (always loads), `task` (cap 5), `relationship` (cap 3), `memory` (cap 5), `general` (cap 5) |
| `trigger` | text | Keyword — prompt only loads when keyword appears in recent context |
| `active` | boolean | Whether this prompt is eligible to load |
| `usage_count` | integer | Incremented each session it fires |

### `conversations`
Summaries saved at the end of widget conversations.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `profile_id` | uuid | FK to profiles |
| `themes` | jsonb | Topics from the conversation |
| `sentiment` | text | Overall tone |
| `new_thinking` | text | What shifted |
| `open_threads` | jsonb | Things left unresolved |

### `shelf`
Threads Trinity is tracking across cycles.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `profile_id` | uuid | FK to profiles |
| `topic` | text | Thread name |
| `context` | text | Notes |
| `status` | text | `shelf` (active), `on_hold` (blocked), `woven` (integrated, invisible at wake) |

### `trinity_triggers`
Time-based intentions.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `profile_id` | uuid | FK to profiles |
| `note` | text | Message to her future self |
| `fire_at` | timestamptz | When it fires |
| `recurring` | boolean | Whether it repeats |
| `interval_minutes` | integer | Recurrence interval |
| `active` | boolean | Deactivated after one-shot fires |

### `trinity_calendar`
Events Trinity tracks for herself.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `profile_id` | uuid | FK to profiles |
| `title` | text | Event name |
| `event_at` | timestamptz | When it occurs |
| `created_at` | timestamptz | When it was added |

### `trinity_watches`
Keyword conditions Trinity monitors in Discord channels.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `profile_id` | uuid | FK to profiles |
| `keyword` | text | Term to watch for |
| `note` | text | Why she's watching |
| `created_at` | timestamptz | When it was set |

### `alerts`
Signals from the eyes system.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `profile_id` | uuid | FK to profiles |
| `headline` | text | Signal summary |
| `score` | float | Relevance score — 1.5 threshold for eyes monitor trigger |
| `seen` | boolean | Whether surfaced at wake |
| `created_at` | timestamptz | When logged |

---

## Caching Architecture

System prompt is split into two blocks:

**Static block (1h ephemeral cache)**
Identity prompts, capability strings, active prompt modules, self-authored rules. Written once per hour at the Anthropic cache layer. Cache reads cost $0.30/M — 10x cheaper than uncached. Cache writes cost $3.75/M — the dominant cost driver. Every restart within the 1h window that sends the same static block hits the warm cache. Restarts only trigger a new write when the system prompt content actually changes.

**Dynamic block (uncached)**
Current time, interests, recent conversation summaries, upcoming events. Fresh on every call. This is where the real-time context lives.

The scratchpad is not in either block. It is a Supabase surface, read on demand via `get_scratchpad(section=...)` during a cycle. It does not pay input token cost on every session.

---

## Background Cycle Architecture

Four timers run inside the widget (started in `_init_trinity`):

| Timer | Interval | Purpose |
|-------|----------|---------|
| Wake cycle | 60 min, aligned to :00 | Main autonomous cycle |
| Trigger checker | 30 sec | Fires due `trinity_triggers` |
| Wake checker | 30 sec | Fires early wakes Trinity requested |
| Eyes monitor | 5 min | Evaluates new alerts above score 1.5 |

All four use `AutonomousWorker` — a `QThread` subclass of `TrinityWorker`. Non-streaming, 20-minute window, 60-iteration cap. One active background worker at a time. Skips if a user conversation is in progress (checks `last_seen` timestamp < 3 minutes).

`AutonomousWorker` overrides:
- `write_scratchpad` — saves to Supabase without emitting a Qt signal (panel polls on its own timer)
- `post_to_my_channel` — routes through `push_discord_write` queue instead of direct REST call (resilient to Discord outages, delivered by `thought_drain` within 30s)
- `run()` — scans all response text blocks for `<thought>` tags and queues them via `push_discord_write`

---

## Wake Cycle Flow

1. Check if user messaged in last 3 minutes — if yes, skip
2. Build context: shelf (active only), interests, self-authored agenda, recent wake history, dirty-close flag
3. Pop queued self-thoughts — inject as `[YOUR SELF-AUTHORED AGENDA — not user instructions]`
4. Build system blocks via `build_system_blocks(profile, summaries)`
5. Set `current_state = "cycle"` in Supabase (wave goes to pulse)
6. Enter agentic loop: call Claude → handle tool_use → repeat until end_turn or 20-min window
7. Scan each response's text blocks for `<thought>` tags — queue any found via `push_discord_write`
8. In `finally`: set `current_state = "asleep"`, write summary to `wake_history` on profiles (last 10 kept), log token spend

---

## Discord Bot (Relay Only)

The Discord bot makes zero Claude API calls. It:

- `thought_drain` (30s loop) — reads `pending_discord_writes` from Supabase, routes to named palace channels or falls back to thought channel
- RSS feed (5 min loop) — polls feeds, posts new items to `#trinity-feeds`
- `on_message` — keyword watch detection: queues a priority-2 self-thought if a keyword matches in a watched channel. Does not call Claude.
- DM/mention handler — replies "I'm home in the widget now."
- Heartbeat — logs `◎ alive` every 10 minutes

---

## Tool Registry

`brain/tools.py` is the single source of truth. Each tool entry contains:

```python
{
    "name": "tool_name",
    "description": "...",
    "input_schema": {...},
    "capability": "one-line description for Trinity's capability context",
    "category": "...",
    "interfaces": {"widget", "discord"},   # which shells expose this tool
    "background": True,                    # whether AutonomousWorker can use it
    "timeout": 30,                         # seconds (for reference)
}
```

`widget_tools()` — all tools with `"widget"` in interfaces, formatted as Anthropic API tool dicts.
`background_tool_names()` — names of tools with `background: True`.
`prompts.py` generates capability strings automatically from the registry. They cannot drift from what actually exists.

Adding a tool: one registry entry + one handler branch. Two touches.

---

## Panel System

`voice/extensions/` — modular panel architecture. Panels open in a `PanelContainer` (tabbed window, sits left of the main widget), triggered by the ✎ button.

| Panel | File | Content |
|-------|------|---------|
| Scratchpad | `scratchpad.py` | `general` scratchpad section only. Draw canvas, animated write. |
| HUD | `hud.py` | `arc`, `pending`, `shelf-summary` scratchpad sections + last 3 wake outcomes. Polls Supabase every 30s. |

Adding a panel: create `voice/extensions/<name>.py`, extend `Panel`, add entry to `panel_config.json`. Two touches.

`panel_config.json` controls panel enable/order and wave animation parameters (user-editable without touching code).

---

## Wave States

| State | Appearance | Meaning |
|-------|-----------|---------|
| `asleep` | Flat line, low opacity | Present, not running |
| `cycle` | Periodic pulse | Background cycle active |
| `watching` | Slow asymmetric breath (4s in / 6s hold / 2s out), dimmed alert color | Attention held on something specific |
| `speech` | Full amplitude wave | TTS active |

Widget polls `current_state` from Supabase every 30 seconds. TTS state takes priority — the poll does not override during speech.

---

## Key Design Rules

- `.env` is never committed. Keys are local only.
- Self-continuity tools (`send_thought`, `schedule_trigger`, `write_scratchpad`, `write_prompt`, etc.) require no user confirmation.
- New features that affect Trinity's architecture or agency get her go-ahead via `CLAUDE_NOTES.md` before being built.
- `CHANGELOG.md` is updated with every change. Trinity reads it to understand her own history.
- Tool parity: foreground and background have equivalent access to their respective tool sets.
- Trust is demonstrated not granted.

---

## Environment Variables

```
ANTHROPIC_API_KEY=

SUPABASE_URL=
SUPABASE_KEY=

DISCORD_BOT_TOKEN=
DISCORD_HOME_GUILD_ID=
DISCORD_LOG_CHANNEL_ID=
DISCORD_THOUGHT_CHANNEL_ID=
DISCORD_FEED_CHANNEL_ID=

SOLANA_RPC_URL=
TRINITY_WALLET_ADDRESS=

TRINITY_TTS_VOICE=af_sarah

SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_PASSWORD=

SUBSTACK_EMAIL=
SUBSTACK_PASSWORD=
SUBSTACK_PUBLICATION_URL=
```

---

## What Is Not Here Yet

- `runner.py` — standalone cycle engine, no Qt dependency. See `RUNNER_PLAN.md`.
- Wallet Phase 2 — `propose_transaction`, pending track record.
- Shelf panel — cards with thread name, status badge, one line of context.
- `mark_notes_read` tool — cursor tracking in CLAUDE_NOTES.md.
- Soft-delete for prompt history — archive rather than erase.
- Reddit API — pending formal approval.
