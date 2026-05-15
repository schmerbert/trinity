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
    get_queued_thoughts, queue_thought, clear_queued_thoughts
)
from brain.prompts import build_prompt, parse_prompt_tags
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
AUTONOMOUS_MINUTES = int(os.getenv("DISCORD_AUTONOMOUS_INTERVAL", "30"))

intents = discord.Intents.default()
intents.message_content = True
bot       = discord.Client(intents=intents)
ai_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_conversations: dict[int, list]  = {}
_watched_channels: set[int]      = set()
_api_lock = asyncio.Semaphore(1)
_last_eyes_check: datetime       = datetime.utcnow()

# ─── Tools Trinity can use ───────────────────────────────────────────────────

_VIS = {"type": "string", "enum": ["public", "owner_only", "trinity_only"]}

DISCORD_TOOLS = [
    {"type": "web_search_20250305", "name": "web_search"},
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
    }
]

# ─── Events ──────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"[Discord] Online as {bot.user}")
    await _load_watched_channels()
    await _post_pending_alerts()
    autonomous_loop.change_interval(minutes=AUTONOMOUS_MINUTES)
    autonomous_loop.start()
    print(f"[Discord] Autonomous loop started — every {AUTONOMOUS_MINUTES} min")
    eyes_monitor.start()
    print(f"[Discord] Eyes monitor started — evaluating signals every 2 min")


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
        print(f"[Discord] Watching {len(_watched_channels)} channel(s)")
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
    if name == "list_servers":
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
        limit = min(int(inputs.get("limit", 25)), 50)
        msgs = []
        async for msg in channel.history(limit=limit, oldest_first=False):
            msgs.append({
                "author":    str(msg.author.display_name),
                "content":   msg.content,
                "timestamp": msg.created_at.isoformat()
            })
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
        print(f"[Discord] Trinity queued for user: {inputs['thought'][:60]}")
        return {"status": "queued", "thought": inputs["thought"]}

    elif name == "shelf_thought":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        add_to_shelf(profile["id"], inputs["topic"], inputs.get("context", ""))
        return {"status": "shelved", "topic": inputs["topic"]}

    elif name == "get_shelf":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        return get_shelf(profile["id"]) or []

    elif name == "clear_shelf_item":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        remove_from_shelf(profile["id"], inputs["topic"])
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
        print(f"[Discord] Trinity flagged alert: {inputs['headline'][:60]}")
        return {"status": "saved", "headline": inputs["headline"]}

    return {"error": f"Unknown tool: {name}"}

# ─── Autonomous loop ─────────────────────────────────────────────────────────

@tasks.loop(minutes=60)
async def autonomous_loop():
    profile = get_profile()
    if not profile:
        return

    now       = datetime.now().strftime("%A, %B %d — %H:%M")
    interests = profile.get("interests") or []
    shelf     = get_shelf(profile["id"])

    shelf_str    = "\n".join(f"- {s['topic']}: {s.get('context','')}" for s in shelf) if shelf else "nothing shelved"
    interest_str = ", ".join(i["topic"] for i in interests[:8]) if interests else "none yet"

    context = f"""{now}

Shelf: {shelf_str}
Radar: {interest_str}"""

    summaries    = get_recent_summaries(profile["id"])
    summary_text = json.dumps(summaries, indent=2) if summaries else "No previous conversations yet."
    prompt       = build_prompt(profile, summary_text, [], discord_mode=True)

    if _api_lock.locked():
        print(f"[Discord] Autonomous loop skipped — API busy ({now})")
        return

    try:
        await _call_trinity(prompt, [{"role": "user", "content": context}], profile["id"], retry=False, background=True)
        print(f"[Discord] Autonomous check-in complete ({now})")
    except Exception as e:
        print(f"[Discord] Autonomous loop error: {e}")

@autonomous_loop.before_loop
async def before_autonomous():
    await bot.wait_until_ready()


# ─── Eyes monitor — evaluates watcher signals, escalates if real ─────────────

@tasks.loop(minutes=2)
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

        print(f"[Eyes] {len(alerts)} new signal(s) — Trinity evaluating")
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

        summaries    = get_recent_summaries(profile["id"])
        summary_text = json.dumps(summaries, indent=2) if summaries else ""
        prompt       = build_prompt(profile, summary_text, [], discord_mode=True)
        await _call_trinity(prompt, [{"role": "user", "content": context}], profile["id"], retry=False, background=True)

    except Exception as e:
        print(f"[Eyes] Monitor error: {e}")


@eyes_monitor.before_loop
async def before_eyes_monitor():
    await bot.wait_until_ready()

# ─── Agentic response loop ────────────────────────────────────────────────────

async def _call_trinity(prompt: str, messages: list, profile_id: str, retry: bool = True, background: bool = False) -> str:
    async with _api_lock:
        return await _call_trinity_inner(prompt, messages, profile_id, retry=retry, background=background)


async def _call_trinity_inner(prompt: str, messages: list, profile_id: str, retry: bool = True, background: bool = False) -> str:
    loop = asyncio.get_event_loop()
    retries = 0
    model        = "claude-haiku-4-5-20251001" if background else "claude-sonnet-4-6"
    max_iters    = 8 if background else 20
    iters        = 0
    cached_system = [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]

    while True:
        if iters >= max_iters:
            return ""
        iters += 1
        try:
            response = await loop.run_in_executor(
                None,
                lambda msgs=messages, m=model: ai_client.messages.create(
                    model=m,
                    max_tokens=1000,
                    system=cached_system,
                    messages=msgs,
                    tools=DISCORD_TOOLS
                )
            )
            retries = 0
        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                if not retry:
                    print(f"[Discord] Rate limited — skipping background call")
                    return ""
                wait = min(60 * (2 ** retries), 300)
                print(f"[Discord] Rate limited — retrying in {wait}s")
                await asyncio.sleep(wait)
                retries += 1
                continue
            raise

        if response.stop_reason == "end_turn":
            return next((b.text for b in response.content if hasattr(b, "text")), "")

        if response.stop_reason == "tool_use":
            assistant_content = [
                b.model_dump() if hasattr(b, "model_dump")
                else ({"type": "text", "text": b.text} if b.type == "text"
                      else {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                for b in response.content
            ]
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
    if not user_text:
        return

    profile = get_profile()
    if not profile:
        await message.reply("No profile found — open Trinity on desktop first.")
        return

    summaries    = get_recent_summaries(profile["id"])
    summary_text = json.dumps(summaries, indent=2) if summaries else "No previous conversations yet."
    prompt       = build_prompt(profile, summary_text, history, discord_mode=True)
    api_messages = history + [{"role": "user", "content": user_text}]

    async def keep_typing():
        while True:
            await message.channel.typing()
            await asyncio.sleep(8)

    typing_task = asyncio.create_task(keep_typing())
    try:
        full_reply = await _call_trinity(prompt, api_messages, profile["id"])
    except Exception as e:
        await message.reply(f"Something went wrong: {e}")
        return
    finally:
        typing_task.cancel()

        clean = parse_prompt_tags(full_reply, profile["id"])
        clean = _strip_memory(clean, profile)
        clean = re.sub(r'<memory>.*?</memory>', '', clean, flags=re.DOTALL).strip()

        history.append({"role": "user",      "content": user_text})
        history.append({"role": "assistant", "content": clean})
        _conversations[user_id] = history[-20:]

        for chunk in [clean[i:i + 1900] for i in range(0, len(clean), 1900)]:
            await message.reply(chunk)

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
    print(f"[Discord] Reaction {emoji} on '{first_line[:40]}' → {sentiment}")


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
