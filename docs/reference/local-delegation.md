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
before enabling LoopX mode. Expand it to recover paged work and artifact hashes,
or check one member's prerequisites. Reads run only on request; ordinary polling
does not run validators or preflight. Closing the panel changes no work state.
This local operator entrypoint does not grant a Lark audience access.

中文：配置原有执行绑定后，在 Goal 对话中展开「团队执行情况」，不必让模型转述或
跳到另一个页面。可分页查看原请求、当前验收与产物哈希，点「检查启动条件」读取
指定任务的 Turn 决策及实际模型配置。运行时未探测时明确显示「尚未验证」，不能
把已登记、已分配或 dry-run 成功当作正在运行。暂停时仍可检查；检查不启动成员。

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
