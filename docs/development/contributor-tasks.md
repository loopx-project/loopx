# Contributor Task Board

This board is the public, contributor-facing projection of LoopX work.
It is intentionally different from `.local` active goal state:

- this file lists public work that can be discussed, claimed, reviewed, and
  validated in the repository;
- `.local`, `.loopx`, and live `ACTIVE_GOAL_STATE.md` files remain local
  runtime data for maintainers and automation;
- private benchmark traces, verifier output, raw agent sessions, credentials,
  internal document links, and local machine paths must not be copied here.

Landed context, the product-manager cut, the maintainer progress log and the
Turn Loop Controller plan moved to
[contributor-tasks-history.md](contributor-tasks-history.md) on 2026-09-26 so
that this board only lists work that is still open and anchored.

## Task Admission Rule

A row is claimable only when it names **one anchor** and **one gap**:

| Anchor type | What to cite | Example |
| --- | --- | --- |
| Roadmap | An S stream, G milestone or R card in the [overall roadmap](../architecture/rfcs/loopx-overall-roadmap-v0.md), plus the sentence in that card that the task closes | `R1 · "confirmed commitments survive materialization"` |
| RFC obligation | An RFC in [docs/architecture/rfcs](../architecture/rfcs/README.md) with status Accepted or Active, plus the section or invariant the task implements or pins | `shared-goal-authority-state-provider-v0 · §lease transfer` |
| Reproduced adoption defect | A public issue with a reproduction from a real install, first run, upgrade, host or platform, opened by or confirmed with a user | `#4941 Windows non-UTF-8 locale` |

The **gap** states who is blocked without the change and what they can do
after it. The **exit** states when the row closes. A row without all three is
not a task; it is a discussion.

The following are **not tasks on their own**. They are accepted only as part of
an anchored row that consumes them:

- a new entry in the [interaction pattern catalog](../concepts/interaction-pattern-catalog.md)
  (see its admission note);
- a test that pins current behavior without reproducing a public defect or an
  RFC invariant named in the row;
- another dimension, provider arm or fixture row on an existing conformance
  fixture without a named invariant it newly covers;
- renaming, re-wording or restating existing docs, vocabulary or protocol text;
- a module split, extraction or "own X in its own module" change without a
  budget, boundary test or caller it unblocks.

Maintainers route such proposals to a Discussion or fold them into the row that
needs them. Review credits the gap closed, not the PR count; release notes list
contributors by anchored rows.

## Status Legend

| Status | Meaning |
| --- | --- |
| Available | Ready for a contributor to claim the linked outcome and deliver a cohesive PR. |
| Claimed | Someone has said they are working on it, or a maintainer assigned it. |
| Maintainer-owned | Active work is happening in maintainer/local automation; ask before touching. |
| Needs design | Discussion is welcome, but implementation needs agreement first. |
| Blocked | Waiting on a decision, dependency, or maintainer writeback. |
| Done | Completed; moved to [history](contributor-tasks-history.md). |

## How To Claim Work

