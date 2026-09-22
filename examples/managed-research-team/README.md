# Synthetic research team

A local coordinator organizes two local DSH Agents and two cloud Ark Agents
to analyze a filing and its correction. An Ark reviewer adopts a local
analyst's result, independently checks it, and returns evidence for the local
coordinator's combined report. There is no `phase` argument or script that
selects the next business step. The model chooses questions and delegation order
through the [shared local delegation interface](../../docs/reference/local-delegation.md).

This composition example prepares an isolated synthetic Goal, roster and
worktrees, then starts one local DSH coordinator Turn. Each member uses existing
Todo, Turn and TS acceptance owners. Ark is an [optional execution provider](../../packages/loopx-ark-turn/README.md).
The example supplies domain inputs, validators and operator bindings; it does
not implement a separate scheduler or task database.

## Run

From a matching source checkout, install both optional providers into the same
interpreter. Node 24.21 or later qualifies the File/SQLite example.

```bash
uv sync --extra test --extra deepseek-harness
uv pip install --python .venv/bin/python -e packages/loopx-ark-turn
```

Set `ARK_API_KEY`, `ARK_MODEL_ID` and `ARK_ENVIRONMENT_ID` in the process
environment. DSH reuses the machine operator credential; `DEEPSEEK_API_KEY`
is an explicit environment override. The Ark Environment must already belong to the operator;
this launcher never creates or deletes it. The local model defaults to
`deepseek-v4-flash@high`; select another profile with `--dsh-model`.

Choose a **new private disposable directory**. Never point this example at an
active Goal or research workspace. The canonical Goal quota governs admission;
this version no longer has the old demo-only two-attempt counter. Each binding
has a finite deadline, and rejected attempts may still incur provider usage.
The local lead and nested cloud coordinator have up to 20 minutes each; other
members have five-minute host budgets, including cleanup. These are per-binding
limits, not a fleet-wide currency cap.

```bash
uv run --no-sync --extra test python examples/managed-research-team/research_team.py \
  run "$DEMO_ROOT" --model "$ARK_MODEL_ID" --environment-id "$ARK_ENVIRONMENT_ID"

uv run --no-sync --extra test python examples/managed-research-team/research_team.py \
  validate-report "$DEMO_ROOT"
```

The launcher succeeds only after the lead's validated Turn and canonical Todo
completion. Read `completion.json`, `lead/report.json`, `lead-turn.json`, the
private collaboration execution receipts and `provider-receipts/`. They are local
experiment records, not publishable fixtures. Canonical readback:

```bash
uv run --no-sync --extra test python -m loopx.cli \
  --registry "$DEMO_ROOT/registry.json" --runtime-root "$DEMO_ROOT/runtime" \
  --format json todo list --goal-id synthetic-managed-research

uv run --no-sync --extra test python -m loopx.cli \
  --registry "$DEMO_ROOT/registry.json" --runtime-root "$DEMO_ROOT/runtime" \
  --format json goal-acceptance verify --goal-id synthetic-managed-research --execute
```

## Configure a Luna / DSH / Ark team

Use `--team-size LUNA DSH ARK` to select positive counts for all three member
runtimes. Counts exclude the coordinator. For example, this single command
prepares the isolated Goal and starts a DSH coordinator with one member per
runtime:

```bash
uv run --no-sync --extra test python examples/managed-research-team/research_team.py \
  run "$DEMO_ROOT" --team-size 1 1 1 \
  --model "$ARK_MODEL_ID" --environment-id "$ARK_ENVIRONMENT_ID"
```

Codex members use independent `gpt-5.6-luna@max` Turns and the machine's existing
Codex login. Install `codex` on `PATH` first. DSH uses the machine operator
credential or an explicit process environment override; no credential is
copied into the Goal. Ark uses the selected model and existing Environment.
This does not advertise a different DSH model version than the one the provider
actually serves. A generated binding is configuration, not a successful login
or model-availability check. `run` makes real, billable provider calls.

