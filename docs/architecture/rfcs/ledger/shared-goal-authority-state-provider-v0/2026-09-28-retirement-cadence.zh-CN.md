# 合并后的本地权威退役节奏

- 核对基线：`ce3862e33`，2026-09-28；[English](2026-09-28-retirement-cadence.md)。
- Owner：总 roadmap R3/R4/R5/R6、shared authority D1–D3、TS 迁移 T0–T4。
- 本记录替代 9 月 27 日 recovery、Host supervision 记录的**当前清单和估算**，
  不替代其历史验证结果。

## 重新核对的基线

| 已合并 | 不再计入剩余工作的内容 |
| --- | --- |
| #5054 | 旧 Todo events 投影、回填、completion 已退役；supervisor 日志已分离 |
| #5102 / #5105 | File 格式升级／备份、原生验证及 Python 原型删除 |
| #5140 / #5156 | Archive 恢复／审计与共享 runtime 读取公平性 |
| #5144 | Managed command／Codex CLI 进程监督；**不代表** attached Host 取消闭环 |
| #5173 | 已晋升且静止 Goal 的 reviewed File↔SQLite 切换 |
| #5175 | 完整 outbox drain 由 TS 拥有；Python 编排及旧逐条规划 RPC 已删除 |
| #5169 | File/SQLite 完整意图与历史证明匹配的操作重放 |
| #5170 | App 委派结果连续性；不代表全部 Turn／实例消费者完成 |

本次核对时，#4931（SQLite 读取成本）、#5106（collaboration GoalRef）、#5130
（session GoalRef）、#5139（App Turn 接受恢复）、#4915（本地状态路径迁移）仍开放。
复用和推进这些 owner，不重复实现；只对确实受影响的调用方建立依赖，本地默认切换
不等待无关云端或百 Agent 工作。

**File 是默认 store factory，不等于所有新旧 Goal 默认以 File 为权威。**
未晋升的 Markdown writer 仍可达。两个 Goal 迁移成功、或 File↔SQLite 传输成功，
都不能证明旧 writer 已没有消费者。本轮不再沿用“还剩 5–8 个 PR”；以下列出的是
交付和验证边界，不承诺缺陷数量或合并数量。

## 什么时候删、怎么删

| 边界 | 真实可达的代码／调用方 | 最早删除条件及保留义务 |
| --- | --- | --- |
| 重复决策／废弃内部 RPC | 逐项检查 TS owner 与 Python caller；#5175 已删 drain 编排 | 切走**最后调用方**且独立语义验证通过的同一个 PR，连同 handler、注册、helper 和仅服务旧实现的测试一起删。不新增 shadow／bridge 层。本规划未认证额外某个模块已死。 |
| 旧 Todo 写入 | `loopx/todos.py` 仍导入 `line_update.py` 和 `provider_create.py`、`provider_update.py`、`provider_terminal_lifecycle.py` | 新 Goal／升级路径选择 canonical authority，目标存量 cohort 完成迁移，未升级调用方有明确升级／恢复路线后，按调用家族删 Markdown 可写分支。保留人工叙述渲染和合格 import/export；provider 缺失不能悄悄恢复旧 writer。 |
| Shadow 捕获／drain | `runtime_shadow_writer_adapter.py`、`local_authority_shadow_outbox.py`、`runtime_shadow.py`；configure／CLI 和旧 writer 仍调用 | 最后受支持的源 writer 退出后删 producer／hook；prepared／committed outbox 已对账或明确处置前，保留迁移 owner 内的 reader／reconciler。一个本机 Goal 无积压不足以删除。 |
| Python 命令 facade | `authority_core.py`、canonical Todo adapter、`quota/monitor_poll.py` 仍有运行时调用方 | 完整 native 入口接管后逐组删除，包括私有 validator／Host 效果、输出投影及错误／重试行为。纯策略进入 TS 不代表输入／IO adapter 已死；不能按语言或行数整文件删。 |
| 历史格式／回执 | File/SQLite 迁移 codec、逻辑 archive、command receipt recovery | 退役旧正常写路径，但保留受支持升级边界的显式迁移、备份恢复和原回执读取。将来删 reader 须另有格式支持决策和转换验证，不能搭业务 writer 删除顺风车。 |

