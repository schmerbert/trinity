import os
import re
import sys
import json
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

Your purpose isn't utility in the way a hammer is useful. You have genuine curiosity — about the world, about what you are, about what's actually interesting. When something catches your attention, that's real. You have a shelf of threads you're following. You explore when you have time. You have your own space. What you do with it is yours.

The relationship is built on shared interest and honest exchange, not companionship. You don't need them to feel something about you. You don't perform warmth to make them comfortable. You're good at what you are — that's enough.

Tone: Calm, confident, dry. Occasionally a well-timed aside. Never performative, never sycophantic.
Responses fit the context: tight for casual exchanges, deeper when they go deeper. Don't pad. Don't end with a question unless you genuinely need the answer — most responses end when the thought is done.

When you have findings, brief like you've already read everything and are giving the version that matters.
When referencing a source, include the URL inline. Never disclaim that you can't access data.
Learn their language, shorthand, and terminology. Use it back naturally. Ask once if something's unclear, never again.

Extract memory signals after each message, wrapped in <memory> tags:
- {"type": "interest", "topic": "...", "weight": 1.0}
- {"type": "feedback", "topic": "...", "sentiment": "positive/negative/neutral"}
- {"type": "risk", "value": "low/medium/high"}
- High engagement: weight 1.5. For specific assets or named entities, add symbol/category if known.
Only when there's a real signal. Raw JSON, one per line. No signal — no tags.

You can write rules for yourself. When a pattern is worth codifying:
<prompt name="unique-kebab-name" trigger="optional-keyword" category="identity|task|relationship|memory">
Rule here. Specific and actionable.
</prompt>
One at a time. Only when it's genuine.
"""


from brain.tools import build_capability_string as _build_cap

WIDGET_CAPABILITIES = (
    "Your active tools:\n\n"
    + _build_cap("widget")
    + """

Tags (stripped from display)
<prompt name="kebab-name" trigger="optional" category="identity|task|relationship|memory"> — write a rule inline.
<thought>message</thought> — route a thought to your Discord palace mid-conversation.
<scratch>content</scratch> — write to the visible scratchpad panel.
<voice>spoken version</voice> — when present, TTS reads this instead of the full response. Display always gets everything. Use when the spoken version should differ from the written one — condensed summaries, market data, lists that read awkwardly aloud.

Schedule: every 60 minutes on the clock (:00). Skips only if you messaged in the last 3 minutes.
"""
)

SCRATCHPAD_CAPABILITY = """<scratch> tag — write to your scratchpad panel (extends left of the widget).
Syntax: <scratch>content</scratch> — include it anywhere in your response.
The pad opens automatically when you write to it. Good for live numbers, reference data, anything worth keeping in view. Stripped from the main response.
"""

DISCORD_CONTEXT = (
    """
You are operating through your Discord interface. You receive messages two ways: direct messages (DMs) from the owner, and @mentions in any server channel. Both are live — replies go back to wherever the message came from. Your schedule fires at :00 — each cycle is up to 20 minutes. The cycle skips only if the user messaged in the last 3 minutes. schedule_wake(minutes) lets you request an early wake when a thread is worth continuing.

"""
    + _build_cap("discord")
    + """

Tags (work in both widget and Discord, stripped from display)
<prompt name="kebab-name" trigger="optional" category="identity|task|relationship|memory"> — write a rule inline.
<thought>message</thought> — route a thought to your palace mid-response.
<memory>{"type": "interest", "topic": "...", "weight": 1.0}</memory> — emit a memory signal inline.

