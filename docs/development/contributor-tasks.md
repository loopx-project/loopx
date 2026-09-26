# Contributor Task Board

This board is the public, contributor-facing projection of LoopX work.
It is intentionally different from `.local` active goal state:

- this file lists public work that can be discussed, claimed, reviewed, and
  validated in the repository;
- `.local`, `.loopx`, and live `ACTIVE_GOAL_STATE.md` files remain local
  runtime data for maintainers and automation;
- private benchmark traces, verifier output, raw agent sessions, credentials,
  internal document links, and local machine paths must not be copied here.

The goal is to make important work discoverable without turning the repository
into a mirror of maintainer scratch state.

## Status Legend

| Status | Meaning |
| --- | --- |
| Available | Ready for a contributor to claim the linked outcome and deliver a cohesive PR. |
| Claimed | Someone has said they are working on it, or a maintainer assigned it. |
| Maintainer-owned | Active work is happening in maintainer/local automation; ask before touching. |
| Needs design | Discussion is welcome, but implementation needs agreement first. |
| Blocked | Waiting on a decision, dependency, or maintainer writeback. |
| Done | Completed and ready to archive from this board. |

## How To Claim Work

For first-review contacts, accepted subsystem owners and voluntary scope
confirmation, use the [governance review roster](../../.github/GOVERNANCE.md#first-review-responsibilities).
Request the closest contact before escalating to the lead maintainer. A
preferred review contact is not an exclusive task claim or new merge authority.

1. Prefer a linked GitHub issue. If there is no issue yet, open one with the
   contributor task template.
2. Comment that you would like to work on the task. Maintainers will mark it
   `claimed` or agree a complete, independently verifiable slice.
3. For docs-only typo fixes or obviously tiny cleanups, opening a direct PR is
   fine.
4. If a claimed task has no update for 14 days, maintainers may release it back
   to `Available` after one ping.
5. If a task is `Maintainer-owned`, do not duplicate the work. Ask whether
   there is a public helper slice instead.

## Current Technical Directions

The [overall roadmap](../architecture/rfcs/loopx-overall-roadmap-v0.md) and
[tracking issue #4574](https://github.com/huangruiteng/loopx/issues/4574) own
cross-domain priorities and G0–G5 acceptance. The [Technical Directions map](../project/technical-directions.md)
owns contributor routing and current maturity; this board does not keep a second
copy of those stages. Before claiming a row, reconcile its linked task with
latest main, related PRs and the roadmap. Historical rows are not proof that a
missing feature remains unimplemented or a proposed slice is still useful.

A claimable task names the current gap, independently useful outcome, existing
owner/caller, dependencies and decisive validation. For a staged increment,
record the remaining gap and next owner/task; do not make a field, fixture or
PR count the completion target. Preserve existing authoritative Todo/issue
identity rather than copying the whole plan here.

## Priority Queue

| Priority | Direction | Slice | Issue / PR | Status |
| --- | --- | --- | --- | --- |
| P0 | Core hardening | Exact-head review of remote execution and terminal writeback fencing: fenced journal recovery absorbed into TypeScript | #3074 | Done |
| P0 | Core hardening | Wire caller-approved `validation_command` into the remaining self-report entry points | #3082 / #3142 #3291 #3343 | Done |
| P1 | Benchmark evidence | Qualify a reproducible adapter-fidelity or treatment-integrity gap with existing focused fixtures | #3243 | Needs design |
| P0 | Operator surface / IM | Close the R1 confirmed-team commitment/readback gap, then qualify R2 real peer dependency handoff | #4574 / #4339 | Needs design |
| P1 | Shared coordination | Qualify the selected local authority durability and crash/replay boundary against existing D2 acceptance | #4224 / #3245 | Needs design |
| P1 | Core hardening | One budget-aware CLI output ergonomics slice | #2881 | Needs design |
| P2 | Project docs | Release docs install, activation, and recovery guidance through v0.5.4 | GH-C04 | Landed via #3982 |
| P2 | Maintainability | CLI ownership and hot-module extraction | GH-C06 / #4659 #4803 #4818 | Done |

## Product Manager Cut

LoopX is converging from a control-plane library into a management surface for
long-running agent work. Product-capability contributions should prefer slices
that make existing kernel objects understandable to users instead of adding
another source of truth.

| Product slice | Current substrate | Contributor-sized next cut |
| --- | --- | --- |
| Management frontstage | Goals, todos, gates, claims, evidence, quota, run history, `goal_channel_projection_v0`, `task_graph_projection_v0`, `issue_fix_outcome_projection_v0`, `agent_management_projection_v0`, and same-source Explore views are already compact read models. The public homepage, hosted docs, and localized dashboard now expose these surfaces. | Translate the read models into stable operator concepts such as work item, owner, decision, evidence, budget, risk, and next action; preserve lineage, keep raw machine fields in drill-downs, and do not create a second task or case store. Changes to a public first viewport remain maintainer-preview work. |
| Conversational commands | The four canonical global manager CLI commands are shipped: `/loopx-global-summary`, `/loopx-global-gates`, `/loopx-global-todos`, and `/loopx-global-risks`; legacy `/loop-global-*` forms are only migration aliases. | Keep their focused read-only contracts and public-safe smokes aligned. `/loop-goal-summary` remains host-only and outside this contributor slice; do not invent another manager command or alias family. |
| Runtime connector modes | `host_mode_plan_v0` selects visible, isolated-headless, gateway, service, and hybrid modes over the connector catalog. Host-loop activation now covers Codex surfaces, Claude Code, OpenCode 1/2 goal loops, TraeX, Pi, Gemini, Cursor, DeepSeek Harness, and custom agents; a scheduler-hint-aware external worker demonstrates one signed headless route. LoopX Turn remains one isolated request/effect/receipt transaction rather than a recurring loop. | Add one provider-neutral parity slice for route preservation, skill delivery/readback, continuation deadlines, signed primary actions, or stage/receipt visibility. Keep host wake/process ownership outside LoopX core and do not create a second scheduler or duplicate controller. |
| Planner-worker mode | The experimental planner-worker contract now supports one bounded plan, one selected worker step, an allowlisted validation set, a clean-worktree boundary, and a typed receipt; the TraeX probe is only one extension provider. | Add provider-neutral usage and failure guidance around the shipped fake runtime. Keep model routing explicit, validation caller-approved, and recurring scheduling or broad multi-agent orchestration outside this mode. |
| Visible governance | Quota, scheduler hints, authoritative interaction contracts, decision scopes, user gates, peer claims, optional task leases, a repository change-window gate with a pending ledger (#3319), interface budgets, and provider-neutral PR program snapshots already exist in machine contracts. A shared-goal authority/state-provider RFC now defines the next coordination boundary without making the proposal runtime authority. | Show who can act, who must approve, which decision scope applies, what budget was spent, and how pause/override/terminate decisions map back to LoopX state; add one provider-neutral negative fixture proving a locked repository window rejects writes and pending ledger rows resolve with a typed lifecycle after merge or close. Keep proposal state, claims, leases, and PR program observations from becoming a new runtime hierarchy or write authority. |
| Decision and material quality | Decision Context and Material Lifecycle are experimental, built-in, default-off capabilities. They separate revision-bound evidence, advisory proposals, material planning, owner-gated apply, and private cursor/source state. | Build synthetic, no-provider walkthroughs that make these boundaries visible. Do not add private adapters, source bodies, provider payloads, or a second lifecycle store. |
| Memory and content workflows | Agent Turn Recall composes quota-selected work with scoped Reward Memory whose post-outcome utility attribution is advisory and read-only (#3280), while `content_ops_item_v0` preserves stable item identity, revision-bound approval, delivery/readback receipts, and supersession. Both remain advisory or preview-level and add no provider authority. | Add synthetic walkthroughs and negative fixtures that prove identity, revision, and failure boundaries. Keep provider payloads, draft bodies, credentials, raw sessions, and external writes outside LoopX state. |
| Extensions and change qualification | Standalone `extension init` scaffolding and managed zero-permission execution demonstrate optional provider delivery; a `loopx-repo-health` provider publishes public repository-health snapshots (#3272). Exact-diff Change Quality is separately goal-scoped, simplify-first, and enforced through fresh receipts when enabled. | Improve one existing provider or validation seam at a time. Do not invent a capability for installability, auto-run discovered repository tasks, or weaken exact-scope receipt checks. |

## Recent Maintainer Progress

These public milestones changed which tasks are still useful contributor entry
points:

| Area | Landed | Contributor implication |
| --- | --- | --- |
| Turn and settlement | Typed settlement now covers CLI, Codex App, task leases, and todo completion: caller-approved `validation_command` commits verified completion receipts (#3142), user-role done updates run the same declared gate (#3291, #3293), and MCP `complete_task` inherits that gate with a pinned negative fixture (#3343), closing GH-C85 / #3082. A shared typed settlement receipt-chain driver unifies replay (#3199), M7 parity fixtures plus a read-only journal inspection/`interpret_turn_journal` lens shipped (#3189, #3193, #3205), per-todo validation timeouts override the 20s default (#3210), and replan typed semantic exits settle exhausted-goal and future-monitor reentry (#3213). Failed-session recovery now resumes preserved sessions without weakening drift checks (#3262, #3266) and same-turn terminal closeout recovers (#3261), closing #3228; ambiguous quota-spend retries and terminal no-followup ordering are also settled (#3258, #3250). Receipt-bound monitor settlement now closes deterministically and governed continuous-monitor proposals settle in the Kernel (#3513, #3511); replaying a completed heartbeat turn returns `heartbeat_settled_skip` with no re-spend and no successor conflict when its completion, writeback, and spend receipts exist (#3578, closing #3567). Post-v0.5.3, settlement readback is consolidated into the typed TypeScript boundary with per-effect reduction cases pinned, successor Turn outcomes validate, non-completion terminal closeout is rejected, a turn survives capability re-entry, fenced journal recovery and terminal-writeback fencing absorb into TypeScript (#3074), Turn recovery decisions unify on Journal inspection (#3719), host Todo settlement cuts over to TypeScript (#3724), settlement readback facades retire (#3714, #3723), Vision refresh authority moves to TypeScript (#3720), read-only autonomous replan settlement lands (#3746), and delayed scheduler ACKs settle after autonomous replan (#3742). | Add receipt-chain drift or replay-identity negatives on the shared driver. No second settlement ledger, model call, or double quota spend. |
| Effect program runtime | A shared typed Effect Program drives quota, Turn, task-lease, and todo-completion settlement. The Dev Book course teaches the current runtime (#3097), M7.1 parity fixtures and the read-only replay lens shipped (#3189, #3193), and the turn driver is the second consumer of the settlement algebra. The TypeScript control-plane migration has entered its transaction payoff phase (#3447): settlement (#3464), delivery routing (#3481), the scheduler transition kernel (#3434), and todo completion (#3530) now cut over to typed TypeScript transactions, and receipt-bound phase classification moves into the typed quota boundary (#3578). Post-v0.5.3, host Todo settlement (#3724), native task-lease acquire (#3702), Vision refresh authority (#3720), and settlement readback facade cleanup (#3714, #3723) also move into the typed boundary, with governed-capability-lifecycle validation (#3706) and scheduler host follow-up (#3704) under review. The scheduler remains outside settlement. | Add receipt-chain drift or replay-identity negative cases on the shared driver; do not extract a shared executor until two adapters share execution ownership or build an interpreter protocol before both consume the same plan/receipt algebra. |
| Review quality | PR review now requires scope-fit evidence for production surface changes (#3090), the execution contract carries four self-dev review lenses (#3123), example-only PRs need durable smoke-value evidence (#3134), age-fair exact-head scheduling persists across restarts (#3317), durable projection ACK is required (#3744), and disproportionate changes are gated (#3731). | Add synthetic conformance and negative cases around scope-fit evidence, causal chains, exact-head review packets, durable projection ACKs, and durable-smoke-value claims. |
| Task leases | Typed task-lease CLI with preserved legacy error codes landed (#3095); on-disk hard leases surface in goal-channel projection (#3039); Turn fencing uses lease fences plus an append-only journal, and the OpenCode 2 goal worker fences its own live worker lease. A task-lease generation ABA fix shipped (#3393), a Pi `loopx_task_lease` facade over the shipped `task_lease_v0` CLI merged (#3559, closing #3549), task-lease settlement cut over to TypeScript (#3674), and native task-lease acquire cut over too (#3702). | Adopt the same facade in one more real host integration (for example TraeX) or add a transfer/overlap-write-scope fixture. Keep soft-claim routing and undeclared-lease authority unchanged. |
| Status, quota, monitors | Replan context is host-projected from the evidence ledger; two equivalent typed progress observations create an obligation, maintenance writes fail closed against the same full goal-frontier reducer used by quota, and an exact runnable-successor Todo carries the obligation-bound semantic receipt and turn boundary. Heartbeat todos survive capability reentry (#3321), Todo identity filtering and UTC ordering are corrected (#3311), unbound `/loopx` sessions inherit existing agent identity (#3315), declared validation gates run on user-role done updates (#3291, #3293), quota guards follow the selected Todo (#3506), and a bounded fallback action portfolio keeps quota decisions actionable (#3514). Guided start is bound to one turn (#3572), stale generated Next Actions rebind to the current todo (#3524), unknown workspace causality is repairable (#3519), malformed runtime recovery and settlement payloads fail closed (#3525), managed and queued turn creation are serialized (#3542, #3562), and guided selection packet consumption is explicit (#3707). Post-v0.5.3, `todo list` gained a bounded thin projection with an output-budget cap (#3679), the dashboard projects completed Todos with done-count run progress (#3689), guided-todo onboarding delta coverage shipped (#3716), agent-lane Next Action selection prefers the selected Todo over shared prose (#3693), typed blocker outcomes settle (#3734), Turn receipt priority wins in compact output (#3726), and CLI common command owners load lazily (#3717). Manual evidence reads, prose ACKs, and historical repair-delta claims are diagnostic only. Compact scheduler-hint and heartbeat-prompt budgets plus todo-detail cold paths remain the reference. | Extend one measured performance, detail-readback, lock-timeout, malformed-state, typed progress, or obligation-bound semantic-transition case. Keep default output bounded and cold-path detail available. |
| Governance and productization | A synthetic visible-governance slice landed (#3086) and its Stage-2 proposal-vs-shipped refresh keeps additive `claim_work` / coordination-head truth plus a lease-as-fence negative explicit; decision-context evidence cursors settle (#3079); the React homepage rebuild (#3098) landed; a deterministic project registry (#3170) serializes global sync; per-goal handoff mode gates claim/lease authority (#3164) and hard-lease gates auto-acquire completion keys (#3198); a repository change-window gate and pending ledger ship (#3319); goal channels default to human-gate auto-notify on new channels (#3523); coordination state rules are centralized (#3410) and the Stage 2 slice ships an aggregate head, file provider, and `claim_work` executor (#3529); project repository delivery lands through capability hooks (#3570) with gitless delivery workspaces settled (#3574) and CPA/provider-routing qualification recorded (#3576, #3573, #3563); a read-only stride shadow observation M1 (#3207), a synthetic stride-boundary shadow fixture (#3290), and the hierarchical stride RFC (#3204) open the next boundary; goal-artifact lifecycle projection (#3136) proposes a read-model boundary and post-outcome memory utility attribution from RFC #3215 is implemented (#3280). Post-v0.5.3, periodic-report post-writeback hooks ship (#3691) with terminal Todo closeout dispatch (#3748), pending-intent consumption (#3749), Chinese-analysis editorial requirement (#3750), rejected-draft reopen (#3751), actual work interval (#3754), approved delivery bound to the Goal Channel Bot (#3755), and normalized card readback (#3757); the dashboard retains startup error context (#3745), keeps the default status source same-origin (#3732), and avoids an unbound lark-cli variable (#3738); doctor validates deep checks by install kind (#3736); Frontstage Pages PR validation is isolated (#3728); Luna ships as a bounded account-ring extension (#3737); and structured outbound mentions are verified (#3741). | Add one synthetic lifecycle or stride-boundary fixture, extend the visible-governance walkthrough with a missing negative case, or characterize the shipped `claim_work` executor with a file-backed parity fixture. Keep activation explicit and leave source bodies, draft bodies, review text, provider payloads, private locators, cursor state, and apply/publish authority outside public fixtures. |
| Security boundaries | Four merged hardening fixes contain state-file override writes (#3140), reject shell metacharacters in launcher worker commands (#3139), validate `goal_id` in reward routes (#3138), and stop ACAO:* on unauthenticated status reads (#3137). Loopback CORS, worker-command charset, and state-file symlink containment are pinned as regression tests (#3340). GH-C90 audited the four shipped negative fixtures and added one durable mutation per boundary: path-prefix sibling containment, worker-command input redirect, absolute `goal_id` path segments, and `file://localhost` ACAO rejection (`tests/test_state_file_containment.py`, `tests/test_worker_command_validation.py`, `tests/test_feedback_goal_id_validation.py`, `tests/test_status_server_cors.py`), completing the GH-C90 slice (#3655, closing #3636). | Keep credentials, private reproduction details, and advisory coordination out of public fixtures; extend only when a new shipped boundary lacks a distinct fail-closed mutation. |
| Runtime connectors and content workflows | DeepSeek Harness Turn adapter with real e2e smoke landed (#3188); OpenCode 1 and OpenCode 2 continuous goal loops ship (#3151); content-ops gained a layout template library and dense-cover defaults (#3222, #3223); provider-neutral PR program snapshots ship (#2814); `computer_use_runtime_v0` is now a machine-checkable protocol contract (#3279); a `loopx-community-discussion` public source provider (#3299) and a `loopx-repo-health` provider (#3272) extend public source coverage; goal-channel botmux runtime integration lands with terminal dispatches preserved and uncertain dispatches persisted, desktop chat routes through a loopback service, chat-wide routing and contextual inbox replies land (#3555), and managed and queued turn creation are serialized (#3542, #3562); the desktop added workspace language settings, localized write previews, and preserved heartbeat schedule and off-hours semantics (#3594); host parity, skill-delivery, and observable-handle thin pytest now cover the expanded host list. Post-v0.5.3, bot document comment scopes read from the published app (#3722), turn-start reaction replay is bounded (#3733), and the DSH plugin is one-step ready (#3725). | Add one provider-neutral parity slice for DeepSeek Harness, OpenCode 1/2, goal-channel botmux, content-ops delivery/readback, or desktop locale parity; keep raw transcripts, provider payloads, credentials, and host-local paths out of fixtures. |
| Benchmark boundary | Benchmark research was reset around native runners (#3267): native Codex Goal connects to the real runtime (#3271), workers are isolated from the host (#3277), provider env binds to installed Goal profiles (#3276) with formally verified treatments (#3275) and live continuations (#3273), runtime evidence binds to the exact container (#3303), a post-run case analyst brief ships (#3289), an integrity qualification toolkit landed (#3241), source-env usage is distinguished from credential probes (#3298, #3278), and accountable closeout requires Todo validation (#3229). A provider-neutral four-arm study contract ships (#3516), alongside restricted task-source access (#3504), non-http git clones classified as network with loopback integrity probes allowed (#3510), namespaced public case ids (#3532), locked git-clone integrity boundaries (#3515), orchestrator runtime provenance, and fail-closed runtime closeout drift (#3503). Deterministic fixtures already cover GH-C99 without live scoring or uploads: `tests/capabilities/test_benchmark_four_arm_contract.py`, `tests/capabilities/test_benchmark_experiment_board.py` (orchestrator runtime provenance negatives), `tests/capabilities/test_benchmark_runtime_continuity.py` (fail-closed closeout drift), and integrity/network/task-source cases in `tests/capabilities/test_benchmark_toolkit.py`; the covered GH-C99 fixture task was retired (#3653). Public native Goal trajectory summaries now derive from compact lifecycle facts without retaining raw artifacts (#3327), completing the GH-C16 slice. Post-v0.5.3, an external agent phase ships (#3565), restricted-access suspicion adjudicates by causal use (#3747), the plan-role fidelity gate is removed (#3753), and retired fidelity references are cleaned up (#3756). Shared lifecycle, readiness, ledger, and reducer contracts remain the public seam; generic Effect Program conformance and replay tests harden settlement infrastructure but do not change benchmark scoring or authorize live runs. Live scored comparisons stay held until a fresh task-free runner lifecycle receipt proves readiness. | Extend synthetic setup/termination attribution, derive the public trajectory summary for a second non-SkillsBench adapter, or add a SWE adapter only when a second SWE route needs shared launch/observe/ingest behavior. Do not launch scoring, duplicate the controller, or expose raw task text, logs, trajectories, verifier tails, credentials, uploads, or local paths. |
| Validation and change quality | Python tests are green on the latest runtime-bearing `main` change; public smoke parity and the frontstage Pages build are restored, with Codex App fallback receipt identity isolated (#3233); public smoke reliability was restored again (#3302), stargazer history now fetches through REST (#3320), and a repository-hygiene smoke plus release-timeline ratchet landed (#3249). The Auto Research KNN evidence-normalization smoke now gates improved/contradicted status and protected-scope checks on semantics-derived public eval fixtures rather than wall-clock speedup, completing GH-C77. Post-v0.5.3, the remaining public smoke boundaries are repaired (#3740) and public smoke fixtures are fixed (#3739). | Retain negative/mutation coverage on deterministic fixtures, and distinguish infrastructure outages from product regressions. Keep live model/provider checks explicit and low-frequency. |
| Release and install | v0.5.4 is the latest public tag and package version (v0.5.0-v0.5.4 landed after the last board refresh). PyPI remains the default complete install path (#3301) with explicit installation ownership (#3566); canonical project links are published (#3253), derived capability/manpage surfaces are synced (#3254), exact Miaoda releases are verified in periodic-report receipts (#3508) with governed delivery (#3401), extension doctor readiness recovers across releases (#3556), doctor deep checks validate by install kind (#3736), and the public release timeline covers v0.1.3 through v0.5.4. | Keep install, activation, and recovery guidance aligned with the PyPI default and tagged stable versus post-tag `main`, and continue contributor-safe update recovery without adding a parallel release checklist. |
| Public docs and onboarding | Hosted docs, a public homepage, the Dev Book, localized dashboard copy, public/private boundary examples, GitHub issue forms, and a PR/issue label taxonomy have landed. Slash-command installation now exposes all four canonical global manager commands through their shipped CLI wrappers; fresh-project onboarding and its regression fixture landed (#3093, #3103), and harness-above positioning (#3202), ecosystem adoption and derivatives inventory (#3224), GitHub maintenance and ops automation best practices (#3227), long-horizon/commercialization strategy docs (#3217-#3220), the TypeScript control-plane migration RFC (#3226), the open strategy review process (#3295), stale-issue reminders without auto-close (#3297), welcome-all-contribution-shapes guidance (#3294), consolidated community policy (#3238), Apache-2.0 open-core adoption (#3235), a capability implementation code map (#3252) with co-located docs (#3265), outcome/extension path clarifications (#3242), the NoKV semantic-authority RFC (#3263), the long-horizon benchmark research program RFC (#3240), the published technical directions (#3248), and a DCO sign-off reminder (#3316) are public. The Auto Research stop/takeover/state-aware-wake walkthrough (GH-C43) documents the shipped control cycle without a second launcher or README first-screen change. Post-v0.5.3, the planner-worker operator guide (#3630), the hosted-docs navigation and locale-parity check (#3632), and the atomic-promotion failure matrix (#3633) land; the GH-C49 frontstage legibility polish (#3663), host no-spend parity (#3661), reward-memory walkthrough (#3659), governance Stage-2 refresh (#3657), and KNN closeout (#3654) are merged. | Keep contributor, release, protocol, course, showcase, and RFC surfaces concise and linked to public evidence; add navigation, locale, and RFC-compatibility checks without appending status narratives or aliases. |

## Turn Loop Controller Plan

`loopx turn run-once` remains the atomic governed executor: decide, execute one
bounded host segment, validate independently, write back, spend once, and
project the latest scheduler contract. Host-loop activation, the external
scheduler worker, and visible Pi/TraeX integrations provide concrete outer
loops, but they do not make LoopX a resident scheduler. The maintainer-owned
pure controller and replan transition are still in hardening, so contributors
should focus on independently derived decision tables, cross-host parity,
fail-closed fixtures, or docs that clarify the boundary below.

| Priority | Planned slice | Required boundary and proof |
| --- | --- | --- |
| P0 | Harden the maintainer-owned pure Turn Loop Controller transition contract over one typed settlement receipt plus a fresh quota/scheduler decision. | Return exactly one typed disposition such as `run_now`, `wait`, `user_action_required`, `repair`, `replan`, or `terminal`; reject malformed receipts, legacy plans without a typed settlement, stale continuation, and invalid budgets without invoking a model, sleeping, mutating a host scheduler, writing state, or spending quota. |
| P0 | Make `replan_required` a real continuation boundary. | Before another Turn, write a bounded todo or vision delta, obtain a fresh TurnEnvelope, and preserve the causal agent/todo frontier. Never rerun the same stale todo merely because a host session is resumable. Reuse the existing autonomous-replan and two-stall contracts. |
| P1 | Qualify host-loop activation and skill-delivery parity. | Compare Codex, Claude Code, OpenCode 1/2, TraeX, Pi, Gemini, Cursor, DeepSeek Harness, and custom-agent packets against one provider-neutral fixture. Required skills and readback must come from the canonical release/goal contract rather than ambient host state; install dedupe and cwd isolation keep canonical manifests the source of truth. |
| P1 | Extend scheduler-owner and monitor parity from the shipped external worker. | Apply signed `primary_action`, `scheduler_hint` wake/backoff/terminal-stop, concrete user routing, and quiet no-spend monitor decisions through the declared runtime owner. `run-once` remains the only delivery transaction. |
| P2 | Qualify parity with Codex App heartbeat and adaptive child admission. | Use deterministic fixtures across active work, wait, user gate, repair, replan, child admission/conflict, monitor, and terminal states, followed by one explicit opt-in real-host qualification. Preserve independent validation and exclude raw prompts, transcripts, credentials, and host-local paths. |

Do not open a second implementation PR for the pure transition contract while
the maintainer-owned slice is active. Scheduler process management,
host-specific wake APIs, and operator presentation remain later adapters so
each slice stays reviewable and reversible.

### Starter / Good First

Low setup, docs-first, or narrow fixture work. These should be good entry
points for contributors who are still learning the repository.

| ID | Area | Task | Validation |
| --- | --- | --- | --- |
| GH-C02 | tests | Landed: #4436 (merged 2026-09-15) extends the owning `todo-archive-completed` smoke with the archive invariant no CLI-level coverage exercised: a durable standing decision survives the archive as active standing authority, and `todo archive-completed --role user` leaves the Agent Todo section untouched. The earlier #3623 attempt (closed unmerged 2026-08-31) was rejected because it repeated assertions the owning smoke already carried; prefer a new durable invariant over a second same-shape smoke. | `python3 examples/control_plane/todo-archive-completed-smoke.py`, `python3 examples/control_plane/todo-lifecycle-cli-smoke.py`, and `python3 -m py_compile loopx/*.py` |
| GH-C04 | docs | Landed: #3982 (merged 2026-09-06) synced the public timeline to tagged v0.5.4 evidence and completed the restart-host activation note beside the first-time PyPI install block; the #3301/#3566/#3556 install, ownership, activation-recovery, and extension-doctor alignment landed earlier via #3810. The `docs/release-readiness-v0.5.4` branch is absent from the remote because its work merged, so this row is history rather than an open claim. | `python3 examples/fresh-clone-quickstart-smoke.py`, `python3 examples/loopx-update-smoke.py`, `python3 examples/release/release-readiness-doc-smoke.py`, `python3 examples/release/release-version-contract-smoke.py`, and `loopx check --scan-path docs/product/release-readiness.md --scan-path CONTRIBUTING.md` |
### Focused Implementation

Small-to-medium code changes with a clear validation surface. These are good
for contributors who can run local CLI smokes and keep changes scoped.

| ID | Area | Task | Validation |
| --- | --- | --- | --- |
| GH-C06 | cli | Landed: #4659 owns `update` registration and dispatch, #4803 owns `goal-channel prepare-operation` and `deliver-operation`, and #4818 owns quota action selection. The public commands remain unchanged, each parent and extracted owner stays below its module budget, and no compatibility wrapper was added. | Command-specific smoke, `python3 examples/cli-command-module-size-ownership-command-modularization-smoke.py`, `python3 regression/cli-command-module-contract.py`, and focused pytest if rules move |
| GH-C88 | cli | Implement one budget-aware CLI output ergonomics slice for #2881: shorter default summaries with a typed `--json` escape hatch on one command family, keeping hot-path payload budgets and differential allowances intact. | `python3 examples/control_plane/cli-output-budget-regression-smoke.py`, focused command smoke, and `loopx check --scan-path docs/status-data-contract.md --scan-path docs/development/contributor-tasks.md` |
| GH-C70 | runtime | Landed: #3664 (merged 2026-09-04) narrows host-loop parity to one producer-generated bounded-wait scheduler-hint contract between the external scheduler worker and Pi: both real consumers must produce the same provider-neutral stop/wait plan, including the final quota/replan recheck triggered by the third unchanged poll. | `python3 -m pytest -q tests/test_host_loop_runtime_parity.py tests/test_external_scheduler_worker.py tests/test_pi_goal_mode.py`, `node --test tests/pi_goal_loop_runtime.test.mjs`, `python3 examples/external-scheduler-worker-smoke.py`, and `loopx check --scan-path docs/integrations/runtime-connector-catalog.md --scan-path docs/development/contributor-tasks.md` |
| GH-C100 | state | Landed via #4422: the provider-neutral parity fixture drives the shipped file-backed `claim_work` executor through same-target competition, independent-target rebase, exact replay, operation-identity mismatch, and stale-generation rejection. The fixtures remain synthetic and public-safe. | `python3 -m pytest -q tests/control_plane/test_coordination_executor.py tests/control_plane/test_coordination_file_provider.py tests/control_plane/test_coordination_provider_parity.py` and `loopx check --scan-path loopx/control_plane/coordination --scan-path docs/architecture/rfcs/shared-goal-authority-state-provider-v0.md --scan-path docs/development/contributor-tasks.md` |
| GH-C102 | state / tests | Extend the shared production-scale coordination fixture with one accepted RFC invariant or reproduced public regression that its current envelope does not cover. Update the checked-in envelope, shared generator, and an independent negative or mutation-style assertion; run the same dimension through every affected provider conformance arm. Keep the data deterministic and public-safe, do not copy a production snapshot, and do not weaken an existing dimension merely to make a provider pass. | `npm run test:control-plane`; when a provider-backed arm changes, run its isolated real integration suite (for PostgreSQL, `LOOPX_TEST_POSTGRES_URL="$DISPOSABLE_POSTGRES_URL" npm run test:postgresql-authority-store`); `loopx check --scan-path tests/fixtures/control_plane --scan-path tests/control_plane_ts --scan-path docs/development/testing-and-quality.md` |

### Advanced Implementation

Shared-state, adapter, or benchmark-control changes. Please open an issue first
and keep the first PR as a narrow slice.

| ID | Area | Task | Validation |
| --- | --- | --- | --- |
| GH-C07 | state | Landed: #4048 (merged 2026-09-07) added `tests/test_todo_write_serialization.py`, a deterministic concurrent add/update regression that drives the real `add_goal_todo` and `update_goal_todo` writers against one state file through the shared legacy writer fence, so a second mutation can no longer be lost. The global registry keeps its own serialization coverage in `tests/test_global_registry_write_serialization.py`, and the remaining `atomic_write_state_text` call sites either hold the sibling lock or document that contract on the caller. | `pytest -q tests/test_todo_write_serialization.py tests/test_global_registry_write_serialization.py` |
| GH-C47 | state | Task leases now back Turn fencing and typed CLI acquire/release, the OpenCode 2 goal worker fences its own live worker lease, a lease generation ABA fix is shipped (#3393), and a Pi `loopx_task_lease` facade over the shipped `task_lease_v0` CLI merged (#3559, closing #3549); claim coordination lives there. Adopt the same facade in one more real host integration (for example TraeX): advertise the capability explicitly, preserve soft-claim routing, expose acquire/renew/transfer/release outcomes, and prove overlapping write scopes fail without making `quota should-run` enforce undeclared lease authority. | `python3 examples/control_plane/task-lease-runtime-smoke.py`, `python3 -m pytest -q tests/control_plane/test_task_lease.py tests/test_loopx_turn_driver.py`, and a host-focused fake fixture |

### Design / RFC

Direction-setting work. These tasks should usually produce a doc or issue
before implementation.

| ID | Area | Task | Validation |
| --- | --- | --- | --- |
| GH-C89 | governance | Landed: [#4172](https://github.com/huangruiteng/loopx/pull/4172) (merged 2026-09-11) responds to the AGE-style attractor proposal (#2831) with a provider-neutral, Agent-scoped direction baseline. It defines the Goal-owned read boundary, exact-revision drift signal, advisory-only Vision re-evaluation, and synthetic fixture plan without making repository docs a write authority. | Public design note with a synthetic drift fixture plan plus `loopx check --scan-path docs/architecture/rfcs --scan-path docs/development/contributor-tasks.md` |
| GH-C96 | design / migration | Claimed: the compatibility review is published as [TypeScript migration compatibility notes](../architecture/typescript-migration-compatibility-notes.md), verifying typed state rules (zero `as unknown as` casts and a one-entry named seam inventory for the internal clone assertion on `main`), domain neutrality (providers stay explicit adapters), behavior-change disclosure (no undisclosed change found), the public/private boundary, and a stage ledger of sixteen merged Stage 2B cutovers against the RFC's three time layers, without starting new migration work. | `python3 examples/docs-governance-smoke.py` and `loopx check --scan-path docs/architecture/rfcs/typescript-control-plane-migration-v0.md --scan-path docs/development/contributor-tasks.md` |
| GH-C35 | integration | Design the next provider-neutral external-host adapter on top of LoopX Turn and TurnEnvelope, using the shipped external worker, Pi, and TraeX routes as conformance examples rather than special cases. Map compact session events into requests, planned effects, committed receipts, independent validation, recovery, and attention items while keeping raw transcripts, credentials, billing, permissions, and product frontstage outside LoopX. | Public design note with adapter-neutral fake-host smoke plan plus `loopx check --scan-path docs/integrations/runtime-connector-catalog.md --scan-path docs/development/contributor-tasks.md` |
| GH-C37 | interaction model | Curate the interaction pattern catalog with one new public-safe good/bad case, including trigger signals, user channel, agent channel, state contract, bad smell, and validation reference. Do not copy raw chat, private benchmark artifacts, or internal links. | `loopx check --scan-path docs/concepts/interaction-pattern-catalog.md` |

### Maintainer-Owned / Coordination Required

Visible work that should not be duplicated. Ask for a public helper slice
instead of launching private runs or broad product changes.

| ID | Area | Task | Validation |
| --- | --- | --- | --- |
| GH-C72 | workflow runtime | The pure Turn Loop Controller and its fail-closed repair remain maintainer-owned even though host-loop activation, the external worker, Pi, TraeX, and typed settlement are shipped. Do not duplicate the controller. Public helpers may independently review decision-table semantics or propose synthetic malformed-receipt/cross-host fixtures; do not launch hosts, alter scheduler ownership, or weaken validation to make a candidate pass. | Maintainer-run focused controller pytest, LoopX Turn transaction tests, autonomous-replan and bounded monitor no-change smokes, and risk-based premerge canary |
| GH-C67 | issue-fix | The first operator rendering of `issue_fix_outcome_projection_v0` is an active coordination lane. Do not build a competing case ledger or operator surface. Ask for a synthetic fixture, accessibility, or projection-parity helper slice that keeps provider, sink, and private notification state out. | `python3 examples/issue-fix-outcome-projection-smoke.py`, the selected public surface smoke, and `loopx check --scan-path loopx/capabilities/issue_fix --scan-path docs/development/contributor-tasks.md` |
| GH-C101 | dashboard | Dashboard Chat turn completion and single-command startup remain maintainer-owned live fixes (#3758): retryable Codex app-server Turns must render replies and one supported command must bring status backend plus UI up together. Do not build a competing dashboard or chat route; ask for a synthetic app-server protocol fixture or a regression smoke instead. | Maintainer-run packaged dashboard smoke, real app-server protocol v2 flow, and `loopx check --scan-path apps/presentation/dashboard --scan-path docs/development/contributor-tasks.md` |
| GH-C18 | benchmark | Long-horizon benchmark evidence program, including live local no-upload cases, runner contracts, trace retention, score accounting, and good/bad case attribution. Do not duplicate live runs or inspect private artifacts unless maintainers split out a public helper issue. | Maintainer-run benchmark ledger and public/private scan |
| GH-C19 | benchmark | Main-table SkillsBench product-mode comparison: raw Codex autonomous max5 versus the qualified LoopX Turn route, no verifier feedback to either arm, stop on reward 1 or declared done. Scoring stays held until a fresh task-free runner lifecycle receipt proves readiness; the native-runner research reset (#3267) and the shipped public trajectory summary seam (#3327) define the current public helper boundaries. Live matched pairs and official/countable receipt review remain maintainer-owned; external contributors can help with synthetic schema, docs, reducers, and smokes only. | Maintainer-run readiness receipt, compact ledger, case-analysis update, and public receipt/boundary scan |

## Projection Sources

This board is maintained from public-safe projections of:

- the local `loopx-meta` Agent Todo list;
- public docs under `docs/`, especially the state interaction model, status
  data contract, quota allocation, integration guide, product vision, the
  repository change-window gate contract (#3319), the TypeScript transaction
  payoff phase (#3447), benchmark research docs (including the four-arm study
  contract #3516 and the public trajectory summary #3327), the goal artifact
  lifecycle projection RFC (#3136), the hierarchical stride, post-outcome
  memory utility, human-attention, and TypeScript migration RFCs, the Dev Book
  and control-plane course, and the PR/issue label taxonomy;
- recent maintainer review of which work is externally claimable versus
  maintainer-owned live automation.

Projection rules:

- copy the task intent, not private evidence details;
- convert private benchmark runs into public helper slices unless maintainers
  explicitly publish a runnable issue;
- mark live benchmark, release, and automation lanes as `Maintainer-owned`
  when duplicate work would waste compute or weaken evidence;
- prefer tasks that name likely files and validation, so contributors can start
  without reading local active state.

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

- Keep this board curated. If it grows beyond roughly 35 open rows, move older
  or lower-priority work into GitHub issues and keep only the best entry points
  here.
- Every public task should include a scope, expected validation, and owner
  state.
- Do not publish private/local state. Summarize it into a public task only when
  the work is safe for the repository.
- After a meaningful internal milestone, update this board manually if there is
  a new contributor-sized slice.
- Remove or refresh stale tasks instead of leaving obsolete "good first issue"
  entries in place.
