# active_state_structured_projection_v0

`active_state_structured_projection_v0` is a read model for
`ACTIVE_GOAL_STATE.md`. It keeps Markdown as the human/agent workbench while
exposing typed todo, gate, next-action, and migration diagnostics for status,
quota, review packets, dashboards, and future event-store migration.

This is not a new canonical store. The projection is recomputable from the
current active-state Markdown and does not grant write permission.

The machine-owned Todo read subset is versioned separately in
`coordination_state_contract_v0.json`. That provider-neutral contract is shared
by Python and TypeScript. It declares the legacy consumer record and a separate
native domain record for file, NoKV, or PostgreSQL authority heads.
`archive_state` is durable task state: archival changes handoff and succession
eligibility independently of completion. `source_section` and optional `index`
belong to the Markdown compatibility projection, not native creation inputs.
Legacy v0 records retain those fields; importing them into the domain version
requires explicit qualification, including preservation of priority tie ordering
currently influenced by `index`. There is no automatic stored-head migration.
Provider-bound projection rejects an unknown
field instead of silently dropping it. Removing a declared field requires a
reviewed compatibility decision and maintainer approval, including for fields
that are persisted but not yet used by a decision path.

## Shape

```json
{
  "schema_version": "active_state_structured_projection_v0",
  "source": "markdown_active_state",
  "source_ref": "ACTIVE_GOAL_STATE.md",
  "goal_id": "optional-goal-id",
  "frontmatter": {
    "status": "active",
    "updated_at": "2026-06-28T00:00:00+08:00"
  },
  "next_action": {
    "count": 1,
    "first": "Run the next bounded validation slice.",
    "entries": ["Run the next bounded validation slice."]
  },
  "todos": {
    "user": {
      "total_count": 1,
      "open_count": 1,
      "done_count": 0,
      "implicit_todo_id_count": 0,
      "items": []
    },
    "agent": {
      "total_count": 1,
      "open_count": 1,
      "done_count": 0,
      "implicit_todo_id_count": 0,
      "items": []
    }
  },
  "diagnostics": {
    "schema_version": "active_state_projection_diagnostics_v0",
    "parseable": true,
    "migration_ready": true,
    "warning_count": 0,
    "error_count": 0,
    "warnings": [],
    "errors": []
  }
}
```

## Todo Items

Todo items use the existing `todo_item_v0` fields where possible:

- `todo_id`, `todo_id_source`, `role`, `status`, `done`;
- `priority`, `title`, `task_class`, `action_kind`, `continuation_policy`;
- `claimed_by`, `blocks_agent`, `global_gate`, `unblocks_todo_id`;
- `resume_when`, `no_followup`;
- monitor metadata such as `target_key`, `cadence`, `next_due_at`, and
  `consecutive_no_change`;
- compact evidence fields such as `note`, `evidence`, `reason`,
  `completed_at`, and `updated_at`.

`todo_id_source=metadata` means the item carried explicit LoopX metadata.
`todo_id_source=generated` means the projection generated a stable compatibility
id from role, source section, index, and text. Generated ids are useful for
read compatibility but are not migration-ready.

## Diagnostics

Diagnostics are intentionally small and machine-readable:

| Diagnostic | Severity | Meaning |
| --- | --- | --- |
| `missing_frontmatter` | warning | Markdown lacks frontmatter such as status or updated time. |
| `missing_next_action` | warning | No `## Next Action` entries were projected. |
| `missing_todo_sections` | warning | No user or agent todo items were projected. |
| `implicit_todo_ids` | warning | Some todo ids were generated instead of explicit metadata ids. |
| `duplicate_todo_ids` | error | Multiple items use the same explicit or generated todo id. |

`migration_ready=true` requires at least one todo item, no errors, and no
implicit todo ids. A non-ready projection can still be useful for status and
operator displays; it should not be promoted as canonical event-store input.

## Reader Contract

Readers should treat this projection as:

