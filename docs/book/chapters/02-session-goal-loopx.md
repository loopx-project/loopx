# 普通会话、Codex Goal 与 LoopX

上一章说明了跨会话衔接的问题。这一章回答：**任务需要哪些状态与持续执行能力，应交给哪一层？** 普通会话、Host Goal 和 LoopX 可以组合使用，重点是当前运行面能否保留需要的状态、等待条件和恢复入口。

## 从一个坏的结局开始

```text
周五 16:40  用户交代任务："给 CLI 加上 --format json，补测试并提一个 PR。"
周五 16:52  任务在普通会话里开始。改动不大，测试也在写。
周五 18:10  测试通过了。PR 已提交。会话结束，用户下班。
周六 05:00  CI 在第二个平台矩阵上失败。失败信息只落在 PR 页面上。
周一 09:20  用户回来。会话已经不存在了。接手者看到的是一个失败的 PR，
            和一句"补测试并提一个 PR"。
```

这个结局里没有人做错事。改动是对的，测试真的跑了，PR 也是真的。丢失的是**判断**：为什么
schema 选成现在这样、这个失败是不是预期内的、用户上次说的"保持默认输出兼容"具体指哪些
字段、以及那个还没回复的维护者意见。

它们只存在于那条已经结束的会话里。

现在把同一个任务换一种方式做错：

```text
周一 09:00  同样的任务交给 LoopX，Goal 已注册，Todo 已建：实现、测试、发布。
周一 09:10  测试失败。原因是一个字段名需要维护者拍板，其他人都不能决定。
周一 09:12  控制面建了 Gate G，阻塞 Todo C。这判断是对的。
周一 09:12  本机只跑 heartbeat。它没有渠道向任何活人推送这条未决问题。
周一 09:40  下一次 tick：Gate 仍阻塞 Todo C，独立的 Todo D 选不出一条能动的。
周一 10:20  之后的每一次 tick 都是同一个结果。
周二 14:00  用户从别人那里听说这个任务在等人拍板。
```

第一段故事里，任务留在了会话层，接手者什么都不知道。第二段故事里，任务被升级到 LoopX，
却缺少能把一个未决问题送到人面前的那个表面。两次失败的形状相同：**工作都停下来了，而且
没有人被告知要停下来等谁。**

代价并不止于这一次返工。从周一到周二，控制面每次唤醒都重新编译一次同样的结论，消耗 Host
唤醒、时间和模型调用（这类无变化的 Gate 等待不扣 LoopX quota），而任务的完成时间没有任何变化。

## 为什么"大任务用 LoopX，小任务用会话"不够用

自然的排序直觉是按任务的规模来分：短任务留在会话里，规模上去就升级到长程控制面。上面两个
故事都不符合这个直觉。第二个故事里的任务很小，只有三个 Todo，却因为一个未决问题卡了两天。

真正的变量在这里：**任务在停下来的时候，需要什么才能继续？** 会话层的合法停止点只有一个，
就是"会话结束"。它不接受"等 CI 跑完""等维护者拍板""等明天"这类停在半路的理由。长程层
有别的合法停止点，包括等待、阻塞和交接，条件是这些停止点能被写下来，并且有人会被唤醒。

于是有两个维度必须分开检查：

- **任务属性**：它会跨会话吗，它会等外部条件吗，它会被交接吗；
- **Host 表面**：在当前这个运行面上，等待、阻塞、唤醒和交接是否真的能发生。

只检查第一个维度，就会得到第二个故事。决策工具需要同时覆盖这两列。

## 三层状态

| 层次 | 主要拥有的状态 | 主要解决的问题 |
| --- | --- | --- |
| 普通 Agent 会话 | 当前 transcript、工具结果和本轮计划 | 完成一次上下文内的推理与执行 |
| Codex Goal | Host/thread 上持久的 objective 与 Goal lifecycle | 让同一 Host 围绕目标继续 Turn，并判断 active、blocked 或 complete |
| LoopX | 项目拥有的 Goal、Todo、Gate、Evidence、Quota 与恢复状态 | 组织跨 session、Agent、Host 和外部系统的可审计生命周期 |

LoopX 不复制 Host 的模型执行，也不把 Codex Goal 降级成一个 prompt 技巧。Host Goal 负责"继续
围绕目标运行"，LoopX 负责"从项目状态编译出当前合法的一轮工作"。

