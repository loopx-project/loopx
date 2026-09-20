# 生产者值域的文字跟随实际走过的集合

对 F1/F2 所声称的内容属规范性变更；当前源码树上没有任何检查的判定改变。改变的是：
人所审计的那句话，与检查器实际走过的集合，不能再各说各话。

- **两个答案同时为绿。** 给 `settlement_binding_kind` 配上可执行 witness 后，它成为
  第一个声明 producers 的 `cross_runtime` 词表，`check_producers` 会走它，F1/F2 报
  `7/26`。而同一份注册表仍写着 `Kernel(V)` 是唯一声明 producers 的层，F2 的正文也
  仍说 `cross_runtime` 层不声明 producers。机器读者与人类读者对"F1/F2 证明了什么"
  得到两个不同答案，而 122 个焦点测试与 25 个 CI 检查在两种说法下都通过。
- **这是结构性缺口，不是疏忽。** `check_invariant_domain` 会把 `quantifies_over`、
  `verified`、`registered` 钉在由注册表推导出的计数上，机器域因此不会腐化；
  `statement` 与 `evidence` 却是没有任何检查读取的自由文本。于是扩大走过的集合，
  在数字上是一次性失败，在声明上却是静默的。
- **值域按谓词命名，不按层命名。** `Producers(V)` 是声明了 producers 的子集：所有
  `tier: kernel` 词表，加上其他层中携带可执行生产证据的词表。这正是
  `check_producers` 访问的集合，因此当非 kernel 层的词表凭证据进入时，名字不必
  重写。
- **声明自带边界。** F1/F2 明确写出留在域外的部分——仍未声明任何 producer 的 19 个
  `cross_runtime` 词表——而不是只写已验证的 7 个。只陈述已验证的一半，会让未验证的
  余量在没有任何 diff 说明的情况下悄悄缩水。
- **这种一致性是被检查的。** `examples/semantic-vocabulary-drift-smoke.py` 中的
  `check_domain_prose` 推导出走过的集合，并拒绝与之矛盾的文字：一旦有非 kernel 词表
  被走过，生产者值域的 statement/evidence 就不得再以 `Kernel(V)` 为界；也不得在某层
  已有成员声明 producers 时宣称该层不声明 producers；每条 statement 还必须写出留在
  域外的数量。已用突变验证：恢复 `Kernel(V)` 的 universe、把跨运行时否认句加回 F2、
  把 F1 的 `19 cross_runtime` 换成含糊措辞，三者都会让冒烟变红，
  `tests/architecture/test_semantic_vocabulary_drift.py` 中的两条回归把它们钉住。
- **RFC 正文自身也带着同一条矛盾。** 两个语言版本的形式模型章节都把 F1/F2 写在
  `Kernel(V)` 上，并重复了跨运行时否认句，现已改写到 `Producers(V)`。2026-09-17 那条
  最早把不变量收敛到 `Kernel(V)` 的附录条目保持原样：它是只追加的历史，记录当时准确。
