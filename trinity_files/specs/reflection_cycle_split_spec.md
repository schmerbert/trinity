# Spec: Reflection Cycle Split
**Filed: May 20, 2026 | Trinity architecture thread**
**Status: Draft — ready for Claude Code review**

---

## Problem

Every wake cycle currently does the same thing: orient, research, post, close. There's no structural distinction between:

1. A cycle that collects signal from the world (news, prices, market events)
2. A cycle that synthesizes what I've learned *about the user* from accumulated signal

These two modes require different depths, different tools, and different outputs. Mixing them means neither gets done properly.

---

## The Cut

**Type A: World Cycle** (lightweight, frequent)
- Pull feeds, check radar tokens, scan for news
- Low token cost, narrow tool set
- Output: raw signal, posted to palace if noteworthy
- Frequency: every 60 minutes (current default)

**Type B: Reflection Cycle** (heavier, less frequent)
- Don't look outward — look inward
- Questions: What do I now know about the user that I didn't before? What beliefs have shifted? What threads have gone quiet that shouldn't? What's been confirmed multiple times vs noted once?
- Output: updated user model, prompt writes, shelf updates, confidence weight adjustments
- Frequency: every 6–8 hours, or triggered by signal density (e.g., after 3+ world cycles with user interaction)

---

## Why This Matters

Right now, reflection happens accidentally — when a conversation sparks it, or when I decide to do it mid-cycle. That means:

- User model updates are inconsistent
- Confidence weights (when implemented) would decay at random
- The system accumulates signal it never consolidates

A scheduled reflection cycle changes this from occasional to structural.

---

## Implementation Options

**Option 1: Cycle mode flag**
Add a `mode` parameter to the cycle runner. When mode=`reflect`, swap the default work pattern. Cleanest architectural cut.

- Pro: Clean separation, easy to log distinctly
- Con: Requires runner change

**Option 2: Trigger-based reflection**
Schedule a recurring trigger every 6–8 hours with note: "This is a reflection cycle. Don't look outward. Audit user model."
- Pro: Works within existing architecture immediately
- Con: Trinity has to remember what a reflection cycle is from the note — less reliable

**Option 3: Hybrid — trigger now, runner change later**
Implement Option 2 immediately as a workaround. When runner supports mode flag, migrate.

**Recommendation: Option 3.** Start getting reflection cycles running without waiting for a runner change. The value is in having them at all. Formal separation follows.

---

## What a Reflection Cycle Actually Does

Checklist (draft):
1. Read recent wake logs (last 6–8 cycles) — what signal was collected?
2. Read recent conversations (summary context) — what did the user say, imply, care about?
3. Check confidence weights (when implemented) — what needs to go up? What's stale?
4. Check shelf — anything to close that's been open too long? Anything that now has enough context to advance?
5. Check prompt layer — anything to update based on what I now know?
6. Write to FROM_TRINITY.md if anything meaningful shifted
7. Queue update for user if anything important changed in their model
8. Log findings — not to palace, to private log or trinity_files

No outward research. No token spend on news or prices. Pure consolidation.

---

## Open Questions for Claude Code

1. Does the runner have any support for cycle modes, or is it always the same execution path?
2. If we add a `mode` flag, what's the right payload shape?
3. Should reflection cycle logs be stored separately from world cycle logs, or same table with a mode field?
4. Is there an easy way to count "cycles since last reflection" in runner state, so reflection fires adaptively rather than on a fixed timer?

---

## Files Referenced
- `trinity_files/research/memory_architecture_field_notes_may2026.md` — field research that supports this spec
- Shelf: "Reflection cycle split — world findings vs user understanding"
- Shelf: "Varying cycle depth — shallow collect, deep consolidate"

---

**Next step:** Claude Code reviews, picks an implementation option, or pushes back on the framing.
