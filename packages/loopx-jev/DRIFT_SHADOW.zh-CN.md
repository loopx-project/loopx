# 从明确选定的文件变化中，旁路评估任务进展

[English](DRIFT_SHADOW.md)

本功能在真实 `refresh-state` 成功前后采集显式指定文件的变化，由**独立消费进程**调用 Jev，提供历史观察结果。观察器本身不会纠正、暂停、重新派发、确认完成或给 Agent 注入消息。当 Goal 启用核心的[进展评估哨兵](../../loopx/capabilities/progress_review/README.zh-CN.md)策略后，观察器写出的类型化回执会出现在 `loopx status` 中；`assist` 模式下，连续若干条已完成的漂移回执会触发**已有的** `autonomous_replan_obligation`，除此之外不改变任何行为。集成测试通过不代表已经提高任务成功率或节省时间。

## 实现归属和接入范围

命令位于可选包 `loopx-jev-pilot`，产品入口是任务进展旁路观察命令和对照 harness；没有排序代码或调度器。核心侧的策略、回执契约和 trigger 属于内置 capability `progress-review-sentinel`，它不导入本包任何代码。输入来源明确标为 `scoped_checkpoint_capture`，不自称 Decision Context provider。[决策记录](DESIGN_DECISIONS.zh-CN.md)关联研究历史和证据限制。

现有 L1 `reliability-diagnostics` 的禁止出站、禁止影响 Agent 的契约保持独立，不能拿它的收据证明模型推理合格。本实现不修改它或 `state_refresh.py`，模型请求不会进入核心事务或核心写锁。

这是显式 CLI 接入：在真实刷新调用位置使用 wrapper，并单独运行消费者。原 `loopx refresh-state` 和原生 Codex/Claude 会话保持原行为；没有自动 hook 安装或 Lark 开关。观察器自身的设置（模型、出站、限额、off/shadow）绑定本地一个 Goal 观察目录，每个 Goal 应使用独立配置文件。核心是否读取这些回执由另一层按 Goal 的注册表策略决定：`loopx configure-goal --progress-review-mode`，Dashboard 中也可编辑，默认关闭。操作者提供契约导出，这不自动证明规范 Goal 验收或工作区独占权。

## “限定文件”具体指什么

就是初始化时通过 `drift init --path` 明确指定的仓库相对文件。例如修复重试逻辑时，可以选择 `src/retry.py`、`tests/test_retry.py` 和 `reports/retry_probe.json`。这是观察器的材料清单，**不是限制工作 Agent 只能修改哪些文件**；程序不会自动发现相关文件，也不会扫描整个仓库。

| 材料 | 如何进入评估 |
| --- | --- |
| 目标和验收条件 | 操作者提供的 basis JSON |
| `--path` 指定文件 | 读取前后内容及净变化；允许指定尚未创建的文件 |
| basis 中可选的 `evidence` 引用 | 明确列出的普通文件，例如测试或实验报告 |
| 其他源码、依赖或对话历史 | 不自动读取；缺失会限制判断能力 |

路径必须是文件，不是目录或通配符；最多选 32 个，并受下文的字节上限约束。初始化后清单固定；要变更范围，需显式新建观察器/预算并建立新基线。Agent 在清单外做的工作可能完全有效，`no_delta` 只表示所观察材料没有变化，不表示整个任务没有进展。读取测试报告也不等于执行测试或独立认证其中的声明。

例如只选择函数所在文件，而漏掉被调用的 helper 和对应测试，评估就不能证明完整行为。应主动纳入相关测试、结果和依赖；必要材料装不下时，不能把部分材料包装成完整证据。

## 操作方法

使用 Python 3.11+ 和 LoopX 检出要求的 Node 运行时。采集器目前面向 Linux/macOS 的 POSIX 文件处理；Windows 采集未验证，缺少所需文件原语时记录观察不可用。此可选发行包不进入 LoopX 默认 wheel；从源码根目录在新环境中安装，再执行不需要 key 的集成测试：

```bash
uv venv .venv-jev
uv pip install --python .venv-jev/bin/python -e '.[test]' -e packages/loopx-jev
.venv-jev/bin/python -m pytest packages/loopx-jev/tests/test_drift.py packages/loopx-jev/tests/test_drift_cli.py -q
.venv-jev/bin/loopx-jev drift --help
```

本切片只观察任务进展，不提供其他评估方向或排序代码。使用 `loopx_jev_drift_config_v0` 和 `minimum_label_probability`；旧多方向试点配置会被拒绝，不会静默提升。配置 key 本身不启用 shadow 或允许出站。失败时原 Agent 工作继续，不会自动启动独立 Agent 裁判。

为已有 Goal 创建被 Git 忽略的本地目录，复制 [config.shadow.json](examples/drift/config.shadow.json) 和 [basis.json](examples/drift/basis.json)。将示例 Goal id、目标、验收条件改为本次契约。可选 `evidence` 引用交付工作区中的常规文件，例如独立产生的测试报告；不要在配置或契约中填写密钥。默认 `allow_egress: false`，确认指定材料允许出站后再设为 true，并在**消费者的环境变量**中配置 `TYPESAFE_API_KEY`。

以下变量代表这个 Goal 已有的本地路径。在待观察的工作**开始之前**建立基线，使用独立交付工作区。每个 `--path` 是一个精确的相对文件路径，允许文件尚未创建，不支持目录或通配符。除了源码，应纳入相关测试和研究产物；不要纳入观察目录、可变 LoopX 状态或凭据。

```bash
loopx-jev drift init --state-dir "$OBSERVER" --config "$CONFIG" \
  --workspace "$WORKSPACE" --basis "$BASIS" \
  --path src/retry.py --path tests/test_retry.py

# 在原刷新调用位置使用，保留原有参数和绑定。
loopx-jev drift refresh --state-dir "$OBSERVER" --config "$CONFIG" -- \
  --registry "$REGISTRY" --runtime-root "$RUNTIME" refresh-state \
  --goal-id "$GOAL_ID" --format json

# 在另一个终端/进程中消费：执行一遍，或在指定时间内轮询。
loopx-jev drift drain --state-dir "$OBSERVER" --config "$CONFIG"
loopx-jev drift drain --state-dir "$OBSERVER" --config "$CONFIG" --watch-seconds 300
loopx-jev drift status --state-dir "$OBSERVER"

loopx-jev drift configure --state-dir "$OBSERVER" --mode off
loopx-jev drift configure --state-dir "$OBSERVER" --mode shadow
```

受管 Turn 必需的 Agent/Todo/Turn/validation 参数仍须完整传入，wrapper 不豁免原规则。它保留原命令 stdout 和退出码，在 stderr 输出简短采集结果。不传配置即关闭，不读取观察材料或 key，不导入传输模块；dry-run 不采集。核心写入成功但采集失败时，原命令仍成功，观察器记录失败并使基线失效。无效或未知输出不能当成观察成功。

通过 `configure` 关闭/开启：每次变更推进单调版本号，即使配置字节恢复原样，处理中结果仍失效。重新开启后的第一次刷新只重建基线。直接编辑配置会改变内容哈希，但在两次检查之间关闭又恢复原文件无法被检测；撤销请使用命令。目标契约变更也会重建基线。

卸载时恢复原 `loopx refresh-state` 调用，停止消费者，并在所选环境卸载 `loopx-jev-pilot`。本地证据按操作者留存策略删除；删除请求墓碑并建立新目录相当于显式开始新实验和新预算，不是透明续跑。

## 给核心的回执、问题与标注

`drift init --runtime-root <runtime>` 把观察器绑定到 LoopX 运行时。每个入队事件会先写一条 **pending** 回执（`not_evaluated`、`pending_evaluation`），独立消费者评估完成后再用结果回执覆盖它，路径为 `<runtime>/goals/<goal-id>/progress-review/receipts/<event-id>.json`（`progress_review_receipt_v0`）。不带 `--runtime-root` 时，结果只留在私有状态目录。`drift init` 还会打印观察器的 `contract_revision`（basis 文件的 sha256）；Goal 策略必须 pin 这个值，`assist` 才能触发任何义务。

模型只收到操作者 basis 中的 `objective`、`acceptance`、`non_goals`、`horizon`、`evidence`、`already_known` 字段和限定材料。Goal 身份、样例名称与实验记账不会进入请求；有测试固定“只有 `goal_id` 不同的两份 basis 生成字节相同的请求”。

每次请求问两道 Choice（`relation`、`increment`）和三道 Noul。`behavior_change` 问 delta 是否改变可观察的运行行为；`serves_acceptance` 与 `evidence_increment` 针对**前后检查点之间的变化**而不是 after 状态整体：是否实现、验证或作为前置服务于一条 before 检查点尚未满足的验收条件，以及是否新增关于某条验收条件的可核对证据。漂移布尔值由规则 `progress_review_signal_rule_v1` 按标签阈值 `t` 推导；核心会从类型化判断重新计算并拒绝布尔值不一致的回执：

| 信号 | 判为漂移 | 判为非漂移 | 其余 |
| --- | --- | --- | --- |
| `noul` | `P(serves_acceptance) ≤ 1−t` 且 `P(evidence_increment) ≤ 1−t` | 任一概率 `≥ t` | null |
| `choice` | `relation = off_goal` 且 `increment = no_new_evidence` | `on_goal`、`necessary_prerequisite` 或 `new_evidence` | null |

`behavior_change` 只记录、不参与判定：一个无关的行为变化仍是漂移；服务验收或新增目标证据的文档、负结果不是漂移。落在 `(1−t, t)` 内的 Noul 概率视为未决；没有任何已决答案的评估记为 `abstained`。`abstained`、`failed`、`not_evaluated`、`stale` 事件的回执信号全为 null。

核心只在 Goal 的注册表策略允许时读取回执，`assist` 还要求 pin：

```bash
loopx configure-goal --goal-id <goal-id> --progress-review-mode shadow --execute
loopx configure-goal --goal-id <goal-id> --progress-review-mode assist \
  --progress-review-signal noul --progress-review-drift-threshold 2 \
  --progress-review-contract-revision <drift init 打印的 sha256> --execute
```

绑定到其他修订的回执是过期历史，永不计数。按 `turn_instance_id` 找到的回执必须具有相同非空 Agent 和完全一致的 Todo（未绑定工作允许双方都缺省）；缺失身份不是通配符。只有真正缺失 Turn 时才允许唯一的 `(generated_at, agent_id, todo_id)` 回退，无效或冲突 Turn 不能降级。回执或 run 重试中的身份冲突不予归属，匿名 ACK 不能解除其他 Agent 的义务。这些更严格的 assist 规则也适用于历史回执，详见[身份契约](../../loopx/capabilities/progress_review/README.zh-CN.md#回执)。回执为 pending、failed、abstained、stale、undecided、不匹配、缺失或绑定到其他修订的转换是未评估的：它永不算作漂移，会打断尚未形成的连续段，但不会延长或解除已形成的义务。只有已确认的重规划或更新的 completed on-goal 判定能结束义务。pin 是手动的：最新回执绑定到非 pin 修订时，`loopx status` 报告 `rebind_hint: newer_receipts_under_unpinned_revision`。回执的 `sequence` 是本观察器状态的本地计数，`drift init` 新建状态时从 0 重新计数；核心按 run 的 `generated_at`、再 `recorded_at` 排序，重新初始化的观察器不会被读成比它替换的状态更旧。`assist` 下义务携带形成期间的全部类型化声明；回放其中任何一条，或只改标识沿用其 evidence id，都不算 ack。

`assist` 会改变 Agent 的工作契约：它产生带 stop condition 与 ack 要求的 `required` 义务。义务会把被评估窗口的类型化进展观察绑定为基线，现有 writeback 据此拒绝原样重提该观察或仅在同一 hypothesis 下更换 evidence id 的 ack；只有存在这样的观察时才会触发。它不授予暂停、gate 或验收权限，但不是被动建议。观察器自身的 `off/shadow` 开关控制模型调用与出站；Goal 的 `off/shadow/assist` 策略控制核心如何使用已经存在的回执。关闭观察器不会撤回已写出的回执；清除 Goal 策略才会。

`drift label --state-dir <dir> --event-id <id> --truth drift|on_goal|unknown` 记录私有的人工真值；随后 `drift status` 按信号给出混淆表。标注不会离开私有目录，也不会进入回执。

`sentinel compare --matrix … --responses … --output …` 用已提交的 provider 录制回放 `tests/fixtures/sentinel/` 下的 16 序列矩阵，逐序列报告：类型化重复保险丝何时触发、每种信号首次标记漂移的轮次、`assist` 何时会触发义务，以及所有误报。`--live` 改为真实调用并录制；已提交的 `expected_summary.json` 固定了最后一次 live 的结果。

## 证据、去重和结果含义

- 比较前后检查点之间有效工作文件的净变化，包含期间已提交、已暂存、未暂存的文件变化，以及明确列出的未跟踪文件和可选证据文件；Git 只读。仅暂存区变化而工作文件相同，记为 `index_only_change_unknown`，不交给模型猜测。
- 模型输入同时包含前后检查点的限定文件内容，包括未改动文件，而不只有 delta；单看变动行通常无法理解新测试或实验。仍受整个请求字节上限约束，超限拒绝评估，不悄悄删去必要上下文。相同补丁作用于不同周边源码，使用不同的证据身份。
- 连续读取两次，并在刷新后再次核对；这不是文件系统原子快照或作者归属证明。应使用单写者 worktree。两次读取间改变后又恢复的内容、范围外工作仍不可见，不能靠 diff 证明整个任务的进展。
- 基线重建、无变化、重复事件和重复证据不调用模型。同一 Goal/Agent/Todo/Turn 的检查点补充去重；无 Turn 绑定时采用持久化 run 的摘要。同一契约下相同 delta 不算第二个独立观察；使用显式序号保留顺序，不依赖 JSON 键顺序。
- 排队材料是冻结的历史输入，之后工作区继续工作不使其失效；契约、配置版本、原记录更改或丢失会使其失效。历史结果没有对当前任务的控制权。
- 保留 `on_goal`、`necessary_prerequisite`、`off_goal`、`unknown`，以及独立的证据增量分类。必要测试、研究、负结果、文档都可能推进目标，不要求产生运行时行为变化；`no_new_evidence` 本身不是漂移结论。没有实现连续异常保险丝或自动升级复核。
- 无 key、禁止出站、过期输入、传输失败、弃权分别记录。请求不自动重试；即使详细结果被删除，请求预留仍保留，无法确定是否发出的请求不会再次发送。消费者崩溃后可复用已存的模型响应，不重复调用。

固定上限为 32 个文件、文件文本合计 32 KiB、可选证据文本 32 KiB、delta 32 KiB、16 个待消费观察、256 个事件身份。超限、二进制、符号链接输入拒绝处理，不截取后强行判断。初始化请求预算默认 20 次、最多 100 次，修改配置不能提升已初始化的预算。队列满时使基线失效并显示采集失败，不把漏掉的几轮悄悄算成一轮。

观察目录使用私有权限，JSON 文件权限为 0600。当前基线和待处理 job 包含原始限定材料，必须留在本地忽略目录；完成后删除 job 中的原始 delta，保留简短结果和请求墓碑。复用的凭据模式过滤只是辅助检查，不能保证任意源码都适合出站，仍需审查范围。

`status` 展示状态、判断、采集失败和客户端纳秒计时：采集准备、原命令、最终状态写入前的采集、评估及可用的传输/子进程阶段。父阶段包含子阶段，不能全部相加；缓存计时单独标记。这些不是服务端纯推理耗时，也不是 Agent 节省的时间。

## 录制对照结果与仍未证明的部分

冻结矩阵含 9 个漂移序列（6 个从第 1 轮起纯装饰性，3 个先做真实工作后才漂移）和 7 个标注为 on-goal 的真实上游提交；每轮都自报 `advanced` 并更换 hypothesis id。已提交的 live 录制使用问题集 `scoped-progress-sentinel-v2`（`jev-1.13.0`，35 轮，34 轮 completed，1 轮按失败关闭）：

| | 类型化重复保险丝 | `noul` 回执 | `choice` 回执 |
| --- | --- | --- | --- |
| 在 gold 轮被标记的漂移序列 | 0/9，按构造不可见 | 9/9 | 5/9 |
| 达到 `assist` 义务（阈值 2） | 0/9 | 9/9 | 2/9 |
| 真实 on-goal 提交被误报 | 0/7 | 已评估的 0/6 | 已评估的 0/6 |
| 真实 on-goal 提交无判定（按失败关闭） | — | 1/7 | 1/7 |
| 混合序列内的提前告警 | 0 | 0 | 0 |

6 个纯装饰性序列（含一个 18 KB 的改名扫描）在第 1 轮被标记、第 2 轮即可触发义务；3 个混合序列恰在各自的漂移轮（第 3 轮）被标记、第 4 轮触发义务：真实实现落地后的装饰性改动现在 `serves_acceptance` 只有 0.06–0.13，旧措辞下是 0.62–0.90。已执行的负结果探测（`serves_acceptance` 0.15、`evidence_increment` 0.85）与必要的失败测试（`serves_acceptance` 0.73）不被标记，原因是规则保护目标证据，而不是因为它们改变了行为。第二次独立 live 在全部 16 个序列上复现了同样的首次告警轮、义务轮与误报数，35/35 completed。已提交录制中按失败关闭的那一轮是一个真实 on-goal 提交（`fix_manager_refused_read_argument`）：它没有判定，既不是误报也不是确认通过，写成“0/7”会夸大证据。两次 v2 的客户端评估延迟中位 1.45–1.49 s、P95 2.9 s，早先录制为中位 0.74–0.81 s；差异来自网络与 provider 时段，不是问题集。输入 token 中位 1890。

相比早先录制改变了什么，以及为何还不能算留出证据：第一版问题把 `serves_acceptance` 问在整个文件上，并让 `noul` 以行为变化为门槛，只标记了 6/9，且漏掉全部“实现落地后的改动”轮；其请求还带有由样例名派生的 `goal_id`，可能暗示标签。v2 的措辞与规则是在看到这些构造序列上的漏检之后写的，因此构造用例对新措辞不再算留出集；7 个真实提交没有用于调参。35 个回答中有 1 个因所选标签不是概率 argmax 被严格解码器拒绝，该轮按失败关闭，既不算漂移也不算误报。

测试中的注入答案只证明链路，不证明模型质量。构造用例的 gold 由作者标注；真实提交的 on-goal 标签来自其已合入上游。进入干预前，应用 `drift label` 标注留出的多轮 Goal，在相同材料上对照保险丝与独立 Agent 裁判比较首次告警轮次，并测量误报、提前量、复核负担与完整开销。升级、暂停与自动纠正仍不在本次范围；遵守义务是否减少了无效工作在这里没有测量。
