# Exploratory benchmark behavior findings / 探索性行为发现

`benchmark-toolkit` can share a bounded observation without uploading a complete
study or its run results. A `behavior_finding` is analysis, not a leaderboard
entry, population estimate, or new score authority. Existing study dashboards
continue to derive scores exclusively from run rows. Existing case insights keep
their terminal exact-run attachment requirement.

This record belongs to the existing built-in `benchmark-toolkit` capability;
the existing local upload provider owns storage, digests, readback and revision
lineage. No new capability, provider, network permission or publication authority
is introduced.

## Content contract

Use `benchmark_behavior_finding_v0`. Required fields:

- Identity: `benchmark_id`, `study_id`, `finding_id`, `title`.
- `claim_scope: exploratory_behavior`, plus bounded `settings`.
- `selection`: `basis` (`post_hoc`, `predeclared`, `all_available`), `rule`,
  counting `unit`, positive `population_count` and `sample_count`, and
  `cohort_digest` (SHA-256 of the producer's fixed selection record).
  `all_available` is valid only when `sample_count == population_count`.
- Separate `observation` from `interpretation`.
- `measures`: up to 16 optional numerical summaries; each has `name`, `unit`,
  `aggregation` (`count`, `sum`, `mean`, `median`, `rate`, `difference`),
  `groups` of `{label, value, n}`, and a `caveat`. Values must be finite and
  group sample counts cannot exceed the selected sample. These are producer
  summaries, not recomputed official metrics. Explain metric denominators and
  adjustment rules in the caveat; `n` counts selected units, not test assertions.
  `count` means a subset of those units (an integer from 0 to n); `rate` is a
  fraction from 0 to 1. Use `sum` for totals over other metric units.
- `evidence`: 1–16 `{kind, digest, label, relation, summary}` entries.
  Kinds: `case_insight`, `cohort_summary`, `protocol`; relations: `supports`,
  `contradicts`, `context`. A case-insight digest can reference a locally retained
  redacted insight without uploading the run or raw trajectory. Digests attest
  provenance; the provider does not verify evidence whose contents are unshared.
- Nonempty `limitations`, `counterevidence`, `next_probe`. If no counterexample
  was reviewed, state that explicitly; absence is not evidence of no failures.
- `privacy_classification: public_safe` and
  `producer_redaction_attested: true`. No raw log, trajectory, task text or score
  eligibility field is accepted. Bounded prose still requires human/producer
  privacy review; schema validation is not a secret scanner.
- Fields declared as text or tokens must be JSON strings. Objects, arrays,
  numbers and booleans are rejected rather than implicitly stringified.

## Local workflow

Start with [the synthetic example](../../examples/benchmark-behavior-finding.json)
and replace its content with reviewed observations. No manifest or board upload
is required. Use the same producer identity for later revisions of a finding.

```sh
loopx benchmark upload-envelope --record-kind behavior_finding \
  --payload-json examples/benchmark-behavior-finding.json --producer-id researcher --producer-version v1 \
  --benchmark-id fixture-bench --study-id exploration \
  --idempotency-key validation-v1 --observed-at 2026-01-01T00:00:00+00:00 \
  --source-revision research-v1 --format json > envelope.json
loopx benchmark upload-local --envelope-json envelope.json --store findings.jsonl --format json
loopx benchmark upload-local --envelope-json envelope.json --store findings.jsonl --execute --format json
loopx benchmark upload-readback --store findings.jsonl --record-id <record-id> --format json
loopx benchmark behavior-report --store findings.jsonl \
  --benchmark-id fixture-bench --study-id exploration --format json > report.json
```

Use a new idempotency key and `--supersedes-record-id` when revising the same
finding. The report shows active findings with record/digest/revision provenance.
Reporters render observations, interpretations, sample selection, limitations
and counterevidence together. Do not promote findings into a full study ranking.
Apply the owner's disclosure scope separately from schema validity: an
exploratory label and redaction attestation do not authorize all aggregate
results. A small table of totals or deltas may reconstruct an intentionally
withheld study conclusion. Build the presentation from an explicit allowed
projection, and apply the same scope to folded content, downloadable records,
linked reports and screenshots. Keep any permitted metric categories explicit;
do not infer permission for outcome statistics from permission to share effort.
The local provider performs no network upload. Remote publication requires an
independently authorized provider and an explicit content review.

## Compare a local proxy with released scores

For one run, compare the proxy with a simple control that always picks the
later submission. A search that improves over time can give both rules a high
agreement count. The offline [analysis script](../../benchmark/proxy_order.py)
compares the two rules on exactly the same pairs.

Run `python -m benchmark.proxy_order proxy.json native.json` from the checkout.
Both files have a common `cohort` string, their own metric `direction`
(`minimize` or `maximize`) and a `rows` array. For example:

```json
{"cohort":"example","direction":"minimize","rows":[
  {"source_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","observed_at":10,"score":2},
  {"source_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","observed_at":20,"score":1}]}
```

In the native file, use the accepted source digest, `accepted_at` on the
same clock, and the released score (`null` until a valid score is available).
Select one case, protocol, scoring version, budget and fixed local panel first.
Hash the same candidate content on both sides and include every accepted
candidate in the window, even when its local score is missing. For repeat
measurements choose a rule before inspecting the scores. The script rejects
duplicate source rows. A matching cohort name records the analyst's selection;
the analyst must verify comparable inputs, source digests and timestamps.

The output distinguishes missing coverage, native ties, abstentions, agreements
and reversals. Each local result must precede acceptance of its own candidate.
Pairs share candidates and the solver may already have learned from older
feedback. Counts describe the selected sequence. They give neither independent
trials nor a population accuracy estimate. Close scores can change order under
timing noise. Check same-source repeats where available and keep the ordering
provisional when uncertainty is unknown. To test a prospective choice, freeze
the alternatives and the choice before their respective outcomes are known.
If only one alternative has a label, absolute error can still be measured
but the ranking between alternatives remains unknown.

Use the coverage and counts in a redacted behavior finding when authorized;
retain the study selection, uncertainty and counterexamples alongside the
measurements. This analysis reads caller-supplied local files only.

## 中文说明

行为发现用于分享局部 pattern 与案例解释；无需先上传完整实验数据。它复用
benchmark-toolkit 的 envelope、本地模拟上传、readback 和 supersession，不新增
独立 capability。正式 study report、单 run case insight、跨案例行为发现是三个
不同的分析层次，原有分数与 case insight 资格规则保持原语义。

每条发现必须同时描述设置、选样方式与分母、观察、机制假设、局限、反例及后续验证。
可以引用保留在本地的脱敏 case insight 摘要与摘要哈希；这不代表接收方已经验证了
未共享的轨迹。上传器只验证结构、身份与摘要绑定，不自动认可作者的因果解释。

展示时可围绕正向机制组织材料，同时呈现相关反例和适用范围。事后筛选的长时任务
不能直接解释为模型无关的长程能力；按 baseline 耗时筛选可能带来回归均值偏差。
wall-clock、token 成本与技术进展应分开讨论，不能用较短运行自动推导更高效率。
