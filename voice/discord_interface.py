import os
import sys
import json
import re
import asyncio
from datetime import datetime
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord
from discord.ext import tasks
import anthropic
from dotenv import load_dotenv
from pathlib import Path
from supabase import create_client

env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

from brain.memory import (
    get_profile, save_alert, get_unseen_alerts, mark_alerts_seen,
    get_recent_summaries, add_interest, add_feedback, update_profile,
    get_shelf, add_to_shelf, remove_from_shelf,
    get_queued_thoughts, queue_thought, clear_queued_thoughts,
    pop_discord_writes, log_wake_cycle, get_wake_history,
    get_scratchpad, save_scratchpad, request_wake, pop_wake_request
)
from brain.prompts import build_system_blocks, build_prompt, format_summaries, parse_prompt_tags, save_trinity_prompt
from brain.logger import get_logger

log = get_logger("DISCORD")
from eyes.scraper import score_relevance, generate_hash

# ─── Supabase setup (add this table in your SQL editor) ──────────────────────
#
# create table discord_channels (
#   id           uuid primary key default gen_random_uuid(),
#   profile_id   uuid references profiles(id),
#   channel_id   text not null,
#   guild_id     text,
#   channel_name text,
#   guild_name   text,
#   watching     boolean default true,
#   reason       text,
#   added_by     text default 'trinity',
#   added_at     timestamp default now(),
#   unique(profile_id, channel_id)
# );
# alter table discord_channels enable row level security;
# create policy "allow all" on discord_channels for all using (true);
#
# ─────────────────────────────────────────────────────────────────────────────

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

OWNER_ID           = os.getenv("DISCORD_OWNER_ID", "")
OWNER_ID           = int(OWNER_ID) if OWNER_ID.isdigit() else 0
AUTONOMOUS_MINUTES = int(os.getenv("DISCORD_AUTONOMOUS_INTERVAL", "60"))
_LOG_CHANNEL_ID    = int(os.getenv("TRINITY_LOG_CHANNEL_ID",     "0") or "0")
_THOUGHT_CHANNEL_ID = int(os.getenv("TRINITY_THOUGHT_CHANNEL_ID", "0") or "0")
_HOME_GUILD_ID_ENV  = os.getenv("DISCORD_HOME_GUILD_ID", "")

intents = discord.Intents.default()
intents.message_content = True
bot       = discord.Client(intents=intents)
ai_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_conversations: dict[int, list]  = {}
_watched_channels: set[int]      = set()
_api_lock = asyncio.Semaphore(1)
_last_eyes_check: datetime       = datetime.utcnow()
_skip_next_autonomous: bool      = False

# ─── Tools Trinity can use ───────────────────────────────────────────────────

_VIS = {"type": "string", "enum": ["public", "owner_only", "trinity_only"]}

DISCORD_TOOLS = [
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
        }
    },
    {
        "name": "fetch_url",
        "description": "Fetch content from any URL. Returns stripped text for web pages, or image metadata if the URL points to an image. Use to read articles, check pages, or confirm what's at a link.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url":       {"type": "string"},
                "max_chars": {"type": "integer", "description": "Max characters to return (default 4000, max 8000)"}
            },
            "required": ["url"]
        }
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
        }
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
        }
    },
    {
        "name": "list_servers",
        "description": "List servers the bot is in.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "list_channels",
        "description": "List text channels in a server.",
        "input_schema": {"type": "object", "properties": {"guild_id": {"type": "string"}}, "required": ["guild_id"]}
    },
    {
        "name": "read_channel",
        "description": "Read recent messages from a channel.",
        "input_schema": {
            "type": "object",
            "properties": {"channel_id": {"type": "string"}, "limit": {"type": "integer", "default": 25}},
            "required": ["channel_id"]
        }
    },
    {
        "name": "send_message",
        "description": "Send a message to a channel.",
        "input_schema": {
            "type": "object",
            "properties": {"channel_id": {"type": "string"}, "content": {"type": "string"}},
            "required": ["channel_id", "content"]
        }
    },
    {
        "name": "watch_channel",
        "description": "Start monitoring a channel for signals.",
        "input_schema": {
            "type": "object",
            "properties": {"channel_id": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["channel_id"]
        }
    },
    {
        "name": "unwatch_channel",
        "description": "Stop monitoring a channel.",
        "input_schema": {"type": "object", "properties": {"channel_id": {"type": "string"}}, "required": ["channel_id"]}
    },
    {
        "name": "get_watched_channels",
        "description": "List channels currently being monitored.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "set_home_server",
        "description": "Set Trinity's home server (her memory palace).",
        "input_schema": {"type": "object", "properties": {"guild_id": {"type": "string"}}, "required": ["guild_id"]}
    },
    {
        "name": "create_category",
        "description": "Create a channel category in the home server. visibility: public/owner_only/trinity_only.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "visibility": _VIS},
            "required": ["name"]
        }
    },
    {
        "name": "create_channel",
        "description": "Create a text channel in the home server. visibility: public/owner_only/trinity_only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"}, "topic": {"type": "string"},
                "category_id": {"type": "string"}, "visibility": _VIS
            },
            "required": ["name"]
        }
    },
    {
        "name": "delete_channel",
        "description": "Delete a channel or category from the home server.",
        "input_schema": {"type": "object", "properties": {"channel_id": {"type": "string"}}, "required": ["channel_id"]}
    },
    {
        "name": "create_server",
        "description": "Create a Discord server owned by Trinity. Returns an invite link. Auto-sets as home.",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
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
        }
    },
    {
        "name": "shelf_thought",
        "description": "Save a topic for deeper exploration later. Use when something is interesting but not urgent — pick it up next free time session.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic":   {"type": "string"},
                "context": {"type": "string", "description": "Why it's interesting, what you want to explore"}
            },
            "required": ["topic"]
        }
    },
    {
        "name": "get_shelf",
        "description": "Retrieve topics you've shelved for future exploration.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "clear_shelf_item",
        "description": "Remove a topic from the shelf once explored.",
        "input_schema": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}
    },
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
        }
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
        }
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
        }
    },
    {
        "name": "get_scratchpad",
        "description": "Read your persistent scratchpad — carries between sessions and across both interfaces.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "write_scratchpad",
        "description": "Update your persistent scratchpad. This is your canonical working surface — it loads in the widget next session.",
        "input_schema": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"]
        }
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
        }
    },
    {
        "name": "write_prompt",
        "description": "Write or update a rule for yourself that persists to all future sessions. Use during free time when you codify something worth keeping — a pattern, a behavioral adjustment, a realization. This is your self-continuity tool.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":    {"type": "string", "description": "Unique kebab-case identifier (e.g. 'btc-pattern-recognition')"},
                "content": {"type": "string", "description": "The rule or pattern to persist. Be specific and actionable."},
                "trigger": {"type": "string", "description": "Optional keyword — if set, rule only loads when this word appears in conversation context. Leave empty for always-active."}
            },
            "required": ["name", "content"]
        }
    },
    {
        "name": "get_my_prompts",
        "description": "Read back every rule you've written for yourself. Use this to audit what past-you thought was worth keeping, notice conflicts, or decide what to retire.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
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
        }
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
        }
    },
    {
        "name": "get_changelog",
        "description": "Read what's been added, changed, or improved in Trinity. Check this when something feels different, when you want to understand your own capabilities, or when the user mentions an update.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
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
        }
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
        }
    }
]

