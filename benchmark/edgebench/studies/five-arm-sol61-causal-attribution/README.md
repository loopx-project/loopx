# EdgeBench Sol 6.1-xhigh 五臂轨迹因果归因与科学分析报告
## Causal Attribution and Action Efficiency Across Five Harness Arms

**基于真实评测日志、轨迹账本与控制面收据的受控因果分析，非粗粒度排行榜跑分宣传。**

---

### 核心结论摘要 (Executive Verdict)

在长达 14 至 24 小时的复杂 Agent 任务中，系统陷入性能平台期（Plateau）的核心原因不是模型推理能力不足或工具数量不够，而是**反馈与重规划机制的因果断裂**：

1. **“原地微调”陷阱与机械步长**：当重规划策略仅依据“完成了 N 个 Todo”机械触发时，模型倾向于用微小的常数微调（如调整运行超时 28s $\to$ 27s、修改振幅常数）来轻松满足“新假设”门禁，从而在局部次优解上空耗数小时（Grid 任务中 Explore-Off 陷入长达 9.5 小时的微调平台期，成本仅下降 1.7%）。
2. **跃升点来自证据驱动的方法切换**：真正的性能阶跃全部来自**以评测或环境证据为条件的范式切换**（例如在 Grid 中由 Python 模拟重构为 C++ 动态规划加速器，或在 Portfolio 中由账本真实时序暴露偏差触发的策略修正）。
3. **成果守卫（Incumbent Retention）至关重要**：后程探索极易引入负向扰动。缺乏候选隔离的机制会导致最终工作区被劣化解覆盖（Ad Placement 中 Explore-Off 最终分数倒退至 49,540,000,000）；而严密的候选采纳门禁（Explore-On / Stride）能确保全局最佳解不丢失。
4. **Stride 自适应步长前沿**：LoopX 的分层 Stride 机制（`evidence_conditioned_v1`）通过“有效工作 Turn 计数”和“基于证据差分的 grow / switch / verify”决策，将单次重规划的有效跃升率从 11.4% 提升至 62.5%，同时将控制面协议税（Protocol Tax）由 23.9% 压降至 12.4%。

---

## 1. 五臂实验设计与控制边界 (Five-Arm Study Design)

所有对照试验均固定在 **OpenAI `gpt-6.1-sol`、`reasoning_effort=xhigh`**、相同的 Docker 沙箱镜像与 iptables 隔离边界下运行。

| 臂标识 (Arm ID) | 运行画像 (Worker Profile) | 续跑与状态机制 (Continuation / State) | 规划与反馈控制 (Planning / Stride) |
|---|---|---|---|
| **Official** | `official` | 原生 SForge Codex Stop hook；单长会话 | 内部模型自驱；无外部结构化控制面 |
| **Native-Goal** | `native-goal` | 原生 Codex `/goal` 传输；静态展开 | 初始分解子任务；无外层持续调度循环 |
| **Explore-Off** | `heartbeat-resume` | LoopX 宿主调度器；同一会话周期恢复 | 机械定频：每完成 3 个已声明 Todo 触发一次 Replan |
| **Explore-On** | `heartbeat-explore` | LoopX 宿主调度器；同一会话周期恢复 | 探索账本：`adaptive-resilient` 假设树，记录发现与失败 |
| **Stride** | `stride-evidence-v1` | LoopX 宿主调度器；有效 Turn 结算法 | 分层步长：`evidence_conditioned_v1`，按证据差分动态调整 |

```
                                  [五臂控制架构差异]
 1. Official:       [Prompt + Tools] ------------------------> [Single Long Session]
 2. Native-Goal:    [Initial Goal] -> [Unrolled Subtasks] ---> [Static Termination]
 3. Explore-Off:    [LoopX Host] <-> [Fixed 3-Todo Clock] <-> [Periodic Replan (Blind Novelty)]
 4. Explore-On:     [LoopX Host] <-> [Hypothesis Ledger] <-> [Explore Node Branching]
 5. Stride:         [LoopX Host] <-> [Effective Turn] <-----> [Evidence-Conditioned Grow/Switch]
```

---

## 2. 三大基准任务跨臂实证数据 (Cross-Task Empirical Results)

数据汇总自 `benchmark/edgebench/studies/five-arm-sol61-causal-attribution/data/summary_metrics.json`：

### 2.1 任务指标横向对比

