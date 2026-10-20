# Execution observations and legacy research stages

## Independent execution observations

For new heartbeat experiments that need local execution measurements, set
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
installation, task stages and stride selection unchanged. Ordinary Goal
initialization is intentionally repaired as described below.
The adapter installs the existing `loopx-research-probe` command and
passes `LOOPX_EXECUTION_OBSERVATION=local_probe` to its worker. Planning and
heartbeat instructions request evidence when relevant: reuse an existing
observation that matches the actual execution, candidate and input; use the
probe during a needed experiment only when a decision-critical measurement
is missing. Do not repeat an experiment merely to produce a new receipt.
Configuration and ordinary readback do not invoke the probe or a model.

Runtime receipts record the resolved producer and retain the legacy
`research_loop` marker when applicable. Those fields describe configuration;
they do not attest that a probe ran, that evidence was adopted or that a
candidate improved. Keep the resolved settings and task input in the
experiment's existing records; no separate product mode is introduced.

## Original Goal at the adapter entry

When Harbor creates an ordinary Goal without an explicit `task_objective`,
its objective is the first complete task instruction with leading/trailing
whitespace removed (`instruction.strip()`). An explicit `task_objective`
takes precedence. Later phases update the task input without replacing the
existing Goal's objective, so phase feedback cannot narrow the original goal.

This adapter initialization repair applies with `execution_observation`
omitted, `None` or `"local_probe"`. It is independent of measurement selection
and changes the previous ordinary entry's generic Goal objective. It does not
change the Core Goal contract or prove that a model will autonomously continue
correctly. The legacy `research_loop=True` combination still uses its original
generic objective when `task_objective` is omitted; an explicit objective
continues to take precedence there too. Compare experiments at pinned product
and runner revisions, including this ordinary-entry change in the treatment
definition even when no observation producer is selected.

## Legacy treatment and migration

`research_loop=True` retains its historical combination for compatibility:
it prepares an initial-candidate Todo and a deferred research successor,
selects `evidence_conditioned_v1`, installs the same probe and delivers the
original research instructions. It still prepares these stages when
`task_entry="loopx-planned"` is explicitly selected. An explicitly
conflicting stride strategy, including `"off"`, remains rejected.

Combining `research_loop=True` with `execution_observation="local_probe"`
is rejected before work begins. To use independent planning or a different
stride strategy, remove `research_loop` and use the configuration above.
Use a new trial for the changed treatment. Historical studies remain bound
to their original product and runner revisions, task inputs and treatment
definition; do not rewrite frozen experiment configurations or relabel their
results as runs of the independent producer.

The compatibility entry uses existing `todo_done` and successor semantics.
Finishing the candidate releases the retained optimization work; it cannot
by itself close that work. Research retains the best candidate, meaningful
negative results and reasoned closeout against the original objective.
User Stop, the original budget and genuine blockers remain authoritative.
Preparation writes ordinary Agent Vision scope as `surface_only`, without
claiming research progress or spending a work Turn. Existing research work
is not reopened, a real wait is not cleared and later Vision is not replaced.

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

新 heartbeat 实验按需选择 `execution_observation="local_probe"`，上述 YAML
可直接放入现有 `agents[].kwargs` 配置；保留原模型、任务输入和预算。
`task_entry` 和 `stride_strategy` 独立选择，`"off"` 须在 YAML 中保留引号。
`loopx-planned` 继续由既有 planner 决定任务结构，`seeded-todo` 只预置保留
完整验收要求的普通任务。采集执行证据不会预建初始候选/研究阶段，也不要求
完成普通任务后继续研究；后续工作由原 Goal、当前证据和剩余有价值工作决定。

独立参数目前只支持 heartbeat；省略或设为 `None` 不启用该入口，同时省略
`research_loop` 时，探针安装、任务阶段和 stride 选择的默认行为不变。
普通 Goal 初始化则包含下面说明的有意修复。新旧入口使用同一个 `loopx-research-probe`
命令，新入口通过 `LOOPX_EXECUTION_OBSERVATION=local_probe` 传给 worker。
规划和 heartbeat 指引要求优先复用与实际执行、候选和输入相符的现有观测；
缺少影响决策的关键测量时，才在需要进行的实验中使用探针，不为补一份回执
重复实验。配置和普通读回不触发探针或模型调用。运行回执记录解析后的 producer，
旧入口仍保留 `research_loop` 标识；这些配置记录不证明发生了执行或实质采用。

Harbor 新建普通 Goal 时，未显式提供 `task_objective` 就将首次完整任务指令
去除首尾空白后的 `instruction.strip()` 保存为 objective；显式值优先。
后续 phase 更新任务输入，不替换已有 Goal 的 objective，避免阶段反馈收窄
原目标。这项适配器入口修复在未选观测、`None` 和 `"local_probe"` 下均生效，
有意替换普通入口过去的泛化目标文案，不是测量参数附带的研究语义，也未修改
Core Goal 合同或证明模型已经能够自主正确接续。旧 `research_loop=True`
组合未指定 objective 时仍保留原泛化定义，显式值仍优先。即使未启用观测，
实验对比也须固定 product/runner revision，并记录普通 Goal 初始化的变化。

`research_loop=True` 继续表示完整旧处理：预建候选 Todo 和研究后继，使用
`evidence_conditioned_v1`、同一探针和原研究指引。即使显式选择 `loopx-planned`
也保留原阶段初始化；显式 `"off"` 等冲突策略仍会被拒绝。不能同时启用旧参数
和 `execution_observation="local_probe"`；需要独立规划或策略时，移除旧参数，
采用上述新配置，并建立新 trial。旧实验继续固定原 product/runner 版本、输入
和处理定义，冻结配置及历史结果不改标为新入口的验证。

兼容入口沿用既有 Todo 依赖、successor 和 Vision：候选完成只释放后续研究，
准备阶段使用 `surface_only`，不消耗工作 Turn、不声称已经取得研究收益。
已有研究不会被重开，真实等待不会被清除，后续 Vision 不被准备动作覆盖。
保留最好候选和有意义的负结果，依据原目标及剩余工作解释研究结束；Stop、
原预算和真实阻塞保持有效，不要求填满时长或改变原生评测规则。

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