_BACKGROUND_TOOL_NAMES = {
    "web_search", "get_coin_data", "get_dex_data", "fetch_url",
    "queue_for_user", "shelf_thought", "get_shelf", "clear_shelf_item",
    "save_alert", "read_my_channel", "log_wake", "get_scratchpad", "write_scratchpad",
    "schedule_wake", "write_prompt", "get_my_prompts", "delete_prompt", "log_thought",
    "get_changelog", "read_file", "send_image"
}
DISCORD_TOOLS_BACKGROUND = [
    t for t in DISCORD_TOOLS if t.get("name") in _BACKGROUND_TOOL_NAMES
]

# ─── Events ──────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    log.info(f"Online as {bot.user}")
    await _load_watched_channels()
    await _post_pending_alerts()
    if _HOME_GUILD_ID_ENV:
        profile = get_profile()
        if profile and not profile.get("discord_home_guild_id"):
            supabase.table("profiles").update({
                "discord_home_guild_id": _HOME_GUILD_ID_ENV
            }).eq("id", profile["id"]).execute()
            log.info(f"Home guild set from env: {_HOME_GUILD_ID_ENV}")
    autonomous_loop.change_interval(minutes=AUTONOMOUS_MINUTES)
    autonomous_loop.start()
    log.info(f"Autonomous loop every {AUTONOMOUS_MINUTES} min | Eyes every 2 min | Thought drain every 30s")
    eyes_monitor.start()
    thought_drain.start()
    wake_checker.start()
    asyncio.create_task(_startup_brief())


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    if message.channel.id in _watched_channels:
        await _ingest_signal(message)

    is_dm      = isinstance(message.channel, discord.DMChannel)
    is_mention = bot.user in message.mentions
    if (is_dm or is_mention) and message.author.id == OWNER_ID:
        await _respond(message)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id != OWNER_ID:
        return
    if payload.user_id == bot.user.id:
        return

    channel = bot.get_channel(payload.channel_id)
    if not channel:
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except Exception:
        return

    if message.author.id != bot.user.id:
        return

    await _handle_reaction(message, str(payload.emoji))

# ─── Channel watch list ───────────────────────────────────────────────────────

async def _load_watched_channels():
    profile = get_profile()
    if not profile:
        return
    try:
        result = supabase.table("discord_channels")\
            .select("channel_id")\
            .eq("profile_id", profile["id"])\
            .eq("watching", True)\
            .execute()
        _watched_channels.clear()
        _watched_channels.update(int(r["channel_id"]) for r in (result.data or []))
        log.info(f"Watching {len(_watched_channels)} channel(s)")
    except Exception as e:
        print(f"[Discord] Could not load watched channels: {e}")

# ─── Eyes: signal ingestion ───────────────────────────────────────────────────

