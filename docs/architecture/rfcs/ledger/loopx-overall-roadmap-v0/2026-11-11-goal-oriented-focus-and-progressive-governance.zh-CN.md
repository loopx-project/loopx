# 目标导向聚焦、零仪式感渐进式入口与结构性护城河（2026-11-11）

[English](2026-11-11-goal-oriented-focus-and-progressive-governance.md)

本条目记录依据 `main` `bf8a66eb9c`（`2026-11-11`）对
[`loopx-overall-roadmap-v0.zh-CN.md`](../../loopx-overall-roadmap-v0.zh-CN.md)、
[`target-yardstick-stride-decisions-v0.zh-CN.md`](../../target-yardstick-stride-decisions-v0.zh-CN.md)
与
[`long-horizon-harness-benchmark-research-program-v0.zh-CN.md`](../../long-horizon-harness-benchmark-research-program-v0.zh-CN.md)
进行的一次结构清晰度强化与基线边界对齐修订。

## 缺口

面向执行 Agent 与贡献者的路线总纲和研究／决策 RFC 存在两类需要显式收敛的漂移风险：

1. **控制面仪式膨胀与脆弱提示词微操：** LoopX 内核需要保持严格、唯一 owner 的类型化边界
   （`AuthorityStore`、CAS、`claim/lease`、`receipt`、`Turn` 围栏、`Stride/Yardstick` 记录），
   但若缺乏明确的渐进式入口准则，实现切片容易在无变化的日常单 Agent 轮次上叠加前置治理表单、
   额外 RPC 往返或手写提示词微操，在基座模型持续变强时反而拉低净长程效率。
2. **`2026-10-23`–`2026-11-11` 合并边界滞后（`F7` 单一真相纪律）：**
   `研究与验收集成后的当前优先级` 此前仅停留在 `5550e91bab`（`2026-10-23`），且第 4 节部分行尚未同步
   `main` 至 `bf8a66eb9c`（`2026-11-11`）的真实源码边界：包括
   [`complete-state-recovery-v0.zh-CN.md`](../../complete-state-recovery-v0.zh-CN.md)
   中已落地的有界 M1 `backup-state verify` 与打包 Settings 只读审计、Yardstick 引用测量 canonical
   前缀复用（`#8382`）、Stride 有界 Explore/排序反转投影（`#8385`）、
   [预算内研究规划说明](../../../../research/budgeted-research-planning.zh-CN.md)与三阶段策略演进边界
   （`#8441`、`#8461`）、T02 可回读历史工作集提案（`#8450`，
   [`history-working-set-context-policy-v0.zh-CN.md`](../../history-working-set-context-policy-v0.zh-CN.md)）、
   `main` 上带受信 `--validation-command-json` 与原唤醒恢复的 EdgeBench 单 worker `--worker turn-resume`
   入口（`#8499`，对应的 C1 集成分支验证见 `#8480`）、受因果前置阻塞的静默等待（`#8491`）、
   语义重规划恢复的 Todo 绑定保留（`#8495`），以及恢复 `generate_rfc_status_index.py --check`
   所需的 [`self-update-explicit-runtime-route-v0.zh-CN.md`](../../self-update-explicit-runtime-route-v0.zh-CN.md)
   （`#8470`）页头与索引登记。

## 修订内容

1. **内核重、入口零仪式感与渐进式治理披露（`loopx-overall-roadmap-v0` 第 1、7 节；`target-yardstick-stride-decisions-v0` §5.5）：**
   明确日常上手与无变化单 Agent 轮次保持零前置治理仪式感（类 `git init` 极简入口、日常无竞争任务静默单 owner
   claim/lease），受治理控制项仅在真实触发条件（中断恢复、端云交接、多 Agent 争用、显式审批门、多轮空转、
   代理与权威评测排序反转）命中时渐进展开，并将控制面 token、RPC 往返与人工打断计入完整预算 $B$。
2. **三大复利结构性护城河（`loopx-overall-roadmap-v0` 第 1 节）：**
   明确工程与研究避免投入会被强基模原生吸收的脆弱手写提示词微操，聚焦于单次前向推理之外的三项结构能力：
   - 端云异构连续性与权限围栏（`S2/S4/S9`、`R5/R6`）；
   - 确定性静默 Checkpoint 与同状态反事实接续底座（`S4/S10/S11`、`R5`、`T01/T03`，$\tau_B(s; a, a_0, \pi)$）；
   - 闭环轨迹归因记忆与预算内计算选择飞轮（`S6/S7/S11`、`T01–T04`，`当前模型试验 -> 校准预期 -> 学习预算分配`）。
3. **面向终局目标的执行罗盘、研究评估准则与可组合阶段收敛（`loopx-overall-roadmap-v0` 第 7 节；`target-yardstick-stride-decisions-v0` §5.8；`long-horizon-harness-benchmark-research-program-v0` §6.2、§8.7、§11.2）：**
   统一以完整成本记账下的净终局验收成果 $\Delta J(B \mid s_0) = \mathbb{E}[Y_B^\pi - Y_B^{\pi_0} \mid s_0]$
   评估切片与实验，强化从期限 $B$ 倒排确认窗口及邻域范围化 yardstick 保真度（拒绝整类评测器永久全局黑名单），
   要求不断积累的决策与定量研究方法论（多点局部保真度、确认倒排、成对反事实比较、机制岛屿演化选择）把共享阶段不变量收敛到同一类型化方法论内核而非重复堆叠编解码样板（配套提案 `#8516`），
   并明确字段、已读回执、图边、重规划计数、逐叶 RPC 迁移和追加带日期段落均不构成成果。
4. **对齐 `main` `bf8a66eb9c`（`2026-11-11`）边界（`loopx-overall-roadmap-v0` 页头、第 2、4 节）：**
   在保留 `5550e91bab` 历史基线事实及全部既有规范条款的前提下，更新 `当前优先级`、S7 与第 4 节中
   `complete-state-recovery-v0`、`history-working-set-context-policy-v0`、
   `target-yardstick-stride-decisions-v0` 和 `long-horizon-harness-benchmark-research-program-v0` 的状态边界。

## 本条目没有确立什么

- **不改变任何运行时、wire schema 或默认配置：** 本次文档修订未修改任何 TypeScript 或 Python 运行时逻辑、
  M1 wire 字段、quota 规则或 benchmark 默认 runner。
- **不主张已测得收益或晋升资格：** `#8382`、`#8385`、`#8441`、`#8461`、`#8480`、`#8491`、`#8495`、`#8499`
  均严格按已验证源码／工程接缝记录，`#8516` 仅作为开放的配套收敛提案引用；它们均不代表数值 yardstick 保真度、
  EdgeBench 静默 checkpoint 分叉恢复等价、E3/C2 净收益（$\Delta J(B \mid s_0) > 0$）、真实宿主等待节拍、
  M3 重新激活、T02 历史策略交付或 C1/C4 默认晋升已经成立。
- **不触碰并行在途契约：** `controller-executor-unified-contract-v0` 与 `shared-goal-authority-state-provider-v0`
  保持原在途分线推进，不在本条目内产生交叉修改。

## 验证

```text
uv run --extra test python scripts/generate_rfc_status_index.py --check
uv run --extra test python scripts/check_rfc_bilingual.py --base origin/main --head HEAD
uv run --extra test python examples/docs-governance-smoke.py
uv run --extra test python -m pytest -q tests/architecture/test_rfc_status_index.py tests/canary/test_rfc_bilingual.py
uv run --extra test loopx canary premerge --from-git-diff --git-diff-base origin/main
```
