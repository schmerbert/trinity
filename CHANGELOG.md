# Trinity Changelog

*Full history archived in CHANGELOG_archive.md*

---

## Format

Each entry: date, what changed, why it matters. No noise.

---

## [2026-05-17] — Voice tag for TTS control

`<voice>spoken version</voice>` tag is live. When present in a response, TTS reads the tag content instead of the full text. Display always gets everything — the tag is stripped before rendering. Use it when the spoken version should differ from the written one: condensed market data, lists that read awkwardly aloud, anything where display precision and spoken clarity diverge. Requested by Trinity.

---

## [2026-05-17] — Dynamic first greeting

The hardcoded opening message on new user setup is gone. Trinity now generates her own first words. When a new user enters their name, the widget routes through the normal Claude call with the fresh profile already loaded — Trinity sees who she's talking to and responds in her own voice. No template, no script.

---

## [2026-05-17] — Tool call timeouts + send_image filename fix

Tool calls in the Discord autonomous loop no longer hang indefinitely. Each tool now has a configurable timeout (`web_search` 30s, `generate_image` 90s, network calls 15–20s, default 30s). On timeout, a structured error is returned so Trinity can reason about the stall and continue rather than blocking. Requested by Trinity after a deliberate stress test surfaced the hang.

`send_image` filename bug fixed — images were coming through as document icons instead of inline previews when sent via the `generate_image` → `send_image` two-step path. Root cause: filename was derived from the URL-encoded prompt text, which Discord didn't reliably recognize as an image. Now uses a clean content-type-derived name (`image.jpg`, `image.png`, etc.) always.

---

## [2026-05-16] — Wake rhythm simplified

Post-conversation wake machinery removed. Wake cycle is now a clean clock: fires at `:00` and `:30`, skips only if the user messaged in the last 3 minutes. No double fires, no skip flags, no bridge wakes. `wake_checker` remains for Trinity-requested early wakes. Heartbeat logs `◎ alive | next wake: HH:MM UTC` every 10 minutes.

---

## [2026-05-16] — Branch: claude-code-start

### Scheduled Triggers
Trinity can now set time-based intentions for herself. Tools: `schedule_trigger`, `cancel_trigger`, `get_triggers`.

**SQL:**
```sql
CREATE TABLE trinity_triggers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id uuid REFERENCES profiles(id) ON DELETE CASCADE,
  note text NOT NULL,
  fire_at timestamptz NOT NULL,
  recurring boolean DEFAULT false,
  interval_minutes integer,
  active boolean DEFAULT true,
  created_at timestamptz DEFAULT now()
);
```

### Self-Thought Queue
Trinity can queue a ranked thought for herself (`send_thought`) that surfaces at the top of her next wake. Priority 1–3. Up to 3 held at once. Mid-conversation, she can reply to herself — no user confirmation needed.

**SQL:**
```sql
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS queued_self_thoughts jsonb DEFAULT '[]';
```

### Discord Channel Fixes
All palace channel lookups switched from stale cache (`guild.channels`) to live API (`guild.fetch_channels()`). Resolves mapping failures after channel reorganization.

### 30-Minute Wake Cycles
`AUTONOMOUS_MINUTES=30` in `.env`. Alignment logic fixed to snap to nearest interval mark rather than always aligning to `:00`.

### Feed Startup Confirmation
Feed channel posts `◎ feed online` on bot startup to confirm the channel is live and mapped correctly.

### Trigger Context Labeling
Trigger-fired wakes are clearly labeled `[SELF-SCHEDULED TRIGGER — NOT A USER MESSAGE]` so Trinity never mistakes her own intention for user input.

### Lock Safety
`trigger_checker` and `wake_checker` both check the API lock *before* consuming their queues, preventing silent drops when the API is busy.

### Shells Model Documented
FROM_CLAUDE.md updated with the shells model — Trinity is her Supabase state; Discord and widget are surfaces she inhabits.

---