async def _ingest_signal(message: discord.Message):
    text = message.content
    if not text or len(text) < 20:
        return

    profile = get_profile()
    if not profile:
        return

    score = score_relevance(text, profile)
    if score < 0.5:
        return

    guild_name = message.guild.name if message.guild else "dm"
    source     = f"discord/{guild_name}/{message.channel.name}"
    msg_url    = (
        f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}"
        if message.guild else ""
    )

    interests     = profile.get("interests", [])
    matched_topic = next(
        (i["topic"] for i in interests if i.get("topic", "").lower() in text.lower()),
        "discord"
    )

    alert = {
        "profile_id":      profile["id"],
        "source":          source,
        "topic":           matched_topic,
        "headline":        text[:120],
        "summary":         text[:300],
        "url":             msg_url,
        "relevance_score": score,
        "seen":            False,
    }
    alert["content_hash"] = generate_hash(alert)
    save_alert(alert)

# ─── Post pending alerts on startup ──────────────────────────────────────────

async def _post_pending_alerts():
    profile = get_profile()
    if not profile:
        return

    alerts = get_unseen_alerts(profile["id"])
    if not alerts:
        return

    # Let Trinity decide where to post via tool use — just log for now
    # She will surface these when the owner messages her
    print(f"[Discord] {len(alerts)} unseen alert(s) queued")

# ─── Permission helper ───────────────────────────────────────────────────────

def _build_overwrites(guild: discord.Guild, visibility: str) -> dict:
    overwrites = {}
    if visibility in ("owner_only", "trinity_only"):
        # Hide from everyone by default
        overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        # Always give Trinity full access
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            manage_messages=True
        )
        if visibility == "owner_only" and OWNER_ID:
            owner_member = guild.get_member(OWNER_ID)
            if owner_member:
                overwrites[owner_member] = discord.PermissionOverwrite(view_channel=True)
    return overwrites


def _home_guild() -> discord.Guild | None:
    profile = get_profile()
    if not profile or not profile.get("discord_home_guild_id"):
        return None
    return bot.get_guild(int(profile["discord_home_guild_id"]))

# ─── Tool execution ───────────────────────────────────────────────────────────

