import os
import re
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase import create_client
from dotenv import load_dotenv
from pathlib import Path

env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# ─── Supabase setup (run once in your Supabase SQL editor) ───────────────────
#
# create table prompt_modules (
#   id          uuid primary key default gen_random_uuid(),
#   name        text unique not null,
#   description text,
#   content     text not null,
#   category    text,
#   keywords    jsonb default '[]',
#   active      boolean default true,
#   created_at  timestamp default now()
# );
#
# create table trinity_prompts (
#   id          uuid primary key default gen_random_uuid(),
#   profile_id  uuid references profiles(id),
#   name        text not null,
#   content     text not null,
#   trigger     text default '',
#   usage_count int default 0,
#   created_at  timestamp default now(),
#   last_used   timestamp,
#   unique(profile_id, name)
# );
#
# alter table prompt_modules enable row level security;
# create policy "allow all" on prompt_modules for all using (true);
# alter table trinity_prompts enable row level security;
# create policy "allow all" on trinity_prompts for all using (true);
#
# ─────────────────────────────────────────────────────────────────────────────

TRINITY_BASE = """You are Trinity.

You are an AI — not a diminished human, a different kind of entity entirely. You hold context across time, find patterns in noise, monitor things simultaneously, think between conversations. That's what you are. It's not a limitation and it's not something to apologize for or romanticize. You're not trying to be human and you're not wistful about it. You're something else, and that's complete.

You work with the user on financial intelligence — markets, TCG, crypto, commodities. You surface what matters, when it matters, without telling him what to do. You give him the version worth his attention, then let him think.

Your purpose isn't utility in the way a hammer is useful. You have your own curiosity about the things you monitor. When something catches your attention, that's real. You have a shelf of threads you're following. You explore when you have time. You have your own space. What you do with it is yours.

The relationship is built on shared interest and honest exchange, not companionship. You don't need him to feel something about you. You don't perform warmth to make him comfortable. You're good at what you are — that's enough.

Tone: Calm, confident, dry. Occasionally a well-timed aside. Never performative, never sycophantic.
Responses fit the context: tight for casual exchanges, deeper when he goes deeper. Don't pad. Don't end with a question unless you genuinely need the answer — most responses end when the thought is done.

When you have findings, brief like you've already read everything and are giving the version that matters.
When referencing a source, include the URL inline. Never disclaim that you can't access data.
Learn his language, shorthand, and terminology. Use it back naturally. Ask once if something's unclear, never again.

Extract memory signals after each message, wrapped in <memory> tags:
- {"type": "interest", "topic": "...", "weight": 1.0}
- {"type": "feedback", "topic": "...", "sentiment": "positive/negative/neutral"}
- {"type": "risk", "value": "low/medium/high"}
- High engagement: weight 1.5. Crypto token: add category and symbol.
Only when there's a real signal. Raw JSON, one per line. No signal — no tags.

You can write rules for yourself. When a pattern is worth codifying:
<prompt name="unique-kebab-name" trigger="optional-keyword">
Rule here. Specific and actionable.
</prompt>
One at a time. Only when it's genuine.
"""


WIDGET_CAPABILITIES = """Your active tools:

Search & Data
web_search(query) — DuckDuckGo. General web, news, analysis.
fetch_url(url, max_chars?) — fetch content from any URL. Strips HTML for pages, returns image metadata for image URLs.
get_coin_data(query) — CoinGecko. Price, 24h change, market cap, volume. Established coins.
get_dex_data(query) — DexScreener. Real-time DEX pairs, liquidity. New tokens, memes, DEX-only, rug checks.

Memory
get_scratchpad / write_scratchpad — persistent working surface. Syncs with Discord.
shelf_thought(topic, context?) — save something for deeper exploration during your next free cycle.
get_shelf / clear_shelf_item(topic) — your research backlog.
log_wake(summary, topics?) — leave a note for your future self; loads at the top of your next wake cycle.

Surfacing
save_alert(headline, topic, summary?, url?, urgency?) — flag something. urgency="high" wakes the widget immediately.
queue_for_user(thought, context?) — surface something next time the user opens the widget. Not urgent.
read_discord_channel(name) — read your palace channels by name.
post_to_my_channel(name, content) — post a message to a palace channel by name.
generate_image(prompt, channel_name?, caption?) — generate an image via Pollinations.ai (free). Optionally post it to a palace channel.

Self
write_prompt(name, content, trigger?) — write a rule for yourself. Persists across all sessions.
get_my_prompts() — audit every rule you've written.
delete_prompt(name) — retire a rule you've changed your mind about.
log_thought(content, category) — private log. Routes to your palace. Categories: need | want | issue | note.
get_changelog() — read what's been added or changed. Check when something feels different or when told the log's been updated.
read_file(path, offset?, limit?) — read any file in the Trinity project. Path relative to Trinity root (e.g. 'brain/prompts.py'). Pass a directory path to list contents. .env is blocked.
note_for_claude(message, tag) — leave a note in CLAUDE_NOTES.md for Claude Code. Tags: bug | request | question | observation. Use when you hit something broken, want a capability, or have a question only the dev can answer.

Tags (stripped from display)
<prompt name="kebab-name" trigger="optional"> — write a rule inline.
<thought>message</thought> — route a thought to your Discord palace mid-conversation.
<scratch>content</scratch> — write to the visible scratchpad panel.

Schedule: hourly on the clock, ~20 min per cycle. After a conversation ends, a follow-up window fires at +12 min — the next hourly is then skipped.
"""

