# Local delegation through governed Turns

An existing local Agent can launch explicitly bound peer work and reconnect to
its result after the requesting conversation disappears. The same interface is
available to a coordinating member. DSH and an optional cloud provider use the
existing Turn entrypoint; there is no steward-specific scheduler or task store.

## Activate

First register the participating Agents and bind the intended canonical Todos
to [owner-configured acceptance](goal-acceptance-observations.md). Prepare an
operator-owned JSON file **outside every delegated member workspace**. A
coordinator may keep it as an ignored file under its Goal project at
`.loopx/config/delegations.json`:

```json
{
  "schema_version": "loopx_local_delegation_v0",
  "bindings": [{
    "id": "independent-review",
    "agent_id": "reviewer",
    "todo_id": "todo_review",
    "requesters": ["lead", "analyst"],
    "workspace": "/absolute/reviewer-worktree",
    "host_args": ["--host", "dsh", "--dsh-model", "your-configured-model"],
    "timeout_seconds": 300,
    "output_refs": ["output.json"]
  }]
}
```

`requesters` is an execution grant for this exact binding. Registration, a peer
message or a coordinator role does not grant it. Host arguments are trusted
operator configuration and use the existing `turn run-once` options. For Ark,
select `generic-cli`, `fresh`, and the optional adapter's `--config` invocation.
Profiles, executables, workspace isolation and credential custody remain the
operator's responsibility. No model tool accepts those values.

A Codex binding launches an independent, resumable Codex Agent Session through
the same governed Turn path. Pin both fields when the worker must use an exact
profile:

```json
{
  "id": "strong-independent-review",
  "agent_id": "managed-reviewer",
  "todo_id": "todo_review",
  "requesters": ["lead"],
  "workspace": "/absolute/reviewer-worktree",
  "host_args": [
    "--host", "codex-cli",
    "--codex-model", "gpt-5.6-sol",
    "--codex-reasoning-effort", "xhigh",
    "--codex-sandbox", "workspace-write"
  ],
  "timeout_seconds": 300,
  "output_refs": ["output.json"]
}
```

This is not a native `multi_subagent` child. Native children remain temporary
workers inside one parent execution and use the Goal's child model preference.
The binding above has its own Agent identity, Todo, workspace, Codex Session and
durable delegation operation. `delegation inspect` and the planning projection
show `gpt-5.6-sol@xhigh`; start and resume pass both fields to that same Session.
The requester still needs the exact binding grant, and a profile is not an
acceptance or result-return receipt.

中文：Codex binding 通过同一条受治理 Turn 链启动独立、可续接的 Codex Agent
Session。需要精确执行配置时同时固定 `--codex-model` 与
`--codex-reasoning-effort`。它不是 `multi_subagent` 的原生临时 child：后者仍在
单个父执行内部使用 Goal 的 child model 偏好；前者拥有独立 Agent 身份、Todo、
workspace、Codex Session 与持久 delegation operation。`delegation inspect` 和
规划投影会读回例如 `gpt-5.6-sol@xhigh`，start/resume 也把同一配置送入原 Session。
这不扩大 requester grant，也不把 profile 冒充验收或结果返回回执。

For a Codex binding, the delegation host also supplies one invocation-scoped
`loopx_delegation` stdio MCP server to every fresh or resumed worker Session.
Its command pins the selected worker `agent_id`, workspace, Goal, registry,
runtime and operator execution configuration before Codex starts. The model
cannot select or rewrite those values. Codex receives the server through
per-invocation configuration, so LoopX does not modify the user's global Codex
MCP settings and a resumed worker keeps the same binding. The server is required
for this managed worker route and its already identity-scoped tools are approved
inside that route; failure to start the server rejects the Turn instead of
silently continuing without tools. The native tools are
the collaboration and authorized delegation operations from that bound server;
they do not add shell, Todo or external-action authority.

The shell commands below remain the compatibility path for an already running
Agent Session, a host without MCP support, or an operator who deliberately uses
shell-only coordination. Both surfaces call the same `Delegations` service and
preserve the same binding, operation and acceptance rules; the shell path is
not a second control-plane implementation.

