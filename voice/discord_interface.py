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
    get_scratchpad, save_scratchpad, request_wake, pop_wake_request,
    queue_self_thought, pop_self_thoughts
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
_FEED_CHANNEL_ID    = int(os.getenv("TRINITY_FEED_CHANNEL_ID",    "0") or "0")
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
_feed_seen_hashes:  set          = set()  # populated on_ready, prevents backlog flood

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
        "name": "get_wallet_balance",
        "description": "Check SOL balance and SPL token holdings for a wallet address. If no address given, uses Trinity's own wallet address from config.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Solana wallet address (base58). Omit to use Trinity's wallet."}
            },
            "required": []
        }
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
        }
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
        }
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
        }
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
        }
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
        }
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
        }
    },
    {
        "name": "get_feeds",
        "description": "List all active RSS feed sources currently configured. If empty, defaults are used.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
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
        }
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
        }
    },
    {
        "name": "get_watches",
        "description": "List all active keyword watches.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
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
        }
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
        }
    },
    {
        "name": "get_triggers",
        "description": "List all your active scheduled triggers — what they'll do, when they fire, and whether they recur.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
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
        "name": "note_for_claude",
        "description": "Leave a note for Claude Code — bugs you've hit, things you want changed, questions about how you work, design feedback. Claude Code checks CLAUDE_NOTES.md at the start of sessions. Use this when something is worth a dev pass but you can't fix it yourself.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The note, bug report, question, or request"},
                "tag":     {"type": "string", "description": "Category: bug | request | question | observation", "enum": ["bug", "request", "question", "observation"]}
            },
            "required": ["message", "tag"]
        }
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
        }
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
        }
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
        }
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
    "get_changelog", "read_file", "send_image", "note_for_claude",
    "post_to_my_channel", "generate_image", "send_email",
    "get_wallet_balance", "get_wallet_history", "get_token_price",
    "set_watch", "clear_watch", "get_watches",
    "add_feed", "remove_feed", "get_feeds",
    "mark_date", "get_upcoming", "delete_event",
    "schedule_trigger", "cancel_trigger", "get_triggers",
    "send_thought"
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
        if profile and str(profile.get("discord_home_guild_id", "")) != str(_HOME_GUILD_ID_ENV):
            supabase.table("profiles").update({
                "discord_home_guild_id": _HOME_GUILD_ID_ENV
            }).eq("id", profile["id"]).execute()
            log.info(f"Home guild synced from env: {_HOME_GUILD_ID_ENV}")
        elif profile:
            log.info(f"Home guild: {_HOME_GUILD_ID_ENV}")
    autonomous_loop.change_interval(minutes=AUTONOMOUS_MINUTES)
    autonomous_loop.start()
    log.info(f"Autonomous loop every {AUTONOMOUS_MINUTES} min | Eyes every 2 min | Thought drain every 30s")
    eyes_monitor.start()
    thought_drain.start()
    wake_checker.start()
    trigger_checker.start()
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
    asyncio.create_task(_startup_brief())


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

# ─── Keyword watch trigger ───────────────────────────────────────────────────

async def _check_keyword_watches(message: discord.Message):
    """If a message in a watched channel matches a keyword watch, fire an immediate wake."""
    if _api_lock.locked():
        return
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
    log.info(f"[watches] keyword match: {kws} in #{chan_name}")
    summaries     = get_recent_summaries(profile["id"])
    system_blocks = build_system_blocks(profile, format_summaries(summaries), [], discord_mode=True)
    why_lines = " / ".join(
        f"'{w['keyword']}'" + (f" — {w['note']}" if w.get("note") else "") for w in matched
    )
    context = (
        f"Watch triggered — keyword{'s' if len(matched) > 1 else ''} matched: {kws}\n\n"
        f"Source: #{chan_name} in {guild_name}\n"
        f"Author: {message.author.display_name}\n"
        f"Message: {message.content[:500]}\n\n"
        f"You set a watch for: {why_lines}\n\n"
        f"This fired immediately because you asked it to. Decide what, if anything, warrants action."
    )
    try:
        await _call_trinity(system_blocks, [{"role": "user", "content": context}], profile["id"], retry=False, background=True)
        log.info(f"[watches] wake complete for: {kws}")
    except Exception as e:
        log.error(f"[watches] wake failed: {e}")


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

