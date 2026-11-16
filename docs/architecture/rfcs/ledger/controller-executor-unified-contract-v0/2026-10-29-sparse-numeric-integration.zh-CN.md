# 共用稀疏数值运输与显式 binding 升级

English mirror: [matching checkpoint](2026-10-29-sparse-numeric-integration.md).

这是 [#7430](https://github.com/loopx-project/loopx/pull/7430) 中的 C1 提案工作，不是
安装采用或默认 writer 切换。Runtime `b9d1ce1847589de6e5ecab546b91631fb20e2083`
采用 [#7661](https://github.com/loopx-project/loopx/pull/7661) 的 collector 与 context
回归，来源 commit `3435ef65854ede411ca494f73da18b168d404362`。该独立前置保留原
owner 和评审路径；C1 不复制第二套 collector。

## 复用与兼容

既有 Python IO collector 只为整数值 float、带符号零及超出 JavaScript 安全范围的整数
提供元数据；普通小数 double 无须逐项重复路径／token。Context delivery、完整组合、
capture／replan 检查及 Host／driver 消费者共用它。来源身份和 hash 仍归 TypeScript，
没有第二 Python digest、上下文选择器、cache 或恢复规则；一个局部 codec 常量同时
用于组合与校验。

去掉冗余 token 会改变先前实验 hash 身份。对照确认，直接换成稀疏 collector 会让
原样有效的 v1 契约发生完整性校验失败。因此新 C1 binding 显式声明
`context_source_numbers_v2`。七条完整消费路径在依赖工作前，将正确签名的实验 v1
输入拒绝为不支持，而非按 v2 静默重解。同一 v1 产物仍被固定在
`e18d46db51c58ee17c770d7bba915f0133cf9f78` 的 reader 接受，字节不变；不能用重签
来源迁移未完成工作。历史 v0 hash、Turn key 和 receipt 保留原 decoder。

## 同负载运输证据

两个隔离 Python 3.14.8 wheel 各有 1,860 个产品文件与固定源码匹配：基线 `e18d46db5`
与 runtime `b9d1ce184`。成本 probe 给两组同一份不可变合成来源，包含 1,000 项观测和
一项变化；统计组合、校验、Host、冷态／差分和恢复共 12 次调用的双向 JSON。对应的
安全整数对照不变。

| 完整消费者负载 | 基线 request / response | 稀疏 request / response |
| --- | --- | --- |
| 安全整数 | 373,452 / 14,345 字节 | 373,452 / 14,345 字节 |
| 普通小数观测 | 944,548 / 14,345 字节 | 401,456 / 14,345 字节 |

小数组件总量从 **958,893 降至 415,801 字节（56.6%）**；比例成本回归在原样基线失败，
稀疏 runtime 通过。原值、类型与完整来源精确保留；binding codec 和 hash 有意变化。
这些计数不含协议 framing、准备、模型处理、时延、内存、费用和人工注意力。

第一次 probe 分别生成来源，其指纹不同，保留为诊断，不作匹配对照。取得资格的重跑
使用同一不可变来源，并核对全部四个组合输入指纹；公开回归也只生成一次配对模板。

## 真实打包恢复与剩余资格

同一六成果／24 要求合成负载经真实 File／SQLite CLI、native hook、不可变 capture
检查、压缩恢复、提前 spend 拒绝及一次结算运行。四组等宽 namespace case 的既有
21 条来源／恢复判据全过；两版均拒绝数值语义 mutation。读取实际 warm 文件后精确
重建当前来源，缺失 baseline 或损坏 delta 时恢复同一全文。Warm 文件保持
**File 3,839 字节、SQLite 3,847 字节**。

每组观察器 38 次 CLI，另加一次实际 warm 文件读取。Cold capture 的 File 为
68,934 → 68,928 字节，SQLite 为 69,042 → 69,032；观察器 stdout 分别为
497,567 → 497,543 和 498,353 → 498,313。Runtime 观测值仍有变化，这些微小差异不能
归因于 codec 或模型输入节省；先前不同宽 namespace 的运行另行保留。这是 C1 前后
工程对照，不能代替原 quota 成果／完整成本门。

`b9d1ce184` 的受影响 Python ladder 471 过；固定测试模板后，两条运输测试再次通过，
产品代码不变。选定 TypeScript owner ladder 94 过，TS 编译与 Ruff 通过，两条显式
Python 类型目标在 silent imports 下通过。默认 import 展开在两版均报告 623 文件中
5,487 个错误，行号归一化后无新增独立错误；这项失败与先前较窄的 27 错 cohort 分列。
语义 advisory 未发现受支持的 vocabulary carrier，但不覆盖这次有意的字面 codec
升级，也不证明全部语义形式已检查。

Ark Host 在该 runtime 上使用先前准备的精选 SDK 依赖，39 条 mock transport 协议
fixture 也通过；完整可选 SDK 安装及缺失的 MemT tokenizer／runtime profile 仍未取得
资格，不据此认定 live provider 或模型运行已通过。

原 **25,862 > 13,000** 失败保留。Caller／writer 切换、旧 reader 退出及原 quota 可比
验收成果／完整成本仍开放；本检查点不启动模型、不执行 live 安装或合并、不提升默认
或授予新权限。
