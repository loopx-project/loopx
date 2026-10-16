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

## Six complete Python shards

Full Python qualification uses six shard jobs with two xdist workers each:
`--splits 6 --group N --splitting-algorithm least_duration`. It still partitions
the whole collection, excluding only the separately executed Stage2c marker.
No tests are removed. Without timing history the splitter uses equal weights;
six shards do not establish perfect duration balancing. Each numbered shard
shares its queue across workflow runs without replacing another current head.

Long Python shards now wait for the existing `checks` aggregate. An ordinary
PR enters those queues only after core qualification succeeds, so a known-red
candidate does not occupy the long-test lanes. This changes scheduling, not
qualification: skipped shards after a failed prerequisite still fail `pytest`
and `merge-gate`; a passing full candidate still requires every shard and the
same coverage floor. Main, scheduled, manual and merge-queue runs retain full
Python diagnostics after `checks` finishes, even if it failed, provided the
classification and source bundle succeeded and the workflow was not cancelled.
The tradeoff is extra serial latency for a healthy run in an idle queue, in
exchange for less work behind known failures under contention. `ci:full` still
forces the full profile; use a manual run for complete diagnostics of a failing
PR revision. This does not cancel previously queued runs of older workflows.

长 Python 分片现在先等待现有 `checks` 汇总；普通 PR 仅在核心资格检查成功后
进入长队列，避免已知失败的候选继续占用分片。前置失败导致的跳过仍使 `pytest`
和 `merge-gate` 失败，完整候选仍须通过全部分片与原覆盖率门槛。主干、定时、
手动和 merge queue 在汇总结束后仍执行完整 Python 诊断，即使该汇总失败；
分类和源码构建必须成功，取消的 workflow 不再启动分片。代价是空闲队列中健康
候选的串行等待增加；收益是拥塞时不再给已知失败的 PR 排长测试。`ci:full` 仍强制
完整 profile；需要失败候选的全部诊断时可手动运行其分支。旧 workflow 已排队的
任务不会因此自动取消。

The shard command explicitly loads `scripts.ci.pytest_timing`. Its controller
streams pytest's setup/call/teardown durations to Actions logs as each report
arrives, before final JUnit or duration summaries exist. To inspect a downloaded
job log, run `node scripts/ci/pytest_timing.ts JOB_LOG`. The reader reports the
selected and finished cases, observed elapsed time, measured worker-seconds,
phase totals and longest observed cases.
Only collected-item reports supply phase durations. Nested pytest/unittest
subtests remain in pytest's result output; their cost is already included in
the parent call, so they do not duplicate timing phases. Logs produced by the
earlier observer that mixed subtest reports into ordinary phases remain rejected
as contradictory evidence; the reader does not guess which duplicate to discard.
The phase record identifies the full pytest node id by SHA-256 and displays the
parent name without parameter payloads, bounded to 256 characters. Distinct
parameter cases keep distinct hashes; huge values and hosted secret masking of
parameter strings cannot expand or corrupt these JSON records. The existing
verbose pytest result output remains available.
Observation rows occupy their own lines with either quiet (`-q`) or verbose
(`-v`) progress, including serial and xdist runs. The reader still requires
line-framed records rather than guessing how to recover old progress-prefixed logs.
Missing observations, malformed records or contradictory collections fail
readback; an interrupted or incomplete run stays
incomplete. A completed traversal with pytest exit code 1 still has test failures.
Measured worker-seconds sum concurrent test phases and are not runner-minutes,
queue delay, CPU time or the job's complete elapsed time. A partial log supplies
only a lower bound; use job/step timestamps for setup and runner occupancy.
These observations do not become a timing cache, change selection or establish
qualification. Missing durations still use the existing equal-weight fallback.

The same observer flushes failed reports as separate `LOOPX_PYTEST_FAILURE`
JSON lines, including nested failures without counting their duration again.
They retain the same hashed identity, phase, a known exception class and up to
eight repository-relative source positions with literal-redacted assertion
expressions. Messages, parameters, locals and captured IO are omitted from the
new JSON; pytest's existing output is unchanged. Unknown classes or unavailable
source context remain null, and an observer error may omit a diagnostic record.
Only reports received by the controller are covered; collection failures,
worker death before sending and unseen reports at interruption are excluded.
These locations are diagnostic context, not complete cause attribution or
authenticated evidence. JUnit, exit status, full collection and gates remain
required. The timing reader ignores this separate prefix.