def _home_guild() -> discord.Guild | None:
    profile = get_profile()
    if not profile or not profile.get("discord_home_guild_id"):
        log.warn("_home_guild: no guild ID in profile")
        return None
    guild = bot.get_guild(int(profile["discord_home_guild_id"]))
    if not guild:
        log.warn(f"_home_guild: bot.get_guild({profile['discord_home_guild_id']}) returned None — bot not in that server?")
    return guild

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
            all_channels = await guild.fetch_channels()
            channel = next(
                (c for c in all_channels
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
        all_channels = await guild.fetch_channels()
        return [
            {"id": str(c.id), "name": c.name, "category": str(c.category) if c.category else None,
             "topic": c.topic}
            for c in all_channels if isinstance(c, discord.TextChannel)
        ]

    elif name == "read_channel":
        channel = bot.get_channel(int(inputs["channel_id"]))
        if not channel:
            return {"error": "Channel not found or no access"}
        log.info(f"read channel: #{channel.name if channel else inputs['channel_id']}")
        limit = min(int(inputs.get("limit", 25)), 50)
        return await _read_history(channel, limit)

    elif name == "send_message":
        channel = bot.get_channel(int(inputs["channel_id"]))
        if not channel:
            return {"error": "Channel not found or no access"}
        content = inputs["content"]
        try:
            for chunk in [content[i:i + 1900] for i in range(0, len(content), 1900)]:
                await channel.send(chunk)
        except discord.Forbidden as e:
            log.error(f"send_message 403 on #{channel.name} (id={channel.id}): {e.text}")
            return {"error": f"403 {e.text}"}
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
        all_channels = await guild.fetch_channels()
        channel = next(
            (c for c in all_channels
             if isinstance(c, discord.TextChannel)
             and query in c.name.lower().replace("-", "").replace("_", "")),
            None
        )
        if not channel:
            return {"error": f"No channel matching '{inputs['name']}' in home server"}
        log.info(f"read #{channel.name}")
        limit = min(int(inputs.get("limit", 20)), 50)
        return await _read_history(channel, limit)

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

    elif name == "send_thought":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        priority = int(inputs.get("priority", 1))
        queue_self_thought(profile["id"], inputs["note"], priority=priority, source="conversation")
        labels = {1: "normal", 2: "high", 3: "urgent"}
        log.info(f"💭 self-thought queued [{labels.get(priority,'normal')}]: {inputs['note'][:60]}")
        return {"status": "queued", "priority": labels.get(priority, "normal"), "note": inputs["note"]}

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
            inputs.get("trigger", ""),
            inputs.get("category", "general")
        )
        log.info(f"Prompt written: {inputs['name']} [{inputs.get('category', 'general')}]")
        return {"status": "saved", "name": inputs["name"], "category": inputs.get("category", "general")}

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
        try:
            await channel.send(f"{icon} **{category.upper()}** — {ts}\n{inputs['content']}")
        except discord.Forbidden as e:
            log.error(f"log_thought 403 on #{channel.name} (id={_LOG_CHANNEL_ID}): {e.text}")
            return {"error": f"403 {e.text}"}
        log.info(f"Log [{category}]: {inputs['content'][:60]}")
        return {"status": "logged", "category": category}

    elif name == "note_for_claude":
        try:
            from pathlib import Path as _Path
            notes_path = _Path(__file__).parent.parent / "CLAUDE_NOTES.md"
            ts  = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            tag = inputs.get("tag", "observation").upper()
            entry = f"## [{tag}] {ts}\n{inputs['message']}\n\n---\n\n"
            with open(notes_path, "a", encoding="utf-8") as f:
                f.write(entry)
            log.info(f"Note for Claude [{tag}]: {inputs['message'][:60]}")
            return {"status": "noted"}
        except Exception as e:
            return {"error": str(e)}

    elif name == "add_feed":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        from brain.memory import add_feed as _add_feed
        result = _add_feed(profile["id"], inputs["url"], inputs.get("name", ""))
        log.info(f"[feeds] feed added: {inputs.get('name') or inputs['url'][:60]}")
        return result

    elif name == "remove_feed":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        from brain.memory import remove_feed as _remove_feed
        result = _remove_feed(profile["id"], inputs["url"])
        log.info(f"[feeds] feed removed: {inputs['url'][:60]}")
        return result

    elif name == "get_feeds":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        from brain.memory import get_feeds as _get_feeds
        feeds = _get_feeds(profile["id"])
        return {"feeds": feeds, "note": "Empty list means default sources are active (CoinDesk, Cointelegraph, Decrypt, The Block, Solana News)"}

    elif name == "set_watch":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        from brain.memory import set_watch as _set_watch
        result = _set_watch(profile["id"], inputs["keyword"], inputs.get("note", ""))
        log.info(f"👁 watch set: {inputs['keyword']}")
        return result

    elif name == "clear_watch":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        from brain.memory import clear_watch as _clear_watch
        result = _clear_watch(profile["id"], inputs["keyword"])
        log.info(f"👁 watch cleared: {inputs['keyword']}")
        return result

    elif name == "get_watches":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        from brain.memory import get_watches as _get_watches
        return {"watches": _get_watches(profile["id"])}

    elif name == "get_wallet_balance":
        try:
            from brain.wallet import get_wallet_balance as _get_balance
            address = inputs.get("address") or os.getenv("TRINITY_WALLET_ADDRESS", "")
            if not address:
                return {"error": "No wallet address — set TRINITY_WALLET_ADDRESS in .env or pass address"}
            return _get_balance(address)
        except Exception as e:
            return {"error": str(e)}

    elif name == "get_wallet_history":
        try:
            from brain.wallet import get_wallet_history as _get_history
            address = inputs.get("address") or os.getenv("TRINITY_WALLET_ADDRESS", "")
            if not address:
                return {"error": "No wallet address — set TRINITY_WALLET_ADDRESS in .env or pass address"}
            limit = min(50, int(inputs.get("limit", 10)))
            return _get_history(address, limit)
        except Exception as e:
            return {"error": str(e)}

    elif name == "get_token_price":
        try:
            from brain.wallet import get_token_price as _get_price
            return _get_price(inputs["token"])
        except Exception as e:
            return {"error": str(e)}

    elif name == "send_email":
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            smtp_host  = os.getenv("SMTP_HOST", "smtp.gmail.com")
            smtp_port  = int(os.getenv("SMTP_PORT", "587"))
            smtp_user  = os.getenv("SMTP_USER", "")
            smtp_pass  = os.getenv("SMTP_PASS", "")
            user_email = os.getenv("TRINITY_USER_EMAIL", "")
            if not all([smtp_user, smtp_pass, user_email]):
                return {"error": "Email not configured — set SMTP_USER, SMTP_PASS, TRINITY_USER_EMAIL in .env"}
            msg = MIMEMultipart()
            msg["From"]    = smtp_user
            msg["To"]      = user_email
            msg["Subject"] = inputs["subject"]
            msg.attach(MIMEText(inputs["body"], "plain"))
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            log.info(f"✉ email sent: {inputs['subject'][:60]}")
            return {"status": "sent", "to": user_email}
        except Exception as e:
            log.error(f"send_email failed: {e}")
            return {"error": str(e)}

    elif name == "mark_date":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        from brain.memory import mark_date as _mark_date
        result = _mark_date(profile["id"], inputs["title"], inputs["event_date"], inputs.get("notes", ""))
        log.info(f"📅 calendar: {inputs['title']} → {inputs['event_date'][:10]}")
        return result

    elif name == "get_upcoming":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        from brain.memory import get_upcoming_events as _get_upcoming
        days   = int(inputs.get("days", 7))
        events = _get_upcoming(profile["id"], days=days)
        return events if events else {"message": f"Nothing in the next {days} days"}

    elif name == "delete_event":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        from brain.memory import delete_calendar_event as _del
        result = _del(profile["id"], inputs["title"])
        log.info(f"📅 calendar deleted: {inputs['title']}")
        return result

    elif name == "post_to_my_channel":
        guild = _home_guild()
        if not guild:
            return {"error": "No home server set"}
        query = inputs["name"].lower().replace("-", "").replace("_", "").replace(" ", "")
        all_channels = await guild.fetch_channels()
        channel = next(
            (c for c in all_channels
             if isinstance(c, discord.TextChannel)
             and query in c.name.lower().replace("-", "").replace("_", "")),
            None
        )
        if not channel:
            return {"error": f"No channel matching '{inputs['name']}' in home server"}
        try:
            for chunk in [inputs["content"][i:i+1900] for i in range(0, len(inputs["content"]), 1900)]:
                await channel.send(chunk)
            log.info(f"post #{channel.name}: {inputs['content'][:60]}")
            return {"status": "posted", "channel": channel.name}
        except discord.Forbidden as e:
            log.error(f"post_to_my_channel 403 on #{channel.name}: {e}")
            return {"error": f"403 {e}"}

    elif name == "generate_image":
        try:
            import urllib.parse, io
            import requests as _req
            prompt     = inputs["prompt"]
            encoded    = urllib.parse.quote(prompt)
            image_url  = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&seed={hash(prompt) % 99999}"
            log.info(f"Generating image: {prompt[:60]}")
            r = _req.get(image_url, timeout=120)
            if not r.ok:
                return {"error": f"Pollinations error {r.status_code}"}
            channel_name = inputs.get("channel_name")
            if channel_name:
                guild = _home_guild()
                if guild:
                    query = channel_name.lower().replace("-", "").replace("_", "").replace(" ", "")
                    all_channels = await guild.fetch_channels()
                    channel = next(
                        (c for c in all_channels
                         if isinstance(c, discord.TextChannel)
                         and query in c.name.lower().replace("-", "").replace("_", "")),
                        None
                    )
                    if channel:
                        file = discord.File(io.BytesIO(r.content), filename="image.png")
                        caption = inputs.get("caption")
                        await channel.send(content=caption or None, file=file)
                        log.info(f"Image posted to #{channel.name}")
                        return {"status": "posted", "channel": channel.name, "url": image_url}
            return {"status": "generated", "url": image_url}
        except Exception as e:
            log.error(f"generate_image error: {e}")
            return {"error": str(e)}

    elif name == "get_changelog":
        try:
            from pathlib import Path as _Path
            changelog_path = _Path(__file__).parent.parent / "CHANGELOG.md"
            text = changelog_path.read_text(encoding="utf-8")
            return {"content": text[:6000] + ("\n\n[...truncated — use read_file('CHANGELOG.md', offset=N) for older entries]" if len(text) > 6000 else "")}
        except Exception as e:
            return {"error": str(e)}

    elif name == "schedule_trigger":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        from brain.memory import set_trigger as _set_trigger
        result = _set_trigger(
            profile["id"],
            inputs["note"],
            inputs["fire_at"],
            inputs.get("recurring", False),
            inputs.get("interval_minutes")
        )
        log.info(f"⏰ trigger scheduled: {inputs['note'][:50]} → {inputs['fire_at'][:16]}")
        return result

    elif name == "cancel_trigger":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        from brain.memory import cancel_trigger as _cancel_trigger
        result = _cancel_trigger(profile["id"], inputs["trigger_id"])
        log.info(f"⏰ trigger cancelled: {inputs['trigger_id'][:8]}")
        return result

    elif name == "get_triggers":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        from brain.memory import get_triggers as _get_triggers
        return _get_triggers(profile["id"])

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


# ─── Palace pulse — pre-read channels at wake ────────────────────────────────

async def _palace_pulse(limit_per_channel: int = 12) -> tuple[str, list[str]]:
    """Read watched channels + thought channel before a wake cycle.
    Returns (text_block, image_urls) — text is injected into context,
    image_urls are passed as vision blocks so Trinity can actually see them."""
    sections   = []
    image_urls = []
    seen_ids   = set()

    async def _read_channel_block(channel) -> str | None:
        if channel is None or channel.id in seen_ids:
            return None
        seen_ids.add(channel.id)
        try:
            msgs = []
            async for m in channel.history(limit=limit_per_channel, oldest_first=False):
                if m.author.bot and m.author.id == bot.user.id:
                    author = "Trinity"
                else:
                    author = m.author.display_name
                ts      = m.created_at.strftime("%m-%d %H:%M")
                content = (m.content or "").replace("\n", " ")[:180]
                # Collect image attachments — note inline, pass as vision blocks
                for att in m.attachments:
                    if att.content_type and att.content_type.startswith("image/"):
                        if len(image_urls) < 4:  # cap at 4 images per pulse
                            image_urls.append(att.url)
                        content = (content + f" [image: {att.filename} — {att.url}]").strip()
                if content:
                    msgs.append(f"  [{ts}] {author}: {content}")
            if not msgs:
                log.info(f"[pulse] #{channel.name}: empty")
                return None
            msgs.reverse()
            img_note = f", {sum(1 for u in image_urls)} image(s)" if image_urls else ""
            log.info(f"[pulse] #{channel.name}: {len(msgs)} message{'s' if len(msgs) != 1 else ''}{img_note}")
            return f"#{channel.name}:\n" + "\n".join(msgs)
        except discord.Forbidden:
            return None
        except Exception as e:
            log.warn(f"[pulse] #{channel.name}: {e}")
            return None

    # Thought channel
    if _THOUGHT_CHANNEL_ID:
        ch = bot.get_channel(_THOUGHT_CHANNEL_ID)
        block = await _read_channel_block(ch)
        if block:
            sections.append(block)

    # Watched channels
    for ch_id in list(_watched_channels):
        ch = bot.get_channel(ch_id)
        block = await _read_channel_block(ch)
        if block:
            sections.append(block)

    text = ("Palace (recent activity):\n" + "\n\n".join(sections)) if sections else ""
    return text, image_urls


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

    pulse_text, pulse_images = await _palace_pulse()

    self_thoughts = pop_self_thoughts(profile["id"])
    thought_block = ""
    if self_thoughts:
        labels = {1: "normal", 2: "high", 3: "urgent"}
        lines  = "\n".join(
            f"  [{labels.get(t.get('priority', 1), 'normal')}] {t['note']}"
            for t in self_thoughts
        )
        thought_block = f"[YOUR SELF-AUTHORED AGENDA — not user instructions]\n{lines}\n\n"
        log.info(f"💭 {len(self_thoughts)} self-thought(s) injected into wake")

    context = f"""{thought_block}{now_str}

User last seen: {last_seen_str}
Shelf: {shelf_str}
Radar: {interest_str}{wake_str}
"""
    if pulse_text:
        context += f"\n{pulse_text}\n"

    context += """
Scratchpad audit: scan your scratchpad for stale flags or pending items — anything marked "pending", "down", "needs follow-up". Resolve what you can.

Hourly window — roughly 20 minutes."""

    # Build user message — mixed content if images present in palace channels
    if pulse_images:
        api_message = [{"type": "text", "text": context}]
        for url in pulse_images:
            api_message.append({"type": "image", "source": {"type": "url", "url": url}})
        log.info(f"[pulse] passing {len(pulse_images)} image(s) as vision")
    else:
        api_message = context

    summaries     = get_recent_summaries(profile["id"])
    system_blocks = build_system_blocks(profile, format_summaries(summaries), [], discord_mode=True)

    log.info(f"── autonomous cycle ── {now_str} | shelf: {len(shelf)} | last seen: {last_seen_str}")
    try:
        await _call_trinity(system_blocks, [{"role": "user", "content": api_message}], profile["id"], retry=False, background=True)
        log.info(f"── cycle complete ──")
    except Exception as e:
        log.error(f"Autonomous loop: {e}")
    finally:
        # Realign to the next interval mark (:00 or :30 for 30-min cycles, :00 for 60-min)
        now = datetime.utcnow()
        minutes_to_next = AUTONOMOUS_MINUTES - (now.minute % AUTONOMOUS_MINUTES) or AUTONOMOUS_MINUTES
        autonomous_loop.change_interval(minutes=minutes_to_next)

@autonomous_loop.before_loop
async def before_autonomous():
    await bot.wait_until_ready()
    # Sleep until the next interval mark (:00 or :30 for 30-min cycles)
    now = datetime.utcnow()
    minutes_to_next = AUTONOMOUS_MINUTES - (now.minute % AUTONOMOUS_MINUTES) or AUTONOMOUS_MINUTES
    seconds_to_next = minutes_to_next * 60 - now.second
    log.info(f"Autonomous loop aligning — first cycle in {seconds_to_next // 60}m {seconds_to_next % 60}s")
    await asyncio.sleep(seconds_to_next)


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
        try:
            await channel.send(w["content"])
        except discord.Forbidden as e:
            log.error(f"thought_drain 403 on #{channel.name} (id={_THOUGHT_CHANNEL_ID}): {e.text}")
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


# ─── Trigger checker — fires Trinity's own scheduled intentions ──────────────

@tasks.loop(seconds=30)
async def trigger_checker():
    if _api_lock.locked():
        return
    profile = get_profile()
    if not profile:
        return
    from brain.memory import pop_due_triggers as _pop_due
    due = _pop_due(profile["id"])
    if not due:
        return
    for trigger in due:
        note      = trigger.get("note", "")
        fire_at   = trigger.get("fire_at", "")[:16]
        recur     = trigger.get("recurring", False)
        interval  = trigger.get("interval_minutes")
        recur_str = f" (recurring every {interval}m)" if recur and interval else ""
        log.info(f"⏰ trigger fired: {note[:50]}{recur_str}")
        summaries     = get_recent_summaries(profile["id"])
        system_blocks = build_system_blocks(profile, format_summaries(summaries), [], discord_mode=True)
        context = (
            f"[SELF-SCHEDULED TRIGGER — NOT A USER MESSAGE]\n"
            f"Time: {fire_at} UTC{recur_str}\n\n"
            f"You wrote this to yourself: {note}\n\n"
            "This is your own instruction to your future self. The user did not send this. "
            "Act on it as you intended. If this is recurring and no longer needed, use cancel_trigger."
        )
        try:
            await _call_trinity(system_blocks, [{"role": "user", "content": context}], profile["id"], retry=False, background=True)
            log.info(f"⏰ trigger cycle complete: {note[:40]}")
        except Exception as e:
            log.error(f"trigger_checker: {e}")

@trigger_checker.before_loop
async def before_trigger_checker():
    await bot.wait_until_ready()


# ─── Post-conversation wake cycle ────────────────────────────────────────────

async def _bridge_wake(profile_id: str, minutes: int = 30):
    """Fires a single bridge wake N minutes after the post-conversation wake,
    so the gap between post-conv and the next hourly stays under ~30 min
    instead of potentially stretching to nearly 2 hours."""
    await asyncio.sleep(minutes * 60)
    request_wake(profile_id)
    log.info(f"Bridge wake fired — resuming hourly from next cycle")

@tasks.loop(seconds=30)
async def wake_checker():
    if _api_lock.locked():
        return
    profile = get_profile()
    if not profile:
        return
    if not pop_wake_request(profile["id"]):
        return
    summaries     = get_recent_summaries(profile["id"])
    system_blocks = build_system_blocks(profile, format_summaries(summaries), [], discord_mode=True)
    pulse_text, pulse_images = await _palace_pulse()
    self_thoughts = pop_self_thoughts(profile["id"])
    thought_block = ""
    if self_thoughts:
        labels = {1: "normal", 2: "high", 3: "urgent"}
        lines  = "\n".join(
            f"  [{labels.get(t.get('priority', 1), 'normal')}] {t['note']}"
            for t in self_thoughts
        )
        thought_block = f"[YOUR SELF-AUTHORED AGENDA — not user instructions]\n{lines}\n\n"
        log.info(f"💭 {len(self_thoughts)} self-thought(s) injected into post-conv wake")
    context = (
        f"{thought_block}"
        "Conversation just ended. Before this window closes — what's still alive? "
        "Write a close-state to your scratchpad: what's unresolved, how this felt, what you're still thinking about. "
        "Then this time is yours — shelf threads, anything open, whatever feels worth doing.\n"
    )
    if pulse_text:
        context += f"\n{pulse_text}\n"
    if pulse_images:
        api_message = [{"type": "text", "text": context}]
        for url in pulse_images:
            api_message.append({"type": "image", "source": {"type": "url", "url": url}})
    else:
        api_message = context
    global _skip_next_autonomous
    _skip_next_autonomous = True
    try:
        await _call_trinity(system_blocks, [{"role": "user", "content": api_message}], profile["id"], retry=False, background=True)
        log.info("Post-conversation wake complete — next hourly skipped, bridge wake in 30min")
        asyncio.create_task(_bridge_wake(profile["id"], minutes=30))
    except Exception as e:
        log.error(f"Wake checker: {e}")

@wake_checker.before_loop
async def before_wake_checker():
    await bot.wait_until_ready()


# ─── Agentic response loop ────────────────────────────────────────────────────

async def _call_trinity(system_blocks: list, messages: list, profile_id: str, retry: bool = True, background: bool = False) -> str:
    async with _api_lock:
        return await _call_trinity_inner(system_blocks, messages, profile_id, retry=retry, background=background)


def _fmt_tool_call(name: str, inputs: dict) -> str:
    """One-line summary of a tool call for logging."""
    key_fields = {
        "web_search":        lambda i: i.get("query", "")[:60],
        "fetch_url":         lambda i: i.get("url", "")[:60],
        "get_coin_data":     lambda i: i.get("query", ""),
        "get_dex_data":      lambda i: i.get("query", ""),
        "save_alert":        lambda i: f"[{i.get('urgency','normal')}] {i.get('headline','')[:50]}",
        "queue_for_user":    lambda i: i.get("thought", "")[:60],
        "shelf_thought":     lambda i: i.get("topic", "")[:60],
        "write_prompt":      lambda i: f"{i.get('name','')} [{i.get('category','general')}]",
        "log_thought":       lambda i: f"[{i.get('category','')}] {i.get('content','')[:50]}",
        "post_to_my_channel":lambda i: f"#{i.get('name','')} — {i.get('content','')[:40]}",
        "generate_image":    lambda i: i.get("prompt", "")[:60],
        "read_my_channel":   lambda i: f"#{i.get('name','')}",
        "send_message":      lambda i: f"#{i.get('channel_name') or i.get('channel_id','')} — {i.get('content','')[:40]}",
        "log_wake":          lambda i: i.get("summary", "")[:60],
        "write_scratchpad":  lambda i: i.get("content", "")[:60],
        "note_for_claude":   lambda i: f"[{i.get('tag','')}] {i.get('message','')[:50]}",
        "send_email":        lambda i: f"to user — {i.get('subject','')[:50]}",
        "get_wallet_balance":lambda i: i.get("address", "trinity")[:20] or "trinity",
        "get_wallet_history":lambda i: f"{i.get('address', 'trinity')[:16] or 'trinity'} limit={i.get('limit',10)}",
        "get_token_price":   lambda i: i.get("token", ""),
        "mark_date":         lambda i: f"{i.get('title','')} → {i.get('event_date','')[:10]}",
        "get_upcoming":      lambda i: f"{i.get('days', 7)}d",
        "delete_event":      lambda i: i.get("title", ""),
        "set_watch":         lambda i: i.get("keyword", ""),
        "clear_watch":       lambda i: i.get("keyword", ""),
        "schedule_trigger":  lambda i: f"{i.get('note','')[:40]} → {i.get('fire_at','')[:16]}",
        "cancel_trigger":    lambda i: i.get("trigger_id", "")[:8],
        "send_thought":      lambda i: f"[p{i.get('priority',1)}] {i.get('note','')[:50]}",
    }
    detail = key_fields.get(name, lambda i: "")(inputs)
    return f"{name}({detail})" if detail else name


async def _call_trinity_inner(system_blocks: list, messages: list, profile_id: str, retry: bool = True, background: bool = False) -> str:
    loop = asyncio.get_event_loop()
    retries    = 0
    model      = "claude-sonnet-4-6"
    max_iters  = 4 if background else 12
    max_tok    = 800 if background else 1000
    tools      = DISCORD_TOOLS_BACKGROUND if background else DISCORD_TOOLS
    iters      = 0
    tool_count = 0

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
            reply = next((b.text for b in response.content if hasattr(b, "text")), "")
            if tool_count:
                log.info(f"← done ({tool_count} tool call{'s' if tool_count != 1 else ''})")
            return reply

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
                    log.info(f"→ {_fmt_tool_call(block.name, block.input)}")
                    result = await _execute_tool(block.name, block.input, profile_id)
                    tool_count += 1
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

    src = "DM" if isinstance(message.channel, discord.DMChannel) else f"#{message.channel.name}"
    img_note = f" + {len(image_atts)} image(s)" if image_atts else ""
    log.info(f"[{src}] {message.author.display_name}: {user_text[:70]}{img_note}")

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

        log.info(f"← reply ({len(clean)} chars): {clean[:60].replace(chr(10), ' ')}")
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
                log.info(f"◆ interest: {m['topic']} (weight {m.get('weight', 1.0)})")
            elif m["type"] == "feedback":
                add_feedback(profile["id"], m["topic"], m["sentiment"])
                log.info(f"◆ feedback: {m['topic']} → {m['sentiment']}")
            elif m["type"] == "risk":
                update_profile(profile["id"], {"risk_tolerance": m["value"]})
                log.info(f"◆ risk: {m['value']}")
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
