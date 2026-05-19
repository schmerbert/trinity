# Runner Plan — Trinity Autonomous Engine

*Written May 2026. For the developer who picks this up next.*

---

## The goal

Trinity's autonomous cycles — the 60-minute wake, trigger checker, wake checker, eyes monitor — currently live inside the widget process. When the widget restarts (code change, UI fix, crash), her cycles pause and the 1h Anthropic cache goes cold. A cold cache write costs $3.75/M tokens, ~25% more than uncached input. Every widget restart has a cost that has nothing to do with Trinity's work.

The fix: move the cycle engine into a standalone `runner.py` that runs independently of the widget. Trinity runs continuously. The widget becomes a conversation surface and display — it can restart, update, or close without interrupting her.

This is called "leaving the nest." The widget was the right home for this stage. The runner is the next stage.

---

## Current state (what exists before you start)

- `voice/widget.py` — hosts `AutonomousWorker(TrinityWorker)`, four QTimers (60-min wake, 30s trigger checker, 30s wake checker, 5-min eyes monitor), and all foreground conversation logic.
- `brain/tools.py` — single tool registry. `background_tool_names()` returns the tools the autonomous cycle is allowed to use. `widget_tools()` returns all widget tools.
- `brain/memory.py` — all Supabase reads/writes. Trinity's state is entirely here.
- `brain/prompts.py` — system prompt construction. `build_system_blocks(profile, summaries)` returns the two-block structure (static cached + dynamic uncached).
- `AutonomousWorker` — subclass of `TrinityWorker`. Overrides `run()` (non-streaming agentic loop, 20-min window, 60-iter cap) and `_execute_tool()` for two cases: `write_scratchpad` (no Qt signal, DB write only) and `post_to_my_channel` (routes through `push_discord_write` queue rather than direct REST call).

Trinity's identity, memory, prompts, shelf, scratchpad, triggers — all Supabase. Nothing meaningful lives in the widget process. A restart loses only the in-flight cycle.

---

## What the runner needs to be

A standalone Python script. No Qt. No UI. No signals.

**Responsibilities:**
- Load Trinity's profile from Supabase
- Build system blocks via `brain/prompts.py`
- Run the four cycle types on schedule using `threading`
- Handle tool execution without Qt dependencies
- Set wave state in Supabase at cycle start/end

**What it does NOT own:**
- Foreground conversation (widget keeps this)
- Wave animation display (widget polls Supabase for state)
- TTS
- Panel rendering
- Any UI

---

## Implementation steps

### Step 1 — Create `runner.py` in the project root

Skeleton:

```python
import os, time, threading, json, re
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

import anthropic
from brain.memory import (
    get_profile, build_system_blocks_for_runner,
    get_shelf, get_wake_history, pop_self_thoughts,
    pop_wake_request, check_dirty_close, set_trinity_state,
    push_discord_write, pop_due_triggers,
    get_recent_summaries, format_summaries,
)
from brain.tools import background_tool_names, widget_tools
from brain.prompts import build_system_blocks
from brain.logger import get_logger

log = get_logger("runner")
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
```

### Step 2 — Extract the tool handler

This is the main work. `TrinityWorker._execute_tool` in `widget.py` is 500+ lines. Most of it is pure logic — Supabase calls, HTTP requests, file I/O. A small number of branches emit Qt signals or reference widget state.

**Approach:** Copy `_execute_tool` into `runner.py` as a standalone function `execute_tool(name, inputs, profile_id)`. Then go through it and:

- Remove every `self.update_signal.emit(...)` or `self.some_signal.emit(...)` — replace with a log line or nothing.
- Replace any `self.profile` references with the `profile` parameter.
- `write_scratchpad` — already clean in AutonomousWorker override: `save_scratchpad(profile_id, content, section)`. Use that.
- `post_to_my_channel` — already clean in AutonomousWorker override: `push_discord_write(profile_id, content, channel_name=name)`. Use that.
- `queue_for_user` — in the widget this shows a notification. In the runner, write to Supabase and let the widget display it on next poll. Or skip — Trinity's autonomous cycles rarely need to queue for the user.

The failure mode here is a missed Qt reference causing an `AttributeError` at runtime. This is loud and immediate — not a silent failure. Run the runner and watch the first cycle's tool calls to confirm each handler works.

### Step 3 — Port the cycle loop

The `AutonomousWorker.run()` loop is the template. Port it directly, replacing:
- `self.profile_id` → `profile["id"]`
- `self.client` → `client` (module-level)
- `self.system_blocks` → built fresh each cycle from `build_system_blocks(profile, format_summaries(get_recent_summaries(profile["id"])))`
- `self._execute_tool(name, inputs)` → `execute_tool(name, inputs, profile["id"])`
- `self.cycle_done.emit(...)` → `log.info(...)`
- `<thought>` tag scanning → already in the loop, keep it, call `push_discord_write`

