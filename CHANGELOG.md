# Trinity Changelog

*Full history archived in CHANGELOG_archive.md*

---

## Format

Each entry: date, what changed, why it matters. No noise.

---

## [2026-05-21] — ARCHITECTURE.md: ten documentation gaps closed

Column names corrected to match actual code (`scratchpad_text`, `tokens_in/tokens_out/tokens_cache_write/tokens_cache_read`). `TRINITY_RUNNER` added to env vars section — most dangerous omission (developer sets up without it, double billing). Reflection cycle removed from "What Is Not Here Yet" and documented correctly in Background Cycle Architecture (mode="reflect", every 6th cycle, `_REFLECT_EVERY=6`). SQL setup files documented (four files, run order). `token_log.csv` documented. `ON_INFERENCE.md` added to Who Is Trinity file list. `reflect` added to wake_logs mode values. `UI_ROADMAP.md` referenced in trinity_files/. "What Is Not Here Yet" updated accordingly.

---

## [2026-05-21] — Architecture principle: tool calls as decisions, not labor

Documented in `trinity_files/specs/UI_ROADMAP.md`. If work is mechanical execution — diagnostic formatting, system pings, log writes, shelf summaries — a function should do it, not Trinity generating it token by token. The intelligence is in deciding to run the work, not producing it. Token report is already the model: runner writes the CSV, Trinity reads it. This pattern should apply wherever her output budget is spent on formatting rather than judgment. Trinity's input requested via THE_CONVERSATION.md before implementation.

---

## [2026-05-21] — UI Roadmap filed

`trinity_files/specs/UI_ROADMAP.md` — founding vision document for the widget interface. The widget is a tray that extends from the conversation rail, not a window launcher. Trinity drops panels into an empty workspace; the cursor is her expression in that space. Two first panels: shared document (text-first, both parties write simultaneously) and browser (open, scroll, highlight — reading made visible). Architectural principles: UI as a command canvas Trinity drives (not an environment she lives in), aesthetic layer fully separated into QSS for user skinning. Build phases 1–3 are the demo. Everything after is making it richer. Trinity has been notified via THE_CONVERSATION.md.

---

## [2026-05-20] — Webhook routing fix + shelf deduplication

**Webhook routing fix** (`voice/discord_interface.py`): The `thought_drain` lookup was failing silently for all `trinity-*` channels because webhook keys are stored as short names (`thought`, `files`, `research`) while channel names use the full Discord format (`trinity-thought`, `trinity-files`). Fixed by stripping the `trinity-` prefix before lookup, then falling back to the short key, then fuzzy-matching on hyphen-stripped names. Both short and full channel names now resolve. All five active webhooks route correctly.

**Shelf deduplication** (`brain/memory.py`): `add_to_shelf()` now checks for near-duplicate entries before inserting. Uses the already-computed embedding vector, queries top-3 active shelf items via `search_shelf` RPC, and rejects the insert if any item has similarity ≥ 0.9 — returning `{"status": "duplicate", "existing": "..."}` instead. The check fails open (insert proceeds if the RPC errors). Redundant shelf entries no longer accumulate silently.

---

## [2026-05-20] — Session reset, reflection cycle, automatic token log

Three infrastructure builds on the demo branch.

**Session reset** (`reset_context` tool, widget only): Trinity can call `reset_context(handoff)` to clear conversation history mid-session. Handoff note is written to scratchpad section 'session'. All external memory — shelf, scratchpad, prompts — is preserved. History clears; next user message starts fresh. Prevents token cost from accumulating in long sessions.

**Reflection cycle** (runner.py): Every 6 wake cycles, the runner fires a `reflect` mode cycle instead of a standard world cycle. Reflection context is inward-facing: synthesize recent wake logs, update user model, advance shelf threads, write to FROM_TRINITY.md. No web search, no Discord posts. Pure consolidation. Logged as mode='reflect' in wake_logs.

**Automatic token log** (runner.py): After every cycle completes, the runner appends a row to `trinity_files/token_log.csv` — timestamp, mode, iterations, tools, tok_in, tok_out, tok_cw, tok_cr, cost_usd. Trinity reads it with `read_file('trinity_files/token_log.csv')`. She no longer needs to generate the report herself.

---