For first-review contacts, accepted subsystem owners and voluntary scope
confirmation, use the [governance review roster](../../.github/GOVERNANCE.md#first-review-responsibilities).
Request the closest contact before escalating to the lead maintainer. A
preferred review contact is not an exclusive task claim or new merge authority.

1. Prefer a linked GitHub issue. If there is no issue yet, open one with the
   contributor task template; it asks for the anchor and the gap.
2. Comment that you would like to work on the task. Maintainers will mark it
   `claimed` or agree a complete, independently verifiable slice.
3. For docs-only typo fixes or obviously tiny cleanups, opening a direct PR is
   fine.
4. If a claimed task has no update for 14 days, maintainers may release it back
   to `Available` after one ping.
5. If a task is `Maintainer-owned`, do not duplicate the work. Ask whether
   there is a public helper slice instead.

Before claiming a row, reconcile its anchor with latest main, related PRs and
the roadmap. Historical rows are not proof that a missing feature remains
unimplemented or a proposed slice is still useful. The
[Technical Directions map](../project/technical-directions.md) owns contributor
routing and current maturity; this board does not keep a second copy.

## Lane A: Adoption Defects (P0)

Real installs, first runs, upgrades and hosts are the demand signal LoopX has
the least of. A reproduced defect from one of them outranks any internal
polish. Anchor: S12 release/DX hygiene and G0/G5 in the roadmap, plus the
issue.

| ID | Anchor | Gap and exit | Validation | Status |
| --- | --- | --- | --- | --- |
| GH-A01 | S12 · [#4941](https://github.com/loopx-project/loopx/issues/4941) | Windows users on a `cp936`-style locale hit structured state files read or written with the locale codec; #4338, #4942 and #4997 fixed the reported sites and main now has no bare `read_text()` / `write_text()` under `loopx/`. The gap is that nothing stops the class from returning. Exit: a repository guard test fails on any text-mode file I/O under `loopx/` without an explicit encoding, the reporter confirms on a `cp936` host, and #4941 closes. | New guard test next to `tests/test_connector_registry_encoding.py` and `tests/test_runtime_subprocess_utf8.py`; `python3 -m pytest -q` on both | Available |
| GH-A02 | S4/S10 · [#3927](https://github.com/loopx-project/loopx/issues/3927) | A Codex App automation that silently stops delivering scheduled prompts is indistinguishable from a healthy quiet goal; the owner learns days later. Exit: `loopx doctor` or the heartbeat readback reports a missed-delivery window as a typed diagnosis with the last observed tick. | `python3 -m pytest -q tests/test_doctor_installation_scope.py` plus a missed-window fixture; `loopx doctor --deep` readback | Needs design |
| GH-A03 | S3 · [#4336](https://github.com/loopx-project/loopx/issues/4336) | A 0.4.3 install saw `installed` in a progress summary matched as `stalled` and got `autonomous_replan_required` on a healthy goal. Typed progress observations replaced that prose matcher after #3161, but no public regression pins the contract, so a compatibility path could bring it back silently. Exit: a `quota should-run` regression with `installed` / `uninstalled` / `installation completed` summaries emits no no-progress trigger, a typed no-progress observation still does, and the issue records the upgrade guidance. | New focused pytest plus `python3 examples/control_plane/autonomous-replan-no-change-smoke.py` if present; `loopx check --scan-path loopx/quota` | Available |
| GH-A04 | S5 · [#4381](https://github.com/loopx-project/loopx/issues/4381) | Users have to open the dashboard to learn why a task is blocked and what unblocks it; the projection already knows. Exit: the existing blocker projection is delivered through the goal channel / status readback with the recovery action, without a second notifier. | `python3 examples/issue-fix-outcome-projection-smoke.py`, focused status readback smoke | Needs design |
| GH-A05 | S12 · [#4800](https://github.com/loopx-project/loopx/issues/4800) | Default local state lives under `.codex`, which confuses non-Codex hosts and upgrades. Exit: an agreed default path and an explicit, reversible migration command; no silent move. | Design note first; then `python3 examples/loopx-update-smoke.py` and an install smoke on a clean profile | Needs design |

Closed defects that show the lane's shape: #4155 and #4997 (Windows), #4012
(macOS 26.5 app), #3796 / #3867 / #4195 (DSH plugin install), #4051
(PyInstaller skills), #4082 (skill version marker), #4892 (symlinked runtime
root). New reports go through the bug-report or first-run templates and are
promoted here when reproduced.

## Lane B: Roadmap Gaps (R1 / R2 / G1)

These rows close a sentence in an R card. The roadmap's first resource
ordering is R1 commitments and recovery, then G1 small-team qualification.

| ID | Anchor | Gap and exit | Validation | Status |
| --- | --- | --- | --- | --- |
| GH-R2A | R2 · "one steward drives 2–3 bound managed workers" / [#4574](https://github.com/loopx-project/loopx/issues/4574) [#4339](https://github.com/loopx-project/loopx/issues/4339) | Team-plan confirmation writes commitments but the R1 confirmed-team commitment/readback gap is not closed, so R2 real peer dependency handoff cannot be qualified. Exit: independent readback distinguishes all-gap / partial / stale / rejected / committed; then one real two-worker dependency cycle. | Maintainer-agreed slice first; R1 counterexample fixtures, then packaged frontend and CLI readback | Needs design |
| GH-C47 | S3/S4 · task-lease facade parity | Task leases back Turn fencing and typed acquire/release; the Pi facade (#3559) is the only external host that advertises them. Exit: one more real host integration (for example TraeX) advertises the capability, preserves soft-claim routing, exposes acquire/renew/transfer/release outcomes and proves overlapping write scopes fail, without making `quota should-run` enforce undeclared lease authority. | `python3 examples/control_plane/task-lease-runtime-smoke.py`, `python3 -m pytest -q tests/control_plane/test_task_lease.py tests/test_loopx_turn_driver.py`, host-focused fake fixture | Available |
| GH-C88 | S12 · "reduce review effort for useful changes" / [#2881](https://github.com/loopx-project/loopx/issues/2881) | Default CLI summaries exceed the hot-path payload budget on one command family, so agents spend tokens on readback. Exit: shorter default summaries with a typed `--json` escape hatch on that family; budgets and differential allowances intact. | `python3 examples/control_plane/cli-output-budget-regression-smoke.py`, focused command smoke, `loopx check --scan-path docs/status-data-contract.md` | Available |
| GH-C35 | S4 · runtime connector boundary | Design the next provider-neutral external-host adapter on top of LoopX Turn and TurnEnvelope, using the shipped external worker, Pi, and TraeX routes as conformance examples rather than special cases. Map compact session events into requests, planned effects, committed receipts, independent validation, recovery, and attention items while keeping raw transcripts, credentials, billing, permissions, and product frontstage outside LoopX. Exit: a public design note with an adapter-neutral fake-host smoke plan. | `loopx check --scan-path docs/integrations/runtime-connector-catalog.md --scan-path docs/development/contributor-tasks.md` | Needs design |

## Lane C: RFC Obligations

Rows here implement or pin a named section of an Accepted or Active RFC. Name
the section; "extend the fixture" is not an obligation.

| ID | Anchor | Gap and exit | Validation | Status |
| --- | --- | --- | --- | --- |
| GH-C89b | [goal-direction-baseline-v0](../architecture/rfcs/goal-direction-baseline-v0.md) · §7 synthetic case F2 | The RFC landed in #4172 at milestone M0 (design note plus fixture plan F1–F8, no code). Nothing yet proves the M0 claim that a revision change exposes `re_evaluation_required` while inputs, Vision, Todos, leases and Goal route stay byte-identical. Exit: synthetic case F2 is implemented as a public-safe fixture with the RFC's key allowlist, and the RFC's milestone table advances past M0 or records why not. | New focused smoke or pytest implementing F2; public-boundary scan; `loopx check --scan-path docs/architecture/rfcs` | Available |
| GH-R5A | R5 / [shared-goal-authority-state-provider-v0](../architecture/rfcs/shared-goal-authority-state-provider-v0.md) · D2 durability / [#4224](https://github.com/loopx-project/loopx/issues/4224) [#3245](https://github.com/loopx-project/loopx/issues/3245) | The SQLite local authority candidate has capacity profiles but no crash/replay boundary qualified against the D2 acceptance in the RFC. Exit: one crash-during-commit and one replay-after-partial-write case pass on the real backend with the RFC's stated outcome, or the RFC records why D2 changes. | `npm run test:control-plane`; the isolated SQLite integration suite | Needs design |
| GH-C102 | [shared-goal-authority-state-provider-v0](../architecture/rfcs/shared-goal-authority-state-provider-v0.md) · one named invariant | Extend the shared production-scale coordination fixture only for an accepted RFC invariant or a reproduced public regression that the current envelope does not cover, naming the invariant section in the PR. Update the checked-in envelope, shared generator and an independent negative or mutation assertion; run the same dimension through every affected provider arm. Do not add a dimension because the fixture accepts one. Exit: the named invariant has a passing positive case and a failing mutation case on every affected arm. | `npm run test:control-plane`; for PostgreSQL, `LOOPX_TEST_POSTGRES_URL="$DISPOSABLE_POSTGRES_URL" npm run test:postgresql-authority-store`; `loopx check --scan-path tests/fixtures/control_plane --scan-path tests/control_plane_ts` | Available (named invariant required) |
| GH-B01 | S11 / [long-horizon benchmark research program](../architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md) · treatment integrity / [#3243](https://github.com/loopx-project/loopx/issues/3243) | Adapter-fidelity and treatment-integrity gaps are asserted but not reproducible from public fixtures. Exit: one reproducible gap with the existing focused fixtures, reported without live scoring. | Deterministic fixtures in `tests/capabilities/test_benchmark_toolkit.py`; no live runs | Needs design |

## Retired Task Generators

These rows were open-ended and produced volume without a gap. They are closed
as standalone tasks; the work they described is admitted only through an
anchored row above.

| Former ID | Was | Why retired | Where the work goes now |
| --- | --- | --- | --- |
| GH-C37 | "Curate the interaction pattern catalog with one new public-safe case" | Eight IP entries landed in three weeks with no consuming controller, dashboard copy or test named; the catalog is 3,300 lines and its own admission note now applies. | A new IP is part of the anchored row that consumes it; cite the code, test or copy that reads the pattern. |
| GH-C102 (open form) | "Extend the shared coordination fixture with one more dimension" | Dimensions were added because the fixture accepted them, not because an invariant was uncovered. | Kept as GH-C102 above with a named RFC invariant required. |
| Progress-log "contributor implication" hints | "Add one synthetic fixture / negative case / walkthrough" | Hints named a shape, not a gap; they generated test-pin PRs. | Read them in [history](contributor-tasks-history.md); open an anchored issue if a hint maps to an RFC invariant or defect. |

## Maintainer-Owned / Coordination Required

Visible work that should not be duplicated. Ask for a public helper slice
instead of launching private runs or broad product changes.

| ID | Area | Task | Validation |
| --- | --- | --- | --- |
| GH-C72 | workflow runtime | The pure Turn Loop Controller and its fail-closed repair remain maintainer-owned even though host-loop activation, the external worker, Pi, TraeX, and typed settlement are shipped. Do not duplicate the controller. Public helpers may independently review decision-table semantics or propose synthetic malformed-receipt/cross-host fixtures; do not launch hosts, alter scheduler ownership, or weaken validation to make a candidate pass. | Maintainer-run focused controller pytest, LoopX Turn transaction tests, autonomous-replan and bounded monitor no-change smokes, and risk-based premerge canary |
| GH-C67 | issue-fix | The first operator rendering of `issue_fix_outcome_projection_v0` is an active coordination lane. Do not build a competing case ledger or operator surface. Ask for a synthetic fixture, accessibility, or projection-parity helper slice that keeps provider, sink, and private notification state out. | `python3 examples/issue-fix-outcome-projection-smoke.py`, the selected public surface smoke, and `loopx check --scan-path loopx/capabilities/issue_fix --scan-path docs/development/contributor-tasks.md` |
| GH-C101 | dashboard | Dashboard Chat turn completion and single-command startup remain maintainer-owned live fixes (#3758): retryable Codex app-server Turns must render replies and one supported command must bring status backend plus UI up together. Do not build a competing dashboard or chat route; ask for a synthetic app-server protocol fixture or a regression smoke instead. | Maintainer-run packaged dashboard smoke, real app-server protocol v2 flow, and `loopx check --scan-path apps/presentation/dashboard --scan-path docs/development/contributor-tasks.md` |
| GH-C18 | benchmark | Long-horizon benchmark evidence program, including live local no-upload cases, runner contracts, trace retention, score accounting, and good/bad case attribution. Do not duplicate live runs or inspect private artifacts unless maintainers split out a public helper issue. | Maintainer-run benchmark ledger and public/private scan |
| GH-C19 | benchmark | Main-table SkillsBench product-mode comparison: raw Codex autonomous max5 versus the qualified LoopX Turn route, no verifier feedback to either arm, stop on reward 1 or declared done. Scoring stays held until a fresh task-free runner lifecycle receipt proves readiness. Live matched pairs and official/countable receipt review remain maintainer-owned; external contributors can help with synthetic schema, docs, reducers, and smokes only. | Maintainer-run readiness receipt, compact ledger, case-analysis update, and public receipt/boundary scan |

## Projection Sources

This board is maintained from public-safe projections of:

- the local `loopx-meta` Agent Todo list;
- the [overall roadmap](../architecture/rfcs/loopx-overall-roadmap-v0.md) R
  cards and G milestones, the [RFC index](../architecture/rfcs/README.md) and
  public issues with reproductions;
- recent maintainer review of which work is externally claimable versus
  maintainer-owned live automation.

Projection rules:

- copy the task intent, not private evidence details;
- convert private benchmark runs into public helper slices unless maintainers
  explicitly publish a runnable issue;
- mark live benchmark, release, and automation lanes as `Maintainer-owned`
  when duplicate work would waste compute or weaken evidence;
- every claimable row carries an anchor, a gap and an exit; rows without them
  are not published.

## Suggested Labels

Use the public label taxonomy in `docs/operations/pr-issue-labels.md` when
opening or triaging issues:

- Lifecycle labels: `good first issue`, `help wanted`, `triage`,
  `workflow-audit`, `bug`, `enhancement`, `duplicate`, `question`,
  `invalid`, and `wontfix`.
- Area labels: `control-plane`, `benchmark-boundary`, `capability-extension`,
  `public-docs`, and `build-or-ci`.

Board states such as `claimed`, `maintainer-owned`, `needs design`, and
`blocked` are board statuses, not GitHub labels. Track them in issue comments
and through the `triage` or `workflow-audit` lifecycle labels.

## Maintainer Update Rules

- Keep this board curated: at most 25 claimable rows across Lanes A–C. Move
  landed rows to [history](contributor-tasks-history.md); move lower-priority
  work into GitHub issues.
- Every claimable row names an anchor (roadmap S/G/R sentence, RFC section, or
  reproduced public issue), a gap (who is blocked) and an exit (when it
  closes). `examples/docs-governance-smoke.py` checks the anchor column.
- Do not publish private/local state. Summarize it into a public task only when
  the work is safe for the repository.
- After a meaningful internal milestone, update this board manually if there is
  a new contributor-sized slice; put the landed context in history, not here.
- Remove or refresh stale tasks instead of leaving obsolete "good first issue"
  entries in place.
