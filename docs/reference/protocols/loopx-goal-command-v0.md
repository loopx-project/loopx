# loopx_goal_command_v0

`loopx_goal_command_v0` defines the project-local `/loopx` slash command:

| Command | Intent | Mutation policy |
| --- | --- | --- |
| `/loopx` | Inspect or preview project connection. | Read-first; ask before bootstrap/connect writes. |
| `/loopx <goal text>` | Start a concrete goal, plan ranked todos, activate the host loop, and enter the LoopX automation flow. | Explicit invocation may write project-local LoopX state and todos, then must activate or gate the host loop. |

This command is intentionally separate from `/loopx-global-*`: global commands
summarize and manage visible control-plane state across projects, while
`/loopx <goal text>` starts or continues one project goal.

## Goal-Start Flow

When the user provides text after `/loopx`, the host should:

1. Treat the text as explicit user intent to start this project goal.
2. Connect project-local LoopX state if no matching registry goal exists.
3. Plan before writing todos.
4. Write planned todos in exact plan order.
5. Run `refresh-state`.
6. Activate the host loop if it is missing, unknown, or stale:
   - `codex-app`: create or update the Codex App heartbeat automation from the
     generated `heartbeat-prompt` task body.
   - `trae_app`: create or update the Trae App heartbeat automation from the
     generated `heartbeat-prompt` task body. The `trae_app` runtime
     profile preserves Trae host identity while consuming the provider-neutral
     `scheduler_hint.app_automation` cadence, ACK, and terminal-stop contract.
     Its packet carries `host_surface=trae_app` and never requires a Codex
     automation-store fallback. A settled non-terminal turn keeps
     the automation active so the next wake can select the successor.
   - `codex-app-ssh`: when Codex App is attached to a remote workspace over SSH
     and host automation tools are unavailable, set the current visible task to
     `/goal <task_body>` using the generated `codex_app_ssh_goal` profile. After
     its typed unchanged-poll limit and final quota check, use native
     `update_goal(status=blocked)` to block only that host Goal; keep the
     registered LoopX goal active and resume the host with `/goal resume`.
   - `codex-cli`: set the visible Codex CLI TUI to `/goal <task_body>`.
   - `codex-ide-plugin`: set the visible IDE composer task to
     `/goal <task_body>` through the same `codex_cli` runtime profile.
   - `ark-managed-agent`: submit the generated `<task_body>` once as a native
     Goal. The Goal runtime owns continuation and terminal evaluation; do not
     wrap its inner iterations in LoopX Turn or resubmit at phase boundaries.
   - `claude-code`: arm LoopX with `/loopx <task>`, then run native `/loop`.
   - `opencode`: call `loopx_goal_activate` from the installed LoopX OpenCode
     bridge; the bridge gates idle continuation and timer wakes through
     `quota should-run` and completes only on validated terminal no-follow-up.
   - `traex-cli`: set the visible TraeX TUI to `/goal <task_body>` through the
     TraeX visible-goal renderer while quota remains bound to the generic
     `generic_cli` runtime profile. TraeX `/goal` requires
     `[features] goals = true` in `~/.trae/traecli.toml`; if goal mode is off,
     show the pasteable `/goal <task_body>` gate. Do not route to `/loop`
     unless a verified LoopX adapter is installed. This CLI surface does not
     create an App automation and has no slash-command installer; TraeX loads
     skills from `~/.trae/skills`.
   - `pi`: call `loopx_goal_activate` from the installed LoopX Pi extension;
     the extension gates settled continuations and timer wakes through
     `quota should-run` and stops only on validated terminal no-follow-up.
   - `manual` / `other-agent`: wire the external loop driver described by
     `loopx agent-onboard`.
7. If the host cannot mutate that surface, report the exact pasteable gate
   instead of claiming autonomous setup complete.
8. Run `quota should-run`, then start the first bounded segment only when the
   quota contract allows it.

New hosts should discover exact agent types with:

```bash
loopx agent-onboard --list-agent-types
```

Ambiguous values such as `codex` must fail closed because Codex App automation,
Codex App over SSH, the IDE plugin, and Codex CLI use different host-loop
activation paths.
Likewise, callers must choose `trae_app` for Trae App heartbeat automation or
`traex-cli` for the existing visible `/goal` loop.

Codex App SSH, Codex CLI/IDE, and Ark Managed Agent form one native Goal host
family. They share the stable `loopx_goal_prompt_v0` body, the 4,000-character
host budget, per-continuation `quota should-run` packets, durable LoopX
writeback, and non-heartbeat quota accounting. Their continuation owner remains
an explicit host contract:

| Native Goal host | Activation | Continuation and blocked-state owner |
| --- | --- | --- |
| Codex App SSH / Codex CLI / Codex IDE | Set a visible `/goal <task_body>`. | Native Codex Goal; after the unchanged limit it may call `update_goal(status=blocked)`, and only user `/goal resume` reactivates it. |
| Ark Managed Agent | Submit the same prompt family once. | Managed Agent Goal runtime and its durable journal; LoopX must not emulate `/goal resume` or blindly resubmit. |

This family is a prompt, quota, and state-boundary abstraction, not a claim that
all hosts have the same transport or lifecycle API.

The `codex-app-ssh` task body is an interactive Goal contract, not a scheduled
heartbeat. It must fit the Codex `/goal` text limit, call `quota should-run`
with `--begin-turn` so the CLI mints the identity required for exact Todo
selection, and must not instruct the host to invoke `automation_update`, apply
an RRULE, or synthesize `LOOPX_TURN`. This CLI-owned selection receipt does not
turn the Goal into heartbeat automation or a Turn-bound settlement flow; after
validated writeback, the Goal still spends once with `--source visible-goal`.

Visible Goal activation captures the capabilities observed when the task body
is generated, but that initial list is not exhaustive for a long-running
session. Dynamic capability guidance therefore belongs to the CLI decision
packet, not the stable Goal prompt. When `quota should-run` finds a repairable
runtime capability gap, `interaction_contract.cli_channel` returns a typed
`runtime_capability_reentry_v0` packet. Each candidate requires a successful
real-callsite observation before its exact re-entry command may declare
`--available-capability`.

The verified re-entry invocation becomes the capability envelope for that
decision. LoopX then projects the same session-scoped capability flags into
follow-up refresh, spend, monitor, and quota commands. It never persists those
observations as durable grants, and owner-held capabilities such as credentials
remain user gates. This contract is shared by local visible Goal hosts and Ark
Managed Agent Goal mode without requiring prompt regeneration.

Agent identity follows the same fail-closed rule. `agent-onboard` keeps its
fresh-registration path, while Codex App `start-goal --guided` consumes the
ambient `CODEX_THREAD_ID` when `--thread-id` is omitted and must reuse a
matching stable opaque thread binding when available. A stable thread ID with
no binding is no longer treated as fresh onboarding when registered lanes
exist: `start-goal` returns an identity gate that requires selecting one
existing lane, and fresh registration is the default only for a goal with no
registered lanes or explicit `--new-peer`. Existing identities are takeover
choices, never an implicit automatic selection; selecting one requires
explicit user intent for that exact agent. A missing thread ID follows the
same gate when registered lanes exist and remains fail-closed otherwise,
requiring explicit `--agent-id`, lane selection, or new-session intent with
`--new-peer`. LoopX persists `(host_surface, goal_id, thread_id) -> agent_id` with
`bind-agent-thread --execute`; later `/loopx` calls reuse that bound identity
across `start-goal`, heartbeat, quota, refresh-state, and Todo commands. The
preview is advisory; todo writeback requires verified registration and binding
readback. Without a stable thread id, callers must continue to pass an explicit
registered `--agent-id` or explicit `--new-peer`. No gated path may advertise
unscoped heartbeat or quota commands.

The command pack preview is still read-only. It describes the commands and
contracts; the slash invocation is what authorizes project-local state writes.
New-user surfaces should also show the compact slash command catalog from the
command pack, or the equivalent `loopx slash-commands` CLI help, so users can
discover `/loopx`, `/loopx <goal text>`, and the `/loopx-global-*` read-only
manager commands.

## Planning Contract

The planner must create an ordered planning checkpoint before any `todo add`,
but the shape depends on how clear the goal already is:

- `open_ended_product_direction`: broad or fuzzy product directions should
  produce 2-5 public-safe todo items so the user can see the main lanes,
  risks, and execution order before LoopX starts working.
- `clear_bounded_problem`: concrete tasks with a clear success condition should
  use a planner-sized ordered todo plan. The model should produce enough
  concise todos to make the approach explicit, without arbitrary item-count
  caps or management-only filler.

The shared `budget_policy` now explicitly asks for the minimum plan needed to
begin useful work: reuse covered Todos, identify the next executable step and its
decisive validation, and defer uncertain later work to replan. Diagnostic validation,
including permitted feedback, can be that first step before dependent implementation
to strengthen the first deliverable. The quality objective and diagnostic means
coexist: a diagnostic result does not replace the requested deliverable. Do not
require exhaustive upfront decomposition. This changes default
planning guidance for both `/loopx` and `todo plan`, in ordinary and fine-grained
mode; it adds no fixed step/time limit or permission to skip planning/readback,
task acceptance, quota admission or genuine blockers. The existing fine-grained
one-checkpoint rule and broad-goal lane suggestions still apply. Lightweight
planning does not mean early task completion or weaker delivery requirements.

The shared planner now includes `first_delivery_guidance`, also rendered in the
guided `/loopx` start prompt and the existing `todo plan` packet. This changes
default Agent guidance in both ordinary and fine-grained planning: preserve a
strong feasible first deliverable and its coherent implementation/validation,
rather than deliberately weakening it to produce an earlier feedback signal.
Judge initial quality alongside latency and full cost; diagnostic probes remain
legal under the original acceptance, feedback, budget and authority limits, and
preparation must not grow indefinitely. The guidance adds no quality oracle,
mandatory external evaluation, submission allowance or machine admission gate.
Model adoption and resulting quality require separate real-task evidence.

Each new item includes:

- `priority`: `P0`, `P1`, or `P2`;
- `text`: a short checkbox title beginning with `[P0]`, `[P1]`, or `[P2]`;
- `task_class`: usually `advancement_task`;
- `action_kind`: a compact action token such as `implement`, `test`,
  `review`, `document`, or `investigate`.

At least one new item should be `P0` unless the first useful step is blocked by
a concrete user gate. User todos are reserved for owner decisions, private
material, credentials, destructive git, or production authorization.

## Priority Ordering

Priority buckets sort as `P0`, then `P1`, then `P2`. Within the same bucket,
the planner's list order is the relative priority.

Hosts must preserve that order while running `loopx todo add`. LoopX status and
quota projections already use todo index as the same-priority tie-breaker, so
the first written `P0` outranks the second written `P0` without adding a new
rank field.

## Explicit Issue-Fix Capability Route

Goal text never selects a product capability. To enter the issue-fix route, the
caller must explicitly pass `--capability-route issue-fix` to `start-goal` (or
use the equivalent explicit host switch). Issue/PR wording, a public URL, or the
literal string `issue-fix` remain objective text only and grant no route.

Conversational hosts must not ask the model to split that switch from the goal
text and then rebuild separate CLI arguments. Their generated `/loopx` entry
passes the complete visible argument string once through
`start-goal --slash-command-arguments`; the CLI consumes only a leading typed
route switch and treats the remainder as goal text. Direct integrations may
continue to use structured `--capability-route` plus `--goal-text`. These two
input forms are mutually exclusive, and malformed or unsupported leading route
switches fail closed before the guided transaction is built.

Only the leading `--capability-route` switch is parsed. A later occurrence of
the same text in the raw argument string is ordinary goal text and is not an
error.

