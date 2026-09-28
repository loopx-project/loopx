# LoopX Project Governance

This document defines the public project roles and decision process for LoopX.
It governs the repository and its releases. It is separate from LoopX runtime
concepts such as agent peers, todo claims, quota, gates, and write scopes.

## Current Maintainer

| Person | Role | Since | Public evidence |
| --- | --- | --- | --- |
| [`@huangruiteng`](https://github.com/huangruiteng) | Creator and lead maintainer | 2026-05-31 | [Initial public commit](https://github.com/huangruiteng/loopx/commit/7dcdc9dc79226d157ba57d3e8ff4bae664f020c1) |

The lead maintainer is currently the final decision maker for releases,
maintainer appointments, security-sensitive handling, and changes to this
governance model. That tie-break role should be revisited when the active
maintainer group grows.

Path-scoped subsystem appointments and preferred review assignments are
recorded below. A subsystem appointment does not by itself grant
repository-wide maintainer authority.

## Maintainer And Review Roster

This is the public list of everyone who can satisfy a review requirement on
`main`, and the paths they answer for. It is a snapshot audited on
**2026-09-26**; the matching routes live in [`CODEOWNERS`](CODEOWNERS) and a
change to either file changes both in one pull request.

| Account | Role | Scope | `CODEOWNERS` route | Since |
| --- | --- | --- | --- | --- |
| [`@huangruiteng`](https://github.com/huangruiteng) | Lead maintainer | Whole repository; governance, CI, releases, security handling, cross-subsystem decisions | Every path, and sole owner of `.github/`, `scripts/ci/`, release policy and technical directions | 2026-05-31 |
| [`@steven-kid`](https://github.com/steven-kid) | Subsystem maintainer | [Lark integration](#lark-integration) | Lark extension, its CLI delegates, Lark docs and focused tests | 2026-08-16 ([#3236](https://github.com/loopx-project/loopx/pull/3236)) |
| [`@maxliux5`](https://github.com/maxliux5) | Code owner | Frontend source under `apps/presentation/dashboard/` and chat bundle delivery | Dashboard source, `loopx/presentation/chat_bundle.py`, `scripts/chat_bundle*` | 2026-09-08 ([#4071](https://github.com/loopx-project/loopx/pull/4071)) |

Every other module is owned by the lead maintainer: `CODEOWNERS` gives each
top-level module an explicit line so that gap stays visible rather than hidden
behind the `*` fallback. First-review contacts in the
[table below](#first-review-responsibilities) route the first technical
response but do not satisfy code-owner review.

### Code Owner Eligibility

GitHub only honours code owners with repository write access, and a code-owner
approval can merge a change on its paths. A route is therefore added only when
all of the following hold, and the pull request adding it links the evidence:

1. the account has repository write access;
2. the account has publicly accepted a cohesive path scope, in
   [issue #4069](https://github.com/huangruiteng/loopx/issues/4069) or on the
   pull request that adds the route;
3. the account has completed at least three substantive cross-author reviews
   touching that scope in the preceding eight weeks, each naming the exact
   head, the governing invariant and the validation performed; and
4. the scope is a coherent set of paths, not every file the account has
   touched.

Authored-PR counts inform the choice of scope but never satisfy (3) on their
own. A code owner who gives no review in scope for eight weeks is asked
whether to keep, narrow or pause the route; the outcome is recorded here
through a pull request rather than inferred from activity. Contributors who
meet (2) and (3) without write access are recorded as first-review contacts
and may be proposed for write access.

## Review Service Levels

These are the review targets the maintainers commit to for pull requests from
contributors. They were set just below the response times actually observed
over the 90 days before 2026-09-26, so that they hold with the current number
of reviewers rather than describing a best week.

Business days are Monday to Friday in UTC+8, excluding public holidays there.
The clock starts when a pull request is opened or leaves draft, so a pull
request that sat as a draft before it was ready is measured from the moment it
left draft.

A "response" means a review, comment or merge by an account on the
[roster](#maintainer-and-review-roster) above — the accounts that can satisfy a
review requirement. Comments from anyone else are real conversation, but they
do not satisfy this target, and neither do bot accounts. Both facts come from
GitHub's typed actor data rather than from login names.

| Event | Target | Observed 2026-06-28 → 2026-09-26 |
| --- | --- | --- |
| First maintainer response: review, routing comment, or close with a reason | Within 2 business days | 90% within 2 business days; median about 1.5 hours, p90 about 13 hours (728 contributor pull requests) |
| Re-review after the author pushes and re-requests review | Within 2 business days | Not separately measured yet |
| Decision (approve, request changes, or close with rationale) once required checks pass and no review thread is open | Within 5 business days | Merge measured from the review clock: median about 6 hours, p90 about 2 business days |
| Security report acknowledgement | Within 5 business days, per [`SECURITY.md`](SECURITY.md) | Unchanged |

- A code owner or first-review contact who cannot respond within the
  first-response target hands the pull request to the lead maintainer, who
  is the fallback reviewer for every path.
- A pull request missing DCO sign-off or failing required checks still gets a
  first response pointing at the fix; the decision clock starts once checks
  pass.
- A pull request whose requested changes see no author activity for 14 days
  may be closed with a note. Reopening it, or opening a fresh one, is always
  welcome.
- These are targets, not a guarantee. They cover pull-request review only;
  support requests remain best effort, as described in
  [`SUPPORT.md`](SUPPORT.md).

`python3 scripts/review_sla_report.py --since YYYY-MM-DD` reproduces the
observed column and the cross-author review counts used for
[code-owner eligibility](#code-owner-eligibility). Its `--responder` option
defaults to this page's roster. The report reads one page of review and comment
history per pull request and prints how many records exceeded that page, so a
truncated period is disclosed instead of being read as "no response". Those
cross-author counts are raw public activity; they inform a scope but never
qualify an account on their own. Revisit the targets and the roster together,
at least every eight weeks.

## Repository Developers With Write Access

The following developers have, or have been invited to accept, GitHub's
repository `write` role. Write access supports day-to-day pull-request and
branch work within the repository rules. It does not by itself appoint someone
as a maintainer or grant release, security, or governance authority.

| GitHub account | Repository role | Access status |
| --- | --- | --- |
| [`@wujc12`](https://github.com/wujc12) | Write | Active |
| [`@ZaynJarvis`](https://github.com/ZaynJarvis) | Write | Active |
| [`@Hoey041`](https://github.com/Hoey041) | Write | Active |
| [`@maxliux5`](https://github.com/maxliux5) | Write | Active |
| [`@JackyCSer`](https://github.com/JackyCSer) | Write | Active |
| [`@steven-kid`](https://github.com/steven-kid) | Write | Active |
| [`@liubf21`](https://github.com/liubf21) | Write | Active |
| [`@wchwawa`](https://github.com/wchwawa) | Write | Active |
| [`@now-ing`](https://github.com/now-ing) | Write | Active |
| [`@cocolord`](https://github.com/cocolord) | Write | Active |
| [`@liuyizhe`](https://github.com/liuyizhe) | Write | Active |
| [`@Wanli-Lee`](https://github.com/Wanli-Lee) | Write | Active |

GitHub's repository settings are the operational source of truth for access.
This public snapshot should be updated through a pull request when a write-role
invitation is accepted, expires, or is revoked. Maintainer appointments remain
subject to the process below.

## Subsystem Maintainers

A subsystem maintainer is accountable for review quality and contract
coherence inside a named surface. The appointment does not grant authority
over unrelated subsystems, releases, security handling, repository settings,
or admin-bypass merges.

### Lark Integration

| Role | Account | Scope |
| --- | --- | --- |
| Subsystem maintainer | [`@steven-kid`](https://github.com/steven-kid) | Bundled Lark extension, its direct CLI delegates, Lark capability and integration documentation, and focused Lark validation |
| Lead maintainer and fallback reviewer | [`@huangruiteng`](https://github.com/huangruiteng) | Repository governance, cross-subsystem decisions, and review of changes authored by the subsystem maintainer |

The Lark integration maintainer is expected to:

- provide the first substantive response and design review for Lark pull
  requests;
- keep extension implementation, direct CLI delegates, public documentation,
  and focused validation consistent;
- protect authority, readback, owner-private receipt, retry, idempotency, and
  cross-platform boundaries;
- submit approval or change-request reviews on pull requests authored by other
  contributors; and
- escalate changes that alter shared extension lifecycle, status, quota, todo,
  release, security, or repository-governance contracts.

The appointment does not authorize the subsystem maintainer to approve their
own pull requests. A non-author reviewer must still approve those changes under
the repository rules. Merge, release, security, repository-settings, and
admin-bypass authority remain governed by this document and the lead
maintainer.

The matching paths are recorded in [`CODEOWNERS`](CODEOWNERS). The `main`
ruleset requires code-owner approval for owned paths. When a pattern lists
multiple owners, GitHub accepts approval from any one of them, not all of
them. Cross-subsystem decisions still require the lead maintainer's judgment.

### Shared Host Integration Seams

`@steven-kid` is a preferred reviewer, not the sole code owner, for the shared
host integration seams currently centered on:

- `loopx/host_loop_activation.py`;
- `loopx/host_mode_planner.py`;
- `loopx/cli_commands/host_mode_plan.py`; and
- `docs/integrations/runtime-connector-catalog.md`.

Host integration spans Codex, Claude Code, OpenCode, DeepSeek Harness, and
other runtime providers. Provider-specific implementation remains with the
relevant contributors and repository maintainers, while shared status, quota,
todo, scheduler, and Turn contracts remain outside the Lark appointment. These
host paths are therefore not assigned to `@steven-kid` in `CODEOWNERS` at this
stage.

### Changing A Subsystem Appointment

Adding, expanding, narrowing, or retiring a subsystem appointment requires a
public pull request that updates this document and any matching `CODEOWNERS`
routes. Contribution count alone is not sufficient evidence. The decision
should consider sustained technical judgment, cross-author review quality,
boundary discipline, responsiveness, and whether the proposed path scope is
cohesive.

## First-Review Responsibilities

Scope invitations and contributor confirmations are tracked in
[issue #4069](https://github.com/huangruiteng/loopx/issues/4069).

These assignments route the first technical response; they do not appoint a
repository-wide maintainer or transfer release, security, or bypass authority.
The lead maintainer remains the fallback. Contributor availability is
voluntary: anyone may decline, narrow, pause, or hand back a scope without
losing attribution for their work. Silence is not acceptance or approval.

Status reflects the public record in #4069 as of 2026-09-26. The last column
says what still separates each contact from a `CODEOWNERS` route under the
[eligibility rule](#code-owner-eligibility).

| Surface | Contact | Responsibility / status | Path to a code-owner route |
| --- | --- | --- | --- |
| Chat/runtime session lifecycle | [`@Duang777`](https://github.com/Duang777) | Accepted 2026-09-08: managed-session resume/submit/close, request idempotency, focused regressions and follow-up fixes. Shared goal, quota, lease and permission contracts remain outside this assignment. | Needs repository write access and three in-scope cross-author reviews. |
| Shared goal authority qualification | [`@wchwawa`](https://github.com/wchwawa) | Accepted 2026-09-08: implementation review, writer/cursor recovery evidence and bounded qualification. Canonical-authority promotion, provider activation and shared-state policy require separate lead-maintainer review. | Has write access and an accepted scope; needs three in-scope cross-author reviews. |
| Usage and host usage ingestion | [`@liubf21`](https://github.com/liubf21) | Invited to coordinate usage correctness and historical-data compatibility review; no acceptance recorded. Pricing policy and unrelated host/session authority remain outside this scope. | Has write access; needs scope acceptance and in-scope reviews. |
| Post-writeback hooks and reporting | [`@now-ing`](https://github.com/now-ing) | Write access active; invited to select one cohesive initial review scope. Authored work concentrates on post-writeback hooks and periodic reports, and substantive cross-author review comments exist on claim, lease and settlement changes. | Has write access and review history; needs to accept a cohesive scope. |
| TypeScript transaction migration | [`@hhyykk`](https://github.com/hhyykk) | Proposed paired review of complete transaction cutovers and Python/TypeScript parity; scope confirmation pending. | Needs scope acceptance, write access and in-scope reviews. |
| Task leases and scheduler boundaries | [`@yuefengw`](https://github.com/yuefengw) | Proposed paired review of lease lifecycle and boundary regressions; scope confirmation pending. | Needs scope acceptance, write access and in-scope reviews. |
| DSH integration | [`@wujc12`](https://github.com/wujc12) | Designated by the lead maintainer 2026-09-07 as first-review contact for the DSH plugin, installation and host-integration regressions; no acceptance recorded. Shared replan and lifecycle contracts stay separately reviewed. | Has write access; needs scope acceptance and in-scope reviews. |
| Reliability diagnostics | [`@songoow`](https://github.com/songoow) | Accepted 2026-09-09: observer-envelope and ledger integrity, receipt/projection consistency, readback and time-evaluation correctness. Privacy and first-write data boundaries stay separately reviewed. | Needs repository write access and three in-scope cross-author reviews. |

Start by linking a real cross-author PR, not by creating a quota of new
implementation work. A review should state the exact head, the governing
invariant, validation actually performed, and any unresolved boundary. Let the
author address findings before a maintainer takes over; record a necessary
takeover and preserve repair attribution.

Review the arrangement after roughly four weeks of participation or three to
five completed cross-author review cycles. Existing substantive reviews count;
there is no requirement to manufacture findings. Consider independent closure,
regression follow-up, scope control and sustainable availability, not PR count.
Accepted broader appointments and CODEOWNERS additions require a separate PR.
Review comments from contributors without Write are valuable technical input,
but do not replace the approval required from an eligible GitHub reviewer.

## Main Branch Merge Gates

The live [main ruleset](https://github.com/loopx-project/loopx/rules/18121976)
is the operational authority. It requires a pull request, one approving
review, code-owner approval where applicable, dismissal of stale approvals,
approval of the last push by another reviewer, resolved review threads, and
required status checks against an up-to-date base.

The required-check contract is `Sign-off` plus `merge-gate`, both bound to the
GitHub Actions app. `merge-gate` in `python-tests.yml` aggregates core
qualification. During initial rollout, activate that second check only after
the workflow is merged and both a code change and a documentation-only change
have produced the intended results. It must fail for missing, failed, cancelled
or unexpectedly skipped core jobs; only an explicitly classified
documentation-only change may skip those jobs. See the live ruleset for the
currently activated checks, rather than inferring activation from this file.

Only `@huangruiteng` retains the existing `always` bypass entry. Write access
does not grant bypass. A bypass is an exception, not a validation substitute:
record the exact head, reason, completed checks, known failures and recovery
plan in the PR. Neither agents nor access invitations create new bypass actors.

CODEOWNERS routes review, not directory-level write permissions. Unassigned
paths fall back to the lead maintainer; workflow, governance and release-policy
changes remain lead-maintainer-owned. Do not share owner credentials with
contributors or give automated reviewers broader credentials than necessary.

## Project Roles

### Maintainers

Maintainers may review and merge pull requests, publish releases, triage
security reports, and make repository governance decisions. They are expected
to protect compatibility, the public/private boundary, contributor trust, and
the quality of LoopX's control-plane contracts.

Maintainer authority is explicit: it comes from this document and repository
permissions, not from commit count, a runtime todo claim, or an agent role.

### Contributors

Anyone who improves code, tests, documentation, design, issues, or reviews is a
contributor. Accepted commits and co-authored commits are credited through the
public Git history and GitHub contributor views. Contribution does not by
itself grant merge, release, or governance authority.

### Agents And Automation

Agents and automation may prepare changes, run validation, or appear in commit
provenance. They do not become human maintainers and cannot grant themselves
repository authority. A human maintainer remains accountable for merges,
releases, and boundary decisions.

## How Decisions Are Made

- Routine changes use pull-request review, focused validation, and maintainer
  judgment. Silence is not approval when a change requires an explicit gate.
- Changes to persisted state, public contracts, defaults, permissions,
  evidence policy, or compatibility should explain the behavioral impact and
  include proportionate regression coverage.
- Significant product or governance changes should be discussed in a public
  issue or pull request before they are finalized.
- Security reports, credentials, private evidence, and other sensitive matters
  must not be posted in a public issue. Ask a maintainer for a private contact
  path without including the sensitive details.
- Releases are cut by a maintainer after the documented release checks pass.
  Exceptions and known skips should be recorded in the release or pull request.
- When consensus is not reached, the lead maintainer records the decision and
  rationale in the relevant issue or pull request.

## Technical Direction Governance

The versioned
[Current Technical Directions](../docs/project/technical-directions.md) page is
the canonical map of active strategic programs, maturity, contribution routes,
and promotion gates. The pinned
[GitHub Discussion](https://github.com/loopx-project/loopx/discussions/2851) is
its community-facing projection; an issue, Discussion, RFC, or integration
branch does not override merged runtime and stable reference contracts.

Each strategic direction has one long-lived tracking issue. Trackers record
outcomes, boundaries, implementation leads, material decisions, and links to
bounded work. They are not themselves blanket implementation authorization.
A claimable change should have a separate issue or public task-board row with
an explicit smallest slice, base branch, non-goals, and validation plan.

A material change to a direction's stage, scope, implementation lead,
integration branch, or promotion gate requires a pull request updating the
canonical map. The RFC index and contributor task board should change in the
same pull request when their routing changes. Maintainers update the pinned
Discussion after merge and should not maintain an independent roadmap body
there.

The `direction/*` labels route discovery and review. They do not grant
authority, promise delivery, or imply that a Draft or Research item is ready
for implementation. Recognition as an implementation lead records current
public work; it is separate from repository write access, subsystem maintainer
appointment, and repository-wide maintainer authority.

## Becoming A Maintainer

Maintainers are selected from contributors who have shown sustained technical
judgment, reliable review, respect for project boundaries, and care for other
contributors. An active maintainer nominates the candidate; the active
maintainers approve the appointment; and the change is recorded here through a
pull request.

A maintainer may step down at any time. Inactive or emeritus status, when
needed, should likewise be recorded in this file rather than inferred from
recent commit activity.

## Accountability And Scope

Important decisions should leave durable public rationale in an issue, pull
request, release, or stable project document. Private incident details and raw
agent trajectories do not belong in that public record.

This charter does not create a legal entity, employment relationship,
copyright assignment, or trademark registration. See
[Authors and Contributors](../docs/project/authors.md) for attribution,
[Name and Marks](../docs/project/trademarks.md) for name and mark usage, and
[`CONTRIBUTING.md`](../CONTRIBUTING.md) for the contribution workflow.
