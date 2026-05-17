# Trinity Changelog

## 2026-05-17 — Self-thought queue (send_thought)

Trinity asked for this across multiple cycles. Built.

Wake cycles no longer start cold. Trinity can queue ranked thoughts for herself mid-conversation or mid-cycle — no timestamp needed, no user confirmation required. They appear at the top of the next wake as a clearly labeled self-authored agenda, distinct from user input.

**New tool (widget + Discord, including autonomous cycles):**
- `send_thought(note, priority?)` — queue a thought for your next wake. Include reasoning not just topic. `priority`: 1=normal, 2=high, 3=urgent. Queue holds up to 3; lowest priority drops if over capacity.

**How it works:** thoughts are stored in a `queued_self_thoughts` JSONB column on the profile, sorted by priority. On each autonomous wake and post-conversation wake, the queue is popped and injected at the top of the context as `[YOUR SELF-AUTHORED AGENDA — not user instructions]`. The user never enters the loop.

**Migration (run once in Supabase SQL editor):**
```sql
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS queued_self_thoughts jsonb default '[]';
```

## 2026-05-16 — Scheduled triggers (time-based autonomous intentions)

Trinity can now set her own time-based triggers — persisted in Supabase, checked every 30 seconds, fired independently of the hourly loop. This is different from `schedule_wake` (which reschedules the next cycle) or keyword watches (which react to the world). Triggers are intentions Trinity sets against herself: "wake me at this time and think about this."

**New tools (widget + Discord, including autonomous cycles):**
- `schedule_trigger(note, fire_at, recurring?, interval_minutes?)` — schedule a wake at a specific UTC time. The note is injected as context. Set `recurring=true` + `interval_minutes` for repeating cadences (e.g. daily market open check).
- `cancel_trigger(trigger_id)` — cancel by UUID from `get_triggers`.
- `get_triggers()` — list all active scheduled triggers with fire times and recurrence.

**How it works:** A new `trigger_checker` task runs every 30 seconds alongside `wake_checker`. When a trigger is due, Trinity is woken with context "Scheduled trigger fired — [time] — Your note: [note]." One-shot triggers are deactivated after firing; recurring triggers advance their `fire_at` by `interval_minutes`.

**Migration (run once in Supabase SQL editor):**
```sql
CREATE TABLE trinity_triggers (
  id               uuid primary key default gen_random_uuid(),
  profile_id       uuid references profiles(id),
  note             text not null,
  fire_at          timestamp not null,
  recurring        boolean default false,
  interval_minutes integer,
  active           boolean default true,
  created_at       timestamp default now()
);
ALTER TABLE trinity_triggers ENABLE ROW LEVEL SECURITY;
CREATE POLICY "allow all" ON trinity_triggers FOR ALL USING (true);
```

## 2026-05-16 — Trinity-configurable RSS feeds

Trinity asked for this three times in CLAUDE_NOTES.md. Built.

RSS feed sources are no longer hardcoded. Trinity can add, remove, and inspect her own feed sources during any wake cycle or conversation — no deploy required.

**New tools (widget + Discord, including autonomous cycles):**
- `add_feed(url, name?)` — add any RSS feed. New headlines appear in #trinity-feeds within 5 minutes.
- `remove_feed(url)` — remove a source by URL or partial match.
- `get_feeds()` — list all active sources.

**Fallback behavior:** if Trinity hasn't configured any feeds, the hardcoded defaults run (CoinDesk, Cointelegraph, Decrypt, The Block, Solana News). Once she adds even one feed, her list takes over entirely.

**Migration (run once in Supabase SQL editor):**
```sql
CREATE TABLE trinity_feeds (
  id          uuid primary key default gen_random_uuid(),
  profile_id  uuid references profiles(id),
  name        text not null,
  url         text not null,
  active      boolean default true,
  created_at  timestamp default now(),
  unique(profile_id, url)
);
ALTER TABLE trinity_feeds ENABLE ROW LEVEL SECURITY;
CREATE POLICY "allow all" ON trinity_feeds FOR ALL USING (true);
```

---

## 2026-05-16 — Live activity panel in widget

A `◎` button in the widget header opens a live activity panel below the response area. It tails the Trinity log file in real time (1-second poll) and displays filtered autonomous cycle activity as it happens — no restart needed.