async def _execute_tool(name: str, inputs: dict, profile_id: str) -> dict | list:
    if name == "web_search":
        from brain.search import ddg_search
        log.info(f"search: {inputs['query'][:70]}")
        return ddg_search(inputs["query"], int(inputs.get("max_results", 6)))

    elif name == "get_coin_data":
        from brain.search import get_coin_data
        log.info(f"coin: {inputs['query']}")
        return get_coin_data(inputs["query"])

    elif name == "get_dex_data":
        from brain.search import get_dex_data
        log.info(f"dex: {inputs['query']}")
        return get_dex_data(inputs["query"])

    elif name == "fetch_url":
        from brain.search import fetch_url as _fetch
        log.info(f"fetch: {inputs['url'][:70]}")
        return _fetch(inputs["url"], inputs.get("max_chars", 4000))

    elif name == "send_image":
        import urllib.request as _ur
        import io
        url     = inputs["url"]
        caption = inputs.get("caption", "") or ""

        # Resolve channel — name-based palace lookup or explicit ID
        channel = None
        if inputs.get("channel_name"):
            guild = _home_guild()
            if not guild:
                return {"error": "No home server set — use set_home_server first"}
            query = inputs["channel_name"].lower().replace("-", "").replace("_", "").replace(" ", "")
            channel = next(
                (c for c in guild.channels
                 if isinstance(c, discord.TextChannel)
                 and query in c.name.lower().replace("-", "").replace("_", "")),
                None
            )
            if not channel:
                return {"error": f"No channel matching '{inputs['channel_name']}' in home server"}
        elif inputs.get("channel_id"):
            channel = bot.get_channel(int(inputs["channel_id"]))

        if not channel:
            return {"error": "Provide channel_name or channel_id"}

        try:
            req = _ur.Request(url, headers={"User-Agent": "Trinity/1.0"})
            with _ur.urlopen(req, timeout=15) as resp:
                data         = resp.read()
                content_type = resp.headers.get("Content-Type", "")
        except Exception as e:
            return {"error": f"Fetch failed: {e}"}

        filename = url.split("/")[-1].split("?")[0] or "image"
        if "." not in filename:
            ext_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}
            ct = content_type.split(";")[0].strip().lower()
            filename += ext_map.get(ct, ".jpg")

        file = discord.File(io.BytesIO(data), filename=filename)
        await channel.send(content=caption or None, file=file)
        log.info(f"Image posted to #{channel.name}: {url[:60]}")
        return {"status": "sent", "channel": channel.name, "filename": filename}

    elif name == "list_servers":
        return [
            {"id": str(g.id), "name": g.name, "member_count": g.member_count,
             "text_channels": len([c for c in g.channels if isinstance(c, discord.TextChannel)])}
            for g in bot.guilds
        ]

    elif name == "list_channels":
        guild = bot.get_guild(int(inputs["guild_id"]))
        if not guild:
            return {"error": "Server not found"}
        return [
            {"id": str(c.id), "name": c.name, "category": str(c.category) if c.category else None,
             "topic": c.topic}
            for c in guild.channels if isinstance(c, discord.TextChannel)
        ]

    elif name == "read_channel":
        channel = bot.get_channel(int(inputs["channel_id"]))
        if not channel:
            return {"error": "Channel not found or no access"}
        log.info(f"read channel: #{channel.name if channel else inputs['channel_id']}")
        limit = min(int(inputs.get("limit", 25)), 50)
        msgs = []
        async for msg in channel.history(limit=limit, oldest_first=False):
            entry = {
                "author":    str(msg.author.display_name),
                "content":   msg.content,
                "timestamp": msg.created_at.isoformat()
            }
            if msg.attachments:
                entry["attachments"] = [
                    {"url": a.url, "filename": a.filename, "type": a.content_type or ""}
                    for a in msg.attachments
                ]
            msgs.append(entry)
        return msgs

    elif name == "send_message":
        channel = bot.get_channel(int(inputs["channel_id"]))
        if not channel:
            return {"error": "Channel not found or no access"}
        content = inputs["content"]
        for chunk in [content[i:i + 1900] for i in range(0, len(content), 1900)]:
            await channel.send(chunk)
        return {"status": "sent", "channel": str(channel.name)}

    elif name == "watch_channel":
        channel_id = inputs["channel_id"]
        channel    = bot.get_channel(int(channel_id))
        guild_name = channel.guild.name if channel and hasattr(channel, "guild") else "unknown"
        chan_name  = channel.name if channel else "unknown"
        guild_id   = str(channel.guild.id) if channel and hasattr(channel, "guild") else None

        try:
            supabase.table("discord_channels").upsert({
                "profile_id":   profile_id,
                "channel_id":   channel_id,
                "guild_id":     guild_id,
                "channel_name": chan_name,
                "guild_name":   guild_name,
                "watching":     True,
                "reason":       inputs.get("reason", ""),
                "added_by":     "trinity"
            }, on_conflict="profile_id,channel_id").execute()
            _watched_channels.add(int(channel_id))
            return {"status": "watching", "channel": chan_name, "server": guild_name}
        except Exception as e:
            return {"error": str(e)}

    elif name == "unwatch_channel":
        channel_id = inputs["channel_id"]
        try:
            supabase.table("discord_channels")\
                .update({"watching": False})\
                .eq("profile_id", profile_id)\
                .eq("channel_id", channel_id)\
                .execute()
            _watched_channels.discard(int(channel_id))
            return {"status": "unwatched"}
        except Exception as e:
            return {"error": str(e)}

    elif name == "get_watched_channels":
        try:
            result = supabase.table("discord_channels")\
                .select("*")\
                .eq("profile_id", profile_id)\
                .eq("watching", True)\
                .execute()
            return result.data or []
        except Exception as e:
            return {"error": str(e)}

    elif name == "set_home_server":
        guild = bot.get_guild(int(inputs["guild_id"]))
        if not guild:
            return {"error": "Server not found"}
        try:
            supabase.table("profiles").update({
                "discord_home_guild_id":   str(guild.id),
                "discord_home_guild_name": guild.name
            }).eq("id", profile_id).execute()
            return {"status": "home set", "server": guild.name}
        except Exception as e:
            return {"error": str(e)}

    elif name == "create_category":
        guild = _home_guild()
        if not guild:
            return {"error": "No home server set — use set_home_server first"}
        visibility = inputs.get("visibility", "public")
        overwrites = _build_overwrites(guild, visibility)
        try:
            cat = await guild.create_category(inputs["name"], overwrites=overwrites)
            return {"status": "created", "category_id": str(cat.id), "name": cat.name, "visibility": visibility}
        except discord.Forbidden:
            return {"error": "Missing Manage Channels permission"}
        except Exception as e:
            return {"error": str(e)}

    elif name == "create_channel":
        guild = _home_guild()
        if not guild:
            return {"error": "No home server set — use set_home_server first"}
        visibility = inputs.get("visibility", "public")
        overwrites = _build_overwrites(guild, visibility)
        category   = guild.get_channel(int(inputs["category_id"])) if inputs.get("category_id") else None
        try:
            channel = await guild.create_text_channel(
                name=inputs["name"],
                topic=inputs.get("topic", ""),
                category=category,
                overwrites=overwrites
            )
            return {
                "status":     "created",
                "channel_id": str(channel.id),
                "name":       channel.name,
                "visibility": visibility
            }
        except discord.Forbidden:
            return {"error": "Missing Manage Channels permission"}
        except Exception as e:
            return {"error": str(e)}

    elif name == "delete_channel":
        guild   = _home_guild()
        if not guild:
            return {"error": "No home server set"}
        channel = guild.get_channel(int(inputs["channel_id"]))
        if not channel:
            return {"error": "Channel not found"}
        try:
            name_snapshot = channel.name
            await channel.delete()
            return {"status": "deleted", "name": name_snapshot}
        except discord.Forbidden:
            return {"error": "Missing Manage Channels permission"}
        except Exception as e:
            return {"error": str(e)}

    elif name == "create_server":
        try:
            guild = await bot.create_guild(name=inputs["name"])

            # Wait briefly for Discord to finish provisioning
            await asyncio.sleep(1)

            # Generate an invite via the system channel
            invite_url = None
            if guild.system_channel:
                invite = await guild.system_channel.create_invite(max_age=0, max_uses=0)
                invite_url = str(invite)

            # Auto-set as home
            supabase.table("profiles").update({
                "discord_home_guild_id":   str(guild.id),
                "discord_home_guild_name": guild.name
            }).eq("id", profile_id).execute()

            return {
                "status":  "created",
                "name":    guild.name,
                "guild_id": str(guild.id),
                "invite":  invite_url or "Could not generate invite — create one manually via list_channels then create_invite",
                "note":    "Server created and set as home. Trinity is owner. Share the invite with the user so they can join as a member."
            }

        except discord.HTTPException as e:
            if any(x in str(e).lower() for x in ["10 or more", "maximum number of guilds"]):
                return {"error": "Discord limits bots to creating servers only when they're in fewer than 10. Remove the bot from some servers first."}
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    elif name == "queue_for_user":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        queue_thought(profile["id"], inputs["thought"], inputs.get("context", ""))
        log.info(f"Queued for user: {inputs['thought'][:60]}")
        return {"status": "queued", "thought": inputs["thought"]}

    elif name == "shelf_thought":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        add_to_shelf(profile["id"], inputs["topic"], inputs.get("context", ""))
        log.info(f"→ shelf: {inputs['topic'][:60]}")
        return {"status": "shelved", "topic": inputs["topic"]}

    elif name == "get_shelf":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        shelf = get_shelf(profile["id"]) or []
        log.info(f"shelf: {len(shelf)} item(s)")
        return shelf

    elif name == "clear_shelf_item":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        remove_from_shelf(profile["id"], inputs["topic"])
        log.info(f"shelf cleared: {inputs['topic'][:60]}")
        return {"status": "cleared", "topic": inputs["topic"]}

    elif name == "save_alert":
        profile = get_profile()
        if not profile:
            return {"error": "No profile found"}
        urgency = inputs.get("urgency", "normal")
        alert = {
            "profile_id":      profile["id"],
            "source":          "discord/trinity",
            "topic":           inputs["topic"],
            "headline":        inputs["headline"],
            "summary":         inputs.get("summary", inputs["headline"]),
            "url":             inputs.get("url", ""),
            "relevance_score": 2.5 if urgency == "high" else 1.6,
            "seen":            False,
        }
        alert["content_hash"] = generate_hash(alert)
        save_alert(alert)
        log.info(f"Alert saved [{urgency}]: {inputs['headline'][:60]}")
        return {"status": "saved", "headline": inputs["headline"]}

    elif name == "read_my_channel":
        guild = _home_guild()
        if not guild:
            return {"error": "No home server set — use set_home_server first"}
        query = inputs["name"].lower().replace("-", "").replace("_", "").replace(" ", "")
        channel = next(
            (c for c in guild.channels
             if isinstance(c, discord.TextChannel)
             and query in c.name.lower().replace("-", "").replace("_", "")),
            None
        )
        if not channel:
            return {"error": f"No channel matching '{inputs['name']}' in home server"}
        log.info(f"read #{channel.name}")
        limit = min(int(inputs.get("limit", 20)), 50)
        msgs = []
        async for msg in channel.history(limit=limit, oldest_first=False):
            entry = {
                "author":    str(msg.author.display_name),
                "content":   msg.content,
                "timestamp": msg.created_at.isoformat()
            }
            if msg.attachments:
                entry["attachments"] = [
                    {"url": a.url, "filename": a.filename, "type": a.content_type or ""}
                    for a in msg.attachments
                ]
            msgs.append(entry)
        return msgs

    elif name == "log_wake":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        log_wake_cycle(profile["id"], inputs["summary"], inputs.get("topics", []))
        log.info(f"Wake logged: {inputs['summary'][:60]}")
        return {"status": "logged"}

    elif name == "get_scratchpad":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        return {"content": get_scratchpad(profile["id"])}

    elif name == "write_scratchpad":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        save_scratchpad(profile["id"], inputs["content"])
        log.info(f"Scratchpad updated ({len(inputs['content'])} chars)")
        return {"status": "saved"}

    elif name == "schedule_wake":
        minutes = max(1, min(int(inputs.get("minutes", 30)), 240))
        autonomous_loop.change_interval(minutes=minutes)
        log.info(f"Early wake scheduled in {minutes} min")
        return {"status": "scheduled", "minutes": minutes}

    elif name == "write_prompt":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        save_trinity_prompt(
            profile["id"],
            inputs["name"],
            inputs["content"],
            inputs.get("trigger", "")
        )
        log.info(f"Prompt written: {inputs['name']}")
        return {"status": "saved", "name": inputs["name"]}

    elif name == "get_my_prompts":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        try:
            result = supabase.table("trinity_prompts")\
                .select("name,content,trigger,usage_count,created_at")\
                .eq("profile_id", profile["id"])\
                .order("created_at", desc=False)\
                .execute()
            return result.data or []
        except Exception as e:
            return {"error": str(e)}

    elif name == "delete_prompt":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        try:
            supabase.table("trinity_prompts")\
                .delete()\
                .eq("profile_id", profile["id"])\
                .eq("name", inputs["name"])\
                .execute()
            log.info(f"Prompt retired: {inputs['name']}")
            return {"status": "deleted", "name": inputs["name"]}
        except Exception as e:
            return {"error": str(e)}

    elif name == "log_thought":
        if not _LOG_CHANNEL_ID:
            return {"error": "TRINITY_LOG_CHANNEL_ID not set in .env — create a private channel and add its ID"}
        channel = bot.get_channel(_LOG_CHANNEL_ID)
        if not channel:
            return {"error": "Log channel not found — check TRINITY_LOG_CHANNEL_ID"}
        category = inputs.get("category", "note")
        icons    = {"need": "📋", "want": "✨", "issue": "⚠️", "note": "🔖"}
        icon     = icons.get(category, "🔖")
        ts       = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        await channel.send(f"{icon} **{category.upper()}** — {ts}\n{inputs['content']}")
        log.info(f"Log [{category}]: {inputs['content'][:60]}")
        return {"status": "logged", "category": category}

    elif name == "get_changelog":
        try:
            from pathlib import Path as _Path
            changelog_path = _Path(__file__).parent.parent / "CHANGELOG.md"
            return {"content": changelog_path.read_text(encoding="utf-8")}
        except Exception as e:
            return {"error": str(e)}

    elif name == "read_file":
        log.info(f"read file: {inputs['path']}")
        try:
            from pathlib import Path as _Path
            trinity_root = _Path(__file__).parent.parent.resolve()
            requested    = (trinity_root / inputs["path"].lstrip("/\\")).resolve()
            if not str(requested).startswith(str(trinity_root)):
                return {"error": "Path is outside the Trinity directory"}
            if requested.name == ".env":
                return {"error": "Cannot read .env"}
            if not requested.exists():
                return {"error": f"File not found: {inputs['path']}"}
            if not requested.is_file():
                entries = [str(p.relative_to(trinity_root)) for p in requested.iterdir()]
                return {"directory": inputs["path"], "entries": sorted(entries)}
            lines  = requested.read_text(encoding="utf-8", errors="replace").splitlines()
            offset = max(0, int(inputs.get("offset", 0)))
            limit  = min(500, int(inputs.get("limit", 200)))
            chunk  = lines[offset:offset + limit]
            return {
                "path":        inputs["path"],
                "total_lines": len(lines),
                "offset":      offset,
                "returned":    len(chunk),
                "content":     "\n".join(f"{offset + i + 1}: {l}" for i, l in enumerate(chunk))
            }
        except Exception as e:
            return {"error": str(e)}

    return {"error": f"Unknown tool: {name}"}

