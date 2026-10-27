# Required work context

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
narrative is never classified by headings or clipped to a size budget. Read all
delivered context and remaining reads before dependent work; changed sources need
a fresh guard. Enabled scoped acceptance does not replace original Goal intent.

The Goal availability adapter still reads and UTF-8-validates the complete file
and hashes all bytes for its revision. For unmarked or mixed ownership, only its path and revision cross the
internal context-projection bridge, preserving the complete progressive read
without an unused body copy. Explicitly owned sources send the complete
non-generated narrative and region lineage to the typed consumer. Missing files
and invalid UTF-8 still block dependent delivery. Byte reduction alone does not
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

Within successfully read cumulative Goal outcomes, source summaries, review notes
and string evidence references also use exact reuse. They are provenance and
explanations, not current acceptance decisions. Requirement status, review
conclusion, basis currency, independent verification, revisions and unknown fields
remain literal current input. Changed, removed or reordered evidence reconstructs
the complete current source; failed or incomplete sources retain full delivery.

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
any field required as current input cannot use a whole-subtree reference. The
codec chooses a reference only when smaller than its literal or recursive edit.
Current and unknown controls remain inline. Older string-only readers recover
the current full capture for an unsupported structured reference. Missing,
malformed or incompatible references recover the same full current capture;
the reconstructed context must still match its complete digest. Cold delivery,
session invalidation and authority remain unchanged. This saves transport only;
it does not establish model retention, billed-token savings or useful outcomes.

These are presentation rules within the existing default-off option, not a new
configuration or admission policy. A missing continuity basis, a changed epoch, a damaged
patch or an incompatible reader automatically recovers the same original full
capture. Recovery never reruns the guard, asks the owner, spends quota or grants
execution. Wire reduction alone does not establish actual model costs or outcomes.

After reading a native delta completely, the Agent uses its changes with the
retained model context. A resumed tool process may have no earlier Code Mode
variables; that alone does not mean the model lost the baseline. Materializing a
JavaScript object does not require another full read. If required baseline content
is unavailable or continuity is uncertain, recover the same capture in full before
dependent work. Compaction still invalidates the baseline and requires recovery.

### Machine default

**Settings → This machine → Capabilities → Differential context → Codex** uses the
existing revision-locked preview, apply and readback operations in one switch action.
Switching off uses the same transaction. A saved preference does not certify native
host setup or actual delta use; the settings view states this explicitly. Its
`context_delivery` machine namespace accepts
`{"schema_version":"context_delivery_machine_defaults_v0","mode":"off"}` or
the same object with `"mode":"on"`. The CLI exposes that same owner through
`machine-config preview/apply --namespace context_delivery --config-json FILE`;
apply requires the exact preview revision. Removing the namespace restores off.
Sibling settings and Goal records are preserved. The application row selects Codex
within this capability; the existing `mode` remains that Codex preference, with no
additional global switch or host configuration authority. Read-only editors cannot
save it. The UI does not alter other hosts or initialize the current native session.

An explicit `--context-delivery on|off` wins; otherwise each invocation reads the
machine default, with off when absent. The existing worker environment's explicit
`LOOPX_CONTEXT_DELIVERY=off` also overrides inheritance; environment on alone does
not enable delivery. Benchmark trials keep their existing `context_delivery=on`
opt-in. Workers propagate the resolved trial choice, including default off, to
both preflight and solver subprocesses without changing the caller's settings.

Machine consumers that parse `should_run`, selected work or settlement identities
must explicitly request raw output on internal `quota should-run` calls using
`--context-delivery off` or the subprocess `LOOPX_CONTEXT_DELIVERY=off` override,
and retain the exact registry, Goal, Agent and Turn. Enabled native output is a
presentation/capture envelope, not a raw authority decision; a missing
`should_run` in that envelope is not a denial. MCP settlement and Claude status
readers use the existing `LOOPX_CONTEXT_DELIVERY=off` subprocess override, which
also preserves compatibility with older CLI parsers. This does not change the
machine preference or disable differential context for the interactive Agent.

