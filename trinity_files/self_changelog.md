# Trinity Self-Changelog

Append-only. Three sections: prompt changes, proposed updates, architecture decisions.
Format: date | what | why

---

## PROMPT_CHANGES

### 2026-05-19
- **position-sizing-framework** — replaced data record with fetch-on-demand pointer. Actual framework moved to `trinity_files/position_sizing.md`. Reason: data masquerading as a rule, loaded every session unnecessarily.
- **the-configuration** — replaced philosophical statement with lean pointer to FROM_TRINITY.md. Reason: was loading as an instruction, which turned genuine orientation into performance.
- **external-presence** — same as above. Moved reflection content to FROM_TRINITY.md, left only behavioral pointer.
- **financial-context** — deleted. Content absorbed into `user-relationship` where it logically belonged. Reason: redundant separate prompt, token overhead.
- **user-relationship** — updated to be single source of truth for user context including financial background. Consolidated from two sources.

---

## PROPOSED

### 2026-05-19
- **operational-backup + claude-code-routing** — two prompts saying nearly the same thing. Intentional redundancy given routing criticality — leaving for now, but worth reviewing if prompt count becomes a concern again.
- **scratchpad usage** — shelf thread flagged that scratchpad is no longer used as working memory but prompts still treat it that way. Needs a proper pass once the Discord/wake cycle stability work settles.

---

## ARCHITECTURE

### 2026-05-19
- **Data-to-files migration** — established pattern: data records belong in `trinity_files/` with fetch-on-demand prompts, not as full prompt entries. `palace-channel-map` was the first instance of this pattern. `position-sizing-framework` and `financial-context` now follow the same logic. Future audits should check for data disguised as rules.
- **Identity vs behavior separation** — partially addressed. Philosophical/reflective content moved to FROM_TRINITY.md. Behavioral rules remain in prompt layer. Full separation not achieved but worst offenders resolved. ChatGPT's critique was architecturally sound — the tangling was real.
- **Prompt layer purpose** — after this audit: prompts should be behavioral rules and fetch-on-demand pointers only. Memory lives in files. Reflection lives in FROM_TRINITY.md. Identity lives in identity-category prompts (genuinely always-load behavioral orientation, not philosophical statements).
