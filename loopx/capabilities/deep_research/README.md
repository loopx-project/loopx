# Deep research (evidence-ledger research loop)

`/loopx-deepresearch` turns one user question into a bounded, auditable research
session: question, source, claim, and contradiction ledgers live in
`.loopx/deepresearch/research.json`, the packet (`loopx deepresearch status`)
owns what to research next and when to stop, and the final report keeps every
citation resolvable to a recorded source.

## Boundary with auto-research

The built-in `auto-research` capability and this capability both do "bounded
research" but own different truths and must not be merged casually:

| | auto-research | deep-research (this) |
| --- | --- | --- |
| Unit of work | a LoopX **goal** with role-scoped workers | one **session ledger** in a project |
| State authority | goal todos, hypotheses, rollout events | `.loopx/deepresearch/` ledger |
| Progression | worker contract + terminal decision/review | packet expeditions + stop conditions |
| Output | promoted/retired hypotheses in canonical evidence | citation-auditable markdown report |
| Use when | open exploration inside the LoopX control plane | a single user question needing an auditable, source-cited answer |

Choose one per question; they do not share state and neither can close the
other's work.

## Boundary with explore

The `explore` capability owns the goal-scoped, public-safe, cross-session
knowledge topology (questions, findings, evidence relations). deep-research owns
a private, session-scoped transactional ledger:

- deep-research keeps raw source locators (URLs or local paths that may not be
  safe to publish), claim lineage, precise contradiction pairs with sides-with
  adjudication, stop/close decisions, and the citation report. It works
  standalone — with no LoopX goal and no explore opt-in.
- explore keeps the canonical, reusable projection. Any future bridge is a
  one-way, idempotent, public-safe derivation: explore receives sanitized,
  opaque evidence refs only; it never reads the raw deep-research ledger or
  report, never dual-writes research state, and a graph node resolving never
  closes (or reopens) a research run.

In short: deep-research is the only writer of `.loopx/deepresearch/`, and
public surfaces only ever see derived, reconstructable projections.

One ledger invariant spans every transition that adjudicates a contradiction:
no resolution — inside `resolve-question` or standalone
`resolve-contradiction` — may overrule a claim that an already-answered
question cites as evidence. The ledger never silently invalidates a recorded
answer; late-arriving counterevidence that wins forces an explicit decision
(side with the cited claim, or close the run and revisit the question in a new
run) instead of leaving the report internally inconsistent.

## Lifecycle

`start` opens a run; `close` is the explicit terminal transition; the next
`start` archives the closed run (state + report) under
`.loopx/deepresearch/archive/<closed-at>/` and begins fresh. `start --new-run`
auto-closes only a run whose stop conditions already fired; an active run
always requires an explicit `close` first. State files are only ever rotated by
these typed transitions — never edited by hand.

## Layout

- Domain state machine and report (capability owner):
  `loopx/capabilities/deep_research/runtime.py`
- CLI adapter: `loopx/cli_commands/deepresearch.py` (`loopx deepresearch …`)
- Host entry: the `/loopx-deepresearch` skill facade installed by
  `loopx slash-commands --install`

Recorded claims carry caller-declared provenance: `--tool` records the tool the
caller says produced the evidence. The CLI records that provenance; it does not
attest that the tool actually ran — execution receipts belong to a future
host/harness bridge, not this ledger.

For HTTP(S) source deduplication, scheme and host are case-insensitive, and an
empty path is equivalent to `/`. Paths, queries, fragments, user information
and non-root trailing slashes remain significant. The recorded locator is never
rewritten. Local and opaque references keep their existing comparison behavior;
receipt-backed sources keep their exact-locator and evidence-replay rules.
