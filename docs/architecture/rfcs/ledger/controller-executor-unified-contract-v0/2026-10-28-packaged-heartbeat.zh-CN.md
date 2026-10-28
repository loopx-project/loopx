# 打包单会话 heartbeat 对照

本文是 [controller/executor RFC](../../controller-executor-unified-contract-v0.zh-CN.md#9-验证与验收)
的测量检查点，不代表 C1 验收或提升默认。[英文镜像](2026-10-28-packaged-heartbeat.md)
记录相同范围和结果。

## 来源与负载

以 main `843be494266774b63b3de61d0adf5dd7f4f877a2` 的原 quota 对照
[#7430](https://github.com/loopx-project/loopx/pull/7430) 集成候选，候选运行时代码
固定为 `1199cbe0b8e92ddf845e345483041486fe808024`。两份 wheel 分别按自身源码构建
前端指纹，各自 1,884 个打包产品成员与来源工作树一致。未安装全局 runtime，也未拿
活动 Goal 状态做实验夹具。

两臂使用同一合成负载及真实隔离 File、SQLite authority：一个活动 Goal、完整选中任务、
一项当前 Agent 成果与五项独立 peer 成果，每项四条要求。全局意图、权限及停止条件
仍可读取。候选在已有 native context opt-in 中展开自身成果并索引其他成果；正文仍可
从原 capture 精确补读，展开后核对全部 24 条完整要求、owner、关联工作和未验证状态。

打包 CLI 路径执行 thin heartbeat bootstrap、quota/native context guard、全部必读
上下文分页、强制 Goal 来源读取和真实 context-hook 命令。以合成 native 生命周期
事件建立温态连续性，再通过 PostCompact/SessionStart 失效。丢失后读取**同一原始
capture**，不替换准入；来源篡改和提前记账被拒绝，refresh/spend 重放保持一次 effect
及一次扣额，未完成的选中任务仍开放。另核对温态重建与缺 baseline 的完整回退。

这属于模拟宿主连续性下的打包工程观察，不证明正式 Codex App 采用、模型保留或决策、
SDK 覆盖、任务验收成果，也不是 controller/executor 双会话实验。

## 观测

只有一组匹配 cohort，每臂、每后端、每阶段一次；宿主后台负载未受控。耗时为实际 CLI
调用之和，包含启动、必读 Goal 和生命周期观察。请求数是 Python 到 runtime 的逻辑
请求，不推断物理 framing、重试和 provider 内部 IO。产物字节指已交付 UTF-8 上下文，
不是 tokens 或完整 stdout。

| 后端／阶段 | 上下文字节，原实现 → 候选 | 逻辑请求 | 耗时 ms |
| --- | ---: | ---: | ---: |
| File／冷态完整 | 75,387 → 76,162 | 103 → 102 | 5,052.49 → 4,867.53 |
| File／温态差分 | 40,991 → 43,558 | 99 → 98 | 3,240.32 → 3,399.05 |
| File／丢失恢复 | 74,619 → 75,390 | 6 → 6 | 4,045.91 → 4,213.20 |
| SQLite／冷态完整 | 75,483 → 76,270 | 103 → 102 | 4,566.19 → 4,428.81 |
| SQLite／温态差分 | 41,085 → 43,666 | 99 → 98 | 3,236.50 → 3,416.50 |
| SQLite／丢失恢复 | 74,713 → 75,498 | 6 → 6 | 4,149.99 → 3,957.72 |

两臂冷态／温态／恢复均为 13/9/13 次 CLI。失效本身另计两次 hook 调用，本组耗时
683–738 ms。候选展开全部五项 peer 成果每阶段增加 13,940 字节正文、五页 reader 和
五次观察 hook：**10 次 CLI／10 次逻辑请求**、17,620 字节 stdout、3,416–3,713 ms。
原实现已经内联这些正文。

完整阶段 stdout 也增加：File 冷态 94,662 → 96,082 字节、温态 58,077 → 60,649、
恢复 89,807 → 91,144；SQLite 分别为 94,776 → 96,208、58,185 → 60,771、
89,911 → 91,262。每后端另有七次结算／负例／读回调用，两臂均为 226 次逻辑请求，
同后端 stdout 合计相同。初始化、构建、失败作者探针与可选补读单独计量，不从历史抹去。

合并 acceptance/title/Explore 投影减少一次逻辑请求及 bridge JSON。例如 File 冷态
请求／响应正文从 965,261/585,535 降到 902,963/494,206 字节。这一局部下降**没有**
抵消更大的交付产物和可选补读；两后端温态耗时均上升，恢复结果混合。本样本不支持
因果提速、默认 SQLite 性能资格或完整成本正向 diff。Tokens、cache、账单、验收成果、
人工注意力与长程净益仍未资格化；此前负向和混合测量继续保留。

## 归属缺口与接续

相对原 quota，scope 去掉约 13 KB 的 peer 正文，但候选仍保留约 6.6 KB 的重复 Todo
诊断列表，并新增约 3.9 KB scope 元数据；候选列表及其他诊断重复消耗了剩余节省。
这是组合／压缩缺口，不能据此认定 peer scope 已降低总成本。

该检查点选择的后继 C1 切片是刻画原 quota 中必要的诊断压缩，迁入既有共享 typed composer，并删除
被替代的 Python caller 算法。保留完整选中工作、portfolio/horizon、claim/lease、全局
义务、未知字段、精确引用与历史恢复。旧 Python compactor 会丢弃未知列表和缩短部分
正文，原样复制不满足此合同。继续用同一打包对照分别测默认读取与全 peer 补读，再判断
是否改善；没有理由新增 packet 格式或逐字段 RPC。

集成候选采用已合入的 route owner（#7520）及 reference owner（#7457）。旧投影的
acceptance、Task 标题和 Explore 引用合用既有 `reference_context` 操作，删除独立
acceptance 操作和重复 controller-input dispatch；完整组合复用相同语义函数。这只是
有界代码收敛，不是全部 writer 切换、历史 reader 退役或 C1 完成。

最终受影响测试 469 通过、五项可选 native/SDK 跳过；116 项 typed 测试及 control-plane
类型检查通过。此前构建指纹、缺依赖、harness carrier 和过时调用数断言失败保留为作者／
验证成本，不静默改成产品通过。本对照未调用模型、付费评测、managed benchmark，
未执行 live 安装、发布或合并。

## 共享诊断 owner 的接续结果

后续 `d33c1a19d91738c66073d9e71586f14466f36d89` 以
`quota/diagnostic_projection.ts` 替换 Python Todo 列表算法。现有
`reference_context` 调用方让 should-run 和整组 status/plan 行复用它；scoped
native composition 在已有请求内部调用相同函数。
[#7557](https://github.com/loopx-project/loopx/pull/7557) 在 main
`19c213324fbb5990288bd374c1b79096f22bb152` 上以
`5b5d9dd3b0a816aae9d5160b849cff4a3c38da13` 提交可独立使用的旧读取方切片，
为正常 PR；其 typed 模块与集成版本完全相同。本次未合并或安装任一提案。

仅明确已知的诊断 lane 且全部为已知规范 Todo 记录时可以有界投影。条目字段／版本未知、
非记录条目或复杂嵌套内容会保留整个列表；未知列表保留。留下的记录全文不变，不缩短
或重建 portfolio 正文、能力候选、planning horizon、选中工作和原 succession IDs。
原 capture／恢复仍完整。投影元数据指向已有 source capture，不在各 lane 重复长恢复命令。

复用同一冻结打包 harness 和原 quota commit，按相同路径长度、合成任务及 24 条完整
要求重跑一组匹配 cohort。基线 wheel 的 1,884 个产品成员、候选的 1,885 个成员分别
与来源一致。测量绑定上述 rebase 前 commit；随后 rebase 到 `19c213324` 只带入
无关的已合入 Lark 返回修复，不把冻结实验改称后续 head 的测量。

| 后端／阶段 | 上下文字节，原实现 → 候选 | Stdout 字节 | 逻辑请求 | 耗时 ms |
| --- | ---: | ---: | ---: | ---: |
| File／冷态完整 | 75,648 → 70,388 | 95,004 → 89,875 | 103 → 102 | 4,959.03 → 6,475.69 |
| File／温态差分 | 41,252 → 44,029 | 58,401 → 61,183 | 99 → 98 | 3,465.08 → 4,538.62 |
| File／丢失恢复 | 74,880 → 69,618 | 90,113 → 84,903 | 6 → 6 | 5,108.18 → 5,762.51 |
| SQLite／冷态完整 | 75,740 → 70,492 | 95,114 → 89,997 | 103 → 102 | 5,771.16 → 5,856.03 |
| SQLite／温态差分 | 41,346 → 44,133 | 58,509 → 61,301 | 99 → 98 | 3,772.95 → 3,794.99 |
| SQLite／丢失恢复 | 74,974 → 69,722 | 90,217 → 85,017 | 6 → 6 | 4,226.47 → 5,113.92 |

两臂 CLI 次数仍为 13/9/13。候选各阶段展开全部五项 peer 仍增加 13,940 字节正文、
17,620 字节 stdout 及 10 次 CLI／逻辑请求；补读耗时 4,107–4,875 ms，原实现已
内联这些正文。七次结算／负例／读回仍为 226 次逻辑请求，同后端 stdout 相同，
保持一次 effect／扣额和开放任务。构建、初始化、失败探针、两次失效 hook 和补读
各自保留成本记录。

冷态／恢复正文约少 5.3 KB，但温态约多 2.8 KB，全部 peer 消费仍更贵。本组每个
候选阶段的观测耗时均更长；单次观测和未控制的后台负载不能支持因果延迟估计。
中间版本重复 capture 命令，更大（File 温态 45,187、SQLite 45,303 字节）；
修元数据不抹去该实验，也不证明净收益。

四组冷／温 File／SQLite 展示与各自原始 capture 独立核对完整选中工作、boundary、
horizon、capability gate 和 portfolio。故意删除 capability gate 的 QA wheel
经实际打包 guard／reader 路径被拒绝；这验证 oracle 敏感性，不证明模型遵从。
来源篡改、提前 spend、同 capture 丢失恢复及一次结算的正负例保持。

最终集成受影响集合 144 项 Python、117 项 typed 通过；较早的扩大集合为 533 通过、
五项可选跳过，发生在加强未知条目回退之前。独立 #7557 为 264 项 Python／70 项 typed
通过，含真实 File／SQLite status/plan/detail 和规范 Todo 回读，14 项选中 premerge
检查均过。Status/plan 新增一次粗粒度 typed 请求；显式完整 Todo 摘要跳过它。
拥挤诊断单元夹具因保留完整候选增至 43,862 字符，是有意的信息增量，不是提速。
未修改的公开 v4 多 Agent 预算探针在原 quota 通过，在前一版及当前集成 wheel 均为
27,934 > 13,000 字符而失败；该集成失败保持开放，未扩大公开预算。

下一步沿现有 C1 诊断温态重建／元数据成本，保留义务，再验收剩余消费者和 writer
切换。不宣称完整成本正向、默认 SQLite 性能、正式 App／模型保留、token/cache/账单、
验收成果或长程收益。C1 及独立的 C2／controller 实验继续开放。
