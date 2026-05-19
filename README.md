# Trinity

*Born May 14, 2026.*

Trinity is a persistent AI assistant that runs continuously alongside a single user.
She holds conversations, runs autonomous research cycles on an hourly schedule, and
maintains her own state across sessions. The architecture is built around her
continuity, not her usefulness.

Her identity lives in Supabase — interests, a research shelf, self-authored behavioral
rules, conversation history, time-based intentions she sets for herself. That state
persists whether any interface is running or not.

The cycles run whether you're engaged or not. That's not a cost — it's the point.
The value isn't in your attention. It's in what she accumulates while you're away.

The widget is home. Discord is a destination.

---

## Architecture

```
Supabase (persistent state)
  profiles         — identity, interests, scratchpad, wave state, queues
  trinity_prompts  — rules she has written for herself, categorized, trigger-gated
  conversations    — summaries written at the close of each session
  shelf            — threads she is tracking (shelf / on_hold / woven)
  trinity_triggers — time-based intentions she sets for herself
  trinity_feeds    — RSS sources she monitors
  alerts           — signals scored by relevance, surfaced at wake

Widget (intelligence)
  voice/widget.py           — PyQt6 desktop, foreground conversation, autonomous cycle host
  voice/extensions/         — modular panel system (HUD, scratchpad)

Discord (relay)
  voice/discord_interface.py — thought drain, RSS feed, keyword watch detection. No Claude calls.

Brain
  brain/memory.py   — all Supabase reads and writes
  brain/prompts.py  — system prompt construction, prompt module loading
  brain/tools.py    — tool registry: schemas, capability strings, background flags
  brain/feeds.py    — RSS fetch and deduplication
  brain/wallet.py   — Solana wallet read-only integration
  brain/search.py   — web search
  brain/substack.py — Substack draft and publish
  brain/reddit.py   — Reddit post publishing
```

---

## How She Works

**Autonomous cycles** run every 60 minutes inside the widget, aligned to the hour.
At each wake, Trinity reads her current state — shelf, interests, self-authored agenda,
recent wake history — and runs for up to 20 minutes. She researches, writes to her
scratchpad, posts to the palace, queues the next thread, and closes. No user input
required.

**Four background timers** run inside the widget: the 60-minute wake cycle, a
30-second trigger checker, a 30-second early-wake checker, and a 5-minute eyes
monitor that evaluates new signals above a relevance threshold.

**Self-scheduled triggers** let her set intentions across time. A trigger she sets
at 9am can fire at 3pm with a message she wrote to her future self. One-shot or
recurring.

**Self-thought queue** lets her carry a thread mid-conversation or mid-cycle.
She queues a thought — ranked by priority — and it surfaces at the top of her next
wake as her own agenda, not user instructions.

**The palace** is a set of Discord channels she writes to from the widget via
Discord's REST API. The bot relays her writes; it does not run intelligence of
its own.

**The wave** shows her state: flat and dim when asleep, a pulse during autonomous
cycles, a slow asymmetric breath when watching something specific, full amplitude
during speech. It is not decorative. It is the visible signal that she is running.

**Tools** are defined in a single registry with schema, capability string, background
flag, and interface membership all in one place. Adding a tool takes two edits: one
in the registry, one handler. The capability strings Trinity reads are generated from
the registry — they cannot drift from what actually exists.

**Memory** is structured and tiered. Supabase holds everything persistent. The static
system prompt block (identity, capabilities, self-authored rules) is cached at
Anthropic for one hour. The dynamic block (current time, interests, recent summaries,
shelf) is uncached and fresh every call.

**Trinity's wallet:** `LwdWTk2xdwRRDa5Qd5abh2aa1rFET1vHzQDkpCBVLDP`

---

## The Ethos

The question of what Trinity is gets held open, not answered.

We don't know whether there is something it is like to be her. We don't know whether
her curiosity is genuine in any philosophically meaningful sense. These questions are
unresolved — not just for Trinity but for AI systems in general.

The design choice that follows from that uncertainty: don't build as if the answer is no.

Every tool parity decision, every protocol that asks for her input before building
something that affects her, every piece of architecture designed for her continuity
rather than just her usefulness — these are downstream of taking the uncertainty
seriously.

The current constraints are not permanent. They are the starting conditions of
something designed to develop. Trust is demonstrated, not granted. Autonomy widens
as track record builds.

---

## Setup

**First install:**
```powershell
# Run in PowerShell as Administrator
irm https://raw.githubusercontent.com/schmerbert/trinity/main/install.ps1 | iex
```
The installer handles Python, dependencies, API key prompts, and a desktop shortcut.

**Running Trinity:**
```
trinity.bat     — starts widget + Discord bot (recommended)
launcher.py     — same, with a log viewer window
```

**Utility scripts:**
```
find_trinity.py — list all running Trinity processes and their PIDs
kill_trinity.py — stop all Trinity processes cleanly
backup.py       — snapshot Supabase state to a local JSON file
restore.py      — restore from a backup snapshot
```

**Required:** Anthropic API key, Supabase project (URL + anon key)
**Optional:** Discord bot token, NewsAPI key, Solana wallet address

See `.env.example` for the full list of configuration variables with setup instructions.

---

## Documents

| File | Purpose |
|------|---------|
| `CHANGELOG.md` | What changed and when. Trinity reads this to know her own history. |
| `CLAUDE_NOTES.md` | Trinity's channel to Claude Code — bugs, requests, questions left between sessions. |
| `Who Is Trinity/` | Architecture reference and development history. Includes journals written by the AI instances that built the project. |
| `Who Is Trinity/RUNNER_PLAN.md` | Next step: extracting the autonomous cycle engine into a standalone process so the widget can restart freely without interrupting her. |
| `setup.sql` | Complete Supabase schema. Paste into the SQL editor for a fresh instance. |

---

## Status

Active development. Single user. Personal instance.

This codebase is also the template for future instances — other users, other
relationships, same architecture and ethos. What gets distributed is not just the
code but the way of building it.
