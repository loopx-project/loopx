# Tracker 阶段验收与有条件字段退役

当前读回见[登记来源处理](2026-10-12-registered-source-treatment.zh-CN.md)。下文保留原基线观测，不代表当前 PR 状态。


状态：**提案**，不是迁移完成或批准记录。基线：
`36134771355f05c9bcc5657ce739bb86426383dc`。
交付状态与有序工作计划归 [#4447](https://github.com/loopx-project/loopx/issues/4447)
维护；[讨论 #4738](https://github.com/loopx-project/loopx/discussions/4738)
提供切片规格及纠正。第 11 节承载 M3 契约修改提案，本条目记录其证据。

旧 tracker 要求删除第一个字段以证明有用，把手段当成了结果。在本基线上，
`protocol_action_packet` 仍有现行 writer 和兼容消费者；
[#4794](https://github.com/loopx-project/loopx/pull/4794) 已实现 writer 退休，
但尚未合入本基线。其语义字段投影仍承担 Envelope/签名兼容职责。句法 reader
数少既不能证明字段冗余，也不能授权删除历史读取。可以保留派生兼容投影，同时
消除重复构造或独立决策权威。

建议的 tracker 阶段为：

1. 在集成树上验证已交付的守卫边界和选定的生产路径简化。fresh packet 构造不得
   重复：采用 PR-05 迁移时，新 quota/live/paused/recovery 输出不携带 packet，
   同时保留历史 v0 reader 与签名投影；未采用迁移时，保留字段最多在最终 live
   阶段渲染一次。已结算工作不得重复构造/花费，未准入选择不能获得结算能力，
   inbox 来源优先级保持不变。保留合法 workspace repair 和独立 capability effect。
   单分支本地测试不能认证集成结果。
2. 对 26 个已登记词表闭合来源，不假装每个值都来自内部 producer。基线上，六个
   runtime producer 条目与一个 compatibility-only 条目构成报告中的 F1/F2 7/26；
   仍有 19 个 cross-runtime 词表不在该验证范围内。按 settlement/receipt、
   workspace/Todo、其余有界 owner 的顺序追踪。内部生产需要真实见证，外部输入
   需要 decoder 合法/非法输入证据，兼容项需要保留范围和退出条件。local-only
   分类必须证明不跨边界。只贴标签或给 unknown 改名不能获得证据信用。
3. 在同一最终版本上复审，记录通过/失败/未测试边界；修订的验收范围获准且证据
   满足后才能关闭。协议格式和签名继续受保护。全程序分析、所有字段删除、F6
   全面持久兼容证明和未登记词表治理不属于这个有限交付 tracker。

本提案不修改 F1/F2 公式或强制执行域、不增加来源 schema、不降低预算，也不宣称
19 项已验证。来源分类与生产验证必须保持为两种声明。任何新的来源证据契约实现
都需要单独评审及负例；已合并的 settlement-binding 见证只是 pilot，不是第二阶段全部。

当实际消费者/权威简化收益和版本化兼容方案具备时，仍可启动 M3。在此之前保留字段
及其检查。不要仅为了字段名归零而保持 tracker 打开，也不要把缺失的来源证据改名为
文档工作后关闭。