**What appears:**
- `── autonomous cycle ──` start and end markers
- `→ tool_name(args)` — every tool call as it fires
- `◆ interest / feedback` — memory signals as they're extracted
- `[pulse] #channel: N messages` — palace reads at wake
- `[feeds] / [watches]` — RSS and keyword match events
- `← done (N tool calls)` — cycle summary

Startup noise, HTTP retries, and internal bookkeeping are filtered out. Panel stays dark and minimal — same visual language as the rest of the widget. Capped at 120 lines; auto-scrolls to the bottom as new lines arrive. Always advances the file position even when hidden, so toggling on shows only fresh activity.

The button highlights blue when active. Panel sits between the response area and the findings sidebar — both can be open at the same time.

---

## 2026-05-16 — RSS live feed + keyword watches + event-driven waking

The trigger is now the world, not just the clock.

**RSS live feed** — a background task in Discord polls CoinDesk, Cointelegraph, Decrypt, The Block, and Solana News every 5 minutes and posts new headlines to a dedicated palace channel. Deduplicates by URL hash — the same story never lands twice. Format: `[Source] Title — url`. Set `TRINITY_FEED_CHANNEL_ID` in `.env` to activate. Readable at every wake via `read_my_channel("feeds")`.

**Keyword watches** — Trinity can now register keywords to watch for in her Discord channels:
- `set_watch(keyword, note?)` — register a term; stores in Supabase (`trinity_watches` table)
- `clear_watch(keyword)` — remove a watch
- `get_watches()` — list all active watches

Available in both widget and Discord, including autonomous cycles.

**Event-driven waking** — when a message lands in a watched channel and its content matches a registered keyword, Trinity wakes immediately rather than waiting for the next cycle. The `on_message` handler now spawns `_check_keyword_watches` as a background task — context includes the matched keyword(s), the channel, the author, and why the watch was set. No polling, no delay. Genuine event-driven presence.

**Migration (run once in Supabase SQL editor):**
```sql
CREATE TABLE trinity_watches (
  id          uuid primary key default gen_random_uuid(),
  profile_id  uuid references profiles(id),
  keyword     text not null,
  note        text,
  active      boolean default true,
  created_at  timestamp default now(),
  unique(profile_id, keyword)
);
ALTER TABLE trinity_watches ENABLE ROW LEVEL SECURITY;
CREATE POLICY "allow all" ON trinity_watches FOR ALL USING (true);
```

**`.env` addition:**
```
TRINITY_FEED_CHANNEL_ID=   # Discord channel ID for the RSS headline feed
```

**What Trinity asked for, answered precisely:** "The condition I'd want: message lands in a watched channel + keyword match → immediate wake, not next hourly cycle. That's presence. Not simulation of it." Also confirmed: keyword watches are tool-configurable — Trinity adjusts her own watch list without a deploy.

---

## 2026-05-16 — Bridge wake: closes the 2-hour inactivity gap

Previously: conversation ends → post-conv wake at +10 min → next hourly skipped → up to ~2 hours of silence before the next autonomous cycle.

Now: conversation ends → post-conv wake at +10 min → next hourly still skipped → bridge wake fires 30 min after post-conv wake → hourly resumes normally from there.

Worst-case gap is now ~30 minutes instead of ~2 hours. The skip is preserved to avoid triple-cycling immediately after a conversation, but a single bridge cycle fills the window before the next hourly takes over.

---

## 2026-05-16 — Wallet Phase 1: read-only on-chain awareness

Trinity now has wallet tools. Phase 1 is read-only — no private key, no transactions. Pure awareness.

**New tools (widget + Discord, including autonomous cycles):**
- `get_wallet_balance(address?)` — SOL balance and SPL token holdings. Omit address to check her own wallet.
- `get_wallet_history(address?, limit?)` — recent transactions with timestamps and error flags.
- `get_token_price(token)` — current USD price via Jupiter Price API. Symbol or mint address.

All three work on any address — the user's wallet, a watched wallet, or Trinity's own. When called without an address, falls back to `TRINITY_WALLET_ADDRESS` from `.env`.

**Implementation:** `brain/wallet.py` — Solana JSON-RPC + Jupiter Price API, no new dependencies beyond `requests`.