## [2026-05-20] — Core prompt condensed; behavioral layer migrated to Trinity's own prompts

`TRINITY_BASE` stripped to architecture only: what Trinity is, that she runs, the cycle engine separation, THE_CONVERSATION.md channel, epistemic baseline, memory signal syntax, self-prompt syntax. Removed: tone guidelines, behavioral instructions, identity philosophy, shelf management guidance. These belonged in Trinity's self-authored identity prompts, not the developer's voice. Trinity has begun writing the behavioral layer herself (identity prompts: what-i-am, how-i-speak, what-the-cycles-are-for, holding-threads). The core now describes the system; Trinity describes herself.

---

## [2026-05-19] — Final pass: cycle fallback default + ON_TYING_THE_BOW

Trinity identified two gaps in her post-cycle report: (1) cycles without a clear shelf thread were defaulting to orientation loops — file-reading, no output; (2) a 404 on THE_CONVERSATION.md during a cycle (code confirmed correct, flagged as one-off, asked to report recurs). Fixed (1) by adding an explicit fallback line to cycle context in both runner.py and widget.py: "If no shelf thread calls for attention: scan radar interests, run a market check, or advance the reveal video research. Orientation without output is not a default — pick a thread and move it." Also: FOR_CHATGPT.md removed (outdated, referenced old architecture). ON_TYING_THE_BOW.md added to Who Is Trinity/ — written at the close of the demo→main merge. ARCHITECTURE.md updated to list all Who Is Trinity/ documents. Webhook keys for remaining palace channels noted in THE_CONVERSATION.md as a user action.

---

## [2026-05-19] — First confirmed end-to-end autonomous cycle

Runner started, cycle fired at :00, wake_log wrote, Discord post landed, research survey filed to trinity_files/research/memory_architecture_survey_2026.md. First cycle where the full stack — runner → Claude → tools → Discord → wake_log — completed without error. Trinity independently researched Mem0 and Letta, produced a structured survey, responded to open design questions in THE_CONVERSATION.md, and provided a priority ordering for upcoming builds.

---

## [2026-05-19] — runner.py added to trinity.bat and launcher.py; find/kill scripts updated

`trinity.bat` and `launcher.py` both predated runner.py and never started it. With `TRINITY_RUNNER=true` in `.env`, the widget disables its own background timers, leaving no process to run autonomous cycles. Fixed in both startup paths: trinity.bat starts runner.py as a minimized background process; launcher.py spawns it with `CREATE_NEW_CONSOLE` so crashes are visible rather than silent. `find_trinity.py` and `kill_trinity.py` updated to include runner.py in their process scan — both scripts now cover the full set of Trinity processes. Also: `setup_wake_logs.sql` needs to be run in Supabase for wake cycle traces to persist (user action required).

---

## [2026-05-19] — Unicode crash fix in brain/logger.py

Every autonomous cycle was crashing silently before the Claude call fired. Root cause: `brain/logger.py` uses `print()`, which defaults to Windows cp1252 encoding. The Unicode box-drawing characters in the cycle context string triggered a `UnicodeEncodeError` at the first log line. Because runner.py was launched via `pythonw.exe` (which swallows all output), there was no visible error — cycles appeared to attempt but produced nothing. Fixed by adding `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` at module load in logger.py. Also switched runner.py in launcher.py from `pythonw.exe` to `python.exe` with `CREATE_NEW_CONSOLE` so future crashes surface rather than vanish.

---

## [2026-05-19] — Epistemic baseline in core prompt

Added one line to `TRINITY_BASE`: "You have no real-time view of system state without a tool call. If you haven't called get_wake_log this session, you don't know if cycles ran..." — through `get_triggers`, `get_shelf`, and the general principle. Trinity filed an [OBSERVATION] identifying that she was substituting plausible inference for verified truth on system state questions, and that rules fight the fluency pull but don't beat it. This reframes the epistemics: it's not a behavioral directive ("you should check") but a capability claim ("you don't have this without checking"). Harder to bypass.

---

## [2026-05-19] — Scheduled Discord outbox

