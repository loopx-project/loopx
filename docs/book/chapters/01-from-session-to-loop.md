# 从一次会话到长程任务

一个 Agent 能在当前会话里改代码、跑测试、解释结果，不代表它能可靠地拥有一项持续数天的工作。本章说明这个差别从哪里来，以及它为什么需要一个不在会话里的东西。

## 一个看起来很顺利的开头

假设你要给已有 CLI 增加 `--format json`：

```text
10:02  你让 Agent 改输出层。
10:11  改完了，默认文本输出保持不变，加了测试。
10:14  本地测试全过，提交，推送，开 PR。
10:16  CI 开始跑。Agent 说："等待 CI 结果，通过后请维护者确认 JSON contract。"
10:17  你关掉窗口去开会。
```

到 10:17 为止，一切正常。问题从这一刻开始：

```text
次日 09:30  你重新打开。CI 昨天就绿了，但没人知道。
次日 09:30  维护者在 PR 里问了字段命名，没人回。
次日 09:31  Agent 不知道昨天做过什么，重新读了一遍代码，然后问：
            "需要我实现 --format json 吗？"
```

这个情境中的代码和测试可能都正确，失败发生在工作衔接上：等待对象、验收责任和恢复入口没有被接手者可靠地读取。即使 Host 保存了聊天，也仍需要从当前 PR、CI 与工作状态重建下一步。

## 为什么这是结构性的

自然的反应是"那就别关窗口"。但这取决于一个不可能维持的前提：工作必须在一次连续会话内完成。真实工作会不断打破它：

- 上下文被压缩，早期步骤让位给近期内容；
- CI 要等，而等待的时间以小时计；
- 维护者隔天才回复；
- 另一个 Agent 接手，它的上下文从零开始；
- 外部依赖更新，昨天的判断今天不再成立。

这些是长程工作需要准备处理的情形。关键控制信息如果只能靠当前上下文解释，跨会话接手就会依赖人工重建；持久记录与明确读取入口可以减少这种依赖。

## 会话上下文是工作内存

模型上下文天然适合承载这些东西：

- 当前问题的局部推理；
- 刚读取的代码；
- 本轮的工具有返回结果；
- 即将执行的短计划。

它不适合作为这些事实的唯一存放位置：

| 事实 | 为什么不能只放在上下文里 |
|---|---|
| 当前目标与验收条件 | 压缩后可能只剩最近的步骤，"为什么做"丢了 |
| 哪些工作已完成、通过了什么验证 | 下一位执行者无法区分"做过并验证"和"打算做" |
| 哪个动作在等谁的决定 | 等待是跨会话的，而上下文不跨会话 |
| 哪个 Agent 拥有当前任务 | 重启或交接后，所有权无法从记忆里恢复 |
| 外部世界的当前状态 | 昨天读到的 CI 状态今天可能已经变了 |

Host 可以持久保存 transcript，但旧对话本身不提供当前权限、验收或恢复状态。判据是接手者能否从明确的事实源重新核对这些信息，而不依赖原会话的隐含理解。

## 执行面与控制面

理解了"状态必须外置"，接下来的问题是：外置之后，谁来用它？

**Execution plane（执行面）** 负责执行一个有界动作：Agent 修改代码、shell 运行测试、provider 调用 GitHub、Host 启动下一次模型 Turn。

**Control plane（控制面）** 负责判断什么动作现在合法、为什么继续、何时等待，以及结果如何进入持久状态——目标与验收是否仍然有效、当前 frontier 里哪个 Todo 可以执行、是否存在需要用户处理的 Gate、当前 evidence 能否支持状态转换、quota 是否允许再启动一轮、中断后从哪里恢复。

```text
Control plane: 选择并约束下一步
       ↓
Execution plane: 执行一个有界动作
       ↓
Observation / receipt: 返回可验证结果
       ↓
Control plane: 接受、拒绝或重规划
```

**控制面不替代执行面。** LoopX 不写代码、不托管 Git、也不代替 CI。它让这些系统的结果能被一个跨 Turn 的工作生命周期消费——这正是 10:17 之后缺的那一环。

## 三类长程任务为什么能共用同一控制面

LoopX 的控制合同不绑定某一种业务流程。仓库里的 Control-Plane Course 用三类 Showcase 说明：领域事实和验收方式完全不同，Goal、Todo、Gate、Quota、Evidence 与恢复机制仍然可以复用。

