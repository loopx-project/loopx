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

## 候选实测资格

候选 `9d91c5bc178a0acddbbab3276b404b9d0ceada3f` 与上述固定基线比较。
以下结果只覆盖本次有界合成 fixture，不代表通用性能保证或 T4 完成：

- 原生 TypeScript 测试 6,129 项通过、77 项跳过；focused Python 测试 33 项
  通过。随机差分覆盖 160 个等价案例；仅以当前页判断成员、以当前时间刷新旧
  观察时间这两种独立变异均被检出。
- 八组实际 CLI baseline/head 配对覆盖 File／SQLite 首页、末页、越界 cursor
  和未注册调用方，包括位于首页之外的注册调用方。旧字段、顺序、诊断及退出状态
  一致，新增 envelope 单独核对。registry、Goal、canonical authority 和 fence
  数据不变；SQLite 初次读取创建了空 WAL／SHM sidecar，因此不宣称零文件系统写入。
- 独立 wheel／sdist 环境在 checkout 外通过 16 条安装态 console 命令，覆盖
  两个 provider 和四种案例；确认导入自安装目录且运行时源码 hash 一致。
  未执行最低 Node 版本或 Windows 资格验证。
- Premerge 退出码为 0，并保留一项继承的 advisory。跳过项与 advisory 仍是
  验收限制，不能算成额外通过的检查。

### 完整命令成本

测量环境为 Linux、Python 3.12.14、Node 24.22.1，使用相同的合成 28-Agent
canonical File／SQLite fixture，工作集为空，显式指定 runtime root 并关闭
usage telemetry。最终交替 baseline/head 的测量共 120 次命令执行：每个
provider、每个版本各 20 次暖态和 10 次冷态。暖态为新 Python 进程复用运行中
TS server；冷态为新 Python 进程及无 TS server 的隔离 runtime 目录；未清除
操作系统文件缓存。计时包含整个子进程，小样本 p95 使用 nearest-rank 方法。

| Provider／runtime | 基线 p50／p95（ms） | 候选 p50／p95（ms） |
| --- | --- | --- |
| File 暖态 | 348.85 / 397.95 | 348.14 / 380.69 |
| File 冷态 | 949.17 / 986.70 | 950.08 / 1026.45 |
| SQLite 暖态 | 342.77 / 396.21 | 338.94 / 373.52 |
| SQLite 冷态 | 947.77 / 1020.42 | 953.57 / 1078.73 |

每次测量的运行时请求数均从五次增至六次。首页 stdout 从 9,330 增至 11,384
UTF-8 字节（增加 2,054，22.0%）。File 冷态 p95 增加 39.75 ms（4.0%），
SQLite 增加 58.31 ms（5.7%）；较低的暖态样本不能证明加速或统计显著性。
本记录量化新增 crossing 和 envelope 的成本，不授予延迟或发布资格；此前的
CLI 优化结果不能资格化本候选。

首轮配对尝试复用了长时间空闲的 runtime 目录，在目录执行前、未改动的
`explore.configuration.goal` 调用中遇到一次 SQLite transport 失败，退出码为 1；
确切根因尚未确认。该轮结果作为独立负面证据保留。随后使用全新隔离 runtime
目录完整重跑，120 次测量全部通过且 fixture hash 不变，并非仅重试失败样本。
长时间空闲 runtime 的可靠性仍未获资格确认。本检查点不关闭 T4，也不改变发布资格。

## Bridge 退出与回滚

当真实目录调用方及必要事实来源可直接调用原生 owner 时，删除剩余 Python
适配器；此前它只负责事实、公共安全与兼容传输。本次不新增存储、双写或持久化
迁移，回滚仅恢复上一版代码包。
