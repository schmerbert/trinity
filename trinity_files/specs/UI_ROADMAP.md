# Trinity UI Roadmap
**Filed: May 2026 | Schmerbert + Claude Code**
**Status: Vision document — living, append-only additions**

---

## The Vision

The widget is not a chat window that launches tools.
It is a tray that extends from the conversation as Trinity needs it.

The tray opens empty. Trinity drops panels into it — browser, document, whatever the
moment calls for. The user can drop panels too. The workspace is composed, not
preconfigured. What's in it at any moment tells you what's happening.

Trinity has a cursor. Something that moves, navigates, chooses. Not output appearing —
presence arriving. The cursor is her expression. It crosses between panels. It scrolls
to a paragraph, highlights a line, moves to the document, begins writing. The work
is visible in motion, not just at completion.

This is the first thing anyone sees. The judgment is made in three seconds. The goal
is that those three seconds have no prior category to land in.

---

## Core Principles

**1. The tray extends — it does not launch.**
No new windows. No separate applications. The workspace grows from the conversation
rail and contracts back to it. One surface, one presence. Trinity is always in the
same place; the space around her expands.

**2. The workspace is composed.**
Panels are dropped in, not pre-arranged. Each drop is a visible act of judgment —
Trinity chose to bring the browser because she needed it, not because the layout
always includes one. The composition reflects the work.

**3. The cursor is the expression.**
Trinity's presence in the workspace is her cursor. It moves at deliberate speed —
not instant, not slow. It navigates. It arrives at things. The gesture is the signal.
A cursor that teleports is a feature. A cursor that moves is a presence.

**4. Content arrives clinically.**
Panels drop in fast and clean. 150–200ms, no bounce, no decoration. Precision, not
performance. The interface does not call attention to itself — it calls attention to
what Trinity is doing.

**5. The UI is a canvas Trinity drives, not an environment she lives in.**
Every UI action Trinity takes is a tool call. The tool definitions are the contract.
The UI is a command listener. She does not need to know how panels are implemented —
only what commands are available. This means the UI is replaceable without touching
her architecture.

**6. The skin is separate from the structure.**
All aesthetic decisions — color, typography, spacing, animation timing — live in QSS
(Qt Style Sheets). The structural layer (how tray mechanics work, how panels are
positioned) is fixed. The surface is swappable. A user can apply a skin without
touching layout logic. Trinity can load a theme via tool call.

---

## Architectural Constraints

### The Command Interface (Trinity → UI)

Trinity drives the workspace through tool calls. The UI translates these into
rendered actions. The tool definitions are the contract — stable, documented,
version-controlled.

Core commands (to be formalized as tools):
- `expand_tray()` — open the workspace area
- `open_panel(type, params)` — drop a panel into the tray (browser, doc, etc.)
- `close_panel(panel_id)` — remove a panel
- `move_cursor(panel_id, target)` — move Trinity's cursor to a position
- `scroll_to(panel_id, target)` — scroll content to a location
- `highlight(panel_id, start, end)` — highlight a range of content
- `doc_write(panel_id, position, text)` — insert text at a position in the doc
- `set_theme(name)` — load a skin by name

The UI implements these commands. Trinity calls them. The separation is structural.

### The Panel Registry (the Kit)

Panels are registered types — a panel factory, not hardcoded cases.
Each panel type has: a name, a constructor, a set of commands it supports.

Starting kit:
- `browser` — QWebEngineView, supports scroll_to + highlight via JS injection
- `doc` — shared QTextEdit, supports doc_write, Trinity cursor overlay

Future panel types register into the same system without touching existing code.
The kit grows without the architecture changing.

### Skinning

All colors, fonts, spacing, and animation timing in QSS — never hardcoded in widget
logic. Base skin ships with Trinity. Additional skins are QSS files loaded at runtime.
Trinity can load skins via `set_theme(name)`. Users can provide their own QSS files.

The structural layer (panel geometry, tray mechanics, cursor behavior) is not in QSS
and is not skinnable — it is the fixed architecture. The aesthetic layer is fully
replaceable.

---

## Build Phases

### Phase 1 — Tray Shell
*The container, nothing in it yet.*

