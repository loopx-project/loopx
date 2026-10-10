# Replan 历史决策归属

目标来源为总纲 #4574 与 T3 消费侧 TS 迁移。原有 Python 历史扫描分别处理重试、
ACK、中性记账及 agent 归属，规则已经分叉。本次统一到一次 typed history
projection（inline 或 snapshot 传输），并在 TS 内直接复用 Todo resume planner。
Python 保留历史解码、指纹及 obligation 呈现；删除被替代的触发扫描和重复词表。

基线 `709734cd6` 上独立编写的四个反例失败：周期复盘重复累计重试、监控重复
累计重试、ACK 未截断进展停滞、记账打断同质进展。新实现修复这些问题，保持
阈值、优先级和 obligation 标识。280 行多 agent 交错 fixture 与边界负例验证
先归属后 ACK、重试、无效身份、输入拒绝及不修改源数据。File/SQLite 消费侧测试
使用真实持久化 provider，删除展示文件后读取状态并执行 quota CLI。

本机只读演练覆盖 345 条活动 Todo、600 条历史记录和五个 agent：五个历史投影
与基线一致，隔离 File/SQLite 读后核对及公开 quota CLI 通过，活动源未修改。
该证据仅限活动读模型，不证明完整 Goal 晋升。独立的完整捕获因归档依赖缺少或
不兼容的 role/task-class 事实而拒绝；这个迁移阻塞保留，没有绕过门禁或改写活动状态。

本次闭合历史触发决策族，不代表全部 T3 或默认切换。进展指纹解码、obligation
组装、frontier 结算及完整捕获资格仍有各自归属。没有加入模型观察器、provider
切换、前端配置项或可选 capability。

## 长历史传输修复

此前 600 行演练不足以证明长期运行的 Goal。即使去掉正文，所有紧凑事实仍随历史
增长而堆积在一次 RPC 中，导致 `refresh-state` 在写回前失败。12,000 次重试的
独立反例在旧边界失败；重试洪流之前的证据仍必须参与决策。

归属沿用内置 work-item replan owner，不新增 capability、provider、store 或可选
激活。Python codec 只按序列化字节数选择传输方式。本地 IO 适配器检查绝对路径、
同用户私有普通文件、精确长度和 SHA-256，再调用不变的 TS reducer。小请求保留
原方法，大请求改传紧凑快照引用。临时文件跨 runtime 重试保留，正常成功或失败后
清理；进程骤停可能遗留 OS 临时文件，但它没有回执/权限，也不会被当成 canonical
state 重用。既有同 UID runtime 是信任边界，不是远端上传服务或新增权限隔离。

验收覆盖超限后的历史证据、四种 operation 的 inline/snapshot 一致性、ACK/peer/
重试语义、摘要/长度/缺失/symlink/输入拒绝、私有文件清理，以及真实 CLI 的预览、
写回、重放和一次 quota 扣记。既有历史字节不变，返回结果、trigger identity 及
2 MiB RPC 上限不变。受影响入口是 CLI；前端和 Lark 通过既有入口消费不变的结果
及错误，因此无需新设置或展示协议。

本次只关闭已复现的传输故障，不代表全部规模化完成。编码、解析和求值仍有
O(history) 内存/时间成本。后续 S7/R7 容量切片应测量 bridge 字节、峰值内存和
p95 结算时间，再以完整历史为 oracle 验收 checkpoint/cursor 归约，覆盖较旧 ACK、
缺失归属和 Turn 去重。不能为使结算成功而归档/截断历史或提高阈值。

## 有效 Turn 复核节奏：验收进行中

同一个 open Todo 跨越多轮已结算工作时，按已完成 Todo 数触发可能长期延后复核。
降低历史运行记录阈值不能解决它：有 classification 的记录不等于已接受的结算。
候选实现复用 work-item 历史归约器，对每次投影的一份不可变快照调用 quota
结算读取 owner；Python 只传递显式节奏及来源，不解释回执。

Goal 显式选项使用独立的 `replan_after_effective_turns` 字段，既有完成 Todo
配置保持原单位。隔离的真实 CLI 验证覆盖 open Todo 连续结算、未扣记写回不计数、
扣记缺少回执不计数、有效负证据计数，以及 quota/写回义务 ID 一致。
peer ACK 和重试仍由既有历史 owner 处理；未启用新选项的调用保持旧行为。

候选配置在既有 cadence capability 内扩展了带显式计数单位的机器 v1 schema；
读取 v0 时保留原存储与单位。打包后的中英文设备/Goal 编辑器已通过真实、锁定 revision
的 handler，覆盖旧值回读、v1 迁移、非法输入不写入、Goal 覆盖及清除后恢复继承，
并检查窄屏和键盘焦点。共享浏览器 fixture 在接收写入前声明并验证隔离的同步目标；
仅设置 HTTP server runtime 不足以隔离源 registry 的路由。

exact-source 读取复用既有 quota admission，并借用外层 refresh 锁；过期实例的
ACK 不能清空当前实例计数，缺失或过期 admission 会被拒绝。这只验收共享读取
边界，不表示新增 source-session 产品入口。

合成批量测试暴露了每次显式读取前重复过滤全量历史的成本，V8 剖析将其定位到
quota readback owner。先复用既有 Turn 索引再过滤，消除了重复扫描；最新 Turn
推断仍读取完整的 owner 历史。同输入 1,000 Turn 热态样本从 177–195 ms 降为
13–19 ms；这些本地样本不代表全量 p95，也不证明求解分数提升。

Harbor/SForge 现可通过既有 Goal CLI 传递显式有效 Turn 节律，并在执行前核对
持久化的单位及数值。适配器复用一处配置/回执映射，Python 不增加计数决策源。
隔离真实 CLI 的 bootstrap、覆盖、回滚已通过；适配器检查覆盖旧默认值、
不支持的 profile 和错误单位读回。完整 provider 安装执行、维护者合并及匹配
效果实验仍待验收，活动实验保持冻结。SForge planned task entry 是另一项尚未
闭合的适配器流程。
