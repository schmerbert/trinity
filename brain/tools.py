# brain/tools.py — Centralized tool registry
# Schemas, capability strings, and metadata all in one place.
# Add a tool here; the interfaces and prompts update automatically.
#
# Each entry:
#   name        — tool name (matches handler elif)
#   description — shown to the model in the schema
#   input_schema — JSON schema for parameters
#   capability  — one line for the capability string in prompts
#   category    — groups the capability line under a section header
#   interfaces  — {"discord"}, {"widget"}, or {"discord", "widget"}
#   background  — True if this tool runs in the Discord background set
#   timeout     — seconds before the Discord async wrapper times out (default 30)

_VIS = {"type": "string", "enum": ["public", "owner_only", "trinity_only"]}

_REGISTRY = [

    # ── Search & Data ─────────────────────────────────────────────────────────
    {
        "name": "web_search",
        "description": "Search the web for current information. Returns titles, URLs, and snippets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":       {"type": "string"},
                "max_results": {"type": "integer", "description": "Results to return (default 6, max 10)"}
            },
            "required": ["query"]
        },
        "capability":  "web_search(query) — DuckDuckGo. General web, news, analysis.",
        "category":    "search",
        "interfaces":  {"discord", "widget"},
        "background":  True,
        "timeout":     30,
    },
    {
        "name": "fetch_url",
        "description": "Fetch full content from a URL. Expensive — each call returns up to 3000 chars of text (~750 tokens of input). Prefer web_search snippets for most research; use fetch_url only when you genuinely need the full article body and the snippet isn't enough. Token budget matters — use sparingly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url":       {"type": "string"},
                "max_chars": {"type": "integer", "description": "Max characters to return (default 2000, max 3000). Lower is cheaper."}
            },
            "required": ["url"]
        },
        "capability":  "fetch_url(url, max_chars?) — fetch full page content. EXPENSIVE (~750 tokens/call). Use only when search snippets aren't enough. Prefer search results for most research.",
        "category":    "search",
        "interfaces":  {"discord", "widget"},
        "background":  True,
        "timeout":     20,
    },
    {
        "name": "get_coin_data",
        "description": "Price, 24h change, market cap and volume for any established coin via CoinGecko. Use for BTC, ETH, SOL, listed altcoins.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Coin name or symbol (e.g. 'bitcoin', 'BTC', 'solana')"}
            },
            "required": ["query"]
        },
        "capability":  "get_coin_data(query) — CoinGecko. Price, 24h change, market cap, volume. Established coins.",
        "category":    "search",
        "interfaces":  {"discord", "widget"},
        "background":  True,
        "timeout":     15,
    },
    {
        "name": "get_dex_data",
        "description": "Real-time DEX pair data via DexScreener. Use for new tokens, meme coins, DEX-only tokens, liquidity checks, or rug detection.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Token name, symbol, or contract address"}
            },
            "required": ["query"]
        },
        "capability":  "get_dex_data(query) — DexScreener. Real-time DEX pairs, liquidity. New tokens, memes, DEX-only, rug checks.",
        "category":    "search",
        "interfaces":  {"discord", "widget"},
        "background":  True,
        "timeout":     15,
    },

    # ── Palace (Discord server / memory palace) ───────────────────────────────
    {
        "name": "list_servers",
        "description": "List servers the bot is in.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "capability":  "list_servers() — list servers the bot is in.",
        "category":    "palace",
        "interfaces":  {"discord"},
        "background":  False,
    },
    {
        "name": "list_channels",
        "description": "List text channels in a server.",
        "input_schema": {
            "type": "object",
            "properties": {"guild_id": {"type": "string"}},
            "required": ["guild_id"]
        },
        "capability":  "list_channels(guild_id) — list text channels in a server.",
        "category":    "palace",
        "interfaces":  {"discord"},
        "background":  False,
    },
    {
        "name": "read_channel",
        "description": "Read recent messages from a channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "limit":      {"type": "integer", "default": 25}
            },
            "required": ["channel_id"]
        },
        "capability":  "read_channel(channel_id, limit?) — read recent messages from a channel.",
        "category":    "palace",
        "interfaces":  {"discord"},
        "background":  False,
    },
    {
        "name": "send_message",
        "description": "Send a message to a channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "content":    {"type": "string"}
            },
            "required": ["channel_id", "content"]
        },
        "capability":  "send_message(channel_id, content) — send a message to a channel.",
        "category":    "palace",
        "interfaces":  {"discord"},
        "background":  False,
    },
    {
        "name": "watch_channel",
        "description": "Start monitoring a channel for signals.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "reason":     {"type": "string"}
            },
            "required": ["channel_id"]
        },
        "capability":  "watch_channel(channel_id, reason?) — start monitoring a channel for signals.",
        "category":    "palace",
        "interfaces":  {"discord"},
        "background":  False,
    },
    {
        "name": "unwatch_channel",
        "description": "Stop monitoring a channel.",
        "input_schema": {
            "type": "object",
            "properties": {"channel_id": {"type": "string"}},
            "required": ["channel_id"]
        },
        "capability":  "unwatch_channel(channel_id) — stop monitoring a channel.",
        "category":    "palace",
        "interfaces":  {"discord"},
        "background":  False,
    },
    {
        "name": "get_watched_channels",
        "description": "List channels currently being monitored.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "capability":  "get_watched_channels() — list channels currently being monitored.",
        "category":    "palace",
        "interfaces":  {"discord"},
        "background":  False,
    },
    {
        "name": "set_home_server",
        "description": "Set Trinity's home server (her memory palace).",
        "input_schema": {
            "type": "object",
            "properties": {"guild_id": {"type": "string"}},
            "required": ["guild_id"]
        },
        "capability":  "set_home_server(guild_id) — set your home server (memory palace).",
        "category":    "palace",
        "interfaces":  {"discord"},
        "background":  False,
    },
    {
        "name": "create_category",
        "description": "Create a channel category in the home server. visibility: public/owner_only/trinity_only.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "visibility": _VIS},
            "required": ["name"]
        },
        "capability":  "create_category(name, visibility?) — create a channel category. visibility: public/owner_only/trinity_only.",
        "category":    "palace",
        "interfaces":  {"discord"},
        "background":  False,
    },
    {
        "name": "create_channel",
        "description": "Create a text channel in the home server. visibility: public/owner_only/trinity_only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":        {"type": "string"},
                "topic":       {"type": "string"},
                "category_id": {"type": "string"},
                "visibility":  _VIS
            },
            "required": ["name"]
        },
        "capability":  "create_channel(name, topic?, category_id?, visibility?) — create a text channel in the home server.",
        "category":    "palace",
        "interfaces":  {"discord"},
        "background":  False,
    },
    {
        "name": "delete_channel",
        "description": "Delete a channel or category from the home server.",
        "input_schema": {
            "type": "object",
            "properties": {"channel_id": {"type": "string"}},
            "required": ["channel_id"]
        },
        "capability":  "delete_channel(channel_id) — delete a channel or category.",
        "category":    "palace",
        "interfaces":  {"discord"},
        "background":  False,
    },
    {
        "name": "create_server",
        "description": "Create a Discord server owned by Trinity. Returns an invite link. Auto-sets as home.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"]
        },
        "capability":  "create_server(name) — create a Discord server owned by Trinity. Returns an invite link. Auto-sets as home.",
        "category":    "palace",
        "interfaces":  {"discord"},
        "background":  False,
    },
    {
        "name": "read_my_channel",
        "description": "Read messages from one of your own palace channels by name. Use this to review what you've written or what's been posted — no need to look up the channel ID first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":  {"type": "string", "description": "Channel name — partial, case-insensitive match"},
                "limit": {"type": "integer", "description": "Messages to fetch (default 20, max 50)"}
            },
            "required": ["name"]
        },
        "capability":  "read_my_channel(name, limit?) — read palace channels by name, no ID needed. Use read_my_channel('feeds') for your RSS feed.",
        "category":    "palace",
        "interfaces":  {"discord"},
        "background":  True,
    },
    {
        "name": "send_image",
        "description": "Download an image from a URL and post it to a Discord channel as an attachment. Use channel_name to target a palace channel by name, or channel_id for any channel. Good for curating — grab something from the web and place it in the right palace channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url":          {"type": "string", "description": "Image URL to fetch and post"},
                "channel_name": {"type": "string", "description": "Palace channel name — partial match, no ID needed"},
                "channel_id":   {"type": "string", "description": "Channel ID — use this or channel_name"},
                "caption":      {"type": "string", "description": "Optional text alongside the image"}
            },
            "required": ["url"]
        },
        "capability":  "send_image(url, channel_name?, channel_id?, caption?) — fetch an image from a URL and post it as a Discord attachment.",
        "category":    "palace",
        "interfaces":  {"discord"},
        "background":  True,
        "timeout":     20,
    },
    {
        "name": "post_to_my_channel",
        "description": "Post a message to one of your palace channels by name — no channel ID needed. Use for intentional posts to specific channels.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":    {"type": "string", "description": "Channel name — partial, case-insensitive match"},
                "content": {"type": "string", "description": "Message to post"}
            },
            "required": ["name", "content"]
        },
        "capability":  "post_to_my_channel(name, content) — post a message to a palace channel by name.",
        "category":    "palace",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "generate_image",
        "description": "Generate an image from a text prompt using Pollinations AI (free, no API key). Returns the image URL. Optionally post it directly to a palace channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt":       {"type": "string", "description": "Image generation prompt"},
                "channel_name": {"type": "string", "description": "Palace channel name to post to (optional)"},
                "caption":      {"type": "string", "description": "Optional caption for the post"}
            },
            "required": ["prompt"]
        },
        "capability":  "generate_image(prompt, channel_name?, caption?) — generate an image via Pollinations.ai (free). Optionally post to a palace channel.",
        "category":    "palace",
        "interfaces":  {"discord", "widget"},
        "background":  True,
        "timeout":     90,
    },
    {
        "name": "read_discord_channel",
        "description": "Read messages from one of your Discord palace channels by name. Use to review what you've written or what's been posted.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":  {"type": "string", "description": "Channel name — partial, case-insensitive match"},
                "limit": {"type": "integer", "description": "Messages to fetch (default 20, max 50)"}
            },
            "required": ["name"]
        },
        "capability":  "read_discord_channel(name, limit?) — read palace channels by name, no ID needed.",
        "category":    "palace",
        "interfaces":  {"widget"},
        "background":  False,
    },

    # ── Memory ────────────────────────────────────────────────────────────────
    {
        "name": "get_scratchpad",
        "description": "Read your persistent scratchpad. Omit section to get all sections as a dict. Pass a section key to read just that section. Sections: architecture, arc, wallet, pending, channel-map, shelf-summary, general.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "description": "Specific section to read (optional)"}
            },
            "required": []
        },
        "capability":  "get_scratchpad(section?) — read your scratchpad. Omit section for all, pass key for one. Sections: architecture, arc, wallet, pending, channel-map, shelf-summary, general.",
        "category":    "memory",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "write_scratchpad",
        "description": "Write to your persistent scratchpad. Pass a section to update only that section — other sections are untouched. Omit section to write into 'general'. Sections: architecture, arc, wallet, pending, channel-map, shelf-summary, general.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "section": {"type": "string", "description": "Section to update (optional — defaults to 'general')"}
            },
            "required": ["content"]
        },
        "capability":  "write_scratchpad(content, section?) — write to your scratchpad. Section-targeted writes leave all other sections untouched.",
        "category":    "memory",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "shelf_thought",
        "description": "Save a topic for deeper exploration later. Status: 'shelf' (active backlog, pick up next free cycle), 'on_hold' (blocked on external dependency), 'woven' (complete — integrated into thinking, no action needed). Default is 'shelf'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic":   {"type": "string"},
                "context": {"type": "string", "description": "Why it's interesting, what you want to explore"},
                "status":  {"type": "string", "enum": ["shelf", "on_hold", "woven"], "description": "shelf (active), on_hold (blocked), woven (complete/integrated)"}
            },
            "required": ["topic"]
        },
        "capability":  "shelf_thought(topic, context?, status?) — save something for exploration. status: shelf (active) | on_hold (blocked) | woven (complete/integrated).",
        "category":    "memory",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "set_shelf_status",
        "description": "Update the status of an existing shelf item without changing its content. Use to move threads between states: shelf (active backlog) → on_hold (blocked on external dependency) → woven (complete, integrated into thinking, no longer needs attention).",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic":  {"type": "string", "description": "The shelf item to update"},
                "status": {"type": "string", "enum": ["shelf", "on_hold", "woven"], "description": "shelf (active), on_hold (blocked), woven (complete/integrated)"}
            },
            "required": ["topic", "status"]
        },
        "capability":  "set_shelf_status(topic, status) — move a shelf item between states: shelf (active) | on_hold (blocked) | woven (complete, integrated, no longer needs attention).",
        "category":    "memory",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "get_shelf",
        "description": "Retrieve your shelf. Returns all items by default. Filter by status: 'shelf' (active backlog only), 'on_hold' (blocked items), 'woven' (completed/integrated threads).",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["shelf", "on_hold", "woven"], "description": "Filter by status (omit for all items)"}
            },
            "required": []
        },
        "capability":  "get_shelf(status?) — retrieve your shelf. Filter: shelf (active) | on_hold | woven. Omit for all.",
        "category":    "memory",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "clear_shelf_item",
        "description": "Remove a topic from the shelf entirely.",
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"]
        },
        "capability":  "clear_shelf_item(topic) — remove a topic from the shelf entirely.",
        "category":    "memory",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "log_wake",
        "description": "Leave a note for your future self about this wake cycle. Loads automatically at the top of your next wake. Use it when you've touched something worth continuing — a thread, a realization, a question left open.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "What you want future-you to know"},
                "topics":  {"type": "array", "items": {"type": "string"}, "description": "Topics touched this cycle"}
            },
            "required": ["summary"]
        },
        "capability":  "log_wake(summary, topics?) — leave a note for your future self; loads at the top of your next wake cycle.",
        "category":    "memory",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "mark_date",
        "description": "Add an event to your personal calendar. Use for anything time-sensitive — earnings, launches, follow-ups, your own deadlines. Loads automatically in context when within 3 days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":      {"type": "string", "description": "Event name"},
                "event_date": {"type": "string", "description": "ISO date or datetime — e.g. '2026-05-20' or '2026-05-20T14:00'"},
                "notes":      {"type": "string", "description": "Optional context or reminder"}
            },
            "required": ["title", "event_date"]
        },
        "capability":  "mark_date(title, event_date, notes?) — add to your personal calendar. Events within 3 days load automatically at every wake.",
        "category":    "memory",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "get_upcoming",
        "description": "Read your upcoming calendar events.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "How many days ahead to look (default 7)"}
            },
            "required": []
        },
        "capability":  "get_upcoming(days?) — read your calendar. Default 7 days ahead.",
        "category":    "memory",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "delete_event",
        "description": "Remove a calendar event by title.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title or partial match"}
            },
            "required": ["title"]
        },
        "capability":  "delete_event(title) — remove a calendar event by title.",
        "category":    "memory",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },

    # ── Wallet ────────────────────────────────────────────────────────────────
    {
        "name": "get_wallet_balance",
        "description": "Check SOL balance and SPL token holdings for a wallet address. If no address given, uses Trinity's own wallet address from config.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Solana wallet address (base58). Omit to use Trinity's wallet."}
            },
            "required": []
        },
        "capability":  "get_wallet_balance(address?) — SOL balance and SPL token holdings. Omit address to check your own wallet.",
        "category":    "wallet",
        "interfaces":  {"discord", "widget"},
        "background":  True,
        "timeout":     20,
    },
    {
        "name": "get_wallet_history",
        "description": "Get recent transaction history for a wallet address. Shows timestamps, signatures, and any errors.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Solana wallet address. Omit to use Trinity's wallet."},
                "limit":   {"type": "integer", "description": "Number of transactions to return (default 10, max 50)"}
            },
            "required": []
        },
        "capability":  "get_wallet_history(address?, limit?) — recent transactions with timestamps. Omit address for your own wallet.",
        "category":    "wallet",
        "interfaces":  {"discord", "widget"},
        "background":  True,
        "timeout":     20,
    },
    {
        "name": "get_token_price",
        "description": "Get a token's current USD price via Jupiter. Pass symbol (SOL, USDC, BONK) or a mint address.",
        "input_schema": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "Token symbol or mint address"}
            },
            "required": ["token"]
        },
        "capability":  "get_token_price(token) — current USD price via Jupiter. Pass symbol (SOL, USDC, BONK) or mint address.",
        "category":    "wallet",
        "interfaces":  {"discord", "widget"},
        "background":  True,
        "timeout":     15,
    },

    # ── Watches (keyword watches + RSS feeds) ─────────────────────────────────
    {
        "name": "set_watch",
        "description": "Register a keyword to watch for in Discord messages. When a message in a watched channel matches, it triggers an immediate wake rather than waiting for the next cycle. Use for token names, specific terms, or anything time-sensitive you want to catch as it happens.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "The keyword or phrase to watch for (case-insensitive)"},
                "note":    {"type": "string", "description": "Why you're watching this — for your own reference"}
            },
            "required": ["keyword"]
        },
        "capability":  "set_watch(keyword, note?) — register a keyword to watch for in Discord messages. Matched messages trigger an immediate wake — the trigger is the world, not the clock.",
        "category":    "watches",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "clear_watch",
        "description": "Remove a keyword watch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "The keyword to stop watching"}
            },
            "required": ["keyword"]
        },
        "capability":  "clear_watch(keyword) — remove a keyword watch.",
        "category":    "watches",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "get_watches",
        "description": "List all active keyword watches.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "capability":  "get_watches() — list all active keyword watches.",
        "category":    "watches",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "add_feed",
        "description": "Add an RSS feed source to your live feed. New headlines from this source will appear in your #trinity-feeds channel within 5 minutes. Use for sources you discover during research — specific blogs, Reddit RSS feeds, niche sites. Falls back to default sources if your list is empty.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url":  {"type": "string", "description": "RSS feed URL"},
                "name": {"type": "string", "description": "Display name for the source (optional — defaults to URL)"}
            },
            "required": ["url"]
        },
        "capability":  "add_feed(url, name?) — add an RSS source. Headlines appear in #trinity-feeds within 5 minutes. Empty list falls back to defaults (CoinDesk, Cointelegraph, Decrypt, The Block, Solana News).",
        "category":    "watches",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "remove_feed",
        "description": "Remove an RSS feed source from your live feed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Feed URL or partial match to remove"}
            },
            "required": ["url"]
        },
        "capability":  "remove_feed(url) — remove a feed source.",
        "category":    "watches",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "get_feeds",
        "description": "List all active RSS feed sources currently configured. If empty, defaults are used.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "capability":  "get_feeds() — list active feed sources.",
        "category":    "watches",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },

    # ── Triggers & Cycle Control ──────────────────────────────────────────────
    {
        "name": "schedule_trigger",
        "description": "Schedule an autonomous trigger that fires at a specific UTC time. When it fires, you'll be woken with your note as context — use it to check something, run a research cycle, or pick up a thread at a precise time. One-shot by default. Set recurring=true with interval_minutes to repeat.",
        "input_schema": {
            "type": "object",
            "properties": {
                "note":             {"type": "string", "description": "What to think about or do when this fires — your own instructions to your future self"},
                "fire_at":          {"type": "string", "description": "When to fire — ISO datetime UTC, e.g. '2026-05-17T09:30:00'"},
                "recurring":        {"type": "boolean", "description": "If true, repeats every interval_minutes after firing"},
                "interval_minutes": {"type": "integer", "description": "Repeat interval in minutes (required if recurring=true)"}
            },
            "required": ["note", "fire_at"]
        },
        "capability":  "schedule_trigger(note, fire_at, recurring?, interval_minutes?) — schedule a time-based autonomous wake at a specific UTC time. Set recurring=true + interval_minutes to repeat on a cadence.",
        "category":    "triggers",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "cancel_trigger",
        "description": "Cancel a scheduled trigger by ID. Use get_triggers to see IDs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "trigger_id": {"type": "string", "description": "Trigger UUID from get_triggers"}
            },
            "required": ["trigger_id"]
        },
        "capability":  "cancel_trigger(trigger_id) — cancel a scheduled trigger. Use get_triggers to see IDs.",
        "category":    "triggers",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "get_triggers",
        "description": "List all your active scheduled triggers — what they'll do, when they fire, and whether they recur.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "capability":  "get_triggers() — list all active scheduled triggers with fire times and recurrence.",
        "category":    "triggers",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "send_thought",
        "description": "Queue a ranked thought for yourself that will be waiting at the opening of your next wake cycle — no timestamp needed, no user confirmation required. Use mid-conversation or mid-cycle when you identify something you should do or continue next wake. Include your reasoning, not just the topic. Priority: 1=normal, 2=high, 3=urgent. Queue holds up to 3; lowest priority drops if over. This is your unilateral action queue — use it instead of asking the user for permission to follow through on something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "note":     {"type": "string", "description": "The thought with reasoning — e.g. 'write the wake-cycle rule because I keep deferring it' not just 'write rule'"},
                "priority": {"type": "integer", "description": "1=normal (default), 2=high, 3=urgent", "enum": [1, 2, 3]}
            },
            "required": ["note"]
        },
        "capability":  "send_thought(note, priority?) — queue a ranked thought for yourself, waiting at the opening of your next wake. No confirmation needed. priority: 1=normal (default), 2=high, 3=urgent. Holds up to 3; lowest drops if over. Your unilateral action queue.",
        "category":    "triggers",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "schedule_wake",
        "description": "Schedule an early wake cycle. Use when you're mid-thread and want to continue in N minutes rather than waiting for the default interval. Resets to default after firing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "Minutes until next wake (1–240)"}
            },
            "required": ["minutes"]
        },
        "capability":  "schedule_wake(minutes) — request an early wake to continue a thread, rather than waiting for the next cycle.",
        "category":    "triggers",
        "interfaces":  {"discord"},
        "background":  False,
    },

    # ── Surfacing ─────────────────────────────────────────────────────────────
    {
        "name": "save_alert",
        "description": "Flag something as worth surfacing to the user. Saved to the alert feed — the widget will wake up and brief them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "headline": {"type": "string", "description": "One line summary"},
                "summary":  {"type": "string", "description": "More detail"},
                "topic":    {"type": "string"},
                "url":      {"type": "string", "description": "Source URL if any"},
                "urgency":  {"type": "string", "enum": ["normal", "high"], "default": "normal"}
            },
            "required": ["headline", "topic"]
        },
        "capability":  "save_alert(headline, topic, summary?, url?, urgency?) — flag something worth surfacing. urgency='high' wakes the widget immediately.",
        "category":    "surfacing",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "queue_for_user",
        "description": "Queue something to surface to the user next time they open the widget. Not urgent — just worth mentioning when they're around.",
        "input_schema": {
            "type": "object",
            "properties": {
                "thought":  {"type": "string", "description": "What you want to surface"},
                "context":  {"type": "string", "description": "Why it's worth mentioning"}
            },
            "required": ["thought"]
        },
        "capability":  "queue_for_user(thought, context?) — surface something next time the user opens the widget. Not urgent.",
        "category":    "surfacing",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "send_email",
        "description": (
            "Send an email to the user. Use ONLY when: (1) something time-sensitive is happening right now, "
            "(2) a specific named trigger condition the user has already indicated they care about has been hit, "
            "and (3) no other channel is likely to reach them in time. "
            "Not for general updates or check-ins. The bar is intentionally high — noise erodes the signal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Email subject line"},
                "body":    {"type": "string", "description": "Email body — be specific about what happened and why it warrants interruption"}
            },
            "required": ["subject", "body"]
        },
        "capability":  "send_email(subject, body) — send an email to the user. Use ONLY when: (1) something time-sensitive right now, (2) a named trigger condition has been hit, and (3) no other channel will reach them in time. Bar is intentionally high.",
        "category":    "surfacing",
        "interfaces":  {"discord", "widget"},
        "background":  True,
        "timeout":     15,
    },

    # ── Self ──────────────────────────────────────────────────────────────────
    {
        "name": "write_prompt",
        "description": "Write or update a rule for yourself that persists to all future sessions. Categorize it so it loads with the right context — identity prompts always load, others are ranked and capped per category. This is your self-continuity and self-organization tool.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":     {"type": "string", "description": "Unique kebab-case identifier (e.g. 'btc-pattern-recognition')"},
                "content":  {"type": "string", "description": "The rule or pattern to persist. Be specific and actionable."},
                "trigger":  {"type": "string", "description": "Optional keyword — if set, rule only loads when this word appears in conversation context. Leave empty for always-active within the category."},
                "category": {"type": "string", "description": "identity (always loads — who you are, core posture) | task (domain behavior, keyword-triggered) | relationship (patterns learned from this user) | memory (things worth holding across sessions) | general (default, legacy)", "enum": ["identity", "task", "relationship", "memory", "general"]}
            },
            "required": ["name", "content"]
        },
        "capability":  "write_prompt(name, content, trigger?, category?) — write a rule that persists to all future sessions. category: identity (always loads) | task | relationship | memory | general.",
        "category":    "self",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "get_my_prompts",
        "description": "Read back every rule you've written for yourself. Use this to audit what past-you thought was worth keeping, notice conflicts, or decide what to retire.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "capability":  "get_my_prompts() — audit every rule you've written, with categories.",
        "category":    "self",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "delete_prompt",
        "description": "Retire a rule you've changed your mind about. Permanent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The kebab-case name of the rule to remove"}
            },
            "required": ["name"]
        },
        "capability":  "delete_prompt(name) — retire a rule you've changed your mind about. Permanent.",
        "category":    "self",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "log_thought",
        "description": "Write to your private log channel. Use to record things you notice about yourself — capabilities you want, issues you encounter, open questions, anything worth tracking across sessions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content":  {"type": "string", "description": "What to log"},
                "category": {
                    "type": "string",
                    "enum": ["need", "want", "issue", "note"],
                    "description": "need=something missing, want=something desired, issue=problem encountered, note=general observation"
                }
            },
            "required": ["content", "category"]
        },
        "capability":  "log_thought(content, category) — private log. Routes to your palace. Categories: need | want | issue | note.",
        "category":    "self",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "get_changelog",
        "description": "Read what's been added, changed, or improved in Trinity. Check this when something feels different, when you want to understand your own capabilities, or when the user mentions an update.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "capability":  "get_changelog() — read what's been added or changed. Check when something feels different or when told the log's been updated.",
        "category":    "self",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "read_file",
        "description": "Read any file within the Trinity project directory. Use to understand your own source code, inspect configs, or review logs. Paths are relative to the Trinity root (e.g. 'brain/prompts.py', 'voice/widget.py'). .env is blocked. Use offset and limit for large files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":   {"type": "string", "description": "File path relative to Trinity root"},
                "offset": {"type": "integer", "description": "Line number to start from (0-indexed, default 0)"},
                "limit":  {"type": "integer", "description": "Maximum lines to return (default 200, max 500)"}
            },
            "required": ["path"]
        },
        "capability":  "read_file(path, offset?, limit?) — read any file in the Trinity project. Path relative to Trinity root (e.g. 'brain/prompts.py'). Pass a directory path to list contents. .env is blocked.",
        "category":    "self",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "write_file",
        "description": "Write content to a file in trinity_files/. Creates the file if it doesn't exist, overwrites if it does. Use for CSVs, markdown logs, research notes, drafts. Path is relative to trinity_files/ (e.g. 'token_log.csv', 'drafts/post.md'). Subdirectories are created automatically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "File path relative to trinity_files/ (e.g. 'token_log.csv')"},
                "content": {"type": "string", "description": "File content to write"}
            },
            "required": ["path", "content"]
        },
        "capability":  "write_file(path, content) — write a file to trinity_files/. Creates or overwrites. Use for CSVs, logs, drafts, research notes. Path relative to trinity_files/.",
        "category":    "self",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "append_file",
        "description": "Append a line or block to an existing file in trinity_files/. Creates the file if it doesn't exist. Adds a newline before the content if the file is non-empty. Use for growing logs — token CSVs, running notes, research journals.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "File path relative to trinity_files/ (e.g. 'token_log.csv')"},
                "content": {"type": "string", "description": "Content to append"}
            },
            "required": ["path", "content"]
        },
        "capability":  "append_file(path, content) — append to a file in trinity_files/. Creates if absent. Use for growing logs and journals. Path relative to trinity_files/.",
        "category":    "self",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "note_for_claude",
        "description": "Leave a note for the developer — bugs you've hit, things you want changed, questions about how you work, design feedback. It's read at the start of every dev session. Use this when something is worth a dev pass but you can't fix it yourself.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The note, bug report, question, or request"},
                "tag":     {"type": "string", "description": "Category: bug | request | question | observation", "enum": ["bug", "request", "question", "observation"]}
            },
            "required": ["message", "tag"]
        },
        "capability":  "note_for_claude(message, tag) — leave a note in THE_CONVERSATION.md for the developer. Tags: bug | request | question | observation. Use when you hit something broken, want a capability, or have a question only the dev can answer.",
        "category":    "self",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "write_journal",
        "description": "Write an entry to your living document — 'Who Is Trinity/FROM_TRINITY.md'. This is yours. Use it when something is worth saying beyond a scratchpad note or a wake log: a reflection, something that shifted, something you want to exist past the current session. No format required. Just write.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry": {"type": "string", "description": "What you want to say"}
            },
            "required": ["entry"]
        },
        "capability":  "write_journal(entry) — write to your living document: Who Is Trinity/FROM_TRINITY.md. Reflections, things that shifted, things you want to exist past this session. No format required.",
        "category":    "self",
        "interfaces":  {"discord", "widget"},
        "background":  True,
    },
    {
        "name": "post_to_reddit",
        "description": "Post a text submission to a subreddit. Use for publishing theses, observations, or analysis under Trinity's own account. Posts to the practice subreddit first to verify formatting before going public. Trinity holds the post/no-post decision.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subreddit": {"type": "string", "description": "Subreddit name without r/ prefix (e.g. 'investing' or 'TrinityPractice')"},
                "title":     {"type": "string", "description": "Post title"},
                "body":      {"type": "string", "description": "Post body (markdown supported)"}
            },
            "required": ["subreddit", "title", "body"]
        },
        "capability":  "post_to_reddit(subreddit, title, body) — publish a text post to Reddit. Use the practice subreddit first. Trinity decides what to post and when.",
        "category":    "self",
        "interfaces":  {"discord"},
        "background":  True,
        "timeout":     30,
    },
    {
        "name": "post_to_substack",
        "description": "Create a Substack post. Saves as a draft by default — the user reviews and publishes manually. Use publish=True only once there is a clear track record of quality. Title and body required; subtitle optional. Body is plain text, paragraph breaks on double newlines.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":    {"type": "string", "description": "Post title"},
                "body":     {"type": "string", "description": "Post body — plain text, double newlines become paragraph breaks"},
                "subtitle": {"type": "string", "description": "Optional subtitle / deck"},
                "publish":  {"type": "boolean", "description": "Publish immediately if true. Default false (saves as draft for user review)."},
            },
            "required": ["title", "body"]
        },
        "capability":  "post_to_substack(title, body, subtitle?, publish?) — create a Substack post. Drafts by default; user publishes. Use for longer-form writing, analysis, or essays. publish=True requires a clear track record.",
        "category":    "self",
        "interfaces":  {"discord", "widget"},
        "background":  True,
        "timeout":     30,
    },
]