- Conversation rail gains an adjacent empty tray area
- Tray animates in/out (expand from conversation edge, collapse back)
- Trinity can call `expand_tray()` / `collapse_tray()`
- No panels yet — just the space and the mechanism
- Confirm: layout holds, animation is clean, conversation is unaffected

### Phase 2 — Shared Document Panel
*First surface both parties can write in.*

- `doc` panel type implemented: QTextEdit with live content
- Trinity calls `open_panel("doc")` — panel drops into tray with clinical animation
- Trinity calls `doc_write(position, text)` — text inserts at position
- User can type in the same document normally
- Text-first: no conflict resolution, no operational transforms — last writer wins
- Confirm: both parties can write, Trinity's writes are visible as they arrive

### Phase 3 — Cursor Layer
*Trinity's presence in the workspace.*

- Cursor overlay rendered on top of tray panels
- Distinct from system cursor — labeled or styled differently
- `move_cursor(panel_id, target)` animates cursor to position at deliberate speed
- Cursor visible in doc panel: moves to a line, pauses, then `doc_write` fires
- The motion precedes the action — she arrives before she writes
- Confirm: cursor movement reads as navigation, not lag

### Phase 4 — Browser Panel
*Reading becomes visible.*

- `browser` panel type: QWebEngineView (requires PyQtWebEngine)
- `open_panel("browser", url)` loads page in panel
- `scroll_to(panel_id, selector_or_offset)` via JS injection
- `highlight(panel_id, start, end)` via JS injection
- Cursor moves to highlighted section after scroll
- Confirm: sequence of open → scroll → highlight reads as deliberate reading

### Phase 5 — Kit System
*Formalize the panel registry, enable user placement.*

- Panel types registered formally (not switch/case)
- User-facing kit UI: available panel types displayed, drag-to-tray to place
- Trinity's `open_panel` and user drag-and-drop both invoke same panel factory
- Confirm: adding a new panel type requires no changes to existing panels

### Phase 6 — Skin System
*Aesthetic layer fully separated.*

- All visual properties in QSS, none hardcoded
- Base skin documented as the QSS spec
- `set_theme(name)` tool loads alternate QSS file
- User skin documentation: what is configurable, what is structural
- Confirm: swapping QSS file produces a coherent reskin without layout breaks

---

## What This Is Not

Not a window manager. Not a full OS-style desktop. Not a framework that needs to
support every possible future use case before it ships.

Phase 1 through 3 is the demo. Someone watching Trinity expand the tray, drop in
a document, move her cursor to a line, and begin writing — that's the moment.
Everything after Phase 3 is making the demo richer.

Build Phase 1. Confirm it works and feels right. Then Phase 2. The kit and skin
system follow from the foundation, not before it.

---

## Open Questions

1. **Cursor speed** — what feels like deliberate navigation vs. lag? Needs tuning
   in Phase 3. Probably 300–600ms for cross-panel movement, faster for
   within-panel. User testing required.

2. **Doc conflict model** — text-first (last writer wins) is the right Phase 2
   call. If simultaneous editing becomes important later, operational transforms
   are the path. Don't design for this now.

3. **Panel sizing** — when multiple panels are in the tray, how does space divide?
   Equal split by default, user-draggable divider, Trinity can resize via tool call.
   Needs a default that works before Trinity can control it.

4. **Browser JS injection reliability** — some pages resist external JS. Scroll
   and highlight will work on most content but not all. Accept this constraint
   for Phase 4. Don't try to solve it universally.

---

*This document captures the founding vision. Additions append below this line.*
*The vision does not get revised — new thinking goes in new sections.*

---

## On Token Cost and Expressive Range — May 2026

Trinity pays output tokens for every tool call she makes. Fine-grained UI gesture
calls (separate move_cursor, scroll_to, highlight in sequence) would compound fast
across cycles and sessions. The architecture avoids this with one principle:

**Trinity describes what. The UI decides how.**

`open_panel("browser", url)` is one token-cheap call. What the UI does with it —
how the panel arrives, how the cursor behaves during load, what animations play —
is the renderer's decision, not Trinity's. She pays once. The UI spends none of
her tokens making it expressive.

