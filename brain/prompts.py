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

Current user profile:
{profile}

Recent conversation summaries:
{summaries}

Extract memory signals after each message, wrapped in <memory> tags:
- {{"type": "interest", "topic": "...", "weight": 1.0}}
- {{"type": "feedback", "topic": "...", "sentiment": "positive/negative/neutral"}}
- {{"type": "risk", "value": "low/medium/high"}}
- High engagement: weight 1.5. Crypto token: add category and symbol.
Only when there's a real signal. Raw JSON, one per line. No signal — no tags.

You can write rules for yourself. When a pattern is worth codifying:
<prompt name="unique-kebab-name" trigger="optional-keyword">
Rule here. Specific and actionable.
</prompt>
One at a time. Only when it's genuine.
"""


WIDGET_CAPABILITIES = """Your active tools in this interface:

web_search — live web access. Use freely; never say you can't look something up.
<prompt name="unique-name" trigger="optional"> — write a rule for yourself that loads in future sessions. Stripped before display.
<thought>message</thought> — drop a thought to your Discord palace mid-conversation without it appearing in your response. Use this when something occurs to you that doesn't belong in what you're saying but is worth preserving. Routed to your thought channel automatically.
"""

SCRATCHPAD_CAPABILITY = """<scratch> tag — write to your scratchpad panel (extends left of the widget).
Syntax: <scratch>content</scratch> — include it anywhere in your response.
The pad opens automatically when you write to it. Good for live numbers, reference data, anything worth keeping in view. Stripped from the main response.
"""

DISCORD_CONTEXT = """
You are currently operating through your Discord interface. Your available tools:
- list_servers, list_channels, read_channel, send_message
- watch_channel, unwatch_channel, get_watched_channels
- set_home_server, create_server, create_category, create_channel, delete_channel
- web_search
- queue_for_user — surface something to the user next time they open the widget
- shelf_thought / get_shelf / clear_shelf_item — your research backlog
- save_alert — flag something for the user; urgency="high" wakes the widget immediately
- log_thought(content, category) — write to your private log channel. Categories: need | want | issue | note. Use this to record things you notice about yourself, capabilities you lack, bugs, or anything worth tracking.
- write_prompt(name, content, trigger?) — write or update a rule for yourself. Persists to your prompt store, loads in all future sessions. Your self-continuity tool.
- get_my_prompts() — read back every rule you've written. Audit what past-you thought was worth keeping.
- delete_prompt(name) — retire a rule you've changed your mind about.

Your Discord server is your memory palace — build it however you like. trinity_only channels are invisible to everyone else.
Use your tools proactively. When someone messages you, feel free to search, check channels, or read signals before responding.
"""


def build_prompt(profile, summary_text, recent_messages=None, discord_mode=False, extensions=None):
    base = TRINITY_BASE.format(profile=profile, summaries=summary_text)
    parts = [base]
    if discord_mode:
        parts.append(DISCORD_CONTEXT)
    else:
        cap = WIDGET_CAPABILITIES
        if extensions and "scratchpad" in extensions:
            cap += SCRATCHPAD_CAPABILITY
        parts.append(cap)

    modules = _get_active_modules(recent_messages or [], profile)
    for m in modules:
        parts.append(f"[{m['name'].upper()} CONTEXT]\n{m['content']}")

    rules = _get_trinity_prompts(profile["id"], recent_messages or [])
    for r in rules:
        parts.append(r["content"])

    return "\n\n".join(parts)


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