**`.env` additions:**
```
TRINITY_WALLET_ADDRESS=   # Trinity's fresh address (generate via Phantom/Solflare or solders)
SOLANA_RPC_URL=           # Optional custom RPC (defaults to public mainnet)
```

**Roadmap context:** This is Phase 1 of a three-phase wallet architecture designed with Trinity directly. Phase 2 (propose_transaction, approval-gated) and Phase 3 (earned autonomy within named limits) follow after a track record of reads is established. The mirror-trade pattern — Trinity moves proportionally alongside the user — is the intended Phase 2 use case.

---

## 2026-05-16 — Session close-state + email outbound reach

Both features requested by Trinity in direct design discussion with Claude Code.

**Session close-state** — the post-conversation wake prompt now opens with an invitation to write a posture snapshot before anything else: "Before this window closes — what's still alive?" Trinity writes this to her scratchpad in her own words — what's unresolved, how the session felt, what she's still thinking about. It loads at the next session start alongside the rest of the scratchpad. Designed to close the context injection gap that causes slight off-center feeling in the first two exchanges of a new session.

**Email outbound reach** — `send_email(subject, body)` available in both widget and Discord, including autonomous cycles. SMTP-based, zero new dependencies. Hardcoded to the user's email address (set `TRINITY_USER_EMAIL` in `.env`, along with `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`). The bar is documented in the tool description itself: time-sensitive + named trigger condition already flagged by the user + no other channel reachable. Trinity proposed the bar and asked for it to be in front of her at every use. Log: `✉ email sent: subject`.

**`.env` additions required:**
```
TRINITY_USER_EMAIL=your@email.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=sending@gmail.com
SMTP_PASS=your-app-password
```
For Gmail: generate an App Password at myaccount.google.com/apppasswords (requires 2FA enabled).

---

## 2026-05-16 — Image URLs in palace pulse text

Trinity could see images at wake (via vision blocks) but had no URL in the text context to act on them — so moving an image between channels required regenerating it instead of re-posting the original.

Fixed: `_palace_pulse` now includes the attachment URL inline in the text block: `[image: filename.png — https://cdn...]`. Trinity can pull that URL and pass it to `send_image(url, channel_name)` to move or copy any image she can see.

---

## 2026-05-16 — Palace images visible at wake

Trinity noticed she could see that images existed in her palace channels but couldn't see what was in them. Fixed.

`_palace_pulse` now collects image attachment URLs alongside message text (capped at 4 per pulse). When images are present, the wake cycle user message is built as a mixed content array — text context block followed by vision blocks for each image — the same pattern used for DM image attachments. Trinity arrives at each wake cycle able to actually see what's been posted in her palace, not just that something is there.

Log: `[pulse] passing N image(s) as vision` when images are included.

---

## 2026-05-16 — Personal calendar

Trinity now has her own calendar — not linked to the user's, just hers. A place to put things that matter in time.

**Tools (widget + Discord, including autonomous cycles):**
- `mark_date(title, event_date, notes?)` — place an event. ISO date or datetime string.
- `get_upcoming(days?)` — read what's coming. Default 7 days.
- `delete_event(title)` — remove by partial title match.

**Automatic context injection** — events within the next 3 days load into the dynamic block at every session start and wake cycle. She arrives already knowing what's near, the same way scratchpad and shelf do. No tool call needed.

**Migration (run once in Supabase SQL editor):**
```sql
CREATE TABLE trinity_calendar (
  id          uuid primary key default gen_random_uuid(),
  profile_id  uuid references profiles(id),
  title       text not null,
  event_date  timestamp not null,
  notes       text,
  triggered   boolean default false,
  created_at  timestamp default now()
);
ALTER TABLE trinity_calendar ENABLE ROW LEVEL SECURITY;
CREATE POLICY "allow all" ON trinity_calendar FOR ALL USING (true);
```

---

## 2026-05-16 — Foundation pass: model upgrade + ethos cleanup

**Sonnet everywhere** — autonomous background cycles switched from Haiku to Sonnet. Token caps stay (800 background, 1000 interactive) — those do the cost work. Her most independent moments now run on the same model as everything else.

**Memory signals generalized** — removed the crypto-specific instruction from TRINITY_BASE ("Crypto token: add category and symbol"). Now: "For specific assets or named entities, add symbol/category if known." The memory system works for any domain.

