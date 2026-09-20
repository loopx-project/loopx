## Goal And Delivered Outcome

<!-- Use a few concrete sentences; references are optional when the request or
regression is self-contained. State the accepted outcome, not a list of files.
For cross-cutting LoopX work, link the relevant overall-roadmap/domain acceptance
when useful. A roadmap id is not required for ordinary fixes or maintenance.
These are author facts; the review capability independently judges delivery.
-->

- Goal/source and gap:
- Observable before → after, with the validation row that proves it:
- Issue/task and intended base: <!-- Use Closes only for the issue actually completed; otherwise Related to. -->

## Scope And Continuation

<!-- A scoped fix may be complete while the parent program remains open.
For a staged increment, explain the useful delta, remaining gap, next owner/task
and why this is an independently testable/reversible boundary. Link existing
work before creating follow-ups. Docs/research/maintenance need a concrete value,
not a fabricated runtime caller. Write "complete within this scope" when no
successor is needed. Do not grade quality from LOC, PR counts or test counts.
-->

- Completed scope and remaining work:
- Slice boundary / successor: <!-- N/A with reason when the accepted task is complete. -->

## Validation

> Public-safe summaries only, including in HTML comments. Do not paste raw logs,
> private data or prompts, private screenshots, credentials, internal URLs, connection
> strings, or local paths. Use repository-relative commands without sensitive
> arguments, aggregate results, and already-public CI links. If evidence is
> private, report its category and limitations only; do not upload it to prove a claim.

<!-- Replace placeholders; do not leave every enum alternative selected.
Use one row per relevant check, including required checks not run or blocked.
Delete the example row. Add rows as needed; there is no required test-count target.
-->

- Tested revision: <!-- public commit SHA; identify older runs after a code change -->
- Run state: <!-- choose one: not_run | running | finished (not a claim of adequacy or success) -->
- Input classes: <!-- choose all used: none | synthetic | public_fixture | authorized_private_read_only; use none alone -->

| Check kind | Result | Public-safe evidence / limitation |
| --- | --- | --- |
| `unit` | `not_run` | Example only: repository-relative test path; behavior checked; aggregate outcome or generic reason not run. |

<!-- Check kind: static | unit | integration | real_entrypoint | real_backend | regression_parity | manual
Result: passed | failed | running | not_run | blocked | not_applicable
Evidence: name the affected behavior and safe command/CI link; for a real backend,
give only its product/version and isolation mode, never its address or credentials.
Mocks/in-memory substitutes are not real_backend. For regression_parity, summarize
baseline/head comparison and a failing-before or mutation check, not just test counts.
-->

- Coverage and gaps: <!-- Why do these checks cover the changed paths? Name untested paths,
  skipped/failed checks and follow-up, or "none identified" with a brief rationale.
  Documentation-only changes may use a static/manual row and explain runtime N/A.
  A passing row does not waive required real-path/backend gates. -->

See [validation disclosure guidance](https://github.com/huangruiteng/loopx/blob/main/CONTRIBUTING.md#validation-disclosure).

## Frontend / Visual Evidence

<!--
Required for dashboard, website, desktop, documentation chrome, or any other
user-visible UI change. Attach public-safe screenshots that let reviewers judge
the changed state, not only a happy-path landing screen. For an existing surface,
show both before and after. For a new surface, write "Before: N/A" and show the
after view. Include desktop and mobile when responsive layout changes, and include
loading, empty, error, permission, or gated states when those states changed. Add
a short recording only when still images cannot explain the interaction.

Use synthetic or public fixture data only. Do not upload live/private screenshots,
even when the underlying access was authorized.
-->

- UI impact: <!-- choose one: none | changed -->
- Before:
- After:
- States and viewports shown:
- Source data: <!-- choose one: none | synthetic | public_fixture -->
- Attention review: <!-- Per docs/development/design.md: what earns its place through high-value information, essential interaction, or expressive visual presentation? Name consolidation/removal across the whole viewport, and how failures, uncertainty, and one-step controls remain available. -->

## Type of Change

<!-- Mark the applicable options. -->

- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Refactoring (no functional changes)
- [ ] Documentation update
- [ ] Test update

## LoopX Area

<!-- Mark the primary area. Maintainers apply the matching GitHub label. -->

- [ ] Control plane (goals, todos, quota, scheduler, registry, runtime)
- [ ] Benchmark boundary (adapters, runners, verifiers, scoring, evidence)
- [ ] Capability or extension (providers, adapters, skills)
- [ ] Public docs or presentation surface (README, protocols, dashboard)
- [ ] Build, packaging, installer, or CI
- [ ] Host or runtime integration

## Technical Direction

<!-- Optional routing: Core control-plane hardening; Long-horizon benchmark evidence;
Operator surface and IM integration; Shared Goal Authority and cross-host coordination;
Architecture and research incubator. For cross-cutting work, reference an existing
roadmap S/G/R or domain acceptance id rather than copying the plan.
Routing is not maturity or implementation authority.
-->

- Direction / acceptance reference, when applicable:

## Shared-authority RFC fixture impact

<!--
Complete this section only when the PR claims progress against the TypeScript
control-plane migration or shared Goal Authority RFC. Otherwise write N/A.
Apply the same public-safe rules here. Reference the validation rows above rather
than attaching private fixtures, snapshot identifiers, raw output or infrastructure details.
-->

- Production-scale fixture schema:
- Semantic dimensions changed, or reviewed no-impact rationale:
- Provider conformance arms run:
- Read-only legacy/file/PostgreSQL three-arm rehearsal (required for promotion, runtime-routing, or compatibility-projection changes):

## Boundary Checklist

- [ ] Neither the diff nor this PR body/comments/attachments disclose private state, credentials, raw traces or verifier output, internal links, or local machine paths (including `.loopx/`, `.codex/goals/`, and live `ACTIVE_GOAL_STATE.md`).
- [ ] I did not duplicate maintainer-owned benchmark work unless a maintainer split out a public issue for it.
- [ ] I kept the change scoped to the linked issue/task.
- [ ] I completed the visual evidence section for UI changes, or marked UI impact `none`.
- [ ] Every commit includes a DCO `Signed-off-by` trailer (`git commit -s`).