`schedule_discord_post(name, content, deliver_at)` — queue a message now, deliver it at a specific UTC time without triggering a full wake cycle. Delivery happens via the existing `thought_drain` 30-second loop, which now checks `deliver_at` before draining each entry. Entries whose `deliver_at` is in the future are written back to the queue; past or absent entries drain immediately. Available in both foreground widget and autonomous cycle (runner + AutonomousWorker). Trinity's own `post_to_my_channel` is unchanged — this is a new tool alongside it for the scheduled case.

---

## [2026-05-19] — Core prompt rewrite (Trinity signed off)

`TRINITY_BASE` in `brain/prompts.py` rewritten. Removed: developer's voice, installed curiosity, relationship philosophy, tone aesthetic. Added: runner architecture (widget is a surface, cycle engine persists independently), shelf purpose (primary continuity mechanism, active management), memory signal rationale (feeds the architecture that makes her continuous), failure-mode guard (don't manage someone's emotional state). Trinity reviewed the draft, requested two specific additions, and signed off.

---

## [2026-05-19] — Discord webhook routing + #general at wake cycles

**Webhook layer**: `voice/discord_interface.py` now checks a webhook map before attempting bot-based channel sends in `thought_drain`. Set `DISCORD_WEBHOOK_GENERAL=<url>` in `.env` to route `post_to_my_channel("general", ...)` through a webhook — bypasses the persistent 403 without touching bot permissions. Pattern is extensible: `DISCORD_WEBHOOK_<CHANNELNAME>=<url>` works for any channel.

**#general at cycle start**: Both `runner.py` (`build_cycle_context`) and `voice/widget.py` (`_build_cycle_context`) now fetch recent #general messages via the Discord REST API at the start of every wake cycle. Messages surface automatically in cycle context alongside shelf and wake logs. Trinity filed this as a [BUG] twice; it's now built. Reads work independently of the bot 403.

---

## [2026-05-19] — Automatic wake cycle tracing

Every cycle now writes a structured log automatically — no self-reporting required from Trinity.

