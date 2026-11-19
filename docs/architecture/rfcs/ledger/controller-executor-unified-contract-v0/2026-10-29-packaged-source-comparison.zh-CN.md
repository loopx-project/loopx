# 打包来源保真与 C1 尚未闭合的成本差距

English mirror: [matching checkpoint](2026-10-29-packaged-source-comparison.md).

本检查点支持[单会话对照](../../controller-executor-unified-contract-v0.zh-CN.md#9-验证与验收)。
它验证有界工程负载中的来源交付与恢复，不证明效率净收益，也不结清 C1。

## 版本与负载

原 quota 固定为 mainline `b201da8bab305d053866aa0d4516b97ba4a4499e`，
统一契约提案固定为 [#7430](https://github.com/loopx-project/loopx/pull/7430) 的
`3afc2d547f6f9ca41368fd4f3f34d8f8dae9edcf`。这些是已测版本，不代表后续
mainline 变化已合格。两份隔离 wheel 分别通过自身前端源码指纹校验；各有 1,857 个
打包产品文件与对应源码一致，不含另经 bundle 校验的生成 Chat 资源。两组均使用
Python 3.14。

每组使用同一合成 active Goal、完整已选任务及末尾停止条件、一个当前 Agent 成果，
以及五个 peer 成果，每个成果四条要求。真实 File 与 SQLite authority 分别在可丢弃
runtime 初始化。原始 24 条描述、owner、工作关联与全局停止条件保留，不以活跃 Goal
或已安装 host 为 fixture。

打包 CLI 执行 thin heartbeat bootstrap、一次已准入 App heartbeat guard、保存文件
读取、原生 lifecycle hook、暖态投影、压缩恢复、拒绝对照、写回/扣额及其重放。
Hook 事件为合成事件，实际执行的是打包 hook 和 CLI。确定性观察器为验证等价而读全量
来源；它不是模型，不是建议每轮读取全部 peer 正文，也不证明模型保留。实际发出的暖态
delta 另经读取，使用摘要校验后的 baseline 与当前文件重建。

这是新的同负载对照，不是历史 13,000 字节实验的重跑或替代。
此前 **25,862 > 13,000** 的失败保持不变。

## 来源与恢复结果

两组均保留已选任务、24 条要求及全局停止条件，拒绝提前扣额。恢复原 capture 后，
无需再次准入即可读回同一来源；重复写回和扣额只提交一次 effect、扣一次额度，原任务
保持 open。

Mainline 通用 codec 把已准入来源的 `quota.compute` 从 `1.0` 变为 `1`；两个后端的
冷态、暖态恢复和丢 baseline 精确来源断言均失败，其原生冷态投影也携带 `1`，而非
已准入的 `1.0`。C1 在这些路径保留原值。这实际复用了另行提案
[#7661](https://github.com/loopx-project/loopx/pull/7661) 与
[#7657](https://github.com/loopx-project/loopx/pull/7657) 的来源保真修复，
没有建立 caller 专用数值规则。独立提案及 C1 采用，仍与 mainline 或安装采用分开。

两个原生暖态文件均采用 `captured_file` 连续性。实际 delta 精确重建当前文件；
缺失 baseline 或损坏 delta 时恢复其完整正文。这证明保存文件重建，不证明模型保留。
Mainline 的重建相对于已经归一化的投影精确，并不修复此前的来源丢失。

仅改变数值写法的 capture 负例区分了两种完整性边界。C1 中把 `quota.compute` 从
`1.0` 改为 `1`，仍能通过独立完整契约的 JavaScript 归一化 hash 校验；但使用原始
字节 SHA-256 读取已改文件会被拒绝，恢复原字节后可继续读取。这个 caller 的精确字节
capture 边界通过验证；独立契约的数值承诺仍开放。两种校验互不替代，也不证明任意
Host 输入已合格。

前三次作者运行仍记为失败：第一次用非 active Goal 发起 refinement，第二次超过既有
512 字符要求描述限制，第三次向 `context-project` 传入被禁止的固定输出目录选项。
修正后的同负载组使用合成 active Goal、有界完整描述与既有 output-root 选项，未修改
产品限制、断言或验收标准。

## 已测成本与决策

每个组/后端各一次观察，字节数如下：

| 测量 | File：quota → C1 | SQLite：quota → C1 |
| --- | --- | --- |
| Guard 首次 stdout（文件清单） | 5,413 → 5,440 | 5,429 → 5,456 |
| 冷态保存投影 | 65,756 → 68,411 | 65,866 → 68,519 |
| 冷态全读 CLI stdout | 70,568 → 73,533 | 70,680 → 73,643 |
| 实际暖态 delta 文件 | 3,761 → 3,755 | 3,769 → 3,763 |
| 完整观察器 CLI stdout | 482,505 → 494,464 | 483,299 → 495,250 |

暖态的六字节差异包含 fixture 路径及 surface 拼写，不能证明编码收益。

每组观察器均执行 38 次 CLI 调用，包含 fixture 编写、全量恢复、拒绝及读回。暖态文件
独立验证每组另加一次 CLI 读取；进程内 codec 检查每组另有九次粗粒度调用，C1 还多做
一次独立 hash 负例检查。这些是明确计入的资格验证成本，不是正常 Turn 的必读策略。

38 次调用的耗时之和，File 为 10.08 → 10.98 秒，SQLite 为 9.41 → 9.57 秒。
顺序执行的单次样本、进程/broker 启动及所选全读观察器，不能证明时延效果。暖态补充
计时包含 CLI 和 codec 校验，不是 CLI 时延测量。内存、模型 token/cache/billing、
验收成果、已安装 App 行为和人工注意力成本仍未测量；构造与失败作者探测也仍是成本，
完成后的对照组不能抹除它们。

本负载的有效变化是来源保真与更完整的契约，同时增加了少量冷态和累计输出字节，
没有证明净成本下降。继续复用既有 source/context/journal owner，优先交付独立可用
修复 PR；根据实际 caller 失败选择下一项 hash 或 bridge 改动，不从孤立断言推导出
需要另一份全量来源 carrier 或平行 hash 策略。数值完整性、caller/writer 切换、历史
reader 退出和原 quota 的决策/成果/完整成本对照继续开放；可选双会话研究另行验收。
