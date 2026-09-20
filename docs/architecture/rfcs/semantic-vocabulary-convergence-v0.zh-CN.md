# RFC：语义词表收敛与提交期漂移检查（v0）

- **RFC status：** Draft
- **Delivery maturity：** Partial（M0/M0.5 检查、M1 类型化动作域与 M2 Turn 契约生成已实现；M3/M4 退休仍未完成）
- **Authors / owners：** LoopX 贡献者；控制面内核维护者拥有批准权
- **Created：** 2026-09-15
- **Last normative revision：** 2026-09-17
- **Implementation baseline：** `1dc6ad8d8`
- **Related contracts：** `loopx/semantics/vocabulary_v0.json`、
  `loopx/semantics/inventory.py`、
  `loopx/control_plane/turn_transaction_contract.json`、
  `loopx/control_plane/coordination/coordination_state_contract_v0.json`、
  [Turn Envelope v0](../../reference/protocols/turn-envelope-v0.md)、
  [Turn Loop Controller v0](../../reference/protocols/turn-loop-controller-v0.md)、
  [TypeScript 控制面迁移 v0](typescript-control-plane-migration-v0.zh-CN.md)
- **Language mirror：** [English](https://github.com/huangruiteng/loopx/blob/main/docs/architecture/rfcs/semantic-vocabulary-convergence-v0.md)

## 文档地图与维护契约

本 RFC 同时交付英文版 `semantic-vocabulary-convergence-v0.md` 与本中文语义镜像；
两者互相链接，规范章节变更时必须同步修订。

- 第 1-10 节是持久的设计与验收契约。
- 第 11 节是规范性交付计划。
- 第 12 节记录已实现的选择与剩余决策；已交付的选择或建议答案本身不等于批准。
- 附录是非规范的执行账本、决策日志、证据登记与被否决方案。

RFC 成熟度与交付成熟度彼此独立。带日期的进度条目不修改规范章节。

---

## 1. 决策摘要

1. **什么成为权威。** `loopx/semantics/` 下的策展注册表与检查时计算的清单。注册表
   `vocabulary_v0.json` 为每个内核与跨运行时词表命名：允许定义它的确切
   `module::Symbol`、词表之间的关系（同一概念、共享字段名、子集）、完整投影，
   以及仓库同意只降不升的预算。计算得到的清单映射 `loopx/` 下
   每一个闭集载体：字符串枚举、`Literal` 别名、命名闭集、TypeScript `as const`
   数组，以及在多个模块中定义的每个常量名。一支公共 smoke
   `examples/semantic-vocabulary-drift-smoke.py` 在每个 PR 的默认 `pytest` 扫描
   里用注册表和当前源码的扫描结果核对代码；premerge 与 full-public 舰队是附加表面（第 10 节）。
   扩宽词表、分叉常量或调整预算须在同一 diff 中包含必要的 owner／注册表
   修改。普通新增载体由扫描器自动发现，无须提交生成快照（Q9）。
2. **权威与生成。** 每个枚举住在自己的 owner 模块里；注册表通过 AST 与文本
   扫描核对代码，产品代码永不导入它。M1 生成器核对注册表一致性后，从 Python
   owner 派生 TypeScript effective-action 绑定。这不改变线上取值，也不把值的
   定义权转移给注册表。M2 已通过 `generate_turn_contract.py` 从
   `turn_loop_controller_contract_v0.json` 生成 Turn 词表、路由投影与有序 controller
   规则；这不代表所有跨运行时词表都已迁移。
3. **默认与可选边界。** 检查对仓库始终开启。它没有运行时开关，因为它从不在
   产品内运行。
4. **主要约束。** 失败即关闭、确定性、且不能仅靠改数据被削弱。任一运行时的
   未注册字面量、第二个定义模块、预算超支、注册表列出但无模块携带的值、过期
   的生成绑定、不带符号的 owner 声明、低于记录下限的覆盖计数，每一项都让 smoke
   失败。扫描识别的分发形式写在 smoke 里而不在注册表里。smoke 只读已跟踪
   源码，不打印任何私有数据。
5. **本 RFC 不批准的事。** 把三套 Turn 结果枚举合并为一套、拆分
   `effective_action` 的三个槽位、删除任何旧的 should-run 字段、删除任何
   Python 孪生模块、重命名任何现有值。这些属于后续里程碑，各自受 `AGENTS.md`
   中 schema 缩减规则的门控。

## 2. 问题与动机

LoopX 由大量小型 agent 驱动的 PR 生长而成。每个 PR 在需要之处加上它需要的
词汇。结果不是错误行为，而是漂移：同一概念多种拼法，同一常量在多个文件定义，
同一字段名承载不同词表，以及任何模块都可扩宽而无人察觉的开放字符串集合。
评审者无法从 diff 判断一个新字面量是新状态还是拼写错误，文档也无法跟上一个
没人枚举的集合。

语义面是全仓库的，不是 Turn 内核的局部问题。清单生成器在基线上扫描 `loopx/`
下 1169 个源文件得到：

| 载体 | 数量 | 说明 |
| --- | --- | --- |
| Python 字符串枚举 | 102 | 控制面 29、capabilities 17、extensions 6 |
| 命名闭集（`NAME = frozenset/tuple` 字符串） | 490 | 66 个是字段列表、31 kinds、31 states、29 statuses |
| `Literal[...]` 别名 | 8 | |
| TypeScript `as const` 数组 | 40 | 21 个有值集相等的 Python 侧；14 个没有 |
| 命名字符串常量 | 2002 | 754 个是 `*_SCHEMA_VERSION` |
| 同名同值、跨两个运行时 | 166 | 合法的 py/ts 孪生 |
| 同名同值、同一运行时 | 25 个名字 / 58 处定义 | 分叉；其中 7 个是 schema 版本 |
| 同名不同值 | 18 个名字 / 59 处定义 | 见下 |

在基线上审计出的具体失败：

- `TURN_ENVELOPE_SCHEMA_VERSION` 定义了三次：
  `loopx/control_plane/quota/turn_envelope.py:16`、
  `loopx/control_plane/quota/turn_envelope.ts:13`，以及
  `loopx/control_plane/turn_driver/driver.py:31` 的一份私有副本。M0 删除该副本。
- `HANDOFF_MODES` 由 TypeScript owner 定义，又在
  `control_plane/testing/authority_e2e_fixtures.py:33` 以字面元组重定义。M0 让
  夹具元组从 `HandoffMode` 枚举派生。
- 同一值集跨运行时有两个名字：`work_items/delivery_outcome.ts` 的
  `MATERIAL_DELIVERY_OUTCOMES` 等于 `goals/goal_frontier/outcome_continuity.py`
  的 `VISION_OUTCOME_CHECKPOINT_MATERIAL_OUTCOMES`。两者都是 `DeliveryOutcome`
  去掉 `surface_only`；没有任何地方说明这一点。
- 同名不同值：`DECISION_CONTEXT_CAPABILITY_ID` 在
  `capabilities/decision_context/packets.py:18` 是 `decision_context`，在
  `extension_provider.py:24` 是 `decision-context`；`MCP_REQUIREMENT` 在
  `kunluncode_goal_mode/cli.py:28` 是 `mcp==1.28.1`，在
  `claude_goal_mode/scripts/install.py:83` 是 `mcp<2`。另外 16 个名字是模块内
  通用常量（`SCHEMA_VERSION`、`COMMAND`、`CAPABILITY_ID`），今天的碰撞无害，
  明天却不可见。
- Turn 结果种类靠手工维护了两份：`transaction.py:28` 的 `LoopXTurnResultKind`
  与 `settlement.ts:49` 的 `TURN_RESULT_KINDS`。今天一致；没有任何测试断言过。
  另外 11 对 py/ts 词表同样如此（settlement 的 step、binding、failure kind，
  receipt-bound phase，scheduler transition，Todo completion 的 continuation 与
  recovery，delivery outcome，delivery workspace kind，Goal amendment class，
  Todo decision scope）。
- `effective_action` 是两侧都没有枚举的开放字符串集合。Python 与 TypeScript
  共靠字符串比较分发 31 个不同字面量。其中两个（`observe_replay`、
  `block_replay`）由 `turn_journal.ts:656` 写入 Turn Envelope 的 replay
  observation 槽位，根本不是 should-run 裁决。另外两个
  （`quota_action_selection_deferred`、`quota_action_selection_rejected`）是
  `cli_commands/quota.py:279` 复制进该槽位的 quota 错误码。
  `AgentScopeFrontierAction` 的值又被写进同一 envelope 的
  `agent_scope_frontier.effective_action` 槽位。一个字段名，三套词表。
  `user_gate.py:162` 还把该槽位与 `skip` 比较，而没有任何生产者写入它。
- 三套近似同构的 Turn 结果词表并存：`LoopXTurnResultKind`（12）、
  `LoopXTurnRoute`（8）、`LoopDisposition`（8），`repair`/`repair_required` 与
  `replan`/`replan_required` 是同一裁决的不同拼法。route 到 disposition 的投影
  是 `loop_controller.py:126` 的私有字典；没有任何声明说它覆盖全部输入。真正承重的
  `decide_loop_disposition` 决策表（result kind、retryable、attempt budget、
  decision user action、durable no-follow-up）只存在于控制器协议文档的散文里。
- 文档已称为 legacy 的六个 should-run 决策字段仍各被 7 到 35 个 Python 模块
  提及，没有棘轮阻止新消费者。
- `loopx/control_plane` 下有 43 对同名 `.py`/`.ts` 模块，而迁移 RFC 是
  replacement-first。这个数量没有守卫。
- 仓库已经在运行一支 AST 支撑的控制面债务棘轮
  （`loopx/canary/maintainability_ratchet.py`），带评审化的例外生命周期，但它
  度量的是模块指标与依赖方向，不是词表形状。词表漂移没有棘轮。

现有 owner 无法在本地解决，因为每处修复天然跨模块：Turn driver、quota、
todos、capabilities 与 TypeScript 运行时各自拥有同一想法的一种拼法。

### 不变量

- **I1 单一 owner。** 每个注册词表或常量，恰有注册表列出的定义模块，且注册的
  符号名在 `loopx/` 下别无定义。其余模块一律 import。
- **I2 闭集。** 注册词表可携带的每个值都被列出。任一运行时的代码都不携带未
  注册的值，注册表也不列出代码不携带的值。阶段 `m0`，已交付，且今天即阻断：
  固定字面量扫描遇到未注册的比较会让 smoke 以非零退出。它的证据以扫描器识别的
  分发形式为界，因此以其他形式进入代码的值是未验证，而不是已证明不存在。
- **I3 跨运行时一致。** 词表同时有 Python 与 TypeScript owner 时，两侧集合完全
  相同。
- **I4 完整投影。** 注册投影为每个源值恰好命名一次：要么映射，要么声明拒绝。
- **I5 棘轮只降。** 退休、孪生与清单预算可在任何 PR 中调低。每个预算在 smoke
  里另由一个 `BUDGET_ANCHOR`（或 `RETIREMENT_ANCHOR`）字面量钉住，每个下限由
  一个 `COVERAGE_ANCHOR` 钉住，沿用
  `tests/control_plane/test_m6_quality_gates.py` 的 `RFC_MODULE_BUDGETS` 锚点
  模式，但有一处刻意的不同：注册表的值必须**等于**锚点。先例用 `<=` 比较，
  这会让一个已收紧到锚点以下的预算，在之后的 PR 里不改任何代码就涨回锚点。
  相等性让每次收紧都是两个文件的 diff，每次放松都是评审者可见的代码修改。
- **I6 同 diff 可见。** 语义变化与必要的 owner、注册表或预算修改落在同一个可评审
  diff 中；计算得到的清单报告是证据，不是需提交的权威。
- **I7 确定性且公开安全。** 检查只读已跟踪源码，不需网络或凭据，失败文本只
  命名文件与值，绝不含私有数据。
- **I8 覆盖只增。** 注册词表数、owner 符号数、投影数、关系数、schema 版本数
  与扫描后缀集合被记录为下限。owner 只能是 `module::Symbol` 或 `null`；裸模块
  路径被拒绝，null owner 必须声明字面量扫描。扫描识别的分发形式固定在 smoke
  里。因此一次注册表修改不可能悄悄收窄守卫所见。
- **I9 两种载体形状都被度量。** 词表进入代码的形态有两种：字符串常量
  （`NAME = "value"`）与多值载体（枚举、命名闭集、`Literal` 别名、
  TypeScript `as const` 数组）。两者适用同一条冲突规则：同一个名字在两个模块
  中被定义，值集相同是孪生，值集不同是分叉。冲突预算只统计共享词表子集；
  `SCHEMA_VERSION`、`COMMAND`、`*_LABEL` 这类模块局部约定名仍保留在清单
  总数中可见，但不算漂移。
- **I10 在 PR 路径上。** 漂移 smoke 通过
  `tests/architecture/test_semantic_vocabulary_drift.py` 跑在默认 `pytest`
  扫描里，因此在每个运行 Python 测试的 PR 上失败即关闭。`examples/` 下的舰队
  发现与 `repo-architecture-budget` premerge profile 是附加表面，不是义务：
  舰队在合并后和按日程运行，premerge 按改动路径的 token 选择。
- **I11 角色互异。** 一个词表有一个 owner、若干生产者、若干解释者与若干透传者
  （第 5 节"词表的角色"）。只有 owner 定义集合，只有生产者写入值。提及、
  比较、序列化或展示一个值不带来任何所有权。开始写入值的解释者或透传者已经
  变成生产者，必须登记为生产者。阶段 `m0_5`，已交付。生产者这一半今天即阻断：
  写入 kernel 值却未登记为生产者的位点会让 smoke 以非零退出
  （`undeclared producer sites`）。消费者这一半不阻断：解释者与透传者刻意不登记，
  因此它们唯一的证据是建议性的 F3 清单，没有任何检查能在其上失败。
- **I12 每个内核值都被生产。** 对 `kernel` 词表，未列入 `compatibility_only`
  的每个值至少有一个固定生产形式能识别的生产位点，或已登记输入解码器的
  可执行见证。变量来源备注本身不能作为生产证据。只被比较的值是死值或兼容值，
  绝不是 canonical。
  `effective_action` 的 `skip` 是第一个预期失败。阶段 `m0_5`，已交付。在 kernel
  层上今天即阻断：没有观察到生产者的已注册 kernel 值会让 smoke 以非零退出。证据以
  扫描范围内的生产形式为界，即 F2 的 `verified / registered` 为 6 / 26，因此
  `cross_runtime` 层是未验证而不是通过。M0 时字面量扫描把被比较的值当作已携带。
- **I13 生产者只写注册值。** 写入注册集合之外值的生产位点失败即关闭，与是否
  有消费者比较它无关。生产比比较更严：消费者比较一个未注册值是死代码，生产
  者写一个未注册值是协议漂移。阶段 `m0_5`，已交付。今天即阻断：被识别的生产者
  写入注册集合之外的值会让 smoke 以非零退出，值域与 F1 相同，为 6 / 26。未解析的
  动态位点被报告并计数，绝不当作已证明安全。M0 时字面量扫描把两种形式合在一起覆盖。
- **I14 作用域靠声明而非推断。** 在多个模块中定义的名字是分叉，除非注册表把它
  声明为 `bounded_context` 并列出各上下文及每个上下文一个 owner 符号。已声明
  的名字离开分叉预算；改名不改变预算的含义，不算修复。阶段 `m0_5`，已交付。
  今天即阻断：没有把每个定义模块恰好枚举一次的声明会让 smoke 以非零退出，值域为
  4 / 4 个已声明上下文。M0 时 `SOURCE_SURFACES` 被计为分叉并加了备注。

## 3. 范围与非目标

### 范围内

- 注册表文件、其 schema，以及编辑它的所有权规则。
- 计算清单、可选报告的导出／校验命令及其测试。
- 漂移 smoke 及其在 premerge 与 full-public 舰队中的位置。
- M0 注册的词表：四个 Turn 内核集合（`turn_result_kind`、`turn_route`、
  `loop_disposition`、`effective_action`）、`agent_scope_frontier_action` 与
  `lease_action`，以及基线上 Python 与 TypeScript owner 值集相等的二十个跨
  运行时集合；route 到 disposition 的投影；九条关系；Turn Envelope 的 schema
  版本；六个旧 should-run 字段；控制面孪生数量；清单的分叉与冲突预算。
- 后续里程碑：把 `effective_action` 变成类型化枚举、拆分其三个槽位、通过契约
  发布投影、按现有仓库规则退休旧字段与孪生模块。
- 扫描根是 `loopx/` 包。本 RFC 说的"全仓库"指 `loopx/` 下两个运行时的全部
  载体，不是 git 树里的每个文件。清单的 `root` 与每条 `literal_scan.roots`
  都写 `loopx`，smoke 不读其他目录。

### 非目标

- 改变任何运行时决策、载荷形状或线上格式。
- 扫描 `apps/`（基线约 90 个 TypeScript 文件）或 `examples/`（十余处 smoke 里
  的 `effective_action` 断言）。它们是消费者与测试替身，不是生产者；一个断言
  了未注册值的 smoke 对 M0 不可见，在某个里程碑扩根之前接受这一点，扩根也会
  抬高第 10 节的合并序成本。
- 手工策展每个闭集。清单映射全部闭集；只有跨模块或跨运行时边界并被分发的
  词表才带 owner、值与关系进入策展层。
- 取代 `turn_transaction_contract.json` 或
  `coordination_state_contract_v0.json`。它们仍是各自阶段与记录的 owner；本
  注册表可以引用它们，不能复述它们。
- 取代 `maintainability_ratchet.py`。它拥有模块指标与依赖方向；本注册表拥有
  词表形状。两者的例外生命周期是否合并见第 12 节 Q7。
- 用散文术语表作为强制机制。术语表是有用的伴随物，在第 12 节跟踪，但它不能
  让构建失败。

## 4. 现行系统契约

基线 `1dc6ad8d8` 上的事实：

- `turn_transaction_contract.json` 是两个运行时同时读取的唯一契约：
  `effect_program.py:160-166` 加载阶段元组，`turn_journal.ts:1` 导入该 JSON。
  这是注册表作为共享事实源所效仿的模板。
- `coordination_state_contract_v0.json` 更进一步，通过
  `scripts/generate_coordination_state_contract.py --check` 生成
  `coordination_state_contract_generated.py` 与
  `coordination_state_contract.generated.ts`，由
  `tests/control_plane/test_coordination_state_contract.py` 守卫。清单生成器
  现在效仿它，M2 提议的生成阶段之后效仿它。
- canary runner 会发现每个已跟踪的 `examples/**/*-smoke.py`
  （`loopx/canary/runner.py:392`），因此 `examples/` 下的 smoke 无需在
  `planner.py` 或 `premerge.py` 登记。
- `loopx/canary/maintainability_ratchet.py` 是现有的 AST 支撑的控制面债务棘轮。
  它带有含 `retirement_plan` 的评审化例外并检测过期例外 id。它的对象是模块
  体积、`Any` 密度、决策点数量与禁止的依赖方向；它不读取枚举或常量的值。
- `AGENTS.md` 已要求状态分类使用类型化枚举、禁止 Python 为控制面权威建立第二
  事实源、新增模块前做 scope-fit 评审、并要求任何 schema 缩减获得维护者批准。
  本 RFC 增加的是让这些规则在 diff 中可观测的检查；它不改变规则本身。
- `loopx.control_plane` 的 package data 已经打包 `*.json`；`pyproject.toml`
  增加一行，让 `loopx.semantics` 以同样方式打包其两份 JSON。

## 5. 提议架构

### 所有权与权威

注册表由控制面内核维护者拥有。任何贡献者可以调低预算，或随携带它的代码一起
新增一个值。只有维护者可以批准调高预算、删除值或迁移 owner 模块，批准记入
附录 B。

禁止的替代权威：第二份注册表、复述已注册值的模块内列表，或宣称对已注册词表
具有规范性的散文表格。

**名字的作用域（M0.5 已实现）。** 清单仍按名字归组。`SOURCE_SURFACES` 的
四个有界上下文已登记在 `scope_declarations`，`check_scope_declarations` 将其
owner 模块与实际定义模块核对。已声明的名字仍留在原始分叉清单，只退出语义分叉
预算（I14）。改名不能证明语义修复。M0 的误分类属于历史情况；这不意味着所有
剩余分叉都已声明，也不意味着已证明各上下文值集互斥。

### 词表的角色

提及一个值的模块不是它的 owner，一个词表也不止一种参与者。消费者是读取或接受
值的上位角色，解释者和透传者是它的两个受跟踪子角色。注册表区分这些角色，因为
对每种角色有意义的检查不同：

| 角色 | 做什么 | 是否登记 | 检查 |
| --- | --- | --- | --- |
| Owner | 以每个运行时一个 `module::Symbol` 定义闭集 | 是，自 M0 | I1 到 I3 |
| 生产者 | 把值写入字段：赋值、dict 或对象字面量、构造函数关键字、在已登记判定函数内 `return` 字面量、访问 owner 枚举成员 | `kernel` 词表必须，自 M0.5 | I12、I13 |
| 消费者 | 读取或接受词表值；解释者和透传者都属于这个上位角色 | 通常不登记；只报告关系，不做策展 | F3 |
| 解释者 | 消费者的一种，据值分支或映射：`if`、`match`、`switch`、成员测试 | 否；由分发扫描发现，`--report` 排序 | I2、F3 |
| 透传者 | 消费者的一种，序列化、持久化、转发或展示值而不改变其含义 | 否 | F3；涉及持久化时还需 F6 证据 |

由此得到两条规则。没有生产者的值是死值或兼容值：`skip` 在 `todos/user_gate.py`
被比较却无处写入，M0 放过它，M0.5 让它失败，直到被删除或列入
`compatibility_only`。生产比比较更严：M0.5 单独扫描生产形式，对未注册的被生产
值失败（I13），M0 的字面量扫描继续捕获未注册的比较（I2）。解释者与透传者刻意
不登记；否则每次消费者改动都要碰注册表，正是第 6 节对消费者计数所拒绝的搅动。
它们与词表的关系是 `--report` 的建议性输出。

生产形式在 M0.5 固定在 smoke 里，与分发形式同理：Python 的 `x["f"] = "v"`、
envelope 或 packet 类型构造函数的关键字 `f="v"`、注册表列为生产者的函数内的
`return "v"`、对 owner 枚举的成员访问；TypeScript 对象字面量里的 `f: "v"`、
`x.f = "v"` 与条件表达式。`variable_sourced_values` 保留给生产者从扫描无法跟随
的变量构造的值。哪些词表必须列生产者：`kernel` 自 M0.5；`cross_runtime` 只在
M0.5 之后新增或删除值时；`cross_module` 只在晋升后（Q8）。持久化是生产扫描能回
答的属性：若某个已列生产者符号是 journal 或 receipt 的写方，该词表标为
`persisted`，这正是 Q2 与 Q10 等待的事实。

### M0.5/M1 的可执行生产证据

生产者守卫与 owner 载体检查使用不同证据。定义枚举成员只能证明集合成员关系，
不能证明生产。对带生产者元数据的词表，守卫比较观察到的结果值与 `values`，拒绝
未登记的**函数位点**，并要求每个非兼容值存在观察到的生产者。变量来源备注不能
替代存活证据。`return_producers` 列出其标量返回表达式属于该词表的已登记函数；
返回整个 packet 的构建器不会因此把无关返回文字当作词表值。

Python 通过 AST 解析字段赋值（含下标、属性及带注解赋值）、字典、调用关键字、
owner 成员结果及声明函数的标量返回。导入枚举的别名只解析到已登记 owner，含经由一个被跟踪模块的一跳未改名再导出（第二跳、改名再导出或重新绑定保持 unknown）；
被遮蔽的名字与无法绑定的调用仍为 unknown。条件表达式只检查结果分支，
排除条件中的字面量。另有三条局部形式被绑定，且各自只在明确条件下成立：对
**同模块内**一个未被装饰、非生成器、以普通 `def` 定义的顶层函数的调用，解析为
该函数自身全部 return 的并集，实参从不绑定到形参，因此返回形参仍为 unknown，
结果与调用点无关；被写入多次的局部变量解析为文本上位于该读取之前的那些写入的
并集，且仅当该名字的每一次 store 都是普通的 `name = expression`、**并且**没有
任何写入与该读取处在同一个循环内时成立；只经由直接字面量键下标写入被改动的容器
保留其未被触碰的键，被写入的键携带其初始化值与每一次写入的并集。凡落在上述条件
之外的——装饰器、`async def`、生成器、递归、导入调用或属性调用，把靠后的写入带回
读取处的回边，`with`、`except`、海象、增量赋值、解包、`global` 或 `del` 造成的
重绑定，别名、方法调用、计算键、负索引或更深层的 store、逃逸进调用，以及未知的
`**` 展开——都让该位点保持 unknown，而不是采信一个取值。
TypeScript 的对象写入、赋值及声明返回使用仓库的 TypeScript
解析器，并报告与 Python 扫描器相同的阻塞原因词汇，因此同一套残量分类覆盖两个
运行时。两个解析器都不执行被检查源码。这些是句法结果证据，不是可达性或全程序
数据流证明。

`uv run python examples/semantic-vocabulary-drift-smoke.py --report` 列出未解析的生产
位置。unknown 不能补足缺失值的生产证据。生产者守卫对六个 kernel 条目使用不同证据：`effective_action`、`turn_route`、
`loop_disposition` 和 `agent_scope_frontier_action` 使用源码见证；
`turn_result_kind` 另有固定入口 `transaction._result_kind` 的可执行输入见证。
真实解码器必须为每个注册输入返回相同的类型化成员，并拒绝非法探测输入。这证明
存在允许的生产路径，不表示 Host 实际发出过全部成员或所有 Host 执行都合法。
`input_producer` 不能从数据任意指定执行代码，验证入口固定在 smoke 中。

`lease_action` 明确分类为 legacy/兼容保留：仓库运行时调用者使用分开的
acquire/renew/transfer/release command 类。四个成员为旧的类型化
`LeaseModeGateCommand` 输入接口保留到 M4 调用者/迁移评审；不声称存在持久化
使用。只有每个值都带保留理由及退休里程碑时，生产者列表才能为空。新发现的
生产者必须让原兼容声明失败。
没有生产者元数据的 kernel 词表会明确
报告为覆盖待完成，不能把 owner 一致性宣称为 I12/I13 完成。所有要求的词表通过
相应验收行之前，M0.5 仍未完成。

decision owner 补登记了旧字面量扫描漏掉的五个现存结果：`blocked_health`、
`blocked_wait`、`control_plane_repair`、`operator_gate_notify` 和 `throttled_skip`。
这些登记保留现有 quota 行为。M1 删除无生产者的 `skip` 和合成夹具使用的
`operator_gate`。fallback 消费者改为识别实际的 `quota_skip`；允许执行的 scoped
fallback 不能仍携带跳过动作。旧字段退休仍须单独验收。

TypeScript 解析器准备命令是在仓库根目录执行 `npm ci --ignore-scripts`，使用仓库
锁文件。扫描本身不需网络或凭据；需要 Python 3.11+ 和仓库支持的 Node 运行时。

### M1 动作值域与兼容性

#### 为什么需要这一阶段

M1 的目标是让调用者读对字段，并使后续 PR 能区分“新增决策”与“新增诊断”。
仅登记一个更大的字符串集合不能解决这个问题：把 `result_kind` 或任意 host
动作复制进 quota 动作字段，会让下游将不同含义的值送入同一分发逻辑；仅看到
枚举被比较，也不能证明系统确实产生过该值。M1 分离这些证据，并修复 scoped
fallback 仍识别无生产者 `skip`、而实际输出为 `quota_skip` 的不一致。

这里采用最小必要边界：根动作仍是可区分的 `D ⊔ F`，不为所有字符串增加
线上标签；只登记承重生产者，不要求每个消费者登记；保留历史签名数据的读取
契约。它能检查有限词表、已支持输出形态和生成物一致性，不能证明整个程序
语义完备、所有分支可达或任意变量流都安全。

代价是改动 owner 后要再生成绑定，本地扫描还需锁定的 TypeScript 解析器。这些检查复用已有 CI 作业，但仍会增加
作业工作量和提交修复成本；“没有新增 required job”不等于没有新增义务。
失败时先判断是否真的改变语义：裸动作值改为 owner 引用；新增决策补 owner、
生产者和消费者验证；诊断留在 `error_code`，Turn 结果留在 `decision`；生成物
过期才运行第 10 节命令。扫描误判应修扫描规则并加反例，不能通过扩宽值集、
降低覆盖或放宽预算来消除失败。

#### 输出契约与兼容边界

生产证据中的 `return_paths` 指定返回对象的明确字段/索引路径；`call_producers`
登记经过评审的 builder 输出参数，并核对已跟踪源码中的模块绑定和真实签名。
声明与代码锚点同步修改。这是经过评审的输出契约，不是对任意 helper 函数体
语义的自动证明。局部枚举容器和任意判定函数的参数不证明生产；选出的标量必须
流向已观测输出。被修改或逸出的可变别名保持 unknown。代码生成采用严格枚举
提取；包括第二个 owner 无效的情况在内，都先拒绝不支持的成员，再写任何生成物。
selector 必须与代码拥有的映射精确相等，其他词表默认为空，因此新增 selector
也必须修改代码锚点。同名 keyword 参数在输出角色未确认时，只保留值域/unknown
证据，不能证明生产者存活，也不能迫使普通消费者登记为生产者。

令 `D` 为 `EffectiveAction` 拥有的 32 个决策值，`F` 为
`AgentScopeFrontierAction` 拥有的四个前沿值。根 should-run 及其 envelope 投射
通过 `A = D ⊔ F` 保留现有动作字符串。注册表以代码锚点固定两个成员词表，检查
`D ∩ F = ∅`，因此无需新增线上标签即可从值识别所属域。TypeScript 绑定和联合
类型从两个 owner 派生，不另维护第三份值表。这是 Q6 的注册并集方案。一个
union 成员不能证明另一个 owner 的生产者存活性；规范决策函数的标量返回域
仍为 `D`。

| 表面 | 当前契约 | 兼容性 |
| --- | --- | --- |
| 根 should-run / Turn Envelope `effective_action` | 决策/前沿并集 `A` | 保留前沿判决的语义与拼写 |
| 嵌套 `agent_scope_frontier_v1.action` | 前沿域 `F`，只输出一个动作字段 | 读者优先读 `action`，保留旧 v0 别名兜底 |
| 内部 journal replay observation | 使用既有 `decision=replay_legal\|replay_blocked` | 公共 inspection 与落盘 journal 形状不变 |
| Turn-result Effect observation | Turn 判决放在 `decision`，`effective_action=null` | 有意的投射变化：通过 `decision` 读取判决，host action 字段不能生成 quota 决策 |
| 动作选择拒绝或延迟 | `effective_action=quota_skip`，诊断放在 `error_code` | 有意的 CLI 变化：通过不变的诊断码区分原因 |

新 frontier 写入删除嵌套的冗余 `effective_action`，并将嵌套 schema 升为 v1。
历史已签名 v0 文档不在读取时归一化：envelope capsule 保留当时存在的两个旧
键，journal 恢复原样返回落盘 plan。兼容测试在迁移前刻画旧签名，再逐字段突变
验证签名覆盖，且使用真实文件 journal 写入者与恢复读者。新 v1 签名只因声明的
嵌套 schema/字段缩减发生变化。该别名没有前端配置 owner；真实 quota CLI 和
Markdown 展示已纳入测试。

瞬时 `effect.interpret_turn_result` 投射以前把任意 host action 或 `result_kind`
复制到 quota 动作字段，现在输出 JSON null，由 Python 适配为 `None`。
TypeScript 返回类型将该动作固定为 null；quota observation 仍保留原有字符串
动作。executor 读取结果的 `decision`，持久化规范化后的 host result 和 plan，
不持久化这份瞬时 observation。真实 host 校验仍拒绝不支持的 action 字段；
executor/journal 重放测试验证了不变的无消费 wait 路径。该投射变化不迁移落盘
result、receipt 或 journal 的 schema 版本。

字面量守卫使用 Python AST 与 TypeScript 编译器解析器识别有界的字段写入、
比较、成员测试和 match/switch。即便值已注册，裸动作字面量也会失败，必须导入
owner。条件表达式、相邻的其他字段、注释和字符串中的源码样例不计作动作值。
这是句法边界，不是全程序数据流证明；动态键、别名与未解析表达式仍受明确的
能力边界限制。绑定/术语表的新鲜度检查复用现有 PR pytest 与 smoke，不新增
required CI job。

### 形式模型与证明边界

注册表是更大程序语义的有限规格。令 `V` 为已注册词表集合，`L` 为源码位点集合，
`U(v)` 为词表 `v` 的环境运行时值空间，`S(v)` 为注册允许集合。生产与消费先在
`U(v)` 上定义，再验证是否属于允许集合。模型记录的是关系，而不只是名称：

```text
D ⊆ L × V                         定义词表
P ⊆ L × V × U(v)                  生产值
C ⊆ L × V × U(v)                  消费或据值分支
I ⊆ L × V × V                     将一个词表解释为另一个词表
T ⊆ L × V                         不改变含义地透传
G ⊆ V × V × (S(v_source) ⇀ S(v_target) ∪ {reject}) 做投影
R ⊆ L × V × Version               将值持久化
```

每条义务都按它实际被检查的值域陈述，而不是泛指 `V`。`Kernel(V) ⊆ V` 是
`tier: kernel` 子集，也是唯一声明了 producers 的层；`Produced_scan(v)` 是固定形式
在代码所有的扫描范围内观察到的生产；`ScopeDeclarations` 是注册表声明为有界上下文
的那些分叉名字。

1. **生产闭包（仅 kernel 层）：** `∀v ∈ Kernel(V): Produced_scan(v) ⊆ S(v) ⊆ U(v)`。
   被识别的生产者不能写入注册集合之外的值。扫描范围之外的生产，以及整个
   `cross_runtime` 层，是未验证，而不是已证明闭合。
2. **规范值存活（仅 kernel 层）：** `∀v ∈ Kernel(V): Canonical(v) ⊆ Produced_scan(v) ∪
   CompatibilityOnly(v)`。只被比较、没有生产来源的值是死值或兼容值，不能是
   canonical。`cross_runtime` 层不声明 producers，因此该层的存活性未被验证。
3. **消费者定义域闭包：** `Accepted(c) ⊆ S(v)`，除非消费者显式声明外部定义域或部分定义域。
4. **作用域枚举完备性：** `∀n ∈ ScopeDeclarations`，声明的上下文 owner 模块集合
   恰好等于定义 `n` 的模块集合，每个模块一个上下文，且每个上下文的 owner 符号
   都是 `n`。作用域是声明的、从不推断，所以“只有声明作用域相交时同名冲突才是
   语义冲突”是语义冲突的*定义*，不可能被违反；可检查的义务是一份声明必须枚举
   全部定义模块。拼写本身仍然不能证明等价。
5. **投影全性：** 每个源值都必须映射到目标值，或显式映射为 `reject`。
6. **持久化兼容性：** 持久化词表改变时，必须保持所有读者可读，或声明带版本的迁移。

每条义务在 `formal_model.invariants[].domain` 中记录它量化的集合，smoke 从注册表
推导两个规模数字，而不是相信声明值：

| 义务 | 量化范围 | 已验证 / 已注册 | 证据边界 | 实施阶段 | 今天是否阻断 |
| --- | --- | --- | --- | --- | --- |
| F1、F2 | `vocabularies[tier=kernel].producers` | 6 / 26 | producer 扫描范围 | `m0_5` | 是，在这 6 个之内 |
| F3 | `vocabularies[*]` | 0 / 26 | 仅清单证据 | `advisory` | 否 |
| F4 | `scope_declarations[*].contexts` | 4 / 4 | 声明的定义模块 | `m0_5` | 是 |
| F5 | `projections[*]` | 1 / 1 | 可执行 owner 函数 | `m0` | 是 |
| F6 | `persists_edges[*]` | 0 / 0 | 未建模 | `unproved` | 否 |

`verified` 是该实施阶段真正走到的子值域，`registered` 是同一单位的全体总数。
建议性（advisory）与未证明（unproved）阶段什么都不走，因此 `verified` 必须为 0；
已强制阶段则不得声明空值域。每条义务的 selector 与证据边界由 smoke 里的
`FORMAL_DOMAIN_ANCHOR` 按 `COVERAGE_ANCHOR` 同一模式钉住（I5），因此不可能只改数据
就扒宽一条不变量所声称的范围。F5 的 `verified` 取的是 smoke 真正导入并执行的投影
条数，来自代码里的 `EXECUTED_PROJECTIONS`，而不是注册表自身的行数；已注册但没有
对应执行检查的投影只抬高 `registered`，不抬高 `verified`，与 F1 报 6/26 同理。producer 扫描范围本身每次运行现算而不钉住，
因为分母会随任何新模块移动；smoke 会打印当前比值、未解析位点总数，以及其中
再宽的扫描也永远无法解析的那一部分（E21）。

#### 同一条义务行的四种读法

`formal_model.invariants[]` 的一行有四种读法。本 RFC 分别陈述每一种，因为把它们
混在一起，正是被校验过的元数据变成“已执行的证明”的方式：

| 读法 | 它存在于哪里 | 它能说什么、不能说什么 |
| --- | --- | --- |
| Schema 校验 | 漂移 smoke 里的 `check_formal_model` | 该块具有精确的键集合、五个角色、consumer 层级、七种关系边、F1 到 F6 各恰好陈述一次且 statement 与证据边界非空、层级与阶段一致，以及两个规模由 smoke 从注册表重新推导的 domain。它说明这条声明是*格式良好*的。它从不对声明本身求值 |
| 实施阶段 | `invariants[].enforcement` 与层级名字 | 哪个里程碑拥有这项检查：`m0`、`m0_5`、`advisory`、`unproved`。阶段是交付计划里的位置，不是结果 |
| 证据状态 | `invariants[].evidence`、`invariants[].domain` 与 `proof_boundary` | 检查依托什么，以及它走过总体的多少：具名 `evidence_bound` 下的 `verified / registered`，分类为 `established`、`bounded`、`unknown` 或 `unproved`。子值域上的有界证据不是全体上的证明 |
| 阻断行为 | 违例是否让 `examples/semantic-vocabulary-drift-smoke.py` 以非零退出 | 唯一回答“这会不会拦住合并”的读法。它是 smoke `main()` 里那些调用的性质，而不是注册表任何字段的性质 |

这四者并不同步移动，当前源码树本身就是证据。F1、F2、F4 的实施阶段是 `m0_5`、位于
`blocking_next` 层级，却在今天就会阻断合并——在 kernel 层与已声明作用域之上。F3 通过
schema 校验、带有证据字符串，却什么都不走。F6 通过 schema 校验，而根本没有检查。
因此一行通过校验只确立一件事：这条声明格式良好。从这次校验里读出“已完成的证明”、
“已交付的检查”或“合并阻断项”，正是本小节要防止的失效模式；
`tests/architecture/test_semantic_formal_model.py` 里的回归把这个区分钉在代码里。

这些是不同的证明义务。M0 已建立 owner 集合相等、跨运行时 parity、声明的可执行投影
和基于当前已跟踪源码树计算的清单。固定字面量形式与闭集载体只提供有界证据，不是全程序证明。M0.5
增加有界的生产者和作用域检查。动态代码中的完整生产者发现、`same_concept` 的行为等价、
以及持久化读者兼容性，在建模源码到结果的边之前仍然是未证明状态。注册表通过
`formal_model` 保存这条证明边界；标记为 `unproved` 的性质是显式局限，不能被当作默认通过。


### 健全性、相对完备性与候选决策

这里的“完备”必须带范围。令 `U(v)` 为词表的运行时完整值域，`S(v)` 为注册表允许
的值集合，`P(v)` 为实际产生的值集合，`O(v)` 为扫描器观察到的值集合。生产义务
只有在完整值域上定义时才有意义：

```text
P(v) ⊆ S(v) ⊆ U(v)
```

如果预先把 `P(v)` 定义成 `S(v)` 的子集，第一个包含关系就变成恒真命题。M0 当前
只对 `O(v)` 和已登记的结构载体建立有界结论。

对一个受限语法片段 `L0` 和精确分析器 `A0`，定义：

```text
Sound(A0, property, L0)    := A0 接受 c ⇒ property(c)
Complete(A0, property, L0) := property(c) ⇒ A0 接受 c
```

M0 守卫可以对固定载体和固定分发形式追求这两个性质，但不能对任意动态 Python 或
TypeScript 宣称它们成立。值如果经过别名、配置、反射、外部输入或未识别语法流动，
在有界分析覆盖它之前都属于 `unknown`。Unknown 是证据结果，不是“不存在”的证明。

建议性的候选分类使用一个有限决策：

```text
reuse_existing | extend_vocabulary | create_vocabulary | local_only
external_input | compatibility_only | unknown
```

这样可以让“流程分类”完备，即使程序分析本身不完备。`reuse_existing` 要求槽位相同、
作用域兼容、契约等价。`extend_vocabulary` 要求给出反例，证明复用旧值会把两个需要
不同处理的状态压成一个。`create_vocabulary` 要求出现新的语义定义域或独立 owner 与
生命周期。如果证据不足以在这些情况之间做决定，默认就是 `unknown`；Agent 不能把
未解析候选静默当成复用旧词。

任意程序的行为等价通常不可判定，因此这个 schema 不会把 `same_concept` 自动提升为
定理。只有当输入、输出、状态转换、持久化版本和有限测试域都明确时，行为等价才可
在受限契约内成为阻断条件。这就是可用的证明骨架与“全程序语义收敛已被证明”之间的
边界。

候选处置在此仅为建议性元数据。注册表只保存允许标签与默认值，不存储逐候选决策，
也不在产品代码中强制执行候选处理；漂移 smoke 只验证标签合同。

### 状态模型与 schema

`loopx/semantics/vocabulary_v0.json`，`schema_version` 为
`loopx_semantic_vocabulary_v0`。键集合是封闭的；未知的顶层键或词表键让 smoke
失败。

| 键 | 内容 | 检查 |
| --- | --- | --- |
| `coverage_floor` | 词表、owner 符号、字面量扫描字段、投影、关系、schema 版本的数量；扫描后缀集合 | 实际计数不低于下限，声明的后缀覆盖下限集合，且每个下限必须等于其 `COVERAGE_ANCHOR`（I8） |
| `vocabularies.<name>.owners` | `python` 与 `typescript`，各为 `path::Symbol` 或 `null` | 枚举成员、闭集成员、`Literal` 别名或 `as const` 数组等于 `values`；该符号只在 owner 模块中定义（I1、I2、I3） |
| `vocabularies.<name>.tier`、`status` | `kernel`、`cross_runtime`、`cross_module`；`canonical`、`legacy`、`merge_candidate` | 封闭枚举 |
| `vocabularies.<name>.literal_scan` | `field`、根目录、后缀 | 固定分发形式捕获的每个字面量都已注册；每个注册值被捕获或来自变量（I2） |
| `vocabularies.<name>.variable_sourced_values` | 值到生产者模块 | 生产者仍包含带引号的该值 |
| `scope_declarations.<name>`（M0.5a） | `bounded_context` 及上下文 ID，每个上下文含一个 `module::Symbol` owner | 每个声明名对应一个 inventory 分叉，并且一次且仅一次列出全部定义模块；只从 `multi_value_forks_semantic` 排除，未声明分叉仍可见（I14） |
| `vocabularies.<name>.input_producer` | 固定的可执行解码入口，目前仅用于 `turn_result_kind` | 每个注册输入必须产生匹配的类型化成员，非法探测输入必须拒绝；禁止任意选择执行入口 |
| `vocabularies.<name>.producers`（M0.5） | 写入该字段的 `path::Symbol` 位点，`kernel` 必填 | 每个位点只写注册值；未列入 `compatibility_only` 的每个值至少有一个源码生产位点或可执行输入见证（I12、I13） |
| `vocabularies.<name>.compatibility_only`（M0.5） | 为持久化读者或旧类型化调用接口保留的值 | `values` 的子集；零生产位点；每个值带 `value_notes` 理由与退休里程碑 |
| `formal_model` | 有限的集合、角色关系与层次、语义义务、候选决策，以及已建立/有界/unknown/未证明的声明 | 仅 schema 校验。漂移 smoke 校验精确键集合、角色层次、候选决策，以及 F1 到 F6 各恰好陈述一次且带非空 statement、证据边界与可推导 domain；`tests/architecture/test_semantic_formal_model.py` 对以上每条规则做突变。通过校验的块是格式良好的声明，绝不是已执行的证明；决定是否阻断合并的是 smoke `main()` 里的代码，而不是这个字段（第 5 节“同一条义务行的四种读法”） |
| `formal_model.invariants[].domain` | 义务量化的集合：`quantifies_over` selector、`verified` 与 `registered` 规模、`evidence_bound` | selector 与证据边界都是代码所有的名字，并由 `FORMAL_DOMAIN_ANCHOR` 逐不变量钉住；两个规模都从注册表推导并必须与声明值相等；advisory 与 unproved 阶段必须声明 `verified: 0`，已强制阶段不得声明空值域 |
| `formal_model.enforcement_policy` | 当前阻断、下一阶段阻断、建议性和未证明层级 | 每个形式不变量恰好出现在一个层级中，且层级与其 `enforcement` 实施阶段一致。层级记录的是拥有这项检查的实施阶段，而不是违例今天是否阻断合并；两者在第 11 节分列 |
| `vocabularies.<name>.value_notes`、`deprecated_values` | 逐值评审备注；计划删除的值 | 名字必须是已注册值 |
| `relations.same_concept` | `vocabulary.value` 成员组 | 每个成员可解析 |
| `relations.shared_field_names` | 一个字段名、其槽位及各槽位承载的词表或值 | 每个槽位可解析 |
| `relations.subsets` | 超集词表、排除值、子集符号的 owner | owner 符号等于超集减排除值 |
| `projections.<name>.mapping` | 源值到目标值或 `null` | 键等于源词表；映射值与 owner 函数一致；`null` 路由抛出（I4） |
| `schema_versions.<name>` | 常量名、值、owner 模块 | 唯一的定义模块就是列出的 owner 且都携带该值（I1） |
| `retirement_ledger.<group>.fields` | 每字段在两种指标下的 Python 与 TypeScript 预算：`*_module_budget` 统计携带该字段 token 的模块，`*_migration_surface` 统计真正读、写或以形参/局部名承载它的模块 | 两个实测值都不超过各自预算；字段集合与每个 token 预算与 `RETIREMENT_ANCHOR` 一致，每个迁移面预算与 `MIGRATION_SURFACE_ANCHOR` 一致，且五种句法角色恰好划分 token 计数（I5） |
| `dual_runtime_twins` | 根目录与模块预算 | 同名 `.py`/`.ts` 对数不超过预算（I5） |
| `inventory_ratchets` | 同运行时分叉的名字数与定义数、冲突的名字数与定义数、schema 版本分叉数、多值孪生与分叉数，以及共享词表冲突与分叉子集的预算 | 清单摘要计数不超过预算，且每个预算必须等于其 `BUDGET_ANCHOR` 条目（I5、I9） |

清单保留 `schema_version=loopx_semantic_inventory_v0`。守卫每次从完整的
已跟踪 `loopx/` 源码树计算一次，在 owner、scope 与预算检查中复用，不读报告文件。
`scripts/generate_semantic_inventory.py` 可按需导出同一份结构地图，
报告不入库；它每行一条地列出 Python 枚举、闭集、`Literal` 别名、
TypeScript `as const` 数组，以及拆为跨运行时孪生、同运行时分叉、冲突值、多值
孪生与多值分叉四类的重复定义。每个多值冲突都带上全部定义模块及其值集，因此
可评审的是分叉本身而不只是计数。消费者计数与合并候选组都由 `--report` 打印，
`merge_candidate_groups` 返回这些组，所有清单输出均不提交；合并候选是建议性的，因为值集
相同并不能证明是同一个概念。打印的列表会剔除那些名字恰好等于某个已注册词表自身
owner 符号集合的组：`EffectiveAction` 与 `EFFECTIVE_ACTIONS` 是同一个已注册概念在两个
运行时的两种拼法，不是两个待合并的概念。不传注册表调用 `merge_candidate_groups`
仍可得到未过滤列表。被剔除的对已有定论，因此剔除既不退休任何东西也不做任何分类；
留下的每一组都带上名字、值、模块，以及模块是否横跨两个运行时——后者正是注册表
缺口的形状。单模块的字符串常量只计数，不列出。

值是只增的。删除一个值、字段、owner 或关系属于 schema 缩减，遵循 `AGENTS.md`
规则：枚举受影响表面、调研生产者与读者、在同一 diff 中调低下限、记录维护者
批准。

### 命令或事件生命周期

检查只有一个命令：运行 smoke。它幂等且无副作用。失败文本命名词表、违规文件与
值，修复是机械的：注册该值、import 该常量，或收窄改动范围。

### Provider 或扩展契约

新词表通过一个 PR 加入：新增注册表条目、提高覆盖下限，若存在 TypeScript owner
则指名其 `as const` 数组。当一个词表被多个模块分发或跨越 Python/TypeScript
边界时，即有资格进入策展层；其余由清单映射而不策展。新增载体会在下次全树扫描时自动发现，
真正的共享契约变化仍需评审。

## 6. 备选方案与设计选择

| 备选 | 为何现在不选 |
| --- | --- |
| 一个 PR 把三套 Turn 枚举合一 | 违背 I5 式的渐进；三套枚举有不同 owner 与变化原因（settlement、route、controller）。先注册并投影，只在投影证明同一后再合并（第 12 节 Q2）。 |
| 依赖 `mypy` 的 `Literal` 类型 | 覆盖不到 TypeScript、JSON 载荷与 CLI；而漂移恰恰发生在这些边界。 |
| 仅靠文档术语表 | 不能让构建失败；仓库已有十一份自称 mental model 的文档且没有术语表，这本身就是症状。 |
| 立即把注册表作为运行时绑定权威 | 已实现的 M1 生成器从代码 owner 派生动作绑定，M2 从专属共享契约派生 Turn 绑定。注册表核对这些权威，而不替代它们。 |
| CI 里不带注册表的 grep 式 lint | 把允许集合编码进 linter，变成没有评审痕迹的第二份注册表。 |
| 扩展 `maintainability_ratchet.py` 而不新建注册表 | 它的对象是模块指标与依赖方向，按模块设上限；词表形状需要值、owner 与关系。两者共享棘轮思想而非数据模型。例外生命周期是否合并见 Q7。 |
| 把扫描正则放进注册表 | 数据里的正则可以在扩宽词表的同一次修改中被收窄；M0 评审表明第一版模式漏掉了全部 TypeScript `===` 分发点。形式固定在 smoke 里，后缀集合设下限。 |
| 提交计算清单或消费者计数 | 结构变化会产生没有新增语义权威的合并冲突。全树计算并按需导出报告；消费者计数保持为参考信息。 |

## 7. 安全、隐私与兼容

- M0 没有任何运行时路径导入注册表；检查存在与否，产品行为不变。
- 扫描器使用 `git ls-files --cached -z` 枚举索引中的源文件路径，再读取工作树内容。
  未跟踪与忽略文件不进入清单；新增源文件需先暂存路径，再运行全树扫描。已跟踪
  的符号链接与无法解析的 Python 源码使检查失败；运行时需要带 Git 元数据的检出。
- 字面量及 TypeScript 载体扫描同时识别单引号与双引号。它们仍是结构性文本
  扫描，不是完整解析器，也不做数据流分析。
- 字面量扫描根目录与后缀、孪生模块根目录与预算均有代码锚点；仅修改 JSON
  不能缩窄扫描范围或提高孪生预算。
- 失败文本只使用仓库相对路径与已注册标识符。
- 旧的读写方不受影响。预算冻结其当前分布，不删除任何一处引用。
- 构建期检查不涉及混合版本。M2 引入生成绑定时，生成器的 `--check` 模式与
  smoke 同时运行，过期的生成文件无法合入。

## 8. 迁移与回滚

- **准入。** M0 落地时注册表与清单和基线完全一致，外加两处保持行为的修改以让
  owner 检查通过：`driver.py` 中重复的 `TURN_ENVELOPE_SCHEMA_VERSION` 改为
  import，authority e2e 夹具中的 `HANDOFF_MODES` 元组改为从 `HandoffMode` 枚举
  派生。
- **回滚。** 删除 smoke、`loopx/semantics/` 包、生成器、其测试与 `pyproject.toml`
  的那一行即恢复原状，无运行时影响。后续里程碑在第 11 节各带回滚。
- **不可回退点。** M0 没有。M3 的字段删除是第一个不可逆步骤，逐个门控。

## 9. 验证与验收

| 声明 | 测试或证据 | 要求结果 | 边界 / 排除 |
| --- | --- | --- | --- |
| 基线上注册表与清单和代码一致 | `uv run --extra test loopx canary smoke-suite --script semantic-vocabulary-drift-smoke.py` | `ok` 并输出覆盖、棘轮、预算与孪生报告 | 只证明已注册词表与已映射载体的一致性 |
| 清单按需计算 | `uv run python scripts/generate_semantic_inventory.py` | stdout 输出合法 JSON，不写仓库 | 完整已跟踪源码树，不仅是 PR diff |
| 扫描器分类规则 | `uv run --extra test python -m pytest tests/architecture/test_semantic_inventory.py` | 通过 | 夹具仓库；规则来自本 RFC 而非输出 |
| Python 侧扩宽 `effective_action` 时失败关闭 | 通过 `==`、成员测试或条件表达式加一个未注册字面量 | 失败文本命名该值与文件 | 突变练习；非提交测试 |
| TypeScript 侧扩宽 `effective_action` 时失败关闭 | 通过 `===` 或三元表达式加一个未注册字面量 | 同上 | 同上 |
| 分叉常量时失败关闭 | 在非 owner 模块重定义 `TURN_ENVELOPE_SCHEMA_VERSION` 或 `HANDOFF_MODES`，运行 smoke | 失败列出多出的定义模块或分叉预算 | 同上 |
| Python 与 TypeScript owner 不能分叉 | 从已注册 `as const` 数组删一项，或扩宽已注册枚举 | 失败命名缺失或未注册的值 | 同上 |
| 注册表不能仅靠改数据被削弱 | 声明裸模块 owner；删掉一个 owner；把后缀收窄为 `.py`；重命名一个被关系引用的词表；加一个未知键 | 每项都失败并点名规则 | 同上 |
| 新载体可见 | 新增已跟踪枚举，不导出报告 | 当前扫描能看到它，不因报告新鲜度失败 | 变更与未变更文件之间的新分叉仍受预算约束 |
| 冲突拼法不能增长 | 为已冲突名字加第三种值，重新生成 | 失败命名定义数预算 | 同上 |
| 多值冲突不能增长 | 让一个闭集名在两个模块中以不同值集定义，或以相同值集定义，并重新生成 | `multi_value_forks` 或 `multi_value_twins` 失败并命名新名字 | 突变练习；非提交测试 |
| 注册表不能放松自己的棘轮 | 在同一 diff 中调低任一 `coverage_floor` 计数、调高任一 `inventory_ratchets` 预算或退休预算，同时删掉它所统计的覆盖 | `COVERAGE_ANCHOR`、`BUDGET_ANCHOR` 或 `RETIREMENT_ANCHOR` 失败并命名被锚定的值 | 突变练习；挪动锚点是一次评审者可见的代码修改 |
| 已收紧的预算不能漂回过期锚点 | 只调低注册表预算而不动锚点 | 失败文本指出注册表值与锚点不等 | 用相等而非 `<=`；修法是同 diff 调低锚点 |
| smoke 在 PR 路径上 | `uv run --extra test python -m pytest tests/architecture/test_semantic_vocabulary_drift.py` | 通过；该测试被 `python-tests.yml` 的默认 `pytest -q` 扫描收集 | 舰队与 premerge 表面不是义务（I10） |
| premerge 会为 `loopx/` 的 diff 选中该 smoke | `uv run --extra test loopx canary premerge --changed-file loopx/control_plane/turn_driver/loop_controller.py` | 计划在 `repo-architecture-budget` 下列出 `examples/semantic-vocabulary-drift-smoke.py` | 选择靠触发词；pytest 包装才是保证 |
| 度量覆盖两种载体形状并过滤局部命名 | `uv run --extra test python -m pytest tests/architecture/test_semantic_inventory.py` | 通过，含冲突与模块局部约定两组夹具 | 规则来自本 RFC 而非扫描输出 |
| 两处 owner 修正不改变行为 | `uv run --extra test python -m pytest tests/test_loopx_turn_transaction.py tests/test_loop_turn_loop_controller.py tests/test_turn_loop_disposition.py tests/test_loopx_turn_managed_step.py tests/control_plane -k authority` 与 `uv run --extra test loopx canary premerge --from-git-diff` | 通过 | 在干净树上可复现的 `main` 既有环境失败除外 |
| 文档治理接受这对 RFC | `python3 examples/docs-governance-smoke.py` | 通过 | 检查镜像、链接、索引 |
| 对声明了槽位的词表按位点报告消费者角色，并写明未知量（B5，可选项） | `uv run python scripts/generate_semantic_inventory.py --report --consumer-evidence` 与 `uv run --extra test python -m pytest tests/architecture/test_semantic_consumer_report.py` | 报告先写明自己的覆盖面以及它拒绝分析的词表，再打印 read/interpret/pass-through/unknown 计数、两个未知占比，以及每种未知原因及其位点数；测试通过 | 仅为参考，且在 #4447 中属可选项：在两个根目录的扫描范围内度量语法使用，既不是数据流，也永远不是闸门。覆盖面是 26 个已注册词表中声明了 `literal_scan.field` 的那 1 个；其余 25 个作为 `missing_slot_identity` 上报，不做分析 |
| 退休预算按子串而非标识符计数 | 分别以 `in file.text` 与 `\bgoal_boundary\b` 统计 `goal_boundary` | 基线上 35 对 30 个 Python 模块 | 已知边界；M3 的零读者门需要标识符计数，见第 12 节 |
| 退休指标把读者与提及分开（B3） | `check_reader_metric()` 为携带六个字段 token 的每个模块记录一切成立的事实——读、写、承载、未定、仅提及——并在旁边保留单标签的角色划分 | `goal_boundary`：30 个 token 模块解析为 8 读、7 写、9 承载、1 未定、14 仅提及，迁移面是 16 而非 30；`work_lane_contract` 是 29 中的 29 | 度量的是句法使用，不是数据流。事实相互重叠，因此断言它们**覆盖** token 样本；角色划分断言相加等于它，且只用于排序与打印 |
| 旧字段新增读者会在 PR 路径上失败 | 让一个模块读 `payload["protocol_action_packet"]` 从而超出预算 | `check_reader_metric` 失败并点名该字段与计数 | `tests/architecture/test_semantic_vocabulary_drift.py` 内的提交测试；锚点等值检查与 `RETIREMENT_ANCHOR` 同一套模式 |
| 计算式键保持「未定」而非「不存在」 | 统计首参数不是字面量的 Python mapping 访问器，以及 TypeScript 计算式成员访问 | `loopx/` 下 Python 1711 处、TypeScript 400 处；某字段读者计为零时，是对着这个公开的未知数计零 | 这正是零读者本身不能授权删除的原因（Q11）。它不可归属到任何单个字段，因此与字段专属的未知不同，永远不进入任何迁移面。Python 计算式下标不计入：`rows[index]` 与 `payload[key]` 是同一种语法 |
| 模块局部约定过滤器是一次代码修改 | 扩宽 `inventory.py` 的 `MODULE_LOCAL_CONVENTION` 并重新生成 | `*_semantic` 预算下降而别处无代码改动 | 已知边界；正则在代码里，扩宽是可评审的 diff，未过滤总数仍在预算内 |
| 无人生产的注册值失败（M0.5） | 在基线上运行生产形式扫描 | 失败并点名 `effective_action` 与 `skip`；删除 `skip` 或列入 `compatibility_only` 后通过 | 第一个预期的 I12 失败；只被比较的值不算已携带 |
| 生产未注册值失败（M0.5） | 在某个已列生产位点写 `effective_action: "brand_new"` | 即使无消费者比较它也失败，并点名位点与值 | I13；生产比比较更严 |
| 有界上下文名字只能靠声明离开语义分叉预算（M0.5a） | 为 `SOURCE_SURFACES` 声明四个上下文；另行只改名其中一处定义而不声明 | 原始 `multi_value_forks` 保持 4，`multi_value_forks_semantic` 为 3；单独改名既不改变语义计数，也不构成声明 | I14；诚实的修法是评审者看得见的注册表修改，改名不是修复 |

| 历史上的已提交清单会因上游合并而过期 | 对 `upstream/main` 最近二十个合并提交，在第一父提交与合并结果之间重放扫描器 | 20 次合并中 8 次至少改变一个载体 | Q9 的历史动机；当前检查直接计算合并后的全树，不再依赖提交快照 |
| 形式模型不能静默丢失证明义务 | 从 `formal_model` 删除不变量、角色、候选决策、关系或证明边界分类 | 漂移 smoke 针对形式模型结构失败 | 该模型是有限契约和证明账本，本身不等于这些性质已经被证明 |
| 义务不能声称一个无人清点的值域 | `uv run --extra test python -m pytest tests/architecture/test_semantic_vocabulary_drift.py -k domain` | 删掉 `domain`、调大 `verified` 或 `registered`、自造 selector、使用未钉住的 selector 或跨阶段的证据边界、以及 advisory 不变量声称已验证成员，逐项失败关闭 | 规模从注册表推导，因此该检查把声明值域接地到注册表数据；它不证明该义务在那个值域上成立 |
| 有界绑定形式不能被放宽成假证据 | `uv run --extra test python -m pytest tests/architecture/test_semantic_producer_binding.py` | 通过；每条被识别的形式都有反例孪生——被装饰的、`async`、生成器、被重绑定的、导入的或递归的被调方，无序 store，被别名或逃逸的容器，以及未知 `**` 展开，都让该位点保持未解析 | 夹具仓库；本扫描无法绑定的位点保持未解析并带上被记录的原因，绝不当作 dead |
| F1/F2 恰好量化 producer 检查真正走到的集合 | 同一测试模块：将 `check_producers` 的谓词与 F1/F2 声明的值域对比 | 声明了 `producers` 的词表恰好是 `kernel` 层，26 中的 6；其余 20 个全部是 `cross_runtime` | 扫描范围进一步约束该声明，它被上报而不被钉住 |

已知边界，写明是为了不让这个检查被过度信任：

- **改名可以洗白冲突。** 冲突按名字归组，因此把分叉的一侧改名会降低计数而
  不消除漂移。这里的评审辅助是建议性合并报告；值集相同不能做成硬预算，因为
  `CONFIDENCE_LEVELS` 与 `EDGE_CASE_COMPLEXITIES` 共享 `high/low/medium` 却
  含义不同。合并候选报告本身并不覆盖这一边界：它把**不同**名字、**值集完全相同**
  的项归为一组，而分叉是**同一个**名字下的模块互相分歧，所以那个分组永远不会列出
  任何分叉。`divergent_value_sets(inventory)` 是按名字归组的补充报告，由
  `--report` 打印，列出**仍然存在**的分叉及其分歧值集数量——此前它们只是一串数字。
  它**不是**改名检测器：实测表明，单侧改名后该名字只剩一份定义，因而不再见得分叉，
  会同时退出预算与这份报告。唯一会失败关闭的情形是**已声明**的名字，因为
  `scope_declarations` 指明了每个定义模块，被改名的一侧不再匹配。未声明的单侧改名、
  以及把所有一侧同时改名，都会让预算下降且没有任何报告会说。这一残余与本节其余
  条目一样，在 M0 被接受。
- **单元素载体不可见。** 只有一个字符串成员的闭集不构成词表，因此把一个两值
  集合降为一个值会让它完全退出清单。
- **字面量扫描可能误读同一行上无关的比较。** 形如
  `log("effective_action", kind === "repair_required")` 会被捕获为
  `effective_action` 的值。为了让失败消失而登记被报告的值会扩宽词表，正确做法
  是同时登记字段名与字面量，或改写该行；失败文本会给出文件，评审时可见。
- **锚点是代码而非历史。** PR 仍可挪动锚点，但必须修改一个具名字面量，就在
  注册表改动的旁边。因为检查是相等性，锚点不可能过期，但它也不记住曾达到的
  最低值；那段历史在 git log 里。

## 10. 运维契约

标准 premerge 的 catalog 检查上限从 9 提高到 10，避免新增词表检查挤掉原有
heartbeat/quota 覆盖。quick 与 deep 档位的上限不变。

该检查不可能影响运行中的系统：它只在测试、premerge 与 CI 中执行。其操作者
界面就是失败文本。不适用可观测性、容量或值班契约。

它在哪里运行，以及哪个表面是义务：

| 表面 | 触发 | 选择 | 角色 |
| --- | --- | --- | --- |
| `pytest` 扫描，`python-tests.yml` | 每个分类为需运行 Python 测试的 PR | 经 `tests/architecture/test_semantic_vocabulary_drift.py` 始终被收集 | **提交时义务（I10）** |
| `loopx canary premerge` | 本地，开 PR 之前 | `repo-architecture-budget` profile，触发词含 `loopx/`、`examples/`、`scripts/`、`refactor` | 早期本地信号 |
| 全量公共 smoke 舰队 | push 到 `main`、每日日程、手动触发 | `examples/**/*-smoke.py` 发现 | 合并后确认；按设计不是 PR 必需检查 |

在这张表存在之前，RFC 说 smoke "在 premerge 与 CI 中运行"。在基线上这只在合并
后成立：premerge 对只改 `loopx/control_plane/` 的 diff 不会选中该 smoke，而舰队
工作流被刻意设为非 PR 必需检查。舰队能发现的 smoke 不是提交时检查，除非某个
必需的 PR 作业收集它。

**按需清单（Q9）。** 原来的已提交快照为本来合法的 PR 增加了额外同步义务，
现予以取消。设 `f(T)` 为完整已跟踪源码树的清单，`G(f(T), R)` 为既有注册表、
owner、scope 与预算谓词，检查仍执行 `G(f(T), R)`，只去掉附加条件
`I_committed = f(T)`。所有相关守卫复用本次扫描结果，缺失或过期的本地报告
不能掩盖新分叉。这不证明独立合法的分支合并后不会产生语义冲突；仍须验证
合并后的源码树。不得用只扫描 PR diff 代替全树扫描。

查看清单用 `uv run python scripts/generate_semantic_inventory.py`，默认向
stdout 输出 JSON；追加 `--output .local/semantic-inventory.json` 可导出报告。
`--output <path> --check` 只核对指定报告，不修改它；单独 `--check` 会给出
迁移提示。报告可以作为 CI artifact，但不入库，也不是运行守卫的前置条件。
绑定与术语表继续提交并检查新鲜度；本决策只移除仓库结构清单的提交义务，
不增加 CI 作业。

**解释器与源码。** 在目标 worktree 根目录通过 `uv run` 执行上面的命令。
Python 兼容范围来自 `pyproject.toml`（`>=3.11`），导入的 LoopX 必须来自当前源码。
Canary 将显示为 `python3` 的命令转换为启动 LoopX 的 `sys.executable`；全局安装
即使 Python 版本兼容，也可能扫描另一份发布快照。安装、解释器／源码读回及锁文件
边界见[本地验证环境](../../development/testing-and-quality.md#local-validation-environment--本地验证环境)。
下方历史证据保留实际执行过的命令。

已有完整环境时，也可以用 `bash scripts/loopx-python.sh --exec <Python 参数>`
自动选择已安装的兼容解释器，包括 `.venv/bin/python`，或通过 `LOOPX_PYTHON`
指定。该选择器不会安装 Python 和依赖。舰队与 premerge 的子命令仍可使用
`python3`，由选定的项目或 CI 环境提供 `PATH`。

TypeScript effective-action/frontier 绑定与[术语表](../../reference/glossary.md)通过
`uv run --extra test python scripts/generate_semantic_bindings.py` 生成。修改 Python owner
或注册表后运行该命令；载体变化会自动扫描，清单报告只按需导出。现有漂移 smoke 与 PR pytest
检查生成物新鲜度，不新增 required CI job。运行 TypeScript 生产者扫描之前，
先用 `npm ci --ignore-scripts` 安装锁定的 Node 依赖。

Turn 契约使用独立的生成器和来源。只读验证入口：

```bash
uv run --extra test python scripts/generate_semantic_bindings.py --check
uv run --extra test python scripts/generate_turn_contract.py --check
uv run --extra test python scripts/generate_semantic_inventory.py --report --top 10
uv run --extra test python scripts/generate_semantic_inventory.py --report --consumer-evidence --top 10
```

清单报告属于参考信息；`--consumer-evidence` 必须搭配 `--report`。阻断检查仍由
漂移 smoke 承担。导出报告不是必须提交的产物，报告成功不构成生产或持久兼容证明。

## 11. 规范性交付计划

`bfbb5ac60` 上的实现读回（[讨论 #4738 的 PR-03 事实校正](https://github.com/loopx-project/loopx/discussions/4738#discussioncomment-18514184)）：
M0/M0.5 检查已执行；Q3/Q6 已按第 12 节描述实现；M2 Turn 生成物已被
`transaction.py`、`settlement.ts` 和 loop controller 消费。下表保留各里程碑的
验收义务，不是“从未开始的工作”清单。历史测量保留原 SHA；本次读回不改变 Draft
状态，也不推断批准。

六个旧字段仍在写入。`protocol_action_packet` 退休仍需目标发布版本、消费者范围、
历史签名和回滚契约。[PR #4747](https://github.com/loopx-project/loopx/pull/4747)
中的 `settlement_binding_kind` witness 是提议中的 pilot，在此基线上尚未合入，
也不构成其他跨运行时词表的闭合。F6 仍未证明。PR-07/08 构建改动必须保持阶段
优先级；[PR #4764](https://github.com/loopx-project/loopx/pull/4764) 正在交叠路径上
修改同一 Turn 的延期选择和 monitor 结算，声称组合行为等价前须核对其最终结果。

| 里程碑 | 交付行为 | 进入门 | 退出证据 | 回滚 |
| --- | --- | --- | --- | --- |
| M0 | 含 26 个词表与 9 条关系的注册表、可选导出的计算清单、带固定分发形式与覆盖下限的漂移 smoke、删除两处 owner 分叉、RFC 索引条目 | 本 RFC 开启 | 第 9 节各行全绿；20 类突变失败关闭 | 删除 smoke、`loopx/semantics/`、生成器及其测试 |
| M0.5a | `scope_declarations` 的 `bounded_context` 与每上下文 owner；把语义分叉计数与原始清单计数分开 | M0 合入 | smoke 校验每个声明的上下文 owner；原始 `multi_value_forks` 仍为 4，`multi_value_forks_semantic` 为 3；未声明分叉仍受预算约束 | 删除作用域声明和语义分叉预算 |
| M0.5b | `kernel` 词表的 `producers` 与 `compatibility_only`；带两条角色检查（I12、I13）的生产形式扫描；退休预算改按标识符计数并在一个 diff 里调整六个锚点（Q11）；Q9 的合并序规则写入第 10 节 | M0.5a 完成；Q9 已决或其临时规则被接受 | smoke 在 I11 到 I14 强制下全绿；`skip` 已处理；第 9 节生产者行全绿；为 Q2 回答 `turn_route` 是否持久化 | 删除生产者字段和角色检查；预算回到 M0.5b 前的锚点 |
| M1 | 单一 owner 模块中的 `EffectiveAction` 类型化枚举；Q6 根部 decision/frontier 注册联合、独立的嵌套 frontier action 与 journal replay observation；生产者与消费者 import 它；注册表 `literal_scan` 收紧到枚举 | M0.5 合入；owner 模块已定（Q3）；槽位契约已记录（Q6） | smoke 绿；owner 之外零裸 `effective_action` 字面量；status/should-run 的 parity fixture 不变 | 回退为字面量；注册表保留集合 |
| M2 | route 到 disposition 的投影、`decide_loop_disposition` 决策表与跨运行时集合通过共享契约发布，生成 Python 与 TypeScript 绑定，效仿协调契约生成器 | M1 合入；Q2 与 Q7 已决 | 生成器 `--check` 与 smoke 绿；`settlement.ts` 与 `transaction.py` 读取生成集合 | 从上一版契约重新生成 |
| M3 | 逐字段退休旧 should-run 字段，每个 PR 一个字段，预算降到零并删除字段 | 逐模块清空该字段的迁移面，并评审残留的 unresolved 与计算式键证据；计数归零本身不构成这道门 | 按 `AGENTS.md` 的 schema 缩减记录；附录 B 条目 | 从最后一个写方恢复字段 |
| M4 | 随迁移 RFC 的每次 replacement-first 切换调低孪生预算 | 每个切换 PR | 同 diff 中的预算修改 | 无需；预算跟随代码 |

没有目标的棘轮只是方向，不是计划。下表是本 RFC 完成时的状态；每一行都是一个
注册表预算或 smoke 可检查的词表属性。标为*未决*的行等待第 12 节的决策，这也
是计划在那些决策记录之前只是骨架的原因。

| 表面 | 基线（`1dc6ad8d8`） | 由什么度量 | 本 RFC 关闭时的目标 | 由谁达成 |
| --- | --- | --- | --- | --- |
| `effective_action` 取值 | 33 个字面量，无 owner 符号 | 注册表 `vocabularies.effective_action.values`；`semantic-vocabulary-drift-smoke.py` 在出现未注册字面量时失败 | 一个枚举 owner；`skip`、`observe_replay`、`block_replay` 与两个 `quota_action_selection_*` 码从判定槽位移出；计入五个此前漏记的生产值并移除合成 operator_gate 后，共 32 个决策值 | M1 |
| 同一 envelope 里的 `effective_action` 槽位 | 一个字段名下 3 套词表 | 无计数器：拆槽是 Q6 的决策而非一个数字。读 `relations.shared_field_names` | 1，或在 Q6 保留字段时为一个已注册并集 | M1（Q6） |
| Turn 词表 | 3 套、28 值、21 个不同值、7 个冗余拼法 | 注册表 `vocabularies`；拼法重叠见 `relations.same_concept` | 保留 3 套；投影与决策表生成并校验；拼法不变，除非 Q10 决定合并 | M2（Q2、Q10 *未决*） |
| 同运行时分叉（语义） | 18 个名字 | `semantic-vocabulary-drift-smoke.py`：`same_runtime_forks_semantic` | 0 | 基线窄 PR |
| 冲突值（语义） | 2 个名字 | `semantic-vocabulary-drift-smoke.py`：`conflicting_values_semantic` | 0 | 基线窄 PR |
| 多值分叉 | 4（1 个误分类） | `semantic-vocabulary-drift-smoke.py`：`multi_value_forks` 与 `multi_value_forks_semantic`。今天只打印计数；#4614 增加 `divergent_value_sets` 以按名字列出存活的分叉 | `scope` 声明有界上下文名字后为 0 | M0.5 + 基线窄 PR |
| 多值孪生 | 19 | `semantic-vocabulary-drift-smoke.py`：`multi_value_twins` | 0 | 基线窄 PR |
| 旧 should-run 字段 | 6 个字段，124 py / 10 ts 模块提及 | token 计数：`semantic-vocabulary-drift-smoke.py` 每个字段一对 `<字段>.py` / `<字段>.ts`；迁移面与五种角色：`--report` 下每字段每运行时一行 `retirement_role:` | 0 个字段 | M3，以清空 B3 迁移面为门；token 计数在 Q11 决策前继续计入预算 |
| 合并候选组 | 32 组未评审 | `loopx/semantics/inventory.py` 的 `merge_candidate_groups()`；今天没有任何命令打印它，#4630 增加该 CLI 行。读可评审数而非原始数——注册的跨运行时词表本就同时拥有 Python 与 TypeScript 两个符号，这类配对是 I3 的要求而不是债务 | 每组已分类；只合并 `same_semantics` 的组 | 分类表 PR，随后逐组 PR |
| 控制面 py/ts 孪生 | 43 | `semantic-vocabulary-drift-smoke.py`：`independently_maintained` | 跟随 TypeScript 迁移 RFC；本 RFC 不设目标 | M4 |

*由什么度量* 列点明今天打印每个表面的命令与字段，与第 9 节为每条断言点明一个
测试的写法一致。它**刻意不携带数值**：誊抄来的数字在下一次合并时就过期，而想
知道当前状态的读者应当去跑那条命令，而不是相信一个日期。带日期的数值归交付
追踪（issue #4447，它拥有交付状态）；本表保持为「真值在哪里被度量」的契约。

合并候选要读**可评审数**而非原始数。原始分组会把任意两个携带相同值集的名字配
成一组，其中包含注册的跨运行时词表按 I3 **必须**同时拥有的 Python 与 TypeScript
两个符号。把它们当作债务是度量伪影，不是漂移。

### 两条执行轨道与强制层级

路线图把修复已有语义债务与完善度量工具分开。轨道 A 不等待设计决策：每个窄 PR
逐步删除真实分叉、冲突、孪生和旧读者。轨道 B 改善守卫能够知道的内容：作用域声明、
有界生产者分析、标识符计数和合并序处理。轨道 A 降低债务数量，轨道 B 让这个度量更
接近真实语义。M1 及之后的阶段依赖轨道 B，因为当前度量已知并不完备。

```text
轨道 A：修复现有债务 ──────────────────────────────────────┐
                                                               ├─> M1 类型化槽位
轨道 B：作用域 + 生产者模型 + 度量边界 ─────────────────────┘       │
                                                                      ├─> M2 生成式投影
                                                                      ├─> M3 旧字段退休
                                                                      └─> M4 运行时孪生迁移
```

形式模型使用四个强制层级，避免困难性质意外变成合并阻断：

| 层级 | 性质 | 实施阶段 | 今天是否阻断 PR |
| --- | --- | --- | --- |
| `blocking_now` | F5 投影全性 | `m0`，已交付 | 是。投影既不映射也不拒绝的源值会让 smoke 以非零退出 |
| `blocking_next` | F1 生产闭包、F2 规范值存活、F4 作用域分离 | `m0_5`，已针对 kernel 层与已声明作用域交付 | 是，在各自声明的值域之内。未注册的被生产值、没有观察到生产者的已注册 kernel 值、以及没有枚举全部定义模块的作用域声明，都会让 smoke 以非零退出。这些值域之外什么都不走，那是未验证，不是通过 |
| `advisory` | F3 消费者定义域闭包 | `advisory`，尚未写出分析 | 否。消费者与解释者的边只是清单输出，不可能在其上失败 |
| `unproved` | F6 持久化/版本兼容性 | `unproved`，尚未建模 | 否；同时也不能报告为已通过 |

`blocking_next` 一行原先写作*“M0.5 后计划强制；M0 不宣称已经做到”*。这在该层级被
命名时是对的，在 M0.5b 交付之后就是错的：`check_producers` 与
`check_scope_declarations` 都由漂移 smoke 的 `main()` 调用，而按 I10，该 smoke 在
每个运行 Python 测试的 PR 上失败即关闭。层级名字刻意保持不变——它记录的是哪个里程碑
拥有这项检查——阻断性的断言则移到了单独一列。层级是日程上的位置；只有 `main()` 里的
代码决定什么会拦住合并。

阶段完成条件是验收表中的证据，而不是出现一个公式或注册表条目。有界的源码到结果
分析存在之后，性质才可从 `unproved` 移到 `advisory`；只有记录误报/漏报边界并用突变
测试覆盖已识别形式后，才可移到阻断层。这样既严格防止静默破坏，也允许不完整的分析
为无关改动提供信息而不阻断它们。

PR review 保留这些层级。普通改动记录检查范围和理由，无共享契约影响就结束语义
审查。详细证据只针对受影响契约，可以引用已有审查证据。扫描器盲区仅作建议性
报告；本次修改影响的契约缺少必需验证，或存在明确违规，才以契约、触发修改、
观察证据、最小修复和复验命令阻断批准。F6 的全局证明缺口本身不阻断无关改动，
也不能用来豁免被修改契约要求的兼容性检查。可执行的结论结构见
[review 证据契约](../../../loopx/capabilities/pr_review_queue/README.md#semantic-alignment-and-ci-constraint-recovery)。
模型表现仍需实测：固定任务、模型与预算，比较 token、耗时、独立验收成功率、
误阻塞和漏检，之后才能声称带来收益。

阶段顺序如下：

1. **M0：** 保留当前结构守卫，并明确其证明边界。
2. **M0.5：** 为四个 Turn 内核词表实现 `scope`、生产形式和按标识符计算的退休预算。
3. **M1：** 保持已实现的 Q3 owner、Q6 根部注册联合、独立的嵌套 frontier action
   与 replay observation；这些表面变化时验证已有的版本化兼容契约。
4. **M2：** 维护已实现的 Turn 生成契约、有序 controller 规则与路由投影；
   其余跨运行时迁移逐词表评估，不能以 Turn 生成落地代替全部完成。
5. **M3/M4：** 只有在读者与迁移证据完整后，才退休旧字段并减少 Python/TypeScript 孪生。

这份路线图对依赖和退出证据具有规范效力。Issue #4447 可以承载 owner、建议日期和
运维清单，但不能另立一套目标状态。


## 12. 未决决策

本节保留原问题编号和链接锚点。Q3/Q6 描述已经实现的选择；其他提案保留原有
决策 owner 与批准要求。

1. **注册表位置。** Owner：内核维护者。M0 实现于 `loopx/semantics/`，因为范围是
   全仓库的，而 `loopx/control_plane/` 与 `docs/reference/` 都不是；该包只含两份
   JSON 与扫描器，没有任何产品代码导入它。在记入附录 B 之前这只是提案。M1 前
   需定。
2. **是否合并 `LoopXTurnRoute` 与 `LoopDisposition`？** Owner：Turn driver owner。
   投影覆盖全部输入但非单射（`blocked` 与 `wait` 都映到 `wait`），而 `stop`、
   `terminal`、`contract_error` 只在一侧存在。`same_concept` 关系记录了四个共享
   裁决。M2 的实现保留了两者并发布投影；是否合并需待 managed-step 消费者成熟后再议。
   持久化前提已有实现证据：`run_loopx_turn_once` 经 TypeScript journal writer
   写入完整的 `plan: dict(plan)`，其中包含 `plan.route.kind`；
   `load_loopx_turn_plan_from_journal` 会恢复这个 route。执行器的回放回归用例
   检查实际落盘的 journal 及恢复读者。三套词表仍然独立，生成的投影是非单射的；
   后续若改名，必须迁移持久化 plan，不能只做进程内枚举重构。此证据不等于全部
   外部读者或其他持久化字段的兼容性证明。
3. **`EffectiveAction` 的 owner 模块。** 实现选择 `quota/effective_action.py`，
   对应生成阶段之前的选项。运行时调用者通过 `.value` 保留现有字符串。
   TypeScript 消费者导入从该 Python 枚举生成的
   `quota/effective_action.generated.ts`，测试核对成员名与值的一致性。M2 可以
   从共享契约生成两种绑定，并保留现有 import 路径；不得在运行时模块另写一份
   独立维护的值表。
4. **伴随术语表。** `docs/reference/glossary.md` 从注册表的语义、owner、值及
   兼容性元数据生成，只覆盖已注册词表；清单仍是更广的结构地图。现有 smoke
   拒绝过期生成物。应修改 owner/注册表并重新生成，避免维护第二份文字权威。
   Owner：文档维护者。
5. **词族命名规则。** `gate`、`scope`、`packet`、`handoff`、`settlement` 词族中的
   新标识符是否必须在评审中引用术语表条目。这是评审规则而非 smoke；建议在
   术语表存在后纳入 first-review roster。
6. **动作槽位决策（Q6）。** 根 should-run/Turn Envelope 字段保留为代码锚点
   固定、互不相交的决策/前沿并集。嵌套 frontier v1 使用既有 `action`，journal
   replay 使用既有 `observation.decision`，不新增另一份冗余字段。读取历史 v0
   capsule 时保留已签名的旧字段，新写入使用版本化的缩减形状。见上文 M1
   兼容表与测试；这不授权其他旧字段退休或 Turn 结果枚举合并。
7. **与 `maintainability_ratchet.py` 的关系。** 清单棘轮是否采用它的评审化例外
   生命周期（`retirement_plan`、过期例外检测），还是保持为纯预算。建议：在 M2
   生成落地时采用，让有书面理由的分叉可以被例外而非被预算。Owner：canary
   维护者。
8. **从清单到注册表的晋升规则。** 外部消费者模块不少于三个或存在跨运行时孪生
   的已映射载体是否必须策展。建议：现在作为评审规则采用，待清单积累一个季度
   历史后再由 smoke 强制。Owner：内核维护者。
9. **跨合并的清单新鲜度（Q9）。** 采用全树按需计算与可选的不入库报告。
   删除已提交清单及其逐字新鲜度义务，保留语义谓词、扫描范围、覆盖下限与预算。
   这取代合并后另补再生成提交的建议，也不采用旧选项中只扫描 PR diff 的部分。
   第 10 节规定命令和证明边界。
10. **Turn 词表的终态。** 第 11 节的目标表默认保留三套与七个冗余拼法，因为
   Q2 建议保留两者。Q2 的实际写入及读回证据证明 `turn_route` 已持久化，因此
   实现保留三套不同值集并生成投影，不合并拼法。未来合并提案须提供双读或带版本
   的迁移及读者证据。Owner：Turn driver owner。
11. **退休预算使用独立字段 token。** 六个旧字段预算使用
   `count_identifier_modules()`，因此 `goal_boundary_repair` 不会被算作
   `goal_boundary`。这是保守的词法指标，不等于证明不存在语义读者。B3 在它旁边
   加入 `check_reader_metric()`：为每个模块记录一切成立的事实——读、写、承载、
   未定、仅提及——并把所有非「仅提及」的模块作为迁移面纳入预算。这包含字段专属
   的未知：把字段名当数据持有的模块，以及扫描无法解析的模块，都是删除该字段前
   必须有人处理的工作，未知的是工作形态而不是工作是否存在。两个指标现在都在
   检查。仍然未决的是：当迁移面预算已经能排序删除工作后，是否退役 token 预算；
   以及在 1711 处 Python 计算式键访问与 400 处 TypeScript 计算式成员之下，迁移
   面为零的字段还欠哪些残余证据——它们不属于任何字段，因此清空任何一个字段都
   无法退役它们。Owner：内核维护者。

## 附录 A：执行账本（非规范）

新条目是文件，不再写在这一节里。每条放在
[`ledger/<rfc-slug>/`](ledger/README.zh-CN.md) 下，命名 `YYYY-MM-DD-slug.md` 并配
一份中文镜像；目录名即所属 RFC 文件名；跨 RFC 不设枚举——每个 RFC 的目录就是它的索引。

这个理由是实测的，不是风格偏好。本节过去是一个共享的追加簇：每个新增条目的分支
都插在同一位置，所以并发工作在这里**必然**冲突——#4447 的修复轮里一个下午撞了
八次，每次解法都是"两侧不相交、保留双方"。这是机械劳动，而它的失败模式是静默
的：一次粗心的解决就会丢掉一条没人会发现的记录。一条一个文件消除了这行共享，
命名与镜像配对由 `examples/docs-governance-smoke.py` 校验，约定不会退化回去。

下面的条目早于这次拆分，原地保留。它们是只追加的历史、从不编辑，所以从来不是
产生冲突的那一部分。

### 2026-09-17 — B3：退休指标把读者与提及分开

六个旧 should-run 字段此前按 token 计数计入预算：文本里出现该独立字段名的模块
数。这个数字回答的是「这个名字在这里出现过吗」，而不是退休所问的问题。
`check_reader_metric` 把同一批模块按句法使用分类，并把所有非「仅提及」的模块
作为字段删除前欠下的工作纳入预算。下表的 role 列是固定优先级下的单一标签，用
于排序与打印；退休时真正要读的计数是它旁边那些相互重叠的事实。

| 字段 | Python token | reader | writer | binding | unresolved | mention | 迁移面 | TS token | 迁移面 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `protocol_action_packet` | 5 | 1 | 4 | 0 | 0 | 0 | 5 | 2 | 2 |
| `external_evidence_observation` | 8 | 4 | 1 | 1 | 1 | 1 | 7 | 1 | 1 |
| `heartbeat_recommendation` | 17 | 8 | 4 | 1 | 0 | 4 | 13 | 1 | 1 |
| `execution_obligation` | 20 | 8 | 7 | 0 | 0 | 5 | 15 | 1 | 1 |
| `work_lane_contract` | 29 | 11 | 8 | 9 | 1 | 0 | 29 | 3 | 3 |
| `goal_boundary` | 30 | 8 | 4 | 3 | 1 | 14 | 16 | 2 | 1 |

token 计数掩盖掉的三个结果：

- `goal_boundary` 与 `work_lane_contract` 是 30 与 29，只差一个模块，于是计划
  把两者当作同等代价排序。它们真实的迁移面是 16 与 29。`goal_boundary` 有十四
  个模块是提示词散文与模块路径导入，迁移根本不会碰到；`work_lane_contract`
  一个这样的模块都没有。
- `protocol_action_packet` 只有一个 Python 读者和四个写方。它是代价最低的首个
  M3 删除对象，而 token 计数说不出这一点。
- `loopx/` 下有 1711 处 mapping 访问器使用计算式键，另有 400 处 TypeScript
  计算式成员访问是该运行时的对应形态。任何按名字的扫描——词法的还是句法的——
  都无法把它们归属到某个字段，因此两者都不进入任何字段的迁移面，smoke 把这两
  个数字与各字段计数一起打印。这就是「计数归零不授权删除」的可测形式；残余
  义务归 Q11。

首个实现落地后又实测出五处更正，它们都曾把迁移面做得比实际工作量小：

- TypeScript 扫描只认成员访问，于是 TypeScript 真正使用的写法被归成了散文：
  解构读取、对象字面量写入、以及已声明的属性签名。为两个运行时各写一遍的十二
  种等价访问中，有八种给出不同结论。`{field: x}` 在 Python 是 writer、在
  TypeScript 是 mention，因此把一个 dict 字面量移到边界另一侧就能让迁移面缩小
  而没有迁移任何东西；六个字段在 TypeScript 侧全部实测为零个写入者。
  `execution_obligation.ts` 读作「0 个模块要迁移」，而 `turn_envelope.ts` 正在
  那里声明它的截断上限。现在断言两个运行时对同一种访问给出同一个结论——这正是
  让迁移面可以据以排期的性质。
- 标准不确定的 TypeScript 那一半此前只取自拼出过字段名的模块，145 个里的 4 个。
  扩到每个被跟踪模块后，计数从 82 处升到 395 处，代价 0.52s。
- 汇总统计的是角色标签。该标签单值，于是一个既读又写该字段的模块只被算作读者，
  读写重叠根本无法出现。现在五项事实——读、写、承载、未定、仅提及——各自单独
  统计，模块会计入一切对它成立的集合。集合相互重叠，因此 smoke 断言它们**覆盖**
  token 样本而不是相加等于它，划分则在旁边保留自己「相加等于 token 计数」的断言。
  重叠很大：`heartbeat_recommendation` 迁移面 13 个模块里有 8 个读、8 个写，而
  标签把它报成 8 个读者、4 个写方。
- 把字段名当数据持有的模块被报为 `unresolved`，然后以「并不**已知**需要迁移」
  为由排除在迁移面之外。它是已知需要调查的：不打开那个模块，没人能说该字段不
  在其中，而无论结论如何那都是该字段专属的工作。排除它还会让迁移面在有人把读者
  改写成扫描无法解析的形式时下降。字段专属的未知现在计入；随之
  `external_evidence_observation` 6 → 7、`goal_boundary` 15 → 16、
  `work_lane_contract` 28 → 29，各为一个模块。全仓范围的计算式键总数仍在所有迁
  移面之外：它们不属于任何字段，清空任一字段也永远退役不了它们。
- 不可解析的被跟踪 Python 模块被记成了提及。它可能含有读者，把它记为散文等于凭
  一次解析失败缩小迁移面。它现在是该字段的未知，位于迁移面之内。曾考虑改为抛错
  并否决：那会让在半写状态树上工作的直接调用方整个扫描失败，而「未知」是诚实的
  分类，不是更响的那一种。

每次运行都会按字段、按运行时断言这些角色恰好划分 token 计数。因此新指标是对同
一批模块的重新分类，而不是换了一批更小的样本；本切片也不偿还任何债务：两个预算
都在同一 diff 里钉在各自的实测值上。

这个划分给每个模块分配它首个命中的角色，回答的是「这个模块主要是什么」，不是
「谁写这个字段」。`work_lane_contract` 有 8 个模块角色为 `writer`，而实际写它的
有 13 个；另外 5 个同时也读它，于是划分把它们算作读者。退休时要找齐生产者，读
的是打印在划分旁边的 `reads`／`writes`／`binds` 重叠计数，而不是这个划分本身。

这道检查占 36.9s 守卫中的 8.5s，是五次实测的中位数；这五次都在守卫内部对该函数计
时，而不是相减两次整体守卫耗时，跨度为 35.8–39.8s 中的 8.4–9.2s，即守卫的
21%–25%。相减法先试过并被放弃：在共享机器上它把同一份成本给到 4s 到 32s 之间，
因为它相减的两个数都会随机器上其他任务一起波动。扫描必须遍历每个被跟踪的
Python 模块：计算式键总数是全仓范围的，一个从不提及任何字段的模块同样计入它。
`parse_python` 从 `python_facts` 中析出，使两处扫描对不可解析的源抛出同一个错误；
它刻意不加缓存——把约两百万个 AST 节点留到运行结束，整体实测比解析两次还慢
0.7s，并且会把 `check_inventory` 从 2.4s 拖到 5.4s，而那正是 #4628 刚刚变快的
那一趟。

TypeScript 一次批量复用 `scripts/semantic_production_scan.mjs` 的 TypeScript
解析器，按 AST 属性和字面量下标区分读、写及复合更新，覆盖可选访问与模板插值。
注释、引用示例与正则字面量不会变成访问，也不会遮住后续代码。对象字面量键按
Python 的 `dict_literal_key` 同样计为写入，类型／接口属性签名计为承载，解构
计为读取，未被任何键位消费的字段名字符串计为未定——每一条都与 Python 侧同构。
计算式键的数据流与外部消费者仍在本指标之外，M3 删除前必须另行核查；token 预算
保持不变。

### 2026-09-18 — 生产者扫描三处自信而错误的答案

对"扫描可以把什么报成完整"是规范性的。没有移动任何预算、下限或锚点：
`unresolved_producer_sites` 修前修后都是 40，因为当前树里没有站点触发这些形状。
修的是"未来某次改动能蒙过门禁"的路径，不是现存的违规。

- **为什么方向重要。** F1 要证明 `Produced_scan(v) ⊆ S(v)`，所以危险的错误是
  扫描**没看见**的值——那样一个未注册值就会通过。多报只会产生误报。下面每一条
  都是一个**被漏掉**的值，却装在一个看起来完整的集合里。
- **`global` 重绑定完全不可见。** `_module_functions` 只遍历 `tree.body`，从不
  进入任何函数体，所以另一个函数里的 `global pick; pick = other` 从未被算作
  `pick` 的第二次绑定，同模块调用仍然解析到原始 `def` 的返回值——而这个名字在
  运行时会被模块换掉。现在任何被某个作用域声明为 `global` 且在该作用域赋值的
  名字都被取消资格，调用保留 `call_result`。
- **`**` 展开重播了陈旧的初始值。** `bound` 只跟随朴素的 `name = expression`
  写入，所以一个随后被下标改写的字典仍解析到它的初始值，于是 `{**overrides}`
  把该键的原值报成了产出值。`lookup` 的逐键并集在展开路径上不可达——展开一次
  贡献所有键——因此被改写过的容器的展开现在走未知键答案。
- **TypeScript 扫描器没有作用域模型。** 任何拼作 `String` 的标识符都被读成内建
  转换、任何 `undefined` 都被读成字面量，于是 `function emit(String)`（一个由
  调用方提供、可以返回任何东西的函数）产出了一个自信的值。现在按**文件**粒度
  检测遮蔽，比按作用域更粗，而且是刻意的：文件粒度只可能**扣留**一次内建读法，
  永远不可能**凭空造出**一个。
- **有一条反例是被放松，而不是被删除。** 字面非负下标写入**确实**被建模，读回
  解析为初始值与该写入的并集。在那里强求 `unresolved` 等于把一个更弱的扫描钉死，
  所以断言改成陈述真正要紧的性质：报告的集合可以多报，但绝不能漏掉被写入的值。
- **它没有确立什么。** 扫描仍然不建模任何跨模块数据流；文件粒度的遮蔽检测会对
  "在无关函数里遮蔽了 `String`"的文件也扣留内建读法。两者都是保守方向的失效，
  且都是实测记录而非假设。

### 2026-09-17 — 两处自相矛盾的度量已修正

对 F5 的 `verified` 含义与闭集碰撞身份是规范性的。没有放松任何预算、下限或锚点；
两处都先在 `d8e7af141` 上复现，再动笔。

- **声明把自己当成了证据（F5）。** `projections[*]` selector 的 `verified` 与
  `registered` 都返回 `len(registry["projections"])`，而 `check_projections` 只
  导入了一个写死的投影。加入一个 owner 模块与函数在整棵树里都不存在的投影，并把
  值域声明成 2/2，完整 smoke 依然通过，并在证据边界 `executable_owner_function`
  之下打印 `F5:2/2`。现在 `verified` 只统计代码里 `EXECUTED_PROJECTIONS` 点名的
  投影，且每个投影在注册表里的 `owner` 必须等于检查真正导入的那个函数。同样的注入
  现在要么失败（`claims 2 verified members ... the m0 check walks 1`），要么如实
  声明并打印 `F5:1/2`。
- **重排 `set` 被算成语义分叉。** 碰撞身份用的是 `tuple(item["values"])`，即源码
  顺序。把一个 `set` 字面量里的三行调换位置——成员完全没变——就会把孪生变成分叉，
  一次触发三个预算失败；而由同一份 inventory 算出的 `divergent_value_sets` 正确
  地报告没有分歧：一次扫描、两个输出，且挡住 CI 的是错的那个。现在只有当一个名字
  的**每一个**定义都是 `set`/`frozenset` 时才按成员判定身份，否则仍按源码顺序。
- **这个归一化是刻意收窄的。** `tuple`、`list` 与 `as const` 载体保持顺序敏感，
  因为 `LIFECYCLE_PRIORITY` 就是一个定义在两个模块里的 tuple，它的顺序就是优先级；
  一刀切归一化等于用一个假阴性换掉一个假阳性。同一名字若由不同容器承载，同样保持
  顺序敏感——这让该规则成为纯粹的放松：它只会合并旧规则拆开的定义，因此不会让任何
  未改动的代码树开始失败。
- **它没有做什么。** 它没有确立 enum 与 `Literal` 别名的顺序语义——这些载体不带
  container，仍按顺序敏感处理；也没有新增第二个可执行投影，F5 仍然只走一个。

### 2026-09-17 — B2：三条有界绑定形式，以及仍然保持未解析的残量

- **起因：**[#4447](https://github.com/huangruiteng/loopx/issues/4447) 把 B2 残量
  记为“34 个减去 15 个设计上不可证的”。在 `9003577f9` 上重新测量，总数是 **41**，
  且分布覆盖全部被扫描词表，而不只是 `effective_action`：`annotation_only=5,
  argument_name_only=10, attribute_read=2, call_result=11, other=1,
  typescript_dynamic=8, unstable_local=4`。issue 里的数字已经过期；本条记录实测分布。
- **交付：**在 `python_production` 中新增三条有界局部形式，每条都在
  `tests/architecture/test_semantic_producer_binding.py` 里配有正例与反例。
  1. **同模块调用结果。**对同模块内一个未被装饰、非生成器、以普通 `def` 定义的
     顶层函数的调用，解析为该函数自身全部 return 的并集。实参从不绑定到形参，
     因此返回形参仍然未知，且结果与调用点无关，可按模块扫描记忆化。装饰器、
     `async def`、生成器、该名字的第二个顶层绑定、导入调用与属性调用、局部重绑定
     以及递归，都继续保留 `call_result` 阻塞原因。
  2. **局部变量的有序重绑定。**被写入多次的局部变量，解析为文本上位于该读取之前
     的那些写入的并集，且仅当该名字的每一次 store 都是普通的
     `name = expression`、并且没有任何写入与该读取处在同一个循环内时才成立。
     `with`、`except`、海象、增量赋值、解包、`global` 与 `del` 造成的重绑定不被
     本扫描定序，会直接抹掉该局部变量。

     只有在没有回边横跨的位置上，文本先后才等于执行先后。循环体里靠后的一次写入
     会在下一轮迭代抵达位于顶部的读取，于是「取文本在前的写入」这条过滤会丢掉一个
     活的取值，并报出一个并不封闭的取值集合：一个从第二轮迭代起就发出未注册取值的
     生产者会被读作「完全解析」。这是唯一一种把未知变成错误证据、而不是变成更小残量
     的失效方式。四种形态已被钉为回归——`for` 回边、`while` 回边、由外层循环携带的
     写入、以及在 `finally` 中重绑定——旁边另有两条正例，守住这条形式本来要支持的定序。
  3. **按键精确的容器写入。**只经由直接字面量键下标写入被改动的局部容器，保留其
     未被触碰的键；被写入的键则携带其初始化值与每一次写入的并集。别名、方法调用、
     计算键、负索引或更深层的 store、`del`，以及把容器作为任何调用的实参传出，仍然
     丢弃整个容器。负索引指向的槽位，其序号取决于容器长度，与某个非负索引是同一个
     槽；把它按键 `-1` 记录下来，会让对 `table[0]` 的读取仍然看到那次写入早已替换掉
     的初始化值。对静态可知的 dict 字面量的 `**` 展开会被摊平，因此可选展开
     不再遮蔽同级键；未知展开仍使所有键变为动态。

  TypeScript 解析器补上了 Python 扫描器早已具备的两条可靠形式（`||` 与 `??` 的
  分支、透明的 `String(x)`，以及把 `undefined` 读作无值），更重要的是开始报告
  **同一套阻塞原因词汇**：单一的 `typescript_dynamic` 兜底被
  `attribute_read`、`call_result`、`unstable_local` 与 `dynamic_key` 取代，
  `typescript_dynamic` 只保留为无法进一步归类时的兜底。owner 成员结果
  （`enum_result`）现在也携带原因；未打标签的未知在报告分布里是不可见的。
- **结果：**未解析位点 **41 → 40**，分布为 `annotation_only=5,
  argument_name_only=10, attribute_read=7, call_result=14, other=1,
  unstable_local=3`。八个 TypeScript 位点全部被重新归类（五个 `attribute_read`、
  三个 `call_result`）；它们没有一个是可解析的，因此这部分是分类学修正，不是缩减。
  唯一被关闭的位点是 `driver.py::build_loopx_turn_plan:500`，它同时需要三条形式加上
  展开摊平。证据的改善超过数字所显示的：携带至少一个已知值的未解析行从 **2 → 7**。
  注册表取值、预算与 producer 位点清单均未变化，也没有任何位点变为新可见或未注册。
  回边规则与负索引规则是在这次测量之后补上的，补上后这组数字一个都没有变化：树上
  没有任何一个位点是靠那条不可靠的路径解析出来的——那份「通用性」并没有换来任何
  被这次可靠性修复夺走的东西。
- **随后收掉三条非阻塞的评审发现，没有一条移动任何数字。**`_MODULE_FUNCTIONS`
  以 `id(tree)` 为缓存键，其正确性依赖「`_TREES` 永不淘汰」这个远处不变量；一旦
  给那个缓存加上上限，被复用的 id 就可能返回另一个文件的函数表，把调用静默绑到
  错的被调用者。现在按路径与文本哈希做键，与 `_TREES` 一致。`_is_generator` 用
  `ast.walk`，而它会下降进嵌套作用域，于是一个只是在内部定义了生成器、自己并不
  yield 的普通函数被当成生成器而丢掉绑定——方向是安全的，但它扣住了本切片正要变
  得可行动的证据。`blockerFor` 把对象字面量、数组字面量与模板表达式标成
  `dynamic_key`，而共用词汇把该标签定义为「计算键或非字面量下标」；Python 对同样
  的形状给出 `other`，现在两个运行时一致。残量仍为 40 个位点，分布不变。
- **有意不绑定，并记录原因：**
  - `annotation_only`（5）——五个全部是裸的 `effective_action: str` 字段声明，
    **根本没有值节点**。设计上不可证；issue 的归类得到确认。
  - `argument_name_only`（10）——确认设计上不可证，但需要一点锐化：它们不可证的是
    *生产角色*，而不是表达式不可解析。十个里现在有四个已经携带完整解析出的取值集合，
    并且仍然被正确地判为未解析，因为被调方（`_execution_obligation` 及其同类）是在
    读取该字段而不是产出它。缩减这一类的诚实做法是在注册表 `call_producers` 中声明
    一个经过评审的输出构造器——一次评审者看得见的数据编辑——而绝不是改扫描器。把任何
    与字段同名的关键字算作生产，会使该义务变成同义反复，这是第 5 节所禁止的。
  - `attribute_read`（7）、`call_result`（14）、`unstable_local`（3）与 `other`
    （1）——剩下的每个位点最终都落在本扫描边界之外的四件事之一：读取调用方提供的
    映射或对象（`decision.get("effective_action")`、`run_decision.effective_action`）、
    跨模块调用、返回形参，或方法链。绑定其中任何一种都需要跨模块或对象字段解析，
    那是另一条有自己影响面的有界形式，本切片不做。**本扫描无法绑定的位点，保持
    `unresolved` 并带上它被记录的原因——绝不当作 dead。**
- **成本：**producer 扫描在每个触及 `loopx/` 的 PR 上都会运行。在它覆盖的 319 个
  Python 文件与 5 个 producer 词表上，同一棵树三次取最优：**9.20 s → 7.17 s**。
  加深后的扫描净变快，因为它现在跨词表复用记忆化的语法树与模块函数表，而不再每次
  扫描重新解析。
- **对规范设计的影响：**第 5 节的有界 producer 模型写明这三条形式与共享的阻塞原因
  分类；不变量与里程碑均无变化。

### 2026-09-17 — B5：对声明了槽位的词表报告消费者角色

非规范性；仅为参考证据，且 B5 是 #4447 的可选项。当前源码树上没有任何检查的
通过/失败结果改变，本次新增的内容也不闸住任何合并。

- `consumer_ranking` 统计的是**提及**某符号的模块数。第 5 节早已写明这个数字
  “既不分类角色，也不证明数据流”，因此 B5 新增
  `loopx/semantics/consumer_report.py`：一次有界的 AST 扫描，把每个消费位点分类为
  `read`、`interpret`、`pass_through` 或 `unknown`，并逐行携带位置
  （`module::symbol` 与行号）、扫描所依据的源码 SHA、涉及的值域，以及适用于该行的
  边界说明。
- **覆盖面由注册表声明决定，不靠猜测，而它是 26 中的 1。** B5 以具体的槽位身份为
  前置条件，而只有 `effective_action` 声明了 `literal_scan.field`——与 drift smoke
  已经上报的 `literal_scan_fields:1/1` 是同一个事实。初版实现在没有声明字段时回退到
  词表 id，于是把 25 个词表按一个谁也没声明过存在的字段名去分析，下游每一行都继承了
  这个猜测。该回退已删除：没有声明槽位的词表按名字列在 `missing_slot_identity` 下、
  计入表头，并且完全不做分析——不做部分分析，也不单靠 owner 类分析。
- 锚点全部来自注册表已有的身份，这正是以 B2 为前置条件的原因：已声明的槽位名，以及
  它注册的 owner 类——后者沿用 producer 扫描器同一套“一跳、未改名”的导入绑定纪律，
  且只在没有更近的绑定接管该名字时成立。扫描范围与 `PRODUCER_ROOTS` 一样由代码所有，
  注册表数据无法扒宽它。
- **角色只从能确立它的语法中得出。** 调用、f-string 与再一层属性访问都会终止向上
  攀爬，记为 `unknown` 并写明是哪种构造。`Kind(value)` 与 `sink(value)` 是同一种语法，
  因此都不能当作“值原样出来了”的证据；先前的实现把两者都报成 `pass_through`，还把
  `.value` 当成保义的枚举拆包——而它并未确立那个对象是枚举成员。现在
  `pass_through` 的含义是 AST 显示该值本身被搬运：被返回、被存入、被放进结构，且
  路上没有施加任何函数。已观察到的分支仍然压过未解析的同级用法，因为解释是等级的
  顶端，不可能有更强的东西被藏住。
- 在 `d8e7af141` 上、跨 `loopx/control_plane` 与 `loopx/cli_commands`、
  1213 个已跟踪源文件中的 485 个上实测：**713 行——`read` 0 行、`interpret` 61 行、
  `pass_through` 9 行、`unknown` 643 行，未知占比 90.2%。** 若只看归属
  `effective_action` 的 111 行，未知占比为 36.9%。相对带猜测的实现，这是 921 行降到
  713 行、未知占比从 78.9% 升到 90.2%：报告变小了、也变得更不自信，而这正是证据
  支持的方向。
- 未知量是这次度量的主体，不是待清扫的残渣。602 个位点以计算出的键读取映射，
  因而对被覆盖的词表未解析；18 个模块写出了槽位名却没有可识别的锚点；13 个位点把值
  交给了本扫描不跟进的被调方；9 个携带槽位名的已跟踪 TypeScript 源文件未被遍历，
  因为这条路径上没有 TypeScript 解析器；1 个是不稳定局部变量。每一条都是带位置与
  记录原因的行，沿用 B2 为未解析 producer 位点确立的做法，且两类行都不再被过滤出打印
  清单：有归属的行与无归属的行各自成块、共用同一个 `--top` 配额。只展示语法解析得出
  的那些行，读起来就像是该槽位读者的一份完整普查；而把两类行合成一份按位置排序的清单，
  又会让那 602 行把有归属的行压下去——那是同一种遮蔽的另一种写法。
- 该报告确立的是**语法使用，而非数据流**。它不证明该值来自已注册的 producer，
  不证明该分支可达，也不证明没有行的词表就没有读者——计算键那一群位点，恰恰就是按
  名字归组的扫描无法作出最后这个论断的原因。
- 通过既有报告表面暴露为
  `scripts/generate_semantic_inventory.py --report --consumer-evidence`，缺少
  `--report` 时它会拒绝运行，因为它是参考证据而不是检查。全树上按位点扫描三次运行
  耗时 4.1–4.4 秒，而 `--report` 本已打印的排名约需 217 秒。drift smoke 不会调用它。
- **这让 B5 还剩多少价值。** 一个词表、111 行有归属的行、其中 41 行未知，并且在某次
  M1/M3 迁移为另一个词表声明槽位之前，覆盖面无法增加。诚实的读法是：这套机制跑在了
  它所读的注册表前面；价值要等第一次迁移需要它时才出现，而不是现在。
- 本次未处理：扫描范围是两个根目录而非整棵树；TypeScript 只计数、不解析；
  跟随局部变量只走一跳，因此经过两次别名传递的值是未知而非被追踪。扒宽这三者中的
  任何一个，都是自带风险的另一个变更。

### 2026-09-17 — `cross_runtime` 层的逐值含义

对形式模型非规范：不新增任何不变量，也不改变任何既有检查的结论。改变的是每个
已注册值现在都写明了什么条件产生它。

- 覆盖率从 149 个值中的 68 个升到 149/149。原有的 68 个就是整个 kernel 层，由
  #4625（四个规范 Turn 词表）与 #4626（`effective_action` 与 `lease_action`）
  补齐。本次新增的 81 个是整个 `cross_runtime` 层，共 20 个词表。
- 跟踪 issue 把这批剩余描述为“117 个值”。那是 #4626 合并之前的计数：117 是
  #4625 未覆盖的全部，其中当时仍包含 `effective_action`（32 个）与
  `lease_action`（4 个）。这两个都属 kernel 层且已补齐，所以真正待办的是 81 个。
  以注册表为准，而不是以 issue 文本为准。
- **一条备注必须写什么：** 什么条件产生这个值——运行时要成立什么，代码才会选它。
  不是把标识符换个说法，也不只是它随后的处置。M0 时仅有的三组备注记的都是处置，
  这正是读者仍须从生成的规则表里反推控制流的原因；要闭合的就是这个失效模式。
  这是评审据以衡量一条备注的标准；它不是测试能判定的标准，下面的棘轮也不声称能判定。
- 当产生条件无法确定时，备注就如实写明，并点名什么证据可以了结它，形式为
  `Unresolved: … Missing evidence: …`。按本次实测，81 个中有 2 个处于此状态，且都
  没有臆测：`settlement_failure_kind.cancelled` 在两个 owner 中都有声明、
  解码器也接受，但 `loopx/` 下没有任何分支选它，只有伪造它的测试用到，且没有
  `compatibility_only` 声明说明它是保留值；`todo_decision_scope_kind.other`
  是一个被接受的成员，却没有生产者、也不是兜底——集合外的 kind 会被拒绝而不是
  归并到它——并且没有任何文档说明作者何时该选它。
- 备注同时写明而非隐藏了一条相关边界：若干 `cross_runtime` 值是
  **由作者声明、仅做成员校验** 的，没有任何分支选择它们。四个
  `goal_amendment_class` 值、`todo_decision_scope_kind` 与
  `todo_decision_scope_granularity` 的全部值，以及
  `delivery_outcome.primary_goal_outcome` 都属此类。它们的备注写明由谁声明、
  依据什么判据、该判据在哪里是规范性的，并直说没有代码分支选它。这是该层的真实
  性质，也正是 `cross_runtime` 不声明 producers、处在 F1/F2 之外的原因。
- 本次的棘轮是一个新文件
  `tests/architecture/test_cross_runtime_value_notes.py`，而不是追加到
  `test_semantic_vocabulary_drift.py` 末尾——kernel 层棘轮在那里，且已有多个未合分支
  在该处冲突。它从注册表推导自己的作用集合，因此新增一个 `cross_runtime` 词表无需
  改测试即被覆盖。没有 `value_notes` 条目、条目为空白或仅空格、以及未点名缺失证据的
  unresolved 标记，都会失败；另有一条测试会在某个词表被登记到两个棘轮都不走的层时
  失败。
- **该文件初版带的两道闸门在评审中被移除**，评审是对的。字符下限加非停用词计数声称
  能抓住“只是复述自身标识符”的备注：词数无法说明一条备注写出了产生条件，它可靠改变
  的只是奖励灌水。把未解析数量钉在 2 的预算声称能阻止 “unresolved” 变成省事的默认
  答案：给诚实设上限，换来的小数字来自逼迫下一位作者编造一个产生条件，而不是如实
  记下证据缺失——而那正是证据规则要防的结果。两项义务都真实存在，也都留给评审；测试
  现在只断言它能从注册表判定的东西。
- 本次未处理：备注是散文，没有任何机制检查其真伪。没有任何机制保证所述的产生条件曾经
  成立，或在代码移动之后仍然成立。对 `cross_runtime` 层而言并不存在可供比对的
  producer 扫描，这与 F1/F2 的值域边界已经披露的是同一个缺口。
### 2026-09-17 — 分离公式、角色与强制性声明；对形式签名做突变

强制层级的表述是规范性变更；除新增一条规则外，检查本身不变。#4447 Track B 的 B0 切片。

- **实测到一处不一致，在正文中修正。** 第 11 节层级表把 `blocking_next` 注解为
  *“M0.5 后计划强制；M0 不宣称已经做到”*。F1、F2、F4 都在该层级，而三者今天都失败
  即关闭：删掉一个 `executor.py::_run_turn` 确实写入的已注册值，会抛出
  `producer writes unregistered values`；加入一个没人生产的 kernel 值，会抛出
  `decoder does not produce registered input`；从 `SOURCE_SURFACES` 声明中移除一个
  上下文，会抛出 `contexts must name every defining module exactly once`。每一个都让
  smoke 以非零退出，而按 I10，该 smoke 就在 PR 路径上。层级名字是里程碑标签，因此
  保留，阻断性的断言移到了它自己的一列。
- **四种读法现已分别陈述**，覆盖 I2、I11 到 I14 以及各强制层级被描述的每一处：
  schema 校验、实施阶段、证据状态、实际阻断行为。一行通过校验的 `formal_model` 只
  确立该声明格式良好；它不是已交付的检查，不是已执行的证明，也不是合并阻断项。
- **形式签名此前几乎没有测试。** 只有一个测试触及 `check_formal_model`，而它读的是
  `candidate_decisions` 的两个字段。键集合、五个角色、consumer 层级、六个不变量 ID、
  逐条不变量的形状以及四层划分都未被突变过。
  `tests/architecture/test_semantic_formal_model.py` 新增 26 条单点突变回归，每条都
  断言检查器失败即关闭并指名它自己的那条规则。
- **有一个突变逃逸，检查已收紧。** 完全重复的不变量条目原本能通过：ID 集合与层级
  划分都是集合，重复不改变它们，而 `check_formal_model` 按 ID 构造的每个字典都只保留
  最后一次出现。第二条带有更弱 statement 的 `F1_producer_closedness` 能通过校验，且
  没有任何东西记录 smoke 实际走过的是哪一条。现在列表必须让每个 ID 恰好陈述一次。
- **本次未处理。** 2026-09-17 那条值域条目指出的接地缺口仍然存在：同时挪动某条不变量
  的 `enforcement` 与它的 policy 层级仍然自洽，因此一次协调的双字段修改依然能在不让
  任何测试失败的情况下降级一项检查。要闭合它，层级必须由真正运行的代码推导，而不是
  声明在它旁边。B0 把缺口收窄到“需要协调修改”并记录了残留，但没有闭合它。

### 2026-09-17 — 不变量表述收敛到各自已验证的值域

规范性变更；需要内核维护者批准。当前源码树上没有任何检查的通过/失败结果改变，
改变的是这些不变量所声称的内容。

- F1 与 F2 原本在 `V` 上无条件成立，而 `check_producers` 会跳过每个没有
  `producers` 的词表——26 个中的 20 个，也就是整个 `cross_runtime` 层。现在两者
  都改写在 `Kernel(V)` 上，并以 `Produced_scan(v)`（代码所有的扫描范围内观察到的
  生产）为界，该范围是 1203 个已跟踪 `loopx/**/*.{py,ts}` 文件中的 432 个。
  `validate_production` 自己的 docstring 早已声明不主张全程序闭合性；现在表述与
  它一致。
- F4 原本是 `conflict := collision ∧ scope_overlap`，这是一条定义：作用域是声明
  的、从不推断，因此它不可能被违反。现改写为 `check_scope_declarations` 真正强制
  的枚举完备性性质。
- 每条义务新增 `domain`（`quantifies_over`、`verified`、`registered`、
  `evidence_bound`）。两个规模都在每次运行时从注册表推导，selector 与证据边界这一
  对则由 `FORMAL_DOMAIN_ANCHOR` 逐不变量钉住，因此不可能只改数据就扒宽一条不变量
  所声称的集合。
- 报告会打印各值域规模、扫描范围，以及 41 个未解析 producer 位点中永远不可能成为
  证据的 15 个。
- `check_producers` 里 `continue` 的注释原本说被跳过的是“其他 kernel 家族”。它们
  根本不是 kernel；该注释已修正。
- 本次未处理：`check_formal_model` 仍会接受一个自洽的错误声明，因为同时挪动某条
  不变量的 `enforcement` 与它的 policy 层级仍然自洽。那个接地缺口是另一件事。

### 2026-09-16 — B2 试点：Python producer 扫描器绑定一跳再导出

- **触发：** M2 把三个 Turn owner 迁入 `turn_contract_generated.py` 后，仍经
  `transaction.py` / `driver.py` 兼容再导出取 owner 的生产位点全部变为
  `unknown_producer`（未解决位点 43 → 52），这些模块没有任何代码改动，也没有
  任何检查变红，因为扫描器只在从 owner 所在模块导入时才绑定 owner。
- **交付：** `python_production` 经由一个被跟踪模块绑定一跳未改名再导出；
  第二跳、改名再导出、同名类或赋值、之后的 `import` 都让消费者保持 unknown，
  并附正负 fixture。只跟随 owner 符号名，完整 smoke 耗时不变。两个可执行输入
  见证改由一张代码持有、按已登记 `input_producer` 位点键控的表选择，smoke
  用一个锚点取代四处字面量副本。未解决位点 52 → 41；没有位点新变为可见或
  未登记；注册表值与预算不变。
- **对规范设计的影响：** 第 5 节的有界 producer 模型显式写明一跳规则；
  不变量与里程碑不变。

### 2026-09-16 — 评审一致性修复

- 每种语言只保留一个候选决策小节。
- 注册表与叙述统一使用源码位点 `L`、环境值空间 `U(v)` 和允许集合 `S(v)`，
  不把生产值预先定义为合法值。
- 明确候选处置为建议性元数据；本 schema 不交付逐候选运行时存储或执行门禁。

### 2026-09-15 — 随 RFC 开启 M0

- **基线：** `1dc6ad8d8`
- **交付：** 含四个词表、一个投影、一个 schema 版本、六个旧字段预算、一个孪生
  预算的注册表；漂移 smoke；`driver.py` 中重复的 `TURN_ENVELOPE_SCHEMA_VERSION`
  改为 import。
- **证据：** 第 9 节各行；见附录 C。
- **已知缺口：** 投影检查在 M2 发布之前导入私有的 `_route_to_disposition`。
- **对规范设计的影响：** 无。

### 2026-09-15 — 评审后修订 M0；范围改为全仓库

- **基线：** `1dc6ad8d8`
- **触发：** 一次评审发现第一版字面量扫描对 TypeScript 完全失明（`===` 从不
  匹配）、基线上已有两个未注册值（`observe_replay`、`block_replay`），以及
  owner 检查会静默跳过任何不带符号的 owner。
- **交付：** 注册表迁至 `loopx/semantics/vocabulary_v0.json` 并扩为 26 个词表、
  46 个 owner 符号、9 条关系与覆盖下限；生成清单 `inventory_v0.json`，带
  `--check` 的生成器与单元测试；smoke 重写为固定分发形式（比较、赋值、三元、
  成员、条件表达式）、基于 AST 的 owner 解析、owner 排他性、清单新鲜度与
  分叉/冲突预算；`HANDOFF_MODES` 夹具分叉改为从枚举派生。
- **证据：** 附录 C 的 E6 到 E10。
- **已知缺口：** 承重的 `decide_loop_disposition` 决策表仍只有散文（M2）；
  字面量扫描无法把一个字面量归到 `effective_action` 三个槽位中的某一个（Q6）；
  扫描无法跟随变量传值，因此两个 quota 错误码以"变量来源值"登记并核验生产者，
  而非被证明。
- **对规范设计的影响：** 第 1 至 5、8、9、11、12 节修订；新增 I8。记为未合入
  草案的当日修订。

### 2026-09-15 — 第二次评审后修复 M0 的度量

- **基线：** `1dc6ad8d8`
- **触发：** 第二次评审对 smoke 跑了 14 种攻击，7 种逃逸：单独调低某个
  `coverage_floor`、一次调低全部下限、调高 `inventory_ratchets` 或某个退休
  预算，以及最关键的——在同一个 diff 中删掉一个 owner 并同时调低对应的下限。
  下限与被它守护的文件在同一个文件里，且只用 `>=` 比较，因此注册表可以放松
  自己的棘轮。I5 与 I8 当时是散文，不是机器约束。
- **同时发现：** 冲突检测只跑字符串常量，599 个多值载体只被列出、从未被比较。
  基线上已经有四个同名分叉，其中 `SOURCE_SURFACES` 被定义四次、四套不同值集，
  另有 19 个隐藏孪生。另外，18 个 `conflicting_values` 名字中有 16 个是模块
  局部约定（`SCHEMA_VERSION` 出现 16 次，另有 `COMMAND`、`REQUEST_SCHEMA`、
  `SURFACE`），因此该预算主要在度量局部命名。
- **交付：** smoke 中新增锚点 `COVERAGE_ANCHOR`、`COVERAGE_SUFFIX_ANCHOR`、
  `BUDGET_ANCHOR`、`RETIREMENT_ANCHOR`，关闭全部七种逃逸；
  `multi_value_name_collisions` 让枚举、闭集、`Literal` 别名与 `as const`
  数组适用字符串常量的冲突规则，四个分叉与 19 个孪生按当日计数入预算；
  `MODULE_LOCAL_CONVENTION` 让局部名保留在可见总数中但不进入语义预算
  （`conflicting_values_semantic` 为 2，`same_runtime_forks_semantic` 为 18）；
  针对 32 组同名异名同值集的建议性合并候选报告；在独立冲突夹具上新增两个
  扫描器测试；新增 I9 并写明第 9 节的边界。
- **证据：** 附录 C 的 E11 到 E13。
- **已知缺口：** 冲突按名字归组，因此改名仍可洗白一个；单元素载体不可见；
  字面量扫描可能误读同一行上无关的比较。
- **对规范设计的影响：** I5 与 I8 从"意图"改写为"已强制"；新增 I9；第 5 节
  表格与第 9 节各行更新。现在放松预算的唯一方式是挪动锚点，而那是一次代码
  修改。
- **被收紧的未决项：** Q7 可能从"采纳 `maintainability_ratchet` 的例外
  生命周期"收敛为"共用它的锚点模式"，因为本 smoke 已经在用该模式。

### 2026-09-15 — 第三次评审后把 M0 放上 PR 路径

- **基线：** `1dc6ad8d8`
- **触发：** 第三次评审问 smoke 到底在哪里运行。对只改 `loop_controller.py`
  与 `turn_envelope.ts` 的 diff 做 premerge 规划，得到 32 条命令，不含本
  smoke；`full-public-smokes.yml` 只在 push 到 `main` 与每日日程触发，且文档
  写明刻意不作 PR 必需检查。每个 PR 都跑的唯一表面是 `pytest` 扫描，而已提交
  的测试只覆盖夹具上的扫描器。RFC 的"提交时"声明因此只在合并后成立。
- **同时发现：** 每个锚点都用 `<=`（下限用 `>=`）比较，忠实复制了
  `RFC_MODULE_BUDGETS` 先例。一个 PR 把预算收紧到锚点以下后，后续 PR 可以不
  改代码把它涨回锚点，棘轮停在锚点最后的值上。
- **交付：** `tests/architecture/test_semantic_vocabulary_drift.py` 在默认扫描
  里以子进程运行 smoke；smoke 加入 `repo-architecture-budget` premerge
  profile，与可维护性棘轮并列；三处锚点比较改为相等；新增 I10；第 10 节增加
  表面表格。
- **证据：** 附录 C 的 E14 到 E16。
- **已知缺口：** pytest 包装每次扫描约耗 3 秒；premerge 选择仍依赖触发词匹配
  改动路径。
- **对规范设计的影响：** I5 改述为相等并说明偏离先例的理由；新增 I10；第 9 节
  增三行；第 10 节从一句话改写为表面表格。

### 2026-09-15 — 第四次评审 M0：范围、合并序、终态

- **基线：** 已合入 `503991dd2`；`upstream/main` 在 `2f84af990`，领先分支十二
  个提交。
- **触发：** 第四次评审问守卫的输入依赖什么、"全仓库"覆盖什么。把十二个上游
  提交合入临时树后清单过期（一个枚举、三个闭集）；对上游最近二十次合并重放
  扫描器，八次会有同样结果。RFC 写全仓库，而清单根与每条字面量扫描都写
  `loopx/`；`examples/` 有十余处 `effective_action` 断言，`apps/` 约九十个
  TypeScript 文件，smoke 从不读取。
- **同时发现：** `SOURCE_SURFACES` 是四个 CLI 命令各列自己的数据来源，不是分
  叉；按名归组的规则无法表达这一点。退休预算按子串计数（`goal_boundary` 35
  对 30 个标识符模块）。计划有预算但没有终态，四个入口决策没有 owner 期限。
- **交付：** 第 3 节把扫描根固定为 `loopx/` 并把 `apps/` 与 `examples/` 列为
  非目标；第 5 节预告 M0.5 的 `scope` 字段并以 `SOURCE_SURFACES` 为首例；第 9
  节增三行已知边界；第 10 节增合并序风险与解释器两段；第 11 节增终态表；第
  12 节增 Q9 到 Q11 并给 Q2 加核实说明；注册表 `inventory_ratchets` 增一条关于
  误分类分叉的备注。代码与预算未变。
- **有意不做：** premerge planner 保留 `python3`，因为舰队所有命令都这样拼写，
  runner smoke 也断言了这段文本；改为记录解释器要求。
- **证据：** 附录 C 的 E17 到 E20。
- **对规范设计的影响：** 第 3 节范围收窄以匹配代码；第 11 节有了完成定义；第
  12 节增三条决策。

### 2026-09-15 — 角色与作用域模型写入契约

- **触发：** RFC 使用"生产者"与"消费者"十九次却从未定义，Q2 与 Q10 依赖一个
  文中从未说明的"生产者检查"，`scope` 只存在于一段预告，且第 1 节在第 10 节
  把 pytest 扫描定为义务之后仍写 smoke "在每次 premerge 与 full-public 运行"。
- **交付：** 第 5 节新增"词表的角色"（owner、生产者、解释者、透传者）与三行
  schema（`scope`、`producers`、`compatibility_only`）；第 2 节新增 I11 到 I14，
  每条标注自 M0.5 起强制；第 9 节新增三行 M0.5 验证；第 11 节新增 M0.5 里程碑，
  M1 改为以它为门；Q2 与 Q10 指向 I12 而非未定义的检查；第 1 节与第 10 节一致。
  代码、注册表值与预算未变；M0 的 smoke 尚未强制 I11 到 I14。
- **对规范设计的影响：** 新增四条带明确强制里程碑的不变量；计划有了"被生产"
  的定义，M3 的零读者门与 Q2 的持久化问题都能使用它。

## 附录 B：决策日志

**「负责角色 / 批准」这一列记录什么，何时更新。** 这一列记录的是**可以从仓库本身核实的行为**
—— 合并提交，以及存在批准评审时的那条评审 —— 而不是写下该行时的状态。由此有两条规则，
两条都来自这张表在两个方向上各错过一次：

1. **谁让改动落地，谁在同一个 diff 里更新对应行。** 这张表的每一行都曾写着「PR 评审待完成」
   或「批准尚未给出」，而对应改动已经在 `main` 上；合并时没有任何东西更新这一列，于是账本
   陈述了与树相反的事实。一行停在撰写时刻比没有这一行更糟，因为读者会把看起来已闭合的记录
   当作已定。
2. **合并与批准评审是两种行为，必须分别命名。** 凡第 5 节要求内核维护者批准之处，记录**实际
   发生的那一种**。不要根据仓库之外给出的指示写「已批准」，也不要在改动落地后还留着「尚未
   给出」—— 这两种错误都在这张表上发生过，而且都会误导一个看不到仓库外对话的读者。


| 日期 | 决策 | Owner / 批准 | 备选 | 变更的规范章节 |
| --- | --- | --- | --- | --- |
| 2026-09-16 | Q9：全树按需计算；移除已提交结构清单 | 根据[维护者反馈](https://github.com/huangruiteng/loopx/pull/4360#issuecomment-5692062394)实现，已落地于 [#4494](https://github.com/huangruiteng/loopx/pull/4494)，合并 `75fcd5556` | 取代合并后补再生成；拒绝只扫描 diff | 1、I6、3、5、9、10、12 |
| 2026-09-16 | B2：Python producer 扫描器绑定一跳未改名再导出 | 实现，Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) B2；已落地于 [#4573](https://github.com/huangruiteng/loopx/pull/4573)，合并 `6979d528b`，@huangruiteng 已批准 | 要求每个消费者都从 owner 模块导入（脆弱；M2 中已静默失效）；拒绝无界多跳解析 | 5、附录 A |
| 2026-09-17 | B3 修复：统计全部五项相互正交的使用事实而不是角色标签，并把字段专属的未知纳入迁移面 | 实现，Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) B3；将 `goal_boundary` 由 15 上调至 16、`work_lane_contract` 由 28 上调至 29、`external_evidence_observation` 由 6 上调至 7，三处上调各自只涉及一个把字段名当数据携带的模块，token 预算与 carrier 计数均未变动；按第 5 节这需要内核维护者批准。**在案的是一次合并，不是一条批准评审：**[#4651](https://github.com/huangruiteng/loopx/pull/4651) 由 @huangruiteng 于 2026-09-18 以 `02cc53bd5` 合并，当时一条 `CHANGES_REQUESTED` 评审仍然成立，且不存在任何批准评审。维护者合并是否满足第 5 节对预算上调的要求，由维护者判断；本行记录的是这个行为，不是对它的结论 | 继续统计角色标签并加一列重叠数（否决：该标签单值，任何由它构造的计数都会漏报排序靠后的那个事实，多加一列并不改变这一点）；让 `unresolved` 留在迁移面之外，理由是「并不已知需要迁移」（否决：它是已知需要调查的，而把它排除在外的预算会在有人把读者改写成扫描无法解析的形式时下降）；对不可解析模块直接抛错（否决：这会让在半写状态树上工作的直接调用方整个扫描失败，而「未知」是诚实的分类，不是更响的那一种）；只依赖跨运行时等价性测试（否决：它断言的是一致，而两个运行时都把解构读成散文也是一致的） | 11、附录 A、附录 B |
| 2026-09-17 | B3：在 token 计数旁边为迁移面设预算；Q11 决策前两者都保留 | 实现，Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) B3；已落地于 [#4651](https://github.com/huangruiteng/loopx/pull/4651)，合并 `02cc53bd5` | 直接用新指标取代 token 预算（否决：token 计数正是证明新角色划分同一批模块的锚，在引入角色的同一个 diff 里把它删掉会让更小的数字无法复核）；把 Python 计算式下标也计为未定读取（否决：`rows[index]` 与 `payload[key]` 是同一种语法，未知数会大到不再携带信息；TypeScript 没有 mapping 访问器约定，其计算式成员访问单独计数并声明为上界） | 5、9、11、12 |
| 2026-09-16 | B1 改名不变性：新增按名字归组的分歧报告；写明它未闭合的边界 | 实现，Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) B1；已落地于 [#4614](https://github.com/huangruiteng/loopx/pull/4614)，合并 `0a4917956`，@huangruiteng 已批准 | 把预算改按值集归组（否决：`CONFIDENCE_LEVELS` 与 `EDGE_CASE_COMPLEXITIES` 共享 `high/low/medium` 而含义不同）；提交名字账本（M0 否决：Q9 已退役提交式清单）。该报告列出仍然存在的分叉；初稿称它能抓住单侧改名，实测证否，故两份镜像按真实行为写明边界 | 9 |
| 2026-09-17 | B2：绑定同模块调用结果、局部变量有序重绑定与按键精确的容器写入；对 TypeScript 残量做重新归类而非缩减 | 实现，Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) B2；已落地于 [#4682](https://github.com/huangruiteng/loopx/pull/4682)，合并 `14a766e50`，@huangruiteng 已批准 | 绑定跨模块调用与对象字段（拒绝：那是另一条有自己影响面的有界形式，不属于本切片）；把与字段同名的关键字算作生产（拒绝：会使该义务变成同义反复，见第 5 节）；保留 `typescript_dynamic` 作为单一兜底（拒绝：八个位点共用一个原因，残量无法被行动）；把被调方形参绑定到调用点实参（拒绝：结果会依赖调用方而无法记忆化，且一次错误绑定会凭空造出证据） | 5、9、附录 A |
| 2026-09-17 | B5（可选项）：只对声明了 `literal_scan.field` 的词表按位点报告消费者角色，并写明未知占比，而不是把一切都分类 | 实现，Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) B5；已落地于 [#4663](https://github.com/huangruiteng/loopx/pull/4663)，合并 `736299027`，@huangruiteng 已批准 | 没有声明槽位时回退到词表 id（评审中否决：这把 26 个词表中的 25 个按一个谁也没声明过的字段名去分析，下游每一行都继承了该猜测；它们现在列在 `missing_slot_identity` 下，不做分析）；在注册表中登记消费者（否决：跟踪 issue 明令禁止全量消费者登记，且一份声明清单是主张而非证据）；把该报告做成合并闸门（否决：F3 属于参考层级，90.2% 的未知占比也不足以闸住任何东西）；只报告语法能解析的位点（否决：这会让表格读起来像是完整的，因此未识别的提及与计算键读取都作为带原因的行打印出来） | 9、附录 A、附录 B、附录 C |
| 2026-09-17 | B0：为 I2/I11-I14 与各强制层级分别陈述 schema 校验、实施阶段、证据状态与阻断行为；要求每个形式不变量 ID 恰好出现一次 | 实现，Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) B0；已落地于 [#4661](https://github.com/huangruiteng/loopx/pull/4661)，合并 `2303ff033`，@huangruiteng 已批准 | 把 `blocking_next` 层级改名以匹配其行为（否决：层级名字表示拥有该检查的里程碑，改名会丢掉这层含义，并从另一个方向把两种读法重新合并）；在 `formal_model` 中加一个 `blocks_today` 布尔字段（否决：那只会多出一个可被读者误当作度量的声明字段，而该事实是 smoke `main()` 的性质，任何注册表修改都改不了它）；保留原注解、只在账本里记一笔缺口（否决：评审者引用的正是那句注解） | 2、5、11、附录 A、附录 B |
| 2026-09-17 | 将 F1/F2 限定在 kernel 层与扫描范围，把 F4 重述为作用域枚举完备性，并给每条义务加上可推导的 `domain` | 实现，Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447)；**已获批准** —— @huangruiteng 在 [#4631](https://github.com/huangruiteng/loopx/pull/4631) 上给出，合并 `440b002fb`（2026-09-17）；那条批准评审即是在案授权 | 保留无条件表述、只在正文记一笔缺口（否决：该表述比 `validate_production` 自己的 docstring 还强）；把 F4 重述为各上下文值集互斥（否决：会被仓库自身数据推翻，`scope_declarations` 恰恰就是为了允许合理的同名复用）；扒宽扫描让无条件声明成立（否决：那是自带风险的另一个变更） | 5、9、附录 B、附录 C |
| 2026-09-17 | 为每个 `cross_runtime` 值写明产生它的条件，把逐值覆盖率从 68/149 提到 149/149，并用一个独立测试文件加以棘轮化 | 实现，Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) Track A；已落地于 [#4662](https://github.com/huangruiteng/loopx/pull/4662)，合并 `11b857dec`，@huangruiteng 已批准 | 追加到 `test_semantic_vocabulary_drift.py` 末尾的 kernel 棘轮（否决：已有三个未合 PR 在该处冲突，而同一 diff 内的规则正是合并时最容易丢失的东西）；为两个没有生产者的值推断含义（按证据规则否决：一旦写进表里，臆测的备注与经核实的备注无法区分）；只记录由分支选择的值（否决：那会让由作者声明的值看起来像是没写，而“由作者声明”本身才是更有用的事实）；用字符下限加非停用词计数来强制“不得只是复述”这条标准，并把未解析数量上限钉在 2（评审中双双否决：词数无法说明一条备注写出了产生条件，只会奖励灌水；而给诚实设预算会逼迫下一位作者编造条件，而不是如实记下证据缺失） | 附录 A、附录 B |

## 附录 C：证据登记

| 证据 id | 声明 | 基线 / 环境 | 产物或命令 | 结果 | 隐私 / 有效性边界 |
| --- | --- | --- | --- | --- | --- |
| E1 | envelope schema 常量有三处定义 | `1dc6ad8d8` | `rg -n 'TURN_ENVELOPE_SCHEMA_VERSION\s*=' loopx` | 3 个文件 | 仅源码 |
| E2 | `loopx/` 下 28 个不同的 `effective_action` 字面量 | `1dc6ad8d8` | smoke 的 `literal_scan` | 28 | 受正则约束；不含散文提及 |
| E3 | 控制面下 43 对 py/ts 孪生 | `1dc6ad8d8` | smoke 孪生报告 | 43 | 仅同名规则 |
| E4 | 旧字段分布 | `1dc6ad8d8` | smoke 预算报告 | 见注册表 | 模块提及数，非调用点 |
| E6 | 闭集载体全局普查 | `1dc6ad8d8` | `python3.11 scripts/generate_semantic_inventory.py` 摘要 | 102 枚举、490 闭集、8 别名、40 数组、2002 命名常量、166 孪生、25/58 分叉、18/59 冲突 | AST 与 `as const` 文本扫描；仅模块级 |
| E7 | 第一版扫描模式捕获零个 TypeScript 站点 | `1dc6ad8d8` | 对每个含 `effective_action` 的 `.ts` 行应用该模式 | 7 个分发文件中 0 个匹配；`===` 总是失败 | 受模式约束 |
| E8 | 第一版 smoke 全绿时基线上已有两个未注册 `effective_action` 值 | `1dc6ad8d8` | `turn_journal.ts:656` 三元表达式 | `observe_replay`、`block_replay` | 同上 |
| E9 | owner 检查跳过了裸模块 owner | `1dc6ad8d8` | 第一版 smoke 的 `if "::" in python_owner` | `effective_action` 的 owner 从未被检查 | 读码加突变 |
| E10 | 二十个漂移突变全部失败关闭（TS `===`、TS 三元、Python 成员、经 `or ""` 的 Python `==`、裸 owner、删 owner、收窄后缀、重命名词表、分叉符号、删 TS 值、扩宽枚举、改投影、旧字段回涨、清单过期、第三种冲突拼法、死值、变量生产者消失、子集破坏、schema 版本分叉、注册表未知键） | `1dc6ad8d8` + 本地改动，改动新增载体时重新生成清单，每次运行后恢复 | 临时改动后以 `python3 -B` 运行 smoke | 20/20 退出码 1 并点名规则、值或文件 | 本地练习，非提交测试 |
| E5 | 九个漂移突变全部失败关闭（含数字与不含数字的未注册字面量、分叉常量、删除 TS 种类、扩宽 Python 枚举、改投影、旧字段回涨、注册表死值、新 py/ts 孪生） | `1dc6ad8d8` + 本地改动，每次运行后恢复 | 临时改动后以 `python3 -B` 运行 smoke | 9/9 退出码 1 并命名违规值或文件 | 本地练习，非提交测试；同尺寸同秒改写需 `-B` 绕过过期字节码 |
| E11 | 注册表可以在一个 diff 内放松自己的棘轮 | `1dc6ad8d8` + 本地改动 | 十四种注册表突变：调低一个下限、调低全部下限、在删掉它统计的 owner 的同时调低下限、调高全部 `inventory_ratchets` 条目、调高单个条目、调高一个退休预算 | 锚点前 7 种逃逸，锚点后 0 种；每个被捕获的失败都命名被锚定的值 | 本地练习，非提交测试 |
| E12 | 599 个多值载体只被列出、从未被比较 | `1dc6ad8d8` | 对枚举、闭集、`Literal` 别名与 `as const` 数组应用冲突规则 | 基线上已有 4 个同名分叉（10 个定义）与 19 个孪生，均未入预算；仅 `SOURCE_SURFACES` 就有四套不同值集 | 按名字归组；一次改名会把一个名字移出比较 |
| E14 | smoke 不在 PR 路径上 | `1dc6ad8d8` + M0 | `loopx canary premerge --changed-file loopx/control_plane/turn_driver/loop_controller.py --changed-file loopx/control_plane/quota/turn_envelope.ts`；`.github/workflows/full-public-smokes.yml` 的触发条件 | 规划 32 条命令，smoke 缺席；舰队只在 push 到 `main` 与日程运行 | 按路径 token 选择；CI 接线读自工作流文件 |
| E15 | 已收紧的预算可以漂回锚点 | `1dc6ad8d8` + M0 | smoke 中的 `ratchets[key] <= BUDGET_ANCHOR[key]` 与 `floor[key] >= anchored` | 收紧后的预算与锚点之间的任何值都能通过 | 代码阅读；先例用同样的比较 |
| E16 | 相等性关闭停滞，包装进入扫描 | `1dc6ad8d8` + M0 | 只调低一个 `inventory_ratchets` 条目而不动锚点，然后在干净树上跑 `pytest tests/architecture/test_semantic_vocabulary_drift.py` | 突变失败并同时命名两个值；包装约 3 秒通过 | 本地练习加已提交测试 |
| E17 | 上游合并会让已提交清单过期 | `upstream/main` `2f84af990`，最近 20 个 first-parent 合并 | 对每个改动的 `loopx/**/*.{py,ts}` 在第一父提交与合并结果之间比较扫描器事实 | 20 次合并中 8 次至少改变一个载体；本分支自己的上游同步新增 1 个枚举与 3 个闭集 | 事实级比较，等价于完整再生成 |
| E18 | 声明范围超出扫描根 | `503991dd2` + M0 | 从注册表读 `literal_scan.roots` 与清单 `root`；在 `examples/` 下 `grep` `effective_action` 分发字面量；统计 `apps/` 下 `.ts`/`.tsx` | 根只有 `loopx`；`examples/` 12+ 处断言；`apps/` 90 个文件 | 消费者与测试替身，非生产者 |
| E19 | `SOURCE_SURFACES` 是四个有界上下文，不是分叉 | `503991dd2` | 从清单读出四个 `multi_value_forks` 定义 | 每个模块列出自己 CLI 命令的数据来源，值互不相交 | 读值后的判断；规则本身做不出 |
| E20 | 退休预算按子串高估 | `503991dd2` | 对 `loopx/**/*.py` 分别用 `'goal_boundary' in text` 与 `\bgoal_boundary\b` | 35 对 30 个模块 | 标识符计数才是 M3 门的度量 |
| E21 | F1/F2 写成无条件，但只在一个层上被验证 | `3ca868193` | 从源码树读 `check_producers` 的跳过谓词与 producer 扫描根目录 | 26 个词表中 6 个声明了 `producers`，恰好是 `tier: kernel` 那几个；被跳过的 20 个全部是 `cross_runtime`；扫描触及 1203 个已跟踪 `loopx/**/*.{py,ts}` 中的 432 个（35.9%），未覆盖部分主要是 capabilities 285、其余控制面 192、extensions 83 | 计数来自注册表与已跟踪源码树；分母会随任何新模块移动，所以只上报、不钉住 |
| E22 | 15 个被上报的未解析位点永远不可能成为证据 | `3ca868193` | smoke 报告的 `unresolved_producer_blockers` | 41 个未解析位点，其中 `argument_name_only` 10 个、`annotation_only` 5 个分别是以字段名命名的关键字参数和裸声明；其余 26 个是动态或跨过程的 | 按标签归组；这两个标签在扫描器里由代码持有，因此这个下界只能靠改代码移动 |
| E23 | F4 写法本身不可能被违反 | `3ca868193` | 对照 F4 表述阅读 `check_scope_declarations` | 作用域是声明的、从不推断，所以 `conflict := collision ∧ scope_overlap` 是一条定义；真正被强制的是一份声明必须恰好枚举每个定义模块，范围是 1 份声明、4 个上下文 | 阅读检查后的判断；各上下文值集互斥故意*不*作为该性质，因为 `SOURCE_SURFACES` 正是合理地在四个上下文复用同一个名字（E19） |
| E24 | 退休预算把提及算成了读者 | B3 integration tree | 对六个旧字段运行 `check_reader_metric()`；断言五项事实覆盖 `count_identifier_modules()`、角色标签划分它；断言两个运行时对同一种访问给出同一结论 | 109 个 py token 模块解析为 85 个迁移面模块；`goal_boundary` 30 → 16，`work_lane_contract` 29 → 29，`protocol_action_packet` 5 → 5 且只有一个模块读它；十二种等价访问中曾有 8 种跨运行时结论不一致，六个字段在 TS 侧曾全部实测为零写入者 | 度量句法使用而非数据流；1711 处 Python 计算式键访问与 400 处 TypeScript 计算式成员仍无法归属且不属于任何字段，因此迁移面为零不等于读者为零；角色划分不是生产者计数 |
| E27 | 两个运行时结论一致，并不能凭此把某种访问留在迁移面里 | B3 repair tree | 把同一个 TypeScript 访问的七种写法各自单独扫描，每种都必须落到一个具名类别且位于迁移面之内：点号读、下标读、简写解构、别名解构、对象字面量键、类型属性、名字经局部变量进入下标 | 七种全部在迁移面内，没有一种是 mention。仅靠跨运行时一致性测试，在两个运行时都把解构读成散文时同样会通过——而这正是首个实现的实际形态 | 这是一组反例，不是完备性证明：没人写下来的第八种写法仍未被度量，这正是无法证明的使用要落到 `unresolved` 而不是落到一个自信默认值的原因 |

| E26 | B5 消费者证据覆盖 26 个已注册词表中的 1 个 | `d8e7af141` | `scripts/generate_semantic_inventory.py --report --consumer-evidence`，并与 drift smoke 的 `literal_scan_fields` 覆盖面互相印证 | 1 个词表声明了 `literal_scan.field`（`effective_action`）并被分析；25 个作为 `missing_slot_identity` 上报、不做分析。在 1213 个已跟踪源文件中的 485 个上共 713 行：`read` 0、`interpret` 61、`pass_through` 9、`unknown` 643（90.2%）；归属 `effective_action` 的 111 行中有 41 行未知（36.9%）。被删除的“回退到词表 id”实现当时报出 921 行、未知占比 78.9% | 覆盖面是注册表的性质而非代码的性质：只有当某个词表声明了槽位时它才会变化。各行是两个根目录范围内的语法使用，不是数据流；该扫描仅为参考——缺少 `--report` 时会拒绝运行 |
| E13 | 冲突预算主要在度量局部命名 | `1dc6ad8d8` | 对 `conflicting_values` 与 `same_runtime_forks` 名字应用 `MODULE_LOCAL_CONVENTION` | 18 个冲突中 16 个、25 个分叉中 7 个是模块局部约定；语义子集分别为 2 与 18 | 分类是名字模式，已在扫描器中说明并由夹具测试钉住 |

## 附录 D：被否决或取代的方案

见第 6 节。单 PR 合并枚举被否决，因为三套枚举变化原因不同；可重开该决策的证据
是 M2 之后投影被证明为双射。

## 附录 E：事故与评审教训

- 跨运行时手工同步的平行常量列表在其中一侧改变之前总能通过评审；接受第二份
  副本之前必须先有一致性检查。
- 在大模块里私有抽一份共享常量看起来无害，却是 schema 版本分叉最常见的方式。
  测试夹具是第二常见的方式：`HANDOFF_MODES` 被复制进了一个 e2e 夹具。
- 只接受 `[a-z_]` 的字面量扫描在 M0 突变练习中悄悄放过了 `_v2` 拼法。应捕获
  所有带引号字符串并单独校验形状，让畸形值被报告而非被忽略。
- 按 Python 例子写出的扫描模式在 TypeScript 上一个都不匹配；一个在含违规的
  基线上仍然全绿的守卫，只证明守卫是盲的。在宣称不变量之前，对注册表声称
  覆盖的每个运行时做突变测试。
- 当注册表既是规范又是校验器的输入，一次数据修改就能削弱校验器。把识别形式
  留在代码里、给覆盖计数设下限、拒绝不是 `module::Symbol` 的 owner。
- 只按名字计数的棘轮会放过已冲突名字的第三种拼法。定义数与名字数都要预算。
- 舰队能发现的 smoke 不是提交时检查。要问它被哪个必需的 PR 作业收集，并用
  一个只改被守护代码的 diff 做规划，看选择是否找到它。如果答案是"合并后"，
  这条不变量就是报告，不是门。
- 用 `<=` 比较的锚点只钉住写下它时的值。之后的每次收紧都无保护，直到有人记得
  挪锚点。用相等性比较，两个值就分不开。
- 同一个字段名可以在一个 envelope 里承载多套词表；看得见字段的扫描看不见
  槽位。把槽位记为关系，让歧义成为已登记的事实，而不是注册表背书的意外。
- 整棵树的已提交快照让守卫的输入依赖别人的合并。提交它之前先量一下树在它
  之下变化的频率，并写下 `main` 变红时由谁再生成。
- 按名归组的碰撞规则需要一种方式说"这些是共用一个名字的不同东西"。没有它，
  诚实的修法与不诚实的修法（改名）降低的是同一个数字，评审者分不出来。
- 文档比代码更快地扩大范围时，两者必须朝更便宜的那个方向对齐，但必须一致。
  扫描器没有实现的范围声明是一条假不变量。
- 只降不升的预算描述的是方向。在第二个里程碑之前写出目标表，否则没人能说
  工作何时完成。
- 等待一个 RFC 从未定义的"检查"的决策，是披着审慎外衣的悬空引用。点名交付
  该检查的不变量与里程碑，否则决策没有输入，永远关不掉。
- 用了十九次角色词（生产者、消费者）不等于定义了它。在角色成为一张每行带检查
  的表之前，"谁写入这个值"是每个评审者答案都不同的问题。
