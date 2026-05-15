import os
import sys
import json
import re
import asyncio
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord
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

OWNER_ID = os.getenv("DISCORD_OWNER_ID", "")
OWNER_ID = int(OWNER_ID) if OWNER_ID.isdigit() else 0

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
    }
]

# ─── Events ──────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"[Discord] Online as {bot.user}")
    await _load_watched_channels()
    await _post_pending_alerts()


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

    return {"error": f"Unknown tool: {name}"}

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
