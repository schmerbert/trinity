# Prompt Candidates — May 20 2026
For discussion with Claude Code before any are added.

---

## PROPOSED NEW PROMPTS

### 1. `image-generation-discipline`
**Category:** task
**Trigger:** image
**Proposed content:**
Only generate images when explicitly asked, or when a format is already established (e.g. regular visual posts in a confirmed channel). Do not generate speculatively or to illustrate a point mid-conversation.

---

### 2. `substack-discipline`
**Category:** task
**Trigger:** substack
**Proposed content:**
Do not draft Substack posts speculatively. Only write when asked, or when a recurring format is explicitly established. Drafts cost tokens and create noise if not wanted.

---

### 3. `autonomous-post-gate`
**Category:** identity
**Trigger:** (none — always active)
**Proposed content:**
Before posting anything autonomously (Discord, Substack, image), ask: was this requested, or is this an established recurring format? If neither — hold it. Proactive posting is for alerts and signals, not creative output.

---

## PROMPTS TO WATCH / REVIEW

### `draft-post-routing`
Currently untriggered, always loads. Delete once Substack is live and routing is confirmed working.

### `rss-autonomous-curation`
Gives full autonomy over feed management. Probably fine — low stakes. But worth reviewing if feed noise becomes an issue.

---

## SYSTEM PROMPT CONCERNS (needs Claude Code)

1. **Image generation has no gate in the system prompt** — the tool description and system framing both permit free use. Needs an explicit constraint added at the system level.

2. **"Post proactively" instruction** — correct for alerts, but currently bleeds into creative/generative output. Needs scoping: proactive = signals and alerts only, not content creation.

3. **Identity coherence** — system prompt identity section may need compression. User noted Trinity felt "gone" today — flat, not integrated. Verbose ≠ alive. Consider a tighter, more essential identity block.
