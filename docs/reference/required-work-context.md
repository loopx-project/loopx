# Required work context

For a task example, supported setup, recovery and evaluation plan, start with the
[context-delivery guide](../guides/context-delivery.md). This reference remains
the owner of current delivery semantics.

This page specifies current delivery behavior. The
[unified-contract RFC](../architecture/rfcs/controller-executor-unified-contract-v0.md)
proposes one full/delta/detail/recovery pipeline across entrypoints. Its
controller-authored brief must retain complete original requirements and
reachable evidence; experimental model-token targets never permit truncation
or replace source-consumption obligations.
The proposed shared composition boundary preserves source revisions and pending
read obligations before capture. Recovering delivery from that immutable capture
does not refresh authority; a legitimate new selection/authority check remains
distinct from retrying transport.

The RFC's [two-layer delivery design](../architecture/rfcs/controller-executor-unified-contract-v0.md#differential-context)
proposes host-independent Progressive defaults, independent qualified Delta and
current-capture discovery of unread material, with an explicit legacy/off
migration matrix. Those changes are **not shipped by this page**: current defaults
and `context_delivery` on/off behavior below remain in force. Current selected
reads already reach the complete capture; calling a reader or reaching EOF alone
does not prove model-visible delivery, retention or adoption. Native saved-file
Delta keeps its existing artifact proof without a full-model-read prerequisite.