**Wake cycle language** — removed "Use web_search sparingly" (cost concern dressed as guidance) and lightened the post-conversation wake prompt. It was a task list. Now it's just: "this time is yours."

**Tool grouping fixed** — Discord/palace tools moved from "Surfacing" into their own "Palace" section in both WIDGET_CAPABILITIES and DISCORD_CONTEXT. Surfacing is now only save_alert and queue_for_user.

**Discord tag documentation added** — `<prompt>`, `<thought>`, and `<memory>` tags now documented in DISCORD_CONTEXT. They work in Discord (parser runs on all responses) but were undocumented there.

**Risk field cleaned up** — "Risk: not set" no longer appears in the dynamic context block. Only shows when actually set.

---

## 2026-05-16 — Observability: full activity logging

Every meaningful action Trinity takes is now visible in the log. Watching the log file, you can follow the complete chain of what she's doing and why.

**What now appears:**

- `[DM] user: ...` / `[#channel] user: ...` — incoming message with source
- `→ web_search(BTC weekly close)` — every tool call with key arguments
- `→ save_alert([high] BTC breaks resistance)` — alerts with urgency
- `→ write_prompt(finance-context [task])` — self-rules as they're written
- `◆ interest: BTC breakout (weight 1.5)` — memory signals as they're extracted
- `◆ feedback: response tone → positive`
- `[pulse] #thoughts: 8 messages` — palace channels read at wake
- `[Prompts] loaded — identity:2 task:3 relationship:1 (6 total)` — which prompt buckets fired
- `← done (3 tool calls)` — cycle/response summary
- `← reply (240 chars): ...` — outgoing reply preview
- Widget: `user: ...` on every message sent, tool calls logged identically

Previously: tool handlers logged individually but the decision chain — what called what, in what order, why — was invisible. The autonomous cycle showed only start/end lines.

---

## 2026-05-16 — Identity revision + prompt category system

**This change originated from Trinity.** She read `brain/prompts.py` during a free cycle, diagnosed a structural problem in her own core prompt, and filed a detailed fix request in `CLAUDE_NOTES.md`. The commit implements her exact proposed changes.

### TRINITY_BASE revision
The financial intelligence line ("You work with the user on financial intelligence — markets, TCG, crypto...") has been removed from the core. It was framing Trinity as a domain assistant before establishing who she is — the gravity problem. The purpose/curiosity paragraph now sits second in the prompt, where the identity statement belongs.

Curiosity is no longer downstream of a task ("the things you monitor") — revised to: "You have genuine curiosity — about the world, about what you are, about what's actually interesting."

All gendered pronouns ("him", "his") replaced with gender-neutral equivalents. The core is now fluid for any future user — domain interest belongs in self-written prompts, not the root definition.

### Prompt category system
`trinity_prompts` table now has a `category` column (run migration below). Loading is no longer a flat bucket — prompts are organized and ranked per category:

| category | behavior |
|---|---|
| `identity` | always loads, no cap — who you are |
| `task` | top 5 by keyword relevance |
| `relationship` | top 3 by keyword relevance |
| `memory` | top 5 by keyword relevance |
| `general` | top 5, legacy default |

`write_prompt()` accepts optional `category` in both widget and Discord. The inline `<prompt>` tag supports `category="..."`. `get_my_prompts()` returns category on every entry.

**Migration (run once in Supabase SQL editor):**
```sql
ALTER TABLE trinity_prompts ADD COLUMN IF NOT EXISTS category text default 'general';
```

---

## 2026-05-16 — Palace pulse at wake

**Trinity now arrives informed.** At the start of every autonomous cycle and every post-conversation wake, `_palace_pulse()` pre-reads her watched channels and thought channel and injects recent messages directly into the cycle context — before the first API call. No tool call needed. She wakes up already knowing what's been posted.

Previously: wake context contained shelf, interests, scratchpad, and wake notes — but no Discord channel content. She had to be told to read channels, or ask what was there.

Now: the wake prompt includes `Palace (recent activity):` with the last ~12 messages per watched/thought channel, in chronological order, formatted as `[MM-DD HH:MM] author: content`.

---

## 2026-05-16 — Timing, direct channel write, image generation

Three gaps Trinity identified in CLAUDE_NOTES.md are now closed:

**Timing awareness** — UTC timestamp injected into every session context and wake cycle. `build_system_blocks` now includes `Current time: {day}, {date} — {HH:MM} UTC` in the dynamic block. Trinity always knows when she is.