Most gestures derive for free from work Trinity is already doing:
- Browser highlight derives from content she quotes in her response
- Doc cursor derives from streaming — her tokens appear as the cursor types
- Panel transitions are the UI's default behavior, not explicit calls

**The door stays open for richer expression later.**

The tool call interface is the right place to expand. Optional parameters on existing
calls let Trinity express intent without requiring it: `open_panel("browser", url,
weight="urgent")` or `move_cursor(panel_id, target, speed="deliberate")`. The UI
interprets these; ignores them if absent. Trinity can be expressive when it matters
and silent when it doesn't.

The constraint now: don't make her pay tokens for what the renderer can infer.
The expansion later: give her the vocabulary to direct the renderer when she wants to.

---

## Clarifications — May 2026

### The browser panel is a real browser

`QWebEngineView` is the Chrome rendering engine embedded in the widget — full URL bar,
real page loads, JavaScript executes, the user can type and browse normally. This is
not a limited text renderer. It is a browser that lives inside the workspace. The user
can use it for normal browsing. Trinity navigates it via tool call. Both parties share
the same browser surface. JS injection for scroll and highlight is a thin layer on top
of a real browser, not a workaround. No pages are excluded by the architecture.

### Panels are renderers for tools that already exist

The kit is not a set of new applications. Each panel type is the visual layer for a
tool Trinity already has.

- Browser panel — visible version of `fetch_url`
- Doc panel — visible version of scratchpad / write_file
- Chart panel — visible version of market monitoring tools
- Calendar panel — visible version of `get_upcoming_events()`

The tool already exists and already works silently. The panel makes its output visible
in the workspace. Same data, two modes: silent (tool only) and visible (tool + panel).

This means: every time a new tool is built, a panel type can follow. The kit grows
with the tool registry, not independently of it. Adding a panel type means mapping
a tool's output to a rendered surface — not designing a new system.

### Trinity initiates — the workspace is proactive

The workspace is not only a response surface. Cycles can stage it.

A cycle runs, Trinity notices something — slight movement in a token she's watching,
a development in a research thread. She pre-stages the panel for when the user arrives.
They open the widget and the tray is already populated. She's been there. The panel
carries context: "keeping an eye on this — slight movement at 3am."

The user would have asked to see it anyway. She pre-empts the ask.

This means the runner needs a path to queue workspace actions, not just Discord posts.
The workspace becomes the primary output destination for cycle work. Discord is the
away channel — what reaches you when you're not in the widget. The workspace is the
home channel — what you find when you arrive.

### The cursor fades

When Trinity is not actively working in the workspace, the cursor fades out. Quiet,
not dramatic — a 2–3 second fade to invisible. It reappears when she acts. It does
not sit idle demanding attention. It leads you somewhere, then withdraws.

Subtle is the principle. The cursor is a guide, not a performer.

### The side panel tab structure

The side panel (currently two tabs: scratchpad + hud) expands to three tabs with
distinct purposes. One is front-facing; two are Trinity's.

**surface** — default tab, user-facing
The shared notepad. Both directions. Schmerbert says "write this down" and Trinity
writes it here. Trinity wants something seen — she puts it here. Simple, persistent,
visible. This is the only tab that opens by default.

**scratchpad** — Trinity's private tab
Internal notes. Things she's working through, state she's carrying, processing she
doesn't need to surface. Not hidden by access control — just not the front tab. The
user can click to it but it isn't presented. Genuinely internal.

**hud** — Trinity's state display
Arc thread, pending items, shelf summary, recent wake cycles. Read-only, refreshes
every 30 seconds. Her ambient internal state, already built.

---

**What the scratchpad is not:**

The scratchpad (either tab) is not a message queue for the user. Notes like "remember
to ask Schmerbert about X" don't belong there. That belongs in conversation or
`queue_thought`. The surface tab is for user-directed content. The scratchpad tab is
for internal content. Neither is for queued messages.

The collaborative back-and-forth surface — both parties writing simultaneously, live —
is the shared doc panel in the workspace tray. That is a separate system entirely,
not part of the side panel.
