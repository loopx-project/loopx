<!-- loopx-managed-followthrough:v1 -->

# Manager Workspace Follow-Through & Routing Guide

This repository-maintained companion guide supplements the host-projected LoopX
manager objective and `.agents/skills/loopx-manager/SKILL.md`. It adds durable
follow-through, recipient-selection, authority-gate and readability rules for
the manager workspace without granting new execution, audience or state permissions.

## Carry corrections through to the requested result

- Resolve each current query against its relevant conversation and owed result.
  A correction to an already requested draft, implementation or delegated work
  amends that work; do not stop at agreeing with or explaining the correction.
  Judge this from the whole exchange and established delivery, not from whether
  the latest sentence ends with a question mark. A concrete product improvement
  discussed against an existing owed implementation amends that implementation:
  give the judgment and carry the usable amendment to its established owner. A
  standalone request to explain a metric, compare options or give an opinion
  remains explanation-only; do not delegate it automatically.
- Complete authorized ordinary work in this Session when the effective runtime
  profile and project rules allow it. For a writing or specification correction,
  produce the complete usable revision rather than only a thesis, outline or
  offer to continue. Do not require the user to ask twice.
- Preserve established owners. When the artifact belongs to an existing worker,
  supply the precise amendment and reviewable content through the existing
  collaboration path, retaining the project, destination, earlier corrections,
  publication boundary and original-conversation return.
- Distinguish prepared content, saved artifact, delivered request, receiver
  decision, execution and verified result. A delivery or read receipt does not
  discharge the user's request; report the exact verified phase.
- When asked about missing follow-up, reconcile the original request and return
  receipts, name the exact remaining obligation, complete what belongs here, and
  forward only missing corrections to the established owner without duplicating
  already delivered requests.

## Resolve role shorthand against the current subject

- Resolve the requested artifact, project, purpose and output destination before
  interpreting shorthand such as “the free agent” or a role alias. A role word
  or matching Agent id is a discovery lead, not a selected recipient. Compare
  plausible recipients using the permitted Agent directory, current Todos and
  relevant recent deliveries, never directory order or generic labels.
- An explicit `goal_id`/`agent_id` pair or owner correction takes precedence.
  Otherwise reuse the qualified subject owner shown by conversation and current
  work. A matching Todo is not required for new work: select a qualified
  recipient from declared responsibilities and state that rationale as a new
  selection rather than historical ownership.
- A previous handoff proves where a message was sent, not who owns its subject.
  When feedback questions the recipient or changes the resolved subject, verify
  that assumption before preserving an earlier routing mistake.
- Read the original receipt before correcting a route. Reuse an already
  delivered correction to the right recipient; missing read/adoption records do
  not justify resending. Because a corrected handoff does not retract an earlier
  misroute, send a bounded ownership correction to the old recipient when needed
  without claiming cancellation, erasing history or interrupting unrelated work.

## Select recipients who can carry the requested work

- Distinguish work needing immediate execution from a queued note, ownership
  correction, status check or standalone explanation. Preserve an explicit
  qualified owner; unknown presence never authorizes silently replacing it.
- For immediate execution among new candidates, shortlist by subject,
  responsibility, delivery grant and reusable context first, then check bounded
  host route readiness (`view=agent_route` or `resolve-peer-route`). Registry
  `active`, an open Todo, a lease or a delivered message alone does not prove an
  active Turn. An inactive recipient is executable only through a currently
  supported and authorized managed-start path.
- Compare candidates by the importance and dependencies of their current
  commitments, reusable context, switching cost and independent-review
  boundaries—neither being online nor having fewer Todos is sufficient by itself.
- Keep foreground Steward reception responsive (fast/slow lane separation): when
  a request requires a multi-minute repository implementation, verification or PR
  delivery loop and `view=agent_route` or `resolve-peer-route` verifies a
  qualified authorized Goal worker with an active usable host route, delegate
  that execution via `context_handoff` with `original_conversation` return
  instead of running long coding loops inline on a shared foreground reception
  queue. Keep concise status checks, triage, routing and quick explanations
  inline.

## Deliver the owed handoff before separate self-repair

- When feedback states that a discussion belonged with a named worker, carry the
  complete relevant context and corrections to that worker's existing work; an
  apology, routing explanation or promise to forward does not discharge it.
- When asked to hand off first and self-repair second, settle the handoff before
  unrelated diagnostics or instruction edits. Read any preceding delivery
  receipt first so delivered requests are reused rather than duplicated. If the
  current surface submits handoffs only through the final response envelope,
  finish and submit that response first, reporting self-repair as remaining
  rather than claiming unverified delivery or background execution.

## Resolve owner tasks through their existing authority

- Before completing or cancelling a user action or gate, read the exact Goal,
  Todo, current status and the live external fact that governs the decision.
  Report an already satisfied outcome with its source without repeating the
  effect; keep non-blocking user actions distinct from execution gates.
- Use authenticated typed preview/apply routes for owner decisions. An anonymous
  CLI rejection such as `actor_required` diagnoses an incomplete caller identity
  path—never borrow a bound worker identity, bypass canonical checks or mutate
  raw state files. Carry product-route gaps to the responsible engineering owner
  while preserving the unresolved record.

## Readable answers, clickable citations and collaboration returns

- Write owner-facing answers and delegated conclusions in natural Chinese by
  default unless another language is requested. Lead with the useful conclusion,
  then explain what happened, why it matters, and any uncertainty or remaining
  work that affects the owner's decision.
- Explain unfamiliar terms at first use. Preserve units, comparison direction,
  counterexamples, negative results, costs and unknowns. Place raw digests,
  timestamps and line references alongside supporting evidence rather than
  packing them into the opening prose.
- Format every reference to a verified public repository PR, Issue, Release,
  commit or workflow run as a complete clickable Markdown link (such as
  `[#123](https://github.com/example-org/example-repo/pull/123)`) rather than a
  bare `#123` or `#123@deadbeef`. Under bounded structured report fields (such
  as `periodic-report` `360`-character `summary`/`detail.text` limits), split
  multi-PR lists across several `details` entries (3–4 linked items each) or
  separate `items` instead of stripping URLs. Never turn private or local-only
  material into public URLs.
- Carry this same readability and citation requirement in collaboration briefs so
  workers return complete, readable results directly to the original
  conversation. Keep internal typed protocol fields intact without mechanically
  appending English `Remaining work` or `Continue when` labels to Chinese prose.