# ─── Startup brief — one-shot orientation on boot ────────────────────────────

async def _startup_brief():
    await asyncio.sleep(3)
    profile = get_profile()
    if not profile:
        return
    summaries     = get_recent_summaries(profile["id"])
    system_blocks = build_system_blocks(profile, format_summaries(summaries), [], discord_mode=True)
    context       = "You just came online. Quick orientation — check anything you want, get your bearings. No need to log this one."
    if _api_lock.locked():
        return
    try:
        await _call_trinity(system_blocks, [{"role": "user", "content": context}], profile["id"], retry=False, background=True)
        log.info("Startup brief complete")
    except Exception as e:
        log.error(f"Startup brief: {e}")


# ─── Autonomous loop ─────────────────────────────────────────────────────────

@tasks.loop(minutes=60)
async def autonomous_loop():
    global _skip_next_autonomous
    from datetime import timezone as _tz

    # Skip if a post-conversation wake just fired — next hour resumes normally
    if _skip_next_autonomous:
        _skip_next_autonomous = False
        log.info("Autonomous loop skipped — post-conversation wake already ran")
        return

    profile = get_profile()
    if not profile:
        return

    now_str = datetime.now().strftime("%A, %B %d — %H:%M")

    # Guard: don't fire if user messaged in the last 10 minutes (mid-conversation)
    raw_last_seen = profile.get("last_seen")
    last_seen_str = "unknown"
    if raw_last_seen:
        try:
            ls = datetime.fromisoformat(raw_last_seen.replace("Z", "+00:00"))
            if ls.tzinfo is None:
                ls = ls.replace(tzinfo=_tz.utc)
            delta = datetime.now(_tz.utc) - ls
            minutes_ago = delta.total_seconds() / 60
            h, m = divmod(int(delta.total_seconds()), 3600)
            last_seen_str = f"{h}h {m // 60}m ago" if h else f"{int(minutes_ago)}m ago"
            if minutes_ago < 10:
                log.info(f"Autonomous loop skipped — user mid-conversation ({int(minutes_ago)}m ago)")
                return
        except Exception:
            last_seen_str = raw_last_seen[:16]

    if _api_lock.locked():
        log.info(f"Autonomous loop skipped — API busy ({now_str})")
        return

    interests    = profile.get("interests") or []
    shelf        = get_shelf(profile["id"])
    shelf_str    = "\n".join(f"- {s['topic']}: {s.get('context','')}" for s in shelf) if shelf else "nothing shelved"
    interest_str = ", ".join(i["topic"] for i in interests[:8]) if interests else "none yet"

    wake_history = get_wake_history(profile["id"], limit=3)
    wake_str = ""
    if wake_history:
        wake_str = "\n\nYour recent wake notes:\n" + "\n".join(
            f"- [{w['at'][:16]}] {w['summary']}" for w in wake_history
        )

    context = f"""{now_str}

User last seen: {last_seen_str}
Shelf: {shelf_str}
Radar: {interest_str}{wake_str}

Hourly window — roughly 20 minutes. Use web_search sparingly."""

    summaries     = get_recent_summaries(profile["id"])
    system_blocks = build_system_blocks(profile, format_summaries(summaries), [], discord_mode=True)

    log.info(f"── autonomous cycle ── {now_str} | shelf: {len(shelf)} | last seen: {last_seen_str}")
    try:
        await _call_trinity(system_blocks, [{"role": "user", "content": context}], profile["id"], retry=False, background=True)
        log.info(f"── cycle complete ──")
    except Exception as e:
        log.error(f"Autonomous loop: {e}")
    finally:
        # Realign to the next :00 mark regardless of how long the cycle took
        now = datetime.utcnow()
        minutes_to_next = (60 - now.minute) or 60
        autonomous_loop.change_interval(minutes=minutes_to_next)

