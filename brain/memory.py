# Trinity — Personal Financial Intelligence
# Copyright (C) 2025 schmerbert
# Licensed under GNU GPL v3 — see LICENSE file for details

import os
from supabase import create_client
from dotenv import load_dotenv

from pathlib import Path
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

def get_profile():
    result = supabase.table("profiles").select("*").execute()
    if result.data:
        return result.data[0]
    return None

def create_profile(name):
    result = supabase.table("profiles").insert({
        "name": name,
        "risk_tolerance": None,
        "interests": [],
        "feedback_history": []
    }).execute()
    return result.data[0]

def update_profile(profile_id, updates):
    result = supabase.table("profiles").update(updates).eq("id", profile_id).execute()
    return result.data[0]

def add_interest(profile_id, interest, weight=1.0, category=None, symbol=None):
    profile = get_profile()
    interests = profile.get("interests", [])
    
    # Normalize for comparison
    normalized = interest.lower().strip()
    
    for existing in interests:
        existing_normalized = existing.get("topic", "").lower().strip()
        if existing_normalized == normalized:
            existing["weight"] = round(min(existing.get("weight", 1.0) + 0.1, 5.0), 2)
            if category:
                existing["category"] = category
            if symbol:
                existing["symbol"] = symbol
            return update_profile(profile_id, {"interests": interests})
    
    new_interest = {"topic": normalized, "weight": weight}
    if category:
        new_interest["category"] = category
    if symbol:
        new_interest["symbol"] = symbol
    interests.append(new_interest)
    return update_profile(profile_id, {"interests": interests})

def add_feedback(profile_id, topic, sentiment):
    profile = get_profile()
    history = profile.get("feedback_history", [])
    history.append({"topic": topic, "sentiment": sentiment})
    return update_profile(profile_id, {"feedback_history": history})

if __name__ == "__main__":
    profile = get_profile()
    if not profile:
        print("No profile found, creating one...")
        profile = create_profile("test_user")
        print(f"Created profile: {profile}")
    else:
        print(f"Found existing profile: {profile}")

def save_conversation_summary(profile_id, summary):
    result = supabase.table("conversations").insert({
        "profile_id": profile_id,
        "themes": summary.get("themes", []),
        "sentiment": summary.get("sentiment", ""),
        "new_thinking": summary.get("new_thinking", ""),
        "open_threads": summary.get("open_threads", []),
        "communication_style": summary.get("communication_style", "")
    }).execute()
    return result.data[0]

def get_recent_summaries(profile_id, limit=3):
    result = supabase.table("conversations")\
        .select("*")\
        .eq("profile_id", profile_id)\
        .order("created_at", desc=True)\
        .limit(limit)\
        .execute()
    return result.data

def save_alert(alert):
    try:
        result = supabase.table("alerts").insert(alert).execute()
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return None
        print(f"[Brain] Save alert error: {e}")
        return None

def get_unseen_alerts(profile_id, limit=10, min_score=1.5):
    result = supabase.table("alerts")\
        .select("*")\
        .eq("profile_id", profile_id)\
        .eq("seen", False)\
        .gte("relevance_score", min_score)\
        .order("relevance_score", desc=True)\
        .limit(limit)\
        .execute()
    return result.data

def mark_alerts_seen(profile_id):
    supabase.table("alerts")\
        .update({"seen": True})\
        .eq("profile_id", profile_id)\
        .execute()

def process_feedback(profile_id, topic, feedback):
    profile = get_profile()
    interests = profile.get("interests", [])
    
    updated = False
    for interest in interests:
        if interest.get("topic", "").lower() == topic.lower():
            if feedback == "upvote":
                interest["weight"] = round(interest.get("weight", 1.0) + 0.5, 2)
            elif feedback == "downvote":
                interest["weight"] = round(max(0.1, interest.get("weight", 1.0) - 0.3), 2)
            elif feedback == "not_interested":
                interest["weight"] = 0.0
            updated = True
            break
    
    if not updated and feedback == "upvote":
        interests.append({"topic": topic, "weight": 1.5})

    return update_profile(profile_id, {"interests": interests})

