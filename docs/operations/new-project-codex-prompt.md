# New Project Codex Prompt

Use this prompt when you already have:

- a local project folder;
- a project goal document;
- a Codex session with access to that folder.

Replace the placeholders before sending it to Codex.

## CLI Generator

Generate the same handoff prompt locally:

```bash
loopx new-project-prompt \
  --project <PROJECT_ROOT> \
  --goal-doc <GOAL_DOC_PATH>
```

If the project needs a controller that can split scoped sub-agent probes:

```bash
loopx new-project-prompt \
  --project <PROJECT_ROOT> \
  --goal-doc <GOAL_DOC_PATH> \
  --spawn-allowed \
  --allowed-domain docs-map \
  --allowed-domain validation-map \
  --write-scope "docs/**"
```

## Copy-Paste Prompt

````text
我有一个新项目要接入 LoopX。

项目文件夹：
<PROJECT_ROOT>

项目目标文档：
<GOAL_DOC_PATH>

请你按下面步骤推进，不要停在方案讨论：

重要：`loopx connect` 只登记 goal 和 active state，不会生成首连 onboarding todo。
接入后先只读核对状态与目标文档，把第一个交付 todo 的候选写给我确认，再开始 delivery。

0. 先确认当前 shell 能调用 LoopX CLI；如果提示 `loopx`
   不在 PATH，运行本机安装脚本再继续：

   ```bash
   export PATH="$HOME/.local/bin:$PATH"
   install_script="$HOME/loopx/scripts/install-local.sh"
   if ! command -v loopx >/dev/null 2>&1; then
     if [ -x "$install_script" ]; then
       "$install_script"
       export PATH="$HOME/.local/bin:$PATH"
     else
       echo "loopx is not on PATH; clone the LoopX repo and run scripts/install-local.sh" >&2
       exit 1
     fi
   fi
   loopx doctor >/dev/null
   ```

1. 再只读检查项目文件夹和目标文档，抽取：
   - stable goal id；
   - 一句话 objective；
   - domain；
   - authority sources；
   - work clusters；
   - validation surfaces；
   - private/public boundary；
   - 第一个 recommended_action。
2. 运行 LoopX 接入命令。优先使用：

   cd <PROJECT_ROOT>
   loopx connect \
     --goal-id <STABLE_GOAL_ID> \
     --objective "<OBJECTIVE_FROM_GOAL_DOC>" \
     --domain <DOMAIN> \
     --goal-doc <GOAL_DOC_PATH> \
     --adapter-kind read_only_project_map_v0 \
     --adapter-status connected-read-only

   如果已有安全的只读 pre-tick 命令，再追加：

   --next-probe "<READ_ONLY_PRE_TICK_COMMAND>"

   如果项目需要主控拆 sub-agent，再加：

   --spawn-allowed \
   --allowed-domain docs-map \
   --allowed-domain validation-map \
   --write-scope "<SAFE_WRITE_SCOPE>"

   如果接入后才发现 write_scope / boundary 少了，不要为了补 scope 直接
   `bootstrap --force`。先用增量配置：

   loopx configure-goal \
     --goal-id <STABLE_GOAL_ID> \
     --write-scope "<SAFE_WRITE_SCOPE>" \
     --execute

   只有在用户明确要求重建连接时才用 `bootstrap --force`；如果要保留当前
   active state/todo，必须同时加 `--preserve-todos`。

3. 确认 `.loopx/registry.json` 和
   `.loopx/goals/<STABLE_GOAL_ID>/ACTIVE_GOAL_STATE.md` 已创建或更新。
   接入输出里不再有 onboarding 扫描、候选 todo 或自主推进选择项；首连之后状态里
   没有可执行的 agent todo。请只读核对目标文档和 registry 的 `execution_profile`，
   用中文给出 1-3 个第一个交付 todo 的候选，问我确认后，用
   `loopx todo add ...` 写入被接受的条目，再运行
   `loopx refresh-state --goal-id <STABLE_GOAL_ID>` 并汇报。在用户确认前不要开始
   delivery。
  如果目标状态包含私有证据，把 `.loopx/` 和 `.loopx/goals/`
  加入该项目 `.gitignore`。
   `loopx connect` 默认会同步到共享全局 registry；不要手动编辑其他
   项目的 registry。
   接入后检查 registry 里的 `execution_profile`：它是本项目后续 heartbeat /
   adapter 的执行画像。默认 cadence 是 `bounded_progress_segment`，连续小步达到
   阈值后，下一轮必须扩展到 `minimum_scale` 并包含 `must_include` 里的真实
   artifact、targeted validation、state writeback；如果做不到，先报 blocker，
   不 append quota spend。
4. 在任何 heartbeat、scheduled tick、long-running adapter 或自主 delivery 前，
   先问 compute guard：

   ```bash
   loopx --format json --registry "$HOME/.loopx/registry.global.json" quota should-run --goal-id <STABLE_GOAL_ID> --runtime-profile outer_controller
   ```

   Codex App 使用紧凑别名 `--codex-app`；其他常见宿主只传一个
   `--runtime-profile`（`codex_cli`、`claude_code`、`generic_cli` 或
   `outer_controller`）。只有自定义执行上下文才同时传 `--host-surface`、
   `--scheduler-owner` 和 `--execution-mode`。

   如果返回 `state=operator_gate`，把它当成人/控制器交互，而不是安静 skip：优先读取
   payload 里的 `gate_prompt`、`operator_question`、`recommended_action`、
   `next_handoff_condition`、`missing_gates`、`user_todo_summary` 和
   `agent_todo_summary`，用中文主动告诉用户当前卡在哪个 gate、期望怎样回复；同时把
   `agent_todo_summary` 当作项目 agent 自己的安全后续清单。不要执行任何 `agent_command`、adapter work、
   write-control、生产动作或该 gated action。如果同一个未决 gate 最近已经问过，且返回
   `safe_bypass_allowed=true`，该 gate 只阻塞被 gate 覆盖的 delivery path；可以从
   active state / Priority Stack 里选择一个不依赖该 gate 的 bounded 只读分析、steering、
   文档或 P0/P1 工作。若实际完成 safe-bypass 工作，仍需验证、写回进展，并 append 一次
   quota spend。如果 payload 返回 `notify_user_on_open_todo=true`，把开放 user todo 当作
   blocker-push，而不是静默 skip：用中文最多列 3 个开放项和期望回复格式，并且本轮不做
   delivery、不 append quota spend，除非同一个 blocker 最近已经问过。
   无论 `should_run` 是 true 还是 false，都先看 `execution_obligation`、
   `effective_action`、`recovery_delivery_allowed`、`safe_bypass_kind` 和
   `heartbeat_recommendation`。`heartbeat_recommendation.notify` 只是用户通知策略，
   不是执行 gate；如果 `execution_obligation.must_attempt_work=true`，即使
   `notify=DONT_NOTIFY` 也要尝试一个 bounded segment，只有
   `must_attempt_work=false` 才能 quiet no-op。
   如果是 `outcome_floor_recovery`，这是 Codex 可执行 recovery turn：只允许做一次所需
   ranker/cross-domain evidence recovery，或写回阻止该 evidence 的具体 blocker；
   不做 surface-only / synthetic-only 循环，验证并写回后才能 append 一次 quota spend。
   如果不是 operator gate / blocker-push / outcome-floor recovery / 明确 safe-bypass，
   本轮不要做实现或 adapter 工作，只记录 public-safe reason；不要执行任何
   `agent_command`，即使 status 或 review packet 里提到过命令。只有当返回
   `should_run=true` 且 payload 里包含 `agent_command` 时，才执行该命令。如果
   `should_run=true` 但没有 `agent_command`，按 `execution_obligation` /
   `recommended_action` / `goal_boundary` 选择下一个安全 bounded 动作；只读目标保持只读，
   delivery 目标按已授权 write scope 执行。
   如果命令非零，fail closed，先修
   `loopx doctor` / `loopx status`。这个 guard 不等于写权限、
   不绕过 operator gate、也不替代 human reward。
   任何时候，如果你通过 read-only 分析、review doc、gate checklist 或 P0/P1 steering
   发现新的用户/owner 待办，不要只写在 `Next Action`、外部 review 文档或聊天里。
   立刻把它写进 active state 的 user todo 权威区：

   ```bash
   loopx todo add --goal-id <STABLE_GOAL_ID> --role user --task-class user_gate --blocks-agent <agent-id> --text "<public-safe blocking user/owner decision>"
   loopx todo add --goal-id <STABLE_GOAL_ID> --role user --task-class user_action --text "<public-safe non-blocking user/owner todo>"
   ```

   agent 自己的后续动作写成 `--role agent`。写入后如果 dashboard 需要看到最新状态，
   运行 `loopx refresh-state --goal-id <STABLE_GOAL_ID>`。
   完整契约见 LoopX 仓库里的 `docs/project-agent-todo-contract.md`。
5. 如果需要把当前 packet 或已批准命令交给项目 agent，优先生成最小 handoff，
   不要从旧聊天、旧 review packet 或 `run_history.latest_runs` 拼当前状态。当前权威状态来自
   `attention_queue.items` / `project_asset`；如果缺少 `project_asset` 或标记为
   `legacy/raw fallback`，不要把 raw queue 字段当作 owner/gate/stop authority：

   ```bash
   loopx review-packet --goal-id <STABLE_GOAL_ID> --handoff-only
   ```

   只把输出的 handoff 交给目标项目 agent；完整 review packet 留给 operator view /
   evidence drill-down。
6. 如果要给这个项目设置 recurring Codex App heartbeat，默认每 3 分钟一次；不要手抄
   guard 和 spend 协议；先生成 task body，再把输出复制进 automation：

   ```bash
   loopx heartbeat-prompt \
     --goal-id <STABLE_GOAL_ID> \
     --active-state .loopx/goals/<STABLE_GOAL_ID>/ACTIVE_GOAL_STATE.md
   ```

7. 生成一个 read-only project map 或 first pre-tick run。不要启动线上任务、
   不同步外部系统、不要写生产状态，除非目标文档明确授权。通用接入优先跑：

   ```bash
   loopx read-only-map --goal-id <STABLE_GOAL_ID>
   ```
8. 如果本轮只更新了 active state、ledger 或外部规划文档，没有产生新的
   adapter run，追加一个 state-only refresh run，然后停止且不记 quota spend。
   如果本轮实际消耗了 automatic delivery compute，先不要运行这个 state-only
   refresh；把它留到第 10 步记账之后：

   ```bash
   loopx refresh-state --goal-id <STABLE_GOAL_ID>
   ```

   这个命令也会自动同步全局 registry。

9. 跑验证：
   - `loopx registry`
   - `loopx status`（在没有项目局部 registry 的目录里也应自动读共享全局 registry）
   - `loopx check --scan-path <PUBLIC_SAFE_FILE_OR_DIR>`
10. 如果本轮实际花了 automatic delivery compute（例如 read-only map、adapter
   tick、实现推进或验证推进），在 validation / writeback 完成后，先记录本轮
   accountable delivery：

   ```bash
   loopx refresh-state --goal-id <STABLE_GOAL_ID> \
     --classification <PUBLIC_SAFE_PROGRESS_CLASSIFICATION> \
     --delivery-batch-scale <ACTUAL_DELIVERY_BATCH_SCALE> \
     --delivery-outcome <ACTUAL_DELIVERY_OUTCOME>
   ```

   三个 placeholder 都必须替换为本轮实际验证过的值；不要把较小或仅准备性的
   turn 默认、拔高成 `multi_surface` / `outcome_progress`。
   这是 `spend-slot` 将消费的因果记录；普通 state-only refresh 不能替代它。
   然后原样（不加管道或过滤）append 一次 quota spend；输出为空或不明确时用只读
   quota status 核对，不能重跑：

   ```bash
   loopx --format json --registry "$HOME/.loopx/registry.global.json" quota spend-slot --goal-id <STABLE_GOAL_ID> --slots 1 --source adapter --execute
   ```

   如果 dashboard 或 controller 在 spend 后仍需状态更新，再运行第 8 步不带
   delivery outcome 的 state-only refresh。不要在 spend 后追加另一个
   accountable progress refresh，否则它会成为新的未记账 delivery。

   不要为 quiet `should_run=false` skip、preflight 失败、或纯 dry-run preview 记账；
   如果 `should_run=false` 但实际完成了 `safe_bypass_allowed=true` 的 bounded
   safe-bypass 工作，要记一次账。不要重复执行。
11. 最后用中文汇报：
   - changed files；
   - validation output；
   - 当前 goal 在 dashboard/attention queue 里会怎么显示；
   - next safe action；
   - 如果还不能接入 decision-advisor，明确缺哪些 gates。
````

## Minimal Command

If the goal is simple and does not need a project-specific adapter yet:

```bash
cd <PROJECT_ROOT>
loopx connect \
  --goal-id <STABLE_GOAL_ID> \
  --objective "<OBJECTIVE_FROM_GOAL_DOC>" \
  --domain <DOMAIN> \
  --goal-doc <GOAL_DOC_PATH>
```

Then inspect:

```bash
loopx registry
loopx status
loopx check --scan-root .
```

## What Good Looks Like

The first connection is successful when:

- the project has a stable `ACTIVE_GOAL_STATE.md`;
- the registry points to that state file;
- the goal appears in `loopx status`;
- the attention queue says exactly who should act next;
- private evidence is kept in the project or local runtime, not in public docs;
- the next Codex tick can continue from saved state instead of re-reading the
  whole conversation.

For larger projects, the first useful adapter is usually read-only. It should
map documents, TODOs, validation surfaces, risks, and handoff packets before it
edits files.
