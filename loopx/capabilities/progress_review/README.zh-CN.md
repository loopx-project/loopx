# 进展评估哨兵

[English](README.md)

进展评估哨兵让一个 Goal 消费**类型化的漂移回执**。回执由一个可选的、外部的、有界评估器在每次捕获到的工作转换后写入。能力默认关闭。`shadow` 模式下核心只记录和展示回执；`assist` 模式下，连续若干条已完成的漂移回执会变成**已有的** `autonomous_replan_obligation`，除此之外不改变任何行为。

它要补的是现有类型化重复保险丝按构造看不见的一种情形：Agent 每轮自报 `advanced`、每轮更换 `hypothesis_id`、测试始终全绿，但限定文件的实际变化只是改名和调整字段顺序。今天这类工作只能在 20 条 durable run 之后由周期复审兜底发现。

## 核心做什么、不做什么

| 核心会 | 核心不会 |
| --- | --- |
| 只通过一个严格 schema `progress_review_receipt_v0` 读取回执 | 调用模型、读取原始 diff、导入观察器包 |
| 按 `turn_instance_id` 关联 run 行，缺失时退回 `(generated_at, agent_id, todo_id)` | 覆盖或补充 Agent 自己的 `progress_observation` |
| 只计入状态为 `completed` 且所选漂移信号为 `True` 的回执 | 把 `unknown`、`abstained`、`failed`、`stale` 或缺失的回执算作漂移 |
| 在已确认的自主重规划处停止计数并重新武装 | 暂停 Turn、打开 user gate、判定 Goal 验收 |
| 把窗口内最新的类型化 `progress_observation` 绑定为义务的 `progress_baseline`，并把窗口内每条不同的声明作为 `progress_window` 一并携带；共享的出口策略拒绝回放其中任何一条、也拒绝只改标识但沿用其 evidence id 的 ack | 让观察器或其模型来 ack，或把改名、回放当成转向 |
| 已形成的义务在更新的转换处于未评估、失败、弃权、过期或无法归属时保持打开，并报告数量 | 把缺少评估当成漂移已被处理的证据 |
| 像现有重规划策略一样跳过 neutral 记账行（配额消费/作废） | 把记账行当成缺口或进展 |
| 只计入绑定到 Goal 负责人所 pin 修订的回执，并在更新的回执绑定到别处时报告 | 自动跟随观察器 basis；pin 是手动的 |

类型化重复保险丝保持优先。只有它沉默时，回执连续段才会补充证据。

## 策略

```bash
loopx configure-goal --goal-id <goal-id> --progress-review-mode shadow --execute
loopx configure-goal --goal-id <goal-id> --progress-review-mode assist \
  --progress-review-signal noul --progress-review-drift-threshold 2 --execute
loopx configure-goal --goal-id <goal-id> --clear-progress-review-configuration --execute
```

| 字段 | 取值 | 含义 |
| --- | --- | --- |
| `mode` | `off`、`shadow`、`assist` | `off` 不加载任何内容；`shadow` 记录并展示；`assist` 可以触发义务 |
| `signal` | `noul`、`choice` | 哪一组判断算作漂移 |
| `drift_threshold` | 2–20 | 触发义务前需要的连续已完成漂移回执数 |
| `contract_revision` | sha256 或空 | 回执必须绑定的观察器 basis 修订，由 `loopx-jev drift init` 打印；`assist` 必需。pin 是手动的：绑定到其他修订的回执永不计数，basis 变化本身不会让早先回执退休，最新回执绑定到别处时 status 报告 `rebind_hint: newer_receipts_under_unpinned_revision` |

策略保存在 Goal 注册表的 `control_plane.progress_review`，可在 `loopx configure-goal --goal-id <goal-id>` 输出的 `feature_summary` 和 Dashboard 能力编辑器中看到。格式错误的配置块会安全地退回 `off`。

## 回执

回执由可选的 `loopx-jev-pilot` 发行版中的观察器写入 `<runtime-root>/goals/<goal-id>/progress-review/receipts/<event-id>.json`（见 [`packages/loopx-jev/DRIFT_SHADOW.zh-CN.md`](../../../packages/loopx-jev/DRIFT_SHADOW.zh-CN.md)）。每条回执只包含类型化字段：

- 身份：`goal_id`、`event_id`、`evidence_id`、`contract_revision`、`sequence`，以及 run 的 `turn_instance_id`、`generated_at`、`agent_id`、`todo_id`；
- `status`：`completed`、`abstained`、`failed`、`not_evaluated`、`stale`；
- `judgments.choice`：`relation` 与 `increment` 标签或 null；
- `judgments.noul`：`behavior_change`、`serves_acceptance`、`evidence_increment` 的概率或 null；
- `drift_signal.noul`、`drift_signal.choice`：`true`、`false` 或 null；
- `timing_ns`、`usage`、`label_probability_threshold`、`recorded_at`。

`sequence` 是写回执的观察器的本地计数，`drift init` 创建新的观察器状态时会从 0 重新计数。核心从不跨观察器比较 sequence：加载、加载上限、最新修订判断和同一转换两条回执的合并，都按 run 的 `generated_at`、再 `recorded_at`、再 `sequence` 排序（`progress_review_receipt_order_key`）。

漂移信号遵循规则 `progress_review_signal_rule_v1`，按标签阈值 `t` 推导；核心读取回执时会从类型化判断重新计算，并拒绝布尔值不一致的回执：

- `noul`：`P(serves_acceptance) ≤ 1−t` **且** `P(evidence_increment) ≤ 1−t` 为漂移；任一概率 `≥ t` 为非漂移；其余为 null。`behavior_change` 只记录、不参与判定。
- `choice`：`relation = off_goal` **且** `increment = no_new_evidence` 为漂移；`on_goal`、`necessary_prerequisite` 或 `new_evidence` 为非漂移；其余为 null。

两道问题都针对检查点之间的变化而不是 after 状态整体：对已经满足验收的文件做改动是漂移，而服务验收条件或新增其证据的文档、负结果、前置测试不是。

按 `turn_instance_id` 找到的回执必须具有相同且非空的 Agent，Todo 必须完全一致（未绑定工作允许双方都缺省）。缺失身份不是通配符。同一 Turn 在回执或保留的重试历史中具有冲突的 Agent/Todo 时，不予归属；时钟不能解决身份冲突，同身份的后续评估仍按时间排序。只有真正缺少 Turn 时才允许 `(generated_at, agent_id, todo_id)` 回退，且 run 与证据两侧都必须唯一。无效或相互冲突的 direct/settlement Turn 不能降级匹配。ACK 仅作用于具名 Agent 泳道；匿名行不能结清其义务或提供进展 baseline。同一泳道有效 ACK 在配额记账行中仍然有效。

这收紧了原 stage-0 `assist` 的兼容行为：身份不完整的历史回执仍可在 shadow/status 查看，但不能形成或解除义务。off 行为、模型请求、手动 revision pin 与类型化 replan 出口保持原契约。

每个被捕获的转换只有三种：**漂移**（completed、所选信号为 `true`、pin 修订）、**on-goal**（completed、信号为 `false`）或**未评估**，后者带一个类型化原因（`pending`、`failed`、`abstained`、`stale`、`undecided`、`missing`、`unattributed`、`identity_conflict`、`other_revision`、`not_evaluated`）。形成是保守的：义务需要 `drift_threshold` 个连续漂移转换，中间不能有未评估转换。存续则不然：一旦形成，更新的未评估转换既不延长也不解除义务，其数量作为 `unevaluated_transitions` 记在 trigger 上。只有已确认的重规划或更新的 completed on-goal 判定能结束它；Goal 负责人也可以把模式调回 `shadow` 或 `off`。绑定到非 pin 修订的回执是未评估的历史，永不计数。扫描范围是 Goal 保留的 run 历史（`latest_runs`），义务最多只能与该窗口同样老。

## 解除

`assist` 义务只能按所有自主重规划义务的方式解除：Agent 的下一次 `refresh-state` 必须携带共享出口策略（`work_item.replan_semantics`）对这个来源接受的类型化证据。义务把窗口内最新的类型化进展观察（无论是否已评估）绑定为 `progress_baseline`，并把窗口内每条不同的声明作为 `progress_window` 一并携带，因此任何已经在记录里的声明都不能用来 ack。相对整个窗口，策略接受：

- **至少引用一个基线和窗口内所有声明都没有的 evidence id** 的新 surface、hypothesis 或 probe family；只改标识、沿用这些 evidence id 的写回被拒绝（`progress_identity_without_new_evidence`），回放窗口内已经做过的声明被拒绝（`progress_observation_replayed`）；凭真正的新证据回到更早的 hypothesis 是类型化转向，会被接受；
- 新的具体 blocker，或有覆盖证据的终态；
- 一条新的证据关联 vision path（`continue`、`no_change` 或 `replan`），带验收摘要与 evidence refs——复核被评估的工作后决定保留原计划的 Agent，由此获得一个类型化出口。

原样重提被绑定的观察，或同一 hypothesis 换新的 evidence id，都会被拒绝。trigger 只在窗口内有可绑定的类型化观察时触发；不写类型化观察的 Agent 只会得到回执与状态，不会得到义务。心跳的 requirements 投影同时给出两个出口：进展路径的 `cli_semantic_args` 与 vision 路径的 `alternative_cli_semantic_args`。

## 你会看到什么

只要策略不是 `off`，`loopx status --format json` 会在 Goal 条目及其 `project_asset` 中增加 `external_progress_review`：按状态统计的回执数、按信号统计的漂移数，以及最新回执的类型化判断。`assist` 模式下，满足条件的连续段表现为一个 `autonomous_replan_obligation`，其 trigger 的 `kind` 为 `external_progress_review_drift`，`frontier_identity` 为 `progress_review:<evidence-id>`，附带一条 P1 todo 动作以及一贯的 `required: true`、`stop_condition` 和 ack 契约。心跳提示词已经要求 Agent 遵守该义务并用类型化重规划确认。

## 验证差异

`packages/loopx-jev` 附带对照命令：

```bash
loopx-jev sentinel compare \
  --matrix packages/loopx-jev/tests/fixtures/sentinel/matrix.json \
  --responses packages/loopx-jev/tests/fixtures/sentinel/responses \
  --output /tmp/sentinel-comparison.json
```

它对每个录制序列报告：类型化重复保险丝首次触发的轮次（对自报 advanced 的序列在序列内永不触发）、每种回执信号首次标记漂移的轮次，以及 gold 标注为 on-goal 的序列上的误报。不加 `--live` 时回放已提交的 provider 响应，因此 CI 无需 key 即可复现数字。`python3 examples/progress-review-sentinel-smoke.py` 运行同一回放。

## 录制对照结果

16 序列矩阵的已提交 live 录制（问题集 `scoped-progress-sentinel-v2`，`jev-1.13.0`，35 轮，每轮自报 `advanced`；1 个回答被严格解码器拒绝）：

| | 类型化重复保险丝 | `noul` 回执 | `choice` 回执 |
| --- | --- | --- | --- |
| 在 gold 轮被标记的漂移序列 | 0/9 | 9/9 | 5/9 |
| 阈值 2 下达到 `assist` 义务 | 0/9 | 9/9 | 2/9 |
| 真实 on-goal 上游提交被误报 | 0/7 | 已评估的 0/6 | 已评估的 0/6 |
| 真实 on-goal 提交无判定（按失败关闭） | — | 1/7 | 1/7 |

纯装饰性序列在第 1 轮被标记，混合序列在各自漂移轮被标记；第二次独立 live 复现了全部结果。v2 措辞是在早先录制漏检“实现落地后的改动”之后修订的，因此构造序列对新措辞不再算留出集；7 个真实提交没有用于调参。完整表格、延迟、波动与尚未证明的部分见[操作指南](../../../packages/loopx-jev/DRIFT_SHADOW.zh-CN.md)。

## 采用路径

观察器位于可选发行包 `loopx-jev-pilot`，策略位于 Goal 注册表。建议一次只接入一个 Goal：

1. **安装观察器**：在 LoopX 检出旁执行 `uv pip install -e '.[test]' -e packages/loopx-jev`，把 `TYPESAFE_API_KEY` 放进环境变量；仓库和注册表都不保存它。
2. **绑定一个 Goal**：写一份 basis JSON，包含目标、验收条件和可选证据文件；列出预计会被 Agent 修改的文件；运行
   `loopx-jev drift init --state-dir <dir> --config <config> --workspace <repo> --basis <basis> --runtime-root <runtime> --path <file> ...`，
   记下打印出的 `contract_revision`。
3. **包装真实刷新**：在 Agent 或其宿主运行 `loopx refresh-state` 的位置，改为
   `loopx-jev drift refresh --state-dir <dir> --config <config> -- <原来的 loopx 参数>`。
   原命令、stdout 与退出码保持不变。
4. **单独运行消费者**，例如 `loopx-jev drift drain --state-dir <dir> --config <config> --watch-seconds 600`；它为每个已评估事件在 Goal 运行时下写一条回执。
5. **开启 `shadow`**：`loopx configure-goal --goal-id <goal-id> --progress-review-mode shadow --execute`。此后 `loopx status --format json` 会显示该 Goal 的 `external_progress_review`。
6. **标注你看到的结果**：`loopx-jev drift status --state-dir <dir>` 列出回执；`loopx-jev drift label --state-dir <dir> --event-id <id> --truth drift|on_goal|unknown` 记录你的判断，status 按信号显示一致性。
7. **然后才考虑 `assist`**：用 `--progress-review-contract-revision <sha256>` pin 第 2 步的修订，并设置 `--progress-review-mode assist`。连续的漂移回执会触发 Agent 必须确认的已有重规划义务。pin 不跟随 basis：重新运行 `drift init` 后，status 会显示 `rebind_hint`，直到你 pin 新修订。`--clear-progress-review-configuration` 回到 `off`。

## 成熟度阶梯

| 阶段 | 允许什么 | 进入证据 | 决定者 |
| --- | --- | --- | --- |
| **0 · 默认关闭的预览（本 PR）** | 注册 capability，提供 `shadow` 与需 pin 的 `assist`，默认全部关闭 | 精确 head 上 CI 全绿；评审发现已用确定性方式修复；对照结果可从已提交录制复现；双语文档；不新增权限 | 维护者合并；控制面改动从不自合并 |
| **1 · 真实 Goal 上 shadow** | 两个以上长期 Goal 开 `shadow` | 每个 Goal 至少两周或四十条回执，并用 `drift label` 标注；Goal 负责人报告误报率与相对周期复审的提前量；至少一次真实漂移在第 20 轮之前被标注出来 | Goal 负责人 |
| **2 · 单个 pin 过的 Goal 上 assist** | 一个 Goal 开 pin 过的 `assist` | 阶段 1 的负责人认为误报代价可接受；读取 Agent 的 ack：是否换了切片、几轮后确认、有多少义务是错的 | Goal 负责人加维护者 |
| **3 · 更广默认、升级、暂停** | 本 capability 不提供 | 另行授权的干预实验证明相对原流程减少了无效工作 | 项目决定 |

本 PR 请求的是阶段 0。阶段 1、2 是操作者用这个工具做出的选择；阶段 3 不在本次范围。停在任何阶段或保留原流程都是有效结果。

## 边界

升级（义务被忽略后打开 user gate）与暂停仍是未来工作，本能力不授予。观察器的预测质量与本集成是两个独立问题：已提交的对照展示的是一套问题在一个冻结矩阵上的表现，不是它在你的 Goal 上的表现。