Host 可以保存会话 transcript，也可以保存 native Goal 的 objective 与 lifecycle。LoopX 额外提供项目层的结构化工作状态、权限和恢复入口。区别不只是保存时间长短，还在于这些信息是否能被当前决策可靠读取与校验。

## 四类 Actor

为了避免把产品、模型和状态系统都叫成"Agent"，先固定四类责任：

| Actor | 主要拥有 | 不应拥有 |
| --- | --- | --- |
| User / operator | 方向、私有材料、凭据、production、public claim 等边界决定 | 每个普通 Todo 的人工调度 |
| Host | session、模型 Turn、visible TUI 或 heartbeat 等唤醒表面 | 项目长期事实和自定义状态机 |
| Executor / Agent | 当前 Turn 的推理、工具调用、bounded delivery 与验证 | 隐式长期记忆和越权批准 |
| LoopX control plane | Goal、Todo、Gate、quota、evidence lineage、recovery protocol | 模型推理、Git/CI 外部事实 |

Dashboard、review packet 和 prompt 是 projection/interaction surfaces，不构成第五个
authority。上一节说的"唤醒表面"就落在 Host 这一行：能不能把一个问题送到人面前，取决于
Host 提供了什么，而不是取决于控制面写了什么。

## 三种身份必须分开

LoopX 同时处理 Goal、Agent 和 Host，但三者回答的是不同问题：

| 身份 | 回答的问题 | 稳定性与选择规则 |
| --- | --- | --- |
| `goal_id` | 正在推进哪个长期项目边界？ | 绑定 registry、Todo、Gate 和 evidence lineage；复用时选择已有的精确 id |
| `agent_id` | 当前由哪个 peer/lane 承担工作？ | 绑定 claim、Vision、quota 与 writeback；只有不存在已注册 lane 或显式 `--new-peer` 时新接入才默认注册 fresh identity |
| `host_surface` / runtime profile | 这一轮实际由哪个产品表面执行和唤醒？ | 绑定 App heartbeat、visible Goal 或其他 host loop；必须按当前运行面显式声明 |

已有 Goal 可以继续复用，不代表新 session 应接管已有 Agent identity。最新 Goal-start 合同要求：

1. 多个已注册 Goal 同时存在时，先返回只读 `goal_selection_gate`，再以精确 `--goal-id` 重跑；
2. 不根据相似的 objective、聊天摘要或目录名猜测 Goal；
3. 已存在已注册 lane，且 `--agent-id` 与 `--new-peer` 都未给出时，若当前 host thread 没有已存绑定，
   `start-goal` 返回 `thread_binding_selection_required`（`select_agent_identity`）：默认动作是选择
   已有 lane，不会自动注册新身份，也不会自动接管；
4. 没有任何已注册 lane（首次接入），或显式给出 `--new-peer` 且当前 host thread 没有已存绑定时，
   才默认注册 fresh identity：`start-goal` 返回 `fresh_agent_registration_required`
   （`register_fresh_agent`），首次接入无需额外 `--new-peer`；已绑定的 thread 沿用其绑定的 lane；
5. 只有用户明确要求接管某个已有 Agent，才以该精确 `agent_id` 继续；
6. Agent 名称或前缀不证明 Host，实际运行面要由 host/runtime metadata 说明。

这样，"继续同一个项目"与"冒充上一个执行者"不会被压成同一操作。

## 任务资格卡：先判断是否值得使用 LoopX

LoopX 不是"任务越大越应该用"的同义词。先把任务写成一张可审查的资格卡：

这张卡是本书提供的决策工具，不是 LoopX CLI schema，也不会被 `start-goal` 自动写入状态。
真正进入 LoopX 的 Goal、Todo、Gate、acceptance 和 boundary 仍以当前协议与 CLI 为准。

| 字段 | 要回答的问题 | 不满足时的默认选择 |
| --- | --- | --- |
| `duration` | 是否会跨 session、等待窗口或工作日？ | 普通会话 |
| `external_wait` | 是否要等待 CI、review、审批或外部资源？ | 普通会话或 Host Goal |
| `handoff` | 是否会更换 Agent、Host、设备或责任人？ | 同一 Host Goal |
| `authority` | 是否涉及 private read、凭据、production 或 external write？ | 先定义 Gate，不要启动自动执行 |
| `acceptance` | 什么可观察证据足以判断完成？ | 先补验收，不能只写"持续优化" |
| `baseline` | 和普通会话或 Host Goal 比较时，什么保持一致？ | 不做效果提升 claim |
| `stop_condition` | 何时完成、阻塞、降级或停止投入？ | 先补 terminal contract |