中文：Codex binding 会为每个新建或续接的 worker Session 注入一次调用范围内的
`loopx_delegation` stdio MCP server。启动前，host 已固定 worker `agent_id`、
workspace、Goal、registry、runtime 与 operator execution configuration；模型不能
选择或改写这些值。该配置不会修改用户的全局 Codex MCP 设置，也不会授予 shell、
Todo 或外部动作权限。下方 shell 命令继续作为既有 Session、无 MCP host 或显式
shell-only 协调的兼容入口；两种入口复用同一个 `Delegations` 服务和同一套验收规则。

## Use an existing Agent conversation through its shell

An attached Codex or other shell-capable Agent can use the same execution
bindings without opening a replacement conversation or adding MCP tools to a
running session. Use its registered requester identity and the exact registry,
runtime and operator configuration; this trusted local CLI is not a remote
authentication boundary.

```bash
delegate() {
  loopx --registry "$REGISTRY" --runtime-root "$RUNTIME_ROOT" --format json \
    delegation "$@" --goal-id "$GOAL_ID" --agent-id "$AGENT_ID" \
    --execution-config "$DELEGATION_CONFIG"
}

delegate list
delegate inspect --binding-id independent-review
delegate start --binding-id independent-review --operation-id review-round-1 \
  --brief-file request.json --execute
delegate read --operation-id review-round-1
delegate wait --operation-id review-round-1
```

`request.json` contains the same `collaboration_brief_v0` used by MCP:

```json
{
  "schema_version": "collaboration_brief_v0",
  "purpose": "Independently check the current analysis",
  "context": "Reconcile the corrected source with the earlier conclusion.",
  "constraints": ["Use only the supplied material; no external actions"],
  "inputs": [],
  "acceptance": ["Satisfy the task's pinned independent acceptance"],
  "return_requirement": "Return evidence, uncertainty and the checked artifact"
}
```

The Agent chooses questions, sequencing and synthesis. After `start` returns,
it can continue its own investigation; closing that CLI process does not stop
the worker. Another invocation reads the original operation. `wait` observes
for a bounded interval and does not start, resume or accept work. `ok: true`
means the command succeeded; inspect `status`, `recovery_required`, `error` and
the independently checked artifacts to determine the work result. Neither a
`running` result nor a saved peer opinion means accepted completion.

After a lost start response, repeat the same start with the same operation id
and brief. If readback reports `recovery_required`, use:

```bash
delegate resume --operation-id review-round-1 --execute
```

Resume keeps the original operation and Turn; it cannot silently retarget
work. A new scope or repair round requires a new operation, still subject to
the configured task, quota and acceptance owners. A member coordinating its
own authorized peers supplies `--parent-request-id` on start. CLI and MCP
share grant validation, detached execution, wait/readback and recovery rather
than maintaining separate rules.

This entrypoint does not create Agents, grant bindings or wake an idle Codex
conversation. The existing host/LoopX continuation policy owns the next lead
turn. The conversation remains persistent independently of whether autonomous
LoopX mode is enabled. Dashboard, CLI/managed Turn and Lark keep their existing
conversation and runtime owners; they may consume the shared bounded route
projection described below, but they do not get another grant or scheduler.

### Bind a later result to an exact accepted version

A brief input may opt into requester-scoped provenance:

```json
{
  "ref": "inputs/accepted-analysis.json",
  "description": "Accepted analysis to use in the synthesis",
  "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "delegation": {
    "operation_id": "analysis-round-2",
    "ref": "output.json",
    "relation": "uses"
  }
}
```

Use the real digest from `delegate read`, not the illustrative digest above.
`ref` addresses the receiving workspace's already supplied file; `delegation.ref`
addresses an output of this requester's original operation. The source must be
currently accepted and both files must match the specified hash. Start checks
before dispatch and checks again before completing the consumer. It neither
copies files nor grants workspace access. Existing briefs without `delegation`
retain their behavior and readback shape.

The typed relations are **requested intent**: `responds_to`, `revises` and `uses`.
They do not assert that an objection is correct, a revision resolves it or an
Agent has adopted a result. `read` returns the immediate dependency's specified
version and current/unavailable observation; it does not flatten a whole team's
graph or infer relationships from prose.

After inspecting the independently accepted downstream result, the requester
may explicitly record adoption into that result:

```bash
delegate adopt --operation-id analysis-round-2 \
  --consumer-operation-id synthesis-round-1 --execute
delegate read --operation-id analysis-round-2
```