- read-only;
- public-safe only after normal `loopx check` / boundary scanning;
- a compatibility layer over Markdown, not a replacement for todo/event write
  APIs;
- a bridge for parity tests before moving active-state parsing out of
  `status.py`.

Writers must continue to use LoopX commands such as `loopx todo`,
`loopx refresh-state`, `loopx operator-gate`, and future event append APIs.
Directly editing a projection is not a state transition.

## Markdown Ownership Boundary

New bootstrap and project-registration documents quote each Objective line and
escape HTML metacharacters. Fences, comments, headings, and Todo markers in the
objective remain content rather than document structure. Frontmatter string
encoding and readback share JSON semantics, including escaped Unicode line
separators; only complete delimiter lines terminate frontmatter. Objective
readback composes the existing section reader and decodes generated quotation.
Registration compares metadata values and exact remaining narrative, accepting
legacy Objective presentation without rewriting it; changed content still conflicts.

This is the permanent Python presentation/legacy-input adapter described by the
[TypeScript RFC](../../architecture/rfcs/typescript-control-plane-migration-v0.md#next-delivery-sequence)
and [shared-authority RFC](../../architecture/rfcs/shared-goal-authority-state-provider-v0.md#next-delivery-and-parallel-provider-work).
It adds no business rule, RPC, provider, or authority write. Post-cutover Todo
consumers still read canonical state when Markdown is absent or malformed;
rendering never imports Objective examples into that state. Before cutover,
the existing legacy writer remains subject to its normal fence. Objective is
independent Goal narrative, outside the Todo store and Todo-section recovery.
Existing malformed documents are not automatically repaired.

Markdown is not one undifferentiated database row. Agents generate and maintain
both its structured sections and narrative through LoopX. The distinction is
canonical ownership, not human versus Agent authorship: after promotion,
sections covered by the versioned coordination contract are regenerated from
that authority. Content outside that contract must remain intact until its own
canonical source can reconstruct it.

The cutover is deliberately section-sized, not document-sized:

- before promotion, Markdown remains the authority and writes use its existing
  transaction boundary;
- after promotion, the versioned Todo records live in the canonical provider
  head and Markdown's Todo section is a compatibility/workbench projection;
- other generated sections remain in Markdown until their canonical ownership
  migrates; Todo promotion does not silently discard or reinterpret them;
- a provider outage after promotion fails closed and never makes stale
  Markdown authoritative again.

The first write using this boundary is Todo claim. It exercises a complete
provider-neutral TypeScript transaction while leaving the default local path
unchanged. On promoted `hard_lease` authority, the same claim command may
supply a task-lease idempotency key and optional expected version so the claim,
canonical lease, and durable receipt commit in one provider transaction; the
write scopes come from the canonical Todo rather than caller input. After
promotion, `loopx todo project-markdown` can explicitly
regenerate active and archived Todo sections from the exact provider revision. It
never runs before promotion and never turns Markdown back into authority.

The projection command has four safety properties:

- it replaces only the machine-owned active user, agent and archived Todo regions;
- it preserves every segment outside those spans byte-for-byte;
- it fails closed when a canonical field cannot be represented by the current
  Markdown metadata grammar, rather than dropping that field;
- it defaults an optional nested schema field only when the key is absent;
  explicitly incompatible, malformed or unrepresentable nested values fail
  before normalization;
- it parses the rendered sections back and requires deterministic parity and
  idempotent second rendering before an `--execute` write.

The writer imports the legacy LoopX-generated H2/list/metadata layout. It stops
at the first non-generated line, rather than extending replacement to the next
H2 or EOF; following H1, Setext, indented headings and ordinary narrative remain
outside its ownership. Code fences and multiline comments are not Todo headers.
The projection then emits paired `loopx:todo-region-v0` begin/end markers under
each Todo heading. The renderer, active Todo reader and section editor share
that boundary contract. Future projections use those explicit bounds; orphan,
nested, mismatched or missing markers, and non-generated content inside a marked
region, fail closed. No ordinary Goal is rewritten or opted in by installation.
Unmarked legacy readers and editors retain their heading aliases and multi-line
Todo grammar, but now use the same visible-document boundary: fenced examples,
leading frontmatter and multiline HTML comments cannot supply tasks or edit
anchors. This intentionally changes legacy reads and writes that previously
accepted example checkboxes. Marked archive regions end at their end marker;
following narrative checkboxes are not historical decisions. Legacy heading
substring aliases are input compatibility, not a new classification policy.

Read/edit decoding now shares one Todo block codec. Projection assembly parses
source and rendered document regions once each; marker diagnostics inspect only
real Todo regions. A marker quoted in a code example is not delivery evidence.
Imported `index` values still determine relative order, but the newly rendered
section receives contiguous display ordinals. Canonical records and their
section digests retain the imported values. Archive readback restores priority
and title using the same existing text decoder as active records. Consequently,
sparse historical ordinals and archived priority labels no longer strand
projection recovery. These display corrections do not mutate provider state.

中文：未晋升的旧格式也统一排除 fenced 示例、文首 frontmatter 和多行 HTML 注释中的
假任务／编辑锚点；保留合法标题别名及多行任务文本。已标记归档区域在 end marker
处结束，区域外的叙述清单不再进入历史。读取与编辑共用行解码，投影共用区域解析。
历史行号只决定相对顺序，新展示使用连续行号；provider 中的旧行号、原始记录和
摘要不变。归档读回复用既有优先级／标题解码，避免真实历史导致恢复永久 pending。

Non-Todo byte preservation and canonical Todo parse/render parity are separate
checks: the former compares untouched source slices, while the latter reads only
the generated regions. Neither marker is provider authority or a current-head
freshness guarantee.

### Lossless source text / 无损源文本

Source decoding and parse/render parity retain the complete accepted Todo text
and priority-derived title, including multi-line continuations and archived
records. They normalize whitespace using the existing Markdown codec; they do
not apply the 500-character attention-summary limit. An explicitly conflicting
title still fails parity, even when its first 500 characters match. This is not
permission to accept malformed records or to shorten an already committed Todo
to repair its display.

Status, `todo list` (including an exact ID), dashboard and chat attention views
keep their existing bounded summaries. The canonical provider and regenerated
active state retain the complete source; a display summary is not an input to
source serialization. No new frontend setting, Lark command or parallel state
store is introduced. This change stays in the permanent Python Markdown/legacy
I/O adapter; the TypeScript authority, admission and delivery-confirmation
owners are unchanged.

If a committed mutation reports `projection_delivery=pending` because a long
Todo previously failed parity, read the current provider revision and use the
existing `todo project-markdown --provider-revision <revision> --execute` path.
Do not repeat the business mutation, change the Todo text, or reuse a stale
revision. Rebuilding and an idempotent second rendering do not alter canonical
records, business receipts or the provider revision. Other pending causes
(ownership, malformed metadata, missing validation or provider failure) retain
their existing closed diagnostics.

源解码与读写一致性校验保留已接受 Todo 的完整文本和优先级派生标题，包括多行续文
和归档记录；沿用 Markdown 编解码器的空白规范化，不套用 500 字符的注意力摘要
上限。显式标题冲突仍拒绝，即使前 500 字符相同；不能靠缩短已提交任务来修显示。
`todo list`（含精确 ID）、状态、前端及聊天继续使用原有有界摘要，canonical provider
及重建后的活动状态保留完整源文本，不新增设置、Lark 命令或第二状态库。本次仅修
既有 Python Markdown／兼容 I/O，TS 的权威、准入和交付确认 owner 不变。

若业务已提交、长文本一致性失败导致显示 pending，先读取当前 provider revision，
再用原有 `project-markdown` 恢复；不重复业务、不改短任务、不复用旧版本。恢复与
幂等复读不改变 canonical 记录、业务回执或 provider 版本；其他失败原因仍封闭拒绝。

Each section includes a compact `loopx:todo-section-projection-v0` marker with
the canonical provider revision and a SHA-256 digest of the complete canonical
records for that role. The marker is lineage evidence, not a write API.
The command proves that the rendered records came from an exact provider head.
Execution now also reads authority **after** durable file readback: `delivered`
and `current` require the rendered revision to match that observed head. This
strengthens the previous read-time provenance contract; a successful file write
alone no longer acknowledges delivery when an overlapping commit is observed.
`observed_provider_revision` names the confirmation point, not a lock on future
commits. Later mutations can still make the display stale. Consumers must always
read the provider, never the Markdown marker, for current authority state.

Rollback is intentionally asymmetric. Before promotion, the existing shadow
rollback quarantines the candidate provider lineage and Markdown remains
canonical. After promotion, a provider outage or revision mismatch fails
closed; operators may restore a reviewed provider snapshot and regenerate the
Todo sections, but must not promote stale Markdown back to canonical truth.

## Lease inspection / 租约检查

`loopx task-lease inspect --goal-id <goal> --todo-id <todo>` follows the same
promotion boundary as Todo reads. Before promotion it reads the existing local
lease store. After promotion it reads Todo, lease and handoff mode from one
canonical revision, reports `source_authority`, `provider_revision` and
`legacy_fallback_used=false`, and returns `lease_path=null` because no local
lease JSON is authoritative. Canonical absence returns `lease=null, active=false`;
provider errors fail the read, never revive stale local files or repair display.

`active` retains its existing meaning of an effective lease, not just an
unexpired timestamp. The retained `lease.status` can remain `active` while
`executor_constraint` explains a removed/excluded owner or divergent claim.
The shared typed owner predicate does not grant execution, mutate claims or
settle work. Release still requires its own key/version fence and remains usable
for cleanup after eligibility is lost. Acquire derives current effectiveness
from facts; old wire `effective` hints are accepted but cannot override them.

中文：promotion 后检查租约必须读取同一 revision 的 Todo/lease/handoff mode，
不能拼接本地旧文件。canonical 缺失表示无租约；来源故障明确失败。`active` 仍表示
有效租约，未过期但持有人失去资格时返回原因，不自动续租、转移或清理。
读取不提供写授权；release 的 key/version 门禁与幂等、CAS 规则保持不变。

### Refresh recovery and authoritative diagnostics

After promotion, a successful non-preview `refresh-state` now attempts Todo
projection delivery, including recovery of a previously committed same-Turn
writeback. CLI and Turn use the same path. Legacy refresh and preview remain
non-repairing; rejected admission does not acquire a display-write opportunity.
A provider or display failure after the refresh commit is `projection_delivery=pending`,
not a failed or repeated business write. JSON and Markdown responses disclose
that distinction. Retry the original Turn, or use `todo project-markdown` with a
fresh provider revision; do not repeat Todo completion or quota spend.

The planner retains its complete canonical snapshot for delivery rather than
immediately reading it again. The renderer still confirms authority after
durable file readback. TS returns a typed `next_action=retry|finish`; only
latest-head intent may retry an overlap, and no fourth attempt is admitted.
Pinned explicit projection preserves its requested revision. These are internal,
co-deployed request fields, not persisted request bytes or new receipt versions.
Existing receipts and provider formats are unchanged.

The refresh record's missing-work diagnosis also uses the same canonical Todo
summary. Stale Markdown cannot fabricate a missing-task warning or hide a truly
empty canonical group. Markdown remains the source of independent narrative.
Delivery can catch up to a newer provider revision without rewriting the earlier
refresh record or pretending its original planning snapshot was newer.

Recovery adds real rendering, file durability and confirmation work to committed
promoted refreshes. It is not a free read or a claim of lower latency. The normal
path shares the planning read and adds one confirmation read; same-Turn replay
loads the current head before repairing. Missing display recovers only Todo
sections, with the existing private-validation digest and source-ownership
checks. It cannot reconstruct independent Goal narrative or bypass an
unavailable private validation declaration. No timer, new outbox, persistent ACK,
provider default or active-Goal migration is introduced.

中文：已晋升 Goal 的非预览 `refresh-state` 和同 Turn 重试现在会恢复 Todo 显示。
业务成功、显示 pending 分别报告；只重试显示，不重新完成 Todo 或扣费。规划、缺失工作
诊断与投影起点复用完整 canonical 快照，权威空集合不回退到旧 Markdown；耐久写入后
仍读取 provider 确认，由 TS 统一决定是否追赶以及三次上限。Legacy、预览与拒绝请求
不获得新的显示写入。这个默认行为变化只影响已晋升 Goal，增加了渲染和耐久确认成本；
不改变默认 provider。缺失文件仅恢复 Todo 区域，不能恢复独立 Goal 叙述。

## Migration Path

The projector accepts complete legacy records and native `TodoDomainRecord`
manifests. Native records receive display-only section/index provenance; that
provenance never enters the canonical record. Archived records render in a
machine-owned `Completed Work Archive` region (created when needed) and retain
their original `role`. Unknown canonical fields and unsafe region ownership
continue to fail closed.

For promoted provider-first Todo create, claim, and supported text/planning updates,
the committed authority journal is the transaction-bound projection outbox:
the canonical mutation, complete head, cursor, revision, and receipt land in
one provider transaction. After that commit, the Python compatibility adapter
renders the latest head under the Markdown lock and durably reads it back. A
renderer/write failure leaves typed `pending` delivery
evidence without reversing or hiding the canonical commit. A later successful
mutation, committed `refresh-state` (including same-Turn replay), or
`todo project-markdown --execute` replays the current head idempotently. This is projection recovery, not a second authority path.
The ordinary state writer and projection writer share durable atomic publication.
Missing-display recovery uses create-only publication and cannot overwrite a
concurrently restored document. When bytes already match, execution still syncs
the file and parent directory before reporting `current`: a previous failure
may have occurred after rename but before directory durability. A failed barrier
keeps delivery `pending` and does not acknowledge or repeat the business mutation.
Preview remains read-only and does not request a delivery confirmation.

Unpinned mutation settlement makes at most three delivery attempts under the
existing display lock, reusing a newer complete read for the next attempt. A
pinned `project-markdown --provider-revision` checks its basis before writing and
never silently retargets another revision. An overlap after its write returns
`pending`, the rendered and observed revisions, `delivery_attempts`, and
`retry_business_mutation=false`. Persistent churn also returns pending rather
than looping indefinitely. A confirmation outage preserves the successful
business commit and remains retryable through the existing projection path.
Archived Monitor material generations use the same numeric decoder as active
reads and capture. Textual metadata such as `material_change_generation=12`
round-trips to the canonical integer; zero remains present and mismatched values
still fail parity. This fixes full-document recovery rejected by retained archived
Monitors without rewriting their authority records.

No new queue, persistent ACK, background worker, authority write or provider
default is introduced. Ordinary list/exact reads keep their response shape;
only the internal projection readback request opts into confirmation metadata.

The TypeScript read owner validates complete canonical data and compares the
host's durable readback revision with the same loaded head. Python retains
Markdown ownership, physical durability and rendering. The TypeScript confirmation
owns latest-head versus pinned intent and the three-attempt retry decision. A missing
confirmation from a downlevel runtime cannot be treated as delivery success.
The normal successful execution adds one provider read; each caught-up attempt
reuses the already returned full snapshot. This is a freshness cost, not a
latency improvement or atomic transaction across the database and filesystem.

中文：普通状态与投影共用原子落盘；缺失展示通过仅创建方式发布，避免覆盖并发恢复。
字节相同的执行重试也重新完成文件和目录耐久化。现在还必须在落盘后重新读取 authority，
由 TS 核对版本，才能确认 `current/delivered`。这加强了旧的“读取时来源正确”合同；
确认只对应一次观察点，不承诺之后永不变旧。未固定版本的交付最多尝试三次，复用较新
完整快照；显式 `--provider-revision` 不自动换目标。持续并发或确认失败保留业务提交，
展示返回 pending，重试只恢复展示，不重复业务。缺失文件的第二次追赶使用普通原子
替换，不能继续误用仅创建写入。预览不写入，也不确认交付。未新增队列、持久 ACK、
后台任务或默认 provider；普通读取形状不变，正常交付增加一次真实 provider 读取。
归档 Monitor 的代数元数据复用现有整数解码，修复字符串与整数比较造成的整份恢复失败；
零值仍保留，语义不一致仍拒绝，不改写 canonical 记录。

Supported non-Monitor Agent updates include action/domain/repository and required
write scopes, required/target capabilities and Explore node references. These
declarations use the same canonical planning transaction, not a direct Markdown
edit. Invalid supplied members reject the entire update; empty collections clear
the declaration. They do not grant execution rights, change a lease, or approve
a User decision. Work-requirement edits with a retained lease remain unsupported.

非 Monitor Agent Todo 的 action/domain/repository、写入范围、required/target
capability 和 Explore 引用声明复用同一 canonical planning 事务，不直接编辑
Markdown。非法输入整笔拒绝，空集合明确清除；声明不授予执行权、不变更 lease，
也不批准 User 决策。带保留 lease 的工作要求编辑仍不支持。

### Generated display recovery / 生成式展示恢复

LoopX state documents are generated and maintained by Agents through LoopX.
There is no separate hand-written-document workflow or recovery approval gate.
After promotion, a missing Markdown target is automatically regenerated during
normal projection delivery. The existing command is also sufficient:

```bash
loopx --format json todo list --goal-id <goal-id>
# Use provider_revision from that read; omitting --execute previews only.
loopx todo project-markdown --goal-id <goal-id> --provider-revision <revision>
loopx todo project-markdown --goal-id <goal-id> --provider-revision <revision> --execute
```

The generated document states its recovery scope. The current coordination
provider contains Todo and lease state, not every Objective, operating contract,
vision, Next Action or progress-ledger section. A missing-file rebuild therefore
reports `recovery_scope=todo_sections_only` and `narrative_preserved=false`;
it is not a complete Goal-state restore. Existing non-Todo content is preserved
regardless of who generated it. Those remaining state families must converge
on their own canonical sources before whole-document reconstruction can be
claimed; do not introduce a second Markdown authority or fill gaps from prose.

Publication uses the existing source lock and source-ownership checks. New files
are private (`0600` on POSIX) and published atomically without replacing a file
concurrently restored by another writer. Existing malformed or non-UTF-8 files
remain untouched and delivery stays pending. A stale requested revision or
unavailable canonical provider cannot rebuild the target. Normal reads never
write it. Recovery does not rerun the business transaction or validation command;
private validation declarations must still match the canonical digest.

缺失的展示文件由投影交付自动重建，不增加手写文档假设、人工确认或额外开关；原有
`project-markdown` 仍默认预览。当前只恢复 canonical Todo 的活动与归档区域，并明确
报告 `todo_sections_only`，不冒充完整 Goal 状态恢复。已有的非 Todo 内容无论由谁生成
都保持原样；其权威源归一化是后续状态迁移，不是人工补文档流程。损坏文件不静默覆盖，
revision 不匹配或 provider 不可用时不重建；交付失败只重试投影，不重复业务事务。

1. Emit this projection from active-state Markdown.
2. Add parity smokes comparing it with existing status todo summaries.
3. Move Markdown parsing into a dedicated active-state read-model module behind
   the same projection fields.
4. Promote one complete provider-backed mutation at a time behind the durable
   writer fence; keep the default Markdown mode unchanged.
5. Regenerate only machine-owned active/archive sections from one exact
   canonical provider revision, preserving human narrative and validating
   parse/render parity.
6. Extend the same journal-backed delivery contract to each remaining native
   Todo mutation before claiming full promotion coverage.
7. Promote a provider projection only after rollback and idempotency checks are
   in place.
