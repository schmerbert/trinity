import os
import json
from datetime import datetime
from supabase import create_client
from dotenv import load_dotenv
from pathlib import Path

env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

TABLES = ["profiles", "conversations", "alerts", "prompt_modules", "trinity_prompts"]

snapshot = {}
for table in TABLES:
    try:
        result = supabase.table(table).select("*").execute()
        snapshot[table] = result.data
        print(f"  {table}: {len(result.data)} rows")
    except Exception as e:
        print(f"  {table}: skipped ({e})")
        snapshot[table] = []

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
out_path = Path(__file__).parent / f"trinity_backup_{timestamp}.json"
out_path.write_text(json.dumps(snapshot, indent=2, default=str))
print(f"\nSaved to {out_path.name}")
