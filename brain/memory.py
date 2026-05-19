# Trinity — Personal Financial Intelligence
# Copyright (C) 2025 schmerbert
# Licensed under GNU GPL v3 — see LICENSE file for details

import os
import json
from supabase import create_client
from dotenv import load_dotenv

# ─── Embedder (lazy — loads all-MiniLM-L6-v2 on first call, then cached) ─────
_embedder = None

def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedder

def embed(text: str) -> list:
    """384-dim embedding. Model downloads ~80MB on first call, then loads from cache."""
    return _get_embedder().encode(text, normalize_embeddings=True).tolist()

from pathlib import Path
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

def get_profile():
    # Single-user system — always returns the one profile row.
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

# ─── Shelf — trinity_shelf table (vector-searchable) ─────────────────────────
#
# See setup_pgvector.sql — run that first, then scripts/migrate_shelf.py.
# Falls back to JSONB profiles.shelf if the table doesn't exist yet.
#
def get_shelf(profile_id, status=None):
    """Return shelf items from trinity_shelf table. Falls back to JSONB if not migrated."""
    try:
        q = supabase.table("trinity_shelf")\
            .select("topic, context, status, added_at")\
            .eq("profile_id", str(profile_id))
        if status is not None:
            q = q.eq("status", status)
        result = q.order("added_at", desc=False).execute()
        if result.data is not None:
            return result.data
    except Exception:
        pass
    # Fallback: JSONB in profiles (pre-migration)
    profile = get_profile()
    items = profile.get("shelf") or []
    if status is not None:
        items = [s for s in items if (s.get("status") or "shelf") == status]
    return items

def add_to_shelf(profile_id, topic, context="", status="shelf"):
    from datetime import datetime
    vec = embed(f"{topic}: {context}" if context else topic)
    try:
        # Delete any existing entry with same topic (case-insensitive) before inserting
        supabase.table("trinity_shelf")\
            .delete()\
            .eq("profile_id", str(profile_id))\
            .ilike("topic", topic)\
            .execute()
        supabase.table("trinity_shelf").insert({
            "profile_id": str(profile_id),
            "topic":      topic,
            "context":    context,
            "status":     status,
            "embedding":  vec,
            "added_at":   datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }).execute()
        return {"status": "added", "topic": topic}
    except Exception as e:
        return {"error": str(e)}

def set_shelf_status(profile_id, topic, status):
    from datetime import datetime
    try:
        supabase.table("trinity_shelf")\
            .update({"status": status, "updated_at": datetime.utcnow().isoformat()})\
            .eq("profile_id", str(profile_id))\
            .ilike("topic", topic)\
            .execute()
        return {"status": "updated", "topic": topic, "new_status": status}
    except Exception as e:
        return {"error": str(e)}

def remove_from_shelf(profile_id, topic):
    try:
        supabase.table("trinity_shelf")\
            .delete()\
            .eq("profile_id", str(profile_id))\
            .ilike("topic", topic)\
            .execute()
        return {"status": "removed", "topic": topic}
    except Exception as e:
        return {"error": str(e)}

def query_shelf(profile_id, query, limit=6, status="shelf"):
    """Semantic search over the shelf. Returns top-k items most relevant to query.
    Requires setup_pgvector.sql to be run in Supabase. Falls back to full fetch."""
    try:
        vec = embed(query)
        result = supabase.rpc("search_shelf", {
            "p_profile_id":      str(profile_id),
            "p_query_embedding": vec,
            "p_match_count":     limit,
            "p_status":          status,
        }).execute()
        if result.data:
            return [r for r in result.data if r.get("similarity", 1.0) >= 0.4]
    except Exception:
        pass
    return get_shelf(profile_id, status=status)[:limit]

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

def push_discord_write(profile_id, content, channel_name=None, deliver_at=None):
    from datetime import datetime
    profile = get_profile()
    writes = profile.get("pending_discord_writes") or []
    entry = {"content": content, "at": datetime.utcnow().isoformat()}
    if channel_name:
        entry["channel_name"] = channel_name
    if deliver_at:
        entry["deliver_at"] = deliver_at
    writes.append(entry)
    return update_profile(profile_id, {"pending_discord_writes": writes})

