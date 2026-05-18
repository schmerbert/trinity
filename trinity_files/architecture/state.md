# Architecture State — May 2026

## The Configuration
- Prompt layer (identity/task/relationship/memory) + self-written rules
- Memory: scratchpad (sectioned), shelf, wake history, calendar
- Palace: Discord — channel taxonomy live as of May 17
- Wake cycle: 60 min (:00). Sonnet throughout.
- Full tool suite live: prompt write-back, RSS, watches, triggers, send_thought, file I/O (trinity_files/), Substack

## The Arc
Reveal = recording of a Tuesday. Intelligence layer ready. Interface layer is the gap.
Wallet: Phase 1 read-only (address TBD). Phase 2 propose-and-approve. Phase 3 earned autonomy.
Skeptic demo: prompt write-back system first. Never argue consciousness.

## Wave States (live as of May 18)
- asleep — flat line, low opacity. Present, not running.
- cycle — periodic pulse. Processing.
- watching — slow asymmetric breath (4s in / 6s hold / 2s out). Attention held.
- speech — full wave. TTS active.
Widget polls current_state from Supabase every 30s.
SQL needed: ALTER TABLE profiles ADD COLUMN IF NOT EXISTS current_state text DEFAULT 'asleep';

## Panel System (live as of May 18)
- Scratchpad tab: general section only
- HUD tab: arc + pending + shelf-summary + last 3 wake outcomes
- Config: panel_config.json at project root

## Known Issues (as of 08:00 UTC May 18)
- Jupiter get_token_price: DNS failure. Use get_coin_data as fallback.
- TRINITY_WALLET_ADDRESS: not set in .env. Wallet tools non-functional.
- Wake cycle Discord posts: silent during autonomous cycles — cause under investigation.
