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

env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

from brain.memory import (
    get_profile, save_alert,
    get_unseen_alerts, mark_alerts_seen,
    get_recent_summaries,
    add_interest, add_feedback, update_profile
)
from brain.prompts import build_prompt, parse_prompt_tags
from eyes.scraper import score_relevance, generate_hash

# ─── Config ──────────────────────────────────────────────────────────────────

def _env_int(key):
    val = os.getenv(key, "")
    return int(val) if val.isdigit() else 0

MONITOR_CHANNELS = [
    int(x.strip()) for x in os.getenv("DISCORD_MONITOR_CHANNELS", "").split(",")
    if x.strip().isdigit()
]
ALERT_CHANNEL_ID = _env_int("DISCORD_ALERT_CHANNEL")
OWNER_ID         = _env_int("DISCORD_OWNER_ID")

# ─── Bot setup ───────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot       = discord.Client(intents=intents)
ai_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_conversations: dict[int, list] = {}

# ─── Events ──────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"[Discord] Online as {bot.user}")
    await _post_pending_alerts()


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    if message.channel.id in MONITOR_CHANNELS:
        await _ingest_signal(message)

    is_dm      = isinstance(message.channel, discord.DMChannel)
    is_mention = bot.user in message.mentions
    if (is_dm or is_mention) and message.author.id == OWNER_ID:
        await _respond(message)

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
        "profile_id":    profile["id"],
        "source":        source,
        "topic":         matched_topic,
        "headline":      text[:120],
        "summary":       text[:300],
        "url":           msg_url,
        "relevance_score": score,
        "seen":          False,
    }
    alert["content_hash"] = generate_hash(alert)
    save_alert(alert)

# ─── Post pending alerts to alert channel ────────────────────────────────────

async def _post_pending_alerts():
    if not ALERT_CHANNEL_ID:
        return
    profile = get_profile()
    if not profile:
        return

    alerts = get_unseen_alerts(profile["id"])
    if not alerts:
        return

    channel = bot.get_channel(ALERT_CHANNEL_ID)
    if not channel:
        return

    for alert in alerts[:5]:
        prefix = "⚡" if alert["relevance_score"] >= 2.5 else "●"
        await channel.send(f"{prefix} **{alert['headline']}**\n{alert['url']}")

    mark_alerts_seen(profile["id"])

# ─── Voice: respond to DMs / mentions ────────────────────────────────────────

async def _respond(message: discord.Message):
    user_id  = message.author.id
    history  = _conversations.setdefault(user_id, [])
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

        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: ai_client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=800,
                    system=prompt,
                    messages=messages,
                    tools=[{"type": "web_search_20250305", "name": "web_search"}]
                )
            )
            full_reply = next((b.text for b in response.content if hasattr(b, "text")), "")
        except Exception as e:
            await message.reply(f"Something went wrong: {e}")
            return

        clean = parse_prompt_tags(full_reply, profile["id"])
        clean = _strip_memory(clean, profile)
        clean = re.sub(r'<memory>.*?</memory>', '', clean, flags=re.DOTALL).strip()

        history.append({"role": "user",      "content": user_text})
        history.append({"role": "assistant", "content": clean})
        _conversations[user_id] = history[-20:]

        # Discord 2000 char hard limit — split if needed
        chunks = [clean[i:i + 1900] for i in range(0, len(clean), 1900)]
        for chunk in chunks:
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