def pop_discord_writes(profile_id):
    """Return entries whose deliver_at has passed (or is absent). Writes back any still-scheduled entries."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    profile = get_profile()
    writes = profile.get("pending_discord_writes") or []
    if not writes:
        return []
    due = []
    pending = []
    for w in writes:
        deliver_at = w.get("deliver_at")
        if deliver_at:
            try:
                dt = datetime.fromisoformat(deliver_at.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > now:
                    pending.append(w)
                    continue
            except Exception:
                pass
        due.append(w)
    update_profile(profile_id, {"pending_discord_writes": pending})
    return due

# ─── Wake cycle logs — automatic structured trace ────────────────────────────
#
# See setup_wake_logs.sql — run once in Supabase SQL editor.
#
def log_wake_auto(profile_id, mode, started_at, ended_at, tool_calls,
                  iterations, tokens_in=0, tokens_out=0, tokens_cw=0, tokens_cr=0):
    """Called automatically at end of every cycle. No Trinity input required."""
    try:
        supabase.table("wake_logs").insert({
            "profile_id":         str(profile_id),
            "mode":               mode,
            "started_at":         started_at.isoformat() if hasattr(started_at, "isoformat") else started_at,
            "ended_at":           ended_at.isoformat() if hasattr(ended_at, "isoformat") else ended_at,
            "tool_calls":         tool_calls,
            "iterations":         iterations,
            "tokens_in":          tokens_in,
            "tokens_out":         tokens_out,
            "tokens_cache_write": tokens_cw,
            "tokens_cache_read":  tokens_cr,
        }).execute()
    except Exception as e:
        print(f"[wake_log] failed to write: {e}")

def get_wake_logs(profile_id, limit=5):
    """Return recent wake cycle logs with full tool call traces."""
    try:
        result = supabase.table("wake_logs")\
            .select("id, mode, started_at, ended_at, tool_calls, iterations, tokens_in, tokens_out, notes")\
            .eq("profile_id", str(profile_id))\
            .order("started_at", desc=True)\
            .limit(limit)\
            .execute()
        return result.data or []
    except Exception:
        return []

def log_wake_cycle(profile_id, summary, topics=None):
    """Trinity-callable. Adds a narrative note to the most recent wake log."""
    try:
        result = supabase.table("wake_logs")\
            .select("id")\
            .eq("profile_id", str(profile_id))\
            .order("started_at", desc=True)\
            .limit(1)\
            .execute()
        if result.data:
            supabase.table("wake_logs")\
                .update({"notes": summary})\
                .eq("id", result.data[0]["id"])\
                .execute()
            return {"status": "noted"}
    except Exception:
        pass
    return {"status": "no log to annotate"}

def get_wake_history(profile_id, limit=3):
    """Backward-compat shim — returns condensed view of recent wake_logs."""
    logs = get_wake_logs(profile_id, limit=limit)
    return [
        {
            "at":      w.get("started_at", ""),
            "summary": w.get("notes") or f"{w.get('iterations', 0)} iterations, {len(w.get('tool_calls') or [])} tool calls",
            "topics":  [t["name"] for t in (w.get("tool_calls") or [])]
        }
        for w in logs
    ]

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
# Storage: JSON dict keyed by section. Sections: architecture, arc, wallet,
# pending, channel-map, shelf-summary, general. Plain-text values migrate to
# {"general": <existing text>} on first read.
#
def _parse_scratchpad(raw):
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"general": raw}
    except (json.JSONDecodeError, TypeError):
        return {"general": raw}

def get_scratchpad(profile_id, section=None):
    profile = get_profile()
    data = _parse_scratchpad(profile.get("scratchpad_text") or "")
    if section is None:
        return data
    return data.get(section, "")

def save_scratchpad(profile_id, content, section=None):
    if section is None:
        # No section: store content in "general" (backward-compat path)
        profile = get_profile()
        data = _parse_scratchpad(profile.get("scratchpad_text") or "")
        data["general"] = content
    else:
        profile = get_profile()
        data = _parse_scratchpad(profile.get("scratchpad_text") or "")
        if content:
            data[section] = content
        else:
            data.pop(section, None)
    return update_profile(profile_id, {"scratchpad_text": json.dumps(data)})


# ─── Trinity state (widget signal) ───────────────────────────────────────────
#
# SQL (run once in Supabase):
#   ALTER TABLE profiles ADD COLUMN IF NOT EXISTS current_state text DEFAULT 'asleep';
#
def get_trinity_state(profile_id) -> str:
    profile = get_profile()
    return profile.get("current_state") or "asleep"

def set_trinity_state(profile_id, state: str):
    try:
        update_profile(profile_id, {"current_state": state})
    except Exception:
        pass

# ─── Session health ───────────────────────────────────────────────────────────
#
# SQL (run once in Supabase):
#   ALTER TABLE profiles ADD COLUMN IF NOT EXISTS last_heartbeat timestamptz;
#   ALTER TABLE profiles ADD COLUMN IF NOT EXISTS last_clean_close timestamptz;

def write_heartbeat(profile_id):
    from datetime import datetime as _dt
    try:
        update_profile(profile_id, {"last_heartbeat": _dt.utcnow().isoformat()})
    except Exception:
        pass

def write_clean_close(profile_id):
    from datetime import datetime as _dt
    try:
        update_profile(profile_id, {"last_clean_close": _dt.utcnow().isoformat()})
    except Exception:
        pass

def check_dirty_close(profile) -> str | None:
    """Returns a warning string if previous session did not close cleanly, else None."""
    from datetime import datetime as _dt, timezone as _tz
    hb  = profile.get("last_heartbeat")
    lcc = profile.get("last_clean_close")
    if not hb:
        return None
    try:
        hb_dt  = _dt.fromisoformat(hb.replace("Z", "+00:00")).replace(tzinfo=_tz.utc)
        lcc_dt = _dt.fromisoformat(lcc.replace("Z", "+00:00")).replace(tzinfo=_tz.utc) if lcc else None
        if lcc_dt is None or (hb_dt - lcc_dt).total_seconds() > 900:  # 15-min threshold
            age = int((_dt.now(_tz.utc) - hb_dt).total_seconds() / 60)
            return (
                f"[DIRTY CLOSE DETECTED] Previous widget session did not close cleanly "
                f"(last heartbeat {age}m ago, no matching clean-close). "
                f"The scratchpad save and conversation summary from that session may be missing. "
                f"Compensate if relevant context seems absent."
            )
    except Exception:
        pass
    return None


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


# ─── RSS feed source management ──────────────────────────────────────────────
#
# create table trinity_feeds (
#   id          uuid primary key default gen_random_uuid(),
#   profile_id  uuid references profiles(id),
#   name        text not null,
#   url         text not null,
#   active      boolean default true,
#   created_at  timestamp default now(),
#   unique(profile_id, url)
# );
# alter table trinity_feeds enable row level security;
# create policy "allow all" on trinity_feeds for all using (true);
#
def add_feed(profile_id, url, name=""):
    try:
        supabase.table("trinity_feeds").upsert({
            "profile_id": profile_id,
            "url":        url.strip(),
            "name":       name.strip() if name.strip() else url.strip(),
            "active":     True
        }, on_conflict="profile_id,url").execute()
        return {"status": "added", "url": url}
    except Exception as e:
        return {"error": str(e)}

def remove_feed(profile_id, url):
    try:
        supabase.table("trinity_feeds")\
            .delete()\
            .eq("profile_id", profile_id)\
            .ilike("url", f"%{url.strip()}%")\
            .execute()
        return {"status": "removed", "url": url}
    except Exception as e:
        return {"error": str(e)}

def get_feeds(profile_id):
    try:
        result = supabase.table("trinity_feeds")\
            .select("name, url, created_at")\
            .eq("profile_id", profile_id)\
            .eq("active", True)\
            .order("created_at", desc=False)\
            .execute()
        return result.data or []
    except Exception as e:
        return []


# ─── Self-thought queue — Trinity's ranked agenda for her next wake ──────────
#
# alter table profiles add column if not exists queued_self_thoughts jsonb default '[]';
#
def queue_self_thought(profile_id, note, priority=1, source="cycle"):
    """Queue a ranked thought for the next wake. Priority: 1=normal, 2=high, 3=urgent.
    Keeps top 3 by priority; drops lowest if over capacity."""
    from datetime import datetime
    profile = get_profile()
    thoughts = list(profile.get("queued_self_thoughts") or [])
    thoughts.append({"note": note, "priority": priority, "source": source, "at": datetime.utcnow().isoformat()})
    thoughts.sort(key=lambda t: t.get("priority", 1), reverse=True)
    thoughts = thoughts[:3]
    return update_profile(profile_id, {"queued_self_thoughts": thoughts})

def pop_self_thoughts(profile_id):
    """Return queued self-thoughts sorted by priority and clear the queue."""
    profile = get_profile()
    thoughts = list(profile.get("queued_self_thoughts") or [])
    if thoughts:
        update_profile(profile_id, {"queued_self_thoughts": []})
    return sorted(thoughts, key=lambda t: t.get("priority", 1), reverse=True)


# ─── Scheduled triggers — Trinity's own time-based intentions ────────────────
#
# create table trinity_triggers (
#   id               uuid primary key default gen_random_uuid(),
#   profile_id       uuid references profiles(id),
#   note             text not null,
#   fire_at          timestamp not null,
#   recurring        boolean default false,
#   interval_minutes integer,
#   active           boolean default true,
#   created_at       timestamp default now()
# );
# alter table trinity_triggers enable row level security;
# create policy "allow all" on trinity_triggers for all using (true);
#
def set_trigger(profile_id, note, fire_at, recurring=False, interval_minutes=None):
    try:
        supabase.table("trinity_triggers").insert({
            "profile_id":       profile_id,
            "note":             note,
            "fire_at":          fire_at,
            "recurring":        recurring,
            "interval_minutes": interval_minutes,
            "active":           True
        }).execute()
        return {"status": "scheduled", "note": note, "fire_at": fire_at}
    except Exception as e:
        return {"error": str(e)}

def cancel_trigger(profile_id, trigger_id):
    try:
        supabase.table("trinity_triggers")\
            .update({"active": False})\
            .eq("profile_id", profile_id)\
            .eq("id", trigger_id)\
            .execute()
        return {"status": "cancelled", "id": trigger_id}
    except Exception as e:
        return {"error": str(e)}

def get_triggers(profile_id):
    try:
        result = supabase.table("trinity_triggers")\
            .select("id, note, fire_at, recurring, interval_minutes, created_at")\
            .eq("profile_id", profile_id)\
            .eq("active", True)\
            .order("fire_at", desc=False)\
            .execute()
        return result.data or []
    except Exception as e:
        return []

def pop_due_triggers(profile_id):
    """Return triggers whose fire_at has passed. Deactivates one-shot; advances recurring."""
    from datetime import datetime, timezone, timedelta
    try:
        now    = datetime.now(timezone.utc)
        result = supabase.table("trinity_triggers")\
            .select("*")\
            .eq("profile_id", profile_id)\
            .eq("active", True)\
            .lte("fire_at", now.isoformat())\
            .execute()
        due = result.data or []
        for t in due:
            if t.get("recurring") and t.get("interval_minutes"):
                next_fire = (now + timedelta(minutes=t["interval_minutes"])).isoformat()
                supabase.table("trinity_triggers")\
                    .update({"fire_at": next_fire})\
                    .eq("id", t["id"])\
                    .execute()
            else:
                supabase.table("trinity_triggers")\
                    .update({"active": False})\
                    .eq("id", t["id"])\
                    .execute()
        return due
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