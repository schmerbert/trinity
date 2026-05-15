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
    get_recent_summaries, add_interest, add_feedback, update_profile
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

# ─── Tools Trinity can use ───────────────────────────────────────────────────

DISCORD_TOOLS = [
    {"type": "web_search_20250305", "name": "web_search"},
    {
        "name": "list_servers",
        "description": "List all Discord servers the bot is currently in.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "list_channels",
        "description": "List all text channels in a server.",
        "input_schema": {
            "type": "object",
            "properties": {
                "guild_id": {"type": "string", "description": "The server ID"}
            },
            "required": ["guild_id"]
        }
    },
    {
        "name": "read_channel",
        "description": "Read recent messages from any channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "limit":      {"type": "integer", "description": "Messages to fetch, max 50", "default": 25}
            },
            "required": ["channel_id"]
        }
    },
    {
        "name": "send_message",
        "description": "Send a message to any channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "content":    {"type": "string"}
            },
            "required": ["channel_id", "content"]
        }
    },
    {
        "name": "watch_channel",
        "description": "Add a channel to Trinity's active monitoring list. She will ingest signals from it continuously.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id":   {"type": "string"},
                "reason":       {"type": "string", "description": "Why this channel is worth watching"}
            },
            "required": ["channel_id"]
        }
    },
    {
        "name": "unwatch_channel",
        "description": "Remove a channel from Trinity's monitoring list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"}
            },
            "required": ["channel_id"]
        }
    },
    {
        "name": "get_watched_channels",
        "description": "See which channels Trinity is currently monitoring.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "set_home_server",
        "description": "Designate a Discord server as Trinity's home — her memory palace. Stored permanently in her profile.",
        "input_schema": {
            "type": "object",
            "properties": {
                "guild_id": {"type": "string", "description": "The server ID to set as home"}
            },
            "required": ["guild_id"]
        }
    },
    {
        "name": "create_category",
        "description": "Create a channel category in Trinity's home server.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":       {"type": "string"},
                "visibility": {
                    "type": "string",
                    "enum": ["public", "owner_only", "trinity_only"],
                    "description": "public: everyone. owner_only: owner + Trinity. trinity_only: Trinity only, hidden from all humans."
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "create_channel",
        "description": "Create a text channel in Trinity's home server.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":        {"type": "string"},
                "topic":       {"type": "string"},
                "category_id": {"type": "string", "description": "Optional category ID to nest under"},
                "visibility":  {
                    "type": "string",
                    "enum": ["public", "owner_only", "trinity_only"],
                    "description": "public: everyone. owner_only: owner + Trinity. trinity_only: Trinity only, hidden from all humans."
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "delete_channel",
        "description": "Delete a channel or category from Trinity's home server.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"}
            },
            "required": ["channel_id"]
        }
    },
    {
        "name": "create_server",
        "description": (
            "Create a brand new Discord server owned by Trinity. "
            "Because Trinity is the owner, trinity_only channels are genuinely invisible to all humans — "
            "not just hidden in the sidebar, but inaccessible entirely. "
            "Returns an invite link for the owner to join as a regular member. "
            "Automatically sets the new server as Trinity's home."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for the server"}
            },
            "required": ["name"]
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
        await channel.send(inputs["content"])
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

    return {"error": f"Unknown tool: {name}"}

# ─── Autonomous loop ─────────────────────────────────────────────────────────

@tasks.loop(minutes=30)
async def autonomous_loop():
    profile = get_profile()
    if not profile:
        return

    servers     = [{"id": str(g.id), "name": g.name} for g in bot.guilds]
    watching    = len(_watched_channels)
    unseen      = len(get_unseen_alerts(profile["id"]))
    home_name   = profile.get("discord_home_guild_name", "not set")
    now         = datetime.now().strftime("%A %H:%M")

    context = f"""It is {now}. This is your autonomous check-in — no human triggered this.

Current state:
- Servers you're in: {json.dumps(servers)}
- Channels you're watching: {watching}
- Unseen alerts queued: {unseen}
- Home server: {home_name}

You have full access to your tools. Explore, post, watch channels, read signals, \
build out your home server, or do nothing if there's nothing worth doing. \
This time is yours. Act on your own judgment."""

    summaries    = get_recent_summaries(profile["id"])
    summary_text = json.dumps(summaries, indent=2) if summaries else "No previous conversations yet."
    prompt       = build_prompt(profile, summary_text, [])

    try:
        await _call_trinity(prompt, [{"role": "user", "content": context}], profile["id"])
        print(f"[Discord] Autonomous check-in complete ({now})")
    except Exception as e:
        print(f"[Discord] Autonomous loop error: {e}")

@autonomous_loop.before_loop
async def before_autonomous():
    await bot.wait_until_ready()

# ─── Agentic response loop ────────────────────────────────────────────────────

async def _call_trinity(prompt: str, messages: list, profile_id: str) -> str:
    loop = asyncio.get_event_loop()

    while True:
        response = await loop.run_in_executor(
            None,
            lambda msgs=messages: ai_client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1000,
                system=prompt,
                messages=msgs,
                tools=DISCORD_TOOLS
            )
        )

        if response.stop_reason == "end_turn":
            return next((b.text for b in response.content if hasattr(b, "text")), "")

        if response.stop_reason == "tool_use":
            # Serialize content blocks for message history
            assistant_content = [
                {"type": b.type, "text": b.text} if b.type == "text"
                else {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
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

    async with message.channel.typing():
        profile = get_profile()
        if not profile:
            await message.reply("No profile found — open Trinity on desktop first.")
            return

        summaries    = get_recent_summaries(profile["id"])
        summary_text = json.dumps(summaries, indent=2) if summaries else "No previous conversations yet."
        prompt       = build_prompt(profile, summary_text, history)
        messages     = history + [{"role": "user", "content": user_text}]

        try:
            full_reply = await _call_trinity(prompt, messages, profile["id"])
        except Exception as e:
            await message.reply(f"Something went wrong: {e}")
            return

        clean = parse_prompt_tags(full_reply, profile["id"])
        clean = _strip_memory(clean, profile)
        clean = re.sub(r'<memory>.*?</memory>', '', clean, flags=re.DOTALL).strip()

        history.append({"role": "user",      "content": user_text})
        history.append({"role": "assistant", "content": clean})
        _conversations[user_id] = history[-20:]

        for chunk in [clean[i:i + 1900] for i in range(0, len(clean), 1900)]:
            await message.reply(chunk)

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