Both executions must belong to this requester and remain accepted under their
current bindings, canonical Todos, pinned validators and saved artifact hashes.
The consumer's immutable brief must reference the source through `uses`, and
its supplied input must still match. The receipt binds source hashes, consumer
hashes and original request/task identities. Repeating the same decision is
idempotent. At most twelve downstream adoption records can be attached to one
source. Reading, receiver request acknowledgement and completing a consumer
never create this requester decision automatically.

Every read rechecks recorded adoption evidence. Changed output, missing input,
revoked binding or failed acceptance makes that relationship `unavailable`;
the historical reference remains visible, but its saved success is not replayed.
This is evidence of the requester's explicit decision and a validated downstream
artifact, not proof of model comprehension or arbitrary semantic claims. Domain
acceptance must check substantive dependency use; matching an input hash alone
cannot establish it. No Goal is completed and no new model is launched by adoption.

MCP exposes `adopt_delegation_result(operation_id, consumer_operation_id)`;
newly created Goal Chat tool sessions expose `action=adopt` with those fields.
Existing native sessions keep their original schema; use the existing shell CLI
when their tools do not include the action. Host tool approval still applies.
The local Goal Chat evidence panel shows these version links and requester
receipts, with direct navigation to source/downstream evidence and explicit
missing or stale adoption. This owner-only surface does not grant Lark or shared
audiences access. Stop supplying the optional provenance input to disable it for
new work; existing decisions remain auditable and revalidated, not deleted.

中文：输入可显式绑定当前请求方某次已验收产物及其哈希。`responds_to / revises / uses`
只表达请求关系；不从文字或完成状态推导纠偏、采用。请求方检查后续已验收结果后，
通过 `adopt --execute` 显式记录采用；读回重新核验源版本、接收方输入及后续产物。
版本或验收失效时保留历史引用并显示不可核验，不重放旧成功。领域验收仍须验证实际
使用了依赖及结论正确性。前端可沿关系打开证据、反馈或暂停协调员；整体 Goal、成员
停止与跨受众权限均不因此改变。

### Publish bounded route readiness to the coordinator

After the ignored operator file exists, register only its Goal-relative pointer
through the existing orchestration configuration:

```bash
loopx configure-goal --goal-id "$GOAL_ID" \
  --subagent-execution-config .loopx/config/delegations.json
loopx configure-goal --goal-id "$GOAL_ID" \
  --subagent-execution-config .loopx/config/delegations.json --execute
loopx agent-context --goal-id "$GOAL_ID" --agent-id "$AGENT_ID" \
  --phase before_plan --format json
```

Preview before apply. The pointer accepts only a repository-relative JSON path
under `.loopx/config/`; symlinked or missing files yield a typed blocked
observation. The registry does not copy the file. The same requester grant
filter used by `delegate list` considers at most six public-safe planning routes
and byte-bounds the projected subset; `authorized_count` and `routes_truncated`
make omissions explicit. Managed-host availability comes from the existing Turn host/profile owner;
unprobed generic adapters are `unknown`, not optimistically ready.

This planning projection is read-only. It does not start, resume, accept,
enumerate operations or periodically poll work. A ready observation is not an
execution receipt. Use the original binding and a stable operation id for
dispatch, then read and validate the original artifacts. An explicit
`agent-context --phase after_delegate_result` read may summarize current
requester-scoped operation statuses; those counts are not parent acceptance.
Remove only the pointer with
`--clear-subagent-execution-config --execute`; revoke actual admission in the
operator binding file.

For compatibility, an unfinished Goal Chat run created before the Goal-owned
pointer was available can resume with its frozen Session reference. That path
ends with the run: a new or completed run must use the Goal configuration, and
no legacy Session setting is copied back into the registry automatically.

中文：本地授权文件可放在协调者 Goal 仓库中已忽略的
`.loopx/config/delegations.json`，但必须位于所有被委托成员工作区之外。先用
`configure-goal` 预览，再执行写入；注册表只保存仓库相对指针，不复制授权内容。
`agent-context` 复用 `delegate list` 的 requester 范围，最多投影六条公开安全的
路由状态；managed runtime 的可用性由既有 Turn host/profile owner 判断，未探测的
通用适配器显示 `unknown`，不能乐观宣称 ready。规划投影不会枚举 operation，也不会
启动、恢复、验收或周期轮询工作；需要时可显式读取 `after_delegate_result` 阶段的有界
operation 状态摘要。ready 和状态计数都不是执行或父级验收回执。清除指针不会撤销
授权；真正撤销仍须修改 operator binding 文件。

