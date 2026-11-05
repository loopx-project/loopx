# Goal 路由提示并入既有规划批处理

## 边界与兼容性

本切片将完整只读 Goal 路由投影从 Python 移入
`work_items/goal_route_hint.ts`，由既有
`work_item.action_portfolio.project` 批处理消费。Python 路由 builder、task
override 合并器与生产调用者在同一切片切换并退役。既有 `quota.run_decision`
owner 仍产生 task-orchestration override；本切片消费其结果，不重复实现该策略。

内部 `quota_planning_packet_request_v1` 包含各自可空的 planning 和 route
context，结果为 `quota_planning_packet_v1`。coordination 合同 JSON 登记两个身份，
既有生成器负责 Python/TypeScript 常量。原有 v0 的要求与结果形态保持原意；
外部嵌套的 `goal_route_hint_v0` 不变。

Python 在原 builder 位置、Agent lane、frontier 与 recommendation 选择之后捕获
route facts。既有 planning 调用在 monitor-only、未准入选择、结算及 cadence 清理
之前投影 hint；scheduler/序列化阶段不再回填。来源列表缺失与权威空列表保持区别；
空 frontier 对象仍命中 frontier 分支。候选顺序、先选择后截断展示、局部来源计数、
current action 优先级、canonical frontier action 优先级及 task patch 的省略语义
保留既有合同。

Python 保留既有 Todo/文本 codec，并将共享 `actions_are_projection_aligned`
owner 的结果作为命名兼容事实传递，不选择路由候选、不去重、不计数、不决定路由。
只有共享来源 owner 与最后的 Python quota 组装调用者均有原生替代后，才能退役这些
兼容事实。route-only 请求不引入 provider 读取、虚构 inventory 或 Goal 文档扫描。

hint 仍是既有 quota 读模型及其 `projection_envelope` 内的建议，不是新增权威读 API。
它不授予执行、claim、lease 或写权限，保留持久 Goal Next Action，不引入持久化格式
迁移。不新增 leaf RPC 或 Python 回退 owner。回滚恢复之前的代码/发行物与 fingerprint，
不改写 Goal 状态。

## 成本与资格

已核实集成基线为 `d39662d79784e7afa9dbc844f61452a64cb92c1c`；早期审阅基线为
`2c24d5d102dc35c417b3ce49e25096aa37e1642b`。验收须使用集成基线与不可变候选 revision。

| 局部 planning/route 段 | 修改前 | 候选设计 |
| --- | --- | --- |
| 已需要 planning | 一次 RPC 加 Python 路由规则 | 一次既有 batch RPC |
| 仅需 route | 零次 planning RPC 加 Python 路由规则 | 一次 route-only batch RPC |
| 两者都不需要 | 零次 RPC | 零次 RPC |

这是归属收敛，不宣称降低延迟。执行分支与 route-only 等待/只读分支须分别测量。
保留 warm 非持久化转换 p95 小于 2 ms 的既有目标；完整 CLI p95 回退超过 5% 或
存在无法解释的 25 ms 增量时，进入 owner review。不得放宽 wire、coverage、fixture
或性能预算；使用完整快照传输，不截断来源行。性能失败不得保留生产双 owner 回退：
应优化批处理，或暂停/回滚切换。

本检查点生成合同新鲜度、29 项合同/catalog Python 测试及原生路径对两个相关 canary
profile 的选择均为 **passed**。工作树双语镜像预检通过，staged/exact-head 门禁仍待运行。
最终静态检查、原生规则及 v0/v1 反例、独立
mutation、公开 CLI 清理回归、不可变 baseline/head 的 File 与默认 SQLite 配对
readback、干净 wheel/sdist 安装及成本均为 **not_yet_proven**，须在最终候选上记录。
p50/p95、冷启动、逻辑 IPC、wire/输出字节及持久状态 readback 分别记录；删除的
Python 规则、新增兼容代码、生产代码净变化与测试、生成物及文档分别统计。
不宣称 PostgreSQL 或 Host 资格。源码实现不等于端到端验收、性能、exact-head
审查或维护者合并授权。
