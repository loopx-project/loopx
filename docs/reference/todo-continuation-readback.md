# Todo continuation and closure readback

Todo list, status and quota distinguish a completed record from a closed work
slice. A completed tracked advancement Todo still needs an existing successor
or an explicit `no_followup=true`. This read policy lives in
`control_plane/todos/succession.ts`; Python normalizes legacy input and renders
its decisions. It belongs to the existing Todo control plane, uses the selected
AuthorityStore, and adds no capability or extension provider.

## PR dependency observation / PR 依赖观察

`pr_merged:owner/repo#42` identifies a dependency; absence of a merge event
does not prove that GitHub is still pending. Delivery-history supervision no
longer exempts that unobserved wait. Causal blocked closeout continues to require
a registered `monitor_changed` or `todo_done` target with current readback.

The existing `heartbeat-prequota --goal-id GOAL --agent-id AGENT` observes exact
unfinished PR dependencies from the complete Todo head, including deferred work.
It polls at most four distinct repository-qualified dependencies per invocation,
with a durable 30-minute cadence for successful and failed attempts. A missing
repository binding is reported for repair; it never guesses from PR numbers.
An exact merged observation appends the existing native `pr_merge` fact so the
original condition becomes satisfied. Queue disappearance, closed-without-merge
and provider failures cannot satisfy it. This observation changes no Todo status,
requirements, claims, leases or Goal acceptance. Deferred ready work still needs
its normal lifecycle transition.

Generated heartbeat prompts include prequota only when the host declares
`--available-capability external_evidence_poll`; filesystem/shell declarations
alone do not opt in. Omitting that declaration keeps external observation off.
Explicit prequota invocation authorizes compact metadata reads and local
observation writeback, never merge, review, remote mutation or quota spend.
Read back with `todo list --goal-id GOAL --todo-id TODO --format json`.
These same typed dependency facts are the boundary to reuse in TurnEnvelope;
this heartbeat adoption does not claim that every Turn host already polls them.

`pr_merged:owner/repo#42` 只标识依赖；缺少合并事件不证明 GitHub 仍在等待。
历史交付监督不再豁免这种未观察等待，因果阻塞结算仍要求已注册的
`monitor_changed` 或 `todo_done` 目标及当前回读。
原生 `heartbeat-prequota --goal-id GOAL --agent-id AGENT` 从完整 Todo 源选择
精确的未完成 PR 依赖（含 deferred），每次最多核验四个不同仓库限定的依赖。
成功和失败均保留 30 分钟核验节奏，重启后仍有效。仓库缺失需要修复，不能按编号猜测。
真实合并观察写入既有 `pr_merge`，原条件自然满足；离开 open 列表、关闭未合并或
provider 失败不能解除等待。观察不修改 Todo 状态、要求、认领、lease 或 Goal 验收；
已满足条件的 deferred Todo 仍走正常生命周期转换。
生成的 heartbeat 仅在声明 `--available-capability external_evidence_poll` 时加入
prequota；只声明 filesystem/shell 不会启用。省略该声明即关闭外部观察。
显式调用仅授权紧凑元数据读取和本地观察记账，不授权合并、review、远端写入或额度支出。
通过 `todo list --goal-id GOAL --todo-id TODO --format json` 回读。
TurnEnvelope 应复用同一类型化依赖事实；本批未证明所有 Turn host 已接入自动核验。

## Relationship evidence

The policy evaluates the complete available Todo graph before role, status,
Agent, ID or display-limit selection. It recognizes explicit
`successor_todo_ids`, `superseded_by`, and advancement records pointing back
through `unblocks_todo_id` or `resume_when=todo_done:<source>`.

- A declared successor must exist and differ from the source. A dangling or
  self reference does not close work. A retained archived record remains
  relationship evidence; it does not become active work.
- Explicit links retain their existing role-neutral meaning. Inferred
  successors require an advancement task. `monitor_changed` is a resume
  condition, not an inferred work successor.
- An existing successor records continuation lineage. It does not prove that
  the successor has executed, been accepted or acquired a lease. This is not a
  transitive Goal acceptance proof or a cycle-freedom certificate.
- Basic historical checkboxes without structured execution context keep their
  compatibility behavior. Explicit no-follow-up remains an independent closeout
  choice. Deferred work is never classified as a completed advancement gap.

Both completed-work warnings and handoff gates use that same graph. Previously
handoff ignored explicit successor lists, while completed-work warnings accepted
nonexistent/self links. Filtering or archiving a valid inferred successor could
also manufacture a warning that was absent on the full source.

| Handoff facts, in precedence order | State |
| --- | --- |
| Existing, non-self supersession target | `superseded` |
| Deferred source | `deferred` |
| Source has not completed | `blocking` |
| Completed with explicit no-follow-up | `cleared_no_followup` |
| Completed with a resolved successor | `cleared_with_successor` |
| Completed without either | `cleared_without_successor` |

Only active dependency-linked executor exclusions are handoff gates. These
states describe the gate; none changes claims, grants, leases or stored Todos.
The existing legacy stale-closeout prose hint remains a compatibility adapter
until route-closeout writers supply the explicit replan flag. Its substring
matching can overmatch narrative and is not used for successor resolution,
handoff state or permission. An explicit boolean replan flag takes precedence.

## Selection, proofs and transport

A fresh full-source evaluation accompanies each internal summary row as an
ephemeral Python attribute, outside dictionary fields and JSON serialization. Its fact
digest prevents reuse after relevant item edits; it is a consistency check,
not authentication. Fresh parsing/canonical reads always recompute it rather
than trusting stored evaluations. Shadow capture discards this derived field;
canonical records and durable source digests do not gain a second authority.
Public parser rows keep their existing dictionary schema. Final list/status
responses copy plain dictionaries, retaining decision fields without exposing
the internal evaluation or expanding the hot-path payload.

