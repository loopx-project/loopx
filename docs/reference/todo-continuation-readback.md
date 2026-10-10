# Todo continuation and closure readback

Todo list, status and quota distinguish a completed record from a closed work
slice. A completed tracked advancement Todo still needs an existing successor
or an explicit `no_followup=true`. This read policy lives in
`control_plane/todos/succession.ts`; Python normalizes legacy input and renders
its decisions. It belongs to the existing Todo control plane, uses the selected
AuthorityStore, and adds no capability or extension provider.

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

Todo mutation adapters take the existing archive limit and headroom from
`completed_archive`, avoiding a reverse import of the status collector. Both
canonical transport and the retained unpromoted writer keep the command's
default of ten completed records; status compatibility exports remain intact.

Handoff display is another live compatibility boundary. Status/index, summary
and selected-work consumers still use the Python handoff adapter; its stable
identity, nested-field precedence, bounded source references and credential
rejection must survive retirement. Credential assignments are rejected before
display truncation; ordinary discussion of token budgets remains readable.
Canonical exact-Todo reads return the source record, while status adds derived
handoff context. Neither context grants execution authority or proves completion.
The [handoff characterization tests](../../tests/control_plane/test_todo_handoff_retirement_contract.py)
exercise these distinctions and real File/SQLite CLI readback with legacy
mutation modules absent. They enable a later whole-consumer typed migration;
the same fixture also takes a normally built wheel, installs it into an isolated target and
runs its installed console entrypoint against both real providers, with the old
writer modules and four shadow capture producers physically removed. Module,
distribution and packaged TypeScript provenance are checked before those CLI
journeys. Creation recovery preserves later writes and the original provider;
provider loss refuses admission, while leased settlement and monitor retries
retain their original receipts without repeating effects. This is a disposable
retirement oracle, not adoption on an active Goal, old-backup import/restore
qualification or final writer retirement. Preserve the existing adapter until
that migration replaces its real
callers, rather than adding a projection RPC for every displayed Todo.

Ordinary pytest runs keep the source arm. To require the installed retirement
arm, first prepare the qualified Chat bundle and build the wheel through the
normal distribution build, then supply that artifact explicitly. A supplied
missing/invalid wheel fails the run; it is never converted to a source-only pass.

```bash
LOOPX_TODO_RETIREMENT_WHEEL=path/to/built.whl uv run --extra test python -m pytest tests/control_plane/test_canonical_todo_writer_isolation.py tests/control_plane/test_todo_handoff_retirement_contract.py -q
```

This variable selects test coverage only; it grants no production access or
feature authority. No setting is installed or persisted. Omit the variable to
run the ordinary source arm. Installed test results qualify only the supplied
artifact and removed paths.

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

handoff 展示仍有真实 Python 调用方，退役时必须保留稳定身份、嵌套字段优先级、
有界源引用及截断前的凭据拒绝；普通 token budget 讨论仍可展示。canonical 精确
Todo 读取返回源记录，status 才加入派生交接上下文，两者都不授予执行权限或证明
完成。上述测试覆盖旧 mutation 模块缺席时的真实 File/SQLite CLI 读回，只为后续
整组消费者迁入 TS 建立兼容基线。同一 fixture 还将正常构建的 wheel 安装到隔离目录，
用其实际 console 入口在两种真实 provider 上运行；旧 writer 模块与四个 shadow
capture producer 物理移除，先核对模块、distribution 和打包 TS 来源。创建重试
保留后来写入与原 provider，provider 缺失拒绝准入，lease 结算和 monitor 重试
保持原回执且不重复效果。这是一次性隔离退役验收，不是活跃 Goal 的安装采用、
旧备份导入恢复或最终 writer 退役。替代真实调用方前保留既有适配器，不为每个
展示 Todo 新增一次投影 RPC。

普通 pytest 保留源码验证。完成 Chat bundle 的正常构建并生成 wheel 后，用上文
`LOOPX_TODO_RETIREMENT_WHEEL` 指定产物，才运行安装态退役验证；指定的 wheel
缺失或无效会失败，不降级为源码通过。此变量只选择测试覆盖，无生产或功能授权，
不持久化配置；省略变量恢复源码验证。结论仅覆盖指定产物与实际移除路径。

Todo mutation 适配器直接复用 `completed_archive` 的既有阈值与余量，消除对 status
collector 的反向导入；canonical transport 与保留的旧 writer 仍默认保留十个完成
记录，status 的兼容导出不变。
