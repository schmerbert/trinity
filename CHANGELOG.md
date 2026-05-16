# Trinity Changelog

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
