# Execution observations and research preset

## Choosing the experiment

The heartbeat adapter supports both an independent observation producer and
an explicit research preset. They answer different experimental questions and
share the existing Explore evidence path:

| Experiment intent | Adapter choice | Task structure and strategy |
| --- | --- | --- |
| Validate an existing candidate, reproduce a result, or measure ordinary work without prescribing its next stage. | `execution_observation="local_probe"` | Existing Goal/Todo planning determines the work; `task_entry` and `stride_strategy` remain independent. |
| Test a declared optimization task as initial candidate plus retained follow-up research, keeping the best validated artifact. | `research_loop=True` | The preset creates canonical candidate/research Todos and selects `evidence_conditioned_v1` with the research instructions. |

The research preset remains a supported benchmark combination. Its explicit
follow-up work is useful to test whether candidate completion leads to further
research under the original goal and budget. Independent observations also
serve tasks that do not need that fixed structure. Neither choice introduces
a global LoopX product mode or establishes better outcomes by configuration.

## Independent execution observations

For heartbeat work that needs measurements with its own task structure, set
`execution_observation="local_probe"` on `BenchmarkCodex`. Keep the original
task in the existing Goal/Todo planning input and choose `task_entry` and
`stride_strategy` independently. For example, merge these adapter settings
into the experiment's existing agent configuration, retaining its model,
task inputs and budgets:

```yaml
agents:
  - import_path: benchmark.runtime.harbor:BenchmarkCodex
    kwargs:
      execution_mode: heartbeat
      task_entry: loopx-planned
      execution_observation: local_probe
      stride_strategy: "off"
```

`evidence_conditioned_v1` remains an explicit strategy choice. With
`loopx-planned`, the ordinary planner owns the task structure; with
`seeded-todo`, the ordinary seed retains the task's full acceptance criteria.
Selecting this producer creates no initial-candidate/research stages and
adds no obligation to continue optimizing a completed task. Follow-up work
depends on the original goal, evidence, remaining useful authorized work and
the existing Stop, budget and blocking boundaries.

This option supports `heartbeat` execution only. Omission or `None` leaves it
off; omitting both it and `research_loop` leaves the defaults for probe
installation, task stages and stride selection unchanged. Goal
initialization is intentionally repaired as described below.
The adapter installs the existing `loopx-research-probe` command and
passes `LOOPX_EXECUTION_OBSERVATION=local_probe` to its worker. Planning and
heartbeat instructions request evidence when relevant: reuse an existing
observation that matches the actual execution, candidate and input; use the
probe during a needed experiment only when a decision-critical measurement
is missing. Do not repeat an experiment merely to produce a new receipt.
Configuration and ordinary readback do not invoke the probe or a model.

Runtime receipts record the resolved producer and retain the research preset's
`research_loop` marker when applicable. Those fields describe configuration;
they do not attest that a probe ran, that evidence was adopted or that a
candidate improved. Keep the resolved settings and task input in the
experiment's existing records; no separate product mode is introduced.

## Original Goal at the adapter entry

When Harbor creates a Goal, in either ordinary entry or the research preset,
an omitted `task_objective` uses the first complete task instruction with
leading/trailing whitespace removed (`instruction.strip()`). An explicit
`task_objective` takes precedence. Later phases update the task input without
replacing the existing Goal's objective, retaining the original goal text.

This adapter initialization repair applies with `execution_observation`
omitted, `None` or `"local_probe"`, and with `research_loop=True`. It is
independent of measurement selection and intentionally changes the earlier
generic Goal objective in both entries. The preset's two stages, fixed stride
and additional research instructions are retained; its default Goal input has
changed, so the complete treatment is not identical to historical runs.
This does not change the Core Goal contract or prove that a model will
autonomously continue correctly. Compare experiments at pinned product and
runner revisions, including the Goal initialization change in the treatment
definition even when no observation producer is selected.

## Research preset

Select the preset explicitly in the same experiment configuration:

```yaml
agents:
  - import_path: benchmark.runtime.harbor:BenchmarkCodex
    kwargs:
      execution_mode: heartbeat
      task_entry: loopx-planned
      research_loop: true
```

`research_loop=True` defines a complete benchmark treatment:
it prepares an initial-candidate Todo and a deferred research successor,
selects `evidence_conditioned_v1`, installs the same probe and delivers the
original research instructions. It still prepares these stages when
`task_entry="loopx-planned"` is explicitly selected. An explicitly
conflicting stride strategy, including `"off"`, remains rejected.

Combining `research_loop=True` with `execution_observation="local_probe"`
is rejected before work begins because the preset already selects that
producer. To study independent planning or a different stride strategy, select
the independent observation configuration instead. Use a new trial when
changing treatments; neither entry is an in-place conversion of active work.

The preset uses existing `todo_done` and successor semantics: the retained
research Todo is canonical deferred work with `resume_when=todo_done:<initial>`
and an explicit successor relation. Candidate completion makes that work due
for the ordinary resume/replan path; it cannot by itself close the research
Todo. Goal instructions alone do not precreate this work or its dependency.
Research retains the best candidate, meaningful negative results and reasoned
closeout against the original objective. User Stop, the original budget and
genuine blockers remain authoritative.