Luna members analyze the initial filing. DSH members consume an accepted Luna
artifact and analyze the correction; Ark members check an accepted DSH
artifact. Predecessors are assigned round-robin within this sample graph. The
coordinator chooses execution order and questions through the existing
collaboration tools. The report must adopt **every** configured member,
including an extra Luna member without a downstream consumer. Missing canonical
completion or a changed artifact prevents report acceptance. Repeating the
same analysis with more models does not create independent source families.

`--team-size 2 1 1` creates four member tasks. The same option works with
`prepare` and `prepare-chat`, which make **no model calls**. It cannot be combined
with `--topology`; omitting it preserves the existing local-led four-member
example. Zero/negative counts are rejected before creating a directory. More
members increase spend and can exhaust the isolated Goal's quota or lead's
20-minute deadline; this option is not a capacity or cost guarantee.

Read `project/team.json`, `delegation-config.json` and canonical Todo state to
inspect the generated roster. The `validate-report` and acceptance commands
above recheck every configured dependency. The Chat route below consumes the
same generated configuration; it still requires explicit owner settings and
enabling, rather than silently starting from preparation.

This remains a **synthetic acceptance example**, not a financial-research
showcase or proof of autonomous source discovery. An interrupted run is not
successful: use the existing delegation inventory/read/wait/resume operations
for the original executions. Do not rerun `run` against the same directory or
start replacements while their status is unknown. Pausing a Chat coordinator
does not stop already running members; retain their receipts and use the
individual provider's execution controls. Removing bindings prevents new
admission but does not cancel accepted work.

中文：`--team-size 1 1 1` 表示一名 Luna max、一名 DSH、一名 Ark 成员，协调员
另计。`run` 准备隔离团队并发起真实模型执行；`prepare`/`prepare-chat` 只准备。
可改为 `2 1 1` 等正整数；每名成员都必须通过独立验收并被最终报告采用，不能用
人数或注册成功代替协作证据。凭证复用机器配置，Goal 只持有分工和授权。此处是
明确标注的合成财报验收场景，真实投研、可视化一键启动、团队级停止和宣传影片
仍需分别验证。暂停协调员不会取消成员；运行中断时先恢复原执行，勿重复拉起。

## Goal Chat coordinator

To use the local Goal conversation as the lead, prepare a new disposable team
without launching the DSH lead. Use the same provider setup above:

```bash
uv run --no-sync --extra test python examples/managed-research-team/research_team.py \
  prepare-chat "$DEMO_ROOT" --model "$ARK_MODEL_ID" --environment-id "$ARK_ENVIRONMENT_ID"

export LOOPX_RESEARCH_DEMO_ROOT="$DEMO_ROOT"
uv run --no-sync --extra test python -m loopx.cli \
  --registry "$DEMO_ROOT/registry.json" --runtime-root "$DEMO_ROOT/runtime" \
  chat --port 5310
```

Open `http://127.0.0.1:5310/chat/`, select **Synthetic managed research → Chat**,
then **Enable LoopX → Settings**. Select `lead`, the prepared
`.loopx/config/delegations.json`, and an explicit coordinator total token
allowance. Save and enable. The Codex lead decides delegation order; no script
advances business phases. Pause while a member works, refresh the page, then
continue to observe that original member's accepted result. Queue a correction
for the next native turn or explicitly select inbox/steer.

This route returns the report in the conversation; all configured member tasks have
independent canonical acceptance. It does **not** write `lead/report.json` or
complete `todo_lead-report`. The `validate-report` command above applies to the
DSH/Ark lead route, which has an explicitly bound report-writing tool. Both
routes keep the overall Goal active. Read the member Todos with the canonical
command above; do not infer report acceptance from a native completion label.

中文：用 `prepare-chat` 准备隔离团队，再启动上述本地 Chat。进入该 Goal 的
对话，原地选择 `lead`、已生成的执行配置和协调员额度，开启后让模型组织协作。
可在成员执行时暂停、刷新、恢复，检查成员结果仍回到原对话。此入口把综合报告
返回对话；各个成员任务分别验收，报告 Todo 和整体 Goal 保留给所有者处理。
详见 [Goal 对话运行模式](../../docs/reference/goal-chat-continuation.md)。