机器消费者若解析 `should_run`、选中工作或结算身份，应在内部 `quota should-run`
调用中通过 `--context-delivery off` 或子进程 `LOOPX_CONTEXT_DELIVERY=off`
显式请求原始输出，并保留原 registry、Goal、Agent 和 Turn。
开启差分后的原生输出是展示／快照封装；封装没有 `should_run` 不表示准入拒绝。
MCP 结算和 Claude 状态读取在子进程内设置已有的 `LOOPX_CONTEXT_DELIVERY=off`，
兼容旧版 CLI 解析器；不修改机器开关，也不关闭交互
Agent 的差分上下文。

On accepts the declared native scenarios: Codex App heartbeat (`--codex-app` or its equivalent complete scheduler
declaration), Codex App SSH Goal (`--runtime-profile codex_app_ssh_goal`), and the existing Codex benchmark heartbeat worker (`generic_cli`
plus its explicit heartbeat worker environment). Missing, contradictory or
unsupported Codex declarations return an error before admission. This machine
preference is scoped to Codex: other explicitly resolved hosts keep full delivery.
An explicit `--context-delivery on` on those hosts still errors. Declarations select
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

Native `quota should-run` and the saved `context-project` entry share the same
paged presentation. Each returns page zero, a digest-pinned continuation and full
recovery without another admission. Inline and continuation pages use the same
64000-byte output budget, including recovery, instructions, escaping, commands
and newline. Content shrinks at UTF-8 boundaries to fit that complete stdout;
captures that fit finish inline. This default does not depend on a shell output
declaration. Explicit smaller reader budgets remain available to recover a
truncated range of the same capture. Consume the inline
page before following next_command; when the retained-history limit is unknown,
read one bounded output at a time. This is not a guarantee of model retention.
Forward exec stdout directly in code mode. Repeating the packet in
a hashing tool call does not establish that its original bytes were retained.
Keep these short local reader commands in the foreground; for `exec_command`,
use its normal 10000 ms wait rather than an aggressive early yield. A native
host can deliver a page before the process exits but give `PostToolUse` only the
remaining output when polling finishes. Complete model-visible text then does
not imply complete hook coverage. If a reader yields, consume all its output
chunks; missing hook coverage keeps the next decision on full delivery. Do not
fabricate a read receipt or rerun admission to make it differential. Continuing
that captured decision after context loss still uses its exact full recovery.
The reader verifies the immutable capture digest. Native lifecycle delivery uses
the same byte-range validator as explicit input observations: before merging
reader ranges, the post-tool adapter checks actual UTF-8 content against the
trusted journal's digest-pinned physical capture. A changed body, missing or
replaced file, symlink or digest failure cannot advance the baseline. It keeps
the original recovery reference; it does not collect another decision. This
checks output observed at the hook boundary, not downstream model retention.
Older metadata-only candidates require a new complete verified read before
resume or the codec can reuse them; page metadata alone cannot mark them verified.
Native lifecycle continuity still uses
an explicit engineering assumption: a normally completed full read was read and
remains available until invalidated. This is not a retained-input observation or
a model ACK. The shared codec still validates the exact baseline digest, scope,
current controls and reconstruction. Explicit final-input Host clients retain
their separate confirmed-input contract.

For native setup, `quota context-config --codex-app --goal-id GOAL --agent-id AGENT`
under machine on prepares only that private transport leaf and renders the
existing hooks, recovery instructions and `config_overrides`. Applying those overrides
selects a 20000-token native history limit and declares the existing 64000-byte
output profile. Both the initial guard and continuations use that page budget,
including when the shell lacks the declaration. The worker applies this configuration
automatically inside its isolated opt-in trial. The persistent instruction requests
22000 tokens for each context-read tool result and 24000 for its code-mode emission,
forwarding one stdout at a time. Remove the generated output overrides together with the
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

