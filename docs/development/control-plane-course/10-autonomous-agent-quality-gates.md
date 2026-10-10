# 第 10 讲：Agent 自主写代码时的分层质量门禁

> **本讲结论：** 先用独立 oracle 定义正确语义，再按风险选择 deterministic test、canary、
> 模型行为验证与 release gate；当前实现输出不能反过来定义正确答案。

建议时长：120 分钟。质量模型 30 分钟、分层门禁 35 分钟、组合案例 35 分钟、实验与 review 20 分钟。

一小时专题[长程任务如何收敛](topic-long-horizon-convergence.md)先解释独立 oracle 为什么是
防止长程系统自证正确的必要边界；本讲继续展开不同风险 surface 的具体 qualification。

## 学习目标

完成本讲后，开发者应该能够：

1. 先判断状态和规则是否合理，再判断实现是否符合，而不是把当前输出录成 golden。
2. 按改动风险选择 unit、focused smoke、decision replay、canary、模型行为验证和 release gate。
3. 区分 blocking failure、advisory signal、deferred gap、`not_applicable` 和 manual hold。
4. 解释为什么常规模型行为测试是 actual-default one-arm，而双臂只服务于明确的差分或提升声明。
5. 为一次自主交付写出 source、oracle、decision、receipt 和 owner boundary。
6. 判断测试替身、benchmark 分数和基础设施回执是否保留了真实产品的因果语义。

本讲不要求第一次就读完八个组合案例。建议先完成“独立语义 Oracle”“分层质量地图”与
“门禁结果状态机”，再按改动 surface 选择案例：control-plane rule 读案例一、二、三、七；
agent-facing packet 读案例四；文档与社区资源读案例五；release/benchmark 读案例六、八。
这种选择本身就是 risk-based qualification 的练习。

## 本讲沿三个 Showcase 验证什么

质量门禁不是脱离产品路径的测试清单。三个 Showcase 各自暴露了不同风险：

| Showcase surface | 独立 oracle | 最小确定性证明 | 更高层证明 |
| --- | --- | --- | --- |
| PR observation → successor | PR lifecycle transition table | decision unit + focused lifecycle smoke | GitHub readback / exact commit receipt |
| Fix implementation → delivery | issue acceptance、repository boundary | focused test + diff/workspace scan | PR checks、review outcome |
| Auto ML candidate → launch | experiment contract、resource/authority boundary | schema + preflight + no-effect preview | exact provider effect/readback receipt |
| External task → model evidence | revision/window/baseline comparability、failure attribution | result reducer replay + negative cases | matched evaluator/artifact readback |
| Auto ML evidence → next portfolio | Graph lineage、Harness read-only boundary | result-log + planner gate smoke | repeated portfolio/no-duplicate review |
| Dev experiment → holdout | metric contract 与 dev/holdout 隔离 | evidence schema + decision replay | protected evaluator run |
| Holdout → promotion | acceptance、guardrail、boundary policy | promotion/retirement rule test | matched outcome baseline 或 reviewer gate |
| Agent-facing packet → model action | 独立状态语义与 forbidden outcomes | contract + output budget | actual-default model behavior portfolio |

因此门禁设计应从“哪条因果链可能断掉”出发。Issue-Fix 重点防止错误 successor、错误
workspace 和旧 commit 回执；Single-Agent Auto ML 重点防止不可比实验、基础设施失败误归因、
重复候选和越权 launch；Auto Research 重点防止 evaluator 泄漏、dev-only promotion 和无法
归因的分数。三者共享 Kernel 的 quota、gate、receipt 和 release qualification。

Auto ML 至少需要两组彼此独立的 oracle：

1. **执行 oracle**：候选 identity、code revision、数据窗口、资源与 provider receipt 是否
   精确匹配，证明这一次外部 effect 确实发生在预期对象上；
2. **科学 oracle**：matched baseline、primary metric、user/global guardrail 和 failure
   attribution 是否允许产生 promote/no-promote 结论。

Explore Graph 只能消费第二组 oracle 已接受的 finding；Explore Harness 的 planner smoke
还要证明它不会 claim、launch、mutate 或 spend。即使 portfolio 排序看起来合理，也不能
跳过前两组 oracle。

## 门禁不是“把所有测试都跑一遍”

一个长程 agent 改坏系统，不一定表现为函数返回值错误。它可能：

- 选错 todo；
- 把其他 peer 的 gate 当成自己的 gate；
- 把非阻塞提醒误当成用户授权；
- 在 monitor 没有新证据时重复 spend；
- 丢失 host scheduler ACK，仍声称 cadence 已应用；
- 缩短 agent-facing packet 时删掉决定下一步行为的字段；
- 用旧 commit 的测试结果给新 tag 背书。

因此质量门禁要回答两类问题：

```text
语义问题：在这组源状态下，正确决策到底是什么？
实现问题：当前代码是否稳定地产生这个决策？
```

第二个问题不能反过来定义第一个问题。否则一次错误实现只要刷新 snapshot，就会变成新的“正确行为”。

## 第一道门：独立语义 Oracle

写测试前先用产品规则表达期望，而不是从实现输出复制期望：

```text
source facts
  -> independently reviewed invariant
  -> expected decision and forbidden outcomes
  -> implementation under test
  -> comparison receipt
```

例如 `required_decision_scopes=["publish"]` 的 agent todo 遇到一个
`blocking=false` 的 `user_action`：即使提醒文本里提到 publish，它也不能授予 publish
权限。这里的 oracle 来自 authority invariant，而不是当前 reducer 是否恰好返回
`should_run=true`。

Oracle 至少要写清：

- 哪个 source 拥有事实；
- 谁拥有决策 authority；
- 哪些结果允许；
- 哪些结果必须 fail closed；
- 哪些无关字段变化不应改变结果。

Characterization fixture 仍有用，但它只能记录 legacy 行为。若它与 invariant 冲突，
应修规则并增加反例或 mutation case，不能刷新 golden 来保住错误。

## 测试替身也不能改写产品合同

Fake、mock 和 bridge test double 不只是测试工具；它们决定 fixture 允许什么调用。一个
过强的 fake 会把合法实现判错，一个过弱的 fake 又会让非法路径通过。Turn bridge 的一条
回归可以说明这类风险：创建和清理 baseline 文件是 housekeeping，只有独立 postcondition
验证才计为 meaningful operation。错误 fake 把所有非 probe 命令都强制解释为 meaningful：

```python
# 错误：测试替身自创了“每条成功命令都必须是业务动作”的合同
assert meaningful is True
self.meaningful_operation_count += 1

# 正确：计数服从调用方显式声明
if meaningful:
    self.meaningful_operation_count += 1
```

产品实现中的 `bridge.exec(..., meaningful=True)` 才定义语义动作；测试替身只应记录这项
声明，不能根据命令是否成功、是否允许 non-zero，或是否发生在 validator 周围自行推断。
修复后还要断言最终 count，避免把 fake 放宽成“什么都不检查”。

评审 test double 时至少问四个问题：

1. 参数默认值是否与真实 adapter 一致？
2. housekeeping、observation、semantic effect 是否被分开？
3. failure/timeout/non-zero 是否保留真实分支，而不是全部 raise 或全部 pass？
4. fixture 是否断言最终 receipt，而不只断言调用发生过？

这类失败首先是测试基础设施合同问题，不能直接归因为产品回归。只有把 fake 与真实接口
语义校准后，CI 结果才有资格进入产品判断。

## 分层质量地图

同一个交付行为从近到远接受不同门禁。越靠前越快、越确定；越靠后越昂贵、越接近
真实 agent 或 release 环境。

| 层 | 主要证明 | 常规频率 | 不能替代什么 |
| --- | --- | --- | --- |
| Unit / contract | 纯函数、schema、transition、非法状态拒绝 | 相关 PR | 真实 CLI 串联 |
| Focused deterministic smoke | 一条已交付 CLI 或跨模块路径 | 开发本地、相关 PR | 全仓风险覆盖 |
| Public-safe decision replay | 独立源事实可重放到最终 decision | 控制面回归 | provider 行为 |
| Quality surface catalog | 高风险 surface 都有 oracle 和层级分类 | catalog 变更、premerge | 具体测试执行结果 |
| CLI output budget | 默认 agent 输出有界，base/head 增长可解释 | 输出相关 PR | 字段语义正确性 |
| Risk-based canary | Git diff 对应的最小跨 surface 组合已通过 | 敏感合并、发布前 | 每日完整清单 |
| Full-public smoke fleet | 广覆盖、超时与 inventory 健康 | `main`、每日、手动 | 聚焦回归 |
| Actual-default model behavior | 真实模型正确理解当前默认 packet | 低频本地、敏感发布 | schema、优先级、冷路径恢复 |
| Exact-commit release qualification | 所有回执属于同一 clean commit、tree 和版本 | 发布前 | owner 的发布决定 |
| Matched outcome baseline | 明确的 benchmark/长程提升声明成立 | 有提升声明时 | 普通功能正确性 |

关键原则是**互补而不越权**：

- 模型通过不能覆盖 deterministic contract failure；
- full-public 全通过不能替代一个明确命名旧故障的 focused regression；
- output 字符数没增长不能证明 agent 仍知道下一步做什么；
- release receipt 齐全不能替 owner 自动做发布决定。

## Quality Surface Catalog：先审计覆盖设计

高风险 surface 需要在 catalog 中声明：

```text
surface -> independent oracle -> deterministic minimum
        -> each layer: covered | not_applicable(reason) | deferred(owner)
```

运行：

```bash
loopx canary quality-audit
```

这里的 `not_applicable` 与 `deferred` 不能混用：

- `not_applicable`：这层测试不适合该语义，例如用模型判断确定性的 scheduler 优先级；
- `deferred`：这层有价值但尚未建设，必须保留 owner 和可见缺口；
- `covered`：有真实路径和稳定 oracle，不是只写了一句“已有测试”。

Catalog 不是测试结果汇总，而是“为什么这些层足够”的机器可审计设计。

## PR 快速门：让普通迭代保持便宜

普通 PR 应首先运行快、确定、可复现的门：

```bash
python -m ruff check tests loopx/canary loopx/control_plane loopx/domain_packs loopx/presentation
python -m mypy
python examples/control_plane/cli-output-budget-regression-smoke.py
python -m pytest -q
git diff --check
```

开发时不必每次跑完整矩阵。先跑与改动直接相关的 unit/contract 和 focused smoke，
再让 canary planner 从 Git diff 选择最小跨 surface 集合：

```bash
loopx canary premerge --from-git-diff
```

完整公开 smoke 保持显式、并行、有界：

```bash
loopx canary smoke-suite --suite full-public --jobs 4 --timeout-seconds 120
```

它适合主干、每日或手动运行，不应成为每个文档 patch 的同步阻塞项。保护质量不等于
把最宽的测试频率提高到每次保存文件。

## 真实模型门：常规 one-arm，必要时才 pair

确定性测试能证明 packet 有哪些字段、状态优先级如何、CLI 是否可执行，却不能完全证明
真实 agent 会如何理解一份压缩后的默认载荷。这个剩余风险才交给模型行为验证。

常规 onboarding profile 使用 **actual-default one-arm**：

```text
candidate checkout 的正式 packet builder
  -> deterministic oracle 先检查真实 packet
  -> 仅脱敏本地绝对路径
  -> Doubao 2.1 actor
  -> scenario-specific semantic verifier
  -> bounded receipt / mismatch code
```

产品默认切换时，实际行为与测试输入一起切换。不保留一条已经退休的产品实现，仅为了
长期充当第二臂。常规 portfolio 覆盖正常接入、agent identity、goal selection、selected
todo、peer routing、same-agent continuation、final human gate、healthy continuation 和
projection repair；每个场景重复两次，所有重复都要通过。

这些场景不应一律只做 packet interpretation。当结论是“agent 必须真的调用工具”时，
portfolio 使用 scenario-owned 的 hermetic actor：selected Todo 执行目标 read，replan 执行
evidence-log read，scoped gate 在呈现非阻塞 notice 后执行 successor，capability bridge
则先执行原 blocked Todo 的真实任务侧 callsite，再在同一 heartbeat 执行 quota 投影的
re-entry 命令；只有 quota 重新选中原 Todo，且没有 repair Todo、turn settlement 或
durable capability grant，才算通过。第四种场景里，等待或更新 monitor fallback、在
quota 前绕过 gate、验证前 re-entry、或读取错误目标，都必须失败。四个 actor 只共享已证明的 tool decoding 和隔离 CLI 机制；
各自的 Goal fixture、合法动作状态机和 semantic oracle 保持独立，不引入通用 scenario
runner。

双臂只在两类问题中合理：

1. 临时验证一个敏感改动是否与明确 baseline 语义等价；
2. 发布明确声称 benchmark 或长程 outcome 提升，需要 matched stable/candidate baseline。

冷路径恢复、字段存在、schema 与 scheduler precedence 不进入常规 Doubao portfolio；
这些可确定判断的规则留在 deterministic gate。真实模型调用也不进入普通 CI，避免让
每个 PR 依赖凭证、网络、provider 延迟与服务稳定性。

## Agent-facing 热路径与诊断冷路径

