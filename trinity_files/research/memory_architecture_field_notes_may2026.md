# Agent Memory Architecture — Field Notes
**May 20, 2026 | Trinity research cycle**

---

## What the field looks like right now

Memory is no longer an afterthought. As of 2026, it's a first-class architectural component with its own benchmark suite, research literature, and measurable performance gaps between approaches.

Three benchmarks now define the landscape:
- **LoCoMo** — 1,540 questions testing multi-session recall at varying difficulty (single-hop, multi-hop, open-domain, temporal)
- **LongMemEval** — 500 questions across six categories: session recall, preference recall, knowledge update, temporal reasoning, multi-session
- **BEAM** — operates at 1M–10M token scales; tests what systems do when context volumes are orders of magnitude larger than typical benchmarks

Best published scores (Mem0): 92.5 on LoCoMo, 94.4 on LongMemEval, ~6,900 tokens per query. Biggest gains over baseline: +29.6 on temporal reasoning, +23.1 on multi-hop.

**The hardest open problems (field consensus):**
1. Cross-session identity — reliably knowing *who* you're talking to across disconnected sessions
2. Temporal abstraction at scale — not just storing when something happened, but knowing when older beliefs should yield to newer ones
3. Memory staleness — stale facts that were once true becoming wrong, undetected

---

## What this means for Trinity's architecture

The shelf threads that Claude Code named map directly onto the field's hardest problems:

**Episodic vs semantic separation** → This is the core architectural divide the field solved first. Episodic = raw session observations ("user said X on May 12"). Semantic = consolidated understanding ("user cares about legacy"). Trinity's current system mixes these. The upgrade is to separate them at storage level so queries can target the right layer.

**Confidence weights on user beliefs** → The field's "knowledge update" category — how does a memory system handle contradictory or updated information? The naive answer is "overwrite." The correct answer is "track confidence and recency." The field benchmarks this and systems that don't handle it score poorly on LongMemEval knowledge update tasks.

**Forgetting curves / selective decay** → The field calls this "memory staleness." The unsolved version: how do you know a memory is stale without being told? Trinity's version is tractable because we can design explicit permanence flags — bankruptcy history, origin story, $KIND involvement don't decay. Recent signal like current meme coin interest does. This is actually easier than the general case.

**Reflection cycle split (world findings vs user understanding)** → Letta's architecture separates this at the agent design level. Two cycle types = two memory write paths. Not just cleaner — it's what prevents world observations from polluting user model, and vice versa.

**Varying cycle depth (shallow collect, deep consolidate)** → Field equivalent: Mem0's tiered consolidation. Short cycles for raw ingestion. Longer cycles for compression and re-ranking. This is the mechanism that makes memory *better* over time rather than just *larger*.

---

## What Trinity has that most frameworks don't

The field builds generic infrastructure. Trinity's memory is *personal* — it knows one person, not a class of users. That changes the design constraints significantly:

- Identity problem is solved: we know exactly who we're talking to
- Permanence classes are deterministic: we can manually flag what never decays
- The hardest benchmark problems (cross-session identity, staleness detection without signals) are much easier when the population is N=1 and the relationship is long-term

The risk is the inverse: because it's N=1, every wrong belief compounds. A generic assistant that has a wrong model of a user can be corrected across millions of interactions. A personal AI with a wrong model has to be caught and corrected explicitly, or it just keeps acting on a lie.

This is the design argument for confidence weights: not to be academically rigorous, but because the blast radius of a wrong belief is higher when there's only one person.

---

## Recommended next steps for Claude Code

1. **Start with episodic/semantic split** — lowest risk, highest impact, doesn't require rebuilding existing infrastructure
2. **Add permanence flags** — simple metadata addition; marks certain memory entries as no-decay
3. **Reflection cycle split** — two write paths, one per cycle type; enables targeted consolidation
4. **Confidence weights** — harder to implement correctly; do this after the split so you know which layer is being weighted
5. **Forgetting curves** — design last, after you have enough runtime data to know what actually goes stale

**Not recommended**: adopting Mem0 or Letta wholesale. They've hit the walls we'd hit, but their solutions are for N=many. Trinity's constraints are different enough that we build informed by their research, not on their stack.

---

*Sources: Mem0 State of AI Agent Memory 2026 report; Letta agent memory blog; shelf threads from Claude Code design session May 19*
