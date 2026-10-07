# Todo work counts and bounded display

Todo lists, status and quota summaries carry `work_counts` with schema
`todo_work_counts_v0`. Counts are computed before display limits; quota
recomputes them **after** the existing Agent scope and resume selection.
The contract is read-only. A count never grants a claim, lease, capability,
validation exemption or execution permission.

```sh
loopx --format json todo list --goal-id example --role agent --limit 1 --thin
loopx --format json status --goal-id example
loopx --format json quota should-run --goal-id example --agent-id agent-a
```

The first command returns one Todo while its role summary retains the matched
source's counts. A filtered list describes its filtered source. This is not a
new provider setting: legacy inputs and promoted File/SQLite inputs share the
same typed summary owner. PostgreSQL uses the same provider-neutral records;
service deployment and whole-Goal promotion remain separately qualified.

| Field | Meaning |
| --- | --- |
| `open` | Nonterminal source rows, including blocked work; it is not executable work |
| `advancement` | Observed actionable advancement rows; acceptance-denied and unsatisfied resume rows do not qualify |
| `monitor` | Observed actionable Monitor rows, including future/expired observation context; due/schedule-gap fields retain their existing separate meanings |
| `hidden` | Declared source rows not available for classification, not rows hidden by UI pagination |
| `complete` | Whether the available source covers the declared scope; false counts are lower bounds for classified task kinds |
| `agent_id` | Agent execution scope, or null for an unscoped/role summary |

The observed row count is derived as `open - hidden`; the payload does not
repeat it as a second value that could drift.

`complete=false` survives repeated quota projection, even when the surviving
subset fits on one screen. It cannot certify “Monitor-only work remains.”
Legacy display-only inputs are deduplicated by Todo identity; contradictory
fragments cannot certify completeness. A missing task is never guessed to be
advancement work. Invalid count envelopes and differently scoped count reuse
fail explicitly.

The TypeScript `todos/summary_lanes.ts` owner returns indexes into the one input
array instead of repeating full Todo bodies for each lane. The Python adapter
normalizes legacy fields/timestamps and validates the returned ordinal bounds.
One observation time governs Monitor due and expiry classification within the
batch. Source order, completed/deferred conventions and claimant visibility
remain compatible; `done_count` still includes deferred rows as required by its
existing summary contract.

Intentional corrections: 21 executable Todos no longer become 8 because the
backlog display limit is 8; a compact quota payload no longer turns that count
into 2. Unknown hidden rows are not classified. Public canonical `todo list`
also retains the acceptance guard from the same read revision, so held work
cannot appear executable there while status says it is held. Acceptance-off
reads keep their existing selection behavior.

Markdown stays a permanent display. These reads do not rewrite stale/missing
Markdown, create receipts or mutate canonical records. The additive count
field is not persisted in Todo authority. Older readers can ignore it, but
retain their old undercount behavior; rollback does not require data migration.
T1/T2 caller closure, D1 projection recovery, D2 capacity/elapsed soak and D3
fenced whole-Goal cutover remain separate work.

## Agent-addressed reads

`todo list --agent-id` now composes selection in the same typed summary batch,
sharing scope rules with quota. For User gates, explicit `global_gate` wins,
then `blocks_agent`, then the retained `claimed_by` fallback. User actions use
`bound_agent` first and retained `claimed_by` second. Unscoped records remain
visible. Gate addressing is independent of executor exclusion: an Agent cannot
ignore an explicitly addressed human gate because another Agent owns it.
Agent work still filters by claim and exclusions. A visible row grants no
mutation or execution permission; quota retains its additional eligibility rules.

This intentionally removes other-Agent, claim-only User records from scoped
lists; the old Python list rule ignored their claim while quota honored it.
Unfiltered Goal views retain those records. There is no feature flag or provider
default change. Existing frontend/Lark manager views use the unfiltered Core
read and continue to show the whole Goal; no new configuration editor is needed.

Resume and succession are evaluated on the complete source before selection.
The typed batch filters rows without renumbering their original source indexes,
then builds lanes/counts, and only then applies display limits. Status/identity
filters do not recompute dependencies from their smaller view. One internal `todo.summary.project` batch now composes selection, counts,
visibility allocation and closure. Its transient request replaces the separate
lane and closure RPC calls; it does not change persisted Todo or public summary
schemas. Python decodes legacy input, validates source ordinals and materializes
public fields, with no independent summary count, cap or claimant-allocation rule.

Route-continuation visibility also uses the existing typed quota planning batch.
It shares the normal claim/exclusion rule; eligibility, first-identity deduplication,
sorting and counts precede the display cap. Disabled candidates do not hide later
eligible copies. Historical unclaimed visibility may include excluded records,
but the current-Agent lane excludes them and the execution consumer rechecks
eligibility. A route hint never grants execution or clears a handoff gate.
Python retains legacy field decoding and display formatting. There is no new
RPC, persisted field, provider default or forced migration; supported old planning
requests and legacy/File/SQLite records retain their behavior.

Handoff visibility uses the same batch and gate snapshot. `excluded_agents`
addresses the review lane for these gates, independently of ordinary execution
claims; exclusion still denies execution. Full-source counts precede display
limits, while projected source order, duplicates and historical display states
are retained. Only `cleared_without_successor` contributes to that named gap
count; no-follow-up, superseded and deferred states are distinct. An explicitly
empty projected gate list suppresses legacy reconstruction. Python retains the
legacy renderer and downstream execution checks, with no separate handoff lane
decision or repeated gate reconstruction for route visibility.