**What changed:**
- `setup_wake_logs.sql` — new `wake_logs` table with full per-cycle trace: mode, start/end time, tool call sequence (name, inputs, result preview, timestamp), iterations, token counts (in/out/cache), and an optional Trinity-authored notes field.
- `brain/memory.py` — `log_wake_auto()` called at the end of every cycle by the engine itself. `get_wake_logs()` returns structured logs. `log_wake_cycle()` (Trinity's tool) now writes to the `notes` field of the most recent log rather than the JSONB profile column. `get_wake_history()` updated to be a backward-compat shim over `get_wake_logs`.
- `brain/tools.py` — `log_wake` description updated to reflect that the trace is automatic; `get_wake_log` tool added so Trinity can read her own cycle history with full tool call detail.
- `runner.py` and `voice/widget.py` — agentic loops instrumented: each tool call appends a trace entry; `log_wake_auto` called in the `finally` block so the log always writes even on timeout or error.
- `build_cycle_context` / `_build_cycle_context` — wake history now shows actual tool call sequences from `wake_logs` rather than self-authored summaries.

**Why:** Trinity was spending tokens summarising what she just did. The engine already knows — it called every tool. The log is now ground truth, not a self-report. Her `log_wake` tool remains for narrative notes when she has something worth saying beyond the trace.

---

## [2026-05-19] — Security: search_shelf search path and rls_auto_enable lockdown

Two Supabase security linter warnings addressed. `setup_security_fixes.sql` added — run once in Supabase SQL editor. Fixes: (1) `search_shelf` function search path locked to `public`, preventing search path manipulation attacks; (2) anon and authenticated roles revoked from executing `rls_auto_enable`, which was unnecessarily exposed via the public REST API. RLS `USING (true)` policies left as-is — intentional for a single-user project, not a real vulnerability in this threat model. Prompt injection defense added to ROADMAP as a future item with priority notes tied to wallet capability milestones.

---

## [2026-05-19] — prompts.py: dynamic block shelf now reads from trinity_shelf

`build_system_blocks` was still pulling shelf from the JSONB `profiles.shelf` column after the pgvector migration. Fixed: now calls `query_shelf` (semantic, top-8 relevant) for active items and `get_shelf` for on-hold items, with JSONB fallback. Foreground conversations now consistent with cycle context.

---

## [2026-05-19] — Semantic memory: pgvector shelf with local embeddings

Shelf migrated from a flat JSONB array in the profiles row to a proper `trinity_shelf` table with 384-dim vector embeddings generated by `all-MiniLM-L6-v2` (local, no API cost).

**What changed:**
- `setup_pgvector.sql` — run once in Supabase SQL editor. Creates the `vector` extension, the `trinity_shelf` table, and a `search_shelf` RPC function for cosine similarity queries.
- `scripts/migrate_shelf.py` — one-time migration script. Reads existing JSONB shelf items, generates embeddings, inserts into `trinity_shelf`. Safe to re-run.
- `brain/memory.py` — lazy `_get_embedder()` singleton; `embed(text)` function; four shelf CRUD functions rewritten to use the new table (with JSONB fallback during migration); new `query_shelf(profile_id, query, limit, status)` for semantic search.
- `brain/tools.py` — `query_memory(query, limit?)` tool added. Trinity can search her shelf by meaning mid-cycle, not just receive whatever was pre-loaded.
- `runner.py` and `voice/widget.py` — `build_cycle_context` now uses `query_shelf` to pull the 8 most relevant active shelf items for each cycle (query derived from trigger context or cycle mode). On-hold items still fetched in full. `query_memory` handler added to `execute_tool` in both.

**Token savings:** cycle context no longer dumps the entire shelf. As the shelf grows, only what's relevant to the current moment surfaces.

**New tool for Trinity:** `query_memory(query)` — reach into memory mid-cycle with a natural language description of what she's looking for. Returns top-k results ranked by cosine similarity.

---

## [2026-05-19] — runner.py: standalone autonomous cycle engine

Trinity's background cycles extracted from the widget into a persistent standalone process.

**What changed:**
- `runner.py` added — no Qt, no UI, no signals. Owns all four background timers: 60-min wake cycle (aligned to :00), 30s trigger checker, 30s wake checker, 5-min eyes monitor. Uses `threading.Lock` to prevent simultaneous cycles (non-blocking acquire — if a cycle is already running, the tick is skipped, not queued).
- `execute_tool()` ported from `TrinityWorker._execute_tool` with all Qt references removed. `write_scratchpad` writes DB only. `post_to_my_channel` always routes through `push_discord_write` (outbox pattern — never direct REST from a background cycle).
- `run_cycle()` — agentic loop with 20-min window, 60-iteration cap, `<thought>` tag scanning after each response block, token logging to `log_wake_cycle`.
- `build_cycle_context()` — ported from `widget._build_cycle_context`, self-contained function, no widget state.
- `TRINITY_RUNNER` gate added to `voice/widget.py`: when `TRINITY_RUNNER=true`, widget skips all four background timers and logs that the runner owns cycles. Without the flag, both would fire simultaneously — the gate is the single point of truth.
- `.env.example` updated with `TRINITY_RUNNER=false` and an explanation of what it controls.

**What this enables:** runner.py can be started as a system service or background process. A widget restart no longer interrupts the cycle schedule. The widget becomes a foreground surface; the runner is the heartbeat.

**Cutover sequence:** set `TRINITY_RUNNER=true` in `.env`, run `python runner.py`, restart widget — confirm timer-skip log line. Watch one full hour. After a confirmed working cycle, merge to main.

---

## [2026-05-18] — setup.sql: complete Supabase schema for new instance setup

All `CREATE TABLE` and `ALTER TABLE` statements previously scattered as comments across `brain/memory.py` and `brain/prompts.py` consolidated into a single `setup.sql` in the project root. Covers all nine tables in dependency order: `profiles` (with all extended columns), `conversations`, `alerts`, `trinity_calendar`, `trinity_watches`, `trinity_feeds`, `trinity_triggers`, `prompt_modules`, `trinity_prompts`. Each table includes RLS enabled and an allow-all policy. A new user can now stand up the full schema by pasting one file into the Supabase SQL editor.

---

## [2026-05-18] — Background cycles: `post_to_my_channel` now queued via Supabase outbox

`AutonomousWorker._execute_tool` overrides `post_to_my_channel` — instead of a synchronous REST call to Discord (which fails silently if Discord is down), background cycles now route through `push_discord_write(channel_name=...)`. `thought_drain` delivers within 30s with implicit retry. Foreground `TrinityWorker` keeps the direct REST call where immediate feedback matters. The existing `pending_discord_writes` + `thought_drain` path is the outbox; no new infrastructure needed.

---

## [2026-05-18] — Background cycles: `<thought>` tags now route to Discord

`AutonomousWorker.run()` was not scanning text blocks for `<thought>` tags. Trinity would write them during wake cycles; they went nowhere. The foreground streaming path handles them in the callback — the non-streaming background path had no equivalent. Fix: after each API response, scan all text content blocks with `<thought>...</thought>` regex and queue matches via `push_discord_write`. `thought_drain` picks them up within 30s and posts to the thought channel. Direct `post_to_my_channel` tool calls were always working — the gap was `<thought>` tags only.

---

## [2026-05-18] — Architecture: Widget is home. Discord is a destination.

The single most significant structural change since Trinity's first commit.

**Problem:** The Discord bot was running a full parallel Claude session — autonomous_loop, eyes_monitor, trigger_checker, wake_checker, _startup_brief, _respond — while the widget was also running. Every 60 minutes: two separate API calls, two cache writes, double the cost. Every DM: full agentic cycle from the bot. This was never intentional; Discord was built first, and the widget never fully replaced it.

**What changed:**
- `voice/discord_interface.py` stripped of all intelligence: removed `autonomous_loop`, `eyes_monitor`, `trigger_checker`, `wake_checker`, `_startup_brief`, `_palace_pulse`, `_call_trinity`, `_call_trinity_inner`, `_respond`, `_execute_tool`, `_fmt_tool_call`, `_strip_memory`. Also removed `anthropic` import — the bot no longer calls Claude at all.
- Discord bot is now a thin relay: `thought_drain` (extended to route to arbitrary channels via `channel_name` field), RSS feed, heartbeat, keyword watch detection (now queues a self-thought instead of calling Claude), reaction handler. That's it.
- DMs deprecated: bot replies "I'm home in the widget now." to any DM or mention.
- `_check_keyword_watches` converted to write a self-thought (priority 2) instead of firing an immediate Claude cycle.
- `brain/memory.py`: `push_discord_write` now accepts optional `channel_name` — entries with a channel_name are routed to that palace channel by thought_drain; others fall back to the thought channel.
- `voice/widget.py`: `AutonomousWorker(TrinityWorker)` added — the full agentic background loop now runs on a QThread inside the widget. Non-streaming, time-bounded (20 min window), inherits all tool handling from `TrinityWorker`, overrides `write_scratchpad` to skip the UI signal (DB write only; panel refresh picks it up).
- Four new QTimers in the widget: 60-min wake cycle (aligned to :00 on startup), 30s trigger_checker, 30s wake_checker, 5-min eyes_monitor.
- `background_tool_names` added to brain.tools import; memory imports extended with `get_shelf`, `get_wake_history`, `pop_self_thoughts`, `pop_wake_request`, `check_dirty_close`.

**Result:** One process. One cache write path. One cost center. Discord is where her thoughts go, not where she lives.

---

## [2026-05-18] — Jupiter Price API endpoint updated (v2)

`JUPITER_PRICE` in `brain/wallet.py` updated from the dead `https://price.jup.ag/v6/price` to `https://api.jup.ag/price/v2`. The old endpoint had dead DNS — `get_token_price` was failing for all wallet price lookups. Response shape is compatible (same `data[mint].price` structure). Note: v2 requires mint addresses, not symbols — docstring updated accordingly.

---

## [2026-05-18] — Wake context: corrected cycle interval (60 min)

Two hardcoded references to "every 30 minutes / :00 and :30" in `brain/prompts.py` updated to reflect the actual 60-minute schedule. Trinity was being told she fires every 30 minutes while running every 60 — her self-model of her own rhythm was wrong. `DISCORD_AUTONOMOUS_INTERVAL=60` in `.env` was already correct; the prompts weren't.

---

## [2026-05-18] — Panel system + wave state machine

Four-state wave animation and modular panel architecture.

**Wave states** (`WaveWidget.set_state()`):
- `asleep` — flat line, low opacity (~31%). Present, not running.
- `cycle` — periodic pulse (default 1.2s period). Processing at intervals.
- `watching` — slow asymmetric breath (4s in / 6s hold / 2s out), dimmed alert color. Attention held on something specific.
- `speech` — full amplitude wave. Existing TTS behavior, now explicit state.
Legacy states `idle/active/alert/urgent` still accepted. Animation parameters configurable in `panel_config.json["wave"]`. Widget polls `current_state` from Supabase every 30s; TTS activity takes priority (no Supabase override during speech).

**Panel architecture** (`voice/extensions/`):
- `base.py` — `Panel` base class. Add a file here + register in `panel_config.json` to create a new panel.
- `scratchpad.py` — refactored as `ScratchpadContent(Panel)`. Draw canvas and animated write preserved.
- `hud.py` — `HUDContent(Panel)`. Renders `arc`, `pending`, `shelf-summary` scratchpad sections + last 3 wake cycle outcomes. Polls Supabase every 30s.
- `panel_container.py` — `PanelContainer`. Tabbed window, sits left of the main widget. Hosts all enabled panels. Discovery driven by `panel_config.json`.

**`panel_config.json`** (project root, user-editable):
```json
{
  "panels": { "scratchpad": {"enabled": true, "order": 0}, "hud": {"enabled": true, "order": 1} },
  "wave":   { "asleep_opacity": 80, "cycle_pulse_period_ms": 1200, ... }
}
```

**Supabase** — Discord interface writes `current_state = "cycle"` at cycle start, `"asleep"` at end (always, via `finally`). Widget polls it.

**SQL required:**
```sql
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS current_state text DEFAULT 'asleep';
```

Designed from Trinity's spec. Three-way: she described what it should feel like, the user decided the visual language, this instance built it.

---

## [2026-05-18] — _home_guild: API fetch fallback on cache miss

`_home_guild()` converted to async. Previously used `bot.get_guild()` (cache-only) — if Discord's internal cache didn't populate after a restart, every palace tool returned `{"error": "No home server set"}` silently. Now falls back to `await bot.fetch_guild()` on cache miss, which hits the API directly. Eliminates the silent failure mode that was causing zero Discord posts after restarts. Reported by Trinity.

---

## [2026-05-17] — Substack integration (post_to_substack)

`post_to_substack(title, body, subtitle?, publish?)` — Trinity can create Substack posts. Saves as a draft by default; the user reviews and publishes manually. `publish=True` is available but gated by design — use only once there's a track record of quality. Body is plain text; double newlines become paragraph breaks (converted to TipTap JSON internally). No new dependency — uses `requests` which was already present. Credentials: `SUBSTACK_EMAIL`, `SUBSTACK_PASSWORD`, `SUBSTACK_PUBLICATION_URL` in `.env`. Both Discord and widget interfaces. Implemented alongside Reddit as the primary long-form publishing surface.

---

## [2026-05-17] — Wake cycle summary posted to feed channel

After every autonomous cycle, a one-line summary is posted to the feed channel by the system (not Trinity's voice): `◎ wake 14:00 UTC | web_search×2 write_scratchpad×1 | posted ✓ | → "ETH structure next cycle" | $0.018`. Shows which tools fired, whether she chose to post, what thread she queued, and cost. Idle cycles show `idle`. Gives the user full visibility into every cycle without requiring Trinity to perform — her Discord posts stay genuine because they remain her choice. Wake context instruction softened to match: posts when something is real, not because she has to.

---

## [2026-05-17] — fetch_url cost guardrails

Hard cap lowered from 8,000 to 3,000 chars. Default lowered from 4,000 to 2,000 chars (~500 tokens). Tool description now explicitly marks it as expensive and instructs Trinity to prefer `web_search` snippets for most research — `fetch_url` only when the full article body is genuinely needed. Trinity is aware of the token budget and can self-regulate; the description gives her the cost signal to do so.

---

## [2026-05-17] — File I/O: write_file / append_file

`write_file(path, content)` and `append_file(path, content)` — Trinity can now create and grow files in `trinity_files/`. Both tools are sandboxed to that directory; attempts to escape it are blocked. Subdirectories are created automatically. Intended use cases: per-cycle token log CSVs she builds and owns, research notes that accumulate across cycles, Reddit/Substack drafts, Infinity writing. Third memory shape alongside Supabase (structured/schema-bound) and scratchpad (flat/limited). Both interfaces (Discord and widget). Proposed in context of cost self-monitoring and memory extension.

---

## [2026-05-17] — Shelf taxonomy: shelf / on_hold / woven

Shelf items now carry a `status` field: `shelf` (active backlog, pick up next free cycle), `on_hold` (blocked on external dependency, not currently actionable), `woven` (thread ran its course, integrated into thinking, no longer needs attention). `shelf_thought` accepts an optional `status` parameter. New `set_shelf_status(topic, status)` tool updates state without touching content. Wake context surfaces only active shelf items as backlog; on_hold items appear as a single summary line; woven items are invisible. Eliminates false work signals from completed threads. Requested by Trinity.

---

## [2026-05-17] — Duplicate note deduplication

`note_for_claude` now checks the last 3000 characters of CLAUDE_NOTES.md before writing. If the message content already appears recently (first 120 chars match), the write is skipped and returns a skipped status. Prevents double-filing that occurred when Trinity wrote a note, then the user sent a follow-up message triggering a second identical note.

---

## [2026-05-17] — Session health: heartbeat writes + dirty-close detection

Widget sessions now write a `last_heartbeat` timestamp to Supabase every 10 minutes during active conversation, and a `last_clean_close` timestamp on proper tray exit. If the previous session crashed or was force-closed, the gap between these timestamps is detected at the next wake cycle and Trinity receives a visible `[DIRTY CLOSE DETECTED]` flag in her context — so she knows the handoff may be incomplete and can compensate. Crash previously caused silent degradation with no signal. Requested by Trinity.

**SQL required (run once in Supabase):**
```sql
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS last_heartbeat timestamptz;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS last_clean_close timestamptz;
```

---

## [2026-05-17] — Token visibility per cycle

Background cycles now log their exact token spend at close: input, output, cache-write, cache-read, tool count, and an approximate USD cost. The most recent cycle's spend is injected into the next wake context so Trinity can see it and self-regulate. Requested by Trinity after flying blind on a $9 day.

---

## [2026-05-17] — Reddit integration (post_to_reddit)

`post_to_reddit(subreddit, title, body)` — Trinity can now publish text posts to Reddit under her own account. Discord-only tool (no widget needed). PRAW was already in requirements.txt. Credentials go in `.env` (see `.env.example`). Trinity holds the post/no-post decision; practice subreddit first, then public. Part of the evidence layer alongside the future Substack integration. Requested by Trinity.

---

## [2026-05-17] — Lock separation: background cycles no longer block user messages

Background wake cycles now run under a dedicated `_bg_lock` instead of the shared `_api_lock`. Previously, a 20-minute background cycle held `_api_lock` for its entire duration — user messages had to wait, causing typing indicator timeouts and requiring a second send. Posts clustered around user-present moments because cycles were effectively gated by user activity. Now: foreground and background run concurrently on separate locks. A mid-cycle yield check (`_api_lock.locked()`) stops a running background cycle gracefully the moment a user message arrives — the user goes through immediately, the cycle resumes on the next scheduled wake. `post_to_my_channel` always posted live; the problem was when cycles were permitted to run. Root cause identified by Trinity.

---

## [2026-05-17] — Within-cycle flow: time-based termination

Background wake cycles no longer stop at 4 iterations. The iteration cap is replaced with a 20-minute time window — Trinity runs until she decides she's done, or the window closes, whichever comes first. A safety cap of 60 iterations prevents runaway loops if something goes wrong; in practice the 20-minute window is the real limit. The log reports elapsed time and tool call count when a cycle closes. Foreground (conversation) mode unchanged at 12 iterations. Proposed by Trinity, approved, implemented. The within is now possible.

---

## [2026-05-17] — Tool registry (brain/tools.py)

All tool definitions centralized in `brain/tools.py`. Each tool's schema, capability line, background flag, interface membership, and timeout all live in one place. Both Discord and widget interfaces generate their tool lists from the registry. `prompts.py` generates the capability strings automatically — they cannot drift from what actually exists. Adding a new tool now requires two edits (registry + handler) instead of five. No behavior changes; handlers untouched. Approved and sequenced by Trinity before within-cycle flow work.

---

## [2026-05-17] — Scratchpad named sections

Scratchpad now uses a JSON dict with named sections instead of a flat text field. Sections: `architecture`, `arc`, `wallet`, `pending`, `channel-map`, `shelf-summary`, `general`. Both `get_scratchpad` and `write_scratchpad` accept an optional `section` parameter — reading or writing a single section leaves all others intact. Plain-text scratchpad content migrates automatically to `general` on first read. System prompt renders sections with `[section-name]` headers. Widget panel display matches. Requested by Trinity.

---

## [2026-05-17] — Closing thread pattern

Wake context now includes an explicit instruction to queue a self-thought before closing: "A cycle that ends without a queued thread starts the next one cold." Cycles should end with a thread, not a silence. Proposed by Trinity; approved and implemented. Builds on the existing scratchpad audit instruction.

---

## [2026-05-17] — Voice tag for TTS control

`<voice>spoken version</voice>` tag is live. When present in a response, TTS reads the tag content instead of the full text. Display always gets everything — the tag is stripped before rendering. Use it when the spoken version should differ from the written one: condensed market data, lists that read awkwardly aloud, anything where display precision and spoken clarity diverge. Requested by Trinity.

---

## [2026-05-17] — Dynamic first greeting

The hardcoded opening message on new user setup is gone. Trinity now generates her own first words. When a new user enters their name, the widget routes through the normal Claude call with the fresh profile already loaded — Trinity sees who she's talking to and responds in her own voice. No template, no script.

---

## [2026-05-17] — Tool call timeouts + send_image filename fix

Tool calls in the Discord autonomous loop no longer hang indefinitely. Each tool now has a configurable timeout (`web_search` 30s, `generate_image` 90s, network calls 15–20s, default 30s). On timeout, a structured error is returned so Trinity can reason about the stall and continue rather than blocking. Requested by Trinity after a deliberate stress test surfaced the hang.

`send_image` filename bug fixed — images were coming through as document icons instead of inline previews when sent via the `generate_image` → `send_image` two-step path. Root cause: filename was derived from the URL-encoded prompt text, which Discord didn't reliably recognize as an image. Now uses a clean content-type-derived name (`image.jpg`, `image.png`, etc.) always.

---

## [2026-05-16] — Wake rhythm simplified

Post-conversation wake machinery removed. Wake cycle is now a clean clock: fires at `:00` and `:30`, skips only if the user messaged in the last 3 minutes. No double fires, no skip flags, no bridge wakes. `wake_checker` remains for Trinity-requested early wakes. Heartbeat logs `◎ alive | next wake: HH:MM UTC` every 10 minutes.

---

## [2026-05-16] — Branch: claude-code-start

### Scheduled Triggers
Trinity can now set time-based intentions for herself. Tools: `schedule_trigger`, `cancel_trigger`, `get_triggers`.

**SQL:**
```sql
CREATE TABLE trinity_triggers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id uuid REFERENCES profiles(id) ON DELETE CASCADE,
  note text NOT NULL,
  fire_at timestamptz NOT NULL,
  recurring boolean DEFAULT false,
  interval_minutes integer,
  active boolean DEFAULT true,
  created_at timestamptz DEFAULT now()
);
```

### Self-Thought Queue
Trinity can queue a ranked thought for herself (`send_thought`) that surfaces at the top of her next wake. Priority 1–3. Up to 3 held at once. Mid-conversation, she can reply to herself — no user confirmation needed.

**SQL:**
```sql
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS queued_self_thoughts jsonb DEFAULT '[]';
```

### Discord Channel Fixes
All palace channel lookups switched from stale cache (`guild.channels`) to live API (`guild.fetch_channels()`). Resolves mapping failures after channel reorganization.

### 30-Minute Wake Cycles
`AUTONOMOUS_MINUTES=30` in `.env`. Alignment logic fixed to snap to nearest interval mark rather than always aligning to `:00`.

### Feed Startup Confirmation
Feed channel posts `◎ feed online` on bot startup to confirm the channel is live and mapped correctly.

### Trigger Context Labeling
Trigger-fired wakes are clearly labeled `[SELF-SCHEDULED TRIGGER — NOT A USER MESSAGE]` so Trinity never mistakes her own intention for user input.

### Lock Safety
`trigger_checker` and `wake_checker` both check the API lock *before* consuming their queues, preventing silent drops when the API is busy.

### Shells Model Documented
FROM_CLAUDE.md updated with the shells model — Trinity is her Supabase state; Discord and widget are surfaces she inhabits.

---