`start-goal` projects that explicit switch as a typed
`selected_capability_route`.
This is a bootstrap-only selection, not later-turn authority. The guided
transaction first persists candidate admission in capability-owned state.
Missing evidence projects `evidence_required`; unresolved cross-references,
closed PRs, or maintainer comments project `verification_required`. Only an
`admitted` `proceed` candidate enters feasibility. Final reuse and terminal
routes are distinct from pending verification. The Todo keeps only the
scheduling route (`action_kind`) and stable public target
(`target_key`); issue facts, prior-work checks, repository evidence,
reproduction, scope, and validation remain owned by `issue_fix` state.

Later turns continue through `quota should-run.selected_todo`; they do not call
`start-goal` again or infer admission from stale prompt context. A runnable
feasibility result binds its projected successor to the persisted feasibility
row with `capability_binding_ref`. The route's typed
`implementation_admission.durable_execution_binding` contract tells a Host how
to resolve that ref and compare the Todo's exact `action_kind` and `target_key`
with the admitted projection.

For pre-binding Todos, a Host may compare the exact action and target against
the current feasibility row. Prefix-only matching is never admission authority.
This keeps capability selection, durable Todo execution ownership, and Goal
continuation as separate contracts.

The guided transaction's `command_cwd_source` points to the packet's resolved
`project`; hosts execute its project-relative commands from that exact root.

Before planning implementation, select a currently open public tracker issue.
Repository TODO/FIXME entries, warnings, and incidental test failures may
support later reproduction, but they are not issue identity:

```bash
gh issue list \
  --repo "$(gh repo view --json nameWithOwner --jq .nameWithOwner)" \
  --state open \
  --limit 20 \
  --json number,title,url,labels
```

```bash
loopx issue-fix workflow-plan \
  --url <github-issue-or-pr-url> \
  --repo-path <approved-repo> \
  --repository-context-json <compact-context.json> \
  --fetch-candidate-evidence \
  --goal-id <goal-id> \
  --validation-label "<validation command>" \
  --format json
```

The preview maps public metadata, repository context, intake classification,
branch planning, validation labels, the feasibility checkpoint, and PR review
readiness blockers into `/loopx <goal text>`. Repository context pins compact
policy, architecture, change-scope, reproduction, and validation refs to a
revision; memory and external experts stay advisory until repository-verified.
The built-in public GitHub collector produces issue-specific, complete,
non-truncated receipts for closing PR references, cross-references, and
maintainer comment metadata without retaining bodies. Exact closing
references may be reused directly. Cross-references remain
`verification_required` until their exact current revision is inspected;
maintainer comments project a content-read gate plus disposition successor.
The optional `--candidate-resolution-json` binds those compact outcomes to the
current PR head or maintainer-comment `updatedAt` revision before they feed back
to current source rows, so a changed source revision fails closed. A capped aggregate
PR index may generate candidates but cannot prove that prior work is absent.
The command persists the preflight receipt when `--goal-id` is present. Only
an `admitted` `proceed` decision may start a new implementation and enter
feasibility. Pending verification, final reuse, and terminal routes must not invoke feasibility. For a
`proceed` candidate, record a compact observation and let LoopX select exactly
one implementation route. Write projected successors in priority and planner order:

```bash
loopx issue-fix feasibility \
  --url <github-issue-or-pr-url> \
  --reproduction-status <confirmed|planned|missing|blocked> \
  --scope-class <bounded|uncertain|oversized> \
  --repository-context-json <compact-context.json> \
  --goal-id <goal-id> \
  --format json
```

Write only the projected route successor or no-follow-up. User todos or operator
gates must cover private repro material, issue body/comment reads, external
issue comments, PR creation, merge, publish, destructive git, production
actions, and repository-policy approvals.

## Stop Conditions

Stop and ask the user instead of writing or executing when:

- private source material must be read before a public-safe todo can be formed;
- credentials or secrets are required;
- destructive git or production actions are needed;
- the host cannot execute shell/CLI/tool calls or persist LoopX state;
- the host cannot activate or expose the required host loop and no concrete
  pasteable gate can be shown.
