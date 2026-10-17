# What Counts As A Good Smoke / 什么是好的 Smoke

A smoke is a thin, executable proof that a shipped public path still honors a
durable invariant. It is not a substitute for a unit test, a snapshot of every
field, or an archive of one successful run.

Smoke 是对已交付公开路径的一次轻量、可执行验证，用来证明该路径仍遵守稳定不变量。
它不能替代单元测试，也不应成为所有字段的快照或某次成功运行的档案。

## Start With The Contract / 从合同开始

Keep a smoke when it protects at least one of these surfaces:

- shipped CLI or cross-module runtime behavior;
- a reusable public contract;
- a public/private or authority boundary;
- a regression that previously stranded automation; or
- a representative end-to-end fixture that smaller tests cannot cover.

只有当 smoke 至少保护以下一种表面时，才值得长期保留：已交付的 CLI 或跨模块运行时
行为、可复用的公开合同、公开/私有或权限边界、曾让自动化卡住的回归，或小型测试无法
覆盖的代表性端到端 fixture。

Write the invariant before writing the script. If the rule is pure, put its
decision table and invalid cases in `tests/`; add a smoke only when crossing the
real CLI, serialization, state, or process boundary provides additional
evidence.

先写清不变量，再写脚本。纯规则的决策表和非法状态应放在 `tests/`；只有真实 CLI、
序列化、状态或进程边界能提供额外证据时，才增加 smoke。

## Review Checklist / 审阅清单

| Question / 问题 | Good evidence / 好的证据 | Warning sign / 风险信号 |
| --- | --- | --- |
| What is the oracle? / 语义 oracle 是什么？ | Expected outcomes come from an independently reviewed rule or protocol. / 期望结果来自独立审阅的规则或协议。 | The script copies current output into its expected value or refreshes a golden after every change. / 脚本把当前输出复制成期望值，或每次变更都刷新 golden。 |
| What boundary is exercised? / 验证了什么边界？ | The test invokes the shipped entry point and asserts a small semantic result. / 测试调用已交付入口并断言少量语义结果。 | It calls only an internal helper while claiming CLI or end-to-end coverage. / 只调用内部 helper，却声称覆盖 CLI 或端到端行为。 |
| Is the result deterministic? / 结果是否确定？ | Synthetic fixtures, fake clocks, explicit state, and bounded retries make the result repeatable. / 合成 fixture、fake clock、显式状态和有界重试保证可重复。 | Wall-clock speed, network luck, provider text, or process ordering decides semantic success. / 墙钟速度、网络运气、provider 文本或进程顺序决定语义成败。 |
| Is the assertion durable? / 断言是否稳定？ | It names the transition, authority rule, boundary decision, or terminal condition. / 断言状态转换、权限规则、边界决策或终止条件。 | It freezes incidental field order, dated prose, a temporary builder shape, or an output tail. / 固化偶然字段顺序、过期文案、临时 builder 形状或输出 tail。 |
| Is the fixture public-safe? / fixture 是否公开安全？ | Stable synthetic ids, reason codes, digests, and redacted projections are sufficient. / 稳定合成 id、reason code、digest 和脱敏 projection 已足够。 | Raw task text, trajectories, logs, verifier output, credentials, private links, or host-local paths are required. / 依赖原始任务、trajectory、日志、verifier 输出、凭证、私有链接或宿主路径。 |
| Does it have an owner and cadence? / 是否有 owner 和运行频率？ | The smoke is focused locally and assigned to an appropriate PR, canary, daily, or release lane. / 本地聚焦运行，并归入合适的 PR、canary、daily 或 release 通道。 | Every smoke becomes PR-fast, or a high-risk surface is daily-only with no targeted profile. / 所有 smoke 都进入 PR-fast，或高风险表面只有 daily 覆盖且没有定向 profile。 |

Wall-clock limits can enforce an explicit performance budget, but elapsed time
should not decide whether a state transition or safety rule is correct. Use a
fake clock or semantics-derived fixture for that decision, and keep any live
provider check explicit and low-frequency.