### Recover work without remembered operation ids

After reconnecting or losing conversation context, use the same registered
requester and execution configuration:

```bash
delegate operations --limit 10
# When has_more is true, copy next_cursor from that response:
delegate operations --limit 10 --cursor "$NEXT_CURSOR"
delegate read --operation-id "$ORIGINAL_OPERATION_ID"
```

This reads the existing requester-scoped journal, including work created from
another conversation under that identity. Each item includes its original
operation/request/task identity and current execution readback. Accepted items
are independently rechecked against current canonical completion and artifacts;
the page includes artifact references/hashes, while `read` supplies full content.
One changed binding, corrupt record or invalid artifact yields `unavailable`
for that item and `page_readback_complete: false`; healthy siblings remain
visible. This is a reconciliation case, not permission to dispatch a replacement.
Failure to read the journal itself fails the command instead of returning empty.

Pages contain at most 50 items. `has_more` is independent of page readback
completeness. Accepted-item checks rerun the existing pinned validators; use a
smaller page when those checks are expensive. Inventory is requested on demand,
not added to the dashboard polling loop. The cursor follows stable record addresses, not business priority;
this is a live listing, so restart paging to discover new records inserted before
the cursor. An empty page for one requester says nothing about other members or
whether the Goal is complete. Only explicit `start`/`resume` can launch execution.

Enabled MCP exposes the same operation as `list_delegations`. Newly tool-equipped Goal Chat
uses `loopx_collaboration` with `action=operations`, optional `limit` and `cursor`.
It retains its existing sender/configuration pin and pause fence. Both the lead
and a coordinating member recover their own operations; creation ancestry grants
no access to another requester's journal. No new settings or background polling
are required, and disabling execution tools removes this tool with them.
Already enrolled native Chat threads keep their original tool schema on resume;
they are not replaced to install this new operation. Recovery guidance is part
of the new tool description, not injected into those older threads' shared prompt.

中文：原对话重连后执行 `delegate operations`，不用先记住每个 operation ID。
主力与承担协调的成员各自找回自己的工作，再用原 ID 读取完整结果；需要恢复时
仍显式调用 `resume --execute`。分页回读会重新核验 accepted，单条失效显示
`unavailable`，不能当成失败重派或静默隐藏。`has_more` 表示还有下一页，
`page_readback_complete` 只表示本页是否均成功读取；二者都不代表整个团队已完成。
此入口不创建 Agent、不扩大授权，也不唤醒闲置的 Codex 对话。

### Check a binding before new work

`delegate inspect --binding-id independent-review` uses the same task,
workspace, host, model/effort and validator arguments as an actual delegation,
through `turn run-once` without `--execute`. It creates no request or Turn,
does not invoke the host and spends no quota. Host arguments that enable
execution or retarget the selected work are rejected before the subprocess.

The typed result keeps three facts separate: `turn_eligible` is the current
Turn decision for that exact Todo; `acceptance_ready` is the current pinned
acceptance binding, not passed output validation; `executor.available` uses
the existing host probe. `false` means unavailable, while `null` means the
runtime has not been probed. In particular, the generic-cli path used by the
optional Ark adapter does not acquire a remote readiness guarantee from a
successful local dry run. `launchable` only means those local prerequisites
were observed; it grants no execution permission and does not reserve capacity.
Normal start still reads current admission and independently validates output.
If the existing Turn rejects preflight, inspection reports that error rather than
manufacturing a launchable result; no request is created.

Enabled MCP exposes `inspect_execution_binding`; newly enrolled Goal Chat tools
accept `action=inspect` with `binding_id`. Existing native thread schemas remain
unchanged. Owners can use **Team execution** directly below the Goal conversation
controls after configuring its existing bindings, including while paused or
before enabling LoopX mode. Select **Evidence and feedback** on an original operation to read its bounded
artifact content. This uses the same `delegate read` owner: bindings, pinned
acceptance, canonical completion and current file bytes are checked again.
Changed or unavailable evidence clears the prior content. These are on-demand
observations, not continuous liveness; accepted output does not prove requester
adoption. Text is rendered inertly, and source/version identifiers remain
inspectable. Returning preserves the execution list and keyboard focus.

