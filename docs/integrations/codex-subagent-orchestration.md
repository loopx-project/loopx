# Codex Peer Task Orchestration

LoopX supports two different concepts that must not be conflated:

- durable registered agents are equal peers;
- a host runtime may launch an ephemeral child worker for one bounded task.

No registered identity owns the goal. Claims, leases, task boundaries,
capabilities, and continuation policy decide who acts. When parallel work is
useful, LoopX may choose one temporary task coordinator from the participating
peers. That responsibility ends with the task bundle.

## Peer Runtime Contract

A registered identity uses `agent_model=peer_v1`. It has no rank-bearing role,
implicit review authority, or permanent writeback ownership. A task coordinator
may:

- activate or resume eligible peer lanes;
- issue complete briefs to ephemeral child workers;
- aggregate returned evidence;
- write accepted task-bundle state and account for the completed turn.

It does not own durable goal authority. Repository policy, explicit decision
scopes, and todo continuation still govern review, merge, publication, and
production actions.

## When To Parallelize

The default policy is adaptive orchestration, not a user-selected
`single-agent` or `multi-agent` mode. LoopX projects ready work and hard
boundaries; the task coordinator decides whether parallel execution shortens
the critical path, which todos to delegate, and which qualified host context to
use.

Parallelize when it reduces uncertainty or latency:

- map disjoint code, docs, test, or runtime surfaces;
- implement isolated slices in independent worktrees;
- run an independent review or validation pass;
- inspect separate adapter, evidence, or boundary questions.

The enabled capability also guides **coordinator participation** at its existing
context phases. Before planning, reserve a distinct decision-relevant evidence
question for the coordinator when useful independent work exists. Before
launch, identify that question and its dependencies alongside the child briefs;
continue it while children work. After results return, validate decisive sources,
reconcile findings and revisit uncovered questions. Reviewing children is still
necessary, but does not replace the coordinator's own parallel investigation.
Wait when the remaining useful work depends on child results; do not invent
work, require a minimum child count, or duplicate evidence to fill capacity.
These are guidance-only instructions, not a new scheduler or completion gate.

启用能力后，现有三个上下文阶段也引导主 Agent 参与研究：规划前保留一个
独立、影响决策的证据问题；委派前说明自己的问题、依赖和子任务边界，并在
子 Agent 工作期间推进；回收后核验原件、整合结果、重审尚未覆盖的问题。
核验子任务不能替代主 Agent 自己的并行研究。剩余工作确实依赖子任务时可以
等待，不要求凑满并发、重复取证或制造工作。这些是引导，不是新增调度器或
完成门禁；等待某一候选也不意味着其他可执行研究必须停止。

Keep tightly coupled decisions in one peer lane. Do not launch workers merely
to make the activity graph look busy, and never let worker count override
quota, user gates, write scope, or repository policy.

### Adaptive Admission

`task_orchestration_contract_v2` admits ephemeral child work only when the
current runtime explicitly reports `subagent_spawn` through observed
capabilities and at least two coordinator-owned or unclaimed ready advancement
todos remain. A host name or scheduler runtime profile is metadata and never
supplies `subagent_spawn` or `subagent_resume`. Each candidate is checked
against:

- `task_domain` when the goal declares a non-empty `allowed_domains` filter;
- todo status, `resume_ready`, and open user dependencies;
- `required_capabilities` and observed host capabilities;
- canonical `task_repository` identity when declared (otherwise the goal
  repository remains authoritative); and
- goal-authorized, mutually non-overlapping `required_write_scopes`.

Rejected candidates remain visible in `blocked_lanes` with typed reason codes.
Ready lanes beyond `max_children` remain visible as `capacity_deferred`.
To stay inside the TurnEnvelope budget, the signed contract carries one shared
`child_brief_defaults` block plus a bounded per-lane delta. The typed host
request combines them into a complete `subagent_control_plane_handoff_v0`
brief. The coordinator may still keep the work serial; admission is permission
and capacity, not a command to spawn.

Without `subagent_spawn`, LoopX preserves the existing
`task_orchestration_contract_v1` registered-peer activation path. This keeps
long-lived peer identity, leases, worktrees, and multi-round collaboration
explicit instead of turning them into the default child-worker mechanism.

## Fresh, Fork, Or Resume

The temporary task coordinator chooses a worker context from the work shape:

