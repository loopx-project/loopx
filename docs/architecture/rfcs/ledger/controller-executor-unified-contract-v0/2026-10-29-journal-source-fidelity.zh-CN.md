# C1 Journal 来源保真检查点

English mirror: [matching checkpoint](2026-10-29-journal-source-fidelity.md).

C1 集成 main `a651a824996cce29cc69a5e7e4437b241eef963d`，复用独立普通
[PR #7657](https://github.com/loopx-project/loopx/pull/7657) 的
`c7042f28e01bae93f7960ad165fe6f4d2d847bed`。这是提案源码集成，不代表已合入或已安装。

实测缺口是完整来源经过既有 Turn journal 持久化后，JavaScript 序列化将 `1.0`
变成 `1`、丢失负零并舍入大整数；Python 普通相等判断会掩盖第一种差异。在生产
native Turn 路径中使用合成宿主，以精确 JSON 比较能够在修复前复现失败，原始 capture
则保持完整。数值来源 hash 试验还暴露了上下文重建中的数值归一化；孤立的 hash
拒绝测试不足以认证该试验。本次没有采用该试验，也没有修改历史 hash codec。

Python 现以文本运输原始 journal JSON。既有 TypeScript journal owner 解析并检查
快照的 lifetime、effect、状态转换和锁边界，再通过既有原子 writer 发布同一文本。
原生 object caller 保留原编码；没有新增状态转换 owner、journal schema、历史重写
或权限放宽。C1 复用同一实现，不在各恢复 caller 中分别补回数值。

独立切片的四个数值用例在原 main 失败；修复后六个来源数值用例及既有 lifetime
用例通过。Journal／executor／恢复 cohort 为 178 个 Python 用例通过，另有 106 个
生产 CLI 捕获／恢复用例使用合成宿主，53 个 TypeScript 用例通过。该源码的类型、
Ruff、公开边界扫描和本地标准 premerge 通过；C1 最终精确 head 的集成结果记录在其 PR。

RPC 仍为一次。同一含引号和换行的合成正文，在 8 KiB 下请求参数从 9,837 增至
11,002 字节，在 64 KiB 下从 71,708 增至 80,418 字节。磁盘紧凑编码较小，但 wire
转义增加运输和内存成本，并减少未调整的 2 MiB 请求上限内余量。没有延迟、模型 token
或长程净收益声明。

原 C1 继续在普通、温态和缺失 baseline 的上下文中建立一致的数值来源身份，再推进
hash、caller／writer 切换。原 quota 单会话语义、成果和完整成本对照及冻结预算失败
继续开放，可选双会话另行评估。该前置切片是可独立使用的恢复修正，不结清这些要求。