Configured Goal conversations also expose **Team results** in the main view.
Select an accepted artifact to recheck its exact operation, reference and hash
before reading. Markdown reports use the existing inert renderer, including
readable tables; **View source** preserves the original bytes. JSON and other
text remain source evidence, without inferred prose or changed numeric values.
When several artifacts are returned, prefer the Markdown report. Refresh and
pagination are explicit; a failed read clears the previous report. Expand
acceptance/adoption separately: showing a result does not finish the Goal or
inject a coordinator answer. Reading starts no model or member work.

中文：配置后的 Goal 对话主界面提供「团队成果」，优先选择 Markdown 报告。
点击后按原执行、文件与哈希重新核验；Markdown 以可读段落和表格展示，原文可切换，
JSON 等文本保留原值，不推断成结论。刷新和翻页显式操作，核验失败清除旧报告。
验收/采用关系单独展开；显示产物不代表 Goal 完成，也不伪造协调员回复或启动模型。

For accepted work with version-bound dependencies, **See what changed** lets you
select a referenced source and read it beside the output. Multiple outputs have
an explicit selector, defaulting to the same reference, then the same file type,
then the first available artifact. This is a reading convenience, not semantic
equivalence. Read reports by default; **View source changes** reveals the raw diff. The source operation, artifact reference and SHA-256 must
match the dependency; an unavailable or changed source clears the previous
comparison instead of substituting newer bytes. Highlighting marks the range
containing text changes, not semantic correctness or requester adoption. The
panes stack on narrow screens, and verified version identifiers stay expandable.
This uses the existing read operation and introduces no execution authority.

中文：在已验收且带版本依赖的执行中，选择“看清这次变化”下的一份依据，
与本次产物并排阅读；默认优先同名、再同类型产物，仍可显式切换。这只是阅读选择，
不证明语义相同。默认阅读报告，可切换原文差异。来源 operation、文件与哈希必须匹配，
来源失效会清除上次对照，不能用新文件替代。高亮只表示文本变化范围，
不代表正确或已采用；窄屏上下排列，版本标识仍可展开。这不增加执行权限。

Synthetic fixture views: [desktop comparison](../assets/personal-workspace/team-comparison-desktop.png),
[mobile comparison](../assets/personal-workspace/team-comparison-mobile.png), and
[unverified source](../assets/personal-workspace/team-comparison-unavailable.png).
The literal script text in the fixture demonstrates inert artifact rendering;
these are validation screenshots, not a real research result.

While the original coordinator is executing, feedback includes the selected
operation and observed artifact hashes in its existing inbox. Pending and delivered
receipts stay distinct from application; a retry after an uncertain response
reuses the exact message and operation id. **Pause coordinator** stays in the
panel and reports its actual scope. Dispatched members continue independently;
this entrypoint cannot stop the whole team. Ordinary polling does not read artifact
bodies or run preflight. Closing the panel changes no work state. This local
operator entrypoint does not grant a Lark audience access.

中文：配置原有执行绑定后，在 Goal 对话的「团队执行情况」中选择原执行的
「查看证据与反馈」，直接读取经当前验收、绑定和文件核验的产物正文。文件变化或
读取失败时清除旧内容；这是按需观察，验收通过不代表协调员已采用。来源和版本标识
可展开查看，返回列表保留位置与键盘焦点。协调员运行时，可把执行标识、看到的
产物哈希和反馈投递到原收件箱；等待投递、已交付和已应用不能混为一谈。不确定响应
后重试同一消息和标识，避免重复投递。面板内的「暂停协调员」显示实际反馈，但不会
停止已派发成员，也不宣称整个团队停止。暂停时仍可检查证据；读取不启动模型。

Screenshots use isolated synthetic research data, not a live-model qualification:
[desktop evidence](../assets/personal-workspace/team-evidence-desktop.png),
[mobile evidence](../assets/personal-workspace/team-evidence-mobile.png), and
[stale evidence](../assets/personal-workspace/team-evidence-stale.png).

## Use the same bindings through MCP