| Work type | Context | Required task brief |
| --- | --- | --- |
| Broad mapping, prior-art search, risk discovery | Fresh worker | Objective, authority source, allowed sources, boundary, expected output, non-goals |
| Independent review or adversarial validation | Fresh worker | Claim under review, exact evidence, validation command, acceptance and merge rules |
| Failed-smoke repair or review-comment follow-up | Resume or fork | Worktree, failing evidence, latest patch, next bounded repair |
| Disjoint implementation | Fresh worker in an independent worktree | Claimed todo, allowed paths, write scope, validation, continuation policy |
| Long-running claimed lane | Resume the registered peer task | Agent id, todo or lease, latest accepted evidence |
| Production action or emergency rollback | No automatic worker | Operator approval, stop condition, reversible command plan |

Fresh workers are useful only when the task coordinator can provide a complete
brief. Missing authority, scope, expected output, or validation is a planning
gap, not a reason to launch an under-specified worker.

## Shared Control Plane Handoff

Every child-worker brief starts from the shared control plane. A worker must not
infer current authority from chat history, an old packet, or another worker's
summary.

The existing host-child packet name remains
`subagent_control_plane_handoff_v0` for compatibility. Its lineage fields do
not create durable rank:

- `parent_goal_id`: shared goal lineage, not an owner identity;
- `authority_artifact`: current goal, policy, or review authority;
- `latest_state_ref`: state hash, run id, or generated-at value to read first;
- `quota_gate_snapshot`: current eligibility, wait, or gate state;
- `evidence_boundary`: allowed sources, paths, and public/private rule;
- `writeback_spend_contract`: who may accept evidence and account for the turn;
- `child_decision`: `continue`, `wait`, or `reuse_existing_evidence`.

Only then should the brief include todo id, work scope, expected artifact,
validation, and continuation policy. The compact rule is: child worker reports
evidence only; the temporary task coordinator writes accepted state and spends.

```yaml
subagent_control_plane_handoff_v0:
  parent_goal_id: example-peer-task-goal
  authority_artifact: .codex/goals/example-peer-task-goal/ACTIVE_GOAL_STATE.md
  latest_state_ref: state_hash_or_run_id
  quota_gate_snapshot: eligible
  evidence_boundary: public-safe read-only repository map
  writeback_spend_contract: child worker reports evidence only; task coordinator writes accepted state and spends
  child_decision: continue
goal_id: example-peer-task-goal
todo_id: todo_docs_map
work_scope: inspect docs and return evidence paths
validation: cite files and residual risk; do not edit
continuation_policy: independent_handoff
```

After explicit capability admission, the signed Turn host request uses
legitimate host metadata only to map supported native context operations. The
host name does not admit child work. The task coordinator chooses from that
catalog:

- Codex exposes `fresh` and `resume`;
- Claude Code exposes `fresh` through its native Task surface;
- generic adapters expose no child capability unless the adapter declares one.

`fork` stays out of the public execution catalog until a real host adapter
proves versioned execution state, copy-on-write workspace isolation, capacity
reservation, branch lease, held-result settlement, cancellation, and recovery.
Context choice is advisory execution strategy and cannot widen LoopX authority.

## Receipt And Enforcement Boundary

Each observed child returns a bounded host receipt. `runtime_id` identifies the
stable host kind, not a process, executable, workspace, Session file, or other
local path. The built-in Codex adapter pins it to `codex-cli`; `worker_ref`
remains an opaque host child reference. LoopX rejects receipts that copy local
paths or fail to bind the planned bundle, lane, task-packet digest, context,
workspace, or effect classes. Evidence references are one or more opaque tokens
such as `artifact:child-result`, never prose, URLs, transcripts, or local paths.

The prevention-first packet check is enforced before launch. Result
reconciliation is currently observation-only: it can mark missing, rejected,
cancelled, drifted, or aligned child evidence and require parent acceptance,
but it does not intercept live host tools or automatically terminate a running
child. A receipt therefore makes evidence eligible for parent review; it does
not itself authorize settlement, publication, external writes, or production
actions.

## Claims, Leases, And Worktrees

Registered peers claim work through LoopX todos and leases. The control plane
allows one pending lease for `(goal_id, todo_id)`. `goal_id` is the shared
control-plane lane; `todo_id` is the work item being claimed. A host child may
carry the claim context in its brief, but it does not become a ranked agent.