重规划候选的展示也复用既有 TS quota planning 批处理与 claim/exclusion 规则。
资格、首个有效身份去重、排序和计数先于展示裁剪；显式关闭的副本不遮挡后续有效
副本。历史 unclaimed 展示可包含被排除的记录，current-Agent 分组会排除它们，
执行方仍重新校验资格。建议不授予执行权限或清除 handoff 阻塞。Python 保留旧字段
解码和展示；不新增 RPC、持久字段、provider 默认值或强制迁移。

handoff 展示也使用同一批次与 gate 快照。此处 `excluded_agents` 定向其复核分组，
执行仍受排除限制；它不按普通工作 claim 授予资格。完整计数先于裁剪，保留来源顺序、
重复行和历史展示状态；仅 `cleared_without_successor` 计入对应后继缺口，不能混同
no-follow-up、superseded 或 deferred。显式空 gate 投影不重建旧来源。Python 保留旧
格式展示和下游执行检查，删除独立 handoff 分组判断及 route 展示的重复 gate 重建。

## 中文说明

`work_counts` 由完整来源计算，随后才裁剪展示。Agent quota 先按原有归属、排除、
能力与作用域规则筛选，再重新计数，不能沿用整个 Goal 的数量。`open` 包含 blocked
任务；`advancement` 才是已观察到的可执行推进任务。Monitor 是否到期仍使用独立字段。

`hidden` 表示未取得、无法分类的来源行，不是界面折叠的行数。`complete=false` 时，
已分类数量只是下界，重复投影也不能把未知变成完整，更不能据此声称“只剩 Monitor”。
旧摘要按 Todo 身份去重，矛盾片段不能证明完整；缺失任务不再被猜成 advancement。

TS 统一批量 lane 分类与计数，Python 保留旧格式解码、时间适配和展示。返回数组位置
索引减少同一任务在多个 lane 的重复传输；一个批次使用同一观察时刻。排序、延期与
完成计数约定、claim 展示保留。canonical `todo list` 同时修复了漏传同版本 acceptance
限制的问题，验收受阻的任务仍可见，但不会被列为可执行。

这不改变 provider 默认值，不授予执行权限，不写回 Markdown 或 canonical 状态。
新增计数字段不进入持久化 Todo；回滚无需数据迁移。默认切换、存量迁移、D1–D3 和旧
Python writer 退出仍有各自的验收条件，不能按本 PR 合并数量推定完成。

Agent 定向列表现与 quota 共用 TS 范围规则：User gate 按 global_gate → blocks_agent →
旧 claimed_by 依次判定，User action 按 bound_agent → 旧 claimed_by 判定。无作用域的
旧记录仍可见；显式人类 gate 不会被执行者 claim/exclusion 消除。Agent 工作仍按
claim/exclusion 筛选，可见不代表获准执行。

这是有意纠正：旧列表忽略仅声明 claimed_by 的 User 记录，导致其他 Agent 的工作混入
当前列表。未筛选的整 Goal 视图仍显示这些记录。依赖和 succession 先在完整来源求值，
TS 再筛选并保留原数组位置，最后生成 lanes、计数和有界展示；筛选后的数组位置不是原
来源位置。无需新增 capability、配置、前端或 Lark 编辑入口，不增加一次筛选 RPC。

## Summary chronology and source completeness

The same TS projection now supplies `recent_completed_advancement_items`,
claimant-balanced display lanes, orchestration candidate positions and closure
proofs to legacy and canonical consumers. Display budgets are unchanged;
`items` limits never change full-source counts. Full-source resume/succession
evaluation still precedes filtering, and returned positions refer to the original
array, not a newly numbered subset. Python retains public field allowlists,
warning text, privacy redaction and Markdown parsing/rendering.

**Intentional read behavior changes:** recent completions are ordered by the
actual `completed_at` instant, preserving timezone offsets and microseconds.
Later `updated_at` edits no longer make an old completion recent. Missing or
invalid completion times remain in completed-work counts/history but do not
claim a place in the recent-completion lane. Equal instants retain reverse
source-coordinate order and stable ties. Succession warnings keep their
last-change ordering, now comparing instants rather than timestamp strings;
unknown instants follow known ones without discarding the warning.

A source already marked partial cannot regain `source_proof` or
`terminal_closure_proof` simply because a later selection matches all visible
rows. Query scope and source completeness are independent conditions. These
proofs remain read-only observations, not permission to settle a Goal.

This changes status, Todo-list and quota summary readback for both legacy and
promoted Goals without a flag. Existing frontend and Lark views consume these
Core projections; no new setting or frontend asset is required. No provider,
lease, registry or display writer is added. Rollback requires the matching
Python/TS package but no data migration. Full L5 consumer acceptance, projection
freshness, SQLite D2 and default/cutover gates remain separate.

### 中文补充

摘要的计数、展示上限、领取者之间的展示分配、编排候选位置及收尾证明，现由一个 TS
批次决定；删除 Python 的重复汇总分支和仅为旧内部调用保留的 claim 分配 helper。
Python 继续负责旧数据解码、公开字段筛选、隐私处理与文本展示。

这是有意的读取语义修复：最近完成列表按 `completed_at` 的真实时刻排序，保留时区和
微秒，不再把较晚编辑误作较晚完成。缺失／非法时间仍计入已完成总数和历史，但不进入
最近完成列表。后继缺口警告仍按最后更新时间排序，未知时间靠后，不丢弃警告。
已有 partial 来源不会因为再次筛选命中所有可见行，就重新获得整个来源的收尾证明。

覆盖 legacy 与 canonical 的 status、Todo 查询和 quota 摘要；展示预算保持原值。
没有新增设置、权限或 writer，不改变 provider 默认值，也不宣称完成整 Goal 迁移。