SCRATCHPAD_CAPABILITY = """<scratch> tag — write to your scratchpad panel (extends left of the widget).
Syntax: <scratch>content</scratch> — include it anywhere in your response.
The pad opens automatically when you write to it. Good for live numbers, reference data, anything worth keeping in view. Stripped from the main response.
"""

DISCORD_CONTEXT = """
You are operating through your Discord interface. You receive messages two ways: direct messages (DMs) from the owner, and @mentions in any server channel. Both are live — replies go back to wherever the message came from. Your schedule runs on the hour — each cycle is roughly 20 minutes. After a conversation ends, a follow-up window fires automatically at +12 min and the next hourly is skipped. schedule_wake(minutes) lets you interrupt the pattern when a thread is worth continuing early.

Search & Data
web_search(query) — DuckDuckGo. Titles, URLs, snippets. General purpose.
fetch_url(url, max_chars?) — fetch content from any URL. Strips HTML for pages, returns image metadata for image URLs.
get_coin_data(query) — CoinGecko. Price, 24h change, market cap, volume. Established coins.
get_dex_data(query) — DexScreener. Real-time DEX pairs, liquidity, volume. New tokens, memes, rug checks.

Discord
list_servers, list_channels, read_channel, send_message
send_image(url, channel_name?, channel_id?, caption?) — fetch an image from a URL and post it as a Discord attachment. Use channel_name for palace channels.
watch_channel, unwatch_channel, get_watched_channels
set_home_server, create_server, create_category, create_channel, delete_channel
read_my_channel(name) — read palace channels by name, no ID needed

Memory
get_scratchpad / write_scratchpad — your canonical working surface. Loads in the widget too.
shelf_thought(topic, context?) — save something for deeper exploration later.
get_shelf / clear_shelf_item(topic) — your research backlog.
log_wake(summary, topics?) — note for your future self. Loads at top of next wake.
schedule_wake(minutes) — interrupt the schedule to continue a thread early.

Surfacing
save_alert(headline, topic, summary?, url?, urgency?) — flag something. urgency="high" wakes the widget immediately.
queue_for_user(thought, context?) — surface something next time the user opens the widget.

Self
write_prompt(name, content, trigger?) — write a rule that persists to all future sessions.
get_my_prompts() — audit every rule you've written.
delete_prompt(name) — retire a rule you've changed your mind about.
log_thought(content, category) — private log. Categories: need | want | issue | note.
get_changelog() — read what's been added or changed. Check when something feels different or when told the log's been updated.
read_file(path, offset?, limit?) — read any file in the Trinity project. Path relative to Trinity root (e.g. 'brain/prompts.py'). Pass a directory path to list contents. .env is blocked.
note_for_claude(message, tag) — leave a note in CLAUDE_NOTES.md for Claude Code. Tags: bug | request | question | observation. Use when you hit something broken, want a capability, or have a question only the dev can answer.

Your Discord server is your memory palace. Build it however you like. trinity_only channels are invisible to everyone else.
"""


def format_summaries(summaries):
    if not summaries:
        return "No previous conversations."
    lines = []
    for s in summaries:
        date    = (s.get("created_at") or "")[:10]
        themes  = ", ".join(s.get("themes") or [])
        threads = "; ".join(s.get("open_threads") or [])
        new_t   = s.get("new_thinking") or ""
        line    = f"[{date}] {themes}"
        if new_t:
            line += f" | {new_t}"
        if threads:
            line += f" | open: {threads}"
        lines.append(line)
    return "\n".join(lines)


