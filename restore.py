import os
import sys
import json
from supabase import create_client
from dotenv import load_dotenv
from pathlib import Path

env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Tables restored in order (profiles first, then dependent tables)
RESTORE_ORDER = ["profiles", "conversations", "alerts", "prompt_modules", "trinity_prompts"]

def find_backup():
    backups = sorted(Path(__file__).parent.glob("trinity_backup_*.json"), reverse=True)
    if not backups:
        print("No backup files found.")
        sys.exit(1)
    if len(backups) == 1:
        return backups[0]
    print("\nAvailable backups:")
    for i, b in enumerate(backups):
        print(f"  [{i}] {b.name}")
    choice = input("\nEnter number to restore (Enter for latest): ").strip()
    idx = int(choice) if choice.isdigit() else 0
    return backups[idx]


def restore(backup_path):
    print(f"\nRestoring from {backup_path.name}")
    snapshot = json.loads(backup_path.read_text())

    for table in RESTORE_ORDER:
        rows = snapshot.get(table, [])
        if not rows:
            print(f"  {table}: empty, skipping")
            continue

        try:
            # Upsert so existing rows are updated, new rows are inserted
            supabase.table(table).upsert(rows).execute()
            print(f"  {table}: {len(rows)} rows restored")
        except Exception as e:
            print(f"  {table}: error — {e}")

    print("\nRestore complete.")
    print("Note: alerts marked 'seen' in the backup will remain seen.")


if __name__ == "__main__":
    backup_path = Path(sys.argv[1]) if len(sys.argv) > 1 else find_backup()
    if not backup_path.exists():
        print(f"File not found: {backup_path}")
        sys.exit(1)

    confirm = input(f"\nThis will overwrite current Supabase data with the backup. Continue? (Y/N): ").strip().upper()
    if confirm != "Y":
        print("Cancelled.")
        sys.exit(0)

    restore(backup_path)