Repository-writing peers and child workers use independent worktrees. Overlap
is resolved through task boundaries and repository policy, not through a
permanent controller. Completion uses typed continuation:

- `independent_handoff`: leave the successor available to peers;
- `same_agent_non_delivery`: keep a non-delivery follow-up with the same peer.

Review remains `action_kind=review` over `independent_handoff`. Add the author
to `excluded_agents` only when the successor should stay open for eligible peers
but must not be reclaimed by that author.

## Enabling Bounded Orchestration

The configuration surface and the runtime policy are separate opt-ins. To
expose the local preview-locked API, status field, and Dashboard control, start
the loopback Dashboard explicitly with:

```bash
loopx dashboard --enable-goal-subagent-configuration
```

Without that startup flag, Chat capabilities omit the feature, both
`/api/chat/goal-subagents/*` routes return 404, status omits `spawn_policy`, and
the Dashboard does not render the control. After enabling the surface, opt one
Goal into bounded orchestration:

```bash
loopx configure-goal \
  --goal-id example-peer-task-goal \
  --multi-subagent-feature enabled \
  --max-children 2 \
  --align-codex-subagent-capacity \
  --execute
```

### Codex Host Capacity Alignment / Codex 宿主容量对齐

`max_children` is the Goal-owned upper bound. Codex separately owns the
host-side [`[agents].max_concurrent_threads_per_session`](https://developers.openai.com/zh-Hans/docs/agent-configuration/subagents)
limit. That Codex key
counts child threads and excludes the main thread, so a Goal maximum of `6`
requires a Codex value of `6`, not `7`.

`--align-codex-subagent-capacity` turns the same preview/apply confirmation into
a one-click cross-boundary adjustment. Preview reads the active `CODEX_HOME`,
reports an explicit shortfall or an unknown implicit default, and performs no
write. When a raise is needed, apply writes the canonical Codex key to at least
`max_children`, keeps a higher existing value, removes the legacy
`agents.max_threads` alias, writes atomically, and verifies an exact TOML
readback. A sufficient legacy alias remains a compatible no-op. It never lowers capacity.
Existing Sessions retain their startup configuration; the receipt therefore
states when a new Session is required.

`max_children` 是 Goal 权威拥有的上限；Codex 另外拥有宿主侧的
`[agents].max_concurrent_threads_per_session`。该 Codex 配置只统计子线程，
不包含主线程，因此 Goal 上限为 `6` 时，Codex 目标值也是 `6`，不是 `7`。

加入 `--align-codex-subagent-capacity` 后，同一次 preview/apply 确认即可完成
跨边界的一键对齐。预览只读取当前 `CODEX_HOME`，显示显式容量不足或“隐式默认值
未知”，不会写文件；需要提升时，确认后只会把新配置键提升到至少
`max_children`，保留已有更高值，移除旧别名 `agents.max_threads`，随后进行原子
写入和精确 TOML 读回。容量已足够的旧别名仍是兼容的 no-op。该操作绝不降低
容量。已有 Session 不会热加载宿主配置，因此回执会明确提示何时必须新建 Session。

Task-domain filtering is optional. With no `--allowed-domain`, both tagged and
untagged ready Todos remain eligible subject to every other admission boundary.
Add one or more `--allowed-domain <token>` arguments only to narrow execution to
matching typed Todos; an untagged or non-matching Todo is then blocked with
`task_domain_not_allowed`.

`multi_subagent` remains the compatibility name for host child-worker capacity
and permission policy. It does not ask the user to select a run mode or agent
hierarchy. With observed host child capability, `quota should-run` may project
adaptive `task_orchestration_contract_v2`. Without that capability, it does not
fall back to coordinating registered peers: registration grants Todo ownership,
not cross-agent scheduling authority.

Use `--multi-subagent-feature off` to disable worker spawning. The low-level
`--orchestration-mode` and `--spawn-allowed` flags remain available for host
integrations.

To remove the configuration surface itself, stop the Dashboard and restart it
without `--enable-goal-subagent-configuration`. The flag only exposes a local,
preview-locked configuration contract. It grants no Goal ownership, repository
write, credential, publication, production, or settlement authority; the Goal
policy and the observed host capabilities remain independently authoritative.

## Explicit Registered-Peer Coordination

Registered peers are independent by default. LoopX never hashes the peer set or
open Todo bundle to auto-elect a coordinator. A host that can really activate
or resume durable peer runtimes may select one coordinator explicitly:

```bash
loopx configure-goal \
  --goal-id example-peer-task-goal \
  --peer-task-coordinator codex-alpha \
  --execute
```

The selected coordinator must then report the observed host capability on each
eligible turn:

```bash
loopx quota should-run \
  --goal-id example-peer-task-goal \
  --agent-id codex-alpha \
  --available-capability peer_agent_activation
```

The contract includes only peer lanes that are currently actionable. Dormant
registered agents and closed, blocked, or deferred todos are not coordinator
candidates. A dormant or non-resumable lane is projected under
`blocked_peer_lanes`; if no peer lane can run, the bundle has
`execution_state=blocked`,
`terminal_outcome=blocked`, and `retry_policy=material_peer_state_change_only`.
That blocked diagnostic does not replace the coordinator's own runnable lane or
re-arm an activation obligation on every heartbeat. If the coordinator also
has no in-scope runnable fallback, the final interaction mode is
`peer_coordination_blocked`: schedulers return the bundle to its owner and stop
the recurring heartbeat until peer capability/readiness, coordinator
configuration, or the coordinator's own work frontier materially changes.

Disable registered-peer coordination without changing peer registration or
child-worker policy:

```bash
loopx configure-goal \
  --goal-id example-peer-task-goal \
  --clear-peer-task-coordinator \
  --execute
```

This opt-in grants neither cross-owner Todo mutation nor broader repository,
publication, credential, or production authority. Read it back with
`loopx configure-goal --goal-id example-peer-task-goal` before relying on it.

## Run History And Observation

Run history should attribute task coordination without persisting rank:

```json
{
  "agent_model": "peer_v1",
  "task_coordinator": "codex-alpha",
  "control_plane_handoff_version": "subagent_control_plane_handoff_v0",
  "peer_lanes": [
    {"agent_id": "codex-beta", "todo_id": "todo_docs_map", "state": "completed"},
    {"agent_id": "codex-gamma", "todo_id": "todo_validation", "state": "running"}
  ],
  "accepted_evidence_count": 1,
  "next_action": "review the remaining validation evidence"
}
```

Useful observation surfaces include task bundle, participant peers, worker
context (`fresh`, `fork`, or `resume`), accepted or rejected evidence, leases,
worktrees, quota state, and typed continuation. They must not reconstruct a
durable leader from a temporary coordination event.

## Safety Rules

- Do not spawn when quota or the selected user gate blocks the task.
- Do not infer permissions from an agent name, profile label, or old prompt.
- Do not launch a fresh worker without a complete task brief.
- Do not put credentials, private links, raw logs, or production material in a
  public handoff packet.
- Keep implementation scopes disjoint and use independent worktrees.
- Let repository policy decide review and merge; peer identity grants neither.
- Let one temporary coordinator accept bundle evidence and write one spend
  event after validated progress.

The result is parallel execution without a permanent leader: durable agents
remain peers, while task coordination and host-child relationships stay bounded
to the work that requires them.

## Child Model Preferences And Read-Heavy Work

Persist child launch preferences on the existing Goal orchestration policy:

```bash
loopx configure-goal --goal-id example-peer-task-goal \
  --multi-subagent-feature enabled --max-children 2 \
  --subagent-model gpt-5.6-luna --subagent-reasoning-effort max --execute
loopx --format json configure-goal --goal-id example-peer-task-goal
```

Read back `after.orchestration.model_config` (or the current configuration
summary) and `quota_should_run.goal_boundary.orchestration.model_config`.
The registry stores this under `spawn_policy.model_config`. The current
registry wins over an older project-asset model snapshot. Clearing the setting
cannot resurrect a model preference from that snapshot.

This is a per-Goal preference, not a global vendor default. Existing Goals with
no model configuration retain their previous behavior. Setting a model alone
does not enable spawning, change the coordinator model, register a peer, or
supply a host capability. Disabling spawning retains the preference for later
use. Remove both model and effort with:

```bash
loopx configure-goal --goal-id example-peer-task-goal \
  --clear-subagent-model-config --execute
```

The native launch is still owned by the host/task coordinator. Before launch,
read the model configuration and pass the model and effort explicitly through
that host's supported arguments. Verify support against the host catalog;
unsupported combinations must be reported instead of silently substituting
the coordinator's model. Configuration readback proves persistence, not a model
execution receipt. These instructions are coordinator guidance, not interception
of arbitrary host tool calls. Automatic typed-driver argument propagation is
not implemented by this configuration change.

For read-heavy research, prefer fresh contexts with disjoint evidence questions:
provide the exact source/period, allowed paths, claim to challenge, a bounded
reading budget and a compact expected result. A worker returns source citations,
units, calculations, counterevidence and unknowns; it does not edit the plan,
spend quota or contact third parties. The coordinator verifies decisive claims
against original sources and accepts or rejects the result. Two worker opinions
are not two independent market observations. Record requested model/effort,
worker reference and accepted evidence separately; do not infer the executed
model from response style.

中文操作要点：示例显式配置 Luna/max，仅作用于当前 Goal 的子任务偏好；
未配置的 Goal 不改变默认行为。宿主启动时仍须显式传参并核验支持情况，
不支持时不能悄悄继承主模型。只读研究拆分不同证据问题，子任务返回来源、
反证和未知项，主任务复核并决定是否采纳。配置生效不等于研究结果有效。

### Frontend configuration

The capability detail page also lists **Coordinator workflow guidance** in
English / **主 Agent 协作指导** in Chinese. It shows the three supported phases
and their purpose, using the shared capability catalog. It is read-only metadata,
not a per-run delivery indicator. The existing capability switch controls this
guidance together with child capacity; there is no second enable switch.

Goal settings → Goal details exposes child model and reasoning effort next to
the existing execution boundary. “Use Luna / max” fills the draft only; use
“Preview configuration update” to save it, including while execution is off.
Clearing the preference returns to host defaults after applying the preview.
Turning execution off retains the saved model preference.

The Goal capability editor exposes the same fields through the existing
`multi_subagent` capability. Both UI routes use the same registry policy and
preview/readback validation; a model change invalidates an older preview.
The model field is a preference, not a discovery menu or an execution receipt.
The CLI accepts an empty `--subagent-reasoning-effort ''` to clear effort while
retaining the model; `--clear-subagent-model-config` clears both.

### Authorized delegation route discovery

An operator can make the existing requester-scoped local delegation bindings
discoverable to the same `multi_subagent` capability without copying grants,
credentials or host arguments into the registry:

```bash
loopx configure-goal --goal-id example-peer-task-goal \
  --subagent-execution-config .loopx/config/delegations.json
loopx configure-goal --goal-id example-peer-task-goal \
  --subagent-execution-config .loopx/config/delegations.json --execute
loopx --format json configure-goal --goal-id example-peer-task-goal
```

The pointer must be a repository-relative JSON path under `.loopx/config/`.
Keep the file ignored and operator-owned. The file and the existing local
delegation validator remain the execution-grant authority; the Goal registry
stores only the pointer. The Dashboard Goal settings and generic capability
editor preview, apply and read back the same field. Clear it with
`--clear-subagent-execution-config --execute`. Turning child execution off
retains the pointer for a later re-enable.

An unfinished Goal Chat run created before this field existed may resume with
its already pinned Session reference until that run becomes terminal. New runs
and completed legacy runs must configure the Goal-owned pointer first; the
compatibility path does not write or synchronize a second configuration owner.

At `before_plan`, `loopx agent-context` considers at most six bindings authorized
for the current requester and projects as many as fit the existing context byte
budget; `authorized_count` and `routes_truncated` make omissions explicit. Each
route carries only binding/Agent/Todo/runtime
identity, `ready|blocked|unknown`, an optional public-safe execution profile and
one stable `loopx delegation` entrypoint. A separate, explicit
`agent-context --phase after_delegate_result` read may include bounded
operation-status and recovery-required counts. Automatic planning and managed
return paths do not enumerate the operation journal. These reads do not launch,
resume or accept a worker, and they never expose raw host
arguments, workspaces, output references, credentials or child results.

Runtime availability and business adoption are separate. `ready` says that the
existing runtime owner did not find a launch blocker; it does not select the
route, establish task fit or prove execution. `blocked` or `unknown` never
prevents useful native work. Before dispatch, the coordinator must use the
authorized binding, recheck its runtime/model/budget and record a stable
operation id; it must not silently substitute another runtime or model. No
heartbeat is required to use every route. When Lark or another managed surface
exposes these facts, it must consume the same signed capability context rather
than own another route configuration.

中文：可在现有 `multi_subagent` 能力中配置
`.loopx/config/delegations.json` 指针，让当前请求 Agent 在规划前看到自己已获授权的
委托路由。注册表只保存指针，已忽略的本地文件与既有 delegation 校验器仍是执行授权
唯一来源；Dashboard、通用 capability 编辑器、CLI 和 managed Turn 读写同一字段。
投影只包含有界的路由就绪状态和回执计数，不包含凭据、host 参数、工作区或原始结果，
也不会启动、恢复或验收任务。`ready` 只是运行时观察，不等于任务采用或执行成功；
`blocked/unknown` 不阻塞其他有用工作，调度前仍须核对 runtime/model/budget，并禁止
静默替换。

### Capability context lifecycle

An enabled `multi_subagent` capability contributes to the coordinator through
the generic bounded provider contract in `control_plane/agent_context.ts`.
The capability owns its guidance in `control_plane/subagent_context.ts`; other
capabilities can implement the same typed provider interface and register with
their runtime owner. This initial version registers this built-in provider,
not arbitrary manifest scripts or external plugins.

| Phase | Managed LoopX Turn call site | Coordinator responsibility |
| --- | --- | --- |
| `before_plan` | Live quota decision → `interaction_contract.agent_context` → signed `turn_envelope.agent_context` | Read requester-authorized route readiness when configured, choose only useful independent questions, and keep useful validation and integration work with the parent. |
| `before_delegate` | Admitted child operations → plan and host request `delegation_context` | Bound briefs and expected evidence; recheck the chosen route/runtime/model/budget and forbid silent substitution. |
| `after_delegate_result` | Host receipt reconciliation → journal `host_result.agent_context` → executor result `agent_context` | Reconcile bounded native receipts. Read delegation operation receipts explicitly when that separate source is needed, then validate original evidence and accept/defer/reject. |

Planning guidance does not require two persistent Todos. Managed automatic
child-lane admission still requires its existing prerequisites; this change
does not grant spawning rights or force concurrency. Disabling the capability
omits the context. Model preferences alone do not enable it.

For a native-tool host such as a Codex App session, the LoopX project skill reads
the same interface at delegation and result boundaries:

```bash
loopx agent-context --goal-id example-peer-task-goal --agent-id coordinator \
  --phase before_delegate --format json
loopx agent-context --goal-id example-peer-task-goal --agent-id coordinator \
  --phase after_delegate_result --format json
```

The coordinator must be registered. The command reads current registry policy
without writing a Todo, starting a turn or spending quota. When an execution
configuration is present, `before_plan` reads only the binding directory;
`after_delegate_result` explicitly reads the current requester's existing local
delegation operation journal. Otherwise it has no native execution receipt input
and return-phase facts explicitly say `not_supplied`. The top-level
`host_receipts_observed: false` is retained for compatibility and is explicitly
scoped by `host_receipts_scope: native_tool_input`; it does not negate the
separate bounded delegation-journal observation inside capability facts.
LoopX cannot transparently intercept arbitrary host tools. The managed return
packet is returned to the caller, not automatically sent as another model turn.

Each packet binds Goal/Agent/optional Todo, phase, provider revision and a stable
content ID. When the envelope approaches its existing budget, it carries a
signed content hash and a read instruction pointing to the existing full-decision
route instead of duplicating all guidance. Providers return only guidance, bounded facts and source references;
they cannot replace action, permission or priority fields. Per-provider and
aggregate limits are 2,048 and 3,072 UTF-8 bytes. Failures are isolated and
diagnostic text excludes raw provider errors. These scoped context projections
are not a new progress store and should not be exported as public evidence.
`delivery: projected` means generated, not delivered/read/adopted. Read actual
host receipts and parent validation evidence for those conclusions. Replaying
a journal preserves the same packet; no new execution is inferred.

中文：三个阶段已落到真实控制面接口，开启 capability 自动提供主 Agent
协作指导。原生工具通过 skill 在边界读取同一接口，不声称拦截宿主工具。
关闭 capability 即停止提供；前端展示支持范围，执行、采纳仍须看实际证据。