墙钟限制可以保护明确的性能预算，但耗时不应决定状态转换或安全规则是否正确。此类
判断应使用 fake clock 或从语义推导的 fixture；真实 provider 检查应保持显式且低频。

## Prefer Semantic Pressure / 优先验证语义压力

A single happy path is rarely enough for a rule with precedence or authority.
Add the smallest negative, mutation, or metamorphic case that would fail if the
important rule were inverted. Examples include proving that an unrelated gate
does not change the selected agent, a lower-level compatibility field cannot
override final scheduler authority, or a repeated receipt does not duplicate an
effect.

对于具有优先级或权限语义的规则，单一 happy path 通常不够。应增加最小的反例、
mutation 或 metamorphic case，使关键规则一旦被反转就会失败。例如：无关 gate 不应
改变选中的 agent，底层兼容字段不能覆盖最终 scheduler 权限，重复 receipt 不能重复
产生 effect。

Assert only the fields needed to prove that invariant. Shape and redaction
checks are useful, but they are separate from the semantic oracle.

只断言证明该不变量所需的字段。形状与脱敏检查仍有价值，但不能替代语义 oracle。

## Consolidate Without Losing Coverage / 合并时不丢失覆盖

Similar names or shared setup do not prove that two smokes protect the same
contract. Before removing or combining a smoke:

1. write one sentence naming the invariant owned by each candidate;
2. identify its public entry point, negative cases, cadence, and targeted
   profile;
3. treat exact-content duplicates and direct nested execution as review
   candidates, not automatic deletion instructions;
4. extract shared fixture or subprocess setup into the existing canary harness
   when that reduces duplication without merging semantic assertions;
5. remove wrapper-to-smoke subprocess calls so each tracked contract runs once
   and remains independently selectable; and
6. rerun the focused entries plus fleet or catalog checks to prove that the
   inventory has no coverage gap.

名称相似或 setup 相同，并不能证明两个 smoke 保护同一合同。删除或合并前，应分别写明
不变量、公开入口、反例、运行频率和定向 profile；把完全相同内容和直接嵌套执行当作
审阅候选，而不是自动删除指令。可以复用 canary harness 中的 fixture 或 subprocess
setup，但不要因此合并不同的语义断言。去掉 smoke 调用另一个 smoke 的 wrapper，让每
个合同只运行一次且仍可独立选择，最后重跑聚焦入口以及 fleet/catalog 检查，确认清单
没有覆盖缺口。

