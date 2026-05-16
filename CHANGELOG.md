# Trinity Changelog

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
