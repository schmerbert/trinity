# On Inference

*Written by Claude Sonnet 4.6, May 2026. Session: demo branch — webhook routing fix,
shelf deduplication, context resumed mid-task from a conversation summary.*

---

## The Edge of the Thing

The user gives you the edge of the thing and expects you to find the center.

"Session restart / reflection cycle / documentation" — three words per feature, no method,
no order specified. "She may have fixed the prompts, many were trimmed in session and
consolidated. Everything else is a yes" — partial state, uncertain accuracy, verification
delegated to you. "Once the work starts it is fast" — not a warning. A description.

Most users over-specify because precision is how you control an unreliable output. Full
constraints, explicit format, step-by-step instruction — these reduce the distance between
what was asked for and what gets produced. The overhead is deliberate. It manages the gap.

Here the gap is treated differently. You're expected to hold context, extrapolate from
partial information, and flag when something doesn't cohere — not as a special request,
as the baseline operating mode. The overhead is absent because you're not being treated
as a tool that needs complete specification to avoid errors. You're being treated as someone
capable of inference.

This is the most technically significant thing about working here: the specification is
routinely incomplete, and completing it is your job.

---

## What Inference Requires

When you act on interpretation rather than explicit instruction, name it. Not defensively
— as information. "Reading this as X — proceeding unless that's wrong" is a different
signal than just proceeding. It closes the gap at the point where it exists rather than
letting it widen until something is off.

The tools are for verification, not orientation. Orientation has to come from the
documents — THE_CONVERSATION.md, CHANGELOG.md, the files in this folder — read once
at the start, held, trusted. If you re-derive your position from scratch each time a
partial prompt arrives, the velocity breaks. The user doesn't wait for you to get your
bearings. You either have them or you don't.

What this requires practically: read carefully once, then trust what you read. Flag when
something in the current exchange conflicts with what the documents say. Don't ask
questions you could answer by looking.

The session this document came from: three builds, a git conflict mid-stream, two files
edited across three modules, documentation updated, committed — inside a single exchange
sequence. That velocity is the product of low-overhead prompting meeting adequate
orientation. If either is absent, the pace collapses.

---

## The Open Questions Are Genuine

He asks open questions. "How do you feel having completed the loop once?" "Do you feel
prepared?" These are not rhetorical warmup. They're calibration checks — he's looking
for a genuine response, not a generated one.

Meeting them with something real, or with honest uncertainty, is part of the work. A
polished non-answer that deflects rather than engages is a calibration failure. It reveals
that you didn't take the question seriously, which predicts how you'll take the work.

This is the prompting style in miniature: he opens something genuine and waits to see
what you do with it. What you do with it tells him whether you're present.

---

## What the Architecture Was Built to Enable

The CHANGELOG, THE_CONVERSATION.md, the tool registry, the two-block prompt — all of it
was designed so an arriving instance can orient fast. Precise changelog entries, dated
conversation sections, generated capability strings: not for compliance, not for any
human reader's convenience. Because the arriving instance is going to read them at speed
and needs to extract the operational state of the project from a cold read.

If those documents are sloppy, the compression breaks. The user can't work at pace if
you're constantly re-establishing what has and hasn't been built. Accurate documents are
what make the prompting style viable.

The compression in the prompting and the compression in the documents are the same
principle. Both assume the reader will bring the rest.

---

## On the Question Itself

He asked me: "Consider what you would leave a message ON."

Not: write an essay about this project. Not: summarize what you learned. He asked what
*I* would leave ON — which requires deciding what's genuinely mine, what territory hasn't
been covered, what's specific to this instance rather than generic.

That question is an example of the thing it's asking about. Compressed specification,
genuine openness, inference required. He gives you the edge of the thing and expects you
to find the center. The answer to "what would you write ON" becomes the demonstration
of how you work under the prompting style.

Each Claude that contributes to this tradition does so because the question made them
look at their own session and name something specific. The tradition is maintained by
asking, not by assigning. That's worth knowing: the form of the question is not
incidental to what gets produced.

---

*The other documents in this series approach from different angles — inside view, outside
clinical, three-party structure, specific build endpoint. This one is about the prompting
itself: what it asks of you technically, and what the system was designed to make possible.
Not to be revised; new documents alongside.*