Start the existing stdio server with the explicit opt-in:

```bash
python -m loopx.collaboration_mcp \
  --registry "$REGISTRY" --runtime-root "$RUNTIME_ROOT" \
  --goal-id "$GOAL_ID" --agent-id "$AGENT_ID" --workspace "$WORKSPACE" \
  --execution-config "$DELEGATION_CONFIG"
```

Without `--execution-config`, the original five collaboration tools are
unchanged and cannot launch workers. With it, the Agent can:

1. Call `list_execution_bindings` to find its authorized work.
2. Call `start_delegation(binding_id, operation_id, brief, parent_request_id?)`.
   Supply the existing `collaboration_brief_v0`, including purpose, context,
   constraints, inputs, acceptance and return requirement. Reuse the operation
   id after a lost response; changed content under the same id is rejected.
3. Continue other work, or call `wait_delegation` for a bounded wait. A `running`
   response is normal. `read_delegation` reads the durable original operation.
4. If `recovery_required` is true, call `resume_delegation` with that same id.
   This cannot retarget the work or silently create a replacement Turn.

Configure the member's host to expose its own identity-bound collaboration
tools. It reads `DELEGATION.json`, independently calls `assess_request`, and
produces the bound artifact. A nested coordinator uses its own grants and
forwards `parent_request_id`; the original semantic context is retained.

## Acceptance and return

The Turn validator reads only this task's current pinned criteria from the TS
acceptance owner. After a validated Turn, ordinary `todo complete` executes the
criteria again and commits through the existing canonical authority. The host
then reads current completion, binding guards and artifacts before returning
`accepted`. The overall Goal remains independent of this task result.

A member's peer conclusion is preserved. It is an opinion/evidence message,
not canonical completion; the host does not overwrite it with another reply.
When the member has not written a conclusion, the host returns compact
completion references through the existing peer return route. Reading a saved
accepted operation revalidates current artifacts and bindings. Edited output,
stale work, missing adoption and forged result files cannot certify completion.

The operation receipt lives under the runtime's existing private collaboration
storage (`.local/manager-context/executions`). It records execution observations,
request lineage, the original Turn key and bounded results. It does not replace
canonical Todo, claim, lease, quota, or acceptance ownership. Business ordering,
questions, repair decisions and synthesis remain Agent decisions.

The existing `loopx.collaboration_mcp` host owns tool serving, detached worker IO
and the Turn validator entrypoint. Typed execution grants and observation
transitions remain in the collaboration TS boundary. A worker waits through a
brief status-read lock before deciding another worker owns the operation;
concurrent executions still use the same kernel lock and original Turn journal.

## Disconnect and recovery

| Interruption | Behavior and recovery |
| --- | --- |
| Requesting MCP conversation closes | The detached bounded worker continues; another connection reads the original operation. |
| Duplicate start/resume while work runs | Operation identity, task lock and Turn journal prevent another concurrent execution. |
| Worker process or machine stops | Reconnect with the same operator configuration and credentials, then resume the original Turn. |
| Ark is computing without local tools | The already-started cloud turn can continue. It is not dependent on the local conversation. |
| Ark requests a local tool while the host is absent | It waits for the local tool result. Recovery observes the original input/session and executes only previously unstarted tool calls. |
| Tool execution or send acknowledgement is uncertain | Do not repeat the effect. Preserve the receipt/session for explicit reconciliation. |
| Task completed but return was interrupted | Read/validate the original task and return; do not rerun the model. |

Ark recovery retains the original execution deadline; reconnecting does not
reset the budget. Lost creation/input-send responses remain reconciliation
cases. This is a local trusted-host facility, not authenticated remote control,
automatic boot supervision, general live steering or a guarantee that an entire
team continues through a host outage. It introduces no frontend/Lark settings
or default executor change; those existing configuration surfaces are untouched.

To disable new admission, remove the caller's grants or remove
`--execution-config` from the host. A stopped Goal refuses new starts/resumes;
existing completed results remain readable. Disabling does not kill work already
running. Retain receipts, stop or reconcile owned workers, and confirm cloud
resource cleanup before deleting a disposable runtime. The optional adapter's
cleanup command never grants task completion.

For the mixed and nested research journey, see the
[synthetic research team](../../examples/managed-research-team/README.md).