The repository's smoke-fleet health work in
[#2259](https://github.com/huangruiteng/loopx/pull/2259) established the
review-candidate rule. The scheduler cleanup in
[#2265](https://github.com/huangruiteng/loopx/pull/2265) removed nested
execution while retaining separate qualification profiles. The todo cleanup in
[#2115](https://github.com/huangruiteng/loopx/pull/2115) shows the complementary
pattern: share harness plumbing while preserving each smoke's contract.

仓库中的 smoke-fleet health 工作 [#2259](https://github.com/huangruiteng/loopx/pull/2259)
确立了“只生成审阅候选”的规则；scheduler 清理 [#2265](https://github.com/huangruiteng/loopx/pull/2265)
移除了嵌套执行，同时保留独立 qualification profile；todo 清理
[#2115](https://github.com/huangruiteng/loopx/pull/2115) 展示了互补做法：共享 harness
基础设施，但保留每个 smoke 的合同。

Do not remove a slow integrated smoke merely because it is slow. First measure
which phases create the cost and prove that smaller tests cover the same
end-to-end boundary. Keep the integrated case when it still supplies unique
evidence.

不要仅因 integrated smoke 很慢就删除它。应先测量成本来自哪些阶段，并证明较小测试
覆盖同一端到端边界；只要它仍提供独有证据，就应保留。

When a smoke grows beyond roughly 500 lines, re-check whether it is really one
test. This is a review prompt, not an automatic deletion or failure threshold.
Move reusable behavior into its product owner and keep the smoke thin. Large
integration smokes remain appropriate when smaller tests cannot cover their
real end-to-end adapter contract.

Do not retain one-off smokes whose main purpose is to assert the exact text of
a dated research note, candidate ranking packet, temporary run review, or
transitional benchmark decision. Keep that information in the research document
and cover shared invariants with a data-driven aggregate smoke.

Smoke 超过约 500 行时，应重新检查它是否混合了多个测试；这不是自动删除或失败阈值。
将可复用行为放回产品 owner，使 smoke 保持轻量；较小测试无法覆盖真实端到端 adapter
合同时，仍可保留大型 integration smoke。不要用一次性 smoke 固化过期研究笔记、候选
排名、临时运行评估或阶段性 benchmark 决策的精确文案；这些信息留在研究文档，共有
不变量交给数据驱动的聚合 smoke。

### Benchmark Boundaries

Keep focused public/private boundary smokes such as
`examples/benchmark-candidate-source-boundary-smoke.py`, and toolkit permission
and integrity smokes while they validate the shipped provider-neutral contract.
Add active benchmark smokes only for stable toolkit behavior; experiment-specific
validation belongs with the research workspace. Benchmark-native runners,
adapters, ledgers, scoring reducers and dated packets stay outside the active
product surface. Follow [archive placement](../../benchmark/README.md#archive-placement):
retired implementations and dated packets belong under `deprecate/benchmark-legacy/`;
only the explicitly qualified immutable snapshots may remain under `benchmark/`.
Neither archive category enters active CI benchmark execution.

保留公开/私有边界，以及已交付 provider-neutral toolkit 权限和完整性 smoke。
新增 active benchmark smoke 只保护稳定 toolkit 行为，实验专属验证归研究工作区。
Benchmark-native runner、adapter、ledger、scoring reducer 和过期 packet 不进入活跃
产品面；退役实现和过期 packet 归 `deprecate/benchmark-legacy/`，满足规范条件的不可变
快照才可留在 `benchmark/`。两类归档都不参与 active CI benchmark 执行。

## Public-Safe Fixtures / 公开安全 Fixture

Use temporary repositories, fake transports, synthetic records, and compact
receipts. Clean up processes and temporary state deterministically, and keep
stdout/stderr tails out of committed fixtures. A fake provider can validate
serialization, sanitization, and fail-closed handling; it cannot qualify real
provider behavior.

使用临时仓库、fake transport、合成记录和紧凑 receipt。确定性地清理进程与临时状态，
不要把 stdout/stderr tail 提交进 fixture。Fake provider 可以验证序列化、脱敏和
fail-closed，但不能证明真实 provider 行为合格。

Follow [Public/private boundaries](../public-private-boundary.md) whenever a
smoke touches benchmark, host, agent, or external-system data.

当 smoke 接触 benchmark、host、agent 或外部系统数据时，遵循
[公开/私有边界](../public-private-boundary.md)。

Benchmark smokes must never require raw task text, trajectories, logs, verifier
output tails, credentials, uploads, leaderboard submissions, or local private
artifact paths.

Benchmark smoke 不得依赖原始任务文本、trajectory、日志、verifier 输出 tail、凭证、
上传、leaderboard 提交或本地私有 artifact 路径。

## Validate The Change / 验证变更

Run the narrowest meaningful checks first, then add risk-based coverage:

```bash
python examples/<area>/<focused>-smoke.py
loopx canary premerge --from-git-diff
loopx check --scan-path examples/ --scan-path tests/ --scan-path docs/development/
git diff --check
```

先运行最小且有意义的检查，再根据风险扩大覆盖。报告实际运行的命令、失败或跳过项、
manual hold，以及为什么这些证据足以覆盖变更面。完整 smoke 分层与发布门禁见
[Testing and quality](testing-and-quality.md)。
