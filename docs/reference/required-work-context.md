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
narrative is never classified by headings or clipped to a size budget. For default-off and signed-envelope callers, read all
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

The Goal availability adapter still reads and UTF-8-validates the complete file
and hashes all bytes for its revision. With native context delivery off, only the path and revision of unmarked or mixed sources cross the
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
Goal/Agent binding, without adding fields to ordinary/default-off producer output
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

These are presentation rules within the existing default-off option, not a new
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

既有默认关闭的原生 context delivery 由同一 TypeScript codec 处理普通 quota 与
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
the known parent in the same capture, then retry the corrected batch. Other
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
修正路径语法或在同一 capture 中检查已知父节点，再重试修正后的批次。其他 runtime
故障保留自身分类；启动故障保留原诊断码。这些故障不要求恢复健康的 capture。
文件缺失或损坏仍由原文件/摘要检查拒绝，重新读取不会重新准入。

`context_capture_index_v1` 沿用双摘要分页；展开的目录包含 `list_depth`，
`next_command` 保留深度、路径和两个摘要。更改深度会拒绝旧视图的续读，目录 cursor
也不能用作正文 cursor。遇到输出截断或缺页，用足够的输出预算恢复同一 capture
的受影响范围，保留原 Turn/操作身份，不重新准入。列出字段不表示读过其正文。
包括根选择在内的目录/详情 EOF 不证明全量模型保留；原生文件比较与此独立。
模型可直接查当前完整文件，也可查基线和差分，无须先通读。

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
