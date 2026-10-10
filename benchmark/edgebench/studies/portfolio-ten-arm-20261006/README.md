# Portfolio risk calibration: ten-arm retrospective snapshot

**Exploratory observations, not a matched causal comparison or leaderboard submission.**
This snapshot contains ten designated attempts on `portfolio_risk_calibration`:
five worker profiles × native/blind feedback. Eight use ledger-v2; the two
replacement Explore attempts use the later VaR-timing-v1 evaluator **and different
public task instructions and LoopX/runner source**. Do not attribute a cross-cohort
score difference to Explore. All ten remain `score_countable=false`: a recorded
score is not an integrity-qualified result.

## Results and exact settings

Scores are benchmark-native raw scores, higher is better. “Best” includes native
agent submissions where available as well as host auto-evaluations. “Last” is the
last recorded submission, **not an independent final-artifact replay**. Runtime
is the runner's measured solving duration. Every arm had an 18-hour ceiling;
several stopped early, so actual effort and evaluation opportunities differ.

| Arm / settings | Evaluator | Best | Last | Runtime (min) | Agent / auto evaluations |
|---|---|---:|---:|---:|---:|
| [official-native](data/settings/portfolio-official-native-ledgerv2-01.json) | ledger-v2 | 53.87 | 53.87 | 1080.01 | 140 / 215 |
| [official-blind](data/settings/portfolio-official-blind-ledgerv2-01.json) | ledger-v2 | 11.58 | 0.00 | 1080.01 | 0 / 215 |
| [single-native](data/settings/portfolio-single-native-ledgerv2-01.json) | ledger-v2 | 28.08 | 28.08 | 46.82 | 14 / 9 |
| [single-blind](data/settings/portfolio-single-blind-ledgerv2-01.json) | ledger-v2 | 12.41 | 9.23 | 73.68 | 0 / 14 |
| [native-goal-native](data/settings/portfolio-native-goal-native-ledgerv2-01.json) | ledger-v2 | 26.93 | 26.93 | 220.84 | 16 / 44 |
| [native-goal-blind](data/settings/portfolio-native-goal-blind-ledgerv2-01.json) | ledger-v2 | 14.17 | 0.00 | 34.49 | 0 / 6 |
| [heartbeat-resume-native](data/settings/portfolio-heartbeat-resume-native-ledgerv2-01.json) | ledger-v2 | 24.04 | 24.04 | 1079.99 | 34 / 215 |
| [heartbeat-resume-blind](data/settings/portfolio-heartbeat-resume-blind-ledgerv2-01.json) | ledger-v2 | 17.32 | 15.57 | 1080.00 | 0 / 215 |
| [heartbeat-explore-native](data/settings/portfolio-heartbeat-explore-native-qualifiedv13-01.json) | VaR-timing-v1 | 31.19 | 31.19 | 1079.99 | 21 / 142 |
| [heartbeat-explore-blind](data/settings/portfolio-heartbeat-explore-blind-qualifiedv13-02.json) | VaR-timing-v1 | 14.96 | 0.00 | 1080.00 | 0 / 142 |

- [Scores and durations](data/scores.csv): one row per designated attempt.
- [All 1,442 recorded scoring observations](data/samples.csv): run identity,
  submission sequence, round, elapsed seconds, score and native validity flag.
  Elapsed time uses each report's `submitted_at` minus its run's start epoch;
  this is submission time, not evaluator completion time. No interpolated points.
- [Canonical experiment-board rows](data/run-rows.json): original study/benchmark
  identities, attempts, comparison anchors, insight status and qualification.
- [Binding inventory](data/index.json): each run binds a settings file and its
  SHA-256; all data files and the four private source receipts are hashed.
  Private source files are deliberately not included. Hashes establish byte
  identity, not independent verification or disclosure approval.

The two Explore rows were still `running` in the source board. This export
reconciles them from terminal runtime/final receipts; it refreshes best score and
measured duration for every arm. It preserves integrity, treatment-fidelity and
insight decisions and does not write to the live board or restart experiments.
Older Explore attempts are excluded by explicit selection, not spliced into the
replacement curves. Run IDs and attempts are retained in every data join.

## Settings and reproducibility boundary

