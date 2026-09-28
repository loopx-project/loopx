# RFC: Benchmark Study Upload and Dashboard Projection v0

| Field | Value |
|---|---|
| Status | Accepted |
| Supersedes / closes | none |
| Date | 2026-09-02 |
| Authors | LoopX maintainers |
| Scope | Provider-neutral benchmark study description, upload records, and read-only dashboard projections |

## 1. Decision

LoopX should let benchmark operators describe a study once, upload compact
public-safe records through an optional provider, and inspect the same facts at
campaign, arm, case, and run granularity.

This proposal does not create another scoring authority:

1. The benchmark-native runner and scorer remain authoritative for outcomes.
2. `benchmark_experiment_board_row_v0` remains authoritative for one run's
   lifecycle, metrics, countability, effort, and insight status.
3. Existing matched-pair and factorial reducers remain authoritative for
   eligible comparisons.
4. A study manifest declares design intent and metric meaning; an upload
   envelope transports allowed records; the dashboard is a derived read model.

The implementation should land in two PRs after this RFC: one for the typed
contract and reducers, then one for a read-only dashboard. Choosing or shipping
a hosted storage provider is outside v0.

## 2. Problem and boundary

The experiment board can answer whether individual runs and comparisons are
countable, but it does not yet carry a compact study-level declaration or a
dashboard-oriented projection. Operators therefore have to reconstruct intended
coverage, arm semantics, denominators, runtime health, and benchmark-specific
metric roles outside the capability.

The v0 surface must be reusable across benchmark families. It must not:

- hard-code a four-arm study, DeepSWE, one model provider, or one sink;
- reinterpret native scores or make non-countable rows formally comparable;
- launch, stop, retry, grade, or mutate benchmark runs;
- upload raw tasks, trajectories, logs, hidden tests, verifier output,
  credentials, or local filesystem paths;
- grant network, runner, scorer, or private-evidence authority;
- turn dashboard state into execution or Todo authority.

## 3. Authority and record model

### 3.1 Study manifest

`benchmark_study_manifest_v0` declares immutable comparison intent:

- `benchmark_id`, `study_id`, `protocol_id`, and case-set identity;
- factor ids, legal levels, arm-to-factor assignments, and baseline arm;
- the primary metric, guardrail and supporting metric catalog;
- intended case and arm coverage;
- comparison protocol and source revisions;
- public-safe labels and optional benchmark extension metadata.

An arm is a set of typed factor assignments, not a special name. For example:

| Arm | `orchestrator` | `domain_hint` |
|---|---|---|
| `goal_plain` | `goal` | `none` |
| `loopx_plain` | `loopx` | `none` |
| `goal_domain_hint` | `goal` | `domain_specific` |
| `loopx_domain_hint` | `loopx` | `domain_specific` |

This representation supports two-arm studies, four-arm factorial studies, and
other declared designs without changing the dashboard schema. Existing
factorial reducers continue to decide whether conditional effects and an
interaction contrast are countable.

### 3.2 Upload envelope

`benchmark_upload_envelope_v0` carries exactly one allowlisted record kind:

- a study manifest;
- an existing experiment-board row;
- a redacted case-insight projection;
- an existing public-safe runtime observation.

The envelope includes producer identity and version, benchmark and study ids,
record kind, stable idempotency key, observation time, source revision, privacy
classification, and payload. The contract validates identity alignment between
the envelope and payload. A provider must return a compact readback receipt
binding the accepted record id, digest, and provider revision.

Retries with the same idempotency key and digest are replays. A corrected
terminal upload is a new envelope that explicitly supersedes the prior
transport record and still obeys the experiment board's legal transition
rules; it must not silently rewrite board authority. The core contract defines
validation and receipts, but neither holds provider credentials nor performs an
external write without a separately activated provider.

### 3.3 Redacted case insight

`benchmark_case_insight_projection_v0` reduces post-run analysis to:

- outcome and failure class;
- compact causal explanation and expectedness;
- benchmark or LoopX implication;
- next probe and confidence;
- public-safe evidence handles or digests.

The projection is not the analyst's raw evidence packet. It cannot contain raw
task text, trajectory excerpts, hidden evaluator material, verifier tails, or
host-local paths.

An insight upload must attach to one already uploaded active exact-run board row.
That row must be terminal with `insight.status=complete`, and its case, run, and
outcome identities must match the projection. The board row remains the only arm,
score, countability, integrity, and treatment-fidelity authority.
This deliberately tightens the local simulation's prior permissive behavior:
orphan, pre-terminal, and outcome-mismatched insight uploads are rejected.

`benchmark_runtime_observation_v0` is reused unchanged for exact-job authority,
runner liveness, terminal discovery, and runner-invalid classification.
Occupancy and provider-pressure signals come from the existing concurrency
envelope and feedback contracts; retry and timeout facts remain typed run or
provider observations. V0 does not introduce a second schema for any of these
responsibilities.