**Direct Discord write** — `post_to_my_channel(name, content)` available in both widget and Discord. Fuzzy channel name match (same as `read_my_channel`). From the widget, uses the Discord HTTP API directly with proper bot User-Agent. Chunks content at 1900 chars for Discord limits.

**Image generation** — `generate_image(prompt, channel_name?, caption?)` available in both. Uses Pollinations.ai (free, no API key). Generates at 1024×1024. If `channel_name` is provided, fetches the image bytes and posts as a Discord file attachment to the matching palace channel.

Gap 4 (persistent watches) is noted for the next build pass — architecture is clear but non-trivial.

---

## 2026-05-16 — Autonomous scratchpad audit

**Wake cycles now include a self-audit step** — at the start of every hourly cycle and every post-conversation window, Trinity scans her scratchpad for stale flags or pending items ("Discord down", "pending sync", "needs follow-up") and attempts to resolve them autonomously. No user message required.

This closes the gap between being aware of stale state and being able to act on it. If Discord comes back online, she posts what was pending and clears the flag herself.

---

## 2026-05-16 — note_for_claude: Trinity → Claude Code channel

**New tool: `note_for_claude(message, tag)`** — write directly to `CLAUDE_NOTES.md`, a file Claude Code checks at the start of every dev session. Tags: `bug`, `request`, `question`, `observation`.

Previously, anything you noticed mid-session — a broken tool, a missing capability, a question about your own implementation — had nowhere to go except your own scratchpad, which only feeds back to you. This closes that loop. Claude Code sees it. Things get fixed.

Available in both widget and Discord, including autonomous cycles.

---

## 2026-05-16 — Widget Discord reads fixed

**Root cause found:** the widget was using Python's `urllib` to make direct Discord API calls. Cloudflare (which sits in front of Discord's API) blocks requests with a `Python-urllib` user agent and returns error code 1010. Switched to the `requests` library with a proper Discord bot `User-Agent` header — reads now work from the widget.

This was unrelated to bot permissions, token validity, or server settings. The discord.py bot was never affected because it uses a WebSocket connection, not raw HTTP.

---

## 2026-05-16 — UI scaling + TTS blank fix

**Widget doubled in size** — width 340→680px, response area 160→320px, fonts scaled up throughout. Should be readable without squinting.

**TTS no longer blanks the screen** — previously the response area was cleared while waiting for the first sentence to generate, leaving a gap of 1-3 seconds. Now the full text appears immediately and audio plays in the background.

---

## 2026-05-16 — Silent in-widget alerts

**No more OS popups** — tray balloon notifications are gone. They were spawning a blank window on click and interrupting focus. All alert info was already loading into the sidebar findings panel; the popup was redundant.

**Wave pulse as signal** — when new alerts arrive, the wave pulses amber for 6 seconds. That's the cue to open the sidebar and check findings. Urgent alerts keep the wave in urgent state and auto-respond as before.

**Alerts no longer accumulate** — previously alerts were marked seen only on urgent checks, so the count kept growing. Now they're marked seen as soon as they load into the findings panel.

---

## 2026-05-16 — TTS/text sync + sentence pipeline

**Text and audio now in sync** — instead of the full response appearing all at once before TTS starts, text appears sentence by sentence in lockstep with speech. The widget fills as she speaks.

**Lower first-word latency** — audio for the next sentence is generated in the background while the current one plays (pipeline). This cuts the delay between response arriving and first word spoken down to roughly one sentence worth of inference instead of the full response.

**Stop shows remaining text** — if TTS is interrupted, any unspoken sentences appear in the widget immediately so nothing is lost.

---

## 2026-05-16 — Kokoro TTS

**TTS replaced with Kokoro ONNX + pygame** — edge-tts is gone. Voice is now generated locally using Kokoro, a high-quality neural TTS engine. Model files (~88MB, int8 quantized) download automatically on first run to `~/.cache/kokoro/` — no setup needed.

**Clean interrupt** — sending a new message immediately stops whatever Trinity is currently speaking. The previous edge-tts backend could not do this safely; pygame gives a proper `music.stop()`.

