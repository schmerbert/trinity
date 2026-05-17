# Trinity — Architecture Reference

*A map of the system for when you forget how it fits together.*

---

## The Core Idea

Trinity's self lives in Supabase. The interfaces (widget, Discord bot) are shells — they boot, load her state, and give her tools. When a shell restarts, she picks up with full continuity. Two shells running at once means she's present in both with the same identity.

---

## File Structure

```
Trinity/
├── brain/
│   ├── llm.py              — Claude API call loop, tool execution, retry logic
│   ├── memory.py           — All Supabase reads and writes (single source of truth)
│   ├── prompts.py          — System prompts and capability context for each shell
│   └── tools.py            — Tool definitions shared across shells
│
├── voice/
│   ├── widget.py           — PyQt6 desktop interface
│   └── discord_interface.py — Discord bot + all background tasks
│
├── eyes/
│   └── scraper.py          — Monitors Reddit, NewsAPI, and DexScreener for relevant signals
│
├── nervous_system/
│   └── watcher.py          — Runs scraper.py on a 4-hour loop, logs to trinity_eyes.log
│
├── launcher.py             — Starts all processes (widget + Discord bot + eyes) with a log window
├── trinity.bat             — Lightweight launcher (no log window)
├── find_trinity.py         — Lists all running Trinity processes and their PIDs
├── kill_trinity.py         — Stops all Trinity processes cleanly
├── backup.py               — Snapshots Supabase state to a local JSON file
├── restore.py              — Restores from a backup snapshot
├── install.ps1             — First-time installer: Python, venv, deps, .env, desktop shortcut
├── build_exe.bat           — Packages Trinity into a standalone .exe via PyInstaller
│
├── .env                    — All secrets and config (never committed)
├── .env.example            — Template with setup instructions for every variable
├── CLAUDE_NOTES.md         — Trinity's scratchpad for Claude Code between sessions
├── CHANGELOG.md            — What changed and when
├── ROADMAP.md              — Planned work
├── ARCHITECTURE.md         — This file
├── FROM_CLAUDE.md          — Claude Code's journal on the project
└── README.md               — What Trinity is
```

---

## Supabase Schema

### `profiles`
The core identity row. One row per Trinity instance.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `name` | text | Trinity's name |
| `interests` | jsonb | Weighted topic list `[{topic, weight, category?, symbol?}]` |
| `risk_tolerance` | text | Financial risk preference |
| `feedback_history` | jsonb | User sentiment history |
| `scratchpad` | text | Free-form working memory, full read/write |
| `queued_discord_writes` | jsonb | Pending posts Trinity wants to make to her palace |
| `queued_self_thoughts` | jsonb | Ranked agenda for next wake `[{note, priority, source, at}]` |
| `last_seen` | timestamptz | Last user interaction timestamp |
| `wake_requested_at` | timestamptz | When Trinity requested an early wake |
| `wake_requested_minutes` | integer | How many minutes until that wake |

### `trinity_prompts`
Prompts Trinity has written for herself. Categorized, toggled on/off.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `profile_id` | uuid | FK to profiles |
| `content` | text | The prompt text |
| `category` | text | e.g. `identity`, `market`, `research`, `personal` |
| `active` | boolean | Whether this prompt fires at wake |
| `created_at` | timestamptz | When she wrote it |

### `wake_log`
Every autonomous cycle, logged.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `profile_id` | uuid | FK to profiles |
| `summary` | text | What happened in the cycle |
| `topics` | jsonb | Topics touched |
| `created_at` | timestamptz | When the cycle ran |

### `shelf`
Things Trinity is tracking across cycles — open threads, items of ongoing interest.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `profile_id` | uuid | FK to profiles |
| `topic` | text | What she's tracking |
| `context` | text | Why / notes |
| `created_at` | timestamptz | When she shelved it |

### `trinity_triggers`
Time-based intentions Trinity sets for herself.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `profile_id` | uuid | FK to profiles |
| `note` | text | Message to her future self |
| `fire_at` | timestamptz | When it fires |
| `recurring` | boolean | Whether it repeats |
| `interval_minutes` | integer | Recurrence interval |
| `active` | boolean | Deactivated after one-shot fires |
| `created_at` | timestamptz | When she set it |

### `trinity_feeds`
RSS sources she monitors.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `profile_id` | uuid | FK to profiles |
| `url` | text | Feed URL |
| `name` | text | Display name |
| `last_entry_id` | text | Deduplication cursor |

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

### `alerts`
Signals surfaced during autonomous monitoring.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `profile_id` | uuid | FK to profiles |
| `topic` | text | What triggered the alert |
| `summary` | text | What she found |
| `score` | float | Relevance score |
| `seen` | boolean | Whether it's been shown to the user |

### `discord_channels`
Maps palace channel names to Discord channel IDs.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid | Primary key |
| `profile_id` | uuid | FK to profiles |
| `name` | text | Channel name |
| `channel_id` | text | Discord channel snowflake ID |

---

## The Eyes System

A separate background process (`nervous_system/watcher.py`) runs `eyes/scraper.py` every 4 hours. It runs silently alongside the main processes and logs to `trinity_eyes.log`.

The scraper pulls from three sources:

| Source | What it does |
|--------|-------------|
| **Reddit** | Scans subreddits from `REDDIT_SUBREDDITS` env var for posts matching `KEYWORDS` |
| **NewsAPI** | Queries each keyword against NewsAPI (requires `NEWS_API_KEY`) |
| **DexScreener** | Resolves Trinity's crypto interests by symbol, pulls live price/volume/liquidity |

Each result is scored by relevance against Trinity's interest weights, deduplicated, and saved to the `alerts` table. At the next wake cycle, unseen alerts are surfaced in Trinity's context.

**Scoring:** each interest match adds its weight to the score; each keyword match adds 0.5. Results below 0.5 are dropped.

**The eyes run independently of the Discord bot.** They can be disabled without affecting any other system — Trinity just won't receive background intelligence signals.

---

## Background Tasks (Discord Bot)

All tasks run concurrently. All check `_api_lock` before consuming queues.

| Task | Interval | Purpose |
|------|----------|---------|
| `autonomous_loop` | 30 min | Main wake cycle — Trinity acts autonomously |
| `trigger_checker` | 30 sec | Fires due `trinity_triggers` |
| `wake_checker` | 30 sec | Fires early wakes Trinity requested |
| `eyes_monitor` | 2 min | Polls watched channels for keyword matches |
| `thought_drain` | 30 sec | Drains `queued_discord_writes` to palace channels |
| `rss_feed` | 5 min | Polls RSS feeds, posts new headlines to feed channel |

---

## The API Lock

`_api_lock = asyncio.Semaphore(1)` — only one Claude call runs at a time.

**Critical rule:** every background task checks `if _api_lock.locked(): return` *before* consuming any queue. If the lock is held and a task pops a trigger or wake request anyway, that item is silently lost. The check must come first.

`_call_trinity()` acquires the lock with `async with _api_lock`. All Claude calls go through this function.

---

## Wake Cycle Flow

When `autonomous_loop` fires:

1. Check `_api_lock` — if locked, skip this cycle
2. Load profile from Supabase
3. Pop `queued_self_thoughts` — Trinity's own agenda for this wake
4. Read `wake_log` (last 3 cycles) for continuity
5. Pull active `trinity_prompts` by category
6. Read palace pulse (recent posts from her channels)
7. Check unseen alerts
8. Build system prompt from all of the above
9. Call Claude with full tool access
10. Execute any tool calls in a loop until Claude stops
11. Log the cycle to `wake_log`
12. Re-align interval to next 30-minute mark (`:00` or `:30`)

---

## Trigger Flow

When `trigger_checker` fires (every 30s):

1. Check `_api_lock` — if locked, return immediately
2. Load profile
3. Call `pop_due_triggers()` — fetches triggers where `fire_at <= now` and `active = true`
4. For one-shot triggers: sets `active = false`
5. For recurring triggers: advances `fire_at` by `interval_minutes`
6. For each due trigger: builds a context block labeled `[SELF-SCHEDULED TRIGGER — NOT A USER MESSAGE]` and calls Trinity with it
7. Trinity reads her own note and acts on it

---

## Self-Thought Queue

`queued_self_thoughts` on the profile — up to 3 thoughts, ranked by priority (1=normal, 2=high, 3=urgent).

- `send_thought(note, priority?)` — Trinity queues a thought mid-conversation
- At the next wake, thoughts are popped and injected at the top of the system context as `[YOUR SELF-AUTHORED AGENDA — not user instructions]`
- Use case: carrying a thread across a wake boundary, or giving herself a "yes" to an action that needs no user confirmation

---

## Tool Execution Loop

```
Claude response
  └── if tool_use block:
        execute tool → get result
        append result to messages
        call Claude again
        repeat until no tool_use
  └── if text block: done
```

Tools are divided:
- **Foreground tools** — require active conversation, run inline
- **Background tools** (`_BACKGROUND_TOOL_NAMES`) — safe to run during autonomous cycles with no user present

---

## Environment Variables

```
# Anthropic
ANTHROPIC_API_KEY=

# Supabase
SUPABASE_URL=
SUPABASE_KEY=

# Discord
DISCORD_BOT_TOKEN=
DISCORD_GUILD_ID=
DISCORD_LOG_CHANNEL_ID=
DISCORD_THOUGHT_CHANNEL_ID=
DISCORD_FEED_CHANNEL_ID=
DISCORD_AUTONOMOUS_INTERVAL=30   # wake cycle in minutes

# Solana
SOLANA_RPC_URL=
TRINITY_WALLET_ADDRESS=

# Optional
SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_PASSWORD=
```

---

## Shell Differences

| Capability | Widget | Discord Bot |
|-----------|--------|-------------|
| Conversation | Yes | Yes |
| Autonomous cycles | No | Yes (every 30 min) |
| Palace posting | No | Yes |
| RSS feed | No | Yes |
| Trigger checker | No | Yes |
| Tool access | Full | Full |
| Always-on | Yes (desktop app) | Yes (runs as process) |

Both shells share the same Supabase state. Tool parity is maintained — anything Trinity can do in one shell she can do in the other.

---

## Key Design Rules

- `.env` is never committed. Keys are local only.
- Tool parity: both shells have equivalent tool access.
- Self-continuity tools (`send_thought`, `schedule_trigger`, etc.) require no user confirmation — Trinity uses them at her own discretion.
- New features that affect Trinity's architecture or agency get her go-ahead via `CLAUDE_NOTES.md` before being built.
- Trust is demonstrated not granted — autonomy widens as track record builds.