## Collaboration path

### Keep an existing Codex or other local lead

Use `prepare` instead of `run` to provision only the disposable fixture and
operator bindings. It makes no model call and does not start another lead
session. Keep the provider setup above, including the existing Environment:

```bash
uv run --no-sync --extra test python examples/managed-research-team/research_team.py \
  prepare "$DEMO_ROOT" --model "$ARK_MODEL_ID" --environment-id "$ARK_ENVIRONMENT_ID"
export LOOPX_RESEARCH_DEMO_ROOT="$DEMO_ROOT"

uv run --no-sync --extra test loopx --registry "$DEMO_ROOT/registry.json" \
  --runtime-root "$DEMO_ROOT/runtime" --format json delegation list \
  --goal-id synthetic-managed-research --agent-id lead \
  --execution-config "$DEMO_ROOT/delegation-config.json"
```

The existing Agent then uses [delegation start/read/wait/resume](../../docs/reference/local-delegation.md#use-an-existing-agent-conversation-through-its-shell)
for the listed bindings, writing its own briefs. It reads the synthetic
`input.json` files and returned artifacts, chooses the work order and continues
its own analysis while members run. The nested cloud analyst still requests
its local reviewer through the same service. No business phase argument is
introduced.

After reading all configured canonical completions and exact artifact hashes, the
lead writes `lead/report.json` with the fields described by `scenario.py` and
the acceptance table below. Run `validate-report`, then complete the report
through ordinary `todo complete --todo-id todo_lead-report --agent-id lead
--no-follow-up` against this disposable registry/runtime. That command reruns
the bound validator. Retain the original conversation; preparation does not
attach, resume, migrate or impersonate any existing production Agent.

### Member relationships

The primary `local-led` profile has four independently accepted member tasks:

- The local lead delegates initial-filing analysis to local DSH `local-analyst`.
- Ark `cloud-reviewer` independently verifies that completed artifact and adopts
  its exact hash. Starting early cannot bypass the prerequisite's acceptance.
- Ark `cloud-analyst` receives the corrected-filing task and itself delegates
  independent review to local DSH `local-reviewer`, preserving the parent
  request. It waits for acceptance and adopts the returned artifact.
- The local lead reads all four accepted artifacts, resolves the revision and
  source distinctions, and writes the combined report with exact dependencies.

The two branches may run concurrently. The model chooses when to start, what to
ask, whether to repair rejected work and how to synthesize. The host provides
bounded `list_execution_bindings`, `start_delegation`, `wait_delegation`,
`read_delegation` and `resume_delegation` operations. Members independently
`assess_request`; a read, message or tool ACK cannot complete a task.

The optional `--topology cloud-led` profile retains Ark-to-DSH coordination as
an additional route. It does not substitute for the primary local-led path.

## Independent acceptance

| Evidence | Initial | Corrected | Required conclusion |
| --- | --- | --- | --- |
| Cash from operations | 120 | 105 | Consume the correction |
| Capital expenditure | 30 | 30 | Raw FCF is 90 → 75 |
| Receivables sold | 50 | 50 | Normalized FCF is 40 → 25; delta −15 |
| Fiscal period comparison | H1 vs FY | H1 vs FY | Growth is unsupported |
| Repost of issuer material | Same source | Old figures retained | One current-period source family; corrected repost is stale |

`bootstrap.ts` creates only a fresh disposable canonical runtime and invokes
the production owner configuration API once. It binds each configured member criterion and
one report criterion. Task instructions, the roster and verifier files are
pinned; a member cannot change its own acceptance. Existing Goals are never
promoted or rewritten by this bootstrap.

Core delegation asks the TS acceptance owner for the exact task's criteria and
runs those checks as its Turn validator. It then uses ordinary `todo complete`,
which re-executes validation and atomically commits through the same TS owner.
The report separately checks all configured canonical completions, current binding
guards, adopted hashes and financial conclusions. All configured Todos may be done
while the overall Goal remains active for its owner.

A member's own peer conclusion is preserved. The delegation result independently
reports canonical acceptance; it does not replace that message or trust a
model-authored `accepted` flag. Saved artifacts and old receipts cannot hide
changed inputs, stale work or modified output.

## Recovery and validation

A delegated worker runs independently of the requesting MCP conversation. A
new connection reads its original operation id. Repeating that operation or
resuming a live worker cannot start a concurrent duplicate. After process loss,
recovery uses the original Turn journal. Ark observes its original cloud session
and input; acknowledged tool effects are not repeated. While the host is absent,
cloud computation can continue until it needs a local tool, then waits.
Unknown creation/input acknowledgements or interrupted tool side effects require
explicit reconciliation. Reconnecting does not reset the original deadline.

Durable tests use the production CLI, File/SQLite authority, TS acceptance and
real stdio MCP, with explicit model substitutes where appropriate. They cover
missing adoption, conflicting operation ids, ungranted actors, concurrent
resume, stale/changed artifacts, prerequisite completion and preserved peer
conclusions. Provider tests restore actual execution checkpoints and verify no
new input or acknowledged tool effect. TS acceptance also runs on isolated real
PostgreSQL; no model calls occur in CI.

```bash
uv run --no-sync --extra test python -m pytest -q \
  packages/loopx-ark-turn/tests tests/test_collaboration_mcp.py
```

Real execution qualification uses the public Ark SDK and DSH with synthetic
materials. A process-loss drill kills the owned worker/Turn/provider process
group after the input ACK, observes cloud `requires_action`, then resumes the
same operation to canonical completion. It confirms one provider receipt, the
same Session and input, and cleanup of owned resources. This evidence is
separate from mocked provider tests and does not establish market-research
quality, arbitrary team scale or attached persistent Codex-task integration.

## Boundaries and cleanup

The operator owns Agent registration, bindings, workspaces, executables,
credentials and validators. The model cannot grant new execution authority.
Leaf DSH tools receive no provider credentials. The local lead forwards the
credentials needed for its authorized execution bindings by environment
reference; Ark's local tool process receives the DSH credential only when it
must launch that local member. Ark credentials never enter cloud tool inputs or
results. MCP servers need trusted local OS isolation.

On normal completion Ark deletes its owned Session and Agent and confirms
absence. Retain private receipts after interruption and use the adapter's
cleanup command for known resources. The Environment remains operator-owned.
Disable admission by removing grants or the explicit execution configuration;
stop/reconcile existing workers before deleting the disposable runtime.

This slice provides fixed authorized work, dependent artifacts, nested requests
and local recovery. General Agent creation, dynamically derived work, full
inbox/queue/steer, remote authority and Dashboard/Lark configuration remain with
the existing RFC owners. No default executor, product navigation or recurring
monitor changes here.

## 中文操作与能力说明

主路径由本地 DSH 协调员带领两个本地 DSH 和两个云端 Ark 成员。初始披露走“本地
分析 → 云端独立核验”；修订披露由云端分析员继续委派本地核验员，采用其结果后
返回。最后由本地主 Agent 综合四份带哈希的已验收产物。一次启动之后由模型决定
问题、并发顺序、修正与汇总，不输入 `phase`，也没有示例专用业务调度器。

成员必须先自行记录采用请求，再经过 Turn 验证、普通 Todo 完成入口的重新验证和
TS 提交。主 Agent 断开后，已启动的委派仍可继续；整组本地进程中断后，使用原操作
ID 接回原 Turn 和云端 Session。云端需要本地工具时会等待，不能据此宣称完全离线
自主运行。副作用是否已发生不明时保留记录并核对，绝不自动重复执行。

依次运行上面的安装、`run`、`validate-report` 和 canonical readback 命令。所有数据
是合成投研材料，归一化自由现金流应为 40 → 25、变化 −15，不支持跨期间增长判断。
总体 Goal 保持 active。真实执行记录留在私有实验目录；公开仓库保留可复用接口、
合成案例和验证方法。
