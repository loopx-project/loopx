# CI job exemptions / 按职责免跑重型 CI

## Policy, not a hand-maintained test selection

Every PR receives the same stable merge gate. The classifier reads the complete
NUL-delimited Git diff at immutable base/head revisions; a PR title, label or
author description cannot claim that runtime changes are “just UI”.

| Whole PR | Backend TS/lint/contracts | Full Python / Windows | Stage2c | Packaged Dashboard |
| --- | --- | --- | --- | --- |
| Existing Markdown-only documentation exemption | Skip | Skip | Skip | Not published by Frontstage Pages |
| Client Dashboard source/assets only, optionally with docs | Skip | Skip | Skip | Required type-check, build, coverage, freshness and browser smoke |
| Backend, prompt, tests, dependency, build, CI policy, mixed or unknown | Run | Run | Run | Required type-check, build, coverage and browser smoke; extra freshness for CI-policy rehearsal or forced-full UI |
| main push / daily / manual / merge queue | Run | Run | Run | Required type-check, build, coverage and browser smoke |

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
预算、静态宿主 prompt 和后端 Python/TS 逻辑暂不享受免跑；它们仍可能改变核心行为。
新增一种豁免只需审阅其业务边界和保留的验收，不要求列举全部替代测试文件。

## Four complete Python shards

Full Python qualification uses four runners with two xdist workers each:
`--splits 4 --group N --splitting-algorithm least_duration`. It still partitions
the whole collection, excluding only the separately executed Stage2c marker.
No tests are removed. Without timing history the splitter uses equal weights;
four-way parallelism is not a claim of perfect duration balancing.

The aggregate requires every shard to succeed and all four coverage files to
exist before combining them. The existing full-suite coverage floor remains.
No Python or backend TypeScript coverage artifact or Sonar run is manufactured
when those lanes are exempt. Dashboard coverage remains scoped to the frontend.

全量 Python 从 2 个分片扩大到 4 个，每片仍为 2 个 worker，不提高单机进程争抢。
真实 pytest-split/xdist/coverage 回归覆盖分片集合互斥、并集完整、四份报告合并以及
缺失任意报告时拒绝通过。分片增加会增加安装开销与同时占用的 runner；应看实际
critical path 和 runner-minutes，而不是宣称“4 片必然快一倍”。

## Override and evidence

Add the **`ci:full`** PR label to force full qualification. Label addition/removal
reruns the workflow. Manually dispatching Python Tests also runs full. The label
can only add checks, never waive them. Main, scheduled and merge-queue runs retain
full qualification. A PR is classified from its merge base through its complete
head, including changes from earlier commits, not just its latest commit.

The `ci-impact-plan` artifact and job summary report exact revisions, change kind,
per-job execution flags, reason and coverage scope. The merge gate requires
success for required jobs and an explicit skip for exempt ones; failure,
cancellation, missing outputs, contradictory flags or unexpected skips fail.

Specialized workflows already use GitHub's PR path filters: release artifacts
follow packaged source/build inputs, desktop artifacts follow native/Desktop
and shared Dashboard inputs, optional Ark follows its adapter and shared turn
boundaries, and Frontstage follows its published content. Their filters remain
separate from the required Python merge gate, which starts for every PR and
decides exemptions inside the workflow. Regression fixtures cover both related
and unrelated paths, including a mixed PR that still triggers Ark.

按需触发以整个 PR 的累计改动为输入，先判断职责，再决定运行哪些完整 job。
文档与纯前端 PR 已有明确豁免；后端、桌面、共享依赖或混合改动仍保留完整
Python 主门禁。桌面、发布、Ark 和 Frontstage 的专用 workflow 则继续按各自
路径触发。当前没有实现逐个测试的依赖图选择，也不声称所有后端 PR 都能缩减。

Stage2c retains all correctness cases: its E2E lane uses two runners with two
workers each, while mutants and installed-package lanes remain separate. The
small pytest plugin assigns whole modules using deterministic largest-first
test-count balancing and retains collection order within each module. It does
not split a stateful module across machines or workers. This is not timing-based
optimal scheduling: one very large module can still dominate a shard.

Stage2c E2E 从单 runner 的 4 个 worker 改为两个 runner 各 2 个 worker；总 worker
数不增加，但不再挤在同一台机器。按完整模块分片，并保留 loadfile 与模块内顺序。
真实回归以共享状态、顺序敏感的模块验证两路并集完整且互斥，避免盲目按单测试分片。

The supported minimum-Node contract runs for full candidates. The non-blocking
next-Node probe runs only with daily or manual Python Tests, outside the
per-push critical path. There is no hand-maintained per-test selection list.

## Resource cadence and supersession

Full Public Smokes runs daily at `18:37 UTC` and by manual dispatch on the
selected ref; ordinary main pushes no longer launch it. Its five complete
shards share one built and source-verified Chat artifact, with at most two
shard jobs running at once. Each consumer rejects missing, stale or corrupt
assets before running smokes and installs its own locked Dashboard test
dependencies; a shared build artifact does not provide a runner's test runtime.
This reduces repeated frontend builds from five
to one and background shard occupancy from five runners to two; the complete
sweep may take longer. The existing fleet-health readback still checks receipts.

Python Tests also runs daily at `20:17 UTC`. Its event-scoped concurrency keeps
that full run separate from main pushes and PRs. New PR heads and main pushes
cancel superseded runs; scheduled, manual and merge-queue qualification is not
cancelled by that policy. Release-artifact, desktop-artifact, signed-desktop,
DCO and optional Ark PR checks cancel only superseded validation for the same PR.
Signed-desktop PRs use separate groups from each other and from channel publication.
Release publication remains serialized and is never cancelled by a PR update.

全量 smoke 改为每天一次与指定 ref 的手动运行，不再随普通主干提交启动。五个完整
分片共享一次源码绑定的前端构建，同一时刻最多运行两个分片；全量用例仍保留，
代价是后台 sweep 可能更久。每日完整 Python 验证与 PR／主干提交使用不同并发组，
避免新提交取消夜间基线。过时 PR 检查可取消，发布通道仍保留串行发布保护。

To force a complete smoke sweep before release, open Actions → Full Public
Smokes → Run workflow and select the candidate ref. `ci:full` forces full Python
Tests but does not dispatch the separate smoke workflow. Read back both runs on
the intended source before claiming release qualification. Revert this policy
change to restore per-main smoke triggers and backend checks on client-only PRs.

Track queue time, executed job duration, total runner-minutes and cancellation
rate separately. The workflow structure bounds repeated work and peak demand;
only hosted before/after observations can establish latency or capacity gains.

## Qualification

```bash
uv run --extra test python -m unittest discover -s scripts/ci -p 'test_*.py'
uv run --extra test python -m pytest tests/test_python_ci_workflow.py tests/test_sonarcloud_workflow.py tests/test_ci_resource_policy.py -q
uv run --extra test python examples/full-public-smokes-workflow-smoke.py
uv run --extra test python scripts/ci/review_gate.py classify --base origin/main --head HEAD --plan impact-plan.json
uv run --extra test python scripts/ci/review_gate.py classify --base origin/main --head HEAD --force-full --plan impact-plan.json
```

Use repository-supported Python/test dependencies. Keep generated plans and
JUnit/coverage artifacts outside tracked source. Hosted CI must qualify the real
workflow after policy changes; unit checks do not prove runner scheduling or
latency. Paid model tests remain release/manual only. No Goal, automation,
authority provider, runtime permission or live state is changed by this policy.