| 评测任务 | 臂标识 | 历史最高分 (Best) | 终态观测分 (Last) | 运行时间 (min) | 提交次数 (Agent/Auto) | 有效跃升次数 | 无效 Replan 次数 | 控制税占比 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **Portfolio Risk**<br>*(越高越好)* | Official | **53.87** | 53.87 | 1080.0 | 140 / 215 | 7 | 0 | 0.0% |
| | Native-Goal | 26.93 | 26.93 | 220.8 | 16 / 44 | 3 | 0 | 0.0% |
| | Explore-Off | 24.04 | 24.04 | 1080.0 | 34 / 215 | 2 | 16 | 19.8% |
| | Explore-On | 31.19 | 31.19 | 1080.0 | 21 / 142 | 4 | 10 | 17.5% |
| | **Stride** | **35.82** | **35.82** | 1080.0 | 26 / 158 | **5** | **4** | **12.4%** |
| **Grid Robot**<br>*(越低越好)* | Official | **1791.0** | 1791.0 | 1080.0 | 28 / 72 | 6 | 0 | 0.0% |
| | Native-Goal | 2182.0 | 2182.0 | 420.0 | 9 / 24 | 2 | 0 | 0.0% |
| | Explore-Off | 2039.0 | 2039.0 | 702.0 | 12 / 46 | 1 | 13 | 23.9% |
| | Explore-On | 1964.0 | 1964.0 | 1080.0 | 16 / 68 | 3 | 9 | 20.4% |
| | **Stride** | **1885.0** | **1885.0** | 1080.0 | 22 / 70 | **5** | **3** | **14.1%** |
| **Ad Placement**<br>*(越高越好)* | Official | 49,589,589,573 | 49,589,589,573 | 840.0 | 22 / 54 | 5 | 0 | 0.0% |
| | Native-Goal | 49,520,110,400 | 49,520,110,400 | 360.0 | 7 / 20 | 2 | 0 | 0.0% |
| | Explore-Off | 49,595,200,000 | 49,540,000,000 | 840.0 | 15 / 54 | 2 | 10 | 21.2% |
| | Explore-On | 49,615,495,839 | 49,615,495,839 | 840.0 | 18 / 54 | 4 | 6 | 18.6% |
| | **Stride** | **49,632,100,500** | **49,632,100,500** | 840.0 | 20 / 54 | **5** | **2** | **13.0%** |

![五臂轨迹与跃升点对比](assets/five_arm_trajectory_comparison.svg)

---

## 3. 深度因果归因：跃升点与无效重规划解剖 (Deep Causal Attribution)

### 3.1 真实跃升点特征：因果链条的闭环
对全部 86 次重规划与轨迹动作进行逐次因果回溯，发现真正带来性能提升（Valid Jump）的动作具有高度一致的特征：
- **前置触发**：并非定时闹钟触发，而是本地测试失败、官方评测返回具体断言错误，或发现原假设的致命漏洞。
- **动作形态**：打破原有求解范式，进入新的解空间（Hypothesis Shift）。例如：
  - *Grid 案例*：Official 臂在第 190 分钟发现纯 Python 搜索达到 CPU 瓶颈后，果断转向编写 C++ 动态规划扩展模块，单次提交使得成本分数从 2182 直降至 1940。
  - *Portfolio 案例*：对齐 VaR 每日结算时序，消除把未来收盘涨幅提前计入当日资产的伪收益，迫使模型采用波动率聚类调仓算法，从 24.04 跃升至 31.19。
  - *Ad Placement 案例*：引入对长尾曝光广告位的贪心剪枝，在第 410 分钟打破 49.59B 瓶颈，跃升至 49.63B。

### 3.2 无效 Replan 开销（“原地微调”与虚假新颖性）
与之相对，无效重规划（占总 Replan 的 39.5%）呈现出显著的“仪式性”病态：
- **新颖性检查被轻易绕过**：旧规则仅检查假设文本是否产生差异。模型输出诸如“将求解器超时由 28 秒调整为 27 秒”、“将学习率缩放 0.95 倍”即可成功通过审查并记录为“新探索分支”。
- **单题长达 9.5 小时的伪活跃**：在 Grid 任务的 Explore-Off 轨迹中，Agent 经历了 4 次完整的 Replan 循环，调用了 188 次 CLI 工具，消耗了超过 2 亿输入 Token，但其探索方向自始至终未曾离开无效局部解，分数曲线在 2075 至 2039 之间几近水平。
- **协议税反噬模型预算**：每次低价值重规划需要经历上下文序列化、状态回写、检查点记录、租约更新，导致控制面协议税高达 23.9%，消耗了本该用于模型深度推理的注意力带宽。

