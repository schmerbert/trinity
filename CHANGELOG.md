# Trinity Changelog

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
