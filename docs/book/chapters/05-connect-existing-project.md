# 连接你的 Git 项目

前面的章节解释了为什么状态、权限、执行和证据需要分开。本章把它们接回同一件事：
**让一个已有项目产生第一份可以检查、可以继续、也可以明确停止的工作结果。**

沿用[贯穿任务](00-reading-guide.md#running-example)：A 实现兼容的 JSON 输出 T1，B 补文档 T2，
M1 观察 CI，G1 保留维护者的 schema／发布决定，T3 在条件满足后交付。
这是合成教学任务，不是已经运行成功的项目记录。命令使用你的真实项目与当前 packet；
T1、M1、C1 等代号不是 CLI id，也不能当作 API payload。

!!! tip "快速阅读路线"
    第一次接入，按[准备](#prepare-project)、[连接与身份](#connect-and-identify)、
    [接入验收](#connection-acceptance)执行，再选择 App 或 CLI 章节激活 Host。
    返回[第一份交付](#first-delivery)走完实际工作。已有 Goal 则从读取现状开始，不重复 bootstrap。
    [可选能力](#optional-capabilities)不是主线前置条件。

## 先分清三个完成点 {#three-completions}

安装完成、接入完成、工作完成不是同一句话。安装回答 CLI 与所需 runtime 是否可用；
接入回答工作归属、状态位置与运行路径是否明确；交付回答实际产物是否满足当前验收。
接入成功可以暂时没有执行中的 Turn；一轮成功也可以仍在等待外部检查或批准。

| 完成点 | 可以下的结论 | 还不能下的结论 |
| --- | --- | --- |
| 环境就绪 | 当前入口及所需依赖通过了对应检查 | 项目已经选对 Goal，Host 已经持续运行 |
| 接入可验收 | Goal、Agent、项目边界与下一入口明确，写入结果已读回 | T1 已实现，CI 已通过，发布已获准 |
| 一项工作交付 | 当前产物和对应验证可复核，生命周期记录完整 | 所有 Todo、所有审批和整个 Goal 均已完成 |

**"接入可验收"这一行要能观察到这些事实**：环境就绪（`loopx doctor` 报告可用，项目存在
`.loopx/registry.json` 与 `.loopx/goals/<goal-id>/ACTIVE_GOAL_STATE.md`）；`loopx status` 能显示
active state 与当前 frontier；再次连接按精确 `goal_id` 复用已有 Goal 而不是覆盖；新接入的执行者使用
fresh `agent_id`，除非用户明确授权 takeover；`.loopx/` 与 `.loopx/goals/` 不进入 Git。这些本地状态
是控制面状态，不是项目源码。

因此，本章不是把所有命令连成一段可以盲目粘贴的脚本。读取、预览和执行分段进行，
每次只在前一步给出足够依据后继续。出现拒绝时先理解所保护的条件，不为让教程跑通而绕过检查。

## 一、准备：确认项目，保护已有状态 {#prepare-project}

从你准备管理的 Git 项目根目录开始，不是从 LoopX 的源码目录开始。在目标仓库根目录打开你正在使用的 Agent 开发工具，把下面提示词中的目标和 Host 改成自己的
情况后直接发送：

```text
请把当前 Git 项目安全接入 LoopX。

目标：
- 为这个项目建立一条可恢复、可验证的发布流程。
- 当前 Host 是 Codex App。如果当前环境不是这个 Host，先告诉我，不要猜测。

执行合同：
1. 先只读检查项目根目录、当前分支、git status、.gitignore，以及是否已有
   .loopx/registry.json、.loopx/goals/ 或其他 LoopX 状态。不要覆盖、reset 或清理现有内容。
2. 运行 loopx --version、loopx doctor，并读取本次实际需要的 --help。不要依赖记忆中的旧参数。
   如果 LoopX 尚未安装，先报告缺失和官方 installer 将写入的位置，得到我授权后再安装；不要把
   “找到安装命令”写成“安装已完成”。
3. 如果已有 LoopX 状态，先读 loopx registry、loopx status 和相关 history。优先复用精确
   goal_id；不要 force reconnect，不要按目标文字相似度选择 Goal。
4. 确保 .loopx/、.loopx/goals/ 和 .local/ 被 Git 忽略。如果这些目录已有项目用途或已被跟踪，
   停下来报告冲突，不要擅自删除或 untrack。
5. 对尚未连接的项目，先运行 loopx connect --dry-run，展示将创建或修改的状态；确认没有冲突后
   再执行 loopx connect。已有 registry 时不要为了“重新开始”重复 bootstrap。
6. 如果有多个可选 Goal，停在只读 goal_selection_gate，把 choices 和推荐依据交给我选择；
   在选择前不要写 Todo、注册 Agent 或激活 Host loop。
7. 这是新的执行者时，选择一个新的 public-safe agent_id，先 preview，再用当前 CLI 支持的
   register-agent 命令执行并 read back。只有我明确要求 takeover 时才复用已有 agent_id。
8. 使用 loopx start-goal --guided --project . 和明确的 goal text 生成 transaction packet。
   Host 已知时显式传入正确的 --host-surface；只执行 packet 中与当前权限相符的步骤。
9. 任何用户审批、外部写操作、凭据、权限扩大、Host 选择或 destructive Git 操作都必须停在
   Gate，不能替我决定。
10. 完成后验证 loopx status、todo list、history、quota should-run、git status，以及
   git ls-files .loopx .loopx/goals .local。
11. 不要提交或推送。最后给我一份“接入回报”，列出 goal_id、agent_id、Host、创建或修改的文件、
    当前 Todo/Gate、执行过的 mutation、验证结果、未解决问题和下一步。只完成 preview 时必须
    明确写“尚未接入完成”。
```

这份提示词不是把控制权交给 Agent。它把可执行工作委托给 Agent，同时把以下决定留给你：

- 多个 Goal 中选择哪一个；
- 是否 takeover 已有 Agent identity；
- 使用哪个 Host surface；
- 是否允许外部写操作、凭据或更大 write scope；
- 是否提交或推送仓库改动。

### 接入回报应该长什么样

一个可验收的接入回报至少包含：

```yaml
onboarding:
  status: complete | blocked | preview_only
  project_root: <repository root>
  goal_id: <exact goal id>
  agent_id: <fresh id or explicitly approved takeover id>
  host_surface: <exact host or unresolved>
changes:
  - <changed path and why>
gates:
  - <decision still owned by the user>
verification:
  doctor: pass | fail
  status_readback: pass | fail
  local_state_ignored: pass | fail
  tracked_private_state: []
next_action: <one concrete next step>
```

不要接受“命令运行成功”作为唯一结论。Agent 应同时给出状态 readback 和 Git 隔离证据。

### 示例：首次接入一个项目

```text
请按本章的 Agent 接入合同，把当前项目接入 LoopX。
目标是“为每个发布候选建立构建、审批和 Pages 部署的可恢复流程”。
当前 Host 是 Codex CLI visible TUI。使用新的 public-safe agent_id。
不要提交、推送或触发发布；遇到 Goal 选择、权限和外部写操作时停下来让我决定。
```

### 示例：安全续接已有状态

```text
请先只读检查当前项目已有的 LoopX registry、Goal、Todo、Gate 和 history，再帮助我续接。
优先复用精确 goal_id，但不要自动 takeover 任何已有 agent_id。
如果存在多个 Goal、活动 lease、未完成 mutation 或 workspace 路由不一致，只给诊断和选择，
不要写状态。不要提交或推送。
```

### 先读取仓库现状

无论自己操作还是交给 Agent，写入任何 LoopX 状态之前，先在项目根目录只读检查仓库：

```bash
git rev-parse --show-toplevel
git branch --show-current
git status --short
git ls-files .loopx .loopx/goals .local
```

仓库根目录、当前分支和现有改动是三项不同事实。已有改动不应被 reset 或删除；
如果当前目录是 linked worktree，还要确认实际交付会发生在哪一棵树。看错工作树时，
一个目录的“没有改动”不能证明另一个目录没有产生工作。

项目可能已经包含 `.loopx/registry.json` 或 `.loopx/goals/`。先识别已有 Goal、在途工作和状态位置，
不要复制其他项目的 registry，也不要为了得到一个空白起点覆盖旧状态。
下面是常见布局示意，不是所有 authority、租约和日志的完整物理位置：

```text
your-project/
  .loopx/registry.json
  .loopx/goals/<goal-id>/ACTIVE_GOAL_STATE.md

configured runtime root/
  provider-owned state, execution records and receipts
```

Markdown 在 legacy 路径可能是源，在已选择的 authority 路径可能是兼容视图。
先依据[状态章节](state-substrate.md)辨认模式；不能从文件名推出谁有权写入。

### 忽略规则、index 与历史是三个问题

没有命名冲突时，为实际使用的本地状态目录建立忽略规则。常见条目是：

```text
.loopx/
.loopx/goals/
.local/
```

若同名目录已有产品用途，应先解决冲突；以上规则也不替代对自定义 runtime 路径的检查。
使用 Git 检查具体路径：

```bash
git check-ignore -v .loopx/registry.json
git check-ignore -v .loopx/goals/example/ACTIVE_GOAL_STATE.md
git check-ignore -v --no-index .loopx/registry.json
git ls-files .loopx .loopx/goals .local
git check-ignore -v .loopx/goals/example/ACTIVE_GOAL_STATE.md
```

`check-ignore` 解释匹配规则；要看规则本身，`!` 否定规则不表示已经忽略。
默认情况下已跟踪文件不受忽略规则支配；`--no-index` 用于不考虑 index 而诊断规则，
不是“强制忽略”，也不是专门处理尚不存在的文件。
`ls-files` 默认显示当前 index 中的路径；有输出说明路径被跟踪或暂存，**不单独证明已经推送**。
没有输出也不证明旧提交或远端从未包含敏感材料。
依据见 [Git check-ignore](https://git-scm.com/docs/git-check-ignore) 与
[Git ls-files](https://git-scm.com/docs/git-ls-files)。

发现不应跟踪的本地状态时，先检查其内容、暂存情况和历史，再由有权限的人处理 index 或历史。
本章不要求自动执行 `git rm`、清空目录或重写历史。删除工作目录并不能撤回已经公开的信息。

## 二、确认当前安装，而不是只找到命令 {#verify-installation}

本书基线要求 Python 3.11+ 和 Node.js 22.22.3+。本章代码块采用 POSIX shell；
原生 Windows PowerShell 7 用户应使用[安装指南](/loopx/docs/guides/installing-loopx/)中的等价步骤，
无需仅为本教程使用 WSL。

已安装时先运行：

```bash
loopx --version
node --version
loopx doctor
```

尚未安装且已获准修改当前 Python 环境时，发布版入口是：

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

安装与 skill 安装会写入环境；包安装可能访问网络。它们不是只读检查。
普通使用者不必先 clone LoopX；完整源码 checkout 留给后文的实现阅读和回归练习。
已有 pip、pipx 或 archive 安装则按[附录升级说明](appendix-reference.md)确认安装 owner，
不要在不清楚来源时叠加另一个安装。

`doctor` 比 `command -v loopx` 多回答依赖、安装和集成状态，但不是所有 Host 路径都已验证的证明。
需要验证实际 Effect runtime 与 journal checkpoint 路径时再使用：

```bash
loopx doctor --deep
```

LoopX 管理按需启动、空闲退出的 TypeScript Effect runtime，用户不需要手工维护 daemon。
在这个 runtime 的检查语境里，`stopped` 可以表示可按需重启；`missing`、`unsupported` 或 `probe_failed`
需要先处理。不能把该解释套用到已经退出、又无人负责唤醒的 Agent Host。

安装被阻塞时，记录失败项和当前版本，回到安装 owner 修复。不要通过换一个 Goal、复制状态目录
或关闭权限检查来解决依赖问题。

## 三、连接与身份：先选择对象，再接受写入 {#connect-and-identify}

对于尚未连接、已有状态也已检查清楚的项目，先预览：

```bash
loopx connect --dry-run
```

确认项目根、Goal 与状态位置正确后，才执行连接并读回：

```bash
loopx connect
loopx registry
loopx status
```

对于已有项目，应先读取当前连接，按现有路径续接，而不是为了重试一项任务重复连接。
`connect` / `bootstrap` 登记 Goal 并写 active state；首连不会替调用方生成一套 onboarding Todo。
没有可执行工作时，下一步是确认实际任务与工作边界，不是凭空增加配额。

### Guided packet 是计划，不是执行回执

接下来让当前入口解释目标：

```bash
loopx start-goal --guided --project . \
  --goal-text "增加兼容的 JSON 输出，保留默认文本输出；验证和文档完成后交付，不自动发布"
```

返回的是 guided transaction packet。预览成功不证明 Todo 已写、Agent 已注册或 Host 已启动。
读取 packet 当前要求补齐的选择，按照其提供的精确命令继续；不要从旧对话恢复一条已过时的执行指令。

存在多个 Goal 时，在 `goal_selection_gate` 的 choices 中选择精确 `goal_id`；目标文本相似不能作为
静默合并的依据。Goal 选择与 Agent 接管也不同：前者确定长期工作边界，后者涉及执行责任。

有任务文本而未指定 Agent 时，fresh identity 的默认路径取决于是否已有 registered lane，
以及是否明确请求 `--new-peer`。已有 lane 时不要把它当成新 session 的自动身份。
需要新增执行者，可以使用当前支持的注册入口：

```bash
loopx register-agent --goal-id <selected-goal-id> --agent-id <new-public-safe-agent-id> --require-new
loopx register-agent --goal-id <selected-goal-id> --agent-id <new-public-safe-agent-id> --require-new --execute
```

先预览，再在已有授权内执行。`--require-new` 让已注册的 id 返回冲突，而不是幂等成功；guided
packet 生成的 fresh 注册命令也带这个参数。只有 execute 结果的 `ok`、`changed` 与 `written` 均为
true，`global_sync.ok` 为 true，且 `registration_readback.verified` 为 true 时，才用这个新 id
继续 Todo writeback 或激活。`changed=false` 或冲突说明该 id 已存在：换一个 fresh id，不要把它当成
注册成功。接管已有身份必须是明确选择，并继续满足当前 lease、workspace 和 lifecycle 约束。

### Host 选择不是能力授予

已知实际 Host 时显式传入；以下二选一，不是依次激活两个 Host：

```bash
# Codex App
loopx start-goal --guided --project . \
  --goal-text "增加兼容的 JSON 输出并验证，不自动发布" \
  --host-surface codex-app

# Codex CLI visible TUI
loopx start-goal --guided --project . \
  --goal-text "增加兼容的 JSON 输出并验证，不自动发布" \
  --host-surface codex-cli-tui
```

身份已明确时还应使用 packet 提供的精确 Goal/Agent 绑定。不知道 Host 就保持选择未完成，
不要用一个熟悉的名称代替探测。具体激活与读回分别见 [Codex App](06-codex-app.md) 和
[Codex CLI](07-codex-cli.md)，其他适配器见 [Runtime Connector Catalog](/loopx/docs/integrations/runtime-connector-catalog/)。
类型注册、当前可启动、进程内自主继续、进程退出后可再次唤醒，是不同条件。
一个参数被接受不证明这些条件都已成立。

## 四、接入验收：现在可以进入哪一步？ {#connection-acceptance}

先用读取入口检查当前事实：

```bash
loopx registry
loopx status
loopx todo list --goal-id <goal-id>
loopx history --goal-id <goal-id>
git status --short
git ls-files .loopx .loopx/goals .local
```

它们不请求完成 Todo 或扣减交付配额，但“读取”不承诺整个进程完全无 IO：
可能涉及 runtime 启动等支持动作。若环境有严格隔离要求，应按所用入口、provider 和设置单独确认。
不要把所有名称带有“检查”的命令归入同一种无副作用类别。

准备进入本轮时，使用当前 Host 规定的准入路径，例如：

```bash
loopx quota should-run --goal-id <goal-id> --agent-id <agent-id>
```

读取完整 interaction contract、selected Todo、权限、workspace 与后续指令，不能只取一个
`should_run` 布尔值。尤其 `--codex-app` 的相关准入路径可以创建 heartbeat receipt；
它与单纯列举现状不同，不应在诊断脚本中无限重复。
`refresh-state`、获取/续租、结算和 scheduler ACK 也都是各自有条件的操作，不是通用查询。

| 四问 | 接入验收需要的答案 |
| --- | --- |
| 依据什么事实？ | 实际项目与安装版本，精确 Goal/Agent，选择的状态来源、Host 和工作区 |
| 为什么允许或拒绝？ | 本次注册/连接条件是否满足；当前准入允许哪一项工作，或缺少哪个条件 |
| 哪项证据支持？ | 执行结果与对应读回、Git index 检查、当前 contract；不是目录存在或一句“成功” |
| 下一入口在哪里？ | 条件满足则激活已选 Host 或推进选中工作；缺身份、依赖、权限或 runtime 时回到其 owner |

报告可以用普通文字，不需要新增 YAML schema 或另一份状态账本。例如：

> 项目连接与身份已读回，私有状态未进入当前 index。Host 激活尚未确认，所以只完成接入准备，
> 还没有第一次执行。下一步按 CLI 章节中的当前 activation packet 启动并读回；不会宣称 T1 已交付。

这是一份本地操作报告的**教学示意**，不是产品返回类型。对外分享时去掉私有路径、目标内容和凭据。

## 五、从接入走到第一份交付 {#first-delivery}

完成 Host 章节后回到这里。目标不是让界面显示“正在运行”，而是完成一个足以独立验证的小工作段。
不要把本节的推演称为真实 Host 的端到端测试；实际验收必须来自自己的运行结果。

### 第一幕：让 A 做 T1，而不是让它拥有整个发布流程

T1 的输入是当前代码和兼容要求，允许的结果是可检查的代码与测试。发布仍属于 T3 的范围。
Agent 通过当前 Todo/Host 入口确认工作归属、可写工作区和验证声明，然后执行选中的 bounded segment。

这里故意不提供一条绕过当前 packet 的“万能开始命令”：不同 Host、authority mode 与已有工作状态
具有不同前置条件。使用返回的合法动作，含义比从书里复制一个历史 `should_run=true` 更明确。
具体关系见[完整一轮](03-one-turn.md#running-turn)与[工作图和权限](work-graph-and-authority.md)。

### 第二幕：C1 上通过的检查，支持哪一句结论？

产物形成后，验证默认文本行为、JSON 输出以及该修改影响的非法输入。
记录实际 revision、执行的验证和结果；“进程退出 0”只在那个验证器确实检查了目标后置条件时才有意义。
验证期间声明或代码改变，先重新检查结果适用范围，不能让旧验证替新输入背书。

工作经对应 lifecycle 入口接受后，核对 Todo 与写回结果。需要结算时按当前 contract 完成。
`spend_required` 与 `spend_receipt_required` 的恢复路径不同：前者尚欠结算，后者需要恢复回执。
来自可信当前响应的 `settlement_owed.command` 仍要核对原身份、registry/runtime 和授权；
不能删掉绑定参数，也不能执行任意日志里的命令。详见[结算练习](12-control-plane-course.md#checkpoint-settlement)。

已经确认的写回不因后续结算超时而消失。发生未知效果时转入[原身份的恢复](04-runtime-boundaries.md)，
不是给同一任务换 id 再做一遍。本轮内部 quota 记账也不等于模型供应商的真实账单。

### 第三幕：等待 CI，不停止所有独立工作

本地验证不能替代 M1 对远端 C1 的观察。登记的等待应能说清观察对象、适用 revision、下次观察
与终止/持续观察策略，还要有实际可用的执行面。支持的 boundedness 方式包括 expiry、resume 条件
或显式 watch-only；不要把某一字段误写成所有 Monitor 的唯一选择。

G1 没有批准发布时，T3 不能因 CI 通过或 T1 已结算而获得权限。T2 若仍可证明独立，
可以在自身约束满足后继续。用户需要回答问题与 Agent 可以继续另一项工作可以同时成立。
具体判断见[观察与调度](04b-budget-and-admission.md)和[工作图](work-graph-and-authority.md)。

无变化的观察不应冒充交付；它仍可能有计算和网络成本。没有收到通知也不是外部世界没有变化的证据。
若 Host 已退出且没有可用唤醒机制，应报告运行缺口，而不是把 quiet 状态解释为健康等待。

### 第四幕：条件改变，更新受影响的判断

假设代码从 C1 变成 C2。旧 CI 的绿色结果仍然是 C1 的历史事实，但不能自动验收 C2。
应定位受影响的验证与依赖，而不是清空全部记录。G1 的决定是否仍覆盖当前产物，也要看它实际绑定的
对象、scope 与条件，不能只看“approved”这个词。

若维护者同时修改了目标或验收，应走已有 planning/replan 和决定入口，将变化与当前工作关联。
书中的 C1/C2 是解释适用性的例子，不是在声明已有一个统一的 Goal intent-version schema。
修改目标的权限不能由 Agent 为了凑齐“完成”而自行产生。

### 第五幕：把结果返回给需要验收的人

T3 的当前条件均有证据后，重新判断下一步。交付一个可审阅结果与执行外部发布仍是两种动作；
后者继续受其独立授权和实际运行条件约束。普通 owner 决定也不能统一交给自动 Turn 的 quota 代办。

一份有用的返回，应指明产物位置/版本、哪些验收条件已有支持、哪些仍未满足、做过的验证及其限制，
以及接下来由谁决定或行动。链接到了产物、工作内部已结算、收件人实际得到结果、Goal 已接受，
这些事实分别核对，不能把其中任一项自动提升为全部完成。

下面是本地交付报告的示意，而不是额外持久化格式：

> JSON 输出与文档已在 C2 完成；列出的兼容检查及 C2 的 CI 支持当前实现结论。
> 已有工作写回与结算可追溯。维护者的发布决定仍未完成，因此返回待审阅产物，不执行发布。
> 下一入口是 G1 的决定处理；决定接受且仍适用于当前产物后，再重算 T3。

这份报告即使没有“全部完成”，也能让接手者继续。反之，一句“测试全绿，任务完成”既没有界定
被证明的范围，也没有保留未完成责任。

## 六、让 Agent 帮你接入，但不给它猜测权限 {#delegate-onboarding}

可以把以下提示交给已在项目里工作的 Agent。它是委托示例，不是机器强制执行的新协议。
已有明确授权的普通操作可按其范围完成，不要求每个命令都再次打断用户；身份接管、权限扩大、
外部发布和破坏性操作不能从模糊目标中推断出来。

```text
请在当前 Git 项目中建立并走通一项兼容 JSON 输出工作，不自动发布。

先读取实际项目根、分支、现有改动、LoopX 安装及已有 registry/Goal；不要覆盖或清空状态。
检查本地运行材料的忽略规则与 Git index；发现冲突或已跟踪私有材料时报告，不擅自清理历史。
已有 Goal 按精确 id 续接，新执行者不自动接管旧 Agent identity。
先读取 connect/start-goal 的 preview；只执行当前授权、目标与 Host 均已明确的步骤。
遇到选择或条件缺口，说明缺少什么及由谁确认，不通过换 id、关闭 guard 或重复 bootstrap 绕过。
推进当前允许的 T1；验证真实产物并按当前 contract 写回、必要结算，再读回结果。
等待 CI 或批准时说明对象、下一观察条件和执行面；只继续可证明独立的工作。
结尾分别报告：实际事实、允许/拒绝理由、支持证据和下一入口。
只做了预览、接入、一次交付或完整验收，分别如实说明；未经授权不要 commit、push 或发布。
```

遇到不符合预测的结果，首先保留原身份并定位差异。[四组练习](12-control-plane-course.md#reader-checkpoints)
展示怎样检查反例；[故障分流](12-control-plane-course.md#diagnostic-routing)帮助找到下一入口。
练习里的状态删除与损坏注入只属于隔离测试，不能套用到本项目。

## 七、常见阻塞：修复缺失的条件，而不是重开整个流程 {#onboarding-recovery}

| 现象 | 当前需要确认的事实 | 下一入口与完成判据 |
| --- | --- | --- |
| doctor 失败 | 命令实际来自哪里，哪项依赖或集成失败 | 安装指南或该 runtime owner；对应检查重新通过，不以换 Goal 修安装 |
| registry 中有多个 Goal | 选择的是哪一个长期目标 | 当前 selection packet；精确选择后重跑，不从文字相似度合并 |
| 项目状态存在，但全局视图没有 | 源写入是否存在，全局同步是否失败 | 检查 registry 权限与 `sync-global` 的当前用法；源与投影分别读回，不把缺可见性当作没连接 |
| delivery worktree 不匹配 | 实际编辑位置和记录的交付位置 | 当前 `refresh-state --delivery-workspace-path` 路径；明确它是受控写回，修后核对两侧，不复制状态 |
| 已写回但本轮未收口 | 原 Turn 的已完成效果和未决记录 | [恢复章节](04-runtime-boundaries.md)；复用历史事实并补齐所欠步骤，不重做整个 Host |
| 没有可选 Todo 或没有可用 Host | 当前工作边界、等待条件、执行能力 | planning/Host 原入口；必要时明确等待或人工交接，不凭余额制造工作 |

报告失败需要包含版本、入口、对象绑定和最小复现依据。公开 Issue 只带 public-safe 材料；
原始 registry、私有目标、凭据、transcript 和完整日志不应直接上传。

## 八、可选能力放在真实需要之后 {#optional-capabilities}

读到这里，已经可以把一项普通工作从接入推进到明确返回。Capability、Provider 和 Extension 不需要
一开始全部配置。先问当前缺的是调用者结果、执行实现、环境 readiness，还是某项权限。
这些问题对应不同 owner。

```bash
loopx capability list --format json
loopx capability show <capability-id> --format json
loopx --format json configure-goal --goal-id <goal-id>
loopx extension list --format json
```

Catalog 可见不等于 Provider 已安装；已安装不等于 enabled；doctor-ready 不等于当前 Todo 已获准。
`--extension-manifest` 只影响相应 catalog read 时，也不能被当作安装回执。
`configure-goal` 不带设置项时读取当前功能；设置项按具体功能定义，而不是通用的“enable 任意 capability id”。

例如，当前 change-quality 的配置路径是：

```bash
loopx configure-goal --goal-id <goal-id> --change-quality-enabled
loopx configure-goal --goal-id <goal-id> --change-quality-enabled --execute
```

第一条预览，第二条只在允许该配置变化时执行；随后读回实际设置。配置由 `source_registry` 指向的
项目源拥有，不能因为全局视图方便就转向镜像写入。`--runtime-root` 也不重新授予配置权威。
Todo 的 `required_capabilities` 是已有执行前提；`target_capabilities` 是正在建设或修复的对象，
不能把缺失 target 一概当成禁止修复它的理由。

需要学习独立 Provider 时，沿[放置位置](08-extension-placement.md)、[完整教学包](09-extension-scaffold.md)
与[生命周期](10-extension-lifecycle.md)继续。这条分支使用 `packages/loopx-text-stats/`，从包、manifest、
输入到安装、调用、停用和回滚统一学习，不在接入章节再维护一套重复安装步骤。
已有 `loopx-finance-value-discovery` 等领域包则遵循自己的 README 和输入合同；
它们不是本章必装组件，也不能从包名推断其数据采集、账户访问或外部执行能力。

## 代价、适用范围与下一章

持久状态需要备份、迁移与明确归属，不能当作随时可删的缓存；每次更换 Host、工作区或安装版本，
也需要重新确认相关条件。额外读取和验证有成本，但不能未经测量就宣称每项任务必然更慢。
可以明确拒绝接入或停在预览，不必为了使用产品而创造不需要的治理步骤。

本章的具体操作以 Git 工程为前提，不表示研究或资料工作不能使用 LoopX；其他领域需要自己的
产物版本与验证来源。离线、隔离或组织限制也要按实际 Host/provider 逐项判断，不能只凭“本地优先”
就声称满足，更不能仅凭“需要离线”就一概否定。

现在应能区分“准备好了”“正在做”“这一轮已接受”“仍在等待”和“结果已交付”，并为这些判断指出依据。
接下来按实际 Host 阅读 [Codex App](06-codex-app.md) 或 [Codex CLI](07-codex-cli.md)；
工作中断时回到[恢复](04-runtime-boundaries.md)，等待时回到[观察](04b-budget-and-admission.md)，
准备修改 LoopX 本身时再进入[贡献地图](source-protocol-map.md)。