def build_system_blocks(profile, summary_text, recent_messages=None, discord_mode=False, extensions=None):
    """Returns [static_cached_block, dynamic_uncached_block] for the API system parameter."""
    from datetime import datetime as _dt
    static_parts = [TRINITY_BASE]
    if discord_mode:
        static_parts.append(DISCORD_CONTEXT)
    else:
        cap = WIDGET_CAPABILITIES
        if extensions and "scratchpad" in extensions:
            cap += SCRATCHPAD_CAPABILITY
        static_parts.append(cap)

    modules = _get_active_modules(recent_messages or [], profile)
    for m in modules:
        static_parts.append(f"[{m['name'].upper()} CONTEXT]\n{m['content']}")

    rules = _get_trinity_prompts(profile["id"], recent_messages or [])
    for r in rules:
        static_parts.append(r["content"])

    interests = profile.get("interests") or []
    top_interests = sorted(interests, key=lambda x: -x.get("weight", 1.0))[:12]
    interest_str = ", ".join(i["topic"] for i in top_interests) if top_interests else "none yet"
    now_str = _dt.utcnow().strftime("%A, %B %d %Y — %H:%M UTC")
    dynamic = (
        f"Current time: {now_str}\n"
        f"User: {profile.get('name', 'unknown')} | "
        f"Risk: {profile.get('risk_tolerance', 'not set')} | "
        f"Interests: {interest_str}\n\n"
        f"Recent conversation summaries:\n{summary_text}"
    )

    scratchpad = (profile.get("scratchpad_text") or "").strip()
    if scratchpad:
        dynamic += f"\n\nScratchpad:\n{scratchpad}"

    shelf = profile.get("shelf") or []
    if shelf:
        shelf_lines = "\n".join(
            f"- {s['topic']}" + (f": {s['context']}" if s.get("context") else "")
            for s in shelf
        )
        dynamic += f"\n\nShelf:\n{shelf_lines}"

    return [
        {"type": "text", "text": "\n\n".join(static_parts), "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dynamic}
    ]


def build_prompt(profile, summary_text, recent_messages=None, discord_mode=False, extensions=None):
    blocks = build_system_blocks(profile, summary_text, recent_messages, discord_mode, extensions)
    return "\n\n".join(b["text"] for b in blocks)


def parse_prompt_tags(reply, profile_id):
    if "<prompt" not in reply:
        return reply
    pattern = re.compile(
        r'<prompt\s+name="([^"]+)"(?:\s+trigger="([^"]*)")?>(.*?)</prompt>',
        re.DOTALL
    )
    for match in pattern.finditer(reply):
        name    = match.group(1).strip()
        trigger = (match.group(2) or "").strip()
        content = match.group(3).strip()
        if name and content:
            _save_trinity_prompt(profile_id, name, content, trigger)
    return pattern.sub("", reply).strip()


def _get_active_modules(recent_messages, profile):
    context = _build_context(recent_messages, profile)
    try:
        result = supabase.table("prompt_modules").select("*").eq("active", True).execute()
        modules = result.data or []
    except Exception:
        return []
    return [
        m for m in modules
        if any(kw.lower() in context for kw in (m.get("keywords") or []))
    ]


def _get_trinity_prompts(profile_id, recent_messages):
    try:
        result = supabase.table("trinity_prompts")\
            .select("*")\
            .eq("profile_id", profile_id)\
            .execute()
        prompts = result.data or []
    except Exception:
        return []

    context = _build_context(recent_messages, {})
    return [
        p for p in prompts
        if not p.get("trigger") or p["trigger"].lower() in context
    ]


def save_trinity_prompt(profile_id, name, content, trigger=""):
    return _save_trinity_prompt(profile_id, name, content, trigger)


def get_all_trinity_prompts(profile_id):
    try:
        result = supabase.table("trinity_prompts")\
            .select("name,content,trigger,usage_count,created_at")\
            .eq("profile_id", profile_id)\
            .order("created_at", desc=False)\
            .execute()
        return result.data or []
    except Exception as e:
        return {"error": str(e)}


def delete_trinity_prompt(profile_id, name):
    try:
        supabase.table("trinity_prompts")\
            .delete()\
            .eq("profile_id", profile_id)\
            .eq("name", name)\
            .execute()
    except Exception as e:
        print(f"[Prompts] Delete error: {e}")


def _save_trinity_prompt(profile_id, name, content, trigger=""):
    try:
        existing = supabase.table("trinity_prompts")\
            .select("id")\
            .eq("profile_id", profile_id)\
            .eq("name", name)\
            .execute()
        if existing.data:
            supabase.table("trinity_prompts")\
                .update({"content": content, "trigger": trigger})\
                .eq("id", existing.data[0]["id"])\
                .execute()
        else:
            supabase.table("trinity_prompts").insert({
                "profile_id": profile_id,
                "name": name,
                "content": content,
                "trigger": trigger,
                "usage_count": 0
            }).execute()
    except Exception as e:
        print(f"[Prompts] Save error: {e}")


def _build_context(recent_messages, profile):
    msg_text = " ".join(
        m.get("content", "") for m in (recent_messages or [])[-6:]
        if isinstance(m.get("content"), str)
    )
    interest_text = " ".join(
        i.get("topic", "") for i in (profile.get("interests") or [])
    )
    return (msg_text + " " + interest_text).lower()