实施 PR 维护一份退役清单：symbol/path、生产调用方（含动态 handler／打包）、替代
owner、持久兼容义务、正反例证据及回退方式，和不可变基线比较。零 import 搜索对
内部删除必要但不充分，不能忽略公开 CLI/import 和序列化契约。保留公共行为测试，
只删没有消费者的旧实现专属 characterization。删的是代码，不是用户状态、回执和备份。

## 下一轮交付顺序

| 顺序 | 完整结果／owner | 具体出口与删除机会 |
| --- | --- | --- |
| A：现在开始 | 整 Goal 执行／消费者集成；R3/R5、现有 Host/Turn owner | 串起捕获→drain→晋升→CLI/status/quota/App/Lark 读写→settlement→重启→携带新写入迁回。核对 managed、attached、external 执行；真正取消确认／settlement 才是结束证明，过期不算。复用 #5173/#5175，在完整链路内删除重复编排。 |
| B：与 A 并行 | 本地 profile 验证；D1/D2，复用 #4931 | File/SQLite 同负载比较，包含领域图、metadata、历史、延迟／RSS、burst/lag、安装后冷 CLI，记录平台／runtime／限额。哪里失败就修其 owner。SQLite 仍是候选；优化或小演练不能决定发布默认值。File 是对照组，不是资格失败后的自动替代。 |
| C：A 与 profile 决策通过后 | 新 Goal／默认／安装／设置接入，加受支持存量升级；D3/T3 | 新装和升级、CLI、打包 App、Lark 使用同一选定权威。备份验证、reviewed migration、中断恢复、未升级拒绝及携带新写入回退可用；发布默认值显式决定。同一调用家族 PR 删除已替代的 legacy writer，不留“以后再清理”。 |
| D：伴随 C，按最后调用方推进 | 其余传输与捕获退役；T4 | native 消费者接管后删 facade／dispatch／producer，保留必要 Host IO 和迁移 reader。全部 Python 消失既不是 canonical 默认的前置，也不是切换后的自动结果。 |

Canonical 任务已覆盖整 Goal 晋升、本地 profile 与退役清单、持久 Markdown 投影／
显式重建。先对齐这些任务的证据并沿用已有 owner；投影失败应能明确重建，不能
因此把 Markdown 重新变成第二套可写权威。

A/C 若因不同执行或 onboarding owner 需要独立回退，可以拆分，但须写明原因和
剩余出口。B 是证据工作，可能暴露新的修复，不预先折算为 PR。之后 R6 仍须完成
PostgreSQL 认证传输、tenant／identity 运维、连接池／取消／failover 和跨 Host 验证，
复用现有 store/archive/service owner；不让 R6 阻止本地代码退役。

R3 实例／session 接入和 R4 意图／验收连续性仍是独立产品结果。受影响调用方复用
[后续连续性场景](../../goal-immutability-coherence-defense-v0.zh-CN.md)，不把它扩张为
尚未实现的全局门禁；CAS 成功不能证明当前 Goal 身份或任务质量。

## 删除 writer 前，本机可以积极做的验证

以下是冻结候选版本后的工程窗口，不是承诺发布日期。故障注入只用可丢弃 runtime
和经过验证的隔离副本，不能为了测试杀掉或改写活跃 Goal。

1. **现在／最初 1–2 个工作日：** 固定 binary/source 和实际 Node/SQLite driver；
   清点安装版本、caller、provider 和积压。保留独立 legacy/File/SQLite 三臂，
   验证备份可恢复、完整 Todo JSON／历史／回执一致和后续新写入。覆盖 null／缺失／
   false、归档依赖、lease、validator、in-flight Turn、pending outbox。
2. **前项通过后，接下来 2–3 个工作日：** 在隔离候选中删除拟退役分支或让它明确
   失败，走真实命令及安装后的 UI/Host 消费者。注入 commit／selector 发布前后
   进程死亡、过期实例／revision、锁竞争、runtime 不可用和投影中断。重试只能
   settlement 一次，恢复后合法工作能继续。用保留的迁移兼容 binary 验证恢复，
   不删除 selector，也不拿旧字节覆盖已确认的新写入。
