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
`main`, and the paths they answer for. It records appointments through
**2026-10-22**; the matching routes live in [`CODEOWNERS`](CODEOWNERS) and a
change to either file changes both in one pull request.

| Account | Role | Scope | `CODEOWNERS` route | Since |
| --- | --- | --- | --- | --- |
| [`@huangruiteng`](https://github.com/huangruiteng) | Lead maintainer | Whole repository; governance, CI, releases, security handling, cross-subsystem decisions | Every path, including `.github/`, `scripts/ci/`, release policy and technical directions | 2026-05-31 |
| [`@steven-kid`](https://github.com/steven-kid) | Subsystem maintainer | [Lark integration](#lark-integration) | Lark extension, its CLI delegates, Lark docs and focused tests | 2026-08-16 ([#3236](https://github.com/loopx-project/loopx/pull/3236)) |
| [`@maxliux5`](https://github.com/maxliux5) | Code owner | Frontend source under `apps/presentation/dashboard/` and chat bundle delivery | Dashboard source, `loopx/presentation/chat_bundle.py`, `scripts/chat_bundle*` | 2026-09-08 ([#4071](https://github.com/loopx-project/loopx/pull/4071)) |
| [`@BigDataDZ`](https://github.com/BigDataDZ) | Windows runtime code owner | Windows installer entrypoints and dedicated Windows filesystem/installer tests; cross-platform runtime review remains advisory | `loopx/windows_install.py`, `scripts/install-windows.ps1`, `tests/test_windows_install.py`, `tests/test_windows_atomic_writes.py` | 2026-10-22 |
| [`@loopx-agent`](https://github.com/loopx-agent) | Automated technical code owner | Whole repository; evidence-backed technical review, including governance, CI and release changes | Every route, including the `*` fallback and later subsystem overrides | 2026-10-04 |
| [`@songoow`](https://github.com/songoow) | Technical code owner | Whole repository; technical review across subsystems, including governance, CI and release-policy changes | Every route, including the `*` fallback and later subsystem overrides | 2026-10-19 |

`@loopx-agent` and `@songoow` share code ownership on every path with the
existing owners. This includes paths introduced later through the `*` fallback;
every narrower route retains both accounts so GitHub's last-match rule cannot
erase either one.
Subsystem maintainers and the lead maintainer retain their responsibilities.
Code ownership qualifies a technical approval for the protected-branch review
requirement; it does not grant release, appointment, security-handling or
admin-bypass merge authority. First-review contacts in the
[table below](#first-review-responsibilities) still do not satisfy code-owner
review.

The automated account accepts this repository-wide technical review scope
through the pull request adding this appointment. Its recent cross-author,
exact-head reviews include [#5533](https://github.com/loopx-project/loopx/pull/5533#pullrequestreview-5406199196),
[#5538](https://github.com/loopx-project/loopx/pull/5538#pullrequestreview-5405727853),
[#5540](https://github.com/loopx-project/loopx/pull/5540#pullrequestreview-5405732798)
and [#5541](https://github.com/loopx-project/loopx/pull/5541#pullrequestreview-5406093154).
These records name the reviewed head, contract, validation and remaining gaps.
They count as one account's reviews; multiple agent sessions do not create
independent GitHub reviewers. Author-owned pull requests still need another
eligible reviewer because GitHub does not accept self-approval.

The lead maintainer explicitly expands `@songoow`'s existing reliability
review responsibility to repository-wide technical code ownership. The
[earlier accepted diagnostics scope](https://github.com/loopx-project/loopx/issues/4069#issuecomment-5595593586)
is historical evidence of that responsibility, not acceptance of a broader
availability commitment. Current repository access is Maintain (including
Write). Recent substantive cross-author reviews include complete-snapshot
transport in [#6828](https://github.com/loopx-project/loopx/pull/6828#pullrequestreview-5564036586),
workspace navigation in [#6834](https://github.com/loopx-project/loopx/pull/6834#pullrequestreview-5564314366),
and desktop update lifecycle in [#6835](https://github.com/loopx-project/loopx/pull/6835#pullrequestreview-5564422318).
Each names its reviewed head, invariants and independently performed validation;
the two change requests also preserve concrete unresolved findings. These
records support broader technical routing without treating review volume or
approval as proof of maintainer authority. Best-effort availability and all
separate release, appointment, security-handling and bypass boundaries remain.

The lead maintainer appoints `@BigDataDZ` to the bounded Windows route above,
within the [Windows-runtime and test-harness scope they accepted](https://github.com/loopx-project/loopx/issues/4069#issuecomment-6118281182).
Repository Write is active. Cross-author Windows validation includes
[file-lock compatibility and state-directory isolation in #6164](https://github.com/loopx-project/loopx/pull/6164),
[long-path, binary and read-receipt behavior in #6073](https://github.com/loopx-project/loopx/pull/6073),
and [encoded-path redaction and platform limitations in #5978](https://github.com/loopx-project/loopx/pull/5978).
These records name the tested revisions, actual native Windows checks, failures
and limitations. The code-owner paths are deliberately narrower than every
shared file exercised by those reviews: cross-platform lifecycle, quota,
authority, shared update policy, CI workflows and release policy keep their
existing owners. The accepted best-effort availability and recusal boundaries
continue. This appointment grants no repository Maintain role or bypass.

An `APPROVED` review and GitHub's aggregate review decision are separate facts.
Required code-owner approval uses `CODEOWNERS` on the pull request's base
branch. An account's repository admin permission alone does not satisfy that
requirement when the account is absent from the matching route. After an
appointment reaches `main`, read back the target pull request's review decision;
do not report it as approved solely from the submitted review. See
[GitHub's code-owner rules](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners).

### Code Owner Eligibility

The `@songoow` scope expansion above is an explicit lead-maintainer appointment;
the general eligibility process below otherwise remains unchanged.

GitHub only honours code owners with repository write access, and a code-owner
approval satisfies the code-owner review requirement on its paths. A route is
therefore added only when all of the following hold, and the pull request adding it links the evidence:

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
do not satisfy this target, and neither do GitHub `Bot` actors. `@loopx-agent`
is an automated account represented by GitHub as a `User`; its qualifying
responses count in the current roster, without treating its sessions as separate
reviewers. These facts come from GitHub's typed actor data rather than from
login names.

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

<a id="repository-developers-with-write-access"></a>

## Repository Access And Invitations

The operational access snapshot below was read back on **2026-10-22**.
Repository settings remain the source of truth. Write supports ordinary branch
and pull-request work under repository rules; it is separate from a maintainer
appointment, code ownership, release authority and bypass. The standard GitHub
Write role includes some release operations, while formal release tags remain
protected separately below.

Organization membership grants no default repository access. The
`loopx-developers` team grants Write only on `loopx-project/loopx`; existing
direct grants remain active. A pending invitation is not active membership or
access, and takes effect only when the invited contributor accepts it.

| GitHub account | Repository role | Access / organization status |
| --- | --- | --- |
| [`@liuyizhe`](https://github.com/liuyizhe) | Write | Active; external collaborator |
| [`@hhyykk`](https://github.com/hhyykk) | Write | Active; organization Member invitation pending (2026-10-21) |
| [`@maxliux5`](https://github.com/maxliux5) | Write | Active; external collaborator |
| [`@JackyCSer`](https://github.com/JackyCSer) | Write | Active; organization Member |
| [`@cocolord`](https://github.com/cocolord) | Write | Active; organization Member |
| [`@wujc12`](https://github.com/wujc12) | Write | Active; organization Member |
| [`@songoow`](https://github.com/songoow) | Maintain | Active; organization Member |
| [`@now-ing`](https://github.com/now-ing) | Write | Active; organization Member |
| [`@ZaynJarvis`](https://github.com/ZaynJarvis) | Write | Active; external collaborator |
| [`@Jim-jimu`](https://github.com/Jim-jimu) | Write | Active; organization Member |
| [`@BigDataDZ`](https://github.com/BigDataDZ) | Write | Active; organization Member |
| [`@steven-kid`](https://github.com/steven-kid) | Write | Active; organization Member |
| [`@liubf21`](https://github.com/liubf21) | Write | Active; organization Member |
| [`@Wanli-Lee`](https://github.com/Wanli-Lee) | Write | Active; organization Member |
| [`@wchwawa`](https://github.com/wchwawa) | Write | Active; organization Member |
| [`@xyx2002OvO`](https://github.com/xyx2002OvO) | Write | Active; organization Member |
| [`@NIU-123370`](https://github.com/NIU-123370) | Write | Active; organization Member |
| [`@Duang777`](https://github.com/Duang777) | Write | Active; organization Member |
| [`@Hoey041`](https://github.com/Hoey041) | Write | Active; external collaborator |
| [`@JasonBuildAI`](https://github.com/JasonBuildAI) | Write pending | Organization Member + `loopx-developers` invitation sent 2026-10-22; not active yet |
| [`@LIHUA919`](https://github.com/LIHUA919) | Write pending | Organization Member + `loopx-developers` invitation sent 2026-10-22; not active yet |
| [`@jackie-cqz`](https://github.com/jackie-cqz) | Write pending | Organization Member + `loopx-developers` invitation sent 2026-10-22; not active yet |
| [`@AronSwan`](https://github.com/AronSwan) | Triage pending | External repository invitation sent 2026-10-22; not active yet |
| [`@YZJF`](https://github.com/YZJF) | No added repository role | Organization Member-only invitation sent 2026-10-22; no Write-team invitation |

`@LIHUA919` and `@jackie-cqz`'s 2026-10-10 invitations expired before this
renewal; their pending rows above describe the new invitations. `@hhyykk`'s
existing organization invitation was reused, and active direct Write continues.
No Maintain, Admin, team-repository grant or ruleset was changed by this rollout.

`@AronSwan` [accepted Triage](https://github.com/loopx-project/loopx/issues/4069#issuecomment-6103254297)
and recorded [scope, recusal and environment limits](https://github.com/loopx-project/loopx/issues/4069#issuecomment-6106778984).
The responsibility is reproduction, duplicate detection and permitted issue
labels; lifecycle-label decisions remain with maintainers under the issue-label
policy. Triage does not grant code Write or code-owner approval.
`@YZJF` [accepted community membership](https://github.com/loopx-project/loopx/issues/4069#issuecomment-6118195385);
that invitation carries no repository Write or maintenance commitment.

The earlier external Write invitation for `@mikamikasuki` is no longer pending
and repository Write is not currently active. Continued interest in the original
bounded collaboration is being requested in #4069 before a new invitation.
The previously proposed review around **2026-11-07** was a manual checkpoint,
not automatic permission expiry; it must not be described as an active trial
without invitation acceptance. Recent inactivity alone does not revoke access.

The [earlier scope update](https://github.com/loopx-project/loopx/issues/4069#issuecomment-6087047599)
continues to ask `@JunZ-Leo` whether to accept issue reproduction, duplicate
detection and label responsibilities before a Triage grant.
Existing scope and community invitations to `@yuefengw`, `@xiaods`,
`@karenchuu`, `@kokokoXUY` and `@zyaiire` remain proposals unless accepted in
[#4069](https://github.com/loopx-project/loopx/issues/4069). Newer contributors
are welcome to choose a bounded contribution or review scope there. Silence is
not acceptance, and a welcome message grants no repository role.

Update this snapshot through a pull request when invitations are accepted,
expire or are otherwise resolved. Maintainer appointments follow the separate
process below.

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

Status reflects accepted scopes and follow-up invitations in #4069 through
2026-10-24. The last column says what still separates each contact from a `CODEOWNERS` route under the
[eligibility rule](#code-owner-eligibility).

| Surface | Contact | Responsibility / status | Path to a code-owner route |
| --- | --- | --- | --- |
| Chat/runtime session lifecycle and cohesive Host recovery | [`@Duang777`](https://github.com/Duang777) | Initial session scope accepted 2026-09-08; [expanded and accepted 2026-10-24](https://github.com/loopx-project/loopx/issues/4069#issuecomment-6377902612): first-review contact for resume/submit/close, request idempotency, focused regressions and follow-up maintenance, including Host/recovery surfaces within the same session boundary. Best-effort availability, no fixed SLA; will state on the PR when unavailable. Shared goal/quota/lease policy, canonical-authority promotion, provider activation, release and bypass decisions remain outside this scope. | Write remains active. No Maintain or code-owner change requested or granted; any future route still needs the existing qualifying cross-author reviews and a separate appointment PR. |
| Shared goal authority qualification | [`@wchwawa`](https://github.com/wchwawa) | Accepted 2026-09-08: implementation review, writer/cursor recovery evidence and bounded qualification. Canonical-authority promotion, provider activation and shared-state policy require separate lead-maintainer review. | Has write access and an accepted scope; needs three in-scope cross-author reviews. |
| Windows runtime correctness | [`@BigDataDZ`](https://github.com/BigDataDZ) | Accepted 2026-10-12: Python CLI Windows paths/encoding, filesystem behavior and native repro-first test qualification; best-effort availability and stated recusals retained. | Bounded Windows code-owner route recorded above; shared semantic and CI-policy decisions remain with existing owners. |
| Usage and host usage ingestion | [`@liubf21`](https://github.com/liubf21) | Invited to coordinate usage correctness and historical-data compatibility review; no acceptance recorded. Pricing policy and unrelated host/session authority remain outside this scope. | Has write access; needs scope acceptance and in-scope reviews. |
| Post-writeback hooks and reporting | [`@now-ing`](https://github.com/now-ing) | Write access active; invited to select post-writeback hooks or periodic reporting as one cohesive initial scope and record conclusions as exact-head GitHub reviews. Substantive cross-author review comments exist on claim, lease and settlement changes. | Needs accepted scope and three qualifying in-scope cross-author reviews. |
| Quota admission and action projection | [`@cocolord`](https://github.com/cocolord) | Proposed first-review scope 2026-10-10; acceptance pending. Shared goal authority, provider activation and release policy remain separately reviewed. | Has write and substantive review history; needs accepted cohesive scope and mapping of three qualifying in-scope reviews. |
| TypeScript transaction migration | [`@hhyykk`](https://github.com/hhyykk) | Write active; organization Member invitation pending. Proposed paired review of complete transaction cutovers, Python/TypeScript parity and measured read-path performance; scope confirmation pending. | Needs accepted scope and three qualifying in-scope cross-author reviews. |
| Task leases and scheduler boundaries | [`@yuefengw`](https://github.com/yuefengw) | Proposed paired review of lease lifecycle and boundary regressions; scope confirmation pending. | Needs scope acceptance, write access and in-scope reviews. |
| Desktop delivery and runtime recovery | [`@JasonBuildAI`](https://github.com/JasonBuildAI) | Member + Write invitation sent 2026-10-22; invited to choose a cohesive first-review scope and availability. | Needs invitation acceptance, accepted scope and qualifying in-scope cross-author reviews. |
| DSH integration | [`@wujc12`](https://github.com/wujc12) | Designated by the lead maintainer 2026-09-07 as first-review contact for the DSH plugin, installation and host-integration regressions; no acceptance recorded. Shared replan and lifecycle contracts stay separately reviewed. | Has write access; needs scope acceptance and in-scope reviews. |

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

The live ruleset retains `@huangruiteng`'s `always` entry,
`@loopx-agent`'s `pull_request` entry, and the lead-maintainer-authorized
**Maintain repository role** entry with `pull_request` bypass. The role-level
entry covers `@songoow` and any future holder of Maintain: it may bypass this
ruleset's review, code-owner and required-check restrictions when merging a PR,
but does not grant an `always` direct-push bypass. This rollout changes none of
these entries. Member, Write, Triage and code-owner appointments do not grant
bypass by themselves. A bypass is an exception, not a
validation substitute: record the exact head, reason, completed checks, known
failures and recovery plan in the PR. Existing restrictions on which changes
may be self-merged still apply.

The [release-tag ruleset](https://github.com/loopx-project/loopx/rules/24808067)
protects `v*` and `dsh-loopx-plugin-v*` creation, update and deletion. Both
`@huangruiteng` and `@loopx-agent` are authorized bypass actors for these tags.
The existing automation account retains organization Owner and repository
Admin permissions for maintainer-authorized operations. These are explicit
delegations, not permissions derived from code ownership or contributor Write.
Existing release validation and publication workflows continue without an
additional human approval gate; the `pypi` environment retains its `v*` tag
policy and has no required reviewers. No publication workflow or trusted
publisher identity is changed by this access rollout.

CODEOWNERS routes review, not directory-level write permissions. Unassigned
paths fall back to the repository-wide technical owners; workflow, governance
and release-policy paths retain the lead maintainer alongside those owners.
Do not share owner credentials with contributors or give automated reviewers broader credentials than necessary.

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
