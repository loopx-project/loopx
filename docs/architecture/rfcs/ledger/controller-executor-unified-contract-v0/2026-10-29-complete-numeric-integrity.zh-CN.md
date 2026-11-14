# 完整契约的数值完整性与 Host 运输

English mirror: [the same checkpoint](2026-10-29-complete-numeric-integrity.md).

这是 [#7430](https://github.com/loopx-project/loopx/pull/7430) 中提议的 C1 集成，
尚未切换 mainline 或安装入口。对照基线为
`4173cc2ee7ef94de00ca777ff8600455c080c29f`，已集成 main `33d9ecd9fd29bce4986da6c9e9a6b758a24f91b3` 和前一份
来源对照 ledger。数值 binding 实现在 `974cd09ef7cb12832911ee58880aaf25e86fb2b2`；
最终 runtime `e18d46db51c58ee17c770d7bba915f0133cf9f78` 同时退役无用的来源 echo。

## 同一来源身份贯穿实际消费者

前一份[打包对照](2026-10-29-packaged-source-comparison.zh-CN.md) 发现：完整契约接受
`1.0 → 1`，原始 capture 字节校验却拒绝变更文件。新实验 binding 显式声明
`hash_codec=context_source_numbers_v1`，来源与内容承诺复用既有 TypeScript 来源身份，
包含稀疏 producer 数值编码，区分 JavaScript parse 可能合并的整数／浮点、负零及大
整数。精确字节布局及同一 producer 值的其他写法仍由独立 capture 字节边界负责。

| 消费者／owner | 收敛方式 |
| --- | --- |
| `diagnostic_projection.ts::contextSourceIdentity` | 跟随保留／搬移的值；删除的 carrier 不在投影身份中留下数值 token。没有 metadata 的既有 caller 保留原 canonical identity。 |
| `turn_envelope.ts` 组合与完整验证 | 同一数值 binding 供 replan、capture、Host 检查及 driver lineage 复用；完整路径不再在每个接缝重新计算归一化整份 decision hash。 |
| Python Turn 运输 | 从原始值派生稀疏数值编码，并应用既有来源 edits；不新增第二套 digest、选择或恢复规则。 |
| `host_candidate.py::complete_host_input` | 复用紧凑的 `host_context` 检查，删除无调用方的 `validate_complete` 来源 echo operation，保留原模型来源值。 |

缺失／不支持的 codec 或缺少原数值运输信息在依赖工作前拒绝。旧实验完整 binding 须
使用版本固定的 reader，原 capture 不改写。历史 v0 hash、Turn key 与回执 decoder
保留既有行为。该 binding 增加完整性检查，不授予执行权限或新增模型侧视图。

## 验证与剩余失败

五种数值 mutation 在未修改基线上失败，修复后通过七类完整消费者。补充验证覆盖不
支持的 codec、丢失数值运输、真实 File/SQLite replan 文件拒绝／恢复、畸形引用、
原 capture 恢复及一次结算。Capture 拒绝保留原始观察诊断与 error code。统一失败
writer 保留 collection error，不调用旧 runtime renderer 或制造完整契约。

`974cd09ef` 的受影响 Python ladder 457 过。最终 runtime 的退役 ladder 220 过；
一条新增断言最初把既有“不支持 operation”消息的词序写反，纠正后该用例单独通过。
最终 TypeScript ladder 149 过。修改文件的类型检查通过；扩大至八文件仍有 27 个继承
错误，基线为 32 个，没有新增错误，不据此宣称完整类型资格。保留初始 442 过／13 败
集成运行：它暴露 capture 诊断、旧 fixture hash 参数及过期失败格式假设；纠正后仍
保留原语义拒绝断言。

合入 main `3b4641c4fc0ec314d347f74b05880bb516012049` 后，产品源码仍与上述最终
runtime 相同。原生 file-manifest、选择及压缩／恢复测试 198 过，46 条 native binary
opt-in 用例仍跳过；external scheduler／worker ladder 183 过。Ark Host 在私有依赖
目录使用 `arkruntime` 0.8.0 和 mock transport，39 条本地协议 fixture 通过。完整可选
SDK profile 的 service 依赖展开已被限时停止；这组精选 fixture 不证明该安装 profile
或 live provider。MemT Host 缺少 tokenizer／runtime 依赖，无法收集 fixture。两条可选
live provider 及模型行为均未取得资格。

两份隔离 Python 3.14 wheel 各有 1,860 个产品文件与固定源码一致，生成的 frontend
assets 分别校验。同一合成六成果／24 要求负载经真实 File/SQLite CLI、native hook、
压缩、损坏恢复、提前 spend 拒绝及一次结算运行。每组既有 21 条来源／恢复判据均过；
基线仍接受纯数值语义 mutation，最终 runtime 拒绝。实际 warm 文件精确重建，缺失
baseline 或损坏 delta 恢复同一全文；未用 live Goal、已安装 Host 或模型作 fixture。

## 成本与下一边界

| 打包观察 | File：基线 → 修复 | SQLite：基线 → 修复 |
| --- | --- | --- |
| 冷态保存视图 | 68,407 → 68,602 字节 | 68,515 → 68,706 字节 |
| 实际 warm delta 文件 | 3,755 → 3,775 字节 | 3,763 → 3,783 字节 |
| 完整观察器 CLI stdout | 494,448 → 495,559 字节 | 495,234 → 496,329 字节 |

每组观察器 38 次 CLI，另加一次实际 delta 读取。九次进程内 codec operation 和一次
独立完整性 control 属于验证工作，不是模型强制读取策略。总字节包含 package／fixture
路径长度差异，增长不能全部归因于 codec 字段。这是 C1 修复前后对照，不能替代原
quota 对照，也不改变先前冻结的 **25,862 > 13,000** 失败。

独立 Host 消费者使用同一份 68,227 字节合成来源，两版均一次 RPC。Request 参数 JSON
从 108,061 增至 108,150 字节，result JSON 从 68,504 降至 207 字节。原任务、事实及
约束精确保留，新 contract binding 本身增加 41 字节；两份安装至隔离环境的 wheel
均复现。这里计参数／结果 JSON，不含协议 framing、组合／准备、模型输入处理、时延、
内存及完整 Turn 成本；删除无用 echo 不等于完整路径结果正向。

继续沿原 C1 owner 推进剩余 consumer／writer 切换、历史 reader 退出及可比验收成果／
完整成本。保留既有独立来源保真 PR 与负责人；本检查点不提升默认、不启动实验，
也不结清 C1。
