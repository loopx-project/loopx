# Todo 摘要展示后续验收（2026-10-27）

本记录补充[原退役记录](2026-10-26-todo-summary-display-projection.zh-CN.md)。生产边界仍是
`todo.context.page` 的摘要展示，以及 quota planning 内的直接组合；两个完整规划决策
都仍有必要。不新增 provider、格式、权限或 RPC owner。

## 保留此前证据

独立评审对照 `be1dc37a008be7017b64b622f653b6707df5c544` 与
`c242ca5f439c269c313329c883299a28d95a55dc` 得到：

- 已注册 `todo_decision_scope_outcome_v0` 的修正通过真实 File/SQLite 来源及
  wheel/sdist 安装验证；
- 一个 SQLite CLI fixture 请求为 83 → 82，其中 `todo.context.page` 为 22 → 21。
  它与原 N1/N3 的 75 → 74 负载不同；两者均不支持删除另一个规划决策；
- 固定 32 对 CLI 样本的基线/候选 p50/p95 为 336.97/521.98、345.96/836.79 ms，
  候选 p95 增加 60.31%；
- 预先声明的 64 对复测为 301.56/400.98、305.88/391.08 ms。后一次结果不能抹掉
  前一次失败；不一致尚未证明可重复回归，也没有解释先前尾延迟；
- 冷启动、温态 typed request、daemon 空闲/突发 RSS 仍缺失。Python 线程 profile
  没有测得 Node CPU，也未证明尾延迟成因。

上述历史批次保留原结论，不改写成通过，默认 SQLite 限值也不放宽。

## Rebase 与真实恢复

本次后续工作的基线为 `ff8d9932294e9004b9ffeb2fb473d00354397eb8`。六个 File/SQLite
恢复用例最初在目标断言前因 `canonical_authority_required` 失败。现在 fixture 先用
已写好的合成来源初始化真实 provider，再调用生产 CLI；没有恢复旧读取 fallback。
八个 receipt/next-action 用例全部通过，覆盖版本保留、错误 Todo 步骤更新拒绝、
原 Turn 合法结算与重放。之前六项保留为 fixture 失败，不声称先前已验证恢复。

## 最终 head 测量契约

[PR #7411](https://github.com/loopx-project/loopx/pull/7411) 记录不可变最终 head、执行命令、
fixture 指纹、环境与结果。有界离线验收在两个修订上使用相同解释器和合成 canonical
SQLite Todo，不修改活跃 Goal：

- 24 对受管进程冷启动，区分已温热的文件系统/编译缓存和新进程；
- 64 对完整 `quota should-run` CLI，AB/BA 交错、不删样本，按 nearest-rank 算 p50/p95；
- 每版本 256 次温态 typed request、256 次完整 quota-planning 投影，区分 transport
  与 Python facade；
- 2,000 次有界投影的 daemon 空闲、突发采样峰值和突发后 RSS；
- 真实 CLI 身份/结果断言、聚焦 TS/Python 与安装回读，与耗时分别报告。

计时期间不并行执行本 Agent 的重型构建/测试。原始私有记录留在 Git 外，只发布合成
汇总。单个合成负载不能证明持续大 Goal 性能。任何未达标保留 owner-review hold；
微基准更快不能豁免完整 CLI 失败。

## 同步随后合入的主线

最终集成基线更新为 `17b2a0f9b818dc6e6fe77ccc567b20ff91b3bc40`。主线已包含
#7459 的 canonical fixture 修复，也改动了真实 quota/context 路径。冲突解决沿用
共同 fixture 修复，不重复补丁；本 PR 保留 File/SQLite 的 receipt-version 回归。
此前 `ff8d9932294e9004b9ffeb2fb473d00354397eb8` 对照和
`e7a54b8f32a648124133b02e11404b75a421fdac` head 保留为历史证据。最终 PR 回复
记录新基线与新精确 head 的正确性、安装和匹配性能复测，不改写旧批次。