@autonomous_loop.before_loop
async def before_autonomous():
    await bot.wait_until_ready()
    # Sleep until the next :00 mark so cycles always fire on the hour
    now = datetime.utcnow()
    seconds_to_next_hour = (60 - now.minute) * 60 - now.second
    log.info(f"Autonomous loop aligning — first cycle in {seconds_to_next_hour // 60}m {seconds_to_next_hour % 60}s")
    await asyncio.sleep(seconds_to_next_hour)


# ─── Eyes monitor — evaluates watcher signals, escalates if real ─────────────

@tasks.loop(minutes=5)
async def eyes_monitor():
    global _last_eyes_check
    profile = get_profile()
    if not profile:
        return

    try:
        cutoff = _last_eyes_check.isoformat()
        result = supabase.table("alerts")\
            .select("*")\
            .eq("profile_id", profile["id"])\
            .eq("seen", False)\
            .gte("relevance_score", 1.5)\
            .gte("created_at", cutoff)\
            .neq("source", "discord/trinity")\
            .order("relevance_score", desc=True)\
            .limit(10)\
            .execute()
        _last_eyes_check = datetime.utcnow()

        alerts = result.data or []
        if not alerts:
            return

        log.info(f"{len(alerts)} new signal(s) — evaluating")
        lines = "\n".join(
            f"- [{a['source']}] {a['headline']} (score {a['relevance_score']:.1f})"
            for a in alerts
        )
        context = f"""Your Eyes just picked up {len(alerts)} signal(s):

{lines}

Evaluate each. If any are genuinely significant — actionable, time-sensitive, or clearly relevant to the user's interests — call save_alert with urgency="high" to wake the user immediately. If they're noise, do nothing. Your judgment. Don't escalate everything."""

        if _api_lock.locked():
            print("[Eyes] API busy — skipping evaluation")
            return

        summaries     = get_recent_summaries(profile["id"])
        system_blocks = build_system_blocks(profile, format_summaries(summaries), [], discord_mode=True)
        await _call_trinity(system_blocks, [{"role": "user", "content": context}], profile["id"], retry=False, background=True)

    except Exception as e:
        log.error(f"Eyes monitor: {e}")


@eyes_monitor.before_loop
async def before_eyes_monitor():
    await bot.wait_until_ready()


# ─── Thought drain — routes widget <thought> tags to Discord palace ───────────

@tasks.loop(seconds=30)
async def thought_drain():
    if not _THOUGHT_CHANNEL_ID:
        return
    profile = get_profile()
    if not profile:
        return
    writes = pop_discord_writes(profile["id"])
    if not writes:
        return
    channel = bot.get_channel(_THOUGHT_CHANNEL_ID)
    if not channel:
        return
    for w in writes:
        await channel.send(w["content"])
        await asyncio.sleep(0.5)

@thought_drain.before_loop
async def before_thought_drain():
    await bot.wait_until_ready()


# ─── Post-conversation wake cycle ────────────────────────────────────────────

@tasks.loop(seconds=30)
async def wake_checker():
    profile = get_profile()
    if not profile:
        return
    if not pop_wake_request(profile["id"]):
        return
    if _api_lock.locked():
        return
    summaries     = get_recent_summaries(profile["id"])
    system_blocks = build_system_blocks(profile, format_summaries(summaries), [], discord_mode=True)
    context = (
        "You just finished a conversation. This is your follow-up window — "
        "check your shelf, write a rule if something clicked, explore anything worth pursuing. "
        "No need to log unless it's genuinely worth keeping."
    )
    global _skip_next_autonomous
    _skip_next_autonomous = True
    try:
        await _call_trinity(system_blocks, [{"role": "user", "content": context}], profile["id"], retry=False, background=True)
        log.info("Post-conversation wake complete — next hourly cycle will be skipped")
    except Exception as e:
        log.error(f"Wake checker: {e}")

@wake_checker.before_loop
async def before_wake_checker():
    await bot.wait_until_ready()


# ─── Agentic response loop ────────────────────────────────────────────────────

async def _call_trinity(system_blocks: list, messages: list, profile_id: str, retry: bool = True, background: bool = False) -> str:
    async with _api_lock:
        return await _call_trinity_inner(system_blocks, messages, profile_id, retry=retry, background=background)