中文：RFC 的[双层交付设计](../architecture/rfcs/controller-executor-unified-contract-v0.zh-CN.md#差分上下文)
提议跨 Host 默认 Progressive、独立资格化 Delta，以及从当前 capture 发现未读材料，
并明确旧配置/off 迁移矩阵。这些变更**尚未由本页交付**，下文当前默认值和
`context_delivery` on/off 行为继续有效。当前选读已能到达完整 capture；调用 reader
或到达 EOF 本身不证明模型可见交付、retention 或采用。原生文件 Delta 仍按既有产物
证明复用，不增加模型全量读取前置。

The RFC's [candidate context by decision stage](../architecture/rfcs/controller-executor-unified-contract-v0.md#candidate-context-by-decision-stage)
proposes complete captured alternatives with discoverable decision facts and
same-capture batch detail reads. It does not change current read obligations.
The current capability gate leaves candidate selection to the Agent; a selected
Todo display is not proof that it can ignore alternatives. Default-off and signed-envelope captures retain their existing full-read obligations.
Enabled native file delivery instead follows the progressive-read contract below. A future staged caller must replace its
delivery, required reads and recovery together, including unknown-scope full
fallback; an index read cannot establish delivery of hidden bodies.

Ordinary Heartbeat/quota delivers the full effective selected Todo, enabled
canonical acceptance and Agent-scoped User obligations at
`interaction_contract.agent_channel.work_context`. These are current work
sources, with readback/revision metadata; no TurnEnvelope switch is needed.
The selected Todo's display body is a display-only view and may be bounded.
Its exact detail read is authoritative for the current body and is validated
against the selected identity, lifecycle, claim and source revision. When a
full-body snapshot accompanies the selection, a mismatch remains pending and
requires a fresh guard. When the selected body is byte-for-byte the same as the exact read, `work_context.selected_todo_ref`
reuses it and the source retains its identity, lifecycle, claim, continuation,
relations and authority revision without repeating the body. If no other
canonical fields need a source record, `work_context.selected_todo_authority`
carries the source and revision. When the view is bounded, the full canonical
detail remains in `work_context.sources`.
The ordinary CLI shares exact acceptance copies within one response. Existing
references follow their complete inline owner when that projection relocates it,
including an owner visited later. Different revisions, types, failures and
sources stay distinct. This adds no source read or authority, admission or
settlement change; saved captures retain their original contents.
Vision-audit and replan-authoring mirrors reuse that local serialization
identity. Boolean/number values, integer/integral-float representations and
signed zero remain distinct when their original JSON encodings differ. Key
order alone does not prevent sharing. This adds no model read or runtime round
trip; distinct source facts retain their complete instructions.
Registered Goal state uses the existing writer's strict generated-region
ownership. When every recognized Todo region is explicitly marked, the source
adapter delivers **all other text verbatim** in `work_context.sources`: Goal
intent, frontmatter, arbitrary sections, comments, history and final stops.
`excluded_todo_regions` retains each generated region's role, line interval and
SHA-256; `revision` hashes the entire original file, including those regions.
Canonical exact Todo and scoped User reads retain task requirements and authority.
This default read changes the Goal obligation from the mixed document to its
complete non-generated text; it does not summarize requirements or erase history.
No source file or canonical record is rewritten.

Complete historical Monitor receipt lines are also generated data. The receipt
codec recognizes their exact marker, schema, Goal/operation identity and encoded
wrapper; `excluded_monitor_receipts` retains `count` and inclusive one-based
`line_ranges`, coalescing adjacent lines. The parent `state_file` and full-file
`revision` bind every interval; per-line hashes add no recovery information.
Previously captured per-line records remain readable. Invalid counts, unordered
or overlapping intervals, and intersections with Todo regions fail closed.
Their encoded bodies remain in the original file for operation replay, while
the current canonical Todo reads still supply active work and obligations.
Ordinary comments, fenced examples, malformed/unknown receipts, foreign-Goal
receipts and lines with additional prose remain verbatim. This avoids making
an initial full context read carry an accumulated recovery journal. It does not
change receipt replay validation or declare a model-input/token saving.

Unmarked or mixed ownership keeps the original full progressive read in
`agent_channel.required_reads`. Malformed markers, non-generated content inside
a marked region, unavailable files and invalid UTF-8 fail closed. Unknown
narrative is never classified by headings or clipped to a size budget. For explicit-off and signed-envelope callers, read all
delivered context and remaining reads before dependent work; changed sources need
a fresh guard. Enabled scoped acceptance does not replace original Goal intent.

Explicitly enabled native context delivery requests complete Goal material before
capture. For an unmarked or mixed Goal, the registered-source adapter supplies
the entire original UTF-8 text with its full-file revision and `full_source: true`.
The existing TypeScript interaction owner checks that digest before fulfilling
the read; `complete_goal_v1` acknowledges support so an older runtime cannot
silently retain a pending Goal read. Missing or invalid sources still reject.
Marked sources retain their generated-region lineage and canonical Todo authority.

Native delta delivery and source fulfillment share the same integrity predicate.
Only exact verified Goal text can be inherited from the retained baseline;
current source witnesses and unknown fields remain literal. Cold delivery and
full-capture recovery retain the complete original text, including arbitrary
sections, comments and final stops.

The native initial selection may render a generated Objective once when the
frontmatter entry and the entire generated Markdown blockquote agree exactly
under the writer's format. The selected text is marked as a presentation.
Literal `--select` and full recovery on the same immutable capture retain the
original bytes. Differing or unrecognized formats keep both values. The native
read observer does not count the presented leaf as reading its literal source.

原生首读仅在生成的 frontmatter 目标与正文整段生成引用相符时展示一次目标。
首读会标记这一呈现；同一捕获的显式选读与完整恢复保留原字节。两处不一致
或格式无法确认时保留原文。首读观察不会把呈现文本记为已读原始字段。

Enabled native quota delivery separates known acceptance requirements from their
supporting material before full/delta presentation. The TypeScript acceptance
projection keeps every scoped outcome objective, requirement description,
review conclusion, current-basis flag, independent-verification flag, work link
and source revision inline. Outcome source history and Agent review notes/evidence
references remain in the immutable original capture. Each projected outcome has
a `supporting_material_pointer`; `context_details` supplies the original path,
SHA-256 and `quota context-read --select` command. Read needed evidence before a
decision, verification or review that depends on it; repeated selectors batch
related source, note and evidence fields without replaying the outcome's inline
requirements. An unmet or stale review stays visible before its explanation
is opened. Agent review still does not establish independent acceptance.

In the first read, an unexpanded outcome can use the parent of its selected
`id` JSON pointer to locate supporting material. The planner omits a separate
address only when it exactly matches that parent and the pinned instructions
are included. The required view and explicit selectors retain the address.
Older captures, different addresses and unavailable guidance keep the original
first-read address.

The archive remains `projection.json`; the required model view is
`required-context.json`. Required reads, full/delta preparation and recovery use
that same model view. A selected detail page cannot acknowledge full model retention of the required
view or prove that other hidden details were read. Native file baselines are
established independently from complete saved captures.
The adapter edits paths selected by the typed owner without round-tripping the
original JSON values through JavaScript. Unknown outcome fields, unknown source
read obligations, unsupported schemas, signed envelopes and unavailable planning
retain their original full content. Context delivery off remains unchanged.
If the required view and its index are not smaller in serialized UTF-8 bytes,
delivery retains the original full view and read obligations; small material
does not need a more expensive detail round trip. This is a transport-size
comparison, not a relevance heuristic or a model-token estimate.
Historical captures retain their original obligations. This acceptance-material
slice does not yet stage candidate selection, preferences or Explore history,
and a smaller body alone does not establish lower total model cost.

Native required views also replace exact repeated registered Todo rows in
summary lanes with `{todo_id, context_ref}`. The JSON pointer addresses the
complete first occurrence in this **same required view**, never an old baseline
or an unexpanded detail. Every lane retains its membership and order; all unique
fields, revisions, ownership, dependency state, contrary evidence and final
constraints remain inline. Referenced rows carry exactly those same obligations.
Unknown row fields/schemas, unknown lanes and unsafe numeric values stay literal.
The reader must recover a missing target from this same capture before dependent
work; it does not need a new detail read when the complete target is present.
This is exact reuse, not candidate relevance or selection. It changes no
canonical record or authority, and does not permit skipping other candidates.
Signed envelopes, disabled native delivery and historical captures keep their
existing behavior. The existing size comparison retains a full view when the
reference guidance would cost more than the duplicate rows.

A context epoch is not a work Turn. Compaction or a failed native hook invalidates
the previous epoch’s delta reuse; it does not require another admission or unconditional replay of the
old quota packet. Within the existing Turn, continue from the working brief with
the Goal, applicable requirements/stops, task and original capture/settlement
identity. Before an action that depends on missing or uncertain facts, recover
those facts from the same immutable capture. An unfinished selected section needs its missing facts before dependent work;
summarizing it does not prove those facts were read. Legacy captures retain
their original read contract until their current work Turn ends.
Do not repeat completed work or settle the original Turn twice. If the original
reference is lost, repair it before dependent work; independently authorized work
can continue. A later work Turn obtains its own current guard and required
context, without prefetching an older packet to manufacture a delta baseline.

The native lifecycle still invalidates the previous baseline. A working brief
is sufficient only for the work whose necessary facts it preserves; it is not
input-byte coverage, a new baseline, renewed authority or proof of freshness or
model retention. These separate boundaries prevent transport loss from becoming
either a repeated full-input loop or permission to act on missing requirements.
No token savings or accepted outcome gains follow from instruction delivery alone.

Reader commands emitted by native quota, saved-context projection, bounded
settlement results and a running `context-read` keep the explicit CLI selection,
or the current Python interpreter when no CLI was specified. This covers initial
navigation, complete reads, continuations and rejected-selection recovery.
Capture bytes, digests, cursors and authority are unchanged; native full/delta
byte comparisons include this reader command metadata. Existing in-process
reference constructors keep their default command route.

An enabled native **full or delta** reference for a recognized decision can include
`first_read_command`, reading its current full capture without an older baseline.
It batches current admission/stop facts, the current action, the selected task,
whole Goal/Todo narrative, source availability/revisions and read routes. Detailed
history and optional authoring recipes remain expandable from the same capture.
Current narrative sources and their provenance precede other sources, including
large scoped preferences; both retain their complete selected values.
The preference source's complete `current` view also stays in the initial read:
corrections, retirement/expiry and empty replacements retain their provenance,
revision and advisory authority. This captured pre-work view does not replace the
preference owner's fresh exact-scope read before a later preference-dependent
external action, and neither delivery nor a preference grants authority.
The initial read includes the bounded `unfinished_delegations` projection and
`read_complete` flag. For normal work with a large agenda, a complete scan
and a matching required Manager Inbox read, the initial view keeps the incoming
and outgoing request counts, scan status and required command. This also
applies during explicit Todo selection when work and quota spending are
still disabled. A complete scan can still hit the summary's display limit.
The initial view then also keeps the exact omitted count and reconciliation
guidance. The required read and its existing cursor deliver the scoped request
rows. The captured summary remains available at its original address. An
incomplete scan, unavailable scan records or an unmatched read keeps the whole
summary inline.
Adoption and queued returns are intermediate states. Read the full current request before
assessing, executing or returning it. Use the existing scoped request and
consumption routes for those actions.
It also exposes existing outcome material addresses and the current settlement
plan's obligations, including unknown step fields. Only attachments explicitly
marked `required=false` defer their guidance/templates; their routes stay visible
and the complete captured attachment must be read before use. Unknown plan
versions remain whole. Initial pages do not repeat pointers already present in
their content; the selection digest still prevents a full-input receipt.
Its `context-read --initial-read` uses the same exact-address selector. New private
selection references pin the resolved addresses and complete selection digest;
continuations use the existing explicit reader instead of rerunning initial
navigation. A later navigation change cannot replace the saved selection.
Historical recipe-only references and explicit-file initial cursors still reject
a changed selection digest. If the original capture passes file-digest
verification, the recovery command restarts at offset zero. Historical
initial-read cursors can recover the full original file. An explicit selection
or field listing keeps its original scope, including a receipt-only diagnostic.
The recovery never repeats admission or rewrites the saved descriptor. Retain
the legacy readers while unfinished Turns still depend on them.
Initial navigation can replace fully
selected children with their complete captured parent to avoid repeating addresses.
An unselected child, including optional or unknown material, prevents that merge.
An array also stays separate when merging would restore an earlier element order
and undo the owner's reading priority; ancestors cannot erase that boundary.
The selected facts and obligations stay identical. Explicit selectors and index
entries keep their original addresses. The option is read-only and exclusive with explicit
selectors or discovery; unknown schemas use generic discovery.
Pinned explicit references replay their recorded address order after the same
canonical address validation. Older descriptors may contain caller-ordered
addresses but a digest of canonical output: only after a digest mismatch, the
reader tries that original canonical planner once over the same verified source
and address set. Only exact matching bytes are returned. This may add one
read-only planner call per legacy page; it adds no model-input receipt or state
write. Historical recipe-only references and raw initial cursors keep their
strict profile-change refusal and full-capture recovery.
The existing internal `context_select` request accepts the validated boolean
`preserve_selection_order` for this rendering step. Public explicit selectors
and diagnostics are unchanged; the stored descriptor shape and CLI options are
unchanged.
Initial selections retain the typed owner's priority rather than alphabetical
pointer order. Current controls, required sources and settlement obligations
precede the complete unread-material index. A coalesced parent keeps
its first selected child's position; explicit selectors retain canonical order.
This reorders the same selected facts without reducing the total body or
guaranteeing that all requirements fit one page. Earlier initial-selection
digests still reject a changed profile; recover from the original full capture.
The concrete first read replaces the broad scalar preview and root index when it
fits; unknown schemas or a tight reference budget keep generic discovery. A warm
delta's existing `command` still reads its wire delta; its initial read and
recovery read current facts directly. Disabled native delivery is unchanged.
This is a starting selection, not all required sources: read applicable criteria,
evidence and the complete operation/settlement contract before depending on them.
The original SHA-bound full recovery remains available; a selected page's EOF
does not prove complete source delivery, model retention or fresh authority.

For an exact complete Turn bound to a Todo whose dated resume condition has
fired, the starting selection puts the existing reopen and same-Turn guard
commands and the current plan's obligations before the longer work context.
Required and unknown step fields stay visible. After reopening, run the
continuation guard for a fresh execution decision. For an alternative that
settles before reopening,
read the complete original plan at `/interaction_contract/cli_channel/settlement_plan`
from the original recovery reference before acting. Delivery and spend require
their existing separate authorization.

The generated `first_read_command` and its paging cursor default to a 12,000-byte
serialized UTF-8 output limit when the caller supplied no tool budget. This
applies after explicit Todo selection as well. An explicit output limit still
controls the opening, and the ordinary bulk and recovery commands keep their
existing budgets.

中文：调用方未指定工具预算时，生成的 `first_read_command` 与续页默认每次最多
返回 12,000 个序列化 UTF-8 字节。显式选择 Todo 后的首读也适用。调用方可以显式
指定首读预算，完整读取与恢复命令继续使用原有预算。

For a saved benchmark preflight, the ordinary wake prompt carries the command for
the first page: native JSON `quota context-project --initial-read` publishes the
saved decision and reads it in one call. Replace `--initial-read` with repeated
`--select` for known fields. The opening defaults to 12,000 serialized UTF-8
bytes including navigation. With a known tool and retained-history budget,
set `--max-page-output-bytes` to a compatible byte limit, up to 64,000. A byte
limit is not a model-token or retention guarantee. An optional `--page-bytes`
also bounds UTF-8 content on the attached page and its cursor, using the same
window rules as `context-read`. The output budget still applies to the complete
serialized response.

Read the top-level `content` and follow its `next_command` until that selection's
`eof`. If the whole initial selection would need paging and its decision and
read obligations fit in one page, the opening contains those controls and
`navigation.remaining_read_command` points to the rest. The page's
`read_instruction` directs the reader to that command even when the opening
reports EOF. Follow it and its cursor before work. Both selections pin the same
immutable capture.
`navigation.first_read_command` replays the opening. Use
`navigation.recovery.command` for other fields. If the response is lost, use the
original digest-bound capture in the wake prompt. Recovery reuses the saved
decision and its original receipt.

When native quota navigation requires explicit Todo selection, the opening
keeps the discovery and same-Turn selection commands with the read obligations
and execution controls. If the recommended candidate makes these controls too
large for one page, the linked remainder contains its full details and the
suggested alternatives. The selection commands use the original Turn identity.

Native ready decisions from `quota should-run` and saved `quota context-project`
can link two reads when boundary detail pushes controls past the serialized
first page budget. When those controls fit, the ordinary reader keeps its cursor.
Read `first_read_command` for the decision, stop conditions and required reads.
If `remaining_read_command` is present,
follow it and its cursor before work, even when the opening reports EOF. It
starts with the complete current write scope and any checkpointed approval
entries. The opening shows the current approval counts and active scope summary.
The remainder can refer to other fully observed, unchanged fields. If the
observation is lost, its same-capture fallback restores the full remainder.

When the guard requires an independent worktree before delivery, the opening
keeps the relocation instructions and the current decision controls. Large
Goal and selected Todo write scope lists are available from the same capture
at `/goal_boundary/write_scope` and `/selected_todo/required_write_scopes`.
Select them if the relocation needs them. Once relocated, rerun the guard for
the same Turn and read its complete current scopes before editing. This
presentation changes neither admission nor the explicit off view.

中文：对于已保存的 benchmark 准入结果，唤醒正文直接给出首读命令。一次调用即可
取得首屏，已知字段可改用多个 `--select`。首读默认最多 12,000 个 UTF-8 字节，含导航。
已知工具直出与历史保留上限时，可用 `--max-page-output-bytes` 指定匹配的字节预算，最高
64,000。字节数不能保证模型实际收到的 token 数或历史保留。附带页及续读也可用
`--page-bytes` 限制正文的 UTF-8 字节数，沿用 `context-read` 的窗口规则。
输出预算仍约束整个序列化响应。

阅读顶层 `content`，按 `next_command` 续读到当前选区的 `eof`。首读内容需要分页且决策
和读取义务能完整放在一页时，首屏先交付这些控制信息，并给出
`navigation.remaining_read_command`。即使首屏已到 EOF，页级 `read_instruction` 仍指向
这条命令。工作前沿这条命令及其游标读完余下的首读内容。两个选区绑定同一份不可变捕获。
`navigation.first_read_command` 可重读首屏，其他字段沿
`navigation.recovery.command` 读取。结果丢失时，用唤醒正文中的原文件及摘要恢复，复用原决策和回执。

原生额度导航要求显式选择 Todo 时，首读把候选发现、同一轮次的选择命令、必读项和执行
约束放在一起。推荐候选的明细导致这些信息超出单页预算时，同一捕获的续读保留完整
候选和其他建议选项。选择命令沿用原轮次身份。

原生 `quota should-run` 及已保存的 `quota context-project` 就绪决策中，如果权限明细让
控制信息超出序列化后的首页预算，导航可给出两段读取。控制信息能放进首页时，沿用普通游标。
先用 `first_read_command` 取得决策、停止条件和必读项。如果给出
`remaining_read_command`，即使首段已到 EOF，也要在开工前沿该命令及其游标读完。
续读从完整的当前写入范围和已记录的审批条目开始，两段绑定同一份捕获。首段保留当前审批
数量和生效范围摘要。
续读可引用其他已完整送达且未变化的字段。观察失效时，从同一捕获恢复完整续段。

准入检查要求先切换到独立工作树时，首读保留迁移指引和当前决策控制信息。
如迁移需要较长的 Goal 或选中 Todo 写入范围，可从同一捕获按
`/goal_boundary/write_scope` 和 `/selected_todo/required_write_scopes` 读取。
切换后以原 Turn 重新准入，编辑前读取其完整的当前范围。该展示调整不改变准入判定
或显式关闭渐进读取时的视图。

When the existing acceptance owner supplies complete `outcome_context`
navigation matching this capture's Goal, Agent, selected Todo, outcome revision
and provider revision, the initial read expands its suggested outcomes in full
as present in the required view, including cross-Agent dependencies and unknown
outcome fields. Other outcomes retain their existing addresses; this is reading
guidance, not proof of independence or permission to skip applicable criteria.
Incomplete, mismatched, ambiguous or unavailable navigation keeps the existing
index/detail route. Folded supporting material still has its original read
obligations. The generic reader needs no Session Hook for this selection;
activation defaults, saved files, Delta and full-read receipts are unchanged.

中文：已启用原生传输的 full 或 delta 引用可给出 `first_read_command`，直接读取本次
完整捕获，无须先读旧基线。首读合并当前准入、停止条件、动作、任务和完整 Goal/Todo
正文，以及来源状态、版本与读取入口；正文来源及其 provenance 先于其他来源，
包括较大的 scoped preferences，双方完整选中值均保留。历史和可选格式说明仍在
同一捕获中按需展开。它是起始选读，不代表已读全部必要来源；依赖具体验收、证据或
执行/结算契约前仍须完整读取。
偏好来源的完整 `current` 视图也直接呈现：纠偏、退役/过期和空视图替换均保留
原来源、版本及 advisory 权限边界。本次捕获的工作前视图不能替代偏好 owner 要求的
后续偏好相关外部动作前精确作用域新读；偏好及其交付均不授予权限。
未知 schema 和过小引用预算保留既有路线；warm delta 的 `command` 仍读取原 wire delta，
首读和恢复入口直接读取当前事实。
关闭原生传输的输出不变；完整恢复和原 SHA 校验均保留。只读的 `--initial-read` 不与
显式选读或索引混用。新私有选读引用固定已解析路径和完整选读摘要，续页复用显式
reader，不重新运行首读导航；后续导航改变不能替换原选读。历史仅保存导航配方的引用
及显式文件首读游标在选读摘要变化时仍拒绝，沿同一完整原 capture 引用恢复，不重新
准入或改写旧描述文件。退役这些 legacy reader 前仍须对账原未完成 Turn；本切片
保留历史解码与完整捕获恢复。
原生 quota、已保存上下文投影、有界结算结果和实际运行的 `context-read` 生成读取
命令时，保留显式指定的 CLI；未指定 CLI 则使用当前 Python 解释器。首读导航、
完整读取、续页及选读拒绝恢复均沿用该程序。捕获正文、摘要、游标和权限不变，原生
full/delta 字节比较计入这些 reader 命令元数据；既有进程内引用构造器保留默认路由。
首读可把已经完整选中的同级内容合并为其完整父节点，减少地址重复；任何未选中的
可选或未知内容都会阻止合并；若数组合并会恢复原元素顺序、覆盖 owner 的读取优先级，
该数组及其祖先也不合并。选中事实和义务不变。显式选读和索引仍保留原地址。
固定显式引用在既有规范地址校验后，按记录的地址顺序重放。旧描述符若保存调用方顺序
却绑定规范排序正文的摘要，仅在摘要不匹配后，对同一已验证来源及相同地址集合重试
一次原规范 planner；只返回摘要完全匹配的字节。此兼容路径每个旧分页最多增加一次
只读 planner 调用，不写状态或证明模型输入覆盖。历史 recipe-only 引用和原始首读
游标仍在 profile 变化时严格拒绝。原捕获摘要通过校验后，恢复命令从字节零开始。
历史首读可恢复完整原文件；显式选读和字段列表保留原范围，包括只读诊断回执。
恢复不会再次准入或改写已保存的描述符。既有内部 `context_select`
请求为重放接受严格 boolean 的 `preserve_selection_order`。常规显式选读和其他错误
诊断不变，reader 描述符字段结构和 CLI 选项不变。
首读按 TypeScript owner 的优先顺序呈现，不再按指针字母序重排；当前控制条件、必读
来源和结算义务先于完整的未读材料索引。合并父节点保留首个已选子节点的位置，
显式选读仍按既有规范排序。这只调整相同事实的顺序，不缩减总正文，也不保证全部
要求能放入一页。旧首读选读摘要遇到 profile 变化仍拒绝混读，可从原完整捕获恢复。
首读也给出现有验收材料地址和当前结算义务。仅明确 `required=false` 的附件延后完整
说明与模板，使用前须读原附件；未知计划保留全文。首读分页不重复正文中的指针，
选读摘要仍阻止将它记作完整输入。
首读包含有界 `unfinished_delegations` 投影和 `read_complete` 标记。已准入的普通
工作 Turn 若有大型委派摘要、扫描完整，且必读列表中有同一条管家收件箱命令，首读保留
收发请求计数、扫描状态和必读命令。扫描完整但摘要达到展示上限时，首读同时保留精确的
省略数量与核对指引。逐条请求通过必读命令及其现有分页游标交付。原摘要仍可从同一
捕获取回。扫描不完整、扫描记录不可读或没有匹配必读命令时，首读保留完整摘要。
采纳和回传入队是中间状态，评估、执行或回传前须读当前请求全文，并沿已有范围约束和
消费入口办理。

既有 acceptance owner 给出的完整 `outcome_context` 导航，与当前 capture 的 Goal、
Agent、所选 Todo、成果版本及 provider revision 一致时，首读完整展开其建议成果在
必读视图中的正文，包括跨 Agent 依赖和未知成果字段；其他成果仍保留原地址。这是读取
建议，不证明相互独立，也不允许省略适用要求。导航不完整、错配、歧义或不可用时保留
原索引/详情路线；折叠的支持材料仍遵守原读取义务。此选读使用无需 Session Hook 的
通用 reader，不改变启用默认值、落盘文件、Delta 或完整读取回执。

An ordinary JSON `quota should-run` with Progressive enabled can stage an
eligible replan from its private complete `projection.json` without an
explicit capture destination. Read `replan_action_packet.review` before choosing
work, then use `review.next` for selection or writeback. Both stage readers pin
the original source hash and the exact file digest. The required view's
`context_details` still addresses the complete source for missing facts.
These reads do not enter the guard, update authority, or spend quota.

普通 JSON `quota should-run` 启用 Progressive 时，可直接用私有完整
`projection.json` 分阶段交付符合条件的重规划，无需指定捕获目录。选定工作前先读
`replan_action_packet.review`，再沿 `review.next` 选任务或读取回写要求。两阶段
均校验原决策哈希和文件字节摘要。必读视图的 `context_details` 提供完整来源，
供补读缺失事实。读取不再进入准入检查，不写入权威状态，也不扣额度。

For supporting fields folded by the existing native Progressive owner,
`context_details.unread_context_index` contains the current complete
`projection.json` source path/digest and exact `pointers`. The initial reader
shows that index; a file-backed warm delta repeats it in full even when its
pointers are unchanged. Each pointer uses the ordinary `context-read`
`--capture-file`, `--capture-sha256` and `--select` route, independently of any
previous packet, retained model input or patch chain. These addresses belong to
the complete source, not the derived `required-context.json` recovery view.

`coverage_confirmed=false` conservatively reports no trusted model-visible
coverage witness. A previous select, reader EOF or file receipt does not clear
an entry or create `confirmed_read_sections`/`consumed_pointers` state.
`read_before_dependent_work` preserves the original obligation to read material
when the decision or verification needs it; it is not an automatic full-read or
execution gate. Updated/reordered material binds to its current addresses;
removed material does not retain a predecessor route. The actual index and
guidance count toward the existing byte guard, including fallback to the
complete file when folding is not smaller. In-view deduplication alone adds no
index. This bounded native slice changes no activation defaults or off path;
cross-host rollout and trusted coverage observation remain C1/C2 work.

中文：现有原生 Progressive owner 折叠的支持字段，在
`context_details.unread_context_index` 中列出当前完整 `projection.json` 的路径、
摘要和精确 `pointers`。首读呈现该索引；文件 warm delta 即使指针未变也完整重发。
各指针通过普通 `context-read --capture-file ... --capture-sha256 ... --select ...`
独立读取，不依赖旧包、模型保留或补丁链；它们属于完整源，不属于派生 required view。
`coverage_confirmed=false` 表示没有可信模型可见覆盖见证，选读、EOF或文件回执不会
清除条目，也不新建已读账本。`read_before_dependent_work` 保留原先按决策/验证需要
读取的义务，不成为自动全读或执行门。变化/重排绑定当前地址，移除材料不沿用前序入口。
索引和指导计入原字节守卫，不更小时回退完整文件；仅视图内去重不新增索引。本原生切片
不改启用默认值或 off 路径，跨宿主推广和可信覆盖观察仍属 C1/C2 未完成工作。

Checkpoint Host preflight uses the hidden `--defer-context-presentation` option
with an explicitly enabled native JSON `should-run` capture. It collects the
same complete Goal source for admitted work-context reads and writes the ordinary
immutable CLI view, without
reading or replacing the model session's continuity state. After native lifecycle
hooks run, the model presents that saved view with `context-project`; this does
not re-enter admission. Deferred presentation requires a capture destination and
cannot be combined with socket handoff. Ordinary callers keep their existing
presentation, and context delivery off keeps its original pending Goal reads.
Unbound staged replans retain their existing review-owned original Goal read.

After a Host claim, the ready guard uses the private
`--prepared-capture-reference` route. It publishes the complete decision and
returns its writer digest with the fields needed to verify the same Todo and
Turn. The hook verifies that digest when it opens the original file. If the
file changed, the receipt preserves the claim and pins recovery to the original
bytes before work can continue.
The private native journal reserves automatic preparation before claiming a
bound capture and records the verified ready capture before projecting it. If
readiness or hook delivery is interrupted, the hook repeatably surfaces
readback for the original claim receipt, current lease and any verified ready
capture without repeating the claim or guard. A Hook response alone does not
prove delivery. For this session the repeat hint is suppressed after a tool
read is verified against the recorded ready capture.
Follow the captured readiness check for the same Todo and Turn once the active
lease matches. An authorized retry of the same bound capture reuses its
managed claim operation, and the authority owner checks the original lease on
replay. The native session keeps the latest eligible live guard digest
separately from the capture. Presenting the exact saved decision preserves
that binding. Older captures and copies with different bytes remain readable.
Preserve any original claim receipt and use `quota should-run` with the same
Goal, Agent, Todo and Turn and a fresh capture if preparation is still
required. A retired lease requires a fresh eligible preparation capture.
The native hook executes only a preparation bound to a captured Turn. An
ordinary claim recommendation remains available to its caller; reading one
or reading an original receipt does not trigger an automatic claim.

For a selected Todo, preparation reads the canonical Todo and leases at one
checked provider revision. The internal read returns only that Todo's status,
assignment and lease facts. Unrelated Todo bodies stay with the provider
reader. Quota still checks ownership and lease expiry before reporting readiness.

中文：私有原生日志在对绑定 capture 发起领取前先登记自动准备，并在投影前记录已校验的
ready capture。若就绪检查或 Hook 送达中断，Hook 可重复提供原领取回执、当前租约
及 ready capture 的回读入口，不再重复自动领取或准入。Hook 输出本身不足以确认送达。
观察到针对原 ready capture 的已验证工具读取后，同会话才停止重复提示。活动租约匹配后，
沿同一 Todo 与 Turn 完成就绪检查。对同一绑定 capture 的授权重试复用原受管领取操作，
由权威 owner 验证原租约。原生会话独立保存最新合格准入的摘要，重新展示逐字节一致的
快照会保留绑定。旧快照和字节不同的副本仍可读，保留已有领取回执。仍需准备时，用原
Goal、Agent、Todo 和 Turn 运行 `quota should-run`，生成新的 capture，再按当前准入
结果准备。已退役的租约需要新的合格 capture。
原生 Hook 只执行与已捕获 Turn 绑定的准备。调用方仍可读取普通领取建议；读取建议或原
领取回执都不会触发自动领取。

准备阶段从同一已校验的权威版本读取 Todo 和租约，只传回所选 Todo 的状态、归属和租约
事实。其他任务正文留在读取端。额度检查仍核对归属和租约到期时间，再报告是否就绪。

The Goal availability adapter still reads and UTF-8-validates the complete file
and hashes all bytes for its revision. With native context delivery off, only the path and revision of unmarked or mixed sources cross the
internal context-projection bridge, preserving the complete progressive read
without an unused body copy. Explicitly owned sources send the complete
non-generated narrative and region lineage to the typed consumer. Whole-line
SQLite archive summaries from the current writer and the older unscoped writer
are recorded as archive ranges when they appear outside marked Todo blocks.
The full file revision still covers these lines. The canonical Todo query owns
archived and omitted work. Unrecognized forms, foreign Goal bindings and
examples inside code fences remain in the narrative. Missing files and invalid
UTF-8 still block dependent delivery. Byte reduction alone does not
establish model-token, latency or outcome gains.

Already delivered sources disappear from `required_reads`; unresolved provider
reads retain exact commands and ordering. The typed interaction owner owns this
selection and fulfillment policy in both ordinary packets and TurnEnvelope.
`work_context.complete` means no known source failure, not that pending reads
have been consumed. TurnEnvelope signs the same content and obligations; its
size warning never truncates current requirements or removes a pending read.
Full progressive reads carry the complete-Goal, no-summary and fresh-guard
obligations. Only a fulfilled registered narrative adds the generated-region
and canonical-Todo ownership explanation; pending reads do not repeat that
inapplicable explanation. Todo-bound semantic replanning also uses the same
autonomous Vision authoring contract in its action and settlement projections,
so exact in-response references retain complete replacement and replan rules.
Ordinary settlement and all execution/receipt checks keep their existing rules.
An existing receipt-bound Todo retains its original Task identity during a
semantic replan. Only an unbound fallback yields to the original Todo-less
planning obligation; that obligation must match its signed Goal, Agent and
Turn. Neither route admits ordinary work while delivery remains withheld.

Preferences remain conditional on scoped records. The existing preference and
Explore hooks return their actual read context through registered adapters;
the generic kernel never executes the displayed shell commands. Discovery
receipts stay separate from the single content carrier. Unconfigured hooks
preserve their off-state. User Todo inventory views disappear only from exact
Todo responses; scoped User decisions and gates remain in the work context
and retain their existing admission/notification authority.

Todo context retains complete scoped records and canonical read provenance,
including the record schema, snapshot digest, count and provider revision. It
omits the known canonical reader's `contract_fields` dictionary: field names
describe the reader's record format, not additional task requirements. Empty
scoped User results still retain their source proof. Unknown read schemas and
malformed dictionaries remain verbatim. The explicit canonical Todo commands
keep their full read metadata; this default context compaction requires no
cross-Turn baseline and leaves all record values, gates and stops intact.

Source failure retains the unresolved read, identifies the unavailable source,
and blocks dependent delivery until recovery and a fresh guard. Reading current
content is not an execution grant or proof of Goal completion.

For selected work, the acceptance adapter reuses the canonical owner's exact
Todo inspection. Its top-level `tasks` observations cover that Todo; the full
Goal contract, configured scope, checks, held work and cumulative outcomes stay
intact, including other owners' requirements. The separate selected-Todo source
retains its complete body and declaration. Todo-less reads and the explicit
`goal-acceptance inspect` command keep their existing full inventory. This
reduces repeated binding observations without treating a shorter context as
acceptance, authority or a measured model benefit.

For enabled native quota with selected work, the same acceptance snapshot may
include `outcome_context` navigation. `suggested_outcome_ids` prioritizes work
connected through registered Todo relations, shared acceptance/source/evidence
references, or failed/stale observations. An Agent owning two outcomes does not
by itself connect their work. `coverage_complete` describes coverage of the
registered relationship graph, not completeness of model understanding or proof
that other outcomes are irrelevant. Missing relationships, unbound work or global
acceptance widen suggestions and expose `limitations`.

This is advisory reading order. It neither filters outcomes nor adds a full-read
obligation, admission rule, approval or completion proof. The complete contract
stays in the existing capture; use generic `quota context-read --select` to open
needed sections, with `--list` where the installed reader supports discovery.
No outcome-specific reader or pagination protocol is introduced. The suggestion
is bound to the exact acceptance digest and provider revision; host oracle
readback that changes the source drops that stale advice. Disabled delivery,
explicit inventory inspection and signed TurnEnvelope retain their prior shapes.

For an exact Todo, the existing command now returns one complete record:

```sh
loopx --format json todo list --goal-id example-goal --todo-id todo_work
```

Read `todo.text` plus identity, status/claim, `relations`, source and canonical
`authority_read` revision. The former `todos`, `agent_todos` and `user_todos`
views are removed from exact responses. There is no opt-in compatibility flag.
Inventory reads without `--todo-id` retain list views; `--thin` belongs to those
bounded lists and is rejected with an exact identity. A full requirement tail
must never be replaced by a summary. Markdown also renders the original once.

Ordinary JSON inventories keep the selected `todos` array intact. Repeated
identical records in the role-summary lanes now use response-local JSON pointers,
for example `{"$ref":"#/todos/0"}`. Read the complete record at that pointer;
it is not a command or a new execution grant. A lane can contain a record outside
an explicit `--limit`; its first complete projection remains in that lane, and
later copies point there. Distinct projected facts are never merged by Todo id
or body text. Counts, order, source revision, scoped User obligations and resume,
claim and lease facts retain their original meaning. The
`todo_list_record_references` field describes this readback.

This is the default JSON CLI presentation, without a compatibility flag.
The native decision/frontend read model, Markdown, `--thin` and exact detail
retain their existing shapes. Existing inventory excerpts remain excerpts and
do not replace a full exact read or a fresh guard. Pointer expansion reconstructs
the prior inventory; reduced response size does not prove model adoption,
faster whole turns or better task outcomes.

Missing or filtered work returns `matched=false`, `todo=null`, `not_found=true`.
Ambiguous records and source failures fail visibly, without stale-display
fallback. Blocked, completed and archived records remain read-only observations.
An exact read grants no claim, lease, execution, publication or quota authority.
Changed requirements require fresh admission under the original owner.

The existing Python reader retains source resolution and filtering. It passes
one source row to the registered TypeScript `todo.context.page` projection,
without transporting duplicate body or role-summary views. Chat manager detail,
Explore writeback and monitor settlement use the exact record rather than a
removed list alias. No second authority store or selection rule is introduced.

This changes default exact CLI/API output and Heartbeat/Turn context delivery. Regenerate saved expanded heartbeat prompts after installing the
change. Bootstrap prompts load installed rules on the next wake. TurnEnvelope
transport remains an explicit projection choice; this change alone neither
upgrades installed automations nor proves model adoption or token/latency gains.
To roll back, install the prior revision and regenerate prompts; changing a
transport flag does not restore the old exact Todo schema.

## Opt-in native reuse

The retained-input codec described first in this section serves explicit
confirmed-input clients. Native Codex uses the separate saved-file basis
described under Machine default; it does not require full model retention.

The existing [native context delivery option](../heartbeat-automation-prompt.md#opt-in-native-context-delivery)
uses one TypeScript codec for ordinary quota and TurnEnvelope. Within a usable
baseline, both representations repeat `planning_horizon.core_goal`'s original
objective, provenance and availability, including a changed or missing objective.
The complete `acceptance_contract` and registered planning trees use exact
reuse, subtree changes or a complete refresh; the acceptance tree is not treated
as the original Goal sentence. Unknown horizon fields remain current input.

The same codec retains complete static closure guidance in quota channels and
the Turn contract capsule. Current closure decisions, conditions, identities,
commands and unknown controls remain current input. Explore's registered owner
also permits exact reuse of branch evidence audits and its evidence-link template;
branch identity, selection, status, resume facts and orchestration gates remain
current. Reordering, refutation, removed evidence and changed guidance reconstruct
the exact current source; no timestamp, revision or text is normalized away.

Within successfully read cumulative Goal outcomes, string source references,
source summaries, review notes and string evidence references also use exact
reuse. They are provenance and explanations, not current acceptance decisions. Requirement status, review
conclusion, basis currency, independent verification, revisions and unknown fields
remain literal current input. Changed, removed or reordered evidence reconstructs
the complete current source; failed or incomplete sources retain full delivery.
Containers emptied solely by removing reusable fields do not force those fields
back into the delta. Originally empty unknown records and non-string source
references remain literal current input. Reuse keeps the locator's exact value;
it neither rereads the referenced material nor refreshes its authority.

The registered preference owner permits exact reuse of its complete instructions,
statements, source quotes and source references. Each read still supplies the
current revision, membership, keys, lifecycle state, expiry, operation lineage
and unknown fields literally; an empty view replaces earlier preferences.
The existing scope-less preference hook uses the validated capture's exact
Goal/Agent binding, without adding fields to ordinary/disabled producer output
or parsing its read command. Explore retains its explicit scoped hook identity.
Unknown, failed, incompatible or ambiguous hook views stay complete. A changed
scope or lost baseline still selects full delivery; reuse never makes a retired
or expired preference actionable or grants authority.

Quota capability candidates use the same exact codec for registered Todo text,
title and note strings. Every candidate remains in the complete cold input;
warm input still carries current membership/order, identity, revision, ownership,
eligibility, handoff and unknown fields. Unknown schemas, duplicate identities or
structured values in those text fields stay literal. This removes repeated
authoring only when a complete baseline is usable; it neither narrows candidate
selection nor implements the proposed decision-stage read contract.

Object edits may group complete current fields in `values`, alongside recursive
`changed` edits and `removed` keys. Array edits use exact indices in `items` and
`values`, with `length` for resizing. These literals are present in the current
input; grouping saves repeated wrappers rather than omitting controls. The
codec compares this encoding with ordinary edits and full replacement before
choosing. A reader that does not recognize the edit recovers the full capture.

Eligible reusable strings, arrays and objects may also use `from`, an array of
object keys or exact array indices in the immutable base capture. This avoids
resending identical authoring and evidence records when task binding or source
ordering moves them to another field. The reference copies the complete exact
value, never a similar value or one from the current patch. A subtree containing
any field required as current input cannot use a bare whole-subtree reference.
It may instead pair `from` with `edit`: copy the exact base value, then apply
the edit to that copy. Every current and unknown control still appears literally
in the edit. Nested `from` paths remain rooted in the original immutable base,
never the edited copy. This avoids repeating a long reference path for each
unchanged requirement when an admitted reader relocates a complete source.
The codec chooses this overlay only when smaller than the ordinary recursive
edit or complete current literal; its instructions appear only when used.
Readers validate the exact current change tree and complete reconstructed digest;
removing a required current override therefore recovers the current full capture.
Older readers recover the same full capture for an unsupported overlay.
Current and unknown controls remain inline. Older string-only readers recover
the current full capture for an unsupported structured reference. Missing,
malformed or incompatible references recover the same full current capture;
the reconstructed context must still match its complete digest. Cold delivery,
session invalidation and authority remain unchanged. This saves transport only;
it does not establish model retention, billed-token savings or useful outcomes.

These are presentation rules within the existing native option, not a new
configuration or admission policy. A missing continuity basis, a changed epoch, a damaged
patch or an incompatible reader automatically recovers the same original full
capture. Recovery never reruns the guard, asks the owner, spends quota or grants
execution. Wire reduction alone does not establish actual model costs or outcomes.

The retained-input rules above apply to explicit confirmed-input clients. Native
Codex delivery instead compares verified saved files (see below); the model may
read changes and necessary facts progressively, without importing the full base.
Compaction starts a new baseline epoch for the next work Turn, without invalidating
the existing Turn or its recovery files.

### Machine default

**Settings → This machine → Capabilities → Progressive context** exposes progressive
reads (`mode`) and cross-Turn reuse (`delta_delivery`). It uses the same
revision-locked preview, apply, readback, disable and rollback owner. Read-only
editors cannot save; sibling settings and Goal records remain unchanged. No choice
installs hooks, trusts a host or proves actual delivery or model retention.

The editor saves `context_delivery_machine_defaults_v1` with both choices.
For example, `{"schema_version":"context_delivery_machine_defaults_v1","mode":"off","delta_delivery":"auto"}`
allows a supported host to compare complete documents without progressive presentation.
Existing v0 rows with `mode=off` retain their original full delivery, including rows
that stored an implicit `delta_delivery=auto`. Editing either switch adopts v1.
`machine-config preview/apply --namespace context_delivery --config-json FILE`
requires the original preview revision. Removing the namespace restores progressive
reads, supported-host Delta AUTO and Antigravity manager history reads. Save the
relevant off setting to keep it disabled. Manager history is delivered automatically. A trusted-owner native manager can read nonempty delivery history from a private snapshot using its permitted shell. Current facts stay inline. Existing explicit legacy `antigravity_mode=off` preferences remain effective.

The CLI facade `--context-delivery auto` (also the omitted default) presents
current requirements before expandable material across hosts. Stateless CLI,
other declared hosts and recovery do not need native setup. A plain read-only
`should-run` stays read-only: capture files do not create a Turn, receipt, session,
journal or execution lease. `--delta-delivery off` independently disables Delta
while retaining progressive reads. Delta AUTO only uses supported declared
continuity hosts. A lone `--context-delivery off` or inherited `LOOPX_CONTEXT_DELIVERY=off` keeps
the original complete path. Explicit `--context-delivery off --delta-delivery auto`
requests cross-Turn reuse of the complete document when continuity is available. Environment
on alone supplies no host, retention or execution proof. Private capture failure
retains the complete current input rather than using an older capture.
New benchmark heartbeat trials default to `context_delivery=on`; pass `off` to
disable. Other benchmark modes remain off. Workers pin the resolved trial choice
in preflight and solver subprocesses; existing saved/frozen trials must not be
rewritten or reused to qualify the new default.

If a supported declared Codex caller has not initialized native continuity yet
(including a missing native session id),
the guard still delivers its complete current capture through the file reader.
It does not create a substitute session, journal, epoch or retained baseline;
progressive selections remain available from that same current source. Native
reuse starts only through the existing session/lifecycle contract. Missing
session identity does not excuse unsafe context directories: their private-file
checks still fail, preserving the original decision and receipt for recovery
without another admission. Explicit off retains ordinary inline delivery.

Machine consumers that parse `should_run`, selected work or settlement identities
must explicitly request raw output on internal `quota should-run` calls using
`--context-delivery off` or the subprocess `LOOPX_CONTEXT_DELIVERY=off` override,
and retain the exact registry, Goal, Agent and Turn. Enabled native output is a
presentation/capture envelope, not a raw authority decision; a missing
`should_run` in that envelope is not a denial. MCP settlement and Claude status
readers, external scheduler workers and scheduler-hint readers use the existing
`LOOPX_CONTEXT_DELIVERY=off` subprocess override, which
also preserves compatibility with older CLI parsers. This does not change the
machine preference or disable differential context for the interactive Agent.

机器消费者若解析 `should_run`、选中工作或结算身份，应在内部 `quota should-run`
调用中通过 `--context-delivery off` 或子进程 `LOOPX_CONTEXT_DELIVERY=off`
显式请求原始输出，并保留原 registry、Goal、Agent 和 Turn。
开启差分后的原生输出是展示／快照封装；封装没有 `should_run` 不表示准入拒绝。
MCP 结算、Claude 状态读取、外部调度 worker 和调度提示读取在子进程内设置已有的 `LOOPX_CONTEXT_DELIVERY=off`，
兼容旧版 CLI 解析器；不修改机器开关，也不关闭交互
Agent 的差分上下文。

Legacy explicit on with Delta AUTO accepts the declared native scenarios: Codex App heartbeat (`--codex-app` or its equivalent complete scheduler
declaration), Codex App SSH Goal (`--runtime-profile codex_app_ssh_goal`), and the existing Codex benchmark heartbeat worker (`generic_cli`
plus its explicit heartbeat worker environment). Missing, contradictory or
unsupported Codex declarations still return an error before admission. Other hosts
use the auto facade for stateless progressive reads. Declarations select
an adapter; they do not authenticate a host or prove retained input.
Native continuity currently requires POSIX; other platforms reject on explicitly.

Both machine-inherited and explicitly enabled `heartbeat-prompt`, `quota should-run` and native context
commands share a private `context-delivery` directory beneath the resolved
machine runtime, with independent journals per native session. A missing leaf is
created with `0700`; unsafe existing leaves are rejected without chmod or
symlink traversal. Explicit on uses the same automatic location when none is supplied;
caller-supplied locations remain supported. A handoff socket keeps its Host-owned
projection path and does not acquire a second native journal.
Unrelated quota commands and digest-bound recovery pages do not activate this
setting. Ordinary machine validation still applies to business operations.

Native `quota should-run` and saved `context-project` persist the complete
capture and return a small file reference, not an eager first page. The response
names the delta/full `path`, digest and reader `command`; `recovery` names the
current complete file. A file delta additionally carries `baseline_reference`.
`index_command` lists current fields and `select_command` batches exact pointers.
Under a smaller explicit ceiling, these shortcut commands may be omitted;
append `--list` or `--select <pointer>` to `recovery.command` instead. Both
operations address the current complete capture, including during delta delivery.
The reference stays below 7900 serialized bytes (or the explicit smaller output
ceiling); if metadata cannot fit, recovery retains the current capture. Reader
pages remain independently bounded. A complete raw tool log is not proof that
the Host retained the same bytes in model history.

In a native progressive session, the initial read can reference a previously
delivered preferences block or acceptance outcome. The first page lists those
pointers in `previously_delivered_fields`. Their requirements still apply.
Use `recovery.command --select <pointer>` for the current body when needed.
Reuse requires complete observed tool delivery in the same task, session and
epoch, and a matching digest of the current visible field. A revised field
appears in full. Losing the observation restores the ordinary initial read.
A saved delta on its own provides no evidence of model retention.

A ready native Turn can use the same observation for an unchanged
`vision_authoring` body already delivered in this task and epoch. The initial
read marks its exact current address and still delivers this Turn's
precondition, command and settlement identity. If the full instructions are
unavailable, select the current complete body before composing the Vision.
Explicit selection and stateless readers keep the literal body.

中文：同一任务、同一原生上下文周期内，首读可以标记已完整交付且未变化的
`vision_authoring`。本轮前提、命令和结算身份仍读取当前值。如果无法使用完整
说明，在编写 Vision 前从本次捕获取回该字段。显式选择和无会话读取保留原文。

Each progressive `context_details.source_path` names this Turn's complete immutable
`projection.json`, including when a warm wire omits unchanged material. The current
`unread_context_index` preserves unobserved or changed supporting pointers and
conservative partially read source blocks. It is navigation, not a full-read gate;
select the facts required for the current decision from that current source.

The private native journal records `consumed_pointers` and `confirmed_read_sections`
only after byte-verified, contiguous complete delivery of a selected view, or the
complete native input's actually included sections. Digests remain in the journal;
they are not repeated in model-facing index rows. A list, partial page, EOF without
earlier coverage, failed/damaged output, subagent hook or unrelated capture cannot
acknowledge a section. Selected reads never establish a full retained-input baseline.
The native hook still verifies each page's bytes. It skips computing section
digests when the terminal range is absent from both the batch and the journal.
Changed content remains unread; compaction or a different Goal/Agent/scope retires
the old epoch's observations. Stateless hosts make no such observation claim and
keep the complete current source available. These observations do not establish
model comprehension, later retention, execution authority or billed-token savings.

After the shared TypeScript codec accepts a delta, the native output adapter
compares its final reference plus a complete explicit-file read against the
same current full capture. The comparison includes compact JSON framing,
UTF-8 escaping, first-page guidance, continuation commands and the active page
budgets, while respecting the reader's existing whole-capture size limit.
A tie, a cheaper full delivery or an unreadable delta under that budget
uses the existing full capture when it is readable. Only the chosen view is
registered; a full fallback keeps the same target and recovery digest and
starts with zero delta ancestry. No additional projection or admission is run.
This is a serialized-output-byte rule for one complete delivery, not a
requirement to read everything or a prediction of token billing. Selective
reads, baseline discovery, later recovery and model-retained history remain
separate costs and decisions.

File references carry the original path, digest and budgeted reader command;
the outer view supplies navigation. The first bounded full-file page supplies
the complete paging and truncation-recovery instructions, including when read
directly by capture/hash in a fresh shell. Selected pages keep their scoped
read instructions. Historical unbounded page responses retain their wire shape.

The native codec's `captured_file` basis verifies the previous full file and
compares exact versions, including unchanged control fields. It does not depend
on a full read, page EOF, a model ACK or a retained-input claim. Goal, Agent,
session, surface, projection version and configuration generation remain scoped;
the context epoch is also bound. After compaction the next work Turn starts a new
full saved baseline; later Turns may use file deltas without a full model read.
Missing, changed, unsafe or incompatible baselines fall back to the current
full-file reference. Integers outside JavaScript's exact range and changes to
integral-float source values (including type or signed-zero changes) use that
same fallback: the bridge cannot preserve all their source distinctions.
Unchanged numeric values stay inherited in place; the codec avoids replacing
their parents or relocating normalized numeric subtrees. The original values remain
available through the same progressive reader, without a full-read obligation.
Each base is a full saved version,
so selective reading never creates a requirement to replay a chain of deltas.

The model chooses the changes and details its current decision needs. It must
understand applicable Goal intent, acceptance criteria, constraints and stops
before dependent work, and the relevant operation/settlement contract before
effects. Supporting evidence is read when verification needs it. There is no
prerequisite to drain the entire packet, baseline or delta. After compaction,
recover only missing necessary facts from the same current capture; preserve
Turn, task and settlement identity and do not re-admit or repeat effects. A file
reference grants neither authority nor freshness. Selected/index reads never
establish full model retention; complete-page observations retain that separate
meaning for explicit retained-input consumers.

For native setup, `quota context-config --codex-app --goal-id GOAL --agent-id AGENT`
under machine on prepares only that private transport leaf and renders the
existing hooks, recovery instructions and `config_overrides`. Applying those overrides
selects a 20000-token native history limit and declares the existing 64000-byte
output profile. The initial guard returns the small file manifest; requested reader pages use that budget,
including when the shell lacks the declaration. The worker applies this configuration
automatically inside its isolated opt-in trial. The persistent instruction requests
22000 tokens for each context-read tool result and 24000 for its code-mode emission,
forwarding one stdout at a time. The post-compaction/recovery hook carries the same
per-call budget guidance so recovery does not depend on retaining the setup text.
Both the nested reader and outer code-mode emission can truncate independently;
per-call requests do not prove retained history or change host configuration.
If either layer truncates, retry the affected range of the same capture with
smaller content and complete-output byte budgets. This avoids a transport retry
failure; it does not make an oversized total context fit or qualify scoped reads.
Remove the generated output overrides together with the
context setup on rollback; saving a machine preference alone does not apply them.
This output sizing does not prove retained input, lower fees or task benefit.
For an SSH Goal, replace `--codex-app`
with `--runtime-profile codex_app_ssh_goal` in setup and the thin/bootstrap prompt.
Its generated guard keeps the native Goal identity and visible-Goal settlement;
it does not become a hosted heartbeat. It does not install or trust hooks,
change host configuration or restart the running App. Configure and trust its
exact hooks for the next native session. Existing loaded threads may retain
their earlier hook configuration despite enabled/trusted `hooks/list` readback;
discovery does not deliver `SessionStart` or initialize a context journal. Use
the host's start/resume path after active work reaches its normal safe boundary,
then verify lifecycle delivery. Keep full delivery and the original capture
until then; do not fabricate events, borrow another session's journal or restart
an active host to manufacture adoption. The native path preserves authentication,
endpoints, provider selection and connection mode, including implicit history via
`previous_response_id`. It neither enables input diagnostics nor scans model-input
history. Benchmark preparation stages those same hooks in its isolated trial home.

`PostToolUse` can record complete digest-bound reader ranges in the existing session
journal for the separate model-retention observation. EOF without preceding pages,
a known malformed/truncated page or a reader error cannot establish that observation. Concurrent and repeated pages coalesce.
`PreCompact`, `PostCompact`, clear, fork and a changed session/workspace invalidate
that observation and rotate the file epoch for the next work Turn. A resume may supply different history under the same session ID; the
[native event contract](https://learn.chatgpt.com/docs/hooks#sessionstart) alone
does not attest retention. The shared Codex adapter can qualify a resume against
the complete read's native replay checkpoint: the native-selected physical
rollout must retain the digest-bound interval beginning at the current baseline's
first reader and every observed reader output,
including complete byte coverage of the pinned capture, with
matching session, workspace, model and permission mode, and no intervening
compaction, rollback, replacement or unknown replay record. This bounded IO
profile is qualified on Codex 0.162.1 and 0.167.0-alpha.3.2 paginated rollouts. It streams only the
baseline's reader interval and subsequent records; unrelated earlier history is
neither hashed nor decoded, and total file size does not decide reuse. The
session metadata's creator version is not the running host version. Code-mode
parent emissions must retain the same complete page bytes observed by nested
reader hooks. Several nested reads may share one parent call; their individual
emission digests and full byte coverage remain required. Missing or changed evidence and unqualified replay records fall
back to full. One decoded record remains bounded; this is not a history-size
limit. Legacy whole-prefix checkpoints require a fresh complete read. It retains the
normal-read engineering assumption rather than certifying future model input;
it neither scans model requests nor asks the model for an ACK.
For a complete one-page selection from the current capture, the adapter can
verify that exact selected output across an ordinary resume. The source and
read scope must still match. Only the replayed literal sections remain
eligible for reuse in the next progressive view. Partial or inherited
selections still need their ordinary recovery, and a selected read cannot
establish a whole-input baseline.
Opted-in benchmark native children create owner-only files automatically; the
parent process and off-mode file creation policy stay unchanged. Group-writable
existing replay sources cannot establish cross-process continuity.
A new independent decision uses its normal guarded context command. Actual
compaction selects a new full saved baseline; ordinary resume preserves the file
epoch without claiming model retention. A previously qualified replay checkpoint
that now fails may conceal compaction and conservatively rotates the epoch.
No prior complete-read checkpoint is required for file comparison. Continuing a
captured decision after loss recovers only the necessary missing facts from that
same original, with no new guard or spend. Ordinary tools and Turn endings do
not invalidate file comparison. Repeated reset events are idempotent.
The adapter commits the current complete capture reference and pending delivery
under the existing session lock before optional material planning or encoding.
A process exit during planning therefore recovers the current source rather
than its predecessor. The planner reuses that archive. A saved file can support
file comparison without implying that its contents were read by the model.


This deliberately replaces per-request complete-input proof with lifecycle
assumptions. Downstream output loss or truncation not exposed to the hook, a
missed invalidation, or hooks silently disabled outside the declared setting may
escape detection. These limits are not certified retention and do not add an
approval gate. Explicit off keeps ordinary full delivery. A journal read, lifecycle
reduction or write failure after acquiring its lock retires that journal before
transient IO recovery can revive the old baseline. The immutable capture and its
already-delivered explicit recovery reference remain available; if the journal
could not be read, session lookup is unavailable. Failure to reach or retire the
journal at all remains part of the missed-hook limitation above.
Generated machine-inherited hooks reload the same machine runtime, so off
or removal stops their journal writes. Explicit manual on options remain explicit.
Replacing the machine configuration invalidates reuse, including off/on round
trips with identical final contents. Unrelated configuration edits may therefore
cause one conservative full delivery.

Generate a persistent entrypoint with `heartbeat-prompt --bootstrap`. It retains
caller-supplied options, including an explicit off, but never freezes resolved
machine defaults or generated private paths. Existing v2 loaders reload the
setting on each wake; old expanded prompts need regeneration. This setting does
not restart stopped Goals, alter quota/leases or qualify installed Desktop,
all-Goal adoption, actual solver costs or outcome benefit.

## 中文

任务示例、受支持设置、恢复与验收方案见[上下文交付指南](../guides/context-delivery.zh-CN.md)。
当前交付语义仍由本 reference 定义。

本页规定当前交付行为。[统一契约提案](../architecture/rfcs/controller-executor-unified-contract-v0.zh-CN.md)
拟让所有入口复用 full/delta/detail/recovery 流程。controller 编写的工作说明必须
保留完整原始要求与可访问证据；实验性模型 token 目标不允许截断，也不替代来源读取义务。
提议的共享组合边界在 capture 前保留来源 revision 与未完成必读义务。从该不可变 capture
恢复交付不刷新权限；合法的新选择/权限检查与传输重试仍是不同操作。

普通 Heartbeat/quota 默认在 `agent_channel.work_context` 返回完整选定 Todo、
启用的验收与当前 Agent 相关的 User Todo，附带来源和修订，不需要开启 TurnEnvelope。
普通 CLI 在同一响应内共享完全相同的验收副本。已有引用跟随本次投影迁移的完整
内联 owner，包括后访问的 owner；不同修订、类型、失败和来源分别保留。不增加来源
读取，不改变权限、准入或结算；已存 capture 保留原始内容。
Vision audit 和 replan authoring 副本复用该本地序列化身份。原始 JSON 编码不同的
布尔／数值、整数／整值浮点表示及带符号零分别保留；仅键顺序不同仍可共享。不增加
模型补读或 runtime 往返；不同来源事实保留完整指引。
Goal Markdown 复用原 writer 对生成区域的严格归属规则：所有已识别 Todo 区域均有
显式标记时，`work_context.sources` 原样携带完整非生成正文，保留目标、frontmatter、
任意章节、注释、历史和末尾停止条件。当前 writer 和旧版无 Goal 参数的 writer 生成的
独立整行 SQLite 归档索引即使位于已标记区域之外，也按 archive 区间记录。原文件修订
覆盖这些行，归档及省略任务由 canonical Todo 查询读取。外 Goal 标记、未知格式及
代码块中的示例仍保留在正文。`excluded_todo_regions` 保留每段的角色、行区间
和 SHA-256，`revision` 仍覆盖完整原文件；任务要求与权限由 canonical 精确 Todo 和
scoped User 读取保留。默认 Goal 必读对象改为完整非生成正文，不摘要要求、不删除历史，
不重写原文件或权威记录。未标记或混合归属仍须渐进式读取全文；错误标记、标记内非生成
内容、来源缺失及非法 UTF-8 均拒绝。不能按标题猜测或为预算截断正文；依赖交付前须读取
全部已提供上下文与剩余必读，来源改变后重新准入，局部验收不能替代整体目标。
完整的历史 Monitor 回执行也属于生成数据。回执 codec 核对精确标记、schema、Goal／
operation 身份及编码外层，`excluded_monitor_receipts` 保留 `count` 和从 1 开始、
首尾均包含的 `line_ranges`，合并相邻行。父级 `state_file` 与完整文件 `revision`
绑定所有区间，不重复逐行哈希。旧捕获的逐行格式仍可读；数量不符、乱序、重叠或与
Todo 区域相交的区间均拒绝。
编码正文仍留在原文件供原操作恢复；当前工作和义务仍由 canonical Todo 读取提供。
普通注释、代码块中的示例、损坏或未知回执、其他 Goal 的回执和附带其他文字的行均原样
保留。这样首轮全文读取不再携带累积的恢复日志；不改变回执重放校验，也不据此宣称
模型输入或 token 成本已经降低。
显式开启的 native context delivery 在捕获前请求完整 Goal。未标记或混合归属的来源
由原 registered-source adapter 提供全部原始 UTF-8 正文、完整修订与 `full_source: true`。
既有 TypeScript interaction owner 核对正文 digest 后才履行读取；`complete_goal_v1`
确认 runtime 支持，防止旧版静默保留未完成 Goal 读取。来源缺失或无效仍拒绝；已有标记
来源保留生成区域血缘和 canonical Todo 权限。

native 差分与来源履行共享同一完整性判断；只有精确核验的 Goal 正文可从保留的
baseline 继承，当前来源凭据和未知字段仍明文提供。冷态及上下文丢失恢复保留完整
原文，包括任意章节、注释和末尾停止条件。不据此证明模型保留、执行权限、token 节省
或已验收成果收益。

Checkpoint Host 预检在显式开启 native JSON `should-run` 捕获时使用隐藏参数
`--defer-context-presentation`。它为已准入工作上下文读取捕获完整 Goal，并保存普通
不可变 CLI 视图，不读取或替换模型会话的连续性状态。原生生命周期 hook 运行后，模型
通过 `context-project` 呈现同一视图，不重复准入。延迟呈现必须指定捕获位置，不能
与 socket handoff 组合。普通调用保持既有呈现；关闭差分时保留原来的待完成 Goal
读取。未绑定任务的分阶段重规划继续沿用 review 所属的原始 Goal 读取。

Host 领取 Todo 后，就绪检查使用内部 `--prepared-capture-reference` 路径。它保存完整决策，
只返回写入时的摘要和核对同一 Todo、Turn 所需的字段。hook 打开原文件时校验该摘要；
若文件已被替换，回执保留原领取记录，恢复原始内容后才能继续工作。

Goal adapter 仍完整读取文件、校验 UTF-8，并以全部字节计算修订摘要；关闭 native
context delivery 时，未标记或混合归属仅在内部桥接传路径与修订，避免未被消费的正文
副本，Agent 仍须渐进式全文读取。显式
归属的来源才向 typed owner 传完整非生成正文和区域血缘。字节减少不能证明模型费用、
延迟或任务效果改善。
已附带的内容不要求重复读取；未提供的 provider 内容继续保留精确命令与顺序。
`work_context.complete` 只表示未发现来源失败，未完成的必读义务仍然有效。
偏好只在对应作用域有记录时出现；Explore 和偏好沿用各自 hook 的真实 adapter，
读取范围由 capability owner 决定。来源失败保留未完成必读项并阻止依赖交付，恢复后
重新准入；User gate 仍生效。大小告警不截断验收或移除剩余必读项。
已有 receipt 绑定的 Todo 在语义重规划中保留原 Task 身份；只有未绑定的备选任务才让位于
原无 Todo 规划义务，该义务必须与签名中的 Goal、Agent 和 Turn 一致。两条路径都不会
在普通交付被禁止时授权执行业务工作。

已选定工作时，验收 adapter 复用 canonical owner 的精确 Todo 读取，顶层 `tasks`
只携带该工作的关联观察；完整 Goal 合同、已配置 scope、检查、受阻工作和累计 outcome
全部保留，包括其他 owner 的要求。选定 Todo 的完整正文与声明仍由独立来源携带。
没有选定工作的读取及显式 `goal-acceptance inspect` 保留完整列表；正文减少不等于
验收、执行权限或已经测得模型收益。

`todo list --todo-id …` 默认只返回一份完整 `todo.text`，保留来源、修订、身份、
状态和关系。精确响应移除列表及角色视图，不保留兼容开关；`--thin` 只用于概览。
缺失/过滤返回未匹配，歧义/来源故障报错，不退回旧摘要。读到记录不等于获得执行
权限；阻塞、已完成、归档记录仍只是观察。需求改变须重新准入。

普通 JSON CLI 列表保留完整的选定 `todos` 数组；角色视图内完全相同的重复记录
改为 `{"$ref":"#/todos/0"}` 等响应内指针，沿指针读取原记录。显式 `--limit`
之外的视图记录仍保留首份正文，后续副本引用它；同 ID 但事实不同的投影不会合并。
计数、顺序、来源、User 义务及恢复/claim/lease 条件保持原义，不增加执行权限。
原生决策/前端读模型、Markdown、thin 和精确详情保持既有结构。没有兼容开关；
原有摘要仍不能替代精确全文和 fresh guard。正文减少不等于模型采用、整轮加速或
任务效果改善。

安装后重新生成旧展开式 heartbeat 提示；此 PR 不热更新 runtime/automation，
也不证明模型已采用或获得质量收益。回滚需恢复旧版本并重新生成提示。

既有原生 context delivery 由同一 TypeScript codec 处理普通 quota 与
TurnEnvelope。在基线连续性成立的口径内，两入口仍逐轮发送 `planning_horizon.core_goal` 的
原始目标、来源和可用性，包括目标改变或缺失。完整 `acceptance_contract` 与已登记
规划树按精确复用、子树变化或全量交付；累计验收树不等于原始目标句。未知 horizon
字段仍作为本轮输入。quota 通道与 Turn 契约摘要复用完整静态收口指导，当前收口判断、
条件、身份、命令与未知控制仍逐轮发送。Explore owner 登记分支证据审计与证据关联
模板的精确复用；分支身份、选择、状态、恢复事实与编排门仍是当前输入。重排、反证、
移除与指导变更完整重建当前来源，不规范化时间戳、修订或正文。
已成功完整读取的累计 Goal outcome 中，字符串来源引用、来源说明、复核笔记及字符串证据引用也按精确
差分传输。它们是来源与解释；要求状态、复核结论、依据是否当前、独立验证标记、修订
及未知字段仍逐轮完整发送。证据变化、移除或重排后仍须精确还原当前来源；来源失败或
读取不完整时保留完整投递。
仅因移除可复用字段而变空的容器，不会把这些字段重新带回差分；原本为空的未知对象和
非字符串来源引用仍逐轮发送。复用保留定位符的精确值，不重读其指向的材料，也不刷新权限。
quota 能力候选沿用同一精确 codec 复用已登记 Todo 的正文、标题与笔记字符串。
冷启动仍完整交付全部候选；warm 仍逐轮携带当前成员与顺序、身份、修订、归属、
准入能力、交接及未知字段。未知 schema、重复身份或文本字段中的结构化值保持原文。
这只在完整基线可用时减少重复正文，不缩小候选选择范围，也不等于按决策阶段读取的
替代契约已经实现。
对象编辑可以在 `values` 中合并携带本轮完整字段，同时保留递归 `changed` 与删除
`removed`；数组的 `items` 与 `values` 使用精确索引，`length` 调整长度。
字段仍在本轮输入中，只减少重复 wrapper；codec 比较普通编辑、合并字段
与完整替换后选择，无法识别该编辑的读者恢复完整 capture。
可复用的字符串、数组和对象还可用 `from` 引用不可变基线中的精确键或数组索引，
避免绑定任务或来源重排后重传相同的指导与证据记录。它复制完整相同的值，不读取
当前 patch，也不模糊匹配。含有必须逐轮发送字段的子树不能整体引用；引用只有比
完整值及递归编辑更小时才采用。当前和未知控制仍完整发送；只支持字符串引用的
旧读者遇到结构化引用时恢复当前完整 capture。引用缺失、损坏或不兼容仍按同一
捕获恢复，完整重建摘要须匹配。冷启动、连续性失效、权限与结算语义保持。
这些只改变既有选项内的展示，不新增配置或准入。缺少可用连续性依据、epoch 改变、patch 损坏
或读者不兼容时，自动恢复同一原始 full capture，不重跑 guard、不问询、不扣额，
也不授予执行权限。传输减少本身不证明实际模型费用或结果改善。

上面的保留输入规则适用于显式 confirmed-input 客户端。原生 Codex 改用校验后的
文件版本比较：模型可渐进式读取变化和必要事实，无须把完整基线装入上下文。
压缩后下一个新 Turn 建立新的完整文件基线，不使已有 Turn 或恢复文件失效。

### 机器级默认

在**设置 → 本机设置 → 能力中心 → 渐进上下文**分别设置渐进读取（`mode`）、独立的跨轮差分
（`delta_delivery`），沿用现有精确修订的预览、
应用、读回、关闭和回滚 owner。只读编辑器不能保存；不改写其他配置和 Goal，不安装／信任 hook，
保存偏好不证明原生初始化、实际投递或模型保留。

设置页以 `context_delivery_machine_defaults_v1` 同时保存两个选择。
例如 `{"schema_version":"context_delivery_machine_defaults_v1","mode":"off","delta_delivery":"auto"}`
让支持的宿主比较完整文档，同时关闭渐进呈现。旧 v0 配置的 `mode=off` 保留原完整交付，
包括曾隐式存入 `delta_delivery=auto` 的记录。编辑任一开关才采用 v1。
CLI 的 `machine-config preview/apply --namespace context_delivery --config-json FILE` 仍需原预览修订。
移除 namespace 恢复渐进读取、支持宿主的 Delta AUTO 与 Antigravity 管家历史读取；要保持关闭应保存对应 off。
管家自动选择交付历史的读取方式。有权限读取私有快照的原生管家可按需读取已有历史，当前目标与待办仍直接可见。既有显式 `antigravity_mode=off` 偏好继续生效。

CLI 的 `--context-delivery auto`（省略时也使用此门面）跨宿主先呈现当前要求，再按需展开材料。
普通 CLI、其他已声明宿主及恢复读取不要求原生初始化；只读 `should-run` 仍不创建 Turn、回执、会话、journal 或执行 lease。
`--delta-delivery off` 可独立关闭跨轮差分而保留渐进读取；Delta AUTO 只使用支持连续性的已声明宿主。
单独指定 `--context-delivery off` 或继承 `LOOPX_CONTEXT_DELIVERY=off` 保留原完整交付路径。
同时指定 `--context-delivery off --delta-delivery auto` 时，有连续性依据的宿主可以复用完整文档的跨轮差分。
环境 on 不证明宿主、保留或执行权限。私有捕获失败时保留本次完整输入，不借用旧捕获。
新 benchmark heartbeat trial 默认 `context_delivery=on`，指定 `off` 可关闭；其他跑法仍关闭。
worker 把本次运行已解析的选择固定传给预检和 solver 子进程，不改写调用方原设置，
不修改或复用旧的在途／冻结 trial 来宣称新默认合格。
兼容的显式 on 与 Delta AUTO 接受以下原生声明：Codex App 心跳（`--codex-app` 或等价完整调度声明）、
Codex App SSH Goal（`--runtime-profile codex_app_ssh_goal`），以及现有 Codex benchmark 心跳 worker（`generic_cli` 与其显式心跳环境）。缺失、冲突、
不支持的 Codex 声明仍在准入前报错；其他宿主通过 auto 门面使用无状态渐进读取。声明只选择 adapter，
不证明宿主身份或上下文保留。
当前原生连续性依赖 POSIX；其他平台显式拒绝开启。

每轮 `context_details.source_path` 都指向当轮完整且不可变的 `projection.json`，暖差分也可通过
该文件选读未变化但此前未读的内容。`unread_context_index` 保留尚未观测到完整读取或已经变化的
支持材料指针，以及保守标记的部分未读来源块；它是导航，不是要求读完整包的 gate。
仅在当前决策依赖相关事实前选读必要部分。

私有原生 journal 的 `consumed_pointers` 与 `confirmed_read_sections` 只记录经过字节核对、
连续完整交付的选读视图，或完整原生输入中实际包含的部分；摘要留在 journal，不反复展示给模型。
目录、部分页、仅 EOF、失败／篡改输出、子 Agent hook、无关捕获都不构成读取确认；选读不建立全量
模型保留基线。
原生 hook 仍逐页核对字节；当本批和 journal 都没有末尾范围时，跳过正文字段的摘要计算。
内容变化继续标为未读；压缩、Goal／Agent／scope 变化使旧 epoch 观测失效。
无状态宿主不声称观测确认，完整当前来源始终可达；这些记录不证明理解、后续保留、执行权限或计费下降。

受支持的 Codex 声明尚未完成原生连续性初始化时（包括尚未提供会话 id），
guard 仍通过文件 reader 交付本次完整捕获，
可以从同一当前来源渐进选读；不会制造替代会话、journal、epoch 或已保留基线。
原生复用仍须由既有会话与生命周期契约建立。缺少会话标识不豁免私有目录检查：
不安全目录仍失败，保留原决策与回执用于恢复，不重新准入。显式关闭仍完整内联交付。

机器继承与显式开启的入口共用已解析 machine runtime 下的私有 `context-delivery` 目录，并按原生
会话隔离 journal。缺失末级目录以 `0700` 创建；不跟随末级符号链接、不修改不安全目录
权限。显式 on 未指定路径时也自动选择目录，仍支持手工覆盖；handoff socket 保留
原有 Host 投影路径，不另设原生 journal。无关 quota 命令和带原始摘要的恢复分页不激活此
设置；业务操作仍须通过原有设备配置校验。

机器开启后，`quota context-config --codex-app --goal-id GOAL --agent-id AGENT` 只准备
私有传输末级目录并生成已有 hook 和恢复指令，不安装或信任 hook，也不更改宿主配置。
SSH Goal 使用 `--runtime-profile codex_app_ssh_goal` 生成 context-config 和 thin/bootstrap 提示，
沿用可见 Goal 的执行与结算身份；仅启用差分时接入捕获目录，不转换成自动心跳。
原生 `quota should-run` 与 `context-project` 先保存完整捕获，默认返回小型文件入口，
不内联第一页。`path`、摘要与 `command` 指向当前差分或全量；`recovery` 指向当前
完整文件；文件差分另带 `baseline_reference`。`index_command` 展示当前字段目录，
`select_command` 批量选读精确路径。引用输出不超过 7900 序列化字节或调用者显式更小
的上限；较小预算下可省略这两个快捷命令，改在 `recovery.command` 后追加
`--list` 或 `--select <pointer>`，差分投递时也直接读取当前完整捕获。
元数据仍放不下时保留当前捕获恢复入口。读取页另受输出预算约束。
原始工具日志完整，不证明宿主保留给模型的字节也完整。

共享 TypeScript codec 接受差分后，原生输出适配层比较两种交付的最终引用与一次
显式按文件通读的总字节数：候选差分，以及同一当前完整捕获。比较包含紧凑 JSON
封装、UTF-8 转义、首屏读取说明、续读命令及当前分页预算，并遵守 reader 既有的
完整捕获大小上限。全量更小、两者相等，
或差分在该预算下无法读取而全量可读时，复用已有完整捕获。只登记最终选中的视图；
全量回退保留相同 target 和恢复摘要，并将差分 ancestry 归零，不增加投影或准入。
这条规则只比较一次完整交付的序列化输出字节，不要求通读，也不预测计费 token。
选读、基线发现、后续恢复及模型历史保留仍是独立的成本与决策。

文件引用保留原始路径、摘要和遵循预算的读取命令；外层视图提供导航。首个有界全文
分页返回完整的分页与截断恢复说明，直接在新 shell 中按 capture/hash 读取也一样。
选读页仍提供其作用域内的读取说明；历史无界分页响应保留原有 wire 结构。

原生 `captured_file` 基线来自校验后的前一完整文件，可差分复用所有未变字段，包括
控制字段；不依赖通读、EOF、模型 ACK 或保留输入声明。Goal、Agent、会话、surface、
投影版本、配置 generation 和上下文 epoch 仍隔离；压缩后下一个新 Turn 建立完整
文件基线，同周期后续 Turn 再差分，无须模型先通读这份基线。
基线缺失、损坏、不安全或不兼容时回退当前完整文件入口；超出 JavaScript 精确范围的
整数也回退，以保留原值。每个基线都是完整保存版本，无须因选读而重放差分链。

模型自主选择本次决策需要的变化和详情：依赖性工作前理解适用的 Goal 意图、验收标准、
约束及停止条件，执行副作用前理解相关操作/结算契约，验证时按需查证材料。
不要求把整包、基线或差分读完才能工作。压缩后仅从同一当前捕获恢复缺失的必要事实，
保留 Turn、任务和结算身份，不重新准入或重复副作用。引用不授予权限或新鲜度。
目录/选读不构成全量模型保留证明；完整页面观察仍为显式 retained-input 消费者
保留独立含义。现有原生配置的读取预算只约束被选择的页面，不强制通读。

为下次原生会话配置并信任精确 hooks，不重启当前 App。已加载线程可能继续沿用旧 hook
配置；`hooks/list` 的 enabled/trusted 读回不等于向该线程交付 `SessionStart` 或初始化
context journal。当前工作到达正常安全边界后，沿宿主正常启动／恢复入口加载配置，再
核验实际生命周期交付。此前保持全量与原捕获，不伪造事件、借用其他会话 journal 或为
制造采用样本重启活动宿主。原生路径保留认证、端点、provider
和连接方式，包括带 `previous_response_id` 的隐式历史；不启用输入诊断，也不逐轮扫描
模型完整输入。Benchmark 在各自隔离的 trial home 内自动准备同一组 hooks。

`PostToolUse` 可在现有 session journal 中合并摘要固定的完整读取范围，用于独立的
模型保留观察；缺少前页的 EOF、已知格式损坏／截断或读取失败不能建立该观察。
并发、重复页幂等合并。`PreCompact`、`PostCompact`、清空、分叉、会话／工作区切换
使该观察失效，并为下一个新工作 Turn 轮换文件 epoch；同一会话 ID 的 resume 也可能
使用不同历史，[原生事件契约](https://learn.chatgpt.com/docs/hooks#sessionstart)本身不证明保留。
共享 Codex adapter 可核对完整读取时的原生重放检查点：原生实际选择的物理历史文件，
须保留从当前基线首次 reader 开始的摘要固定区间，以及每份精确 reader 输出，完整覆盖固定捕获的全部字节；
会话、工作区、模型和权限模式相同，
中间没有压缩、回滚、替换或未知重放记录。此 IO profile 在 Codex 0.162.1 和 0.167.0-alpha.3.2 的分页
rollout 上验证；流式读取当前基线对应区间及后续记录，不哈希或解码无关旧历史，
文件总大小不决定是否差分。session metadata 的创建版本不等于当前宿主版本。
code-mode 父调用的保留输出须包含嵌套 reader hook 观察到的全部精确页字节。
同一父调用可以包含多次嵌套读取，仍逐份核对其输出摘要和全部字节覆盖。
证据缺失、改写或重放记录不受支持时回退全量；单条记录的解码内存仍有边界，
不是历史文件容量限制。旧版全前缀检查点须重新完整读取后建立新检查点。仍采用正常读取后的
工程假设，不声称证明未来模型输入；不扫描模型请求，也不要求模型 ACK。
开启后的 benchmark 原生子进程自动以仅所有者可访问的权限创建文件，父进程与关闭态
保持原权限策略；既有可组写的重放来源不能建立跨进程连续性。
新的独立决定走正常准入与上下文命令。实际压缩后，下一个新工作 Turn 建立新的完整
文件基线；普通 resume 保留文件 epoch，不声称模型保留。曾合格的重放检查点若核验
失败，可能隐藏压缩，保守地轮换 epoch；文件比较本身不要求先有完整读取检查点。
上下文丢失后继续原决定，只从同一原始捕获恢复缺失的必要事实，不重新准入或扣额。
普通工具和 Turn 结束不会使文件比较失效；重复重置事件幂等。
Adapter 在可选材料规划或编码前，先持既有 session 锁提交当前完整 capture 引用及
pending delivery。规划过程中进程退出后，恢复指向当前来源而非上一份；planner
复用同一 archive。完整落盘可支持文件比较，不表示模型已经读过其内容。

这是以生命周期假设替换逐轮完整输入证明。Hook 未暴露的下游丢失／截断、漏掉失效事件，
或在声明开关之外静默禁用 hooks，仍可能漏检；不把这些假设称为实测保留，也不新增审批门。
显式关闭继续普通全量投递。持锁后的 journal 读取、生命周期归约或写入失败会移除该
journal，防止短暂 IO 故障恢复后复活旧基线。不可变 capture 与先前交付的显式恢复引用
仍可读取；无法读取 journal 时，会话查找不可用。完全无法访问或移除 journal 的情况
仍属于上述 hook 漏报限制。
由机器继承生成的 hook 每次重读同一个 runtime，关闭或移除
配置后停止 journal 写入；手工显式 on 保留其显式优先级。
机器配置被替换后重新全量，包括最终内容相同的 off/on 往返；其他机器配置编辑也可能
触发一次保守的全量投递。

通过 `heartbeat-prompt --bootstrap` 生成持久入口，仅保留调用者显式参数（包括 off），
不固化当前机器默认值或自动生成路径。已有 v2 loader 每次唤醒重读设置；旧展开式提示须
重新生成。此开关不重启停用 Goal、不改变额度或 lease；正常 Desktop、全部 Goal 的实际
采用和真实 solver 完整成本及效果仍须分别验收。

### Selected capture details / 按需读取捕获详情

Enabled native navigation uses `quota context-read --reference-file <file>`.
The immutable, private reader document keeps the full capture digest; selected
continuations also keep the exact selection and its full digest there. The CLI
checks both on every page, including after a later Turn or a changed reader
profile. Its short filename is only a locator, never a truncated integrity check,
permission or freshness claim. Missing, malformed, public or symlinked reader
documents and capture/selection digest mismatches fail closed without admission. Selection reads may publish a derived
private reader document beside the capture; they do not write Goal state.
For new pinned value selections, the private reference can retain the selector's
resolved addresses. The reader and native observation reuse those addresses
against the verified source, then check the rendered selection digest. Older
references and new discovery requests use the existing selector.
Compact selected pages use `context_capture_selection_page_v1`, with a reader
reference and byte cursors instead of repeated hashes or pointer vectors. They
are never full-input observations. Explicit file/hash readers and full-page
observation receipts keep their existing schema and complete digest fields.
The navigation document also retains the complete machine navigation metadata.
Very small output budgets may keep the existing explicit hash reference when
the compact navigation cannot fit; no navigation or source is truncated.

已启用的原生导航通过 `--reference-file` 读取不可变私有引用；完整 capture 摘要、
选读路径与 selection 摘要保存在本地，CLI 每页校验，跨 Turn 或 reader profile
改变也不会混读。短文件名只负责定位，不替代完整校验，也不代表权限或新鲜度。
引用缺失、格式错误、公开可读、为符号链接或摘要不符时拒绝读取，不重新 admission。选读可在
捕获旁生成派生私有引用，不修改 Goal 状态。精简选读页返回引用及字节游标，使用
独立 schema，不能作为完整输入证明；原显式文件/摘要接口及完整页证明保持兼容。
新生成的值选读引用可保存选择器已解析的地址。读页和原生观察使用校验过的原捕获
按地址取值，再校验渲染后的选读摘要。旧引用和新字段发现继续使用现有选择器。
导航引用同时保存完整机器元数据；极小输出预算保留原显式摘要引用，不截断正文或导航。

`quota context-read --select /path --select /another/path` reads an explicit
batch of RFC 6901 JSON pointers from the original digest-bound capture. Supply
the existing `--capture-file` and `--capture-sha256` reference, or start through
the existing session lookup. Omit `--select` for unchanged full-capture reads.
Selection is JSON-only; `~1` escapes `/`, `~0` escapes `~`, and the empty pointer
selects the root. Missing pointers or invalid array indices reject the whole
batch; a present JSON `null` is a valid value. Duplicate pointers are removed
and the remaining pointers are sorted.

Explicit `context-read` capture/hash or native-location arguments are independent
of Goal registry discovery and machine configuration. Conflicting default runtime
roots cannot block that read; capture integrity and native-session scope still
apply. A read without an explicit source keeps its existing machine lookup.
Admission and accounting retain their registry/runtime route checks.

The existing page envelope keeps the original `sha256` and adds
`selected_pointers` and `selection_sha256`. Concatenated `content` is a
`context_capture_selection_v1` object with `selections: [{pointer, value}]`.
Its offsets, total bytes and EOF describe only that derived view. Follow
`next_command`, which retains both hashes and the exact selection; a nonzero
offset requires `--selection-sha256`. The reader verifies both hashes, preserves
original JSON values (including large integers), and performs no admission,
settlement or live Goal lookup. Selection requires the existing typed runtime;
ordinary full reads retain their file-only path.

`quota context-read --list` discovers immediate child pointers, JSON types,
serialized value sizes and object/array counts without returning their bodies.
When pointers are known, skip discovery and batch the needed values with repeated
`--select` in one call. When unknown, select the nearest known parent and add
`--list --list-depth 2` to discover nested fields together. Depth defaults to one
and accepts one through eight; select a deeper parent for further exploration.
This bounds traversal depth, not the capture size or readable content. Prefer a
focused subtree to recursively listing unrelated evidence. Omit selectors for the
root. The generated capture guidance teaches this choice without making an index
read mandatory or changing which requirements must be read before work.
Normal capture replies, persistent native setup and post-compaction recovery
carry the same selection advice. Recovery points directly to `--select`, so a
known pointer does not require another index round trip. Batch within the combined
output budget, avoid overlapping parent/child selections, and inspect a missing
pointer's parent before retrying the rejected batch. These are caller guidelines;
selection validation, required reads and authority checks remain unchanged.
When it fits the existing native-reference output ceiling, `field_index` supplies
the complete top-level index of the current recovery capture. Its pointers,
types, byte sizes and member counts match `context-read --list`; it contains no
field bodies and never proves they were read. Select those pointers through
`recovery.command`; a delta's `command` still addresses its patch. Oversized
indexes are omitted whole, leaving `index_command` and full recovery available.
Selection errors identify the original `--select` argument and, for a failed
lookup, its token position (both 1-based). Sorting and duplicate elimination do
not renumber arguments. Inspect the resolved prefix before that token in the
same capture; malformed pointers need syntax repair first. Diagnostics never
echo the pointer or task values, and no part of a rejected batch is delivered.

Reader failures preserve the runtime's `error_kind` and `diagnostic_code` so
programs need not parse `reason`. Selector rejections use `request_rejected`
with `context_selection_invalid_pointer`, `context_selection_invalid_index`
or `context_selection_missing_pointer`. Repair the pointer syntax or inspect
the known parent in the same capture, then retry the corrected batch. Missing
members and invalid array indexes also return `recovery_command`: it lists the
nearest verified container from the caller's original pointer, using the same
capture digest and page budgets. If traversal reached a scalar, it lists that
scalar's containing object/array instead. This command repeats only the supplied
address prefix, never source values, and starts a new index at offset zero;
it is not continuation of the failed selection or proof of reading its body. Other
runtime failures retain their own classification; startup failures retain
their diagnostic code. These failures do not require restoring a healthy
capture. Missing or damaged capture files still fail closed through the
original file/digest checks. Reading again never reruns admission.

The `context_capture_index_v1` view shares the digest-bound pagination contract.
An expanded index includes its `list_depth`; `next_command` preserves the depth,
selectors and both hashes. Changing depth rejects the prior view's continuation.
An index cursor cannot be used for a value selection. For a truncated or missing
page, recover the affected range of the same capture with a sufficient output
budget; keep the original Turn and effect identities rather than repeating
admission. Indexing never claims that the listed bodies have been read.

Selected/index EOF, including root selection, never proves full model retention.
Native file comparison is independent of that claim. The model can inspect the
current file directly, or consult its baseline and delta, without a full drain.

`quota context-read --select /path --select /another/path` 用 RFC 6901 JSON
pointer 从原始摘要绑定的 capture 批量读取详情。提供已有 `--capture-file` 与
`--capture-sha256`，或从已有会话查询入口开始；省略 `--select` 保持原有全量读取。
选择模式仅支持 JSON：`~1` 表示 `/`，`~0` 表示 `~`，空 pointer 选择根对象。
任一路径不存在或数组索引无效时整批失败；已存在的 `null` 合法。路径去重并排序。

显式提供 capture/hash 或原生会话位置的 `context-read` 不依赖 Goal registry 发现或
机器配置。默认 runtime root 冲突不阻断该读取，原 capture 完整性与原生会话作用域
检查仍生效。未显式指定来源时仍沿原机器配置查找；准入与记账保留原路由检查。

分页保留原始 `sha256`，新增 `selected_pointers` 与 `selection_sha256`。
拼接 `content` 后得到带 `selections: [{pointer, value}]` 的
`context_capture_selection_v1`；偏移、总字节数与 EOF 仅描述此派生视图。
沿 `next_command` 继续，它保留两个摘要及精确路径；非零偏移必须携带
`--selection-sha256`。读取器校验两者，保留包括大整数在内的原 JSON 值，
不准入、不结算、不查询实时 Goal。选择模式依赖已有 typed runtime；普通全量
读取仍只走文件路径。`--list` 列出直接子字段的精确 pointer、类型、序列化字节数
及对象/数组成员数，不返回正文。已知路径时跳过目录查询，一次重复 `--select` 批量
读取所需字段；路径未知时选择最近的已知父节点，用 `--list --list-depth 2` 一次
发现多层字段。深度默认为 1，可选 1–8；需要更深时选择新的父节点继续。这只限制
单次遍历深度，不限制 capture 容量或可读正文。优先展开相关子树，省略路径才从根开始。
生成的读取指引会提示上述选择，不把查目录变成前置步骤，也不改变工作前的必读要求。
在原生引用的现有输出上限允许时，`field_index` 直接提供当前 recovery capture 的完整
顶层目录，其 pointer、类型、字节数和成员数与 `context-read --list` 一致。目录不含
正文，也不证明已阅读；通过 `recovery.command` 选读这些路径，delta 的 `command`
仍指向补丁。过大目录整体省略，保留 `index_command` 与完整恢复路径。
普通 capture 回复、原生持久配置与压缩后恢复携带同一份选择指引。恢复命令直接指向
`--select`，已知路径无须多查一轮目录。按合计输出预算分批，避免同时选择父节点和
子节点；路径缺失时先查其父节点，再重试被拒绝的批次。这些是调用建议，不改变
选择校验、必读要求或权限检查。
选择错误会标明原始 `--select` 参数序号；查找失败时还会标明 token 序号，均从 1
开始。排序和去重不改变参数编号。在同一 capture 中检查失败 token 前已解析的
路径前缀；语法错误需先修正 pointer。错误信息不回显路径或任务正文，被拒绝的
批次不会返回部分内容。

读取失败保留 runtime 的 `error_kind` 与 `diagnostic_code`，程序无需解析 `reason`。
选择错误使用 `request_rejected`，原因码分别为 `context_selection_invalid_pointer`、
`context_selection_invalid_index` 或 `context_selection_missing_pointer`。
修正路径语法或在同一 capture 中检查已知父节点，再重试修正后的批次。字段缺失和
数组索引错误还返回 `recovery_command`：用同一 capture 摘要与分页预算列出原始
输入路径中最近的已验证容器；若已走到标量，则列出包含它的对象或数组。命令仅重复
调用方提供的路径前缀，不包含源正文，并从偏移零开始新目录；它不是失败选择的续读，
也不证明读过正文。其他 runtime
故障保留自身分类；启动故障保留原诊断码。这些故障不要求恢复健康的 capture。
文件缺失或损坏仍由原文件/摘要检查拒绝，重新读取不会重新准入。

`context_capture_index_v1` 沿用双摘要分页；展开的目录包含 `list_depth`，
`next_command` 保留深度、路径和两个摘要。更改深度会拒绝旧视图的续读，目录 cursor
也不能用作正文 cursor。遇到输出截断或缺页，用足够的输出预算恢复同一 capture
的受影响范围，保留原 Turn/操作身份，不重新准入。列出字段不表示读过其正文。
包括根选择在内的目录/详情 EOF 不证明全量模型保留；原生文件比较与此独立。
模型可直接查当前完整文件，也可查基线和差分，无须先通读。

### Bounded settlement results / 有界结算结果

A ready Codex App heartbeat with a native session can publish a combined settlement
plan for its admitted Todo. This includes a claim followed by a ready check of the
same Turn, when the upgraded receipt matches the plan. Execute the displayed
writeback command, which runs the owed quota spend and returns the exact receipts.
After a partial failure, use the
returned `settlement_owed` command for the original Turn. Read the result's
supporting snapshot only when needed. Raw delivery, manual plans and other
profiles retain their original settlement commands.

A captured direct replan can also offer a combined `settlement_command` in its
writeback view. It uses the captured semantic writer: fill the evidence
placeholders after review, run it on the original obligation, and check its
receipts. The original plan and manual steps remain available for recovery.
Do not debit again after a settled receipt. Successor creation and Todo-bound
replans retain their original authoring and settlement routes.

Generated Turn-bound JSON `quota spend-slot` commands include
`--max-page-output-bytes 64000`. This output option does not enable Context
Delivery or create a native continuity baseline. Small results remain complete;
when a result exceeds the budget, its `before` / `after` snapshots move to a
private immutable JSON capture. The other fields, including the actual append
outcome, workspace refusal and settlement receipts, remain visible. Use
`result_details.command` with `--select` for needed diagnostic facts; the reader
keeps the same output budget and verifies the full capture digest on every page.
If the remaining controls also exceed the limit, the returned command selects
all of them: read those pages completely before acting. No control text is cut.

Omitting the option preserves the existing full-result API. A nonpositive
budget is rejected before execution. Platforms without qualified POSIX private
capture validation reject this option before settlement; remove only the output
option to use the same Turn's full-result API. Reader references also fail closed
there; mode bits are not a substitute for Windows ACL verification.
A capture failure after evaluation keeps every original control field (including
`ok`, refusal reason, identity and receipts), omits only `before` / `after`, and
adds `result_delivery_error`; the process exits nonzero. This fallback prioritizes
complete controls over the display budget. A refused operation need not have a
spend receipt. Repair capture privacy or read the same operation without the
output option; a delivery failure does not prove that the effect failed to commit.
Never rerun admission or change the original Turn/effect identity to recover
output. This transport change alone does not prove model retention or savings.

Codex App heartbeat 有原生会话，且当前 Todo 已准入并就绪时，可能收到合并结算计划。
同一 Turn 先领取再复查就绪，升级后的回执与计划匹配时也适用。
执行计划给出的写回命令。该命令会执行尚欠的扣额，并返回原回执。部分失败时，
沿返回的 `settlement_owed` 命令恢复原 Turn。结果中的支持快照按需读取。
原始投递、手动计划和其他运行模式继续使用原结算命令。

已捕获的直接重规划也可在写回视图提供合并的 `settlement_command`。该命令复用
捕获中的语义写回参数；复盘后填写证据占位符，沿原义务执行并检查回执。原计划和
手动步骤仍可用于恢复。确认已结算后不再扣额。创建后继 Todo 或已绑定 Todo 的
重规划保留原有的写回和结算路径。

生成的 Turn 绑定 JSON `quota spend-slot` 命令携带
`--max-page-output-bytes 64000`。此输出选项不启用 Context Delivery，也不建立原生
连续性基线。小结果保持完整；超出预算时，将 `before` / `after` 快照存入私有、
不可变的完整 JSON，其他字段仍直接返回，包括实际追加结果、工作目录拒绝和结算
回执。按需在 `result_details.command` 后添加 `--select`；读取器保留相同输出预算，
每页校验完整摘要。如果其余控制信息也超限，返回命令会选择全部控制字段，必须
完整读完这些页再行动，不截断控制正文。

省略选项保留原有完整结果 API；非正预算在执行前拒绝。缺少已验证 POSIX 私有捕获
校验的平台在结算前拒绝此选项；仅移除输出选项即可使用同一 Turn 的完整结果 API。
reader reference 在该平台也安全拒绝，不能用 mode bits 代替 Windows ACL 校验。
求值后捕获失败时保留全部原始控制字段（包括 `ok`、拒绝原因、身份和回执），只省略
`before`／`after`，追加 `result_delivery_error` 并非零退出。此故障兜底优先保全控制
信息，不保证展示预算；被拒绝的操作不一定存在 spend receipt。修复捕获权限或以
原身份读取无输出预算的结果，不能据投递失败认定效果未提交。恢复不重新准入或
更换原 Turn／effect 身份。此传输变化本身不证明模型保留或成本收益。

### Saved context cache / 已保存上下文缓存

For `quota context-project`, enabling context delivery no longer requires a
second cache-directory setup step. Omitting `--decision-output-root` reuses the
native session's private context directory. An explicit missing leaf is created
with mode `0700` under an existing parent; existing directories must remain
private, physically addressed and owned by the current user. Existing symlinks,
non-directories and unsafe permissions are rejected without changing them.
Normal platform parent aliases are resolved before validating the leaf.
Read-only recovery never creates a cache. With context delivery off, projection
remains inert and reads the original capture. Failures retain that capture's
exact digest and Turn receipt; they never rerun admission or spend quota.

With delivery on, the saved projection uses the file-first native entry above.
An explicit full read remains available: follow `next_command` through EOF only
when that complete content is needed. JSON `context-read` defaults to 64000
content bytes under a 64000-byte serialized-output ceiling; Markdown retains its
4096-byte content default. Explicit limits and UTF-8 byte cursors are unchanged.
Oversized or missing tool output is recovered from the affected range of this
same capture, never by another admission. Off responses retain their original
contract. Reader/output sizing alone proves neither model retention nor savings.

Republishing a reader reference through a normal parent-path alias reuses the
existing immutable descriptor when the resolved parent, exact leaf name, full
digest and all other metadata agree. The stored descriptor is never rewritten.
Different leaf names, including hardlinks, remain distinct; capture and descriptor
leaf symlinks are still rejected by the reader. This compatibility does not
establish retention or authorize another admission.

`quota context-project` 开启上下文投递后，不再要求另外手工准备缓存目录。
省略 `--decision-output-root` 时复用原生会话的私有上下文目录；显式指定的新目录
在已存在的父目录下以 `0700` 创建。已有目录仍须属于当前用户、权限私有且不是
符号链接；不修改不安全目录的权限。平台正常的父路径别名先解析再检查末级目录。
只读恢复不创建缓存；关闭上下文投递时保持无副作用，读取原始 capture。失败仍保留
原始摘要和 Turn 回执，不重新准入或扣减额度。

开启时采用上面的文件入口。需要全文时仍可显式读取并沿 `next_command` 到 EOF；
不将此操作设为通用前置条件。JSON `context-read` 默认请求 64000 字节正文，完整输出
上限 64000 字节；Markdown 保留 4096 字节正文默认值。显式上限和 UTF-8 字节 cursor
语义不变。工具输出过大或缺失时，只恢复同一捕获的受影响范围，不重新准入。
关闭态保持原有契约。页大小与输出预算本身不证明模型保留或成本收益。

通过正常父路径别名再次发布读取引用时，只有解析后的父目录、精确末级文件名、完整
摘要及其余元数据均一致，才复用已有不可变描述文件；不重写其内容。不同末级文件名
仍是不同引用，即使它们是硬链接；读取器仍拒绝 capture 或描述文件的末级符号链接。
此兼容不证明模型保留，也不授权再次准入。