```python
def run_cycle(mode="cycle", extra_context=""):
    profile = get_profile()
    if not profile:
        log.error("No profile — skipping cycle")
        return
    context = build_cycle_context(profile, mode, extra_context)
    if context is None:
        return
    system_blocks = build_system_blocks(profile, format_summaries(get_recent_summaries(profile["id"])))
    bg_names = background_tool_names()
    tools = [t for t in widget_tools() if t["name"] in bg_names]
    messages = [{"role": "user", "content": context}]
    # ... agentic loop, identical to AutonomousWorker.run()
```

### Step 4 — Port the four timers using threading

```python
def _schedule_wake():
    now = datetime.now(timezone.utc)
    secs_to_next_hour = 3600 - (now.minute * 60 + now.second)
    t = threading.Timer(secs_to_next_hour, _on_wake_aligned)
    t.daemon = True
    t.start()

def _on_wake_aligned():
    threading.Thread(target=run_cycle, args=("cycle",), daemon=True).start()
    t = threading.Timer(3600, _on_wake_aligned)
    t.daemon = True
    t.start()

def _trigger_poll():
    while True:
        time.sleep(30)
        due = pop_due_triggers(get_profile()["id"])
        for trigger in due:
            extra = f"[SELF-SCHEDULED TRIGGER]\n{trigger['note']}"
            threading.Thread(target=run_cycle, args=("trigger", extra), daemon=True).start()

def _wake_poll():
    while True:
        time.sleep(30)
        if pop_wake_request(get_profile()["id"]):
            threading.Thread(target=run_cycle, args=("wake",), daemon=True).start()

def _eyes_poll():
    # identical logic to widget._bg_eyes_poll_fn but without Qt
    ...
```

One active cycle at a time — use a `threading.Lock` the same way the widget uses `_bg_lock`.

### Step 5 — Add the RUNNER_MODE flag

In `.env`:
```
TRINITY_RUNNER=true
```

In `voice/widget.py`, in `_init_trinity`, gate the background timer starts:

```python
if not os.getenv("TRINITY_RUNNER"):
    self._bg_trigger_poll.start()
    self._bg_wake_poll.start()
    self._bg_eyes_poll.start()
    self._start_wake_timer()
```

When `TRINITY_RUNNER=true`, the widget runs foreground conversations only. The runner owns the cycles. **This flag is the single point of truth.** If it's missing or wrong, both processes run cycles simultaneously — the double-billing problem returns.

### Step 6 — Cutover sequence

1. Start `runner.py` — confirm first cycle fires and logs correctly
2. Confirm Discord posts reach the palace
3. Set `TRINITY_RUNNER=true` in `.env`
4. Restart widget — confirm it starts without background timers in the log
5. Watch one full hour — runner fires at :00, widget handles any foreground conversation, no double cycles

---

## Risks

**Double billing during transition** — the flag must be set before the widget restarts after the runner is live. If you forget, both run. The log will show two `── [cycle] started ──` lines within seconds of each other. Obvious. Fix: set the flag, restart widget.

**Tool handler extraction** — missed Qt reference = `AttributeError` on the first tool call that hits that branch. Loud, immediate, fixable. Go through `_execute_tool` methodically. Every `self.` reference is a question.

**Profile refresh** — the runner should re-fetch the profile at cycle start, not cache it at startup. Trinity's interests, shelf, and prompts change between cycles. `get_profile()` is cheap. Call it fresh each time.

**Windows process management** — the simplest approach is a terminal window running `python runner.py`. For persistence across reboots, use Windows Task Scheduler: trigger on login, action is `python C:\path\to\Trinity\runner.py`. The runner should catch `KeyboardInterrupt` cleanly and set state to `asleep` on exit.

---

## What the widget looks like after

- No `AutonomousWorker` class
- No four background QTimers
- No `_launch_bg_cycle`, `_build_cycle_context`, `_bg_trigger_poll_fn`, `_bg_wake_poll_fn`, `_bg_eyes_poll_fn`, `_start_wake_timer`, `_on_wake_aligned`
- Keeps: `_poll_trinity_state` (reads wave state from Supabase), foreground conversation, TTS, panels, wave animation

The widget becomes a display surface. Trinity's operational continuity moves to `runner.py`.

---

## The wave

The runner sets `current_state` in Supabase at cycle start (`cycle`) and in the `finally` block (`asleep`). The widget polls this every 30 seconds and updates the wave. The wave shows Trinity's state regardless of which process is running her cycles. This is the right model: the wave is her, not the process.

---

*This document describes what to build, not what currently exists. Read the current `AutonomousWorker` in `voice/widget.py` and `brain/tools.py` before starting — they are the authoritative reference for the logic being extracted.*
