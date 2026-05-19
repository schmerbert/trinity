import os
import sys
import asyncio
from datetime import datetime
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord
from discord.ext import tasks
from dotenv import load_dotenv
from pathlib import Path
from supabase import create_client

env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

from brain.memory import (
    get_profile, save_alert, get_unseen_alerts,
    add_feedback,
    pop_discord_writes, queue_self_thought
)
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

OWNER_ID            = os.getenv("DISCORD_OWNER_ID", "")
OWNER_ID            = int(OWNER_ID) if OWNER_ID.isdigit() else 0
_LOG_CHANNEL_ID     = int(os.getenv("TRINITY_LOG_CHANNEL_ID",     "0") or "0")
_THOUGHT_CHANNEL_ID = int(os.getenv("TRINITY_THOUGHT_CHANNEL_ID", "0") or "0")
_FEED_CHANNEL_ID    = int(os.getenv("TRINITY_FEED_CHANNEL_ID",    "0") or "0")
_HOME_GUILD_ID_ENV  = os.getenv("DISCORD_HOME_GUILD_ID", "")

# Webhook map: DISCORD_WEBHOOK_CHANNELNAME=url → {"channel-name": "url"}
# Used in thought_drain to bypass bot send_messages permission.
_WEBHOOKS: dict[str, str] = {
    k[len("DISCORD_WEBHOOK_"):].lower().replace("_", "-"): v
    for k, v in os.environ.items()
    if k.startswith("DISCORD_WEBHOOK_") and v
}

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

_watched_channels: set[int] = set()
_feed_seen_hashes: set      = set()  # populated on_ready, prevents backlog flood

# ─── Events ──────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    log.info(f"Online as {bot.user}")
    await _load_watched_channels()
    await _post_pending_alerts()
    if _HOME_GUILD_ID_ENV:
        profile = get_profile()
        if profile and str(profile.get("discord_home_guild_id", "")) != str(_HOME_GUILD_ID_ENV):
            supabase.table("profiles").update({
                "discord_home_guild_id": _HOME_GUILD_ID_ENV
            }).eq("id", profile["id"]).execute()
            log.info(f"Home guild synced from env: {_HOME_GUILD_ID_ENV}")
        elif profile:
            log.info(f"Home guild: {_HOME_GUILD_ID_ENV}")
    log.info("Tasks: thought_drain every 30s | feeds every 5min | heartbeat every 10min")
    thought_drain.start()
    heartbeat.start()
    # Seed seen feed hashes so first poll doesn't flood the channel with backlog
    global _feed_seen_hashes
    from brain.feeds import seed_seen as _seed_feeds, FEED_SOURCES as _DEFAULT_FEEDS
    from brain.memory import get_feeds as _get_feeds
    _seed_profile = get_profile()
    _db_feeds = _get_feeds(_seed_profile["id"]) if _seed_profile else []
    _seed_sources = [(f["name"], f["url"]) for f in _db_feeds] if _db_feeds else _DEFAULT_FEEDS
    _feed_seen_hashes = await asyncio.get_event_loop().run_in_executor(None, lambda: _seed_feeds(_seed_sources))
    log.info(f"[feeds] seeded {len(_feed_seen_hashes)} existing headlines ({len(_seed_sources)} source(s))")
    if _FEED_CHANNEL_ID:
        feed_channel = bot.get_channel(_FEED_CHANNEL_ID)
        if feed_channel:
            try:
                await feed_channel.send("◎ feed online")
                log.info(f"[feeds] channel confirmed: #{feed_channel.name}")
            except Exception as e:
                log.error(f"[feeds] channel unreachable: {e}")
        else:
            log.error(f"[feeds] channel ID {_FEED_CHANNEL_ID} not found — check TRINITY_FEED_CHANNEL_ID in .env")
        rss_feed.start()
        log.info("[feeds] RSS feed task started")
    else:
        log.info("[feeds] TRINITY_FEED_CHANNEL_ID not set — RSS feed disabled")
    log.info("Discord relay online — intelligence lives in the widget")


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    if message.channel.id in _watched_channels:
        await _ingest_signal(message)
        asyncio.create_task(_check_keyword_watches(message))

    is_dm      = isinstance(message.channel, discord.DMChannel)
    is_mention = bot.user in message.mentions
    if (is_dm or is_mention) and message.author.id == OWNER_ID:
        await message.reply("I'm home in the widget now. Open it there to talk.")


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

# ─── Keyword watch trigger ───────────────────────────────────────────────────

async def _check_keyword_watches(message: discord.Message):
    """If a message in a watched channel matches a keyword watch, queue a self-thought for the widget to pick up."""
    profile = get_profile()
    if not profile:
        return
    from brain.memory import get_watches as _get_watches
    watches = _get_watches(profile["id"])
    if not watches:
        return
    text    = message.content.lower()
    matched = [w for w in watches if w["keyword"].lower() in text]
    if not matched:
        return
    kws       = ", ".join(w["keyword"] for w in matched)
    guild_name = message.guild.name if message.guild else "DM"
    chan_name  = getattr(message.channel, "name", "unknown")
    log.info(f"[watches] keyword match: {kws} in #{chan_name} — queuing self-thought")
    why_lines = " / ".join(
        f"'{w['keyword']}'" + (f" — {w['note']}" if w.get("note") else "") for w in matched
    )
    note = (
        f"[WATCH TRIGGERED] keyword{'s' if len(matched) > 1 else ''}: {kws}\n"
        f"Source: #{chan_name} in {guild_name} | Author: {message.author.display_name}\n"
        f"Message: {message.content[:300]}\n"
        f"You set a watch for: {why_lines}"
    )
    queue_self_thought(profile["id"], note, priority=2, source="discord_watch")


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