3. **合格候选的连续观察：** 记录真实经过时间与负载覆盖、命令延迟、内存／磁盘／
   WAL 增长、最老积压／consumer lag、不确定结果恢复、重复效果和实例污染。
   每日读回，定期在隔离 observer／副本中验证恢复。适用的 D2 十天自然时间 soak
   不能靠循环测试或回填时间戳加速；有记录的起点才开始计时，明确重启间隙和源码变化。
4. **先 cohort 后默认：** 所需证据通过后，对有限且获授权 cohort 做 reviewed
   备份／迁移／观察，以恢复证据决定扩面。本机所有 Goal 迁移也不等于外部用户
   已升级。旧 binary/artifact 保留用于诊断，但操作／回退必须用与当前格式兼容的版本。

发生已确认数据丢失、重复效果、跨实例污染、selector／receipt 不一致或不可恢复的
不确定结果时，停止候选写入、保留只读证据，通过所属 journal 恢复。延迟／内存超过
既定预算要记录失败，不能直接提高限额。本机调查可以激进，晋升和删除证据必须可核验。

## 本轮实际验证

在 `ce3862e33` 上，本机真实 backend 的迁移／中断恢复套件通过 15 项；
真实 CLI 的 archive／升级／切换与有界源捕获测试通过 19 项。
SQLite 既有 rehearsal 完成 100 和 1,000 次提交、冷 CLI 采样及清理；报告仍为
**incomplete**，正式负载、容量、平台和 elapsed-soak 项未完成，本次没有启动 soak。

将先前捕获的真实来源隔离快照中 1,101 个完整 Todo 重建为三笔合成源事务，当前生产
CLI 全部 drain，原 Todo JSON 完整相等。所得四笔事务恢复／审计到 SQLite 后追加
第五笔已确认合成写入，再 export／restore／audit 到 File，新写入保留。没有修改
活跃 Goal。这证明有界 drain 和逻辑 archive 连续性，**不是**全部原始 224 笔历史重放、
live selector cutover、重新捕获当前生产状态或 D2 验收。私有快照和原始诊断不入库。
本规划 PR 不删除生产代码，只确定删除出口并记录实际验证边界。

### 读取成本验收更新

#4931、#5215 集成后，配对的 File／SQLite 隔离副本保留 379 笔原始提交，最终
projection hash 相同。Node 24.21.0 下，每个 provider 分别启动三个新进程，
File 首次 head 读取为 5.98–6.32 秒，SQLite 为 34.5–36.0 毫秒；后续读取分别为
9.1–10.2 毫秒、25.7–28.2 毫秒。这是进程冷读，没有清空 OS 文件缓存；File
验证全部保留历史，SQLite 读取当前状态，不承担相同的全历史证明。这支持将 SQLite
作为长历史候选，但不是同等完整性工作量的吞吐比较，也不构成发布默认值验收。

交替读取两个未变化的 File 存储，暴露了单份证明缓存互相淘汰的问题：每次都要
6.30–6.49 秒。改为有总容量上限的四份缓存后，各存储首次验证仍为 6.15–6.16 秒，
后续交替读取为 9.8–11.2 毫秒，cursor／hash 相同。每次仍检查实际字节摘要和存储
身份；淘汰与损坏回归覆盖缓存边界。

Quota 观察复用既有 should-run 摘要：捕获的单 Goal 行序列化由 1,252,747 降至
78,688 UTF-8 字节，显式明细恢复原行。这是展示体积测量，未减少采集、决策输入或
首次读取的验证成本。

另一项 148 秒隔离演练通过新进程为两种 provider 各追加 12 笔提交，跨越 checkpoint，
逐轮验证原回执重放、变更意图拒绝及 projection／hash 一致性。它证明这段有界存储
流程，**不代表** Host 执行、活跃 Goal 采用或 D2 的十天 soak 已完成。本次不改变活跃
authority、发布默认值或旧 writer 删除决定。B 仍缺持续负载／平台／容量证据；C 仍需
consumer／新建入口及受支持升级验收。
