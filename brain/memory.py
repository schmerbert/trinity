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

def get_unseen_alerts(profile_id, limit=10):
    result = supabase.table("alerts")\
        .select("*")\
        .eq("profile_id", profile_id)\
        .eq("seen", False)\
        .gte("relevance_score", 1.5)\
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