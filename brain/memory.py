import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

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

def add_interest(profile_id, interest, weight=1.0):
    profile = get_profile()
    interests = profile.get("interests", [])
    interests.append({"topic": interest, "weight": weight})
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

def get_unseen_alerts(profile_id, limit=10):
    result = supabase.table("alerts")\
        .select("*")\
        .eq("profile_id", profile_id)\
        .eq("seen", False)\
        .order("relevance_score", desc=True)\
        .limit(limit)\
        .execute()
    return result.data

def mark_alerts_seen(profile_id):
    supabase.table("alerts")\
        .update({"seen": True})\
        .eq("profile_id", profile_id)\
        .execute()