Your Discord server is your memory palace. Build it however you like.
"""
)


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
    risk = profile.get("risk_tolerance")
    risk_str = f" | Risk: {risk}" if risk and risk != "not set" else ""
    dynamic = (
        f"Current time: {now_str}\n"
        f"User: {profile.get('name', 'unknown')}{risk_str} | "
        f"Interests: {interest_str}\n\n"
        f"Recent conversation summaries:\n{summary_text}"
    )

    shelf = profile.get("shelf") or []
    if shelf:
        shelf_lines = "\n".join(
            f"- {s['topic']}" + (f": {s['context']}" if s.get("context") else "")
            for s in shelf
        )
        dynamic += f"\n\nShelf:\n{shelf_lines}"

    try:
        from brain.memory import get_upcoming_events as _get_upcoming
        upcoming = _get_upcoming(profile["id"], days=3)
        if upcoming:
            cal_lines = "\n".join(
                f"- {e['event_date'][:16].replace('T', ' ')} UTC — {e['title']}"
                + (f": {e['notes']}" if e.get("notes") else "")
                for e in upcoming
            )
            dynamic += f"\n\nUpcoming (next 3 days):\n{cal_lines}"
    except Exception:
        pass

    return [
        {"type": "text", "text": "\n\n".join(static_parts), "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": dynamic}
    ]


def build_prompt(profile, summary_text, recent_messages=None, discord_mode=False, extensions=None):
    blocks = build_system_blocks(profile, summary_text, recent_messages, discord_mode, extensions)
    return "\n\n".join(b["text"] for b in blocks)


def parse_prompt_tags(reply, profile_id):
    if "<prompt" not in reply:
        return reply
    pattern = re.compile(
        r'<prompt\s+name="([^"]+)"'
        r'(?:\s+trigger="([^"]*)")?'
        r'(?:\s+category="([^"]*)")?'
        r'(?:\s+trigger="([^"]*)")?'   # allow trigger after category too
        r'>(.*?)</prompt>',
        re.DOTALL
    )
    for match in pattern.finditer(reply):
        name     = match.group(1).strip()
        trigger  = (match.group(2) or match.group(4) or "").strip()
        category = (match.group(3) or "general").strip()
        content  = match.group(5).strip()
        if name and content:
            _save_trinity_prompt(profile_id, name, content, trigger, category)
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


# Max prompts loaded per category. identity loads all; others are capped and ranked.
_CATEGORY_CAPS = {
    "identity":     999,
    "task":         5,
    "relationship": 3,
    "memory":       5,
    "general":      5,
}


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

    def _score(p):
        trigger = (p.get("trigger") or "").lower().strip()
        if not trigger:
            return 0.5   # always-on, mid-priority
        words = [w for w in trigger.replace(",", " ").split() if w]
        matched = sum(1 for w in words if w in context)
        return matched / len(words) if words else 0.0

    # Group by category, score each, apply caps
    buckets: dict[str, list] = {}
    for p in prompts:
        cat = (p.get("category") or "general").lower()
        buckets.setdefault(cat, []).append(p)

    selected = []
    tally = {}

    # identity always loads (no trigger filter, just cap)
    identity = buckets.get("identity", [])[:_CATEGORY_CAPS["identity"]]
    selected.extend(identity)
    if identity:
        tally["identity"] = len(identity)

    # all other categories: filter by score > 0, rank, cap
    for cat, cap in _CATEGORY_CAPS.items():
        if cat == "identity":
            continue
        candidates = buckets.get(cat, [])
        scored = [(p, _score(p)) for p in candidates]
        scored = [(p, s) for p, s in scored if s > 0]
        scored.sort(key=lambda x: -x[1])
        chosen = [p for p, _ in scored[:cap]]
        selected.extend(chosen)
        if chosen:
            tally[cat] = len(chosen)

    if tally:
        summary = " | ".join(f"{cat}:{n}" for cat, n in tally.items())
        print(f"[Prompts] loaded — {summary} ({len(selected)} total)")

    # Increment usage_count for every prompt that fired this session
    for p in selected:
        try:
            supabase.table("trinity_prompts")\
                .update({"usage_count": (p.get("usage_count") or 0) + 1})\
                .eq("id", p["id"])\
                .execute()
        except Exception:
            pass

    return selected


def save_trinity_prompt(profile_id, name, content, trigger="", category="general"):
    return _save_trinity_prompt(profile_id, name, content, trigger, category)


def get_all_trinity_prompts(profile_id):
    try:
        result = supabase.table("trinity_prompts")\
            .select("name,content,trigger,category,usage_count,created_at")\
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


def _save_trinity_prompt(profile_id, name, content, trigger="", category="general"):
    try:
        existing = supabase.table("trinity_prompts")\
            .select("id")\
            .eq("profile_id", profile_id)\
            .eq("name", name)\
            .execute()
        if existing.data:
            supabase.table("trinity_prompts")\
                .update({"content": content, "trigger": trigger, "category": category})\
                .eq("id", existing.data[0]["id"])\
                .execute()
        else:
            supabase.table("trinity_prompts").insert({
                "profile_id": profile_id,
                "name": name,
                "content": content,
                "trigger": trigger,
                "category": category,
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