When choosing between Agent and User frontier waits, compare UTC instants,
not the original ISO spelling retained by a projected deadline. Equal instants
retain source order; due/expired exclusion and pre-compaction evidence are
unchanged. An earlier deadline cannot be postponed by a different UTC offset.

The existing Python deadline rule remains until it can join an already-needed
whole-consumer typed batch. A separate projection RPC adds hot-path latency;
retiring a small rule alone does not justify that cost. This correction grants
no Todo, lease, settlement or host-schedule authority and does not qualify
provider defaults or actual host backoff adoption.

Legacy archive/recreate can retain one archived and one active record with the
same logical Todo ID. The active record owns that ID's inferred edges regardless
of source order; archived metadata cannot supply stale edges for the replacement.
Two active or two archived records with the same ID remain ambiguous and reject.
This read precedence does not relax canonical capture's unique-identity contract.

A status/ID/Agent-filtered list describes that selection but emits no Goal-source
or terminal-closure proof. A display limit alone does not change the source:
counts, warning decisions and proof eligibility are computed first. Handoff
state, successor count and executor exclusions survive the bounded list view.

Terminal closure additionally requires no deferred/convergent work, unresolved
handoff, successor gap or route-replan obligation. Watch-only monitors retain
the existing convergent-work exception. It remains separate from Goal acceptance.

Field-presence sets are interned inside a succession RPC request so long archive
histories do not repeat identical metadata shapes. The full graph is retained;
no record sampling, per-page rule evaluation or RPC limit increase is used.

## Migration and operation

The shared archive-capture owner retains the reachable continuation graph as
well as resume dependencies and standing decisions. It preserves real record
status, including deferred history: capturing a deferred record does **not**
satisfy `todo_done`. Duplicate identities, invalid archive state and incompatible
role/authority combinations still reject capture. Unrelated archive records
remain outside the bounded canonical capture.

For a historical record with an explicit `role=agent` but no `task_class`,
capture retains the same class as the legacy active read. The Python Markdown
codec sends that compatibility classification as `legacy_task_class`, separate
from recorded metadata; the TS selector accepts it only for a recorded Agent
role and an Agent-compatible class. Explicit classes take precedence. Missing
roles still require an explicit Agent-only class; user authority, contradictory
scope and unknown classes cannot be reconstructed from text. The selected class
also drives inferred successor closure, so dependency traversal and materialized
records agree. The existing legacy text classifier remains a compatibility
codec, not a second authority-admission rule or a new classification heuristic.

New legacy Agent archive moves persist the read classification when the source
omitted it, alongside the source role. Unknown metadata and original receipt
lines remain intact. Existing archives are not rewritten by capture.

The internal request is `todo_archive_dependency_capture_request_v2`, and the
result is `todo_archive_dependency_capture_result_v1` with the selected class.
Python and the bundled TS runtime must be upgraded together; an older runtime
rejects the new request instead of silently losing classification or edges. Existing
historical capture/promotion receipts are not rewritten or upgraded in place.
Requalify capture on this runtime before a future promotion.

Use existing read commands; no activation or new option is needed:

```bash
loopx --registry registry.json todo list --goal-id example-goal --format json
loopx --registry registry.json todo list --goal-id example-goal --todo-id todo_source --limit 1 --format json
```

Completion retries also close a receipt/head read race: if the first receipt
lookup misses a peer commit but the head already shows completion, recheck the
matching operation receipt before interpreting a supplied validation receipt.
This returns the committed result without repeating effects; no matching
receipt still follows the existing validation and identity guards.

Reads do not repair Markdown, mutate Todo/lease state or replay a business
operation. Missing promoted Markdown is acceptable; an unavailable provider is
not an empty Goal. No frontend configuration changes are needed: CLI, manager
Chat details and existing status/quota consumers retain their current entry
points. To reverse a business decision, use its ordinary mutation, not a read
model or restored Markdown. Code rollback retains provider state and fences;
old read policies can again misclassify these cases.

## 中文

“这个 Todo 已完成”与“这一段工作已闭环”不同。结构化推进任务完成后，要有真实
存在的后继，或明确声明 `no_followup=true`。TS 现在统一解析显式后继、替代关系和
反向交接关系；Python 保留旧输入规范化与展示。不存在的 ID、自指 ID 不再遮住
未闭环工作，归档与筛选也不再凭空制造后继缺口。

关系在完整可用源上判定，然后才筛选、分页。按状态、ID 或 Agent 筛出的列表不能
为整个源出具闭环证明；仅限制显示条数不会改变完整源上的计数与判断。handoff 的
状态与排除执行者信息不会在压缩展示时丢失。派生判断带相关事实摘要以防陈旧复用，
但不是授权凭据，也不写回 provider。旧 prose replan 提示仍仅用于兼容；显式
布尔标记优先，不能靠标题里的几个词推导后继存在或授予权限。

归档捕获现在保留与当前工作有关的后继图和原有依赖、standing decision。延后历史
可以被保留，但状态仍是 deferred，绝不会因此满足 `todo_done`。新的内部 v1 请求
要求 Python 与 TS 配套升级；旧回执不被重新解释。长历史重复字段集合采用无损共享，
没有放宽 RPC 上限或丢弃历史节点。

本阶段关闭一组 T3/L5 读语义及其 L7 捕获依赖，不代表 D1 投影投递、D2 耐久性、
D3 整 Goal 切换完成，也不修改默认 provider。PostgreSQL 使用相同规则，服务部署与
资格仍独立。复杂 fixture 和只读快照演练不是长期 soak 或生产晋升许可。