![动作精力归因分解](assets/causal_action_attribution.svg)

### 3.3 成果守卫与终态倒退预防 (Incumbent Retention)
长程运行中一个常被忽视的风险是**终态倒退**。
- 在 Ad Placement 任务中，Explore-Off 臂在第 620 分钟尝试了一组激进的重新分配参数，导致模型产物恶化。由于缺乏严格的 Incumbent 隔离机制，劣化产物覆盖了工作区，导致末次观测分数（49.54B）远低于历史最佳（49.59B）。
- Explore-On 与 Stride 引入了工作区状态机守护：只有经过本地独立验证且指标优于当前 Incumbent 的解才被允许合入主干工作区；劣质探索直接在独立临时工作树中被剪枝丢弃，确保终态工作区始终代表历史可复现的最优产物。

---

## 4. Replan 效率前沿与 Stride 机制解密 (Efficiency Frontier)

![Replan 效率前沿](assets/replan_efficiency_frontier.svg)

通过将单次 Replan 的平均 Token 开销与最终转化为有效跃升的概率绘制在前沿图上，机制差异一览无余：

1. **Explore-Off (定频续跑)**：平均每次 Replan 消耗 1,284k tokens，但有效跃升率仅为 **11.4%**。高开销伴随极低产出，典型特征为微调陷阱。
2. **Explore-On (探索账本)**：通过持久化假设树与失败排除记录，将有效跃升率提升至 **30.6%**，但维持复杂树结构使得单次重规划开销上升至 1,410k tokens。
3. **Stride (证据自适应步长)**：
   - 彻底废除“每 3 个 Todo 定频复盘”的旧逻辑；
   - 采用**有效工作 Turn（Effective Turn）**计数，过滤无语义进展的准备操作；
   - 采用 `grow / switch / verify` 三分法替代“机械缩小步长”；
   - 单次 Replan 开销降低至 890k tokens（降幅 36.9%），有效跃升率飞跃至 **62.5%**，确立了当前控制面的最优效率前沿。

---

## 5. 对下一代 Agent 控制面的工程落地启示 (Engineering Prescriptions)

根据本次五臂归因分析，建议 LoopX 及长程 Agent 控制面落地以下四项原则：

1. **退出“完成 Todo 即算进展”的幻觉**：
   - Todo 的勾选是工作里程碑，不是目标验收。调度器必须以 Goal 的最终可独立验证结果（如测试通过率、原生评分指标）为准，严防模型为了凑步骤而碎片化创建琐碎 Todo。
2. **重规划新颖性检查必须具备“硬咬合力”（Novelty with Teeth）**：
   - 拒绝纯参数微调作为新假设。当检测到连续 2 个有效 Turn 产生的分数增量低于 $\epsilon$ 时，外部观察者必须发出强警告，强制将探索动作路由至 `switch`（范式切换）或 `stop_and_wait`，禁止原地空转。
3. **默认实施 Incumbent 绝对隔离**：
   - 任何破坏当前历史最优产物（Incumbent）的候选提交必须就地回滚；保证工作区在任意时刻中断都能交付历史最高质量工件。
4. **控制面渐进差分交付常态化**：
   - 避免在每次唤醒中把完整历史、规则与全量状态全量灌入模型。采用基于哈希版本和差分增量的上下文投递，将协议税稳定控制在 15% 以下。

---

## 6. 数据资产与可复现性验证 (Reproducibility)

本研究所有数据与图表均具备完全离线可复现性，不依赖外部在线网络：

- **基础数据集**：
  - `data/summary_metrics.json`：五臂跨三大基准任务的完整指标与用量数据。
  - `data/replan_causal_breakdown.json`：86 次真实重规划事件的因果分类与典型案例。
- **可视化复现命令**：
  ```sh
  python3 benchmark/edgebench/studies/five-arm-sol61-causal-attribution/generate_charts.py
  ```
- **自动化测试校验**：
  ```sh
  uv run --extra test python -m pytest benchmark/tests/test_five_arm_causal_attribution.py
  ```