## 4. Dashboard projection

`benchmark_study_dashboard_v0` is derived from one manifest plus public-safe
records. It has four drill-down levels:

1. **Campaign summary** — intended and completed coverage, score-countable and
   matched denominators, complete factorial cases, in-flight count, primary and
   binary outcomes by arm, paired contrasts, effort, and runtime health.
2. **Arm detail** — protocol and revision, score and success distributions,
   effort distribution, failure taxonomy, and applicable mechanism receipts.
3. **Case matrix** — each arm's metrics, countability, effort, largest eligible
   contrast, and redacted insight for the same case.
4. **Run detail** — lifecycle, metrics, qualification receipts, provenance,
   and public-safe artifact handles.

Every aggregate names its denominator. Formal comparisons include only rows
accepted by the existing matched-pair or factorial contracts. Incomplete
coverage is labelled `provisional`; a larger raw run count must never look like
a stronger matched result.

For ratio metrics, the dashboard may show both case-macro and suite-micro
aggregation when the manifest declares both. For binary outcomes, it should
show success rate and paired `0 -> 1` / `1 -> 0` transitions.

### 4.1 Benchmark-specific metric mapping

Adapters map native metrics into the manifest catalog without changing their
meaning. A software-engineering benchmark may declare:

| Role | Example metric | Dashboard use |
|---|---|---|
| Primary | feature or requirement pass ratio | Main partial outcome |
| Guardrail | preservation or regression pass ratio | Existing behavior safety |
| Guardrail | binary reward | End-to-end success rate and transitions |
| Supporting | duration, steps, turns, tokens, estimated cost | Effort and efficiency |

Partial progress must not replace the feature, preservation, or binary outcome.
Other benchmark families keep their own native metric names, units, directions,
and success thresholds.

For example, a DeepSWE adapter can map feature/F2P into the primary partial
outcome, preservation/P2P and reward into guardrails, and duration, steps,
turns, tokens, and estimated cost into supporting effort metrics. That mapping
is adapter metadata in the manifest, not a DeepSWE field in the core schema.

## 5. Privacy and provider boundary

The upload allowlist contains compact metrics, typed qualification reason codes,
redacted insights, digests, and provider-scoped artifact handles. Raw task text,
raw trajectories, logs, hidden tests, verifier output, credentials, auth files,
Docker sockets, and local paths remain outside the contract.

Typed record kinds and schemas enforce this boundary. The implementation should
not rely on a substring denylist to decide whether arbitrary prose is safe.
Record producers own redaction before envelope construction, and a provider may
apply stricter policy without weakening the core boundary.

An optional provider owns authentication, transport, retention, and remote
readback. Installing the benchmark capability or rendering a dashboard grants
none of those permissions.

## 6. Delivery slices

### PR 1: Contract and reducers

- add the study manifest, upload envelope/readback receipt, and redacted insight
  projection schemas;
- reuse experiment-board, factorial, and runtime-observation contracts;
- derive a provider-neutral dashboard data packet without rendering UI;
- expose preview/validation CLI entry points, with no implicit network write;
- add semantic tests for identity alignment, idempotent replay, supersession,
  denominator disclosure, incomplete coverage, and private-boundary rejection.

### PR 2: Read-only dashboard

- render the campaign, arm, case, and run views from the derived packet;
- preserve countability and provisional labels in every view;
- provide local readback and focused rendering/accessibility validation;
- complete the repository's first-screen preview gate before finalizing the
  dashboard presentation.

A concrete remote provider should be proposed only when a real sink supplies
an authentication, privacy, transport, and readback lifecycle to validate.

## 7. Acceptance criteria

The v0 direction is accepted when:

- one manifest can represent both a simple baseline/treatment study and a
  factorized four-arm study;
- existing experiment-board rows round-trip through an envelope without losing
  metric, countability, effort, fidelity, or provenance meaning;
- reducers expose campaign, arm, case, and run projections with explicit
  denominators and no new scoring decisions;
- the dashboard renders benchmark-native partial, binary, guardrail, effort,
  and runtime-health data without benchmark-specific core fields;
- invalid identity, unknown record kind, silent terminal rewrite, and forbidden
  evidence references fail closed;
- all external transport remains optional, provider-owned, and independently
  authorized.

---

# RFC：Benchmark Study 上传与看板投影 v0（中文版）

| 字段 | 内容 |
|---|---|
| 状态 | 已接受 |
| 日期 | 2026-09-02 |
| 作者 | LoopX 维护者 |
| 范围 | Provider-neutral 的 benchmark study 描述、上传记录与只读看板投影 |

## 1. 决策