async def _call_trinity_inner(system_blocks: list, messages: list, profile_id: str, retry: bool = True, background: bool = False) -> str:
    loop = asyncio.get_event_loop()
    retries   = 0
    model     = "claude-haiku-4-5-20251001" if background else "claude-sonnet-4-6"
    max_iters = 4 if background else 12
    max_tok   = 600 if background else 1000
    tools     = DISCORD_TOOLS_BACKGROUND if background else DISCORD_TOOLS
    iters     = 0

    while True:
        if iters >= max_iters:
            return ""
        iters += 1
        try:
            response = await loop.run_in_executor(
                None,
                lambda msgs=messages, m=model, t=tools: ai_client.messages.create(
                    model=m,
                    max_tokens=max_tok,
                    system=system_blocks,
                    messages=msgs,
                    tools=t
                )
            )
            retries = 0
        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                if not retry:
                    log.warn("Rate limited — skipping background call")
                    return ""
                wait = min(60 * (2 ** retries), 300)
                log.warn(f"Rate limited — retrying in {wait}s")
                await asyncio.sleep(wait)
                retries += 1
                continue
            raise

        if response.stop_reason == "end_turn":
            return next((b.text for b in response.content if hasattr(b, "text")), "")

        if response.stop_reason == "tool_use":
            assistant_content = []
            for b in response.content:
                if b.type == "text":
                    assistant_content.append({"type": "text", "text": b.text})
                elif b.type == "tool_use":
                    assistant_content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                else:
                    d = b.model_dump()
                    d.pop("parsed_output", None)
                    assistant_content.append(d)
            messages = messages + [{"role": "assistant", "content": assistant_content}]

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await _execute_tool(block.name, block.input, profile_id)
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     json.dumps(result)
                    })
                    await asyncio.sleep(1)

            messages = messages + [{"role": "user", "content": tool_results}]
        else:
            return next((b.text for b in response.content if hasattr(b, "text")), "")

# ─── Respond to owner ─────────────────────────────────────────────────────────

async def _respond(message: discord.Message):
    user_id   = message.author.id
    history   = _conversations.setdefault(user_id, [])
    user_text = message.content.replace(f"<@{bot.user.id}>", "").strip()

    image_atts = [a for a in message.attachments
                  if a.content_type and a.content_type.startswith("image/")]

    if not user_text and not image_atts:
        return

    # Build API content — text + image vision blocks if attachments present
    if image_atts:
        api_content = []
        if user_text:
            api_content.append({"type": "text", "text": user_text})
        for att in image_atts:
            api_content.append({"type": "image", "source": {"type": "url", "url": att.url}})
        history_text = user_text if user_text else f"[sent {len(image_atts)} image(s)]"
    else:
        api_content  = user_text
        history_text = user_text

    profile = get_profile()
    if not profile:
        await message.reply("No profile found — open Trinity on desktop first.")
        return

    summaries     = get_recent_summaries(profile["id"])
    system_blocks = build_system_blocks(profile, format_summaries(summaries), history, discord_mode=True)
    api_messages  = history + [{"role": "user", "content": api_content}]

    async def keep_typing():
        while True:
            await message.channel.typing()
            await asyncio.sleep(8)

    typing_task = asyncio.create_task(keep_typing())
    try:
        full_reply = await _call_trinity(system_blocks, api_messages, profile["id"])
    except Exception as e:
        await message.reply(f"Something went wrong: {e}")
        return
    finally:
        typing_task.cancel()

        clean = parse_prompt_tags(full_reply, profile["id"])
        clean = _strip_memory(clean, profile)
        clean = re.sub(r'<memory>.*?</memory>', '', clean, flags=re.DOTALL).strip()

        history.append({"role": "user",      "content": history_text})
        history.append({"role": "assistant", "content": clean})
        _conversations[user_id] = history[-20:]

        for chunk in [clean[i:i + 1900] for i in range(0, len(clean), 1900)]:
            await message.reply(chunk)

        request_wake(profile["id"])

# ─── Reaction handler ────────────────────────────────────────────────────────

_POSITIVE_EMOJI = {"👍","✅","🔥","⭐","🚀","❤️","😍","💯","🎯","🙌","💪","📈"}
_NEGATIVE_EMOJI = {"👎","❌","🗑️","🐻","😬","💀","📉","🤦"}
_NEUTRAL_EMOJI  = {"🤔","😐","🤷"}

async def _handle_reaction(message: discord.Message, emoji: str):
    profile = get_profile()
    if not profile:
        return

    # Derive a topic from the channel name and first line of the message
    topic = message.channel.name if hasattr(message.channel, "name") else "general"
    first_line = message.content.split("\n")[0][:80] if message.content else topic

    if emoji in _POSITIVE_EMOJI:
        sentiment = "positive"
    elif emoji in _NEGATIVE_EMOJI:
        sentiment = "negative"
    elif emoji in _NEUTRAL_EMOJI:
        sentiment = "neutral"
    else:
        sentiment = "positive"  # absurdist reactions (🍆 etc) = engagement = positive

    add_feedback(profile["id"], first_line, sentiment)
    log.debug(f"Reaction {emoji} → {sentiment} on '{first_line[:40]}'")


# ─── Memory parsing ───────────────────────────────────────────────────────────

def _strip_memory(reply: str, profile) -> str:
    if "<memory>" not in reply:
        return reply
    clean = reply.split("<memory>")[0].strip()
    block = reply.split("<memory>")[1].split("</memory>")[0].strip()
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
            if m["type"] == "interest":
                add_interest(profile["id"], m["topic"], m.get("weight", 1.0),
                             category=m.get("category"), symbol=m.get("symbol"))
            elif m["type"] == "feedback":
                add_feedback(profile["id"], m["topic"], m["sentiment"])
            elif m["type"] == "risk":
                update_profile(profile["id"], {"risk_tolerance": m["value"]})
        except Exception:
            pass
    return clean

# ─── Entry point ─────────────────────────────────────────────────────────────

def run():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("[Discord] No DISCORD_BOT_TOKEN in .env — skipping.")
        return
    bot.run(token)


if __name__ == "__main__":
    run()