Preparation writes ordinary Agent Vision scope as `surface_only` before adding
the successor edge. With the default `hard_lease` handoff, it observes the
candidate Todo's lease version, acquires its own short-lived grant with
compare-and-swap, supplies that grant's exact proof to the Todo update and
releases it in `finally`. This uses the existing ownership guard. It produces
real lease events and version changes, with control overhead, but invokes no
model, runs no experiment and spends no work Turn. `surface_only` does not
claim research progress.

An existing successor edge makes preparation return without changing Vision
or acquiring another lease. A missing edge is repaired only when this Agent's
research Todo remains deferred on exactly that initial Todo's completion;
the retry reuses the retained Todo, publishes scope before the edge and uses
a fresh lease grant after a released failure. Research that has moved to
another status or wait, or belongs to an earlier initial Todo, is returned
without repair. This is bounded initialization recovery; it does not reopen
research or clear a genuine wait.

## Comparison and validation boundary

The [supplied-candidate validation fixture](../../tests/control_plane/test_research_execution_loop.py)
checks the independent entry's real process, Explore readback and preservation
of waits and Stop. It does not demonstrate that ordinary planning replaces
the preset's research continuation, that a model will choose productive
follow-up work, or that this choice improves efficiency. The separate
candidate-to-research fixture checks a synthetic continuation chain; it also
does not establish model research benefit.

For a matched comparison, supply the same complete explicit `task_objective`
to both entries and retain the same task, model, verifier and budget. Record
each entry's resolved task structure, strategy and instructions. Comparing
the whole preset with an independent arm estimates the combined treatment;
isolating a component requires an appropriate additional comparison. Measure
initial-candidate quality, subsequent validated improvements and complete
costs, including observation and control overhead.

The preset has no predetermined retirement. Revisit its scope against actual
usage and maintenance costs. Replacing it with ordinary planning requires
evidence that the requested research continuation and task coverage are retained;
claims about better outcomes or efficiency require matched comparisons.
The existence of a Goal contract or one successful validation is insufficient.
Historical studies remain pinned to their original product and runner
revisions, task inputs and treatment definitions; frozen configurations and
results are not rewritten or relabeled as evidence for another entry.

## Measurement and attribution

Both entries use the staged runner/Python for `loopx-research-probe`. The
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

These entries retain existing Host admission. They do not install C1's
per-provider-request guard or C3's native submission/feedback recovery.
Those guarantees require their separate entries and qualification; C1's
request-admission entry requires a configured Responses-compatible endpoint.
Local process and integration checks do not establish initial-quality parity,
long-horizon benefit or complete-cost efficiency.

## 中文交付边界

保留 `research_loop=True` 作为明确的 benchmark 研究实验预设，同时提供独立
`execution_observation="local_probe"`。两者共享既有 Explore 证据链，但实验
目的不同：已有候选的单次验证、复现实验或普通工程测量适合独立入口；原任务
明确要求持续优化，并需要研究“初始候选之后是否继续产生收益”时，可选择研究
预设。预设的固定组合便于验证这一接续策略；观测独立则允许不需要该结构的任务
复用测量能力。两种选择均有用途，不将其升级为 LoopX 全局产品模式，也不因配置
成功就断言效果更好。

两份 YAML 示例可分别放入现有 `agents[].kwargs` 配置，保留原模型、任务输入
和预算。独立入口的 `task_entry` 和 `stride_strategy` 分别选择，
`"off"` 须在 YAML 中保留引号。
`loopx-planned` 继续由既有 planner 决定任务结构，`seeded-todo` 只预置保留
完整验收要求的普通任务。采集执行证据不会预建初始候选/研究阶段，也不要求
完成普通任务后继续研究；后续工作由原 Goal、当前证据和剩余有价值工作决定。

独立参数目前只支持 heartbeat；省略或设为 `None` 不启用该入口，同时省略
`research_loop` 时，探针安装、任务阶段和 stride 选择的默认行为不变。
两入口的 Goal 初始化均包含下面说明的有意修复。两入口使用同一个 `loopx-research-probe`
命令，独立入口通过 `LOOPX_EXECUTION_OBSERVATION=local_probe` 传给 worker。
独立入口的规划和 heartbeat 指引要求优先复用与实际执行、候选和输入相符的现有观测；
缺少影响决策的关键测量时，才在需要进行的实验中使用探针，不为补一份回执
重复实验。配置和普通读回不触发探针或模型调用。运行回执记录解析后的 producer，
研究预设保留 `research_loop` 标识；这些配置记录不证明发生了执行或实质采用。