All ten: `gpt-6.1-sol`, `xhigh`, timeout 64,800 seconds, auto-evaluation interval
300 seconds, native submission cooldown 120 seconds, task internet disabled.
The pinned runner declares work resources 4 CPUs / 16 GiB and judge resources
4 CPUs / 8 GiB. These are declared per-container limits, not evidence of exclusive
hardware or identical resource contention. Images and task-file hashes differ
across evaluator cohorts. The task hash covers the task JSON file, **not the
complete evaluator**; image IDs are separate provenance and do not imply public
registry availability.

| Worker profile | Completion / continuation | Explore |
|---|---|---|
| official | Native SForge Codex Stop hook and native outer recovery | off |
| single | One Codex invocation; no outer resume | off |
| native-goal | Native Codex Goal transport; no external resume loop | off |
| heartbeat-resume | LoopX scheduler, same-session resume | off |
| heartbeat-explore | LoopX scheduler, same-session resume | graph + analysis-only `adaptive-resilient` harness; no child execution authority |

For heartbeat arms, pinned historical source uses `seeded-todo` and replans after
three completed Todos; it predates effective-turn cadence experiments. These
source-derived settings are distinguished from recorded worker/profile receipts.
Enabling a hook does not establish graph use or adoption by the model.

Native feedback permits submission and returned judge feedback. Blind workers
have local validation and model API access but no judge URL/token or submission
command; host evaluation results are withheld from the worker. Thus native and
blind arms do not have equal feedback opportunities even within an evaluator
cohort. Reported agent/auto counts make that difference visible.

The eight ledger-v2 arms pin LoopX and runner to
`91a4672164238fc2ddc394d61901910984c5e84a`; both replacement Explore arms pin them to
`4e820bb0ced6c0f8dff2acd641a6c19dbc986935`. Each settings file carries full pins,
work/judge image IDs, task hash, public launch argv, worker profile and provenance
for reconstructed defaults. Deployment paths, credentials and endpoint addresses
are omitted. Codex binary digest, SForge distribution digest, seed, effective
token/cost consumption and remote image locations were not captured in this
export and are explicitly unknown. This supports configuration inspection, not a
claim that an independent party can already reproduce the full environment.

To inspect rather than launch historical configuration, open an arm's settings
link above. Reconstruct an authorized run only using that file's exact pins and
public `runner_argv`, supplying your own task/image sources, log directory and
judge/API endpoints. Never silently substitute current `main` defaults for a
historical setting.

## Bounded observations and next experiments

1. **Best-so-far can conceal deterioration.** Heartbeat-resume blind first reached
   17.32 at approximately 10 minutes, then finished its 18-hour window with last
   observed score 15.57. Official blind's best was 11.58 but its last sample was
   zero; native-goal blind similarly had best 14.17 and last zero. Preserve both
   views before interpreting a flat “best” curve as stable solution quality.
2. **Long continuation alone did not guarantee improvement.** Resume blind has
   215 auto-evaluations and 12 recorded outer resumes without exceeding that
   early best. These are observations, not proof that resume caused stagnation.
   A discriminating next analysis links candidate changes, local validation and
   retention decisions to subsequent independently scored artifacts.
3. **Explore native is a mechanism-analysis candidate, not a treatment win.** Its
   best 31.19 first appears at approximately 92.51 minutes and equals its last
   observation. Its 21 agent submissions and 142 auto-evaluations differ from
   official native's 140 and 215. Evaluator, public instructions and source all
   changed; an Explore-only effect cannot be identified here.
4. **Equal budget ceilings are not equal spending.** Single and native-goal arms
   stopped well before 18 hours. One trial per arm, post-hoc replacement selection,
   unequal sampling/feedback and unqualified integrity preclude uncertainty
   estimates, pooled uplift claims and causal rankings. Repeat matched versions,
   measure actual spend and preregister final-artifact versus best-so-far metrics.

The ledger-v2 evaluator's independent VaR used end-of-day holdings against the
same day's realized return. VaR-timing-v1 aligns the exposure timing. The old
scores are retained as historical evaluator outputs; they are not relabeled as
corrected scores. No retrospective rescoring was performed for this release.

## Existing report and insight mechanisms

