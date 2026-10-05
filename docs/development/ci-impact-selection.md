# CI job exemptions / 按职责免跑重型 CI

## Policy, not a hand-maintained test selection

Every PR receives the same stable merge gate. The classifier reads the complete
NUL-delimited Git diff at immutable base/head revisions; a PR title, label or
author description cannot claim that runtime changes are “just UI”.

| Whole PR | Common TS/lint/contracts | Full Python / Windows | Stage2c | Packaged Dashboard |
| --- | --- | --- | --- | --- |
| Existing Markdown-only documentation exemption | Skip | Skip | Skip | Not published by Frontstage Pages |
| Client Dashboard source/assets only, optionally with docs | Run | Skip | Skip | Required build, freshness and browser smoke |
| Backend, prompt, tests, dependency, build, CI policy, mixed or unknown | Run | Run | Run | Also run for CI-policy rehearsal or forced-full UI |
| main push / manual run | Run | Run | Run | Existing surface workflows; CI-policy rehearsal when applicable |

The presentation boundary is deliberately small: Dashboard `src/`, `public/`
and packaged `loopx/web/chat/`, with explicit client-code/image/font extensions.
Package manifests, Vite/build configuration, native desktop code, backend Python
and arbitrary JSON are not exempt. Both sides of renames are classified; moving
runtime code into a UI directory stays full. Symlinks/type changes cannot qualify.

The new exemption is available only when the selector, gate and workflow blobs
match the already-reviewed target branch. Changing CI policy cannot exempt its
own PR. Missing policy or uncertain ownership runs full; missing Git revisions
fail classification. The pre-existing documentation exemption remains supported.

Frontstage Pages qualifies its own public artifact: homepage and projection-demo
builds, bilingual Blog catalog, public-safe bundle, docs, analytics, search and
browser checks. It does not build or upload the private `/chat/` workspace.
Python Tests retains the source-verified packaged workspace and browser checks;
their failures remain product qualification failures, rather than blocking an
unrelated Blog publication. An active production publication completes while
new pushes replace its pending successor; superseded validation for the same PR
still cancels. No runtime capability or permission default changes.

The exporter retains its conservative organization-name probe, with an exact
allowlist for verified public source `href` citations. The exception applies
only to that probe: adjacent prose, unlisted repositories, URL suffixes and
queries still fail, and path, internal-host and credential checks always inspect
the original content. This local publisher policy is not a general classification
of whether an organization or arbitrary GitHub repository is public. The bundle
smoke exercises the exporter guard directly instead of maintaining a second
copy of its policy.

Frontstage Pages 只验收并发布公共网站和 projection demo，保留构建、双语博客、
文档、公开安全边界、分析、搜索及浏览器检查。私有 `/chat/` 的打包与浏览器验收
仍由 Python Tests 承担。新合并不会中断正在运行的正式发布，只替换等待中的下一次
发布；同一 PR 的旧验证仍会取消。

本方案不是为每类 PR 维护一套测试清单，而是明确重型 job 的职责。纯前端变化不需要
重跑后端持久化与崩溃恢复矩阵，但前端自己的实际构建与浏览器验收成为必需项。
预算、静态宿主 prompt 和 Python/TS 逻辑暂不享受免跑；它们仍可能改变核心行为。
新增一种豁免只需审阅其业务边界和保留的验收，不要求列举全部替代测试文件。

## Four complete Python shards

Full Python qualification uses four runners with two xdist workers each:
`--splits 4 --group N --splitting-algorithm least_duration`. It still partitions
the whole collection, excluding only the separately executed Stage2c marker.
No tests are removed. Without timing history the splitter uses equal weights;
four-way parallelism is not a claim of perfect duration balancing.

The aggregate requires every shard to succeed and all four coverage files to
exist before combining them. The existing full-suite coverage floor remains.
No Python coverage artifact or Sonar run is manufactured when Python is exempt.

全量 Python 从 2 个分片扩大到 4 个，每片仍为 2 个 worker，不提高单机进程争抢。
真实 pytest-split/xdist/coverage 回归覆盖分片集合互斥、并集完整、四份报告合并以及
缺失任意报告时拒绝通过。分片增加会增加安装开销与同时占用的 runner；应看实际
critical path 和 runner-minutes，而不是宣称“4 片必然快一倍”。

## Override and evidence

Add the **`ci:full`** PR label to force full qualification. Label addition/removal
reruns the workflow. Manually dispatching Python Tests also runs full. The label
can only add checks, never waive them. Main retains full qualification.

The `ci-impact-plan` artifact and job summary report exact revisions, change kind,
per-job execution flags, reason and coverage scope. The merge gate requires
success for required jobs and an explicit skip for exempt ones; failure,
cancellation, missing outputs, contradictory flags or unexpected skips fail.

Stage2c retains all correctness cases: its E2E lane uses two runners with two
workers each, while mutants and installed-package lanes remain separate. The
small pytest plugin assigns whole modules using deterministic largest-first
test-count balancing and retains collection order within each module. It does
not split a stateful module across machines or workers. This is not timing-based
optimal scheduling: one very large module can still dominate a shard.

Stage2c E2E 从单 runner 的 4 个 worker 改为两个 runner 各 2 个 worker；总 worker
数不增加，但不再挤在同一台机器。按完整模块分片，并保留 loadfile 与模块内顺序。
真实回归以共享状态、顺序敏感的模块验证两路并集完整且互斥，避免盲目按单测试分片。

The first implementation retains minimum-Node checks and removes the earlier vision selected-test runner,
selected/full comparison machinery and its extra shadow workload. The prior
shadow results remain historical evidence, not a permanent extra CI obligation.

## Qualification

```bash
python -m unittest discover -s scripts/ci -p 'test_*.py'
python -m pytest tests/test_python_ci_workflow.py tests/test_sonarcloud_workflow.py -q
python scripts/ci/review_gate.py classify --base origin/main --head HEAD --plan impact-plan.json
python scripts/ci/review_gate.py classify --base origin/main --head HEAD --force-full --plan impact-plan.json
```

Use repository-supported Python/test dependencies. Keep generated plans and
JUnit/coverage artifacts outside tracked source. Hosted CI must qualify the real
workflow after policy changes; unit checks do not prove runner scheduling or
latency. Paid model tests remain release/manual only. No Goal, automation,
authority provider, runtime permission or live state is changed by this policy.
