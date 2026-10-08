# PR #5944: technical coverage and product delivery judgment

Recorded 2026-10-08. Public sources:
[recovery design issue #5940](https://github.com/loopx-project/loopx/issues/5940),
[RFC PR #5944](https://github.com/loopx-project/loopx/pull/5944).
This case is a distilled comparison, not a transcript or a reward label.

| Published judgment | API target commit | Head declared in the review body | Review frame |
| --- | --- | --- | --- |
| [Machine APPROVE, 06:23:53 UTC](https://github.com/loopx-project/loopx/pull/5944#pullrequestreview-5452444228) | `f4c21235dfa6695a7592c8023c9a150268e28f48` | `4d98703445fb64e413f2cfb274617fc932b851cf` | Eight design criteria; owner authority, inert recovery, fencing and replay safety. User experience was declared inapplicable to a docs-only diff. |
| [Machine APPROVE, 08:18:28 UTC](https://github.com/loopx-project/loopx/pull/5944#pullrequestreview-5453719155) | `f4c21235dfa6695a7592c8023c9a150268e28f48` | `1741770661c990eab7d74814fd85ee2246910182` | Updated design/roadmap alignment and prior validation; the product journey remained outside the judgment. |
| [Maintainer-directed model REQUEST_CHANGES, 08:53:20 UTC](https://github.com/loopx-project/loopx/pull/5944#pullrequestreview-5454100835) | `f4c21235dfa6695a7592c8023c9a150268e28f48` | `f4c21235dfa6695a7592c8023c9a150268e28f48` | First useful recovered Goal, earlier minimum packaged journey, and useful post-recovery result with attention/maintenance evidence. |

The final review was authored by a model under maintainer direction; it is not
an independent purely human adjudication. Account names alone do not establish
who reasoned about a PR. Preserve declared actor/model provenance in the linked
reviews. API target commits and body-declared heads differ in the approvals;
therefore this is not a controlled same-head baseline/treated comparison.

The review capability's `check-result` can accept either internally consistent
verdict. Its success certifies packet/body consistency and required structure,
not evidence truth, product value or agreement with a maintainer. The subsequent
checked Request changes therefore does not contradict a successful checker.
It exposes a narrower earlier judgment and a newly explicit product frame.

The three actionable changes requested were:

1. Bound the first outcome to one local Goal and explain the user's recovery
   problem, rather than treating complete state coverage as sufficient value.
2. Include minimum packaged verification/readback with M1, and recovery/failure
   interaction with M3, instead of deferring the user journey to M5.
3. Verify useful work after recovery, human attention and maintenance cost;
   reuse existing typed owners and keep refactoring bounded.

At recording time the revised RFC, implemented recovery journey and resulting
benefit were **unverified**. No recall/application receipt from the earlier
reviews was available. Neither a terminal reward nor a positive/negative memory
utility observation can be derived from this disagreement alone. Subsequent
resolution belongs to a new reviewed revision with new evidence, preserving
these historical facts.

The [machine-consumable experience](loopx-project/loopx/pr-5944-v1.json) retains
the procedure and its stop condition without historical verdict labels. Its
real consumer is the opted-in PR review packet; see [operation/readback](README.md).
Self-repair reuses `review_outcome_continuity_gap` to repair the missing review
frame. Reward Memory owns reusable advice and its separate utility evidence.

## 中文判断

自动评审主要回答“八项技术设计是否覆盖”，后来的人机评审明确要求回答
“第一阶段让用户得到什么、如何操作、恢复后是否产生有用结果”。
`check-result` 通过只证明声明与格式一致，不证明产品判断正确，因此它可以通过
一份一致的 APPROVE，也可以通过一份一致的 Request changes。

经验正文属于现有 reward memory 的 `procedural_experience`；self-repair
修复漏掉完整用户结果的评审工作流。人机判断是新的证据与口径，不自动成为金标准。
原评审没有记忆采用链路，不能由 verdict 差异倒推出 memory utility。
当前只确认公共评审分歧、修改要求与可复用过程；RFC 修订后的效果仍待验证。