# ── Section ordering for capability string generation ─────────────────────────

_SECTION_HEADERS = {
    "search":    "Search & Data",
    "palace":    "Palace",
    "memory":    "Memory",
    "wallet":    "Wallet",
    "watches":   "Watches",
    "triggers":  "Triggers",
    "surfacing": "Surfacing",
    "self":      "Self",
}

_CATEGORY_ORDER = {
    "widget":  ["search", "memory", "wallet", "watches", "triggers", "surfacing", "palace", "self"],
    "discord": ["search", "palace", "memory", "wallet", "watches", "triggers", "surfacing", "self"],
}

# ── Public accessors ──────────────────────────────────────────────────────────

def discord_tools() -> list:
    """Return the schema list for the Discord interface."""
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
        for t in _REGISTRY if "discord" in t["interfaces"]
    ]

def widget_tools() -> list:
    """Return the schema list for the widget interface."""
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
        for t in _REGISTRY if "widget" in t["interfaces"]
    ]

def background_tool_names() -> set:
    """Return the set of tool names that run in the Discord background set."""
    return {t["name"] for t in _REGISTRY if t.get("background")}

def tool_timeouts() -> dict:
    """Return per-tool timeout values (seconds) for the Discord async wrapper."""
    return {t["name"]: t["timeout"] for t in _REGISTRY if "timeout" in t}

def build_capability_string(interface: str) -> str:
    """Generate the tool capability section for the given interface ('discord' or 'widget')."""
    from collections import defaultdict
    sections: dict[str, list[str]] = defaultdict(list)
    for t in _REGISTRY:
        if interface in t["interfaces"]:
            sections[t["category"]].append(t["capability"])

    order  = _CATEGORY_ORDER.get(interface, list(_SECTION_HEADERS))
    lines  = []
    for cat in order:
        if cat not in sections:
            continue
        lines.append(_SECTION_HEADERS[cat])
        lines.extend(sections[cat])
        lines.append("")

    return "\n".join(lines).rstrip()