`PostToolUse` records complete digest-bound reader ranges in the existing session
journal; EOF without preceding pages, a known malformed/truncated page or a reader
error cannot establish a baseline. Concurrent and repeated pages coalesce.
`PreCompact`, `PostCompact`, clear, fork and a changed session/workspace invalidate
it. A resume may supply different history under the same session ID; the
[native event contract](https://learn.chatgpt.com/docs/hooks#sessionstart) alone
does not attest retention. The shared Codex adapter can qualify a resume against
the complete read's native replay checkpoint: the native-selected physical
rollout must retain the digest-bound interval beginning at the current baseline's
first reader and every observed reader output,
including complete byte coverage of the pinned capture, with
matching session, workspace, model and permission mode, and no intervening
compaction, rollback, replacement or unknown replay record. This bounded IO
profile is qualified on Codex 0.162.1 paginated rollouts. It streams only the
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
Opted-in benchmark native children create owner-only files automatically; the
parent process and off-mode file creation policy stay unchanged. Group-writable
existing replay sources cannot establish cross-process continuity.
A new independent decision uses its normal guarded
context command and reads the resulting current full capture; it need not first
read an older capture to seed a delta. Continuing a captured decision after context
loss still requires complete recovery of that exact original, never a new guard
as a substitute. Both paths establish a new chain only after a complete verified
full read. Ordinary tools and turn endings do not invalidate it. An interrupted
pending delivery requires full recovery. Repeated reset events are idempotent.
Recovery reads the same immutable capture without re-admission or spend.

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

本页规定当前交付行为。[统一契约提案](../architecture/rfcs/controller-executor-unified-contract-v0.zh-CN.md)
拟让所有入口复用 full/delta/detail/recovery 流程。controller 编写的工作说明必须
保留完整原始要求与可访问证据；实验性模型 token 目标不允许截断，也不替代来源读取义务。
提议的共享组合边界在 capture 前保留来源 revision 与未完成必读义务。从该不可变 capture
恢复交付不刷新权限；合法的新选择/权限检查与传输重试仍是不同操作。

普通 Heartbeat/quota 默认在 `agent_channel.work_context` 返回完整选定 Todo、
启用的验收与当前 Agent 相关的 User Todo，附带来源和修订，不需要开启 TurnEnvelope。
Goal Markdown 复用原 writer 对生成区域的严格归属规则：所有已识别 Todo 区域均有
显式标记时，`work_context.sources` 原样携带区域外的全部文字，保留目标、frontmatter、
任意章节、注释、历史和末尾停止条件。`excluded_todo_regions` 保留每段的角色、行区间
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
Goal adapter 仍完整读取文件、校验 UTF-8，并以全部字节计算修订摘要；未标记或混合归属
仅在内部桥接传路径与修订，避免未被消费的正文副本，Agent 仍须渐进式全文读取。显式
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

既有默认关闭的原生 context delivery 由同一 TypeScript codec 处理普通 quota 与
TurnEnvelope。在基线连续性成立的口径内，两入口仍逐轮发送 `planning_horizon.core_goal` 的
原始目标、来源和可用性，包括目标改变或缺失。完整 `acceptance_contract` 与已登记
规划树按精确复用、子树变化或全量交付；累计验收树不等于原始目标句。未知 horizon
字段仍作为本轮输入。quota 通道与 Turn 契约摘要复用完整静态收口指导，当前收口判断、
条件、身份、命令与未知控制仍逐轮发送。Explore owner 登记分支证据审计与证据关联
模板的精确复用；分支身份、选择、状态、恢复事实与编排门仍是当前输入。重排、反证、
移除与指导变更完整重建当前来源，不规范化时间戳、修订或正文。
已成功完整读取的累计 Goal outcome 中，来源说明、复核笔记及字符串证据引用也按精确
差分传输。它们是来源与解释；要求状态、复核结论、依据是否当前、独立验证标记、修订
及未知字段仍逐轮完整发送。证据变化、移除或重排后仍须精确还原当前来源；来源失败或
读取不完整时保留完整投递。
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

完整读完原生差分后，Agent 将变化与模型仍保留的上下文一起使用。恢复后的工具进程可能
没有此前的 Code Mode 变量；仅此并不表示模型丢失了基线。不必为了组装 JavaScript
对象再读一次全量。若当前工作所需的基线内容不可用或连续性不确定，仍须先完整恢复
同一捕获，再做依赖它的工作。压缩仍会使基线失效，并要求恢复。

### 机器级默认

在**设置 → 本机设置 → 能力中心 → 差分上下文 → Codex**选择关闭或开启，沿用现有
精确修订预览、应用和读回，由一次开关操作完成；关闭也使用同一事务。
设置页明确区分已保存偏好与尚未验证的原生初始化、实际差分使用。机器 namespace 为 `context_delivery`，配置为
`{"schema_version":"context_delivery_machine_defaults_v0","mode":"off"}`；开启时
把 `mode` 改为 `on`。CLI 的 `machine-config preview/apply --namespace context_delivery
--config-json FILE` 使用同一 owner；应用须带原预览修订。移除此 namespace 恢复关闭，
不改写其他配置和 Goal 记录。应用行选择的是此能力下的 Codex；既有 `mode` 仍表示
该 Codex 偏好，不新增总开关或宿主配置 authority。只读编辑器不能保存；界面不会
改变其他应用或初始化当前原生会话。

显式 `--context-delivery on|off` 优先；省略时每次读取机器设置，未配置则关闭。
已有 worker 环境的 `LOOPX_CONTEXT_DELIVERY=off` 也覆盖继承；环境 on 本身不启用投递。
benchmark trial 保留既有 `context_delivery=on` 启用方式。worker 将本次运行已解析的选择
（含默认关闭）传给预检和 solver 子进程，不改写调用方原设置。
开启接受以下原生声明：Codex App 心跳（`--codex-app` 或等价完整调度声明）、
Codex App SSH Goal（`--runtime-profile codex_app_ssh_goal`），以及现有 Codex benchmark 心跳 worker（`generic_cli` 与其显式心跳环境）。缺失、冲突、
不支持的 Codex 声明在准入前报错。机器偏好仅作用于 Codex；其他已明确解析的宿主
继续完整投递，对这些宿主显式指定 `--context-delivery on` 仍报错。声明只选择 adapter，
不证明宿主身份或上下文保留。
当前原生连续性依赖 POSIX；其他平台显式拒绝开启。

机器继承与显式开启的入口共用已解析 machine runtime 下的私有 `context-delivery` 目录，并按原生
会话隔离 journal。缺失末级目录以 `0700` 创建；不跟随末级符号链接、不修改不安全目录
权限。显式 on 未指定路径时也自动选择目录，仍支持手工覆盖；handoff socket 保留
原有 Host 投影路径，不另设原生 journal。无关 quota 命令和带原始摘要的恢复分页不激活此
设置；业务操作仍须通过原有设备配置校验。

机器开启后，`quota context-config --codex-app --goal-id GOAL --agent-id AGENT` 只准备
私有传输末级目录并生成已有 hook 和恢复指令，不安装或信任 hook，也不更改宿主配置。
SSH Goal 使用 `--runtime-profile codex_app_ssh_goal` 生成 context-config 和 thin/bootstrap 提示，
沿用可见 Goal 的执行与结算身份；仅启用差分时接入捕获目录，不转换成自动心跳。
原生 `quota should-run` 与已保存捕获的 `context-project` 共用分页呈现，直接返回第零页、
固定摘要的后续读取与完整恢复，不另行准入。内联页与续页共用 64000 字节输出预算，
包含恢复字段、完整指引、转义、后续命令与换行；正文按 UTF-8 边界缩小以适配完整 stdout，
能放下的捕获一次返回。默认值不依赖 shell 输出声明；显式更小的读取预算仍可用于恢复
同一捕获中被截断的范围。先消费内联页，再跟随 next_command；保留历史
上限未知时，每次只读一个有界输出。该上限不证明模型保留。Code Mode 直接转发
exec stdout；把整包重打进哈希工具不构成原始字节已被保留的证明。
这些短时本地读取命令应前台等到结束；`exec_command` 使用正常的 10000 ms 等待，
避免激进提前返回。原生宿主可能先把页面交给模型，却只把轮询结束时剩余的输出交给
`PostToolUse`；模型可见正文完整不等于 hook 观察范围完整。读取器若提前返回，仍须
消费全部输出片段；hook 覆盖缺失时，下次决定继续全量投递。不伪造读取回执或重跑
准入来制造差分；丢失上下文后继续该已捕获决定，仍沿它的精确全量恢复引用读取。
原生配置同时返回 `config_overrides`：应用后，原生历史上限为 20000 token，并声明已有
64000 字节输出配置。首次 guard 和续页均使用此分页预算，shell 缺少声明时也一样。
开启的 benchmark worker 在其
隔离会话内自动应用；持久指引要求每次续读直接输出预算 22000 token、Code Mode
输出预算 24000 token，每次直接转发一份 stdout。
回滚时同时移除生成的输出覆盖项；仅保存机器偏好并不会应用这些宿主配置。
该设置不证明输入保留、总费用降低或任务效果改善。
读取器校验不可变 capture 的摘要。Post-tool 适配层复用显式输入观察的字节范围校验，
先将实际 UTF-8 正文与可信 journal 指向的物理捕获及固定摘要核对，再合并读取范围。
正文改变、文件丢失或替换、符号链接及摘要失败均不得晋升基线；保留原始恢复引用，
不重新收集决策。这只验证 hook 边界观察到的输出，不证明下游模型输入保留。
旧版仅凭页元数据建立的候选须先完整读取并通过字节校验，才能在 resume 或 codec 中复用；
页元数据本身不能写入已校验标记。
原生连续性仍明确采用工程假设：正常完整交付的首包已读，
在失效事件发生前仍可用；这不是实际输入保留证明或模型 ACK。共享 codec 仍校验精确基线
摘要、作用域、本轮控制与完整重建。显式 final-input Host 客户端保留独立的确认输入契约。

为下次原生会话配置并信任精确 hooks，不重启当前 App。已加载线程可能继续沿用旧 hook
配置；`hooks/list` 的 enabled/trusted 读回不等于向该线程交付 `SessionStart` 或初始化
context journal。当前工作到达正常安全边界后，沿宿主正常启动／恢复入口加载配置，再
核验实际生命周期交付。此前保持全量与原捕获，不伪造事件、借用其他会话 journal 或为
制造采用样本重启活动宿主。原生路径保留认证、端点、provider
和连接方式，包括带 `previous_response_id` 的隐式历史；不启用输入诊断，也不逐轮扫描
模型完整输入。Benchmark 在各自隔离的 trial home 内自动准备同一组 hooks。

`PostToolUse` 在现有 session journal 中合并摘要固定的完整读取范围；缺少前页的 EOF、
已知的格式损坏／截断或读取失败不能建立基线。并发、重复页幂等合并。`PreCompact`、
`PostCompact`、清空、分叉、会话／工作区切换使基线失效；同一会话 ID 的 resume
也可能使用不同历史，[原生事件契约](https://learn.chatgpt.com/docs/hooks#sessionstart)本身不证明保留。
共享 Codex adapter 可核对完整读取时的原生重放检查点：原生实际选择的物理历史文件，
须保留从当前基线首次 reader 开始的摘要固定区间，以及每份精确 reader 输出，完整覆盖固定捕获的全部字节；
会话、工作区、模型和权限模式相同，
中间没有压缩、回滚、替换或未知重放记录。此 IO profile 在 Codex 0.162.1 的分页
rollout 上验证；流式读取当前基线对应区间及后续记录，不哈希或解码无关旧历史，
文件总大小不决定是否差分。session metadata 的创建版本不等于当前宿主版本。
code-mode 父调用的保留输出须包含嵌套 reader hook 观察到的全部精确页字节。
同一父调用可以包含多次嵌套读取，仍逐份核对其输出摘要和全部字节覆盖。
证据缺失、改写或重放记录不受支持时回退全量；单条记录的解码内存仍有边界，
不是历史文件容量限制。旧版全前缀检查点须重新完整读取后建立新检查点。仍采用正常读取后的
工程假设，不声称证明未来模型输入；不扫描模型请求，也不要求模型 ACK。
开启后的 benchmark 原生子进程自动以仅所有者可访问的权限创建文件，父进程与关闭态
保持原权限策略；既有可组写的重放来源不能建立跨进程连续性。
新的独立决定走正常准入与上下文命令，完整读取其返回的当前全量捕获；不必为了建立差分
基线而先读旧捕获。若丢失上下文后继续某个已捕获决定的依赖工作，仍须完整恢复那份精确
原捕获，不能用新 guard 替代。两条路径都只在完整且已校验的全量读取后建立新差分链。
普通工具和正常轮次结束不无条件失效。尚未完成的交付被中断
后须完整恢复。重复重置事件幂等；恢复读取同一不可变捕获，不重新准入或扣额。

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

With delivery on, a successful saved projection also returns page zero using
the existing `context_capture_page_v1` fields. It uses the same 64000-byte
complete-stdout budget as continuations, including recovery, activation and
instructions; captures that fit finish inline. JSON `context-read` with no page
arguments also defaults to 64000 content bytes under a 64000-byte output cap.
Markdown retains its 4096-byte content default. Explicit page/output arguments
retain their prior semantics; a page above 8192 bytes still requires a sufficient
output cap, within the existing capture limit. The JSON-only
`--max-page-output-bytes` shrinks content at UTF-8 boundaries and persists in
`next_command`, including for explicitly smaller same-capture recovery. An
insufficient cap returns an error without a zero-progress page; increase it for
the same capture. Error messages are not subject to an impossible page limit.
The reader changes no host or retained-history setting. Historical Codex 0.162.1
qualification used a 10,000-token history setting and a 12,000-token code-mode
result budget with one directly forwarded stdout per call; that observation
does not establish another host's output capacity. The shared native setup above
supplies matching history/execution budgets; only the opted-in worker applies
them automatically. Larger defaults never prove history retention, and missing
output still requires recovery from this same capture.
Consume `content`, then follow `next_command` through `eof` for full byte coverage.
The reader verifies the original digest; do not retype or hash the content. The
existing `command` still restarts at zero for older readers or incomplete inline
output. If the optional first-page read fails, that same command remains usable.
Off responses do not add an inline page or activate native reuse; their recovery
references use the same JSON reader default. No admission
or retention receipt is introduced; an inline page alone cannot prove complete
input retention.

`quota context-project` 开启上下文投递后，不再要求另外手工准备缓存目录。
省略 `--decision-output-root` 时复用原生会话的私有上下文目录；显式指定的新目录
在已存在的父目录下以 `0700` 创建。已有目录仍须属于当前用户、权限私有且不是
符号链接；不修改不安全目录的权限。平台正常的父路径别名先解析再检查末级目录。
只读恢复不创建缓存；关闭上下文投递时保持无副作用，读取原始 capture。失败仍保留
原始摘要和 Turn 回执，不重新准入或扣减额度。

开启时，成功的已保存投影同时用现有 `context_capture_page_v1` 字段返回第一页。
该页与续页共用 64000 字节完整 stdout 预算，包含恢复、启用配置与完整指引；能放下的
捕获一次返回。JSON `context-read` 省略分页参数时，也默认在 64000 字节输出上限下
请求 64000 字节正文；Markdown 保留 4096 字节正文默认值。显式分页/输出参数保留
原有语义：超过 8192 字节的页仍须足够的输出上限，且不超过既有 capture 限制。
JSON 专用参数 `--max-page-output-bytes` 按 UTF-8 边界缩减正文，并在 `next_command`
中保留，显式更小的同捕获恢复也一样。上限不足时返回错误，不返回无进展分页；
增大上限后重读同一捕获。错误消息不受无法满足的分页上限限制。
读取器不修改宿主或保留历史设置。历史 Codex 0.162.1 定向验证采用 10,000 token 历史
设置和 12,000 token code-mode 输出预算，每次直接转发一份 stdout；该观察不证明
其他宿主的输出容量。上述共享原生配置提供配套历史和执行预算，仅开启的 worker
自动应用。较大默认值不证明历史保留，缺失输出仍恢复同一 capture。
先读取 `content`，再沿 `next_command` 读至 `eof`，确保完整字节覆盖。
读取器校验原始摘要；无需重抄正文或计算哈希。
原有 `command` 仍从零开始，供旧读取方或内联输出不完整时重读；可选的首屏读取
失败时也保留该命令。关闭态不增加内联分页或启用原生复用；其恢复引用同样采用 JSON
读取器默认值。不新增准入或保留回执；
单页内联内容不证明完整输入仍被保留。