async def _read_history(channel, limit):
    """Fetch channel history with one retry on transient 403."""
    for attempt in range(2):
        try:
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
            empty = sum(1 for m in msgs if not m["content"].strip())
            if msgs:
                log.info(f"read #{channel.name}: {len(msgs)} messages, {empty} with empty content")
            return msgs
        except discord.Forbidden as e:
            if attempt == 0:
                log.warn(f"read #{channel.name} 403 (attempt 1), retrying in 2s — code={e.code} full='{e}'")
                await asyncio.sleep(2)
            else:
                log.error(f"read #{channel.name} 403 (attempt 2, giving up) — code={e.code} full='{e}'")
                return {"error": f"403 {e}"}
        except Exception as e:
            log.error(f"read #{channel.name} error: {e}")
            return {"error": str(e)}

async def _home_guild() -> discord.Guild | None:
    profile = get_profile()
    if not profile or not profile.get("discord_home_guild_id"):
        log.warn("_home_guild: no guild ID in profile")
        return None
    guild_id = int(profile["discord_home_guild_id"])
    guild = bot.get_guild(guild_id)
    if not guild:
        try:
            guild = await bot.fetch_guild(guild_id)
            log.info(f"_home_guild: cache miss — fetched guild via API: {guild.name}")
        except Exception as e:
            log.error(f"_home_guild: fetch_guild failed: {e}")
            return None
    return guild

# ─── Thought drain — routes widget <thought> tags to Discord palace ───────────

@tasks.loop(seconds=30)
async def thought_drain():
    profile = get_profile()
    if not profile:
        return
    writes = pop_discord_writes(profile["id"])
    if not writes:
        return
    for w in writes:
        channel_name = w.get("channel_name")
        if channel_name:
            # Webhook first — bypasses bot send_messages permission
            wh_key = channel_name.lower().replace(" ", "-").replace("_", "-")
            wh_url = _WEBHOOKS.get(wh_key) or next(
                (url for key, url in _WEBHOOKS.items() if key.replace("-", "") == wh_key.replace("-", "")),
                None
            )
            if wh_url:
                try:
                    import aiohttp as _aio
                    async with _aio.ClientSession() as _s:
                        wh = discord.Webhook.from_url(wh_url, session=_s)
                        await wh.send(w["content"][:2000])
                    log.info(f"thought_drain webhook → #{channel_name}")
                    await asyncio.sleep(0.3)
                    continue
                except Exception as e:
                    log.warning(f"thought_drain webhook failed for #{channel_name}: {e}")
            # Fall back: route to named channel in home guild via bot
            try:
                guild = bot.guilds[0] if bot.guilds else None
                if guild:
                    query = channel_name.lower().replace("-", "").replace("_", "").replace(" ", "")
                    ch = next(
                        (c for c in guild.channels
                         if isinstance(c, discord.TextChannel) and query in c.name.lower().replace("-", "").replace("_", "")),
                        None
                    )
                    if ch:
                        await ch.send(w["content"])
                        await asyncio.sleep(0.5)
                        continue
            except Exception as e:
                log.error(f"thought_drain channel lookup failed for '{channel_name}': {e}")
        # Fall back to thought channel
        if not _THOUGHT_CHANNEL_ID:
            continue
        channel = bot.get_channel(_THOUGHT_CHANNEL_ID)
        if not channel:
            continue
        try:
            await channel.send(w["content"])
        except discord.Forbidden as e:
            log.error(f"thought_drain 403 on #{channel.name}: {e.text}")
            break
        except Exception as e:
            log.error(f"thought_drain send error: {e}")
            break
        await asyncio.sleep(0.5)

@thought_drain.before_loop
async def before_thought_drain():
    await bot.wait_until_ready()


# ─── RSS headline feed ───────────────────────────────────────────────────────

@tasks.loop(minutes=5)
async def rss_feed():
    if not _FEED_CHANNEL_ID:
        return
    channel = bot.get_channel(_FEED_CHANNEL_ID)
    if not channel:
        return
    try:
        from brain.feeds import fetch_new_items, format_headline, FEED_SOURCES as _DEFAULT_FEEDS
        from brain.memory import get_feeds as _get_feeds
        profile = get_profile()
        db_feeds = _get_feeds(profile["id"]) if profile else []
        sources  = [(f["name"], f["url"]) for f in db_feeds] if db_feeds else _DEFAULT_FEEDS
        new_items = await asyncio.get_event_loop().run_in_executor(
            None, lambda: fetch_new_items(_feed_seen_hashes, sources)
        )
        if not new_items:
            return
        for item in new_items:
            _feed_seen_hashes.add(item["hash"])
            try:
                await channel.send(format_headline(item))
                await asyncio.sleep(0.5)
            except Exception as e:
                log.warn(f"[feeds] failed to post: {e}")
        log.info(f"[feeds] posted {len(new_items)} new headline{'s' if len(new_items) != 1 else ''}")
    except Exception as e:
        log.error(f"[feeds] {e}")

@rss_feed.before_loop
async def before_rss_feed():
    await bot.wait_until_ready()


# ─── Heartbeat ────────────────────────────────────────────────────────────────

@tasks.loop(minutes=10)
async def heartbeat():
    log.info("◎ alive")

@heartbeat.before_loop
async def before_heartbeat():
    await bot.wait_until_ready()







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


# ─── Entry point ─────────────────────────────────────────────────────────────

def run():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("[Discord] No DISCORD_BOT_TOKEN in .env — skipping.")
        return
    bot.run(token)


if __name__ == "__main__":
    run()