Harbor 新建 Goal 时，普通入口和研究预设均在未显式提供 `task_objective` 时，
将首次完整任务指令去除首尾空白后的 `instruction.strip()` 保存为 objective；
显式值优先。后续 phase 更新任务输入，不替换已有 Goal 的 objective，保留原目标
文本。这项适配器入口修复在未选观测、`None`、`"local_probe"` 和
`research_loop=True` 下均生效，有意替换两入口过去的泛化目标文案。预设的
两阶段、固定 stride 和追加研究指引保留，但默认 Goal 输入已经改变，不能宣称
整个处理与历史版本完全一致。这不是测量参数附带的研究语义，也未修改 Core
Goal 合同或证明模型已经能够自主正确接续。即使未启用观测，实验对比也须固定
product/runner revision，并记录 Goal 初始化的变化。

`research_loop=True` 表示完整研究处理：预建候选 Todo 和研究后继，使用
`evidence_conditioned_v1`、同一探针和原研究指引。即使显式选择 `loopx-planned`
也保留原阶段初始化；显式 `"off"` 等冲突策略仍会被拒绝。预设已选择本地 producer，
因此不能同时指定 `execution_observation="local_probe"`；需要独立规划或其他
stride 时，选择独立入口的配置。变更处理时建立新 trial，不原地改写进行中的工作。

研究预设沿用既有 Todo 依赖、successor 和 Vision：它实际创建 canonical deferred
研究后继及 `resume_when=todo_done:<initial>`，候选完成使其进入普通 resume/replan
接续判断，不等于研究 Todo 完成。普通 Goal 的持续推进指引不会自动创建同一条
持久化后继及依赖，因此两者不能只凭合同文字就视为等价。保留最好候选和有意义
的负结果，依据原目标及剩余工作解释研究结束；Stop、原预算和真实阻塞保持
有效，不要求填满时长或改变原生评测规则。

准备阶段先以 `surface_only` 写入 Agent Vision 范围，再添加后继边。默认
`hard_lease` 下，先读取候选 Todo 的 lease 版本，经 compare-and-swap 取得
独立短暂授权，以该授权的精确 proof 更新 Todo，最后在 `finally` 中释放同一
授权，沿用既有所有权 guard。它会产生真实 lease 事件、版本变化和控制成本，
但不调用模型、不执行实验、不消耗工作 Turn；`surface_only` 不声称研究进展。

已有完整后继边时，准备动作直接返回，不改 Vision，也不再取得 lease。只在本
Agent 的研究 Todo 仍为 deferred 且精确等待当前初始 Todo 完成时，才补缺失
的边；重试复用同一研究 Todo，先写 scope 再写边，先前失败释放后使用新授权。
研究已经转入其他状态、改变等待条件或属于先前初始 Todo 时，原样返回而不修补。
这是有限的初始化恢复，不重开研究或清除真实等待。

现有单次候选验证测试覆盖独立入口的真实进程、Explore 读回以及 Stop/等待保留，
未证明它能替代预设的研究接续，也未证明模型会自主产生有效后续实验或提高效率。
另一个候选到研究的测试验证 synthetic 接续链，同样不等于模型研究收益。
对照实验应给两入口同一个完整显式 `task_objective`，对齐任务、模型、评测器和
预算，记录任务结构、策略与提示的实际组合。整体组合对照只能评价整体处理；
归因单项能力需要额外设计相应对照。除初始质量和后续可验证改善外，还需计算
探针与控制成本在内的完整消耗。

不预定删除研究预设，依据实际用途和维护成本重新判断其范围。若以普通规划替代，
需证明所要求的研究接续及任务覆盖仍然成立；若声称效果或效率更好，则需可比实验。
存在 Goal 合同、通过一次验证都不足以证明它应退役。历史实验继续固定原 product/runner
版本、输入和处理定义，冻结配置及结果不改标为另一入口的验证。

本地探针固定所选文件，记录真实耗时、退出与超时状态；可选 solver 报告与宿主
测量分开，缺失计数保持未知，坏诊断不能抹去可靠测量。证据路径必须新建，原始
输出留在本地。候选变化拒绝归因；文件清单哈希不是原生归档哈希，只覆盖明确
选择的文件及探针前后边界，不涵盖传递依赖或独立写入者。结果仍须对应实际评测
快照、候选和输入版本，计划/实际方法不同须记录理由。

同一可选观测通过既有 Explore 写回、replan 和研究历程保留；输入版本与引用
必须属于该 finding，不认证方法收益或 Goal 完成。finding 引用与观测的
`evidence_refs`、`result_ref` 共用既有 Explore 引用语法，可直接保留
`validation:run/1` 这类原引用；方法 ID 仍使用独立规则。选择 producer 不改变
Goal 的 Explore 模式；需要写回时，仍通过[既有设置](../../loopx/capabilities/explore/README.md#explore-harness-modes)
开启普通证据能力。旧事件缺失观测仍可读，
新事件需要支持该可选字段的 reader。`surface_only` 和未知历史 delivery
不能消费实质结果或创建未解决实验；有用负结果和真实阻塞继续可作为证据。

两入口均沿用现有 Host 准入，不安装 C1 逐次 provider 请求守卫，也不交付 C3
原生提交/反馈恢复。组合 C1 需其独立入口及兼容 Responses 的端点；各组合保证
须分别验证。本地执行和工程回归不证明首次质量持平、长程收益或完整成本效率。
