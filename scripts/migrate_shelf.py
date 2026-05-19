"""
migrate_shelf.py — One-time migration from JSONB shelf to trinity_shelf table.

Run AFTER setup_pgvector.sql has been pasted into Supabase SQL editor:

    python scripts/migrate_shelf.py

Reads profile.shelf (JSONB array), generates embeddings, inserts rows into
trinity_shelf. Safe to re-run — uses upsert on (profile_id, topic).
Does not delete or modify the JSONB column.
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain.memory import get_profile, supabase, embed
from datetime import datetime


def migrate():
    print("Loading profile...")
    profile = get_profile()
    if not profile:
        print("No profile found. Is SUPABASE_URL and SUPABASE_KEY set?")
        return

    shelf = profile.get("shelf") or []
    if not shelf:
        print("Shelf is empty — nothing to migrate.")
        return

    print(f"Migrating {len(shelf)} items (first model load may take ~10s)...\n")

    success, failed = 0, 0
    for item in shelf:
        topic   = item.get("topic", "").strip()
        context = item.get("context", "")
        status  = item.get("status") or "shelf"
        added_at = item.get("added_at")

        if not topic:
            continue

        try:
            vec = embed(f"{topic}: {context}" if context else topic)
            row = {
                "profile_id": str(profile["id"]),
                "topic":      topic,
                "context":    context,
                "status":     status,
                "embedding":  vec,
                "updated_at": datetime.utcnow().isoformat(),
            }
            if added_at:
                row["added_at"] = added_at

            supabase.table("trinity_shelf")\
                .upsert(row, on_conflict="profile_id,topic")\
                .execute()

            print(f"  ✓  [{status}] {topic[:70]}")
            success += 1

        except Exception as e:
            print(f"  ✗  {topic[:70]}  — {e}")
            failed += 1

    print(f"\n{'─' * 50}")
    print(f"Done: {success} migrated, {failed} failed.")
    if failed == 0:
        print("All items in trinity_shelf. JSONB column left intact as backup.")


if __name__ == "__main__":
    migrate()