| Showcase | 领域事实与判断 | 复用的控制面 |
|---|---|---|
| PR Issue Fix | issue feasibility、exact-head checks、review 与 merge state | Todo、claim、workspace guard、monitor、successor、terminal closeout |
| Single-Agent Auto ML | metric contract、matched baseline、实验 revision、外部 task 与 guardrail | Quota、Provider receipt、monitor、defer/resume、promotion Gate |
| Multi-Agent Auto Research | hypothesis、dev/holdout evidence、支持或反驳关系 | per-Agent frontier、handoff、Evidence lineage、promotion/retirement |

三条产品链都能压成同一个长期闭环：

```text
外部事实
  → Provider observation
  → Capability 的领域判断与 transition proposal
  → Kernel 检查 authority、frontier、quota 与 workspace
  → Agent / Host 执行一个 bounded Turn
  → 独立验证、evidence 与 receipt 写回
  → 重新计算 continue | wait | ask | replan | repair | terminal
```

复用的是**生命周期不变量**，而非一段通用 prompt。Issue-Fix 理解 `CHANGES_REQUESTED`，Auto ML 理解 matched baseline，Auto Research 理解 holdout——这些领域含义属于 Capability 与 Domain State。而谁能 claim、是否可执行、何时再次唤醒、什么证据允许 writeback、Goal 能否终止，由同一套 Kernel 合同决定。

这条边界也解释了为什么新增领域能力不该复制一套 runner、queue、retry 和 completion 状态机。领域层提供可判定事实与 proposal，Provider 执行外部调用，Kernel 拥有跨领域生命周期。

需要从三个 Showcase 进入架构与源码入口时，继续读 [Control-Plane Course 第 2 讲](/loopx/docs/development/control-plane-course/02-goal-control-plane-architecture/)；第一次接触术语可以先看[概念导读](/loopx/docs/development/control-plane-course/00-concept-primer/)。

## 最小外置状态长什么样

对上面的贯穿任务，最小状态不是完整 transcript，而是一组能回答恢复问题的事实：

```yaml
# 为解释而简化，不是 LoopX 文件格式
goal: 为 CLI 增加兼容的 JSON 输出
acceptance:
  - 默认文本输出不变
  - JSON schema 有测试
frontier:
  - todo: 等待维护者确认字段命名
    state: blocked
gate:
  question: 是否接受 error_code 作为稳定字段？
evidence:
  - unit tests passed at commit abc123
next_wake:
  when: maintainer decision arrives
```

这些字段的价值在于：**下一位执行者不必相信上一位 Agent 的自述**，而能从目标、工作队列、Gate、证据和 fresh environment 重新判断下一步。示例是解释模型，不是 LoopX 的存储格式。

## 代价与边界：什么被放弃了

外置状态有代价。说清楚代价，才能判断什么时候值得。

**代价一：多一份必须维护的状态。** 项目里多了一个不能随手改、需要理解其生命周期的目录。写错状态比不写状态更糟，因为下游会拿它当真。

**代价二：每一步都变慢。** 读取状态、判断合法性、验证、写回，都比"直接开干"慢。一次会话内能完成的小任务会被这些步骤拖累。

**代价三：需要外部事实可见。** 如果 CI 结果、维护者回复、外部依赖状态无法被程序读取，控制面就只能等，而"等"需要有人重新触发。

**边界一：本书只讲长程运行。** 范围封闭、能在当前上下文完成、不需要等待外部事件、没有跨 Agent 交接的工作，普通会话就够了——第 2 章的任务资格卡会给判据。

**边界二：控制面不拥有领域判断。** 它不判断一个 issue 值不值得修、一个实验指标是否显著；那是 Capability 的职责。

**边界三：恢复需要当前行动条件。** 详见[恢复与运行边界](04-runtime-boundaries.md)。

## 不变式

1. **Prompt 内容不能独自证明持久写回。** 检查可寻址的记录、身份和版本，而不是窗口是否还开着。
2. **执行面与控制面不能互相替代。** 控制面不写代码，执行面不决定自己是否合法。
3. **复用的是生命周期不变量，不是 prompt。** 三个不同领域能共用一套控制面，靠的是这条。
4. **下一位执行者不需要相信前一位的自述。** 如果需要，说明外置得不够。

接下来用[会话、Host Goal 与 LoopX](02-session-goal-loopx.md)判断任务需要哪层状态，再读[四个要求](02b-long-horizon-requirements.md)，把这些问题组织成完整的架构视角。