分片在 controller 中随 pytest 的 setup/call/teardown 报告写入 Actions 日志，
无需等待最终 JUnit 或耗时汇总。下载单个 job 日志后运行
`node scripts/ci/pytest_timing.ts JOB_LOG`，可回读选中／完成数量、已观察耗时、
worker 秒数、各阶段总和与最慢用例。缺失、损坏或矛盾记录拒绝回读；中断与不完整
运行保持不完整。遍历完成且 exit code 为 1 仍代表测试失败。
仅收集项的报告提供 phase 耗时；pytest/unittest 子测试仍由 pytest 输出结果，
其成本已包含在父项 call 中，不重复记入 phase。旧 observer 把子测试混入普通 phase
的日志仍作为矛盾证据拒绝，reader 不猜测应丢弃哪条重复记录。
同一 observer 收到失败报告后立即输出 `LOOPX_PYTEST_FAILURE` 脱敏源码位置、异常类和
断言结构；子测试单独保留失败位置，不重复计时。新增 JSON 不含消息、参数、locals 或
捕获的 IO，原 pytest 输出不变。未知类、缺失源码或诊断异常仍是证据缺口；未送达的
报告与收集失败不在覆盖范围，不能据此完成原因归因、证明真实性或豁免完整门禁。
并发 phase 总和不是
runner-minutes、排队时长或 CPU 时间；不完整记录仅给出下界，环境准备与占用须结合
job/step 时间戳。记录不进入 timing cache、不改变测试选择、不替代门禁；没有耗时
历史时仍按原等权规则分片。
完整 pytest node id 用 SHA-256 标识；显示名去除参数内容并限制为 256 字符。
不同参数仍有不同 hash；超长参数及宿主对参数字符串的脱敏不会扩大或破坏这些
JSON 记录。原有 pytest 详细结果输出仍保留。
无论使用 quiet（`-q`）还是 verbose（`-v`）进度，串行和 xdist 运行中的观察记录
都独占一行。reader 仍要求完整行记录，不猜测如何修复旧的进度字符前缀日志。
The reader accepts a line-leading BOM from concatenated GitHub log chunks before
timestamp/ANSI framing; phase identity, ordering and completion checks remain strict.
reader 接受 GitHub 日志分块拼接产生的行首 BOM，再读取时间戳和 ANSI 包装；
phase 身份、顺序与完整性检查保持严格。

The aggregate requires every shard to succeed and all six coverage files to
exist before combining them. The existing full-suite coverage floor remains.
No Python or backend TypeScript coverage artifact or Sonar run is manufactured
when those lanes are exempt. Dashboard coverage remains scoped to the frontend.

全量 Python 采用 6 个分片，每片仍为 2 个 worker，不提高单机进程争抢。
各分片号在 workflow 之间共享队列，不替换其他当前 head 的待执行资格。
真实 pytest-split/xdist/coverage 回归覆盖分片集合互斥、并集完整、六份报告合并以及
缺失任意报告时拒绝通过。分片增加会增加安装开销与同时占用的 runner；应看实际
critical path 和 runner-minutes，而不是根据分片数宣称必然加速。

## Override and evidence

Add the **`ci:full`** PR label to force full qualification. Label addition/removal
reruns the workflow. Manually dispatching Python Tests also runs full. The label
can only add checks, never waive them. Main, scheduled and merge-queue runs retain
full qualification. A PR is classified from its merge base through its complete
head, including changes from earlier commits, not just its latest commit.

The `ci-impact-plan` artifact and job summary report exact revisions, change kind,
per-job execution flags, reason and coverage scope. `python_shards` reads the
static `test-shard.strategy.matrix.shard` axis from the immutable workflow at
`checkout_sha`, currently six, rather than retaining a separate historical count.
Unsupported or noncontiguous topology fails classification instead of publishing
a fallback count. Docs/presentation exemptions still report zero. The number
describes the workflow topology, not how many jobs actually ran.

