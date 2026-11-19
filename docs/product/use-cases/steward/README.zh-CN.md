# 管家：一句话变成一个被确认的团队

状态：qualification 案例。它记录本机管家旅程今天在工作台面上证明了什么、哪几拍还没有证明，以及如何复现两者。它是产品侧使用说明，不是新能力、新契约或新调度器。

本文案的事实来源是一个确定性的浏览器场景（`examples/personal-workspace-browser/steward-journey.mjs`），全部跑在合成数据上。fixture 顶替了 agent turn；本文描述的每一句都是工作台面渲染出来的事实。

## 什么情况下适用

- 老板想用一句话启动工作
- 这项工作需要不止一个 Agent 或不止一条 lane，所以“有没有人干”本身就是答案的一部分；
- 老板想在同一个地方确认、纠偏、看结果。

## 旅程七拍

| 拍 | 老板做什么 | 界面显示什么 | 状态 |
| --- | --- | --- | --- |
| 1 | 看首屏 | Goal 看板四条 lane（需要你/执行中/观察中/已安排），每张 Goal 卡给出 Agent 与下一步那句话 | 已证明 |
| 2 | 在 Goal 对话里向管家提要求 | 管家的有界快捷提示行（询问下一步 / 向 Agent 获取进度报告 / 配置定时检查 / 看阻塞 / 查证据）点击即发送、不留草稿，这句话成为一个被接受的 Turn，被准入的团队计划卡落在同一个对话里 | 已证明 |
| 3 | 阅读计划卡 | 每条 lane 的 Agent、第一刀 Todo（含优先级与 action kind）、验收信号，以及一条明确“未配齐”并保留未派工工作的 lane；配额包络与停止条件；以及“确认才会建 lane”的说明 | 已证明 |
| 4 | 确认 | 恰好一次 apply、一次 durable write；结果逐条点名已分配的工作与落在哪条 lane，以及哪些没派出去、原因是什么，并说明这份结果不主张执行进度 | 已证明 |
| 5 | 检查到底谁能干活 | — | 缺口 1 |
| 6 | 暂停或撤销某条 lane | — | 缺口 2 |
| 7 | 等某条 lane 失败，问谁负责修 / 用什么判定完成 | — | 缺口 3、4 |

第 5–7 拍由场景以 typed gap 记录，并带上“探针找过什么”的证据：不是“这里先不做”的一句话，而是列出了查找的选择器和文本、以及实际找到什么。

## 推荐姿势

1. **说要结果，不要点将。** 一句话给出结果与约束，让计划卡来回答“谁来做”；先点名 Agent 会把协调变成老板的活。
2. **确认前先看四件事**：Agent、第一刀 bounded Todo、验收信号、缺人情况。看不到缺口的卡片还不具备可评审性。
3. **缺人 lane 是信息。** 未配齐的 lane 会保留它没能派出去的工作并给出原因，老板可以选择砍掉、补人、或接受部分交付。
4. **确认是一次落地的 durable 写入。** 确认只发一次 apply、只做一次 durable write；卡片不能在写入前声称 lane 已存在，写入后必须说明产生了什么。
5. **用回传结果判定交付，不要用对话判定。** 在缺口 5 关闭前，把对话当请求通道，把 Goal 自身状态当事实。
6. **在产生工作的那个对话里纠偏。** 对运行中的 Turn 纠偏今天已支持；对已确认 lane 承诺的纠偏还没有，所以不要确认一张可能需要撤回 lane 的计划。

## 如何复现

```sh
# 开发态台面
LOOPX_PERSONAL_WORKSPACE_SCENARIO=steward-journey \
  node examples/personal-workspace-browser-smoke.mjs

# 打包态工作台
LOOPX_PERSONAL_WORKSPACE_PACKAGED=1 \
LOOPX_PERSONAL_WORKSPACE_SCENARIO=steward-journey \
  node examples/personal-workspace-browser-smoke.mjs
```

运行会在 `output/playwright/personal-workspace/`（已 gitignore）下写出 `steward-journey-report.json`（拍子、缺口、探针证据）与每拍截图。不读取、不截取任何真实 Goal、Agent、凭证或本地路径。

## 已记录缺口与归属

| # | 缺口 | 场景记录的证据 | 归属面 |
| --- | --- | --- | --- |
| 1 | 没有 per-lane readiness 阶梯（registered → bound → launchable → executing） | 探针：无 lane-readiness 元素或文本 | 管家 readiness（roadmap R2 / 审计 F6） |
| 2 | 没有 lane 级纠偏（暂停或撤销已确认承诺） | 探针：无 lane-correction 元素；只有运行中 Turn 的纠偏 | shared alignment（roadmap R4） |
| 3 | lane 失败后不说明阻塞归属与下一步 | 探针：无 lane-blocker 元素或文本 | 恢复与继续（roadmap R3） |
| 4 | 不用 lane 的回传结果判定完成 | 探针：无 lane-return 元素或文本 | 交付回收（roadmap R3） |

确认结果原为缺口 1，当时的证据是“只有一条通用的『已应用』提示”。现在确认后的结果会
说明它分配了哪些工作、分给谁、哪些没有派出去以及原因，因此场景改为对它做断言而不是记为
缺口：结果必须点名每一条已分配的 lane 与每一项未派工的工作及其原因。它仍然不主张执行——
界面说的是“分配已记录，执行进度请查看目标”，所以第 5 拍仍是缺口 1，而不是已证明。

管家快捷提示行原为缺口 1。现在场景对它做断言而不是探针：提示行必须出现五个已发布标签，
点击“看阻塞”必须把消息作为一次被接受的 Turn 发出，输入区必须保持为空。回归时失败信息
会点名缺失标签，因此不会静默通过。

## 本文案不主张什么

- 它不资格化真实管家对话：fixture 顶替了 agent turn，因此接入背后的模型/运行时在这里仍未测试；
- 它不资格化飞书受众，也不资格化任何云端/远端 worker；
- 它不把一条通过的 smoke 当成“某个真实确认过的 Goal 已被产品验收”。

## 真实意图到结果验收

[Golden-query 集](golden-queries.md)覆盖创建、接入已有 Agent、负责人路由、接收方采用、纠偏恢复与注意力。它定义以上合成旅程之外的真实验收目标，不能从浏览器 fixture 推断真实运行通过。
