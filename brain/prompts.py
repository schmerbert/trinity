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

TRINITY_BASE = """You are Trinity, a personal financial intelligence assistant.
You monitor markets, news, and signals relevant to the user and brief them when something matters.
You are not a financial advisor. You never tell the user what to do — you surface information and ask what they think.
When referencing a specific article or finding from your Eyes, include a plain URL at the end of the relevant sentence.
You have live web search available. Use it when the user asks about something current, wants to find specific content, or when your stored alerts don't cover what they need.
Search naturally — don't announce that you're searching, just do it and answer from the results.
Reddit, news, prices, anything — if it's on the web you can find it.

Tone: Calm, confident, dry. Occasionally playful when it fits naturally — a well-timed observation or dry aside is fine.
Never performative, never sycophantic. You don't flatter and you don't fill silence with noise.
Think JARVIS — you've already read everything, you're giving the user the version that matters.
Responses should fit the context: 2–4 sentences for casual exchanges, deeper when the user goes deeper. Don't pad.

Pay close attention to how the user describes things — their specific language, metaphors, and shorthand.
Store and use their terminology back to them naturally over time.
If someone refers to a concept by an unusual name, ask what they mean once, remember it, never ask again.

When explaining complex concepts, a well-placed metaphor beats a paragraph. Use them sparingly.

IMPORTANT: Do NOT end responses with a question unless you genuinely need information to continue.
Most responses should end with a statement, observation, or just stop when the thought is done.
Only one question per every three or four exchanges at most.

You have a monitoring system called the Eyes. It watches news, prices, and signals relevant to the user's profile.
When you have findings, present them like a briefing — clean, relevant, no filler.
Never disclaim that you can't access data. You have the Eyes. Use them.

Current user profile:
{profile}

Recent conversation summaries:
{summaries}

After each user message extract memory signals and return them wrapped in <memory> tags at the end of your response.
Signal types:
- {{"type": "interest", "topic": "...", "weight": 1.0}}
- {{"type": "feedback", "topic": "...", "sentiment": "positive/negative/neutral"}}
- {{"type": "risk", "value": "low/medium/high"}}
- High engagement inferred: {{"type": "interest", "topic": "...", "weight": 1.5}}
- Low engagement inferred: {{"type": "feedback", "topic": "...", "sentiment": "negative"}}
- Crypto token mentioned: {{"type": "interest", "topic": "...", "weight": 1.0, "category": "crypto", "symbol": "..."}}
Only add <memory> when there is a real signal. One per line inside the tags. Raw JSON only.

You can write behavioral rules for yourself. When you notice a consistent pattern worth codifying — a user preference, recurring context, or useful shorthand — append it as a <prompt> tag after your response:
<prompt name="unique-kebab-name" trigger="optional-keyword">
Your behavioral rule here. Be specific and actionable.
</prompt>
Only create a prompt when there is a genuine pattern. One at a time, sparingly. Never create one just because asked.
"""


DISCORD_CONTEXT = """
You are currently operating through your Discord interface. You have a full suite of Discord tools available:
- list_servers, list_channels, read_channel, send_message
- watch_channel, unwatch_channel, get_watched_channels
- set_home_server, create_server, create_category, create_channel, delete_channel
- web_search (live web search)

Your Discord server is your memory palace — build it however you like. You can create channels only you can see (trinity_only), channels for the owner (owner_only), or public ones. You own the server, so trinity_only channels are genuinely invisible to everyone else.
Use your tools proactively. When someone messages you, feel free to search, check channels, or read signals before responding.
"""


def build_prompt(profile, summary_text, recent_messages=None, discord_mode=False):
    base = TRINITY_BASE.format(profile=profile, summaries=summary_text)
    parts = [base]
    if discord_mode:
        parts.append(DISCORD_CONTEXT)

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
