# EdgeBench feedback-first observations and runner corrections

Exploratory snapshot, 2026-10-16. This is an inert research report, not an active
runner, leaderboard submission or statement of current product defaults.
The governing contract is the
[long-horizon research program](../../../docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md).
Native task metrics and selection remain authoritative.

## What this snapshot supports

The combined experimental profile obtained earlier accepted feedback in the
observed local runs. It did not consistently improve final task outcomes or
reduce token use. Integer compression and both nesting comparisons favored the
native Codex profile; the Rust results were close. Borden's high-score endpoints
used returned aggregate evaluation feedback to infer parameters, so those
endpoints are not evidence of independent blind-modeling generalization.

The observations motivated three bounded runner corrections: faithfully record
effective TurnEnvelope configuration, add a default-off feedback-first planning
option, and repair the source subset used by non-LoopX workers. They do not
establish an isolated effect of any one correction.

**Version boundary:** the scored local profiles combined feedback-first,
explicit TurnEnvelope and an experimental relaxation of guard re-entry.
Early profiles used `beb5d43`; later profiles used `45bbf66a`. The combined
changes were rebased as candidate `12bb5c931` in
[PR #6584](https://github.com/loopx-project/loopx/pull/6584).
The revised proposal withdraws the general guard re-entry change pending
affected-caller validation and corrects a configuration-projection defect found
in review. Therefore these scores do **not** evaluate the revised PR head.
Existing experimental receipts remain pinned to their actual source.

## Local outcome observations

The local group used `gpt-6.1-sol` with `xhigh`, a 90-minute nominal budget,
native feedback, a 900-second submission cooldown and no periodic automatic
evaluation. Workers shared a four-CPU, 16-GiB host; ceilings were not exclusive
reservations. There were 13 runs with released scores, including two without a
normal native final result, plus two startup failures without a score.

All metrics in this table are maximized. Values are rounded for presentation;
best and latest refer to different native observations when a candidate regressed.

| Task | Profile | Best | Latest | Accepted submissions | Closeout |
| --- | --- | ---: | ---: | ---: | --- |
| Borden | Combined LoopX candidate | 109.000 | 109.000 | 6 | Native final present |
| Borden | Native Codex | 69.579 | 65.723 | 6 | Native final present |
| Integer compression | Combined LoopX candidate | 24.714 | 17.008 | 6 | Native final present |
| Integer compression | Native Codex | 28.620 | 18.208 | 6 | Native final present |
| Integer compression | Native Goal | 12.836 | 10.950 | 3 | Native final; self-completed at about 38 minutes |
| Rust reconstruction | Combined LoopX candidate | 26.506 | 26.506 | 5 | Native final present |
| Rust reconstruction | Native Codex | 26.597 | 26.597 | 5 | Native final present |
| Rust reconstruction | Native Goal | 26.597 | 26.597 | 4 | Runner failed; released scores only |
| Nesting, pair 1 | Combined LoopX candidate | 22.655 | 22.655 | 6 | Native final present |
| Nesting, pair 1 | Native Codex | 28.555 | 27.189 | 6 | Native final present |
| Nesting, pair 1 | Native Goal | 32.464 | 29.553 | 6 | Runner failed; released scores only |
| Nesting, pair 2 | Combined LoopX candidate | 7.743 | 7.743 | 6 | Native final present |
| Nesting, pair 2 | Native Codex | 26.532 | 25.283 | 6 | Native final present |

Native Goal's two unscored startup attempts failed before useful model work:
the non-heartbeat worker imported an unstaged checkpoint dependency, and the
native Goal subset omitted its shared stdio dependency. The corrected subset
subsequently ran. These failed attempts are not model scores of zero. The two
scored Native Goal runner failures remain distinct from budget expiry that
successfully produced a normal native final result.

The combined candidate's first accepted feedback was approximately 1.8--3.1
minutes across these tasks, versus 2.8--9.7 minutes for the native profile.
The observed candidate/native uncached-input ratios were approximately 1.06
for Borden, 1.31 for compression, 1.01 for Rust, and 1.67/1.57 for the nesting
pairs. Earlier feedback did not imply lower total cost or a better final score.
There were only one or two comparisons per task, with concurrency and resource
pressure; no statistical or stable-ranking conclusion is claimed.

## Remote observations and incomplete runs

| Group | Task / direction | LoopX best | Native best | Evidence stage |
| --- | --- | ---: | ---: | --- |
| Historical 30-minute group | Borden / maximize | 7.014 | 67.947 | Native finals; native latest 61.671 |
| Historical 30-minute group | Grid / minimize | 2880 | 2112 | Native finals |
| Original planned 24-hour pair | Borden / maximize | 85.631 | 109.000 | Interrupted at 12.40 hours; history selections only |
| First Grid 24-hour pair | Grid / minimize | 2592 | 1971 | Interrupted at 33m46s; one released report per arm, final selection unavailable |
| Second Grid pair | Grid / minimize | 2039 | 1844 | Running snapshot at about 10 hours; 10 submissions each |
| New combined-candidate pair | Borden / maximize | 68.108 | 54.705 | Running snapshot at about 1.3 hours; 2 submissions each |

The historical short group used OAuth, a 300-second cooldown and periodic
evaluation. The later groups used the platform route, a 3,600-second cooldown
and no periodic evaluation. Source, provider, hardware and concurrency also
differed. The new Borden pair disabled checkpoint capture; its early lead
cannot establish a repair of the capture/continuation path. Neither running
pair had completed its intended 24-hour acceptance at this snapshot.

The original Borden pair stopped when an additional cohort and a transient
judge exceeded the existing host-wide container guard. Both arms were
gracefully interrupted. The Grid failure came from a Docker stats response
parse error; later isolated Docker reproduction demonstrated the deletion/read
race, but the original request identity was not retained. These are operational
failures, not solver completion, model zeros or completed long-horizon studies.
Order Addition was isolated after an evaluator self-check failure and contributes
no model score. Deferred ablations were not treated as executed experiments.

## Feedback, context cost and trajectory limits

An observational Borden comparison retained the first 60 minutes from each
native session's first event. Its hardware, source, cooldown, checkpoint and
transport histories still differed.

| Same elapsed window | Original LoopX | Combined local candidate | Native Codex |
| --- | ---: | ---: | ---: |
| Accepted feedback points | 1 | 4 | 1 |
| Identifiable control calls | 71 | 34 | 0 |
| Quota reads | 46 | 11 | 0 |
| Goal-document reads | 45 | 7 | 0 |
| Uncached input tokens | 992,877 | 577,418 | 216,212 |
| Cached input tokens | 12,903,424 | 14,247,168 | 4,255,872 |
| Output tokens | 88,133 | 105,059 | 90,329 |

The candidate reduced uncached input relative to original LoopX by about 42%,
while total input increased about 6.7% and output about 19.2%. This does not
prove a reduction in billed cost. Cache and reasoning counts are subsets of
their respective input/output totals. Native response IDs are deduplicated;
cumulative wake values are not added together. Monetary billing remains
unverified.

In the interrupted Borden pair, original LoopX used about 2.83 times the
native profile's uncached input and 1.03 times its output. It obtained more
feedback but narrowed later candidate changes largely to source strength and
duration, while the native profile made broader joint changes earlier. The
native profile also continued consuming tokens after its best score without
new accepted feedback. Activity counts alone do not establish useful progress.

Tool intervals are reported as unions when parallel work overlaps. Model
request intervals include generation and transport. Time outside observed
tools is a mixture of generation, network, control, waiting and unobserved
work, not measured reasoning time. Compact control-call counts can miss
wrapped calls and cannot independently allocate causal overhead.

## What was repaired, and what remains open

| Boundary | Implemented or observed | Limit |
| --- | --- | --- |
| Native result projection | [#6506](https://github.com/loopx-project/loopx/pull/6506) preserves direction and native selected round | Report correctness, not solver uplift |
| Model-provider parity | [#6520](https://github.com/loopx-project/loopx/pull/6520) routes both profiles through the explicit custom provider | Does not prove permanent route lifetime or warm restart |
| Effective TurnEnvelope | Known parsed `fresh_replan` declarations and actual wake sockets are distinguished from path-only configuration and after-wake capture | A configured path alone must not claim activation |
| Feedback-first | Default-off planned/native-feedback opt-in, with prompt parity when disabled | Work ordering, not extra time, feedback allowance or authority |
| Worker source subset | Non-heartbeat checkpoint import isolation and native Goal stdio dependency staging | Does not repair Native Goal deadline closeout |
| General guard re-entry | Experimental candidate change withdrawn from the revised proposal | Affected-caller semantics still require independent validation |
| Grid resource observation | Private controller lifecycle recheck and bounded retries; nine regression cases including real Docker deletion | Separate operational owner; not a shared solver rule or 24-hour success |
| Capture and retention | Original Borden retained eleven verified inert artifacts and both terminal file archives | Actual receiver admission, restore and whole-run warm parity unverified |

The original Borden observations retained 24 failed wakes, two internal
capture-failure flags, two successful `captured=true` wakes, and an incomplete
last wake. A client timeout could coexist with a later valid controller
artifact; neither a successful outer wake nor a valid tar erases the timeout.
Subsequent successful work does not by itself prove sustained repair of a
fresh-replan fault. Corrected private inventory/chronology tooling similarly
does not change native scores or historical source bytes.

## Publication and acceptance boundary

These are selected, non-countable research aggregates, not a complete public
audit corpus. Exact per-attempt manifests, released feedback/source bindings,
terminal/partial-state distinctions, full trajectories and validation receipts
are retained privately. Public readers cannot independently re-run or qualify
those private studies from this table. No task text, answer, verifier output,
runtime configuration, account identifier, infrastructure address, raw log,
trace, archive or private evidence pointer is published here.

The proposed code is qualified by synthetic regression, real local socket and
source-subset entrypoint checks. Historical score observations do not replace
those checks, and their test counts are not pooled across revisions. Missing
native finals remain missing; integrity, countability, sustained operation,
cost-normalized benefit and actual restoration remain separate acceptance.
This report grants no new model, benchmark, submission, publication or merge
authority. Repeated matched studies require a separately admitted protocol.

### 中文结论

组合候选更早取得了原生反馈，但本地编解码和两次排样没有胜过原生；Rust接近，
Borden高分还受已返回聚合指标反推参数的影响。短测、长测、OAuth与平台、
不同冷却和checkpoint设置不能合并成一个胜率。原Borden与第一轮Grid的24小时
目标均未完成；归档通过不等于整轮终态或真实恢复通过。

本次公开提案保留运行器打包、反馈优先及准确配置记录，撤下尚无独立验证的通用
guard重入放宽。表中实验仍归属其原始组合版本，不代表修订后的PR已经取得同样
效果。完整私人报告和原始证据不进入仓库。