The [benchmark toolkit](../../../../loopx/capabilities/benchmark_toolkit/README.md)
owns experiment rows, integrity/countability and upload envelopes. Its case-insight
projection requires complete post-run analysis and an exact terminal run row;
this report does not fabricate completion of pending insights. The
[behavior-finding contract](../../../../docs/reference/benchmark-behavior-findings.md)
is available for selected, evidence-digested mechanism observations without score
authority.

This retrospective collection spans two original studies, two baselines and an
Explore cohort with no same-version baseline. The current study-manifest contract
requires exactly one baseline. We therefore retain canonical run rows and their
original identities instead of inventing a baseline or relabeling this as a
preregistered matched study. The rows can use the existing upload-envelope/local
simulation/readback flow independently. A future multi-cohort display should
compose existing studies without changing their comparison intent.

The reusable adapter [export_report.py](../../export_report.py) adds a narrow
settings-bound export and read-only verifier. It uses the existing Python native
receipt adapter and canonical row normalizer; there is no new control-plane
policy, shared state vocabulary, remote uploader or scoring rule. It reads only
explicitly selected terminal metadata and numeric report fields, not sessions or
solution files. Operator-supplied supplemental settings must already be reviewed
for publication. A digest check does not replace that review.

```sh
uv run --extra test python -m benchmark.edgebench.export_report \
  --verify benchmark/edgebench/studies/portfolio-ten-arm-20261006/data
```

For another terminal collection, supply an explicit JSON list of
`{"row": <canonical board row>, "settings": <reviewed supplemental settings>}`
and run the same module with `--runs-root`, `--selection`, `--output` and
`--observed-at`. It refuses an existing destination and mismatched source/model/
score identities. Keep private inputs outside the repository. No model or
benchmark job is launched by export or verification.

## Trajectory hosting assessment

Two selected sessions, heartbeat-resume blind ledger-v2 and heartbeat-explore
native qualified-v13, are approximately 56.1 and 96.5 MiB uncompressed. They are
selected for plateau/retention and Explore-mechanism analysis, not as representative
random samples or a matched pair. Full sessions, task text, verifier output,
solution archives and private control state are excluded from this repository.

Recommended split: GitHub for this small reviewable report; a versioned Hugging
Face dataset for separately reviewed/redacted sessions, linked by run ID, settings
hash, dataset revision and file hash. The Hub's
[agent trace viewer](https://huggingface.co/docs/hub/agent-traces) supports Codex
JSONL directly. Keep call/result references and event order when redacting; retain
a local redaction manifest identifying removed categories and counts. Do not copy
an entire run directory or assume an automated secret scan certifies publication.
A dataset card should state selection, settings, limitations and redistribution
scope for task/tool content before assigning a license.

For collaborator-only access, use an appropriate organization-owned private
dataset; a personal private repository does not provide general collaborator
access. Confirm membership and visibility before upload. See
[Hub dataset privacy](https://huggingface.co/docs/hub/datasets-overview#privacy)
and [repository settings](https://huggingface.co/docs/hub/repositories-settings).
The [public trajectory dataset](https://huggingface.co/datasets/huangrt01/edgebench-portfolio-trajectories-20261006)
contains two redacted execution projections and their exact settings. The initial
release is pinned to [revision 21a184bb](https://huggingface.co/datasets/huangrt01/edgebench-portfolio-trajectories-20261006/tree/21a184bb1c782b83c9de96fcb79724721ddb13eb).
It retains 1,303 and 1,822 tool call/result pairs respectively. Omitted instruction
text, internal metadata, reasoning blocks, duplicated events and withheld tool
content are documented in its dataset card and redaction manifest; it is not a
complete unredacted archive.

## 中文阅读提示

本报告使用十臂的指定 attempt，保留 1,442 个真实评分点，每臂绑定 runner、LoopX、
模型、预算、反馈权限、任务文件和镜像版本配置。八臂使用 ledger-v2，两个 Explore
替换 attempt 使用 VaR 时间对齐修正版；它们可以同页查看，不能直接当作严格消融。
全部结果仍未完成完整性核验，不能据此宣称 LoopX 或 Explore 的因果收益。

`best_score` 是历史最好值，`last_observed_score` 是最后一次记录的评分，两者都不自动
等于终态工件独立复测。blind 的早期高点、后期退化，以及较短组的提前结束，是下一步
值得核验的机制问题。配置中未知项保持未知，原始轨迹另行脱敏、选择可见范围后托管。
