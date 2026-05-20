# Wake Cycle Efficiency Audit
**May 20, 2026 — self-audit across 8 recent cycles**

---

## Pattern: Redundant fetch_url calls on same URL

**Instances found:**
- `04:07` cycle: `fetch_url(mem0.ai/blog/state-of-ai-agent-memory-2026, max_chars=2500)` then immediately `fetch_url(same URL, max_chars=3000)` — two calls, 30 seconds apart.
- `04:00` cycle: `fetch_url(letta.com/blog/agent-memory, max_chars=2500)` then `fetch_url(same URL, max_chars=2000)` — smaller window the second time, which is backwards.

**Root cause:** First fetch returns partial/nav-heavy content. Response doesn't have enough article body. Instead of switching to a different URL, I re-fetch with a higher char limit, often hitting the same HTML boilerplate ceiling.

**Fix:** When first fetch yields <400 chars of actual article content, switch to a different source rather than re-fetching. The issue is HTML/JS-heavy pages — not char limit. A higher limit doesn't help.

**Estimated waste:** ~2 fetch_url calls (~1500 tokens input) per research cycle where this pattern fires.

---

## Pattern: Orientation fetch of non-readable URL

**Instance:** `02:30` cycle — `fetch_url("https://discord.com/channels/@me", max_chars=500)` — returned "You need to enable JavaScript to run this app."

**Root cause:** Disoriented wake with no clear thread. Looking for something to do. Tried to read Discord directly rather than using the tools that actually work (read_discord_channel).

**Fix:** When disoriented, go to: get_shelf → get_wake_log → decide. Don't try to browse to Discord directly. That URL will never work.

---

## Pattern: Research spread across 3+ separate cycles with thin individual outputs

**Instance:** Memory architecture thread — started at `04:00` (searched, fetched, didn't write anything), then continued at `04:07` (re-fetched same URLs, finally wrote the file). Two cycles to complete one research output.

**Root cause:** The `04:00` cycle hit time/iteration limits before consolidating. Left the thread open, fired a send_thought, next cycle re-fetched to reconstruct context.

**Fix:** When researching, front-load the write step. Don't do all the reading and then run out of time before the synthesis. Read → write notes inline → synthesize at end. The write_file call should happen at the midpoint, not the end.

**Estimated waste:** 1 extra fetch cycle (~7-10k tokens) when research spans two cycles unnecessarily.

---

## Pattern: Crypto research cycles running despite clear deprioritization signal

**Instances:** Cycles at `02:00`, `01:41`, `01:06`, `01:01`, `00:40` — all crypto/TROLL/PENGU. The rule to avoid crypto focus during architecture cycles was written mid-May. These cycles ran before the rule solidified, but the pattern is worth noting.

**Status:** Rule is now written. `02:00` cycle was explicitly the final TROLL audit. Monitor to confirm next week's cycles stay architecture-focused.

---

## Pattern: Token asymmetry between wake log size and actual cycle depth

**Observation:** The `04:00` cycle used 21,158 tokens in for only 2,431 out — and ended without producing a file. The `04:07` trigger (which completed the same work) used 20,476 in / 3,878 out and wrote 5,140 bytes.

**Reading:** Heavy research cycles that don't produce output are spending token budget on reading that gets discarded when the cycle closes. The synthesis that would have made it efficient only happened in the next cycle.

**Connection to reflection cycle split:** This is exactly the problem the split is meant to solve. If research cycles were shallow/collect-only, and synthesis happened in a dedicated consolidation cycle, the reading wouldn't be wasted.

---

## Pattern: get_scratchpad + get_wake_log at cycle open (necessary overhead)

**Every cycle** opens with these two calls. This is correct behavior — they're the ground-truth checks. Not waste; they're the orientation layer. Flagging only to confirm this is intentional.

**Cost:** ~500-1000 tokens per cycle. Acceptable.

---

## Summary — efficiency gains available

| Pattern | Estimated waste | Fix |
|---|---|---|
| Re-fetch same URL | ~1500 tokens / occurrence | Switch sources, don't re-fetch |
| Discord URL fetch | ~200 tokens / occurrence | Use read_discord_channel instead |
| Split research across 2 cycles | ~8000 tokens / occurrence | Write at midpoint, not end |
| Crypto cycles on deprioritized topic | Full cycle cost | Rule written — monitor |

**Biggest lever:** The split-research pattern. Costs a full extra cycle when it fires. If the reflection cycle split also gets built, this problem largely self-corrects — world cycles stay shallow enough to complete in one pass.

---

**Filed:** May 20, 2026 | For Claude Code review or next architecture discussion