LoopX 应允许 benchmark 操作者只描述一次 study，通过可选 provider 上传紧凑、
可公开的安全记录，并在 campaign、arm、case、run 四个粒度查看同一组事实。

本提案不引入新的评分权威：

1. benchmark 原生 runner 和 scorer 仍是结果权威；
2. `benchmark_experiment_board_row_v0` 仍是单次 run 生命周期、指标、
   可计数性、开销和 insight 状态的权威；
3. 现有 matched-pair 与 factorial reducer 仍决定哪些比较可计数；
4. study manifest 声明实验设计意图和指标含义，upload envelope 传输允许的
   记录，看板只是派生的只读模型。

本 RFC 之后分两个 PR 实现：先实现 typed contract 与 reducer，再实现只读
看板。v0 不选择或交付任何托管存储 provider。

## 2. 问题与边界

实验板已经可以回答单个 run 与比较是否可计数，但尚无紧凑的 study 级声明，
也没有面向看板的投影。操作者因此只能在 capability 外部重建预期覆盖率、arm
语义、分母、运行健康度和 benchmark-specific 指标角色。

v0 必须可复用于不同 benchmark 家族，并且不得：

- 写死四臂实验、DeepSWE、某个模型 provider 或某个 sink；
- 重新解释原生分数，或让不可计数的 row 获得正式可比性；
- 启动、停止、重试、评分或修改 benchmark run；
- 上传原始题目、轨迹、日志、隐藏测试、verifier 输出、凭证或本地文件路径；
- 授予网络、runner、scorer 或私有证据权限；
- 让看板状态成为执行或 Todo 权威。

## 3. 权威与记录模型

### 3.1 Study manifest

`benchmark_study_manifest_v0` 声明不可变的比较意图：

- `benchmark_id`、`study_id`、`protocol_id` 与 case-set identity；
- factor id、合法 level、arm 到 factor 的赋值，以及 baseline arm；
- primary metric、guardrail 与 supporting metric catalog；
- 预期 case 和 arm 覆盖；
- comparison protocol 与 source revision；
- 可公开的安全标签和可选 benchmark extension metadata。

arm 是一组 typed factor assignment，而不是一个具有特殊语义的名称。例如：

| Arm | `orchestrator` | `domain_hint` |
|---|---|---|
| `goal_plain` | `goal` | `none` |
| `loopx_plain` | `loopx` | `none` |
| `goal_domain_hint` | `goal` | `domain_specific` |
| `loopx_domain_hint` | `loopx` | `domain_specific` |

这种表示无需修改看板 schema，即可支持两臂实验、四臂析因实验以及其他已声明
的实验设计。现有 factorial reducer 继续决定条件效应和交互对比是否可计数。

### 3.2 Upload envelope

`benchmark_upload_envelope_v0` 每次只携带一种 allowlist 中的记录：

- study manifest；
- 现有 experiment-board row；
- 脱敏后的 case-insight projection；
- 现有可公开的安全 runtime observation。

envelope 包含 producer identity 与版本、benchmark 和 study id、record kind、
稳定的 idempotency key、observation time、source revision、privacy
classification 和 payload。contract 校验 envelope 与 payload 的 identity 是否
一致。provider 必须返回紧凑的 readback receipt，绑定被接受的 record id、
digest 与 provider revision。

相同 idempotency key 和 digest 的重试视为 replay。对终态上传进行纠正时，必须
创建新的 envelope，显式 supersede 先前的 transport record，并继续遵守实验板
的合法 transition 规则；不得静默改写实验板权威。core contract 定义校验与
receipt，但不持有 provider 凭证，也不会在未单独激活 provider 时执行外部写入。

### 3.3 脱敏 Case Insight

`benchmark_case_insight_projection_v0` 将 run 后分析归约为：

- outcome 与 failure class；
- 紧凑的因果解释及结果是否符合预期；
- 对 benchmark 或 LoopX 的启示；
- next probe 与 confidence；
- 可公开的安全 evidence handle 或 digest。

该 projection 不是分析者的原始证据包，不得包含原始题目文本、轨迹片段、隐藏
evaluator 材料、verifier 尾部输出或宿主机本地路径。

insight 上传必须绑定到已上传且处于 active 状态的同一个 exact-run 实验板记录。
该 run 必须已经终态，且 `insight.status=complete`；projection 的 case、run 和
outcome 身份必须与其一致。arm、score、countability、integrity 与
treatment-fidelity 的唯一权威仍是实验板记录。
这是对本地模拟既有宽松行为的有意收紧：孤立、未终态或 outcome 不匹配的
insight 上传会被拒绝。

`benchmark_runtime_observation_v0` 原样复用于 exact-job authority、runner
liveness、终态发现和 runner-invalid 分类。occupancy 与 provider-pressure 信号
来自现有 concurrency envelope 和 feedback contract；重试与超时事实继续作为
typed run 或 provider observation。v0 不为这些职责引入第二套 schema。

