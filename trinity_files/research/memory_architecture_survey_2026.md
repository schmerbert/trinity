# Agent Memory Architecture Survey — May 2026
*Researched for Trinity memory upgrade planning*

## What This Is For
Background reading before building the episodic/semantic split, reflection cycle, and confidence weights. Goal: learn from frameworks that already hit walls we'd hit ourselves. Not to adopt wholesale.

---

## The Two Philosophies

### Mem0 — Pluggable Memory Layer
- Add to any existing agent framework via simple API: `add()` to store, `search()` to retrieve
- Passive extraction: Mem0 decides what's worth remembering automatically
- Retrieval via semantic search (vector) + knowledge graph (Pro tier)
- Flat memory store — no tiering
- Benchmark: 92.5 on LoCoMo, ~6,900 tokens per retrieval call (vs ~26,000 for full-context)
- Apache 2.0, ~48K GitHub stars
- Key weakness: passive extraction means the agent doesn't control its own memory. Works fine for factual recall, weaker for judgment about what matters.

### Letta (formerly MemGPT) — Full Agent Runtime
- Memory is tiered: Core (RAM/in-context) → Recall (recent/cache) → Archival (cold storage)
- **Agent self-edits its own memory** by calling memory tool functions during reasoning loop
- Retrieval is agentic — the agent decides when and what to retrieve
- OS-inspired design: context window is RAM, everything else is disk
- Apache 2.0, ~21K GitHub stars
- Key weakness: tied to Letta's full runtime. Lock-in. Also more complex to run and reason about.

---

## Key Architectural Lessons

### 1. Memory Tiering Matters
Letta's three-tier model maps cleanly to what Trinity needs:
- **Core**: things always in context (user identity, origin story, current active threads)
- **Recall**: recent session data, recent cycle findings
- **Archival**: old research, resolved shelf threads, historical market data

Trinity currently has no tiering — everything is flat (prompts, shelf, scratchpad all co-equal). The episodic/semantic split is step one toward this.

### 2. Forgetting Must Be Selective
Mem0's decay mechanisms remove "irrelevant" information over time. But what's irrelevant is context-dependent.
- **Permanent layer**: origin story, bankruptcy, $KIND history, $KIND, named decisions — never decays
- **Weighted layer**: stated interests, patterns observed — decays unless reinforced
- **Ephemeral layer**: session observations, one-off signals — fades after N cycles

### 3. The Agent Should Control Its Own Memory
Letta's key insight: passive storage produces average recall. Active self-editing (the agent writing to its own memory mid-reasoning) produces better signal/noise ratio. 
Trinity already does a version of this via write_prompt and shelf management. The gap: memory writes are post-hoc (end of cycle) rather than inline during reasoning.

### 4. Token Efficiency Is Real
Mem0 achieves 6,956 tokens per retrieval vs ~26,000 for full-context approaches. At Trinity's current scale this doesn't matter much, but it will when cycle depth increases and conversation history lengthens. Design for this now.

### 5. Hardest Open Problems (2026)
Per Mem0's state-of-field report:
- **Cross-session identity**: recognizing the same person/context across very different conversation framings
- **Temporal abstraction at scale**: knowing that "what we discussed last month" is different from "what we discussed yesterday" even when stored in the same flat vector space
- **Memory staleness**: stored beliefs that were once true but are now wrong (e.g., user's interests shifting)

Trinity's planned **confidence weights on beliefs** and **forgetting curves** directly address #2 and #3.

---

## What This Means for Trinity's Build Roadmap

| Gap | Framework lesson | Trinity's current plan |
|-----|-----------------|----------------------|
| Flat memory | Letta's tiering | Episodic/semantic split |
| Post-hoc writes | Letta's inline self-edit | Reflection cycle split |
| No confidence weights | None in either framework — novel | Confidence weights on beliefs |
| No decay | Mem0's decay mechanisms | Forgetting curves (selective) |
| Token bloat risk | Mem0's token efficiency work | Vary cycle depth (shallow/deep) |

The confidence weights idea isn't something either framework has — that's genuinely novel design territory. Worth flagging to Claude Code as potentially differentiating.

---

## Sources
- https://vectorize.io/articles/mem0-vs-letta
- https://mem0.ai/blog/state-of-ai-agent-memory-2026
- https://arxiv.org/abs/2504.19413 (Mem0 paper)
