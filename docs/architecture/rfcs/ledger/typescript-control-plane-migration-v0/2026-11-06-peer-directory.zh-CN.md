# Peer Agent 目录投影与上游来源依据

## 目标与所有权

本次有界 T3 投影迁移删除 Python 中第二份目录决策实现。公开 `agent-directory`
保持原命令、handler 和 `build_peer_agent_directory()` 签名。一次
`agent.directory.project` 请求拥有成员检查、工作字段、路由展示上限、计数、
cursor、limitations 和当前页 rollup，并在同一 TypeScript 进程内直接调用
`sealProjectionEnvelope`。

Python 保留来源收集、公共输出安全检查及请求／结果兼容校验；删除 `_work_block()`、
`_rollup()` 和被替换的目录组装分支，bridge 失败时不回退到 Python 语义实现。
既有管理投影继续拥有 Todo 选择和管理状态，既有绑定 owner 继续解释路由；
目录投影不复制这两者的算法。

## 兼容与权限

成员资格依据完整注册集，包括不在当前 24 行展示页的 Agent。历史未注册 claimant
仍是诊断行，不取得成员资格。后续页和空页的 `omitted_row_count` 保留整份目录的
既有定义。rollup 分类计数覆盖当前返回页，不受八项展示上限影响；路由计数包含
被隐去的候选，即使只展示最多三个可公开候选。execution facts 未取得、已取得空集
与部分覆盖保持区别，false、null 和字段缺失也保留各自语义。

唯一计划内的公开增量是 `projection_envelope` 及必要的来源告警展示。envelope
继承上游来源观察时间，不以目录组装时间替换；缺失依据保持未知，来源不完整与
展示分页分别记录。本次不增加 presence provider，不解析精确宿主路由，不授予
执行权限，不改变 claim、lease、持久化格式或 provider 默认值。见
[目录契约](../../../../reference/protocols/peer-agent-directory-and-observation-v0.md#directory-projection-ownership-and-source-evidence)。

## 成本与验收边界

固定实现基线为 `faab5f5c291c5359216a0f944fe63fcfbaacdedc`。此前目录组装为
Python 本地工作，本次为该阶段增加一次粗粒度运行时请求；这**不代表整个 CLI
只有一次请求**。收益是目录规则收口及保留来源依据，代码迁移本身不能证明加速
或整 Goal 成果改善。

以下资格待最终集成候选的实测结果记录后确认：

- 实际命令的固定时间 baseline/head 差分，保持旧字段、顺序、诊断及退出状态，
  单独核对 envelope 增量。
- File／SQLite 首末页、后页成员、未授权 claimant、显式 runtime root，以及
  业务状态不变。
- 过期、不可读、不完整、缺失及重放来源依据；独立变异必须能检出只按当前页
  判断成员以及用当前时间刷新旧来源时间的错误。
- 不借用源码 checkout 的干净 wheel／sdist 安装执行。
- 同负载完整 CLI 冷／暖 p50、p95、运行时请求次数及输出体积；类型检查、
  原生／Python 回归及必需的 premerge 检查。

此清单不表示测试已通过。补充结果时须保留失败和未测边界；既往 CLI 优化测量
不能资格化本候选。本检查点不关闭 T4，也不改变发布资格。

## Bridge 退出与回滚

当真实目录调用方及必要事实来源可直接调用原生 owner 时，删除剩余 Python
适配器；此前它只负责事实、公共安全与兼容传输。本次不新增存储、双写或持久化
迁移，回滚仅恢复上一版代码包。