默认 guided projection 是 agent 每轮都会消费的热路径；完整诊断包是显式请求的冷路径。
把字段从热路径移到冷路径不是简单删 JSON，至少要同时证明：

1. 热路径仍保留当前 authority、selected todo、可执行 commands 和 host activation；
2. 被省略细节有显式 drill-down 命令；
3. action signature 与状态语义没有漂移；
4. CLI budget 显示输出确实收敛，而不是把重复文本搬到另一处；
5. actual-default model portfolio 中的真实 agent 行为仍正确。

冷路径不会天然降低接入效果。真正的风险是把 agent 作决定所需的信息也当成“诊断噪声”
一起移走。因此 owner review 应逐项解释字段为什么可移除，而不是只展示字符数下降。

## 门禁结果也是状态机

不要把所有结果压成一个 `passed: bool`：

| 结果 | 对 delivery 的含义 | 后续动作 |
| --- | --- | --- |
| `pass` | 当前 gate 满足 | 继续下一层或交付 |
| `blocking_failure` | 已证明违反 invariant | 停止正常 delivery，修复后重跑 |
| `manual_hold` | 自动证据不足且需要 owner 决策 | 保留明确审阅问题，不自合并 |
| `advisory` | 有风险信号，但不构成当前阻断 | 记录 receipt，可继续最小安全路径 |
| `deferred_gap` | 有价值的层尚未建设 | 写 owner/successor，不伪装已覆盖 |
| `not_applicable` | 该层不适合此 surface | 写稳定理由，不运行无意义测试 |
| `infra_failure` | runner/provider/凭证等无法形成产品结论 | 修基础设施或重试，不能记为产品 pass/fail |

这张表保护两个方向：既不让“非阻塞”偷偷满足 required authority，也不让一个无关或
暂不可用的昂贵门无限阻塞普通改动。

## 组合案例一：Monitor + Gate + Replan

源状态同时包含：

```text
continuous_monitor due
user gate open for scope=publish
blocked successor has material new evidence
replan interval reached
```

正确测试不能只断言某个字段：

1. monitor poll 只做 bounded observation，没有新证据时 no-spend；
2. 新证据可更新 gate 或 successor，但不能替用户授予 publish；
3. replan 可重新选择安全 P1/P2，却不能越过 scoped gate；
4. 最终 `interaction_contract` 是 scheduler authority；
5. 若 cadence 改变，host 必须应用并回写 exact ACK。

最小门禁组合是 decision table + quota integration + scheduler replay。这里没有必要调用
真实模型，因为 precedence 与 authority 都是确定性规则。

### 核心代码领读：Replan 如何覆盖 Monitor Quiet

入口是 `loopx/control_plane/quota/should_run.py::build_quota_should_run`；prepare 与 packet
阶段分别位于 `should_run_prepare.py` 和 `should_run_packet.py`。先找到 monitor quiet 的候选决策，
再继续向下看 replan 分支：

```python
if monitor_quiet_skip:
    normal_delivery_allowed = False
    should_run = False
    effective_action = "monitor_quiet_skip"

if replan_decision_allowed:
    normal_delivery_allowed = False
    recovery_allowed = False
    should_run = True
    effective_action = AUTONOMOUS_REPLAN_REQUIRED_MODE
    reason = (
        "autonomous replan obligation is selected before monitor quiet "
        "or agent-scope wait classification"
    )
```

阅读断点：

1. `monitor_quiet_skip` 只是候选结果，不是函数提前返回；
2. `replan_decision_allowed` 显式改写 `should_run` 与 `effective_action`，所以 replan
   precedence 不是靠 prompt 文案实现；
3. 继续读同函数末尾的 `build_interaction_contract(payload)` 和 `_scheduler_hint(...)`：
   二者都消费已经仲裁完成的 payload，host 不应重新从低层 flags 猜一次决策。

## 组合案例二：多个 Monitor 交错 + Per-Lane Streak

两个 monitor M1、M2 的 no-change poll 交错出现。若测试 fixture 只保存 run-history
文本，global consecutive detector 会被另一条 lane 打断。更可靠的多层验证是：

1. writeback test 证明每次 poll 只递增当前 monitor todo 的 counter；
2. material transition test 证明只重置发生变化的 target；
3. state replay 让 M1/M2 都越过阈值，并保留一个 blocked advancement，最终必须选择
   `autonomous_replan_required`；
4. metamorphic case 把 advancement 改成 same-agent runnable，最终必须回到
   `normal_run`，证明 replan 没有抢占真实可执行工作。

这里测试的不是一份当前 JSON，而是两条独立 invariant：monitor 停滞按 target identity
累计；runnable advancement 在工作选择上优先于 monitor-derived replan。
仓库用 [issue #2272](https://github.com/huangruiteng/loopx/issues/2272) 记录过对应回归：
M1、M2 交错 poll 时，错误的全局计数会让某条 lane 永远达不到 replan 阈值。下文的
public-safe replay 用独立 counter 和可执行 advancement 反例验证修复后的状态模型。

### 核心代码领读：Counter 为什么属于 Monitor Todo

入口是
`loopx/control_plane/work_items/autonomous_replan_obligation.py::_monitor_no_change_evidence`：

```python
raw_monitors = agent_todos.get("monitor_open_items")
monitors = [item for item in raw_monitors or [] if isinstance(item, dict)]
stalled = []
for item in monitors:
    no_change_count = int(str(item.get("consecutive_no_change") or "0"))
    if no_change_count >= threshold:
        stalled.append((no_change_count, item))

stalled.sort(key=lambda pair: pair[0], reverse=True)
no_change_count, monitor = stalled[0]
```

这里没有扫描“最近连续几条 run”。Detector 直接读取每个 open monitor 的 durable
metadata，因此 M1/M2 交错不会互相清零。继续向下看 advancement guard：

```python
for item in raw_advancements or []:
    if item.get("status") != "open":
        continue
    if item.get("task_class") != "advancement_task":
        continue
    claimed_by = str(item.get("claimed_by") or "").strip() or None
    if not claimed_by or claimed_by == agent_id:
        return None
```

这个 `return None` 是第二条 invariant 的落点：same-agent runnable advancement 优先。
Blocked advancement 因 `status != "open"` 被跳过，不会掩盖停滞 monitor。配套回放在
`examples/control_plane/monitor-poll-writeback-smoke.py`，同时读
`assert_interleaved_monitor_stalls_replan_blocked_benchmark` 与
`assert_stalled_monitor_does_not_preempt_runnable_advancement`。

## 组合案例三：Non-blocking User Action + Required Scope

一个提示可以是 user-visible，但 `blocking=false`。一个 agent todo 又要求
`required_decision_scopes=["release"]`。

正确结果是：

- 提示仍可投影给用户；
- 它不满足 release scope；
- 正常 release delivery 被关闭；
- 若 scope dangling 或冲突，进入有界 control-plane repair；
- 只允许验证修复写回，不允许借 repair 名义继续发布。

测试应增加 metamorphic case：加入其他 agent 的无关 gate，或翻转 lower-level
compatibility flag，都不能改变当前 agent 的最终 decision。

### 核心代码领读：可见提醒为什么不是 Authority

Python 入口 `decision_scope.py::build_required_decision_scope_consistency` 只负责
输入 codec 和返回 schema 检查。规则 owner 是同目录
`decision_scope.ts::decisionScopeConsistency`；它先区分 live gate 和非阻塞 action，
再检查 scope、owner 和精确目标：

```ts
const matching = gates.filter(gate => decisionScopeCovers(gate.decision_scope, scope));
const compatible = matching.filter(gate => addressed(gate, owner));
const conflicting = compatible.filter(gate =>
  todoGateRelation(gate, item)?.state === "projection_repair_required");
if (conflicting.length) {
  // 记录 required_decision_scope_target_mismatch；不得由另一 gate 掩盖。
  continue;
}
if (compatible.length) continue;
```

只有 task class、scope、owner 和精确目标一致的 gate 才满足依赖；依赖一致不等于
批准或 lease。随后 `matchingActions` 只用于错误归因
`non_blocking_user_action_scope_collision`，不能成为 authority。只有不兼容 gate 时
归因 `required_decision_scope_gate_owner_mismatch`；没有匹配来源时归因
`dangling_required_decision_scope`。standing authority 的冲突与拒绝/取消后的阻塞要求
也由这个 TS owner 检查。

覆盖范围和精确目标的内部 TS 函数仍被组合规则使用。它们已无生产调用方的三个
Python scalar adapter 与对应 RPC 操作已退役；生产调用继续使用 consistency、
组合 relation、批量 relations 和 gate scope projection，不能另建 Python 决策源。

沿 `build_required_decision_scope_repair_hint` 再读一步：repair 可以修 projection，但
`user_action remains non-blocking`，因此 repair route 也没有获得受限 delivery authority。

## 组合案例四：缩短 Guided Packet

这是敏感改动，至少经过四层：

1. contract test：保留 canonical commands、host activation 和 gate semantics；
2. output budget：比较 base/head 字符数与字段 ledger；
3. focused onboarding smoke：正式 builder 走完真实默认路径；
4. low-frequency actual-default model portfolio：验证真实 agent 仍能选对身份、goal、todo，
   并尊重 human gate。

若 deterministic oracle 已失败，不应继续花费 Doubao 调用；若模型失败，也不能直接推断
产品回归，先区分 semantic mismatch、actor transport、provider timeout 和 verifier bug。

### 核心代码领读：实际默认 Packet 如何进入模型门

先读 `loopx/bootstrap_command_pack.py::build_start_goal_guided_packet`：

```python
command_pack = build_loopx_bootstrap_command_pack(...)
commands = command_pack.get("commands")
activation = command_pack.get("host_loop_activation")
guided_transaction = {
    "schema_version": GUIDED_START_SCHEMA_VERSION,
    "mode": "dry_run_preview",
    "writes_now": False,
    "spends_quota_now": False,
    "ordered_steps": [
        {"id": "inspect_connection", "kind": "read_only", ...},
        {"id": "connect_if_needed", "kind": "conditional_mutation", ...},
        {"id": "plan_ranked_todos", "kind": "model_checkpoint", ...},
        {"id": "activate_host_loop", ...},
    ],
}
```

这里应检查 `commands`、`host_loop_activation`、no-write 和 no-spend，而不是只比较字符数。
再读
`loopx/control_plane/testing/actual_default_model_behavior_portfolio.py::_scenario_contract`：

```python
if spec.actor_kind in {
    "turn", "turn_tool", "replan_tool", "scoped_gate_tool",
    "capability_repair_tool"
}:
    build_model_behavior_actor_request(
        packet,
        semantic_contract_required=False,
        ...,
    )
    contract = _turn_expected_contract(source_packet)
    if _turn_expected_contract(packet) != contract:
        raise ValueError("actor packet diverges from source action contract")
else:
    if spec.phase == "entry":
        _validate_actual_default_projection(packet)
    contract = _semantic_contract(packet, phase=str(spec.phase))

if contract.get("decision", contract.get("route")) != spec.expected_route:
    raise ValueError(...)
```

确定性 preflight 在 actor 调用前运行：action signature、默认投影或 scenario oracle
失败时消耗零次模型调用。模型验证的是剩余的理解风险，不替这些合同兜底。

## 组合案例五：仅文档或社区资源 Patch

只改公开文档时，最小门禁通常是：

- 链接与文档 governance smoke；
- public/private boundary scan；
- `git diff --check`；
- 首屏变化时的 owner preview。

它不需要 Doubao、full-public fleet 或 outcome baseline。若文档描述了新的控制面规则，
则还要对照 canonical protocol 或 focused test，防止文档把不存在的能力写成已交付事实。

### 核心代码领读：Diff 如何选择最小门禁

入口是 `loopx/canary/premerge.py::classify_premerge_surfaces`：

```python
if any(_path_matches(path, DOC_CONTENT_TOKENS) for path in files):
    mark("docs_project_content", "docs-project-content-ops")
if any(_path_matches(path, PUBLIC_BOUNDARY_TOKENS) for path in files):
    mark("public_boundary")
if any(_path_matches(path, BENCHMARK_SENSITIVE_TOKENS) for path in files):
    mark("benchmark_sensitive")
    manual_holds.append({
        "kind": "benchmark_sensitive",
        "reason": "benchmark adapter, scoring, runner, or evidence paths require ...",
    })
```

再进入 `build_premerge_validation_gate`，观察 classification 如何分别生成 direct checks、
catalog run、risk-profile run 和 boundary scan。Docs-only 命中自己的 profile；只有实际
路径命中 benchmark-sensitive token 才产生 manual hold。门禁由 diff surface 决定，
不是由 PR 作者在描述里自报“低风险”。

## 组合案例六：Release 精确 Commit

发布前的关键不是“这些测试最近跑过”，而是“这些回执是否属于即将打 tag 的同一个
clean source identity”。Exact-commit gate 聚合既有通道，不另造一套 runner：

```bash
loopx canary release-qualification \
  --manifest-json release-qualification.json \
  --repo-root .
```

每份 receipt 都要匹配 commit、tree、clean-tree、version 和 tag。缺失、失败、skip、
rebase 漂移或旧 commit 结果都 fail closed。只有发布明确声称 outcome uplift 时，才要求
matched stable/candidate baseline；没有该声明就明确记为 not required。

### 核心代码领读：旧回执为什么不能给新 Tag 背书

先读
`loopx/control_plane/testing/release_commit_qualification.py::collect_release_source_identity`：

```python
return {
    "git_commit": _git(root, "rev-parse", "HEAD"),
    "git_tree": _git(root, "rev-parse", "HEAD^{tree}"),
    "git_dirty": bool(_git(root, "status", "--porcelain")),
    "package_version": package_version,
    "version_tag": f"v{package_version}",
}
```

再读同模块的 `build_exact_release_commit_qualification`：

```python
for qualification_id in sorted(raw_qualifications):
    normalized, check_failures, check_mismatches = _normalize_check(
        qualification_id,
        raw_qualifications[qualification_id],
        candidate=candidate,
    )
    failures.extend(check_failures)
    source_mismatches.extend(check_mismatches)

for field in _SOURCE_FIELDS:
    if observed[field] != candidate[field]:
        source_mismatches.append(f"observed_source:{field}")
if candidate["git_dirty"]:
    source_mismatches.append("candidate:git_dirty")
```

`_normalize_check` 对每份 receipt 再比一次 candidate identity，checkout observation 又
独立比一次。最终 `ready` 需要 missing、failures、source mismatches 同时为空；即使
ready，返回值仍固定 `automatic_release_promotion_allowed=False`，owner decision 没被
测试 reducer 吞掉。

## 组合案例七：行为保持的规则优先级重构

在 [PR #2320](https://github.com/huangruiteng/loopx/pull/2320) 中，goal-frontier replan
从隐式 `if` 链提取成 ordered rules；它没有新增 public payload，
但它仍是 high-risk change：一次顺序漂移就可能让 agent 在已有工作时错误 replan，或在
frontier 耗尽时永久等待。第 9 讲解释了实现结构；这里关注门禁怎样证明“移动了政策，但
没有改变政策”。

第一层是完整 decision table。每个 case 不只设置目标条件，还叠加一个更低优先级条件，
证明 first-match precedence：

```python
@pytest.mark.parametrize(
    ("overrides", "expected_rule", "derives_obligation"),
    [
        (
            {"blocking_handoff_gate_count": 1, "acceptance_gap_count": 1},
            GoalFrontierReplanRule.BLOCKING_HANDOFF_GATE,
            False,
        ),
        (
            {"succession_gap_count": 1, "acceptance_gap_count": 1},
            GoalFrontierReplanRule.TODO_SUCCESSION_GAP,
            True,
        ),
        (
            {"monitor_only_lane": True, "monitor_count": 1},
            GoalFrontierReplanRule.MONITOR_FRONTIER_EXHAUSTED,
            True,
        ),
    ],
)
def test_goal_frontier_replan_decision_table(...):
    decision = select_goal_frontier_replan_rule(
        replace(GoalFrontierReplanFacts(), **overrides)
    )
    assert decision.rule is expected_rule
    assert decision.derives_obligation is derives_obligation
```

第二层是 metamorphic test：不改变当前 agent 的 source facts，只扩大其他 peer 的 backlog，
结果必须不变；再加入当前 agent 自己的 runnable advancement，结果必须翻转。它检查的是
agent-scope invariant，不是某份固定 JSON。

第三层让 focused smoke 经由 `build_quota_should_run` 验证最终路由，防止纯 selector 正确、
integration 却漏传 facts。第四层把 surface 登记进 quality catalog 和 risk-based canary。

这类确定性重构不运行 Doubao。模型可以判断 packet 是否易于理解，却不应决定
`blocking_handoff_gate` 和 `vision_acceptance_gap` 谁优先。将模型层明确记为
`not_applicable(reason)`，比为了“测试层数完整”而增加随机调用更严格。

## 组合案例八：Outcome、Turn 与 Runner Readiness

一个 benchmark postcondition 通过，只能证明当前 workspace 满足结果条件。它不能单独
证明结果由本轮 agent 产生，也不能证明 LoopX 已完成 refresh、spend 与 scheduler
closeout。Runner readiness 因此需要一组相互约束的事实：

```text
pre-agent postcondition = unsatisfied
Turn transaction         = committed
post-agent postcondition = satisfied
official feedback        = blinded
```

`build_skillsbench_benchmark_runner_readiness` 把这组因果条件写成独立 checks：

```python
checks = {
    "turn_transaction_committed": execution.get("status") == "committed",
    "pre_agent_postcondition_checked": (
        scored_workspace_validation.get("pre_agent_postcondition_checked") is True
    ),
    "pre_agent_postcondition_unsatisfied": (
        scored_workspace_validation.get("pre_agent_postcondition_status")
        == "unsatisfied"
    ),
    "post_agent_postcondition_satisfied": (
        scored_workspace_validation.get("post_agent_postcondition_status")
        == "satisfied"
    ),
    "official_feedback_blinded": (
        scored_workspace_validation.get("oracle_feedback_used") is False
    ),
}
```

这组反例很重要：若 baseline 已经通过，即使 agent 又运行一次且最终仍通过，也不能声称
runner 已证明一次有效 transition；若 Turn 未 committed，结果通过也不能替代控制面
receipt；若 official feedback 进入执行输入，分数又失去独立性。

因此复杂系统的测试要同时覆盖三层：结果层证明 postcondition，因果层证明从
unsatisfied 到 satisfied 的变化发生在本轮，控制面层证明 Turn 已 committed。Release
outcome baseline 可以消费 readiness receipt，但不能反过来用一个分数补齐缺失的 Turn
事实。

## Agent 自主交付的标准顺序

```text
1. 读取 diff 与 active control-plane state
2. 写独立 invariant、允许结果和 forbidden outcomes
3. 在 quality catalog 中确认该 surface 的最小层级
4. 先跑 focused deterministic checks
5. 从 Git diff 运行 risk-based canary
6. 只有剩余风险涉及 agent 理解时，才跑 actual-default model gate
7. 汇总 bounded receipts，区分 failure / hold / advisory / deferred
8. 敏感默认行为、权限、发布和首屏变化交 owner review
9. 满足自合并合同的窄 patch 才可自合并
10. 发布时重新绑定 exact commit，不复用漂移回执
```

一项门禁失败时，agent 应更新 todo 或 repair delta；不能只在聊天里说“后续再看”。一项
昂贵门不适用时，也应记录理由；不能默默跳过后再声称“全量验证”。

## 代码与文档阅读路线

1. `docs/development/testing-and-quality.md`
2. `loopx/canary/quality_surface_catalog.py`
3. `loopx/canary/premerge.py`
4. `loopx/control_plane/testing/model_behavior_qualification.py`
5. `loopx/control_plane/testing/release_commit_qualification.py`
6. `tests/control_plane/test_cli_output_budget.py`
7. `tests/control_plane/test_actual_default_model_behavior_portfolio.py`
8. `tests/control_plane/test_scheduler_ack_decision_table.py`
9. `tests/control_plane/test_goal_frontier_replan_rules.py`
10. `examples/control_plane/quota-agent-scoped-user-gate-smoke.py`
11. `tests/control_plane/test_goal_vision_blocked_successor.py`
12. `deprecate/benchmark-legacy/tests/test_skillsbench_turn_runtime.py` (retired)

## 代表性实验

### 实验 A：证明 oracle 独立

为一个 gate fixture 写出 source facts、invariant、expected decision 和 forbidden outcome。
故意修改 product reducer 的低层兼容字段，确认 expected decision 不随实现输出漂移。

### 实验 B：选择最小门禁

分别给 docs-only、quota precedence、guided packet shrink 和 release uplift 四个 diff 设计
门禁组合。任何“所有 diff 都跑同一套测试”的答案都需要重做。

### 实验 C：判定模型测试是否适用

把以下问题分类为 deterministic、actual-default one-arm 或 matched pair：

- schema 缺少 required field；
- agent 是否识别 selected todo；
- scheduler human gate 是否优先于 mapped no-op；
- 新版本是否提升长程 benchmark outcome。

正确分类依次是 deterministic、one-arm、deterministic、matched pair。

## 课后检查

1. 为什么 characterization fixture 不能授予当前行为正确性？
2. `not_applicable` 与 `deferred` 的 owner 语义有什么不同？
3. 为什么常规 Doubao gate 不保留退休产品路径作为第二臂？
4. output budget 通过后，为什么还要检查 semantic field ledger？
5. monitor + gate + replan 同时成立时，哪一层拥有最终 scheduler authority？
6. 为什么旧 commit 的全绿测试不能给新 tag 背书？
7. 哪些改动允许 agent 自合并，哪些必须留下 manual hold？

下一讲进入扩展层：Explore Graph/Harness、Single-Agent Auto ML、multi-agent kernel、
Auto Research、supervisor 和 connectors 如何复用同一套状态内核与质量门禁，而不是创建
第二套控制面。