# ─── Shelf — topics Trinity wants to explore further ─────────────────────────
#
# alter table profiles add column if not exists shelf jsonb default '[]';
#
def get_shelf(profile_id):
    profile = get_profile()
    return profile.get("shelf") or []

def add_to_shelf(profile_id, topic, context=""):
    from datetime import datetime
    shelf = get_shelf(profile_id)
    shelf = [s for s in shelf if s.get("topic", "").lower() != topic.lower()]
    shelf.append({"topic": topic, "context": context, "added_at": datetime.utcnow().isoformat()})
    return update_profile(profile_id, {"shelf": shelf})

def remove_from_shelf(profile_id, topic):
    shelf = get_shelf(profile_id)
    shelf = [s for s in shelf if s.get("topic", "").lower() != topic.lower()]
    return update_profile(profile_id, {"shelf": shelf})

# ─── Queued thoughts — things Trinity wants to surface when user is around ────
#
# alter table profiles add column if not exists queued_thoughts jsonb default '[]';
#
def get_queued_thoughts(profile_id):
    profile = get_profile()
    return profile.get("queued_thoughts") or []

def queue_thought(profile_id, thought, context=""):
    from datetime import datetime
    queue = get_queued_thoughts(profile_id)
    queue.append({"thought": thought, "context": context, "at": datetime.utcnow().isoformat()})
    return update_profile(profile_id, {"queued_thoughts": queue})

def clear_queued_thoughts(profile_id):
    return update_profile(profile_id, {"queued_thoughts": []})

# ─── Pending Discord writes — widget → Discord thought channel ────────────────
#
# alter table profiles add column if not exists pending_discord_writes jsonb default '[]';
#
# alter table profiles add column if not exists last_seen timestamp;
def update_last_seen(profile_id):
    from datetime import datetime
    return update_profile(profile_id, {"last_seen": datetime.utcnow().isoformat()})

def push_discord_write(profile_id, content):
    from datetime import datetime
    profile = get_profile()
    writes = profile.get("pending_discord_writes") or []
    writes.append({"content": content, "at": datetime.utcnow().isoformat()})
    return update_profile(profile_id, {"pending_discord_writes": writes})

def pop_discord_writes(profile_id):
    profile = get_profile()
    writes = profile.get("pending_discord_writes") or []
    if writes:
        update_profile(profile_id, {"pending_discord_writes": []})
    return writes

# ─── Wake cycle log — Trinity's notes to her future self ─────────────────────
#
# alter table profiles add column if not exists wake_history jsonb default '[]';
#
def log_wake_cycle(profile_id, summary, topics=None):
    from datetime import datetime
    profile = get_profile()
    history = profile.get("wake_history") or []
    history.append({
        "at":      datetime.utcnow().isoformat(),
        "summary": summary,
        "topics":  topics or []
    })
    return update_profile(profile_id, {"wake_history": history[-10:]})

def get_wake_history(profile_id, limit=3):
    profile = get_profile()
    history = profile.get("wake_history") or []
    return history[-limit:]

# ─── Post-conversation wake request ──────────────────────────────────────────
#
# alter table profiles add column if not exists wake_requested_at timestamp;
#
def request_wake(profile_id, minutes=10):
    from datetime import datetime, timedelta
    wake_at = (datetime.utcnow() + timedelta(minutes=minutes)).isoformat()
    return update_profile(profile_id, {"wake_requested_at": wake_at})

def pop_wake_request(profile_id):
    """Returns True and clears the request if the scheduled time has passed."""
    from datetime import datetime, timezone
    profile = get_profile()
    wake_at = profile.get("wake_requested_at")
    if not wake_at:
        return False
    try:
        wake_dt = datetime.fromisoformat(wake_at.replace("Z", "+00:00"))
        if wake_dt.tzinfo is None:
            wake_dt = wake_dt.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < wake_dt:
            return False
    except Exception:
        return False
    update_profile(profile_id, {"wake_requested_at": None})
    return True

# ─── Persistent scratchpad ────────────────────────────────────────────────────
#
# alter table profiles add column if not exists scratchpad_text text default '';
#
def get_scratchpad(profile_id):
    profile = get_profile()
    return profile.get("scratchpad_text") or ""

def save_scratchpad(profile_id, text):
    return update_profile(profile_id, {"scratchpad_text": text})


# ─── Calendar ─────────────────────────────────────────────────────────────────
#
# create table trinity_calendar (
#   id          uuid primary key default gen_random_uuid(),
#   profile_id  uuid references profiles(id),
#   title       text not null,
#   event_date  timestamp not null,
#   notes       text,
#   triggered   boolean default false,
#   created_at  timestamp default now()
# );
# alter table trinity_calendar enable row level security;
# create policy "allow all" on trinity_calendar for all using (true);
#

def mark_date(profile_id, title, event_date, notes=""):
    """Add an event to Trinity's personal calendar."""
    try:
        supabase.table("trinity_calendar").insert({
            "profile_id": profile_id,
            "title":      title,
            "event_date": event_date,
            "notes":      notes or "",
            "triggered":  False
        }).execute()
        return {"status": "marked", "title": title, "date": event_date}
    except Exception as e:
        return {"error": str(e)}


def get_upcoming_events(profile_id, days=7):
    """Return upcoming calendar events within the next N days."""
    from datetime import datetime, timezone, timedelta
    try:
        now  = datetime.now(timezone.utc)
        end  = now + timedelta(days=days)
        result = supabase.table("trinity_calendar")\
            .select("*")\
            .eq("profile_id", profile_id)\
            .gte("event_date", now.isoformat())\
            .lte("event_date", end.isoformat())\
            .order("event_date", desc=False)\
            .execute()
        return result.data or []
    except Exception as e:
        return []


# ─── Keyword watches ─────────────────────────────────────────────────────────
#
# Migration (run once in Supabase SQL editor):
#
# create table trinity_watches (
#   id          uuid primary key default gen_random_uuid(),
#   profile_id  uuid references profiles(id),
#   keyword     text not null,
#   note        text,
#   active      boolean default true,
#   created_at  timestamp default now(),
#   unique(profile_id, keyword)
# );
# alter table trinity_watches enable row level security;
# create policy "allow all" on trinity_watches for all using (true);
#
def set_watch(profile_id, keyword, note=""):
    try:
        supabase.table("trinity_watches").upsert({
            "profile_id": profile_id,
            "keyword":    keyword.lower().strip(),
            "note":       note,
            "active":     True
        }, on_conflict="profile_id,keyword").execute()
        return {"status": "watching", "keyword": keyword}
    except Exception as e:
        return {"error": str(e)}

def clear_watch(profile_id, keyword):
    try:
        supabase.table("trinity_watches")\
            .delete()\
            .eq("profile_id", profile_id)\
            .ilike("keyword", f"%{keyword.lower().strip()}%")\
            .execute()
        return {"status": "cleared", "keyword": keyword}
    except Exception as e:
        return {"error": str(e)}

def get_watches(profile_id):
    try:
        result = supabase.table("trinity_watches")\
            .select("keyword, note, created_at")\
            .eq("profile_id", profile_id)\
            .eq("active", True)\
            .order("created_at", desc=False)\
            .execute()
        return result.data or []
    except Exception as e:
        return []


def delete_calendar_event(profile_id, title):
    """Delete a calendar event by title (case-insensitive partial match)."""
    try:
        result = supabase.table("trinity_calendar")\
            .select("id,title")\
            .eq("profile_id", profile_id)\
            .execute()
        matches = [r for r in (result.data or []) if title.lower() in r["title"].lower()]
        if not matches:
            return {"error": f"No event matching '{title}'"}
        for m in matches:
            supabase.table("trinity_calendar").delete().eq("id", m["id"]).execute()
        return {"deleted": [m["title"] for m in matches]}
    except Exception as e:
        return {"error": str(e)}