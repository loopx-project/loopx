# Execution observations through ordinary Goal planning

## Selecting local observations

For heartbeat work that needs local execution measurements, set
`execution_observation="local_probe"` on `BenchmarkCodex`. Keep the full task
in the existing Goal/Todo planning input and choose `task_entry` and
`stride_strategy` independently. Merge these settings into the experiment's
existing agent configuration, retaining its model, task inputs and budgets:

```yaml
agents:
  - import_path: benchmark.runtime.harbor:BenchmarkCodex
    kwargs:
      execution_mode: heartbeat
      task_entry: loopx-planned
      execution_observation: local_probe
      stride_strategy: "off"
```

`evidence_conditioned_v1` remains an independent, explicit strategy choice.
With `loopx-planned`, the ordinary planner determines the work; with
`seeded-todo`, the adapter seeds only the ordinary task and its full acceptance
criteria. No research preset creates candidate/research stages, a successor
Todo, a Vision patch or a forced stride strategy. Work after an initial
candidate follows the original Goal, accepted requirements, ordinary
Todo/continuation/replan and remaining useful authorized work. Candidate
completion does not settle other requested work. User Stop, original budgets
and genuine blockers remain authoritative; no work is created just to fill time.

This option supports heartbeat only. Omission or `None` leaves it off and
preserves the ordinary task-entry, stride and probe-installation defaults.
When selected, the adapter installs `loopx-research-probe` and passes
`LOOPX_EXECUTION_OBSERVATION=local_probe` to the worker. Planning and heartbeat
instructions request reuse of applicable observations from an execution that
matches the actual candidate and input revision. Wrap a needed execution only
when decision-critical measurements are missing; do not repeat an experiment
merely to produce an attachment. Configuration and readback invoke neither a
probe nor a model. Runtime receipts record the selected producer, which does
not attest execution, adoption or improvement.

## Retiring the bundled research treatment

`research_loop` and `LOOPX_RESEARCH_LOOP` have been removed. Any presence of
the adapter kwarg (including `False`) or worker environment variable (including
`0`) fails before runner setup or wake creation with a migration error. Remove
both fields from new configurations; choose observation and stride separately
as above. The fixed two-stage initializer and its additional research
instructions are removed rather than retained as a compatibility mode.

Use a new trial for a changed treatment. Historical configurations, scores,
trajectories and receipts remain pinned to their original product/runner
revisions and are not rewritten or relabeled as ordinary-planning evidence.
A retirement error does not migrate or reopen an active trial.

## Preserving the original Goal

When Harbor creates a Goal, an omitted `task_objective` uses the first complete
task instruction with leading/trailing whitespace removed (`instruction.strip()`).
An explicit `task_objective` takes precedence. Later phases update task input
without replacing the existing Goal objective, clearing waits or overriding Stop.
This initialization repair applies with observations omitted, `None` or
`"local_probe"`; it intentionally replaces the older generic objective.
It is independent of measurement selection and does not change the Core Goal
contract. Pin both product and runner revisions in comparisons, even with
observations off; unchanged defaults do not imply whole-treatment parity with
historical versions.

## Validation and comparison boundary

The [execution integration fixtures](../../tests/control_plane/test_research_execution_loop.py)
exercise the adapter, real local process, File/SQLite authorities, Explore
writeback, evidence-driven replan and subsequent changed execution. They also
cover independent observation with stride off and preservation of waits and Stop.
The [ordinary stage-continuity fixture](../../tests/control_plane/test_research_stage_continuity.py)
uses accepted requirements, the real scheduler/subprocess Host and exactly-once
settlement to verify that a completed stage cannot hide already requested work,
and that an accepted owner revision can withdraw that work.

These are controlled engineering checks with deterministic actors, not evidence
that a model independently chooses productive research or that retirement
improves long-horizon outcomes. For that claim, compare the same complete task,
model, verifier and budgets at pinned revisions; record actual task structure,
strategy and instructions. Measure first-candidate quality, later validated
improvements and complete costs including observations and control overhead.
A combined-treatment comparison does not isolate a component's causal effect.

## Measurement and attribution

The adapter uses the staged runner/Python for `loopx-research-probe`. The
producer is also callable as `python -m benchmark.runtime.research_probe`:

```sh
loopx-research-probe --candidate-file candidate.py --output probe-001.json \
  --input-revision public-case-v1 --planned-method-id scheduler \
  --executed-method-id mask-selection --change-reason 'The control isolated mask selection first.' \
  --evidence-ref probe:local-001 --limit-seconds 30 --diagnostics probe-diagnostics.json \
  -- python3 candidate.py
```

The solver may write `{"search_iterations":2,"stop_reason":"search_cap"}` to
`LOOPX_RESEARCH_DIAGNOSTICS`. The host measures elapsed time, exit/timeout and
the selected candidate-file digest before/after. A changed candidate rejects
attribution. Solver counters and reasons remain reports; missing counters
remain null. Low resource use does not impose a mandatory time floor.
Malformed optional diagnostics preserve reliable host measurements and retain
a bounded diagnostic-error receipt. Evidence paths must be fresh; raw output
stays local. The probe neither submits nor accesses hidden evaluation.

The adjacent `.candidate.json` preserves selected relative paths and hashes.
`candidate_sha256` hashes the sorted JSON file manifest, not native archive
container bytes. Pins cover only declared files at the probe boundaries, not
transitive imports or independent writers. Result attribution must still prove
that the evaluated snapshot matches the selected candidate and input revision.

Attach the emitted `execution_observation` through normal Explore result
writeback. Its input revision and evidence refs must belong to the enclosing
finding; released results must identify the candidate actually evaluated.
Finding refs, observation `evidence_refs` and `result_ref` share the existing
Explore reference syntax, so an existing `validation:run/1` can be retained.
Method ids keep their separate identifier rule. Producer selection leaves
[the Goal's Explore mode](../../loopx/capabilities/explore/README.md#explore-harness-modes)
unchanged; enable ordinary evidence writeback through that existing setting.
Different planned/executed method ids require a change reason. Findings,
replan and research-history details retain this same source. They do not
certify method benefit or Goal completion. Missing observations preserve old
events; a reader for the optional field is required for new retained findings.

Result followthrough uses existing material delivery classifications.
`surface_only` and unknown historical outcomes remain readable but cannot
consume a cited work result or create a new unresolved experiment. Useful
negative results and concrete blockers remain eligible evidence.

This adapter retains existing Host admission. They do not install C1's
per-provider-request guard or C3's native submission/feedback recovery.
Those guarantees require their separate entries and qualification; C1's
request-admission entry requires a configured Responses-compatible endpoint.
Local process and integration checks do not establish initial-quality parity,
long-horizon benefit or complete-cost efficiency.

## 中文交付边界

执行观测复用普通 Goal/Todo/Explore：heartbeat 可独立选择
`execution_observation="local_probe"`，`task_entry` 和 `stride_strategy` 分别配置。
`loopx-planned` 由普通 planner 决定任务；`seeded-todo` 只预置保留完整验收要求的
普通任务。省略观测或设为 `None` 时，不安装探针、不追加观测指引，任务入口和
stride 默认选择保持不变。观测不创建候选/研究两阶段、研究后继或 Vision 补丁，
不强制 `evidence_conditioned_v1`。初始候选之后，按原目标、已接受要求、普通
Todo/replan/Explore 及剩余有价值工作接续；候选完成不能关闭其他已请求工作。
Stop、原预算和真实阻塞继续有效，不为填满时长制造任务。

`research_loop` 和 `LOOPX_RESEARCH_LOOP` 已直接退役，不保留兼容模式。新配置
必须移除这两个字段；即使值为 `False` 或 `0`，仍会在 runner 初始化或 wake 创建
之前明确报迁移错误，防止旧配置静默改变实验含义。需要测量时，按上方 YAML
独立选择 producer 和策略。处理组合变化须建立新 trial，不能原地转换进行中的
工作；历史版本、配置、轨迹、分数和回执保持原归属，不改写为普通规划的验证。

新建 Harbor Goal 时，未提供 `task_objective` 则保存首次完整指令的
`instruction.strip()`；显式值优先。后续 phase 更新输入，不替换原 objective，
也不清除等待或 Stop。这项初始化修复在未选观测、`None` 和 `"local_probe"`
下均生效，有意替换旧泛化目标，不改变 Core Goal 合同。即使未开启观测，对比
也须固定 product/runner revision，不能将默认选择不变宣称为完整历史处理等价。

独立入口使用同一 `loopx-research-probe`。优先复用与实际候选和输入相符的已有
执行证据；缺少影响决策的测量时，才在必要实验的现有预算内使用探针，不为补
附件重复实验。配置和读回不调用模型或探针；producer 回执只记录配置，不证明
发生执行、采用或收益。探针记录真实耗时、退出和超时状态，固定所选文件前后
哈希；可选 solver 计数与宿主测量分开，缺测保持未知，坏诊断不能抹去可靠测量。
证据路径必须新建，原始输出留在本地；不提交原生评测，不读取隐藏评测。候选
变化拒绝归因；文件清单哈希不是原生归档哈希，只覆盖明确选择的文件及探针
边界，不覆盖传递依赖或独立写入者，结果仍须证明对应实际评测快照和输入版本。

观测通过既有 Explore 写回、replan 和研究历程保留，输入版本与引用必须属于
该 finding，计划/实际方法不同须记录理由。finding 引用及观测的 `evidence_refs`、
`result_ref` 共用既有语法，可保留 `validation:run/1`；方法 ID 仍使用独立规则。
选择 producer 不改变 Goal 的 Explore 模式，写回仍通过既有设置开启。旧事件
缺失观测仍可读，新字段需兼容 reader。`surface_only` 和未知历史 delivery
不能消费实质结果或创建未解决实验；有用负结果和真实阻塞仍是有效证据。

真实进程和 File/SQLite 集成检查覆盖普通任务接续、执行观测进入 Explore、
基于证据的 replan、后续变更执行以及 Stop/等待保留；普通阶段接续还覆盖真实
scheduler/Host、验收要求和一次结算。确定性 actor 的工程检查不证明模型自主
研究收益；效果和效率结论仍需对齐任务、模型、评测器、预算及完整测量/控制成本
的实验，整体组合对照不能归因单项因果效果。既有 Host 准入不变；本入口不安装
C1 逐次 provider 请求守卫或 C3 原生提交/反馈恢复，这些组合保证须分别验证，
C1 仍需配置兼容 Responses 的端点。
