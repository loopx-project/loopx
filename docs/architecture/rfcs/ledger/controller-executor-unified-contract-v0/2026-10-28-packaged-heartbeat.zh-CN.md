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

## 搬移上下文的集成

Runtime `167b31e5d078193b9fd73ad6609804e268390972` 已 rebase 到 main
`f205c02a37d5a668c956733a721af911dcac7fab`，采用现有
[#7528](https://github.com/loopx-project/loopx/pull/7528) 的 codec 变更
`759c16a9cf430bb7442b47dd3a5c3336ffb93666`，保留主干的偏好作用域绑定、原生 checkpoint
夹具及智能中介设计。没有新增第二编码器；精确基线子树加字面当前覆盖，替代对未变证据
反复引用长路径。未知及当前控制仍内联，原 capture、身份和恢复保持，scoped wrapper
回归覆盖相同实现。

24 条合成记录搬移，对比该 runtime 的不可变父版本
`87e216f24f966671d35e035df7ba285248b48bad`：普通完整差分 13,199 → 6,090 字节，C1
scoped wrapper 差分 13,809 → 6,230。冷态／当前完整编码分别保持 20,060/20,188 及
20,167/20,295 字节。两者精确重建，实际旧 decoder 对新 overlay 恢复相同当前全文；
原缺失 overlay 用例在采用前失败。这只是 codec 观察，不含模型保留、tokens、framing、
账单或任务成果。

未改打包 harness 再以原 quota `843be494266774b63b3de61d0adf5dd7f4f877a2` 为基线运行
一组匹配 cohort；两臂夹具和路径长度相同，1,884/1,885 个产品成员与各自来源一致。
本组结果绑定本 runtime，之前表格仍属于各自原版本，不重新归属。

| 后端／阶段 | 上下文字节，原实现 → 候选 | Stdout 字节 | 逻辑请求 | 耗时 ms |
| --- | ---: | ---: | ---: | ---: |
| File／冷态完整 | 75,211 → 69,934 | 94,432 → 89,286 | 103 → 102 | 5,961.28 → 5,183.22 |
| File／温态差分 | 40,817 → 43,575 | 57,861 → 60,624 | 99 → 98 | 3,981.29 → 3,592.23 |
| File／丢失恢复 | 74,445 → 69,164 | 89,603 → 84,374 | 6 → 6 | 5,052.05 → 5,156.02 |
| SQLite／冷态完整 | 75,311 → 70,044 | 94,550 → 89,414 | 103 → 102 | 5,205.45 → 6,393.69 |
| SQLite／温态差分 | 40,913 → 43,683 | 57,971 → 60,746 | 99 → 98 | 3,482.77 → 4,216.01 |
| SQLite／丢失恢复 | 74,541 → 69,272 | 89,709 → 84,492 | 6 → 6 | 4,649.12 → 5,590.37 |

CLI 次数仍为 13/9/13；全部五项 peer 补读在候选每阶段仍增加 13,940 字节正文、17,620
字节 stdout 和十次 CLI／逻辑请求，观测耗时 4,046–9,431 ms。七次负例、结算及回读
仍为 226 次逻辑请求，同后端两臂 stdout 相同。篡改／提前 spend 拒绝、24 条全文、同
capture 恢复、一次 effect／扣额及开放任务通过。四组来源核对完整保留 selected work、
boundary、horizon、capability gate 和 portfolio；新 QA mutant wheel 删除能力候选后被
真实 guard／reader 拒绝。计时 cohort 没有其他自有构建或测试并发，宿主后台负载未控；
单次观察不认证延迟或 p95。

最终 typed 集合 147 项及 typecheck 通过。受影响 Python 运行保留 299 通过、51 项可选
native／SDK 跳过，以及长临时 namespace 下两项恢复文本尺寸失败。同路径受控比较在
不可变 main 和本 runtime 均复现两项 2,015 字符失败；两者钩子正文不内嵌原要求全文，
均保留原恢复命令。正常路径的 lifecycle 套件另有全部 145 项通过，没有放宽预算、断言或夹具正文，
原失败记录保留。新 wheel 先遇到主干前端 bundle 过期，重建后打包成功。未改 v4 探针
仍为 27,934 > 13,000 字符而失败。

搬移冗余已减少，普通温态输入仍多约 2.8 KB，全部 peer 阅读仍更贵；后者是原 C1
组合／元数据的另一未解缺口。默认 SQLite 性能、正式 App／模型保留、token/cache/
账单、验收成果和完整长程净收益均未资格。沿原 C1 继续该缺口及剩余消费者，不以采用
本修复结清 C1、C2 或 controller 实验。

随后集成 rebase 到 main `0cfbf11dcea4ba7bc8fb13f06f1c915b63ce8fad`，其中已含 #7528，
重复 commit 因而退出。[#7547](https://github.com/loopx-project/loopx/pull/7547) 两项 Goal
来源提交保留原作者，12 个贡献的产品／测试 blob 与已接收的集成 head 完全相同。
主干 capture owner 仍在准入前 stage、准入后发布，发布后才安装原完整契约。删除未用
capture helper 后，进程级 fence 重新包住实际 Turn executor；既有占用 lane 用例在
修复前失败、修复后通过。

Runtime `6f30cc454f2dea7e77f0821a14a1deaf268efe2e` 的 wheel 含 1,885 个与来源一致的
产品成员。重基后的 typed 集合 148 项及 typecheck 通过。集成 Python 首次记录
185 通过、两项明确的 live-host 跳过及 12 失败：八项新增 staged 回读断言把 adapter
元数据与实际模型投影混比，四项 observer 仍记录 #7547 之前的 metadata-only 操作。
保留对真实 Host 输入的完整比较，观测更严格的 dispatch 操作后，54 项 Host-capture
全部通过，没有放宽产品门；前次失败和其余已通过案例分别保留。

该 wheel 再通过打包 File/SQLite bootstrap、完整 Goal 交付、全部条款／详情读取、同
capture 恢复、拒绝及一次结算。本次功能验证与其他检查并行，不是新的匹配计时 cohort；
旧成本表仍绑定 `167b31e5d`，不改称后续源码的测量。当前版本完整成本与成果资格保持
开放。

随后已提交版本的 premerge 暴露维护性失败：executor 有 94 条语句，超过既有 90 条
限制。Runtime `82f93ab76a403c2633efedb2f8bf02f85be8e3c6` 将已发布输入的安装收回
现有 staged-capture owner，保留恢复绑定与发布顺序。其 1,885 个产品成员与来源一致，
54 项 Host-capture 及未改动的维护性 ratchet 均通过。原失败保留，没有新增例外或改
阈值；本次 owner 收敛不把历史温态输入及 v4 预算失败改判通过。


下一次集成将保留的 C1 历史 rebase 到 main
`6f8f476388645fcea2269329d32974b559d490e6`，采用
[#7557](https://github.com/loopx-project/loopx/pull/7557) 的修复版本
`eb4e9b970aeb1ffb83e46705d6386cc3a4679ead`。该独立切片原先把整个 Python 输入经
JavaScript JSON 往返，展示字段之外的 `1.0`、负零及安全整数范围外的值可能改变。
九项独立的原数值案例在修复前失败。现在共享 typed owner 仅返回展示变更及不可变
来源引用，Python 在原值上应用，不拥有裁剪规则。独立切片通过 305 项 Python、99 项
typed、typecheck、Ruff 及其标准 premerge；这些是源码及真实 File/SQLite CLI 检查，
不是安装或独立验收。

C1 runtime `7f35454b392e09ae574c5065c473ee27b5eee853` 使用逐字节相同的共享诊断
模块。其验收共享阶段原先克隆所有来源对象，导致搬移数值事实无法沿用来源引用；现在
只复制发生变化的已注册 carrier 路径。首次集成保留 253 通过、五项失败：三项暴露
上述克隆问题，两项旧边界夹具不满足完整 Goal 的 dispatch 要求，后两项在前一 C1 head
也失败。夹具现在将任务保留于规范 provider，完整交付非 Todo 的 Goal 内容，同时
验证当前原始契约 Host 输入与历史作用域别名及拒绝，没有放宽 dispatch。最终受影响
集合 258 项 Python 全过；110 项 typed、typecheck 和 Ruff 通过。旧 wheel 与匹配计时
cohort 仍绑定各自实际版本。本次源码接续不认证新的打包 cohort，不消除温态输入或 v4
预算失败，也不结清剩余消费者、reader 退役及实际模型／成果的完整成本。


额外的完整 Host 集合在后续 main 集成上保留 200 通过、两项失败：operation-host
oracle 仍预期折叠空白，而主干 [#7549](https://github.com/loopx-project/loopx/pull/7549)
已经去掉该行为。实际 `chat_agent.py` blob 与已核 main 基线完全相同。两类 transport
都检查原 prompt 字节，并保留 schema、重试和篡改断言后，54 项 Host-capture 全过。
此处修正测试 oracle，没有改变生产 transport，首次失败记录保留。

GitHub 随后报告 #7557 已合并为 `4b2b97b7b726d902a20969e3911fd15d15b4bb2e`，七个
贡献文件均与修复 head 相同。发生合并不能补足尚缺的新 head 独立评审或受保护操作
回执。上述 C1 资格仍绑定冻结的 main 基线，不隐含认证后续 main 变化；C1 仍是尚未
完成的作者集成，原有成本、消费者及退役缺口保持。

## 完整 Goal 复用与可独立使用的原生切片

C1 随后 rebase 到 main `f5e9be3671c1b5c80eba5c62fe4d617bfefd66d6`。Runtime
`a0cc4c14b` 精确复用不变的作用域阅读指令和 objective，wrapper 其他字段仍取当前值；
完整 Goal 正文也仅在既有来源完整性判定通过后复用。该判定原样移到
`goal_context_source.ts`，由 fulfillment 与 codec 共享。正文变化、控制字段、scope、
索引、归属、修订及未知字段仍按字面交付；畸形凭据或缺少基线不能隐藏必读内容。
这减少重复解释，没有再建上下文或决策 owner。

Source `ebdce8a49b4faa584571cfb688aa2c2a238a5860` 保留上述行为和原生完整 Goal
回归覆盖。重建 wheel 的 1,889 个产品成员与源码一致，没有仅存在于包中或不匹配的
成员。使用同一份未改动的私有 harness，与原 quota 基线
`843be494266774b63b3de61d0adf5dd7f4f877a2` 比较：namespace 等长，六项成果、
24 条要求、工作、停止条件及后端相同。两组都计入必需的额外 Goal 文件读取；恢复
还计入两个失效／启动 hook 输出，沿原 capture 读取，不重新准入。可选 peer 展开单列。

| 后端／阶段 | 原 quota stdout 字节 | C1 stdout 字节 | 调用，quota → C1 | RPC，quota → C1 |
| --- | ---: | ---: | ---: | ---: |
| File／冷态 | 94,892 | 90,918 | 13 → 14 | 103 → 103 |
| File／温态 | 58,289 | 52,474 | 9 → 8 | 99 → 98 |
| File／上下文丢失恢复 | 92,068 | 87,418 | 15 → 14 | 8 → 8 |
| SQLite／冷态 | 95,002 | 91,048 | 13 → 14 | 103 → 103 |
| SQLite／温态 | 58,401 | 52,598 | 9 → 8 | 99 → 98 |
| SQLite／上下文丢失恢复 | 92,178 | 87,540 | 15 → 14 | 8 → 8 |

调用数包含原生 CLI／hook 及必需的 Goal 文件读取，与 RPC 分列。此 cohort 的 capture／
delta 自身字节仍大于 quota；完整输入对照改善，是因为原 quota 还须另外读取 7,952
字节的 Goal。C1 可选展开五项独立 peer 成果，每个阶段仍另增 13,940 正文字节及十次
CLI／hook 调用。两后端的完整条款、精确重建、压缩恢复、篡改／提前扣额拒绝、一次
持久写回／扣额及重放均通过。这是确定性打包消费者检查，不是实际模型保留或成果验收。
单个 cohort 与后台工作并行，不认证延迟、模型 token/cache、账单、人工或完整长程
收益。历史温态负向观察仍绑定原先源码。

最终受影响集合集成的 205 项 Python、112 项 typed、typecheck、配置内 mypy 和限定
Ruff 通过。打包源码在未改动的历史 v4 oracle
（digest `d12a8843834aea84…`）仍为 27,934 > 13,000 字符而失败；早先 26,253 字符
观察使用另一份当时源码 oracle（`1b5d010a30e608c6…`），单独保留。正向的分页输入
比较不豁免或重标这两项失败。

可独立使用的 [#7575](https://github.com/loopx-project/loopx/pull/7575)，精确 head
`d980d621219b10353fdaeb058a1a0fd5714f4389`、基于 main
`2cdff65d886d13121ffd78dccd96dd7413ab9d01`，在现有 opt-in 原生路径完整交付 Goal
并精确复用温态正文，不依赖尚未合入的 C1。完整 Goal oracle 在 main 的 File、SQLite
均失败，修改后通过。175 项 Python、57 项 typed、typecheck、配置内 mypy、限定 Ruff
及打包真实后端恢复／结算通过。匹配的原生 stdout 温态少约 7.7 KB，但冷态及丢失恢复
各增约 1.2 KB 和一次 RPC。该取舍明确保留；这个正常作者 PR 不认证完整 C1 或提升默认。
C1 复用相同判定与原生来源行为，作用域 wrapper 复用仍留在 C1。剩余调用方、writer
切换、历史 reader 退役及实际决策／成果资格，沿原工作流继续。

## 同 capture 的 Todo 摘要列表复用

C1 随后 rebase 到 main `ffc90182bc2a896aa9980cf24dcf23a0e3276e3d`，其中 #7575
已合入为 `a6283d923e3c951443fea65ba1a855c4f7c6c335`。原生 Goal producer 和完整性
owner 直接采用，不复制。Runtime `4604ee479` 在既有 typed 诊断 owner 中加入精确
列表复用：相等且非空的已注册数组仅保留一份字面正文，在更小时用同摘要内的
`<lane>_ref` 引用。相等比较保留顺序、JSON 类型和未知条目字段；其他字段与候选集合
不变。完整 composer、action projection 和 native 展示共用此 owner，应用展示上限
前先展开引用。不新增状态读取、capture、准入或结算 owner。正文／引用歧义、引用链、
目标缺失和跨摘要目标均拒绝，包括内部契约 hash 有效的情况。旧 capture 仍可读取。

同一源码工作负载从修改前 27,934 字符降到修改后 25,862，少 2,072（7.4%），全部来自
agent summary；其他根字段长度不变。重建 wheel 的 1,889 个产品成员与源码一致，
独立运行未改动的 `d12a8843834aea84…` v4 oracle，仍为 **25,862 > 13,000** 而失败。
这是 opt-in 完整契约变体，不是默认 quota 输出预算；两项上限均未提高，也不称通过。
相同真实 File／SQLite producer oracle 在不可变修改前 `87c14a5aa` 失败，加入引用后
通过。集成通过 333 项 Python；五项须显式 native Codex 环境的用例仍跳过。125 项
typed、control-plane typecheck、配置内 mypy 和限定 Ruff 通过。另行纠正过期 bridge
测试：display edits 与 edits 比较，展开后的文档保留原语义断言。

同一未改动的打包 harness，再以等长 namespace 和全部必读 Goal 内容，对照原 quota
`843be494…` 与当前集成候选。此新 cohort 测量完整集成，其差异不能把列表修改与
rebase、先前 C1 工作分离归因。

| 后端／阶段 | 原 quota stdout 字节 | C1 stdout 字节 | 调用，quota → C1 | RPC，quota → C1 |
| --- | ---: | ---: | ---: | ---: |
| File／冷态 | 94,510 | 89,911 | 13 → 12 | 103 → 102 |
| File／温态 | 57,931 | 52,094 | 9 → 8 | 99 → 98 |
| File／上下文丢失恢复 | 91,720 | 87,048 | 15 → 14 | 8 → 8 |
| SQLite／冷态 | 94,622 | 90,638 | 13 → 14 | 103 → 103 |
| SQLite／温态 | 58,043 | 52,218 | 9 → 8 | 99 → 98 |
| SQLite／上下文丢失恢复 | 91,830 | 87,170 | 15 → 14 | 8 → 8 |

两后端保留全部 24 条要求、全量／差分重建、无需新准入的原 capture 丢失恢复、精确
可选 peer 补读、篡改及提前扣额拒绝、一次写回／扣额和重放，工作保持 open。完整
capture／delta 自身字节仍大于 quota；原 quota 另读 7,952 字节必需 Goal。可选五项
peer 成果每阶段仍另增 13,940 正文字节和十次 CLI／hook 调用。温态 RPC 响应字节与
丢失恢复的请求／响应字节增长。一次非受控 cohort 不认证延迟、模型保留／token／
cache、账单、人工或验收成果。剩余调用方替换、writer 切换、历史 reader 退役及实际
单会话决策／成果资格仍未完成；controller 模型比较另作后续实验。

## 主线集成与不可变 reader 身份

既有 C1 提案 #7430 集成 main `447c35ed6`，采用共享的原生渐进材料 planner、通用
capture selector、request-scoped settlement 及当前 request 授权。继续以这些 owner
为准，不新增决策或 schema。原生操作在相同 typed 边界内调用既有材料 planner：无需
材料编辑的输入保留一次 RPC；确需编辑时，仍在 capture 和编码前应用于原始 Python
JSON。C1 scope 回退保留完整来源。已签名完整 v1 的材料分阶段迁移尚未完成，其完整
回退不能称为已迁移或已压缩。

修复两处 reader 接线。通用 `context-read --select` 接续的 selection digest 通过既有
CLI 校验。历史 replan 命令在为 executor 展开诊断引用前，先绑定原始 capture 中的完整
输入；此前对展开视图取 hash，会使不可变原 capture 拒绝其生成的有效命令。两项修复
均复用既有 selector、reader 和 hash owner，不另行准入、记回执或扣额。篡改及不同
作用域的读取仍拒绝。

独立的无材料 RPC 次数 oracle 在冻结的初次集成上失败，修复后通过。新增重复 replan
读取 oracle 在完整来源复用 Todo 摘要列表时同样复现失败，保留原绑定后通过。修复后
初次修复源码 `5a770d183` 通过 160 项 typed、23 项真实 File／SQLite replan CLI、control-plane typecheck、
配置内 mypy 和限定 Ruff。既有 resumed-task 展示断言与完整输入断言同时保留；未为
通过测试而移除候选正文、未知字段或上限。

该版本随后运行更广的 37 模块 cohort，在第三项失败处停止：1,364 项通过、53 项环境
跳过，不能称为整套通过。其中两项断言仍读取旧的 detached Host 布局。修正后的
oracle 核对 canonical channel 中完整 Goal 字节和当前任务全文，同时单独保留历史
reader 的原必读及验证断言；签名 CLI 输入只排除 harness 另加的 `_returncode`。
第三项失败暴露普通任务缺少 Task／request 身份时被误判为等待；`281910a95` 恢复其
contract error。四项直接 File／SQLite 和缺失任务回归随后通过。同版本的局部宿主
准备提取通过原维护性上限；这不能证明执行与并发等价。

集成 `bca6350b2` 随后采用 main `ccf6aacc5`，包括 #7594 恢复输出指引、#7609 首次
guard 选择及 #7610 精确 Todo 记录复用。相同 native 操作接收既有材料 planner 的
纯引用编辑，保留原始 Python 值核对；不恢复第二套 heartbeat 视图或引用 owner。
163 项 typed 和 control-plane typecheck 通过；后续限定 caller／recovery 验证在
#7430 绑定各自实测版本记录。记录复用仍保留 main 的 required-view profile，不认证
已签名完整输入的材料分阶段交付。源码及 oracle 修正不倒改上述中断／失败 cohort。

后续 16 模块 cohort 同样在第三项失败处停止，843 项通过。一项新测试仍调用已替换的
私有 helper 签名，改用既有相同 typed-plan fixture，保留原开销断言。两项真实
File／SQLite busy-request 失败暴露作者提取时的错误：lane 装饰器留在准备 helper，
而非完整执行入口。修正后，执行锁继续覆盖准入、host 和结算。既有公共入口拒绝
oracle 在冻结 `fc4763098` 失败，修正后通过，同时核对 busy-request 重启／重放。
现在 premerge 在 executor、lane owner 或 CLI 执行入口变化时自动选择既有七项公共
入口测试，包含 quick 验证；九项限定 planner 用例核对选择及无关 context 编辑排除。
复用真实 fence oracle，不新增重复实现检查。先前自动检查通过不能认证错误的中间
执行入口；两组停止的 cohort 都不称为整套通过。

这是集成验证，不是新安装或模型行为。先前中断的 cohort、随后三项真实 reader 失败
仍保留原失败观察。既有成本表及 **25,862 > 13,000** 仍绑定原版本与状态；未提高上限，
不推断最新版完整成本收益。调用方／writer 迁移、历史 reader 退役及实际单会话决策、
验收成果和完整成本仍属 C1 工作，提案保持未完成。