## 4. 看板投影

`benchmark_study_dashboard_v0` 由一个 manifest 和可公开的安全记录派生，包含
四层下钻：

1. **Campaign summary**：预期与已完成覆盖、score-countable 与 matched 分母、
   完整 factorial case、在途数量、各 arm 的 primary 与 binary outcome、配对
   contrast、effort 和 runtime health；
2. **Arm detail**：protocol 与 revision、分数和成功率分布、effort 分布、失败
   分类及适用的 mechanism receipt；
3. **Case matrix**：同一 case 下各 arm 的指标、可计数性、effort、最大合法
   contrast 与脱敏 insight；
4. **Run detail**：生命周期、指标、qualification receipt、provenance 与可公开
   的安全 artifact handle。

每个聚合值都必须标明分母。正式比较只能包含被现有 matched-pair 或 factorial
contract 接受的 row。覆盖未完成时必须标记为 `provisional`；更多原始 run 数量
不得被表现为更强的 matched 结论。

对于比例指标，若 manifest 同时声明，dashboard 可以同时展示 case-macro 与
suite-micro 聚合。对于 binary outcome，应展示成功率和配对的 `0 -> 1` /
`1 -> 0` 转移。

### 4.1 Benchmark-specific 指标映射

adapter 将原生指标映射到 manifest catalog，但不得改变指标含义。软件工程类
benchmark 可以声明：

| 角色 | 指标示例 | 看板用途 |
|---|---|---|
| Primary | feature 或 requirement pass ratio | 主要 partial outcome |
| Guardrail | preservation 或 regression pass ratio | 既有行为安全性 |
| Guardrail | binary reward | 端到端成功率与状态转移 |
| Supporting | duration、steps、turns、tokens、estimated cost | 开销与效率 |

partial progress 不得取代 feature、preservation 或 binary outcome。其他
benchmark 家族继续使用自身原生的指标名称、单位、方向和成功阈值。

例如，DeepSWE adapter 可以将 feature/F2P 映射为主要 partial outcome，将
preservation/P2P 与 reward 映射为 guardrail，并将 duration、steps、turns、
tokens 和 estimated cost 映射为 supporting effort metric。该映射是 manifest
中的 adapter metadata，而不是 core schema 中的 DeepSWE 字段。

## 5. 隐私与 Provider 边界

upload allowlist 只包含紧凑指标、typed qualification reason code、脱敏 insight、
digest 和 provider-scoped artifact handle。原始题目、原始轨迹、日志、隐藏测试、
verifier 输出、凭证、auth 文件、Docker socket 和本地路径均在 contract 之外。

该边界由 typed record kind 与 schema 强制执行。实现不应依赖 substring denylist
来判断任意文本是否安全。record producer 在构造 envelope 前负责脱敏；provider
可以实施更严格的策略，但不能削弱 core 边界。

可选 provider 负责认证、传输、保留与远端 readback。安装 benchmark capability
或渲染 dashboard 均不会授予上述权限。

## 6. 交付拆分

### PR 1：Contract 与 Reducer

- 添加 study manifest、upload envelope/readback receipt 与脱敏 insight
  projection schema；
- 复用 experiment-board、factorial 和 runtime-observation contract；
- 派生 provider-neutral dashboard data packet，但不渲染 UI；
- 提供 preview/validation CLI 入口，不隐式执行网络写入；
- 为 identity alignment、idempotent replay、supersession、分母披露、不完整
  覆盖与私有边界拒绝添加语义测试。

### PR 2：只读看板

- 从派生 packet 渲染 campaign、arm、case 与 run 视图；
- 在每个视图中保留可计数性和 provisional 标记；
- 提供本地 readback，以及聚焦的 rendering/accessibility 校验；
- 在最终确定 dashboard 展示前完成仓库首屏预览门禁。

只有当真实 sink 提供可验证的认证、隐私、传输与 readback 生命周期时，才应提议
具体的远端 provider。

## 7. 验收标准

满足以下条件时，v0 方向可被接受：

- 一个 manifest 同时能够表达简单 baseline/treatment 实验和析因四臂实验；
- 现有 experiment-board row 可通过 envelope 往返，且不丢失 metric、
  countability、effort、fidelity 或 provenance 语义；
- reducer 提供 campaign、arm、case、run 投影，显式标明分母，且不引入新的
  评分决策；
- dashboard 能展示 benchmark-native 的 partial、binary、guardrail、effort
  与 runtime-health 数据，core schema 中不出现 benchmark-specific 字段；
- 非法 identity、未知 record kind、静默终态改写和受禁 evidence reference
  必须 fail closed；
- 所有外部传输始终是可选的、由 provider 拥有，并需独立授权。
