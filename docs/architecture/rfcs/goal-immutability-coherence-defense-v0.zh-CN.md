# 后续设计：重启与实例替换中的 Goal 连续性

- **RFC 状态：** Accepted（非规范性后续设计记录）。
- **替代 / 关闭：** none
- **交付成熟度：** 延后设计记录；不是新接受的运行时契约。
- **来源：** 保留 [Duang777 在 #5169 中提出的问题](https://github.com/loopx-project/loopx/pull/5169)，评审时收窄其实现与证据宣称。
- **语义镜像：** [English](goal-immutability-coherence-defense-v0.md)。
- **所属契约：** [Goal 实例身份与孤儿恢复](goal-instance-identity-and-orphan-recovery-v0.zh-CN.md)、[Goal 方向基线](goal-direction-baseline-v0.zh-CN.md)、[受治理的修改](shared-goal-alignment-and-governed-amendment-v0.md)、[语义交接](capable-manager-semantic-handoff-v0.zh-CN.md)、[共享权威](shared-goal-authority-state-provider-v0.md)。

这里保留原“Goal 不可变性作为一致性防御”草稿中的后续设计价值，不增加第二份
roadmap、状态 owner、验收门禁或交付宣称。激活、权限和上线由所属 RFC 决定。
以下是建议补充的验收场景，不表示每条路径当前都存在尚未修复的缺陷。

## 值得保留的部分

持久工作承诺应当比模型的工作上下文活得更久。Agent 重启或被替换后，应从已有
owner 恢复获得授权的 Goal、约束、已接受的工作和未完成义务。同名的新 Goal
不能仅因名字相同就继承旧实例的权限。即便单个存储和命令测试已经通过，这些仍是
有价值的长程运行反例。

必须区分以下事实，避免泛化为“语义一致性保证”：

| 事实 | 能证明什么 | 不能证明什么 |
| --- | --- | --- |
| 精确 GoalRef 与源 registry 拥有的实例 fence | 哪次 Goal 生命周期可以接纳动作 | 动作是否有用、输出是否正确 |
| Provider revision / CAS | 新写入是否仍基于预期的存储版本 | 调用方错误解析或重绑定实例时的当前 Goal 权限；revision 是不透明 token，不是可排序计数器 |
| 操作身份与经过验证的原回执 | 哪个操作已经提交及其原结果 | 重复执行外部效果、或把结果挂到替代 Goal 的权限 |
| 已授权的意图／验收基线 | 当前工作应遵守哪些约束和完成条件 | 缺乏独立证据时，模型确实遵守了约束或结果正确 |

**实例身份不可变**不等于 **Goal 意图不可修改**。获得授权的修改必须仍能通过现有
owner 留下版本并生效。模型完全可能基于最新 CAS 版本提交错误改动。
Prompt／上下文优化、类型化约束与结果验证和存储 fence 相互补充，没有一项可以
替代全部其他机制。

## 归入现有 owner 的后续切片

| 切片与 owner | 真实调用场景 | 决定性验收，包含恢复 |
| --- | --- | --- |
| 按实例确认连续性——Goal 实例 RFC；相关 collaboration/session 消费者见 [#5106](https://github.com/loopx-project/loopx/pull/5106)、[#5130](https://github.com/loopx-project/loopx/pull/5130) | 通过获授权的生命周期退役 A，建立同名 B，再经真实入口提交 A 的迟到 Todo／结果、claim 续约、计划确认 | B 不受到错误写入或执行权限污染；过期实例结果可观察，B 的合法工作仍能推进。保留／访问策略允许时，A 的历史回执仍归属于 A。Registry 激活及 legacy/off 行为遵循所属 RFC。 |
| 约束连续性——方向基线、受治理修改 RFC；roadmap R4 | Agent 上下文丢失后以过期材料／验收基线恢复或重绑定，再以已获授权的修改和新基线重复执行 | 从 canonical owner 恢复原约束和已接受工作；重新评估局限于相关 Agent，不把无关工作全部阻塞。合法修改可以推进，不隐式冻结全部 Goal 意图。 |
| 迟到结果的可恢复处置——handoff 与 Effect recovery owner；roadmap R3 | 请求方／实例替换后收到旧请求结果，或外部效果已经提交但响应丢失 | 保留原请求／结果关系和外部效果的持久证据；在所属 ledger 对账，不能静默丢弃证据、自动改挂到 B 或重跑效果。通过获授权的恢复路径返回结果，或明确记录终止处置。 |

实施任一切片前，先核对当前 main、相关 PR 和已有 fixture。补齐当前 owner 的缺口，
不另建“semantic certificate”或通用 coherence 引擎。共享决策放在已有类型化 TS
owner，provider adapter 提供物理存储证据。本记录于 2026-09-27 核对时，#5106、
#5130 及相关 App Turn 恢复 [#5139](https://github.com/loopx-project/loopx/pull/5139)
仍开放；仅合并它们或通过各自孤立测试，不代表上述组合链路已经验收。

## 验证方法与未决事项

使用可丢弃 runtime、公开安全的合成 Goal 和真实受支持 backend；执行前从所属
契约独立推导预期结果：

- 覆盖同实例重启、同名替换、获授权的修改、迟到输入、重叠非法条件，以及恢复后
  的合法工作。只有 stale 拒绝不等于恢复推进。
- 同时覆盖范围误扩大与逃逸：旧绑定、无关当前工作和新增对象须遵守声明的范围。
- 并发创建应遵守 registry 的唯一性和线性化契约，不假定每个竞争请求都应建立一份
  独立可写 Goal；不同的成功生命周期不能共用实例 ID。
- 每次注入一个故障并验证 oracle 的敏感性：错误 GoalRef、缺失提交 fence、交接约束
  丢失或重复效果。区分真实效果证据、模拟 adapter 与模型评测。

未决事项交给已有 owner：各 producer 是否已捕获足够的不可变实例／基线证据；
过期请求方如何收到可恢复结果；是否确需新的 producer/schema。不能通过可变的
“当前 Goal”查询倒推出原实例。如果需要格式修改，必须验收备份／迁移和混合版本
writer；本记录不预先宣称“无需迁移”。

## 本次交付边界与证据状态

[#5169 的操作重放](../../reference/authority-operation-replay.md) 验证历史 File/SQLite
提交的完整意图，且不会回退当前状态；它没有实现或验收以上三组后续链路。
过期 provider revision 的测试不能冒充 Goal 替换、上下文压缩或语义正确性测试。

原草稿的研究百分比和实验成绩表不作为已接受证据保留：本 PR 没有提供可独立审查的
公开 harness、oracle 和来源依据。后续证据须明确精确 revision、真实入口／backend、
故障、独立 oracle 和恢复读回。不沿用数值成功率，也不沿用“CAS 防止语义崩塌”的
结论。本记录不改变 File/SQLite 默认值，也不给现有 D1–D3 验收添加新前置条件。