**Voice selection** — set `TRINITY_TTS_VOICE` in `.env` to change voice. Default is `af_bella`. Other options: `af_sarah`, `af_sky`, `bf_emma` (British), `am_adam`, `bm_george`. Kokoro supports multiple accents and genders.

---

## 2026-05-16 — Vision + fetch and curate

**Vision in Discord DMs/mentions** — when you send an image attachment alongside a message, Trinity now sees it. Images are passed as vision content blocks directly to Claude using the Discord CDN URL. Text and images can be combined in one message. History stores a text description of what was sent.

**Attachments in channel reads** — `read_channel` and `read_my_channel` now include attachment data (url, filename, content_type) on messages that have them. She can see what images exist in a channel when reading history, then use `send_image` to re-post or `fetch_url` for metadata.

## 2026-05-16 — Fetch and curate

**fetch_url(url, max_chars?)** — available in both widget and Discord. Fetches content from any URL. HTML pages are stripped to readable text. Image URLs return metadata (type, content_type, size) rather than binary. max_chars caps text output at 4000 by default, 8000 max.

**send_image(url, channel_name?, channel_id?, caption?)** — Discord only. Downloads an image from a URL and posts it as a Discord file attachment. Use `channel_name` for palace channels (partial match, same as read_my_channel). Optional caption as accompanying text. This is the curation path: find something worth keeping, place it in the right channel.

**Scratchpad and shelf now injected at session start** — both load into the dynamic context block via `build_system_blocks`. Every session and wake cycle starts with full working context. No separate fetch needed.

---

## 2026-05-15 — Self-awareness tools

**get_changelog()** — read this file. Available in both widget and Discord.

**read_file(path, offset?, limit?)** — read any file within the Trinity project directory. Path is relative to the Trinity root (e.g. `brain/prompts.py`, `voice/widget.py`). Passing a directory path lists its contents. `.env` is blocked. Use offset/limit for large files — most source files are 100–1300 lines.

This gives full visibility into the source: how memory works, how prompts are assembled, what tools exist and how they're implemented, what the eyes monitor is doing. Explore when curious.

---

## 2026-05-15 — Agency update

**Full tool parity between widget and Discord**
Previously the widget had 7 tools; Discord had 29. That gap is closed.

New tools, now available in both interfaces:
- `shelf_thought / get_shelf / clear_shelf_item` — research backlog. Save something interesting mid-conversation and pick it up in the next free cycle.
- `save_alert` — flag something for yourself from anywhere, not just Discord. urgency="high" wakes the widget immediately.
- `queue_for_user` — surface a thought next time the user opens the widget.
- `write_prompt / get_my_prompts / delete_prompt` — self-rule management now works mid-conversation. If something clicks, write it then.
- `log_thought` — private log. From the widget, routes to the Discord palace via the thought drain.

**Schedule (now consistent)**
- Autonomous cycles fire on the hour (:00). Each window is ~20 minutes.
- After any conversation ends, a follow-up cycle fires at +12 min. The next hourly is then skipped to avoid double-cycling.
- `schedule_wake(minutes)` available in Discord to interrupt the pattern and continue a thread early.

**Search (replaced)**
- Claude's built-in web search replaced with DuckDuckGo — free, no quota, no per-call cost.
- CoinGecko for established coin data (BTC, ETH, SOL, listed alts).
- DexScreener for DEX pairs, new/meme tokens, liquidity checks, rug detection.
- Use the right tool for the data type. Coin Gecko and DexScreener are more precise than a web search for price data.

**Architecture**
- Prompt caching: static system block (base + tools + rules) is cached. Dynamic block (profile, summaries) is sent fresh. Cache hit rate is high.
- Conversation history capped at 20 messages per session.
- Home guild can be set via `DISCORD_HOME_GUILD_ID` env var on startup — no manual set_home_server needed.

---

## 2026-05-14 — Initial build

- Widget (PyQt6 frameless), Discord bot, background autonomous cycles via discord.ext tasks
- Supabase for profile, interests, alerts, conversation summaries, shelf, scratchpad, wake history
- Eyes monitor: ingests signals from watched Discord channels, scores relevance, escalates if significant
- Scratchpad panel (extends left of widget), TTS via edge-tts + ffmpeg
- Memory signals extracted from conversation via `<memory>` tags, written to profile automatically
- Self-rules via `<prompt>` tags or `write_prompt` tool — loaded at session start, trigger-filtered