`python_shards` 从 `checkout_sha` 对应的不可变 workflow 读取静态分片轴，当前为六片，
不另存历史计数；不支持或不连续的拓扑直接报错，不输出猜测值。文档／前端豁免仍为零。
它描述 workflow 拓扑，不能证明任何分片实际执行或 JUnit／coverage 已完整。

The merge gate requires
success for required jobs and an explicit skip for exempt ones; failure,
cancellation, missing outputs, contradictory flags or unexpected skips fail.

The merge gate also writes a **Qualification evidence** summary: source head,
workflow checkout, event base, attempt, dependency results and links to failed
jobs and steps. It separates execution failures, aggregate failures and incomplete
jobs without attributing a cause from status alone. The read-only job lookup uses
the current run attempt; an unavailable API or missing evidence is reported as
such. Advisory checks can appear in the report, but only the existing gate policy
decides qualification. Summary errors cannot override that verdict. There is no
inherited-main failure waiver or automatic flaky-test exemption.

merge-gate 的摘要记录源码 head、实际 checkout、事件 base 与本次 attempt，
分别列出失败作业、聚合失败和未完成作业，并链接到失败步骤所在 job。
摘要不根据状态猜根因；API 不可用时明确标记证据缺口。它也会列出 advisory
检查，但是否通过仍由原门禁规则决定，不因摘要失败而放行，不自动豁免历史失败。

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

Trusted obsolete-PR cleanup runs in the separate **CI Maintenance** workflow on
`pull_request_target` close/synchronize events. It never checks out PR content
and retains the same PR/repository/head checks before cancellation. A green
maintenance run means cleanup completed, not that Python tests passed. Its
concurrency group is separate from current-head qualification; `Python Tests`
still receives PR close events to cancel the old qualification lane.
Delayed PR events can create runs after the close timestamp. Cleanup also retires
those runs when their source repository, branch and exact head match the still
closed PR and no open PR shares that branch. Later runs at a different head stay
untouched, even with an association to the closed PR; reopening or source changes
stop cancellation. This extends the earlier close-time cutoff for exact-head
residue only. Cleanup can still wait for a runner or miss a run created after its
snapshot; it is not a continuous admission service.
For synchronize events, an omitted run-to-PR association may use the webhook's
exact `before` head only when `after`, source repository and branch match the
current PR. Other unassociated old heads remain protected; the same lifecycle
and sibling-PR checks still run before both cancellation stages.

Dashboard browser qualification retains all three shards. The unchanged unit
coverage command runs only in shard 1, whose `lcov.info` the existing merger
already consumes; browser coverage and acceptance receipts remain required from
all three shards. The merger rejects missing unit coverage or incomplete browser
acceptance. This removes two duplicate unit-coverage invocations per full or
presentation run; it does not establish a hosted wall-time improvement.

旧 PR 清理独立为 CI Maintenance，保留原有来源与 head 校验，也不执行 PR 代码。
清理成功不再显示为一次 Python Tests 成功。Dashboard 的单元覆盖率只在第 1 片
执行一次，三个浏览器分片与验收凭证仍全部必需；缺少单元覆盖率仍拒绝聚合。
这减少了两次重复命令，实际等待时间与端到端耗时仍需上线后测量。
延迟处理的 PR 事件可能在关闭后才创建运行。清理现在也覆盖与仍关闭 PR 的仓库、
分支和 head 完全一致的残留；其他 head 的晚到运行仍保留，重开或来源变化会中止
取消，同分支其他开放 PR 的保护不变。清理本身仍可能等待 runner，也可能遗漏其
快照之后才创建的运行，不能代替持续的准入控制。
同步事件若缺少运行到 PR 的关联，仅在 webhook 的 `after`、源仓库和分支与当前
PR 一致时，允许用其确切 `before` head 识别被替换运行；其他无关联旧 head 仍保留，
普通及强制取消前的状态和同分支 PR 检查不变。

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
node --test scripts/ci/qualification_summary.test.ts scripts/ci/merge-dashboard-coverage.test.mjs
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