前四个字段直接对应第二段故事里的失败：`external_wait` 为真，说明这个任务会停在等待上；
`authority` 为真，说明这个等待必须由人给出决定。两项都满足时，"谁来唤醒那个人"就成了
启动前必须回答的问题，而不是运行一段时间之后才发现的缺口。

满足一项不代表必须使用 LoopX。真正有价值的组合通常是：跨 session + 有外部等待或 handoff +
有独立 acceptance，并且项目需要把 authority、evidence 和恢复条件外置。

### 如何比较普通会话、Goal 与 LoopX

如果要判断 LoopX 是否提升了真实任务，不要比较两个不同任务或不同预算。至少保持：

```text
same task semantics
same runner / model / reasoning settings
same verifier contract
same time and cost budget
```

记录 completion、独立 verifier、错误写入、人工介入、stop-policy、wall time 和 cost。没有 matched
baseline 或独立 verifier 时，可以记录使用体验，不能声称产品能力提升。

[Benchmark 研究 RFC](https://github.com/huangruiteng/loopx/blob/main/docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md)
定义当前研究边界。Benchmark-native 研究必须明确 arm semantics、authority boundary 与独立
verifier，才能作为产品研究证据。

## 用同一任务比较三层

仍以"为 CLI 增加 JSON 输出"为例。

### 普通会话

你对 Agent 说：

> 增加 `--format json`，保持默认输出兼容并补测试。

Agent 可以读取代码、修改文件和运行测试。如果 session 在等待 CI 时结束，恢复者通常只能依赖
Git diff、CI 和人类重新描述。下面这些信息可能只存在于对话中：

- 为什么选择当前 schema；
- 是否还在等维护者决定；
- 哪个失败是预期的；
- 哪个动作尚未真正发生。

### Codex Goal

Codex Goal 把 objective 和 Goal lifecycle 从单次 prompt 中分离。Host 可以围绕
同一目标启动后续 Turn，并在目标处于 active、blocked 或 complete 时采取不同动作。

因此，等待 CI 后继续工作不再要求用户重新粘贴完整目标。Goal 解决的是 **Host 内目标连续性**。
它不必自动成为项目 Todo 图、权限账本或跨 Host registry。

!!! warning "以当前 Host 为准"
    只在普通 prompt 中写 `/goal` 不等于建立了 Host 可读回的持久 Goal。具体入口、状态和恢复操作
    必须以当前 Codex 产品表面为准。

### LoopX

LoopX 在项目侧保存更细的控制合同。例如：

```text
Goal: ship-compatible-json-output
├── Todo A: implement formatter              done
├── Todo B: add schema tests                 done
├── Todo C: obtain field-name decision       blocked by Gate G
└── Todo D: release                          deferred until C

Gate G
├── scope: response.error_code
├── authority: maintainer
└── blocks: Todo C
```

下一轮不只知道"目标还没完成"，还知道：

- 哪个 Todo 可执行；
- Gate 只阻塞哪条 lane；
- 哪个 Agent 持有 claim 或 lease；
- 哪份测试结果是 evidence；
- 发布是否需要外部 effect receipt；
- 当前是否应该运行、等待或 monitor。

## LoopX 增加的五类项目合同

### 1. Todo、claim 与 handoff

Goal 表达项目结果，Todo 表达可调度的工作单元。LoopX 可以为 Todo 记录优先级、依赖、claim、
lease、successor 和 handoff。Per-Agent Vision 则保存某个 peer 当前的 bounded role direction、
acceptance summary 与 replan trigger；它是 per-Agent 的路线，也不是全局产品愿景。

这使"目标仍 active"与"当前谁可以做哪件事"成为两个问题。Agent id 是工作身份，不证明 Host
身份；`codex-*` 前缀也不能证明任务实际运行在 Codex App 还是 CLI。新 session 可以复用同一
Goal 的历史与 frontier，并在没有已注册 lane 或显式 `--new-peer` 时以 fresh Agent identity 进入；
已有 claim 则通过显式 takeover 或 handoff 处理。

### 2. Gate 与 authority

对话可以向人提问，但一个问题是否阻塞所有工作、只阻塞一个 Todo，或者只是一条提醒，需要
明确建模。

LoopX 区分：

- `user_gate`：缺少决定时相关工作不能合法继续；
- `user_action`：需要人处理，但不必阻塞 Agent 的其他 lane；
- safe fallback：不依赖该决定、仍可安全执行的工作。

Gate 的重点在于把决定的 scope、authority 和被阻塞工作绑定起来，而它送到人面前的路径由
Host 提供。开头第二段故事缺的正是后半句。

### 3. Evidence 与 receipt

"Agent 运行了命令"不能替代"状态转换已被证明"。LoopX 区分：

- proposal：建议做什么；
- observation：看到了什么；
- validated evidence：经过检查、可以支持结论的证据；
- effect readback：外部系统返回的当前事实；
- receipt：对一次已接受动作的持久记录。

例如发起 `git push` 后网络超时，不能仅凭工具调用开始就标记发布完成。需要远端 readback 或
其他可验证 receipt。

### 4. Scheduler、monitor 与 quota

Codex Goal 可以由 Host 继续。LoopX 进一步把"现在是否应该继续"变成项目决策：

- `quota should-run`：这一轮是否符合预算与状态；
- monitor：外部条件未变化时静默等待；
- scheduler hint：Host 应在什么节奏再次唤醒；
- backoff：连续无变化时避免盲目轮询；
- spend：只有产生并写回有界进展后才记账。

Host 仍然拥有实际唤醒机制。LoopX 输出调度合同，不假装自己是所有 Host 的 scheduler。

### 5. 跨 Agent、跨 Host 与恢复

LoopX 的 canonical state 属于项目。Codex App、Codex CLI 或其他受支持 Host 可以读取同一个
Goal 边界，而不是各自维护一份"当前进度"。

```text
Codex App heartbeat ─┐
Codex CLI Goal ──────┼──> LoopX project state ──> current Turn packet
Other host hook ─────┘
```

Host 可以不同，项目状态不能分叉成多个事实源。恢复依赖 event、lineage、projection、fresh
environment read 与 replan，不需要新 Host 继承旧 transcript。

### Host 兼容矩阵

LoopX 保留同一 control-plane contract，但不同 Host 的启动和唤醒机制并不相同。当前公开
[Runtime Connector Catalog](https://github.com/huangruiteng/loopx/blob/main/docs/integrations/runtime-connector-catalog.md)
与各 Host adapter 文档给出的主要路径是：

| Host surface | 驱动 | 关键限制 |
| --- | --- | --- |
| Codex App | `$loopx <task>` + App heartbeat | cadence 需要 RRULE apply/readback/ACK |
| Codex App over SSH | visible `/goal` | 不依赖 App automation tools |
| Codex CLI TUI | generated bootstrap + visible `/goal` | 保持 visible、interruptible |
| Claude Code | `/loopx` + opt-in native `/loop` adapter | 仍走同一 quota/writeback |
| OpenCode 1/2 | `/loopx` + opt-in Goal bridge / persistent worker | bridge 或 worker 保持 Host 可见性与停止语义 |
| Pi | opt-in Goal extension + `/loopx` | 绑定保存在项目 `.loopx/`，不获得额外 authority |
| KunlunCode Goal Pro | `loopx-kunluncode` adapter | 只在严格验证后写 completion 与 quota |
| DeepSeek Harness | native skill + same-session Driver / `loopx turn run-once` | 每段 bounded execution 仍需独立 validation |
| Shell / other Agent | guided packet + caller-owned runner | 无 runner hook 时由调用方唤醒 |

这张表就是资格卡第二个维度的数据来源。表中出现一个 Host 不代表所有 Host 都支持相同
automation API。`host_surface` 未知时，应省略一次该参数并使用只读 selection Gate；不要把
Codex CLI、IDE plugin、App SSH 或普通 shell 猜成 Codex App heartbeat。完整表面、启动方式、
停止策略和验证证据以 Runtime Connector Catalog 与对应 Host 文档为准；Dev Book 不复制每个
adapter 的完整 runbook。

## 如何组合 Codex Goal 与 LoopX

典型组合是：

1. LoopX 从项目状态选择 Todo，检查 Gate、能力与 quota；
2. LoopX 生成有界 task body 或 decision packet；
3. Codex Goal 持续承载这个 Host 上的执行；
4. Agent 完成一段工作并验证；
5. 结果写回 LoopX，LoopX 再决定下一轮。

```text
LoopX control plane -> Codex Goal continuation -> Agent Turn
        ^                                      |
        `---------- validated writeback -------'
```

因此，Codex Goal 与 LoopX 的关系更像 Host lifecycle 与 project lifecycle 的组合，而不是两套
互相替代的 Agent runtime。

## 代价与边界：引入一层状态买到了什么

这一章开头两个故事，一个该升级而没有升级，一个升级了却没有配套的 Host 表面。把任务放进
LoopX 换来的是可交接和可审计，代价同样明确。

**代价一：控制面本身要维护。** 每多一个 Goal，就多一份需要保持可读的 registry、Todo 图和
evidence lineage。维护成本随任务数量增长，而它换来的是任务被中断之后仍然可以继续。

**代价二：多了一层需要正确填写的输入。** 资格卡上的 `acceptance` 和 `stop_condition` 必须由
人先写清楚。写不清楚，LoopX 只能在一个说不清何时算完的目标上空转，这比留在会话里更贵。

**代价三：启动之前要多回答几个问题。** 存在多个 Goal 时必须精确选择；agent 身份不能凭名称
前缀或相似度推断；host surface 未知时先走只读选择 Gate。这在单次任务上显得啰嗦，在跨会话任务上正是
恢复的依据。

**边界一：任务规模不作为判据。** 资格卡问的是任务停下来时需要什么，规模只出现在
`duration` 一行里，而且是以"会不会跨 session"的形式出现。

**边界二：LoopX 不让模型变聪明。** 它带来的是可审计的执行，模型能力、推理质量和
验证难度都不因此改变。想提升后两者，要在 harness 和 verifier 上投入。

**边界三：Host 表面决定哪些能力真的可用。** 资格卡判定的 `external_wait` 和 `handoff`，
只有在当前 Host 能唤醒、能推送、能回读时才会生效。缺少这些表面的 Host 上，正确的做法是
缩小任务范围或换一个表面，而不是把任务升级上去期待它自己解决。

**边界四：控制面只为项目级问题存在。** 没有跨 session、没有外部等待、也没有交接的单次任务，
留在会话里更快。为了"更 Agentic"而制造状态，付出的就是上面的代价一。

## 具名失败：选错层次的代价

三个场景，都是上面机制的直接推论，读者可以自己观察。

**失败一：该升级的任务留在会话里。** 一个需要维护者拍板的字段名，留在会话里就只能等人
再来问一次。判断信号是：`authority` 或 `external_wait` 为真，而任务的存活时间超过一次会话。

**失败二：升级到 LoopX，但没有能唤醒人的表面。** 这正是开头第二段故事。Gate 建得对，
Todo 阻塞得对，控制面每次 tick 都做出正确判断，而那个人从头到尾没有收到任何消息。

**失败三：跨 Host 续跑时继承了错误的身份。** 新 session 复用同一 Goal 的历史，并在没有已注册
lane 或显式 `--new-peer` 时以 fresh Agent identity 进入，这是合法路径。若推断出一个相似的 `goal_id`，或者直接接管旧
`agent_id`，得到的就是两个写入者对同一份"当前进度"的假设。

对应的观察方式是读状态，而不是读 transcript：重启之后控制面还能说出任务停在哪、在等谁，
以及下一步由谁执行。这三项是判断能否接手的基础，还需结合任务风险与维护成本决定是否值得接入。

## 不变式

1. **Transcript 可以传递信息，但不能单独证明当前控制状态。** 换运行面后，仍须核对身份、权限、工作项和证据版本。
2. **每一层的持续性边界需要实际确认。** 普通会话也能等待或阻塞；长程控制面负责让这些条件被结构化保存、读取和继续处理。
3. **资格卡有两个维度。** 任务属性说明需要什么，Host 表面说明当前能不能提供。只查前一列，
   会得到一个每次唤醒都正确、却永远无法推进的 Goal。
4. **身份要显式。** Goal 的复用、Agent 的新建、Host 的实际运行面，三者都要声明，都不能靠
   前缀或相似度推断。
5. **比较三层要用同一把尺子。** 没有 matched baseline 和独立 verifier 时，能报告的是使用
   体验，不能报告产品能力提升。

判断依据是任务会在哪里等待、由谁推动继续、当前运行面能否提供那次推动。下一章[长程运行提出的四个要求](02b-long-horizon-requirements.md)把这些判断展开，再进入状态、工作图和单轮事务。
