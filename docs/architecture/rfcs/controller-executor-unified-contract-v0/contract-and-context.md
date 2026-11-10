# Contract and context migration: Controller RFC technical annex

This annex belongs to the [main RFC](../controller-executor-unified-contract-v0.md). It retains field, read,
historical-evidence and cutover requirements; it is not another RFC, task queue or
architecture. Start with the main system diagrams and current delivery path.
Historical experiments retain their revisions; their descriptions of “current”
behavior do not supersede the latest audit in the main document.

### C1 source audit: shared owners and the remaining split

The current complete-source validator also delegates executable required-read
checks to the existing interaction owner. Explicit command/order coordinates
cannot disappear or introduce an unsupported execution order; prose-only and
valid pending obligations remain intact. The same rule reaches quota, managed
preview and actual Turn/Host planning without caller-specific decisions. An
explicit direct complete carrier reaches its typed reader even when its version
is unsupported. The [paired checkpoint](../ledger/controller-executor-unified-contract-v0/2026-10-30-c1-source-convergence.md#executable-required-reads-and-native-runner-attribution)
records counterexamples, compatibility, maintenance scope and the native-runner
budget attribution. It does not waive the remaining budget gate, retire recorded
readers or establish installed/model/outcome benefit.

Bound native execution preparation retains the ordinary managed claim reference
and captures its same-Turn ready decision before CLI compaction. The Host hands
that source to the existing saved-capture presentation/reader owner, rather than
discarding it and making the model evaluate another guard to obtain its current
contract. Goal, Agent, Todo and Turn must match before publication. A qualified
native recheck retains its enabled profile and uses the existing deferred
presentation route. Context delivery does not select settlement ownership;
the saved-capture owner presents the same canonical plan once. Required material remains complete: consume
the complete inline Goal or execute its declared read when it is not inline;
a ready receipt or saved file does not fulfill that consumption. Optional presentation failure
retains the original digest-bound recovery. Explicit off is inert, and an
unbound request remains readback-only and cannot invent a Turn or a claim.
This is a bounded Host handoff repair, not C1 cutover, installed adoption or
qualification of model cost and long-horizon benefit.

The source already shares more than the output names suggest. `_emit_quota_result`
captures the complete decision **before** `_project_quota_cli_payload` chooses
ordinary CLI compaction or `buildTurnEnvelope`. The latter delegates to the
TypeScript owner; its Python module is a transport adapter. Both quota capture
and Turn journal capture call `write_decision_capture` in
[`decision_capture.py`](../../../../loopx/control_plane/turn_driver/decision_capture.py).
Native context delivery and explicit host handoff also reuse that module and
the existing context-delivery owner. C1 must converge the remaining composition
and consumer assumptions, rather than introduce another capture service or
rewrite already shared delta/native-resume logic.

Optional host polling is part of that original observation. The source-fidelity
slice records `--record-host-poll` once before capture and reuses its receipt in
ordinary CLI, envelope and native-context delivery. Recovery reads do not poll
again. A later capture/handoff failure retains the already-recorded observation
in its error response; it does not imply missing admission or authorize replay.
Default-off behavior and unreadable receipt diagnostics remain unchanged. This
repairs capture completeness, not the unified writer/consumer cutover or a
long-horizon cost-benefit result.

A separate native-presentation slice reuses the committed full recovery as its
reader target, avoiding an unused second wire publication. Delta and unwrapped
inputs retain their own immutable files and the same reader/lifecycle owners.
On a matched 107,289-byte synthetic source, capture writes fall from 216,754 to
107,289 bytes; full reader bytes and the 3,937-byte warm delta are unchanged.
This qualifies only duplicate capture IO removal, not model-input reduction,
complete cost, accepted outcomes or C1 writer/consumer cutover.
For a small explicit output ceiling, native presentation can also omit repeated
directory/selection commands while retaining the complete recovery command:
append `--list` or `--select` to discover/read current facts without a baseline.
The original 4,000-byte cold/warm/compaction cases retain their requirements,
Stop, original identity and full recovery; this is transport qualification.

Journal persistence is an independent source-fidelity prerequisite. The proposed
Python adapter reuses context delivery's sparse number-token collector; both
TypeScript consumers reuse one source-number validator. Journals remain JSON
objects, with metadata only for integral floats, signed zero and unsafe integers.
The journal owner restores these numbers before atomic publication and retains
their distinctions in replay and immutable-plan comparisons. Ordinary native
objects retain their encoding; historical receipts are not rewritten.

The interim whole-JSON-string carrier failed the existing 2 MiB request boundary
for a synthetic 1.55-million-character quoted body accepted by the object path.
Keep that negative result: qualify the sparse replacement against the same
workload and limit, including number-dense overhead. Neither this prerequisite
nor a component byte saving qualifies the unified writer/consumer cutover or
long-horizon net benefit.

The proposed Turn selection-reader slice removes the Python adaptive-selection
decision from the driver. Its existing plan, Host, Session, completion and
settlement callers use the interaction owner's `selectedWorkTodo` rule through
the existing source-edit transport. The selection adapter retains Python's historical
scalar/whitespace decoding; original records and numeric values stay intact.
This retires one duplicate rule, not the historical envelope format. Complete
writer/consumer cutover and original-quota outcome/full-cost qualification remain
open. The added bridge calls must be counted; code convergence alone is no
evidence of lower cost.

The current-main integration also checks the provenance of its qualification
fixtures. `read_fixture_todos` previously seeded canonical authority from an
attention summary, erasing an authored tail after 500 characters before capture.
It now uses the existing full source parser and Todo normalizer. A real
File/SQLite CLI case carries that tail through complete capture, a subprocess
Host's artifact, independent validation, settlement and replay. Both original
truncation failures remain evidence; this repairs the test input, not a runtime
policy. Current size and compatibility failures remain open. Deterministic
artifact agreement and measured CLI/RPC bytes do not qualify model comprehension,
native accepted outcomes, total cost or the full C1 cutover.

| Current source / obligation | Present envelope treatment | Required C1 disposition |
| --- | --- | --- |
| `interaction_contract.agent_channel.work_context` and pending required reads | `work_context` plus `required_reads`; identical inline material can be referenced | One complete requirement/evidence source with resolvable references; preserve pending reads and their completion conditions |
| Selected Todo, recommended action, action portfolio and planning horizon | `action`; reference relocation and text compaction occur in `turnActionProjection` | One selected work/action identity; preserve complete actionable clauses and distinguish summaries from executable instructions |
| `interaction_contract.cli_channel.settlement_plan` | Copied into `writeback.settlement_plan`, including original effect identity and conditional steps | Reuse the settlement owner unchanged; neither controller dialogue nor a new capture creates another debit identity |
| User/agent interaction channels, execution policy and task orchestration | Separate `user`, `execution_policy`, `boundary` and orchestration sections | Preserve notification versus execution obligations, authority and stop gates; do not derive them from prose |
| Replan packet, required reads and staged writeback | Dedicated replan projection and deferred writeback | Preserve fact/source access, policy-specific triggers and ordered mutations; no blanket activation of stride behavior |
| Scheduler context and execution ownership | Scheduler projection with caller execution context | Carry the actual host owner and ACK obligations; a common schema does not transfer scheduler authority |
| Complete saved decision, action signature and journal binding | Source hash, projected action hash and captured-decision reference are distinct | Preserve the distinction between semantic integrity, exact transport bytes and receipt identity; no hash substitution |

This is an owner-and-obligation map, **not an exhaustive field inventory or a
parity result**. Current projections have bounded text, allowlists and relocated
references. Matching their action signatures alone cannot prove preservation of
all source clauses or unknown fields. C1 implementation must enumerate those
fields and callers against its exact base/head; anything unclassified holds
cutover, rather than disappearing into an assumed compatibility view.

A synthetic probe of the named baseline demonstrates the limit: two long
`recommended_action` strings with identical prefixes but different tails produce
the same action-signature document and both report `matches=true`, while their
`source_decision_hash` values differ. An unregistered top-level source field has
the same distinction. Conversely, a complete registered settlement plan survives
projection, and changing its Turn identity changes the action signature. These
observations reject promoting the existing bounded envelope unchanged into the
complete contract. They do **not** establish a current authority-loss bug: the
recommendation is diagnostic, the unknown field is synthetic, and the complete
source commitment still distinguishes both inputs. No model benefit is measured.

### Differential context

Reuse the existing context-delivery owner and immutable capture mechanism.
The target has two orthogonal layers; neither creates another semantic producer:

- **Layer 1 — Progressive:** default model-facing delivery across LoopX hosts,
  including ordinary CLI, MCP/HTTP and external workers without Session Hooks.
  Keep the current Goal, applicable cumulative acceptance, failures/unknowns,
  authority, stop conditions and outstanding read/settlement obligations inline.
  Fold heavy history, source bodies and review evidence only through the existing
  typed composition owner, with exact current-capture read routes and explicit
  before-dependent-work obligations. Unknown relevance/schema keeps the complete
  necessary material. This is a delivery default, not permission to collect or
  persist additional data. A caller without an authorized, usable reader receives
  complete inline context; a local file path is not a working remote read route.
  If hard Host limits prevent both routes, expose unavailable context and hold
  dependent work.
- **Layer 2 — Delta:** an exact encoding of the composed instance, automatically
  selected only with qualified consumer/session, role, epoch, schema and source
  binding. Progressive can use full encoding; a complete inline instance can use
  Delta. Qualify both combinations as well as Progressive plus Delta and neither.
  A host without qualified continuity uses current full encoding of Layer 1,
  without losing Progressive or requiring a native session to be invented.

Distinguish two existing reuse claims. **`captured_file`** compares verified,
compatible immutable saved artifacts; exact reconstruction does not require the
model to have read the old file, reached EOF or retained its contents. **Retained
model-input reuse** may omit previously delivered text only with trusted complete
delivery and retention evidence for that exact consumer binding. A saved file,
tool-output receipt or model self-acknowledgement cannot establish that evidence.
Session Hooks observe their supported boundary; they grant no new authority.
Preserve deletion, array order, null, empty values, original numeric values and
complete strings. Unknown fields stay complete until reuse semantics are
registered. Publish the current complete recovery source before an optional view
or delta, verify reconstruction, and choose full when Delta plus its metadata is
not smaller. Layer 1 likewise compares its final serialized UTF-8 view, read
guidance and index with the same capture's complete alternative; use the smaller
valid delivery, never trim mandatory meaning to meet a size budget.

Current authority, admission, cancellation, unresolved-read, settlement and
scheduler facts stay fresh and inline even if unchanged. A warm transport never
relaxes the underlying instructions. Missing/out-of-order deltas, corrupt or
unknown baselines select current full encoding/recovery. Truncated or missing
reads recover the necessary range from the same capture. Compaction invalidates
retention for the next work Turn's new baseline epoch; it neither invalidates the
existing Turn nor forces an old-packet prefetch or immediate full reread. Preserve
the original admission, recovery reference and once-only settlement/debit.
Execution still checks current source validity and authority; recovering an old
capture cannot revive cancelled work. The executor's task conversation consumes
semantic material separately, not a relabeled controller Core-packet delta.

#### Unread material across Turns

Current immutable captures already support exact selected reads. The missing
design is explicit discovery of unread material across deliveries, not a second
reader or a requirement to consume an entire baseline. Extend the existing
context/readback owner and native `session.json` projection; use its equivalent
consumer binding on other hosts. Distinguish publication, model-visible delivery
coverage, retention and semantic adoption/accepted outcomes.

`confirmed_read_sections` is a proposed coverage projection;
`consumed_pointers`, if retained as a name, must derive from that same owner,
not become a second ledger or imply comprehension. Bind evidence to the exact
source/capture digest, revision, consumer/session role, epoch, schema and selected
pointer/range. Confirm coverage only from trusted Host observation of the actual
model-visible result. Calling `--select`, file existence, reader EOF, a successful
tool log or a self-ACK alone is insufficient. Partial/truncated/missing output
stays unconfirmed; selecting a parent/index cannot confirm hidden descendants.
Without such an observer, coverage is unknown and discovery stays conservative.
This coverage never becomes a new full-read gate for `captured_file` Delta.

Every warm delivery carries an **`unread_context_index`** (proposed name) for
eligible non-inline material whose current content is unconfirmed or changed,
including unchanged text omitted by Delta. Include exact current pointers,
source revisions/digests and required-read status, rather than inferred relevance
from a summary. A confirmed range applies only to its observed version; changed
content, reordered arrays or ambiguous mappings require current rebinding and
conservative discovery. Confirmed delivery never proves retained input or adoption.

Each item resolves independently through existing `quota context-read --select`
against the **current complete immutable `projection.json`**, with its exact
capture SHA, without the previous packet, baseline file, model memory or patch
chain. A derived `required-context.json` or wire view retains the existing owner's
explicit mapping to the canonical source; roots and digests are not interchangeable.
Historical exact-version lookup remains a separate, version-pinned artifact/read
contract, not an invented path alias or a reason to silently use stale facts.
If still-required earlier material is absent from the new projection, its owner
must carry it forward or expose its current authorized source/read obligation.
Legitimate expiry, revocation or removal must remain explicit, with unavailable
required material holding dependent work; never silently mark it read or retain
forbidden raw data. External materials keep their own source-consumption contract.

#### Configuration and migration

The following is proposed behavior, not a change to today's Codex-scoped
`context_delivery` on/off interface. Evolve the existing typed configuration owner
to express the two layer decisions; C1 maps exact names and versions before
implementation. Do not add a parallel settings store or packet-format toggle.

| Configuration / caller | Migration and effective behavior |
| --- | --- |
| No saved preference, model-facing caller | Target Layer 1 default applies after that caller's reader/fallback and cost qualification; Layer 2 is auto only for a qualified continuity host, otherwise full encoding. Today's ordinary CLI/other-host full behavior remains until that rollout qualifies. |
| Explicit invocation/environment off | Preserve current precedence and full/raw delivery for that invocation, with no optional codec/coverage-ledger dependency; do not rewrite the saved preference. |
| Existing persisted off | Preserve the complete off path and its original read/recovery obligations; never convert it to Progressive or Delta through an absent-preference migration. |
| Existing on, supported host | Preserve the qualified native file/recovery semantics while mapping to Layer 1 plus qualified Layer 2 auto; no full-model-read prerequisite is introduced. |
| Existing explicit on, unsupported host | Preserve the current validation error during compatibility. A versioned caller rollout may expose supported Layer 1 after qualification; do not silently reinterpret an unsupported request. |
| New independently configured layers | Honor each layer independently, including Progressive off with qualified Delta auto. Explicit Delta off preserves Progressive; unsupported auto falls back to current full encoding. |
| Internal raw machine consumers | Preserve their explicit `--context-delivery off` contracts; do not replace machine JSON with a model-facing file reference or mutate preferences. |

Freeze recorded versions, experimental configurations and historical receipts.
Qualify default expansion as a behavior change on real callers, including explicit
off isolation and failure/recovery; a merged design alone changes no defaults.

#### Delivery acceptance

Reuse C1/C2's real-entry and §9 comparisons, including these decisive cases:

| Case | Required observation |
| --- | --- |
| Ordinary no-hook CLI and supported MCP/HTTP/worker routes | Layer 1 works through the actual authorized reader or complete inline fallback; no dangling local-only references or invented native epoch. |
| Unchanged unread tail becomes decisive next Turn | Warm Delta may omit its bytes, but the current index exposes its full source and actual subsequent selection/work obeys the recovered constraint. Index visibility alone is not success. |
| Changed/unread bodies, reordered arrays, unknown fields or scope | Rebind to the current source; preserve applicable cross-Agent obligations and unknown-scope complete fallback. |
| Partial pages, lost/truncated tool output, EOF without a Host witness | Do not confirm unseen bytes or hidden descendants; recover missing ranges, conservatively index unknown coverage, and keep file Delta usable. |
| Compaction, role/session/schema/epoch change | Invalidate incompatible retention/reuse; recover current facts without replaying the original admission, effects or debit, and without old-packet prefetch. |
| Revoked source, missing reader or legitimately removed material | Respect the current permission/source owner; expose the loss and hold dependent work or use authorized complete fallback, without stale-data disclosure. |
| Full is cheaper; off and legacy configuration | Choose valid full delivery, preserve explicit off and frozen versions, and observe both source identity and raw machine-consumer parity. |

The primary stored-body reduction is **`1 - V/F`**: `F` is the complete capture's
actual UTF-8 file bytes and `V` the selected view body file's actual bytes from
that same capture; saved bytes are `F - V`. Bind both artifacts and their digests,
and report wire/index overhead separately rather than changing that denominator.
The delivery size guards above still include their actual presentation overhead.
A net-benefit claim also counts all later reads/recovery, host/controller/executor calls and latency,
actual tokenizer/cached/output usage, human attention and accepted outcomes.
Preserve unfavorable cases and frozen budgets. A smaller packet alone proves
neither lower token cost nor better outcomes; §9's original quota single-session
comparison stays primary, and two-session costs remain a separate comparison.

### Candidate context by decision stage

Preserve complete candidate context in the canonical capture; deliver it according
to the decision the consumer must make through the same C1/C2 owners. This is a
proposed replacement contract, not permission to skip existing required reads.
The current capability gate assigns selection to the Agent. A `selected_todo`
projection or list order alone does not prove that comparison is finished. Keep
the complete-context repair in [#7557](https://github.com/loopx-project/loopx/pull/7557)
until consumers and recovery obligations are replaced together.

| Decision stage | Required input before the dependent decision |
| --- | --- |
| Select or reconsider work | Complete discoverable candidate index: identity/revision, original objective, declared Goal contribution, priority, eligibility, ownership, dependencies/blockers, current evidence/uncertainty and exact captured detail reference. Missing facts remain unknown. Read full detail for compared alternatives; if the index cannot establish whether an alternative matters, expand it before choosing. Do not silently keep only a top-K set. |
| Continue admitted work | Full selected task, original and cumulative requirements, applicable global/shared constraints, dependencies, contrary evidence, current authority and stops. A registered continuation contract may defer independent alternatives while exposing new/changed eligibility and evidence that can require reconsideration. An Agent still instructed to select over all runnable candidates remains in the selection stage. |
| Replan or accept the Goal | Expand all affected work and acceptance, including cross-Agent dependencies and contradictory evidence. Unknown scope expands to the complete captured basis. Ownership and earlier local completion do not prove irrelevance. |

These are consumer obligations, not three packet schemas or new scheduler modes.
The typed owner determines which reads can be deferred under an explicit contract;
the Agent/controller makes the semantic choice. Author-provided purpose or risk
summaries help discovery but cannot prove omitted prose irrelevant. Do not infer
relevance from keywords, claim labels or a model-authored score. Without sufficient
typed facts, comparison can still require all bodies: cold selection has no
promised compression ratio. Default expansion of the current Agent's outcomes is
useful for ordinary work, with other outcomes discoverable; shared requirements,
dependencies, conflicting evidence and unknown relationships override that default.
Outcome scope and candidate selection remain distinct owners.

Extend the existing immutable reader to batch selected details from the **same
capture**, retaining each requested item's revision, lineage, integrity, coverage
and cursor. Distinct revisions of one Todo stay distinct. Missing, truncated,
unsupported or changed detail holds dependent work and uses that capture's full
recovery; a fresh guard is not a detail reader. One read can deliver several bodies;
verified bodies need not be repeated within a supported retained session. Reading
an index never acknowledges hidden bodies. Retained-model reuse covers only text
actually delivered and retained for that consumer; saved-file Delta has its
separate artifact proof above. Newly required detail must supply its text even
if the machine already holds it. Context loss recovers the required model
artifact, widening to the full source when its scope is unknown. Historical captures
keep their original obligations. Read receipts prove coverage, not comprehension,
retention, fresh authority or acceptance.

The single-session Agent consumes this same pipeline. In the controller/executor
profile, the controller compares alternatives and absorbs Core bookkeeping;
the executor receives the complete selected work and authorized materials through
the existing host channel. It does not become a Core client or receive every
alternative to compensate for an incomplete brief. Reuse capability eligibility,
work-context/read fulfillment, capture/detail and delta owners; add no parallel
candidate store, Python relevance policy or per-item approval round trip.

Qualify against original quota single-session delivery and the corrected complete
context on matched inputs. Include a decisive constraint at the end of an initially
unexpanded candidate, a useful alternative beyond any display limit, changed peer
evidence, a now-runnable blocker, identical titles with different requirements,
missing metadata, context loss and failed batch reads. Actual choice and subsequent
verified work must honor the constraint; reconstructable JSON or an unchanged
selected ID is insufficient. Count initial input, all detail reads, paging, retries,
recovery, both sessions' calls, latency and human intervention through accepted
outcomes. Keep small-task and cold-selection counterexamples where staging costs
more. Report bytes separately from tokens measured with the actual model tokenizer.

Delivery stays within C1/C2: qualify the shared same-capture reader and required-read
recovery through an actual caller; replace that caller's stage-specific delivery
and obligations together; then qualify retained delta and matched outcomes/full
cost. Reuse [#7560](https://github.com/loopx-project/loopx/pull/7560)'s outcome-scope
work without treating outcome relations as candidate-selection proof. Diagnostic
counter folding in [#7587](https://github.com/loopx-project/loopx/pull/7587) is a
separate compatible slice, not this replacement. Each slice can have its own PR
and rollback; no generic retrieval framework or two-session rollout is required.

The native quota caller now implements a bounded acceptance-material slice:
known cumulative objectives, complete requirement descriptions and current review
states remain in its required view; source history and review notes/evidence are
addressable in the original immutable capture. Its read obligation, full/delta
baseline and recovery move together. Selected detail reads never fulfill hidden
required content or establish a full baseline. Unknown semantics and signed
envelopes remain whole; the disabled path is unchanged. The original capture's
values survive the typed path plan without a JavaScript numeric round trip.
If the required view plus its detail index is not smaller in serialized UTF-8
bytes, retain full delivery and its original obligations. Default detail examples
select source, note or evidence fields rather than replaying the whole outcome.

Native exec and code-mode fixtures also qualify two compactions within one work
Turn: a sufficient working brief continues with the original work/settlement
identity without replaying its packet or re-entering admission. The baseline is
still invalidated; unfinished initial reads and missing necessary facts require
same-capture recovery, while a new work Turn obtains its fresh guard. This is
native protocol qualification with deterministic inference, not evidence of
model compliance or live outcome/cost benefit. C1/C2 remains incomplete:
candidate, preference and Explore staging, signed-envelope migration and matched
accepted outcomes with complete costs still need their own qualification.

### C1 wire-layout recommendation

The target combines quota packet's decision-necessary information with
TurnEnvelope's efficiency and replaces both. “Complete” means complete necessary
semantics, not every duplicated diagnostic in today's packet permanently inline.
The lossless root-preserving candidate below is an intermediate migration oracle:
it exposes omissions and reference breakage while consumers move. Renaming that
candidate or forwarding all old fields does not satisfy the final design.

Inventory each field and instruction clause with its owning rule and consumer.
Keep current task, original requirements and revisions, relevant evidence,
authority, required reads, execution/stop conditions and original settlement
obligations available before their dependent decision. Reuse typed ownership to
remove proven duplicate representations and unrelated diagnostic noise. Stable
material can be referenced or differentially delivered only with verified
retention or a resolvable, integrity-bound source and explicit read ordering;
missing context must recover before dependent work. A reference alone does not
prove that the model received its constraints. Unknown fields cannot be silently
classified as optional or dropped by a prose heuristic.

Within one canonical contract, absorb the useful envelope properties: clear
execution identity and action organization, a single machine-owned binding,
less duplicate instruction text, bounded delivery and reliable detail/recovery.
Full/delta/detail are encodings of that instance, not permanent competing semantic
views. Preserve raw source evidence in its existing owner without emitting a
second executable packet. The final wire layout may change where its mapped
references, consumers and measured benefit justify it; root preservation is not
an immutable product constraint.

| Source or concern | Candidate mapping and qualification |
| --- | --- |
| Current decision fields during migration | Preserve path/value in the lossless oracle; enumerate all modes and classify necessary semantics, proven duplication and diagnostic-only material before the final mapping. Do not infer optionality from one observed packet. |
| Work context, required reads and settlement | Keep their existing `interaction_contract` paths and owners. Existing root-relative references retain their meaning; any intentional relocation requires explicit reference and reader migration. Never rewrite prose or command strings by pattern. |
| Host-operation binding | Compose from the actual caller execution context. Validate consistency with source authority; reject conflicting bindings rather than choosing whichever representation is convenient. |
| Content and transport integrity | Commit the entire semantic instance independently of the former compact action digest. Preserve exact capture-byte verification and original settlement identity as separate checks; changing a transport encoding does not create a new effect. |
| Complete capture and Progressive view | Keep canonical `projection.json` distinct from its derived `required-context.json`/wire view; inventory exact root, revision, digest and pointer mappings. Save complete recovery before presentation; reference only through an authorized usable reader. |
| Saved-file reuse, delivery coverage and retention | Inventory each existing typed owner and its evidence boundary separately. Proposed `confirmed_read_sections`/derived `consumed_pointers` describe observed coverage only; neither is artifact-reconstruction proof, comprehension or a full-read gate for file Delta. |
| Unread discovery | Map the proposed `unread_context_index` to current complete-capture pointers, revisions and source/read obligations, including unchanged unconfirmed material. Rebind changed/unknown mappings; retain explicit unavailable states and permission checks. |
| Historical receipts and readers | Decode the recorded version for outstanding work. A temporary internal adapter may serve an existing effect interpreter, but must not become a second emitted contract or a compact replacement for the model's required input. |

A synthetic layout probe used real isolated File/SQLite quota captures and the
existing TypeScript context codec. Preserving source paths permitted warm delta
encoding; moving all facts under a new `facts` key while retaining the old
`quota_packet` surface fell back to full with `delta_not_smaller`. Both layouts
reconstructed exactly, and an epoch change required full delivery. Retention was
synthetically asserted; this measures codec behavior, not host observation,
model comprehension or outcome/cost benefit. It rejects **unmapped root relocation
under the existing surface**, not nesting in general. A redesigned nested layout
remains possible if its mapping and consumers justify the migration cost.

Therefore C1/C2 must explicitly version the context surface and its fresh/reuse
paths with the wire transition. Do not relabel an old surface or inherit its
baseline by Turn identity. The transition first saves a complete compatible
source; the model-facing view may already be Progressive. Reuse requires the
appropriate saved-artifact or retained-model proof above. Qualification includes fields
outside the old action projection, missing baselines, session/epoch changes and
same-capture recovery. This resolves a concrete layout risk; shared writer and
final-consumer replacement remain the next implementation, not another layout
framework or a declaration that C1 is delivered.

The unfinished implementation candidate [#7430](https://github.com/loopx-project/loopx/pull/7430)
now composes a complete source in the existing TypeScript owner before both
quota and native Turn capture, and passes it to actual Codex stdin. Isolated
File/SQLite CLI tests with a synthetic executable cover source integrity,
failed-attempt recovery and once-only settlement. This advances the two-writer
and final-input replacement; it does not qualify C1 cutover. The candidate
now routes capability reentry through the canonical CLI channel and preserves
original hash-bound review/writeback readers in complete-contract Codex input.
Synthetic File/SQLite execution qualifies those readers for fresh obligation-bound
and prior Todo-bound replans. Native launch now derives its one causal binding
from the recorded settlement identity; an observed semantic replan obligation
cannot replace an existing Todo binding. Source-quota and health refusals still
prevent host launch. Release fixture setup now imports its final source once;
these executable checks do not qualify a real model or installed host.
The structured-host renderer now shares complete-input transport with Codex,
including original hash-bound material readers. Actual isolated File/SQLite
Turn-to-DSH runner paths receive that same captured source; historical request
prompts retain their exact content. Structured DSH still has Todo-scoped
sessions: an obligation-only replan is rejected before dispatch, rather than
inventing a Todo binding. Full-source input deliberately retains inactive
capability configuration and diagnostics; those facts do not activate recall
instructions or reflection result fields. This is a disclosed presentation
change, with activation and historical parity checked separately.
The Markdown quota entrypoints now render the same complete contract rather
than selecting a second subset of facts. Actual File/SQLite CLI captures retain
every field and instruction under both the ordinary and former envelope flag;
JSON types and arbitrary source text survive rendering. External-worker
File/SQLite admission, unresolved-attempt recovery and restart checks pass with
the complete producer output, but an opaque shell wake still does not prove
delivery of that original contract to an executor model.
The real benchmark checkpoint preflight now verifies complete source equality
for File/SQLite, replan/non-replan and context delivery on/off. Its optional
full recovery retains that same source, with one initial admission and no
reader writes. This qualifies transport to the checkpoint's model-input file,
not native session capture or a benchmark/model outcome. A matching outer
source hash alone previously admitted an unsupported version or invalid inner
binding through `replan-view`; the reader now uses the existing complete-contract
validator, while unversioned historical reads retain their decoder.
Successor authoring is now bound to the already-decided Turn before complete
capture, using the same formatting owner as historical staged reads. The complete
contract includes original Turn/Goal binding and the obligation to finish the
creation receipt's original settlement before ending, without executing the
successor. A later modern material read does not append another binding. Actual
File/SQLite App-heartbeat and generic-CLI tests create the successor from this
inline contract, reject premature spend, settle the original identity once and
leave the successor open, without another guard or a staged reader dependency.
This changes complete-input successor guidance; historical unversioned reads
retain their old behavior. Settlement-only, capability-intent, unbound and
mismatched-context inputs retain their authoring unchanged. The checkpoint's
legacy-reader tests alone do not establish this lifecycle result. Remaining
material-reader retirement and other C1 consumers must not restore a second
compact executable fact view merely to satisfy historical assertions.
The complete-contract boundary now rejects unknown, empty or mistyped root
execution actions, using the existing decision/frontier vocabularies both
before capture and on later reads. Valid hashes do not make unknown execution
semantics supported. Optional evidence remains intact, including unfamiliar
versions and action-like field names; historical unversioned action routing is
unchanged. This qualifies the root action boundary, not every required semantic
field. A real File/SQLite native-CLI producer fault is rejected before capture,
dispatch or spend. The preceding candidate reached a later launch refusal;
these probes do not show prior unguarded execution. Selected-work tests now consume the complete interaction context and
use the actual native `turn plan` entrypoint for host input. Real File/SQLite
checks retain full task text, canonical notes, scoped gates, fresh requirement
reads and source-loss recovery without rebinding an App capture to a native
host. The six earlier failures at obsolete root-layout assertions remain
historical failures; their downstream semantic checks now execute.
The six earlier staged-reader failures are retained as historical evidence.
The complete composer now reuses Explore's existing applicability correction
before capture: a proven pure replan without a Todo does not advertise the
Todo-bound result attachment. Evidence, independent graph commands and all
before-work obligations remain; unfamiliar/custom instructions and ambiguous
bindings stay intact. Supported Todo-bound attachment authoring remains fully
inline. This is an applicability correction, not an authority change or a new
compact fact view. Real File/SQLite native host inputs qualify both bindings.
The staged tests now consume the complete CLI input and exercise the historical
material-reader codec against that same immutable capture, including a hostile
PATH, unavailable registry, tampered identity/acceptance and the actual writer.
They compare all successor-binding instructions rather than excluding fields
that the complete input now owns. The attachment refusal fixture supplies a
valid explicit finding status so it reaches the real missing-Todo identity gate.
All 23 staged checks and four native host cases passed; these overlapping scopes
do not establish full consumer retirement or packaged-host qualification.
The native context fixture now binds both CLI spellings to `complete_turn` and
compares reconstructed facts with the original captures. Its four initial
failures came from constructing the restoration oracle with retired surfaces;
a Unix rendezvous check had masked the same mismatch by accepting full fallback.
The corrected checks require the original target and actual delta mode. Ten
cases pass through real Codex exec/app-server/code-mode and worker resume with
isolated SQLite and deterministic loopback inference, including compaction,
process resume, replaced history and an unqualified replay profile. No paid
model or benchmark experiment ran. The current full quota-settlement CLI module
passes 158 checks, including the historical cache-parity case; the earlier
intermittent mismatch is not causally explained or relabeled as a pass.
The File/SQLite context-to-DSH subprocess checks now use the complete capture and
production request builder instead of treating a current CLI response as a legacy
action projection. Normal capability-on/off inputs retain every source fact;
bound replan uses the production capture binder and executes the delivered
original-evidence readers. Altered source facts, schema versions and replan
obligations reject before the runner seam. The explicit historical reference
reader remains covered. Eight focused cases pass with an injected recorder,
not a live DSH model; runtime code is unchanged by this test migration.
The complete-contract boundary now also reuses the existing interaction decoder
for its required version, channel types, notification policy and execution
consistency. Four negative cases previously passed composition despite invalid
semantics; they now reject even when a producer supplies correct integrity
hashes. The original object and unknown optional facts remain intact rather than
being replaced with normalized decoder output. File/SQLite native input checks
reject an unsupported inner version before Codex prompt execution or DSH runner
loading, without spend. The complete-contract/context/DSH/stdio selection passes
352 cases; TypeScript checking and focused lint pass. This qualifies these
interaction semantics, not every required field or packaged-host adoption.
Heartbeat templates now own the ordered settlement instruction; exact-Turn
binding adds only its canonical source locator, instead of repeating the
execution and no-static-reconstruction clauses. Thin prompts combine complete
capture and required-context reads before action, while sharing the unchanged
progress/outcome clauses. On the unchanged full-route fixture (320-character
scope, explicit CLI and five capabilities), the unbound prompt falls from 4,981
to 4,895 budget characters under the original 4,900 ceiling. The exact-Turn
variant falls from 5,198 to 4,987 and still fails that ceiling. The padded-route
negative remains rejected; no route, capability, fixture population or limit
was shortened. The prompt/platform selection passes 115 checks with one skip.
These are presentation and clause-retention results, not model adoption or cost
benefit. Four other original CLI-budget failures remain open. Integration with
opaque reader references also exposed two obsolete digest-field assertions;
their File/SQLite consumers now read the pinned machine descriptor, preserve
the original receipt and reject byte-changed captures without admission or spend.
Remaining cache consistency investigation, other old reader expectations, staged instruction parity and
actual optional/external consumers, version admission and packaged host
continuity remain unfinished. Operation-SDK checks without an explicit native
runtime are untested, not qualified. The default writer change remains
unshipped. The candidate is a normal review proposal; whole-C1 adoption and
original-quota net-cost qualification remain open. No model or whole-cost benefit is
established. Historical effect decoding is temporary; it must not become a
second permanent executable view.

### C1 caller replacement and cutover unit

The cutover unit is a supported entrypoint **and its downstream consumers**,
not an output flag. Source inspection at `99921829f2c5` identifies the following
live seams. This supplements the earlier baseline audit; it is a migration
design, not a claim that these paths have changed.

| Live seam | Concrete replacement and retained owner |
| --- | --- |
| `quota.py::_emit_quota_result` / `_project_quota_cli_payload` | Compose the complete contract before capture; replace the ordinary/envelope execution-format branch. Retain admission, capability hooks, capture IO and host poll receipts with their current owners. A diagnostic status view is not an executable contract. |
| `turn.py` after `build_fresh_turn_decision_owner().resolve()` | Call the same composition owner with the actual scheduler execution context. This entry calls `build_turn_envelope` directly; changing quota CLI emission alone does not migrate Turn. Retain the Turn plan, journal and receipt executor. |
| `heartbeat/task_body.py::render_thin_heartbeat_task_body` | Replace the `captured_envelope` instruction split with one contract-reading instruction. Preserve exact selection/reentry transport, notification independence, required reads and original settlement. The saved bootstrap still resolves current instructions on wake. |
| `turn_driver/codex_cli.py::_prompt` and `host_candidate.py::extract_turn_authority` / `render_prompt` | Replace format-specific model inputs at the actual prompt boundary. Today ordinary Codex receives the envelope, staged replan uses extracted authority, and structured hosts use extracted authority. Shared source bytes or signatures do not prove these final inputs preserve the same obligations. Keep provider result encoding and outer effect ownership. |
| `turn_driver/decision_capture.py` and `cli_commands/quota_capture.py` | Reuse immutable capture, validated readers and full/delta delivery. Fresh delivery and saved-capture recovery must reconstruct the same contract; do not reuse a continuity baseline for a different projection merely because its Turn identity matches. |
| `benchmark/runtime/worker.py`, `benchmark/runtime/sessions.py` and `scripts/external_scheduler_worker.py` | Migrate the product entry they actually invoke. Keep evaluation setup and outer wake ownership; do not add benchmark-only conversion or count a managed-host test as attached-host acceptance. |

The proposal's [private-plan checkpoint](../ledger/controller-executor-unified-contract-v0/2026-10-30-c1-source-convergence.md#current-private-plan-boundary)
migrates the complete-source Turn writer and its capture, Host, CLI resume,
native conversation, capability, child-result and journal consumers together.
The [quota and managed-controller cutover](../ledger/controller-executor-unified-contract-v0/2026-10-30-c1-source-convergence.md#current-quota-and-managed-controller-cutover)
also removes normal presentation's v0 builder branch and the managed preview's
transient v0 producer. Both current routes consume the existing complete-source
owner; original-version readers remain explicit. A one-owner missing-material
drill reaches both real CLI entries. This is an unmerged source proposal;
installed adoption, remaining supported readers and original-quota accepted
outcomes with complete costs still keep C1 open.

The [explicit inline caller checkpoint](../../../reference/contracts/interface-budget-contract.md#canonical-cli-fixture-qualification)
qualifies ordinary quota, its retained flag/detail spellings and full transaction
inspection against their registered v1 carriers. The complete off/Markdown
source and native/structured Host input remain intact; historical v0 readers
retain their original-version boundary. Matched source, File/SQLite and long-route
evidence justify revised presentation allowances, preserving the original
failures and all line/growth/authority limits. This accepts additional inline
cost, not an efficiency win, installed adoption or C1 closure.

Reward Memory participation now reads the typed capability facts once when it
registers recall and reflection on the native quota and complete Host routes. It
no longer requests an action signature to decide participation. The verified
Goal/Agent binding, provider policy and original Host source still gate delivery.
`capability_context.ts::contextSource` still signs native quota contributions
against the current work and settlement; retirement of that live branch must
preserve registration, readback and original-version recovery together. The
shared capability namespace guard reserves `work_contract` and
`source_decision_hash` across registration, context and optional result fields;
rejected contributions leave the original Host source usable.

The optional Host projection now uses that existing TypeScript owner for ordered
merge, collision rejection and per-contribution isolation.
Python extracts the governing source once and sends one batch; provider results
and historical capture/journal readers keep their recorded format. The
[merged main slice](https://github.com/loopx-project/loopx/pull/8451) compares
File/SQLite empty, one/two-provider, current/historical and near-limit cases with
unchanged projected advice. Two-provider complete-source RPC falls from four to
two in that bounded stage. Large batches reuse the existing private snapshot
transport, adding temporary IO and a measured near-limit latency penalty;
transport acceptance is extended without raising provider budgets. This is no
model-token, whole-task cost or accepted-outcome claim. Native quota signature
retirement, installed adoption and original-quota qualification remain open.

The [optional-context source follow-up](https://github.com/loopx-project/loopx/pull/8455)
keeps source-free status readback independent of executable-source extraction,
while declared complete native sources pass the existing complete-decision owner
before participation facts or guidance are read. A damaged binding, unsupported
version/codec or changed retained field cannot fall back to the raw-quota signature.
The recall observation is separate from the signed governing payload; enablement
must not rewrite that source merely to attach provider readback. Registration,
namespace, slot and budget checks remain. Valid guidance and recorded wrapped Host
readers keep their binding and canonical settlement; invalid sources retain status
observations but cannot deliver advice. This adds native complete-source validation.
The earlier status-only RPC reduction does not qualify model-window or whole-task
benefit, and native signature retirement remains open.

Integration with main `5988be01` retains the shared quiet-completion rule and
keeps the retired alternate heartbeat body deleted. At that checkpoint,
long-scope thin and complete-capability CLI fixtures exceeded the retained
5,100 presentation budget, and the captured long-route Agent JSON remains above
its 5,400 ceiling. A provisional presentation allowance increase was rejected
because it weakened an existing oversized-scope negative and left the independent
JSON failure. Those failures and the rejected trial remain historical evidence;
the original limits continue to govern the caller repair below. Independent
review and original-quota outcome/full-cost qualification remain open.

Selected Todo notes now adopt the [main field registry](https://github.com/loopx-project/loopx/pull/8386).
C1 removes its separate note rule and duplicate native regression. The shared
identity binds the Goal, registry/runtime route, canonical provider and selected
Todo; a fresh evidence update does not invalidate an unchanged note body. The
native CLI regression covers complete/partial delivery and an evidence-only edit
to that same Todo. Current task and authority facts keep their existing source
and reader; missing observed coverage, changed text, a different Todo or context
loss returns the full note from the same capture. C1's checked exact write-scope
and Vision instruction reuse remain separate fields of the existing registry.
This consolidation does not qualify model retention, installed adoption or
original-quota accepted outcomes and complete costs.

Failed-session retry is also a live reader. In this proposal,
`session_recovery.py::assess_failed_turn_retry_request` gives the binding callback
the checked complete decision for a v1 request. A recorded v0 request retains its
original envelope, including when it has a complete-source sidecar. Invalid or
mixed current input fails before provider IO; the existing lineage owner still
checks the returned binding. File/SQLite qualification must use a callback that
reads its argument, preserve capture/journal bytes on refusal, and resume the
original Session without duplicate Host results or settlement effects. This
reader repair does not establish installed adoption or complete C1 retirement.

Before replacing any seam, its implementation PR records source path, field or
instruction clause, current readers, target owner, representation, recovery and
retirement condition. Relocation must include reference resolution; an omitted
diagnostic needs evidence that it carries no action, authority, required read or
acceptance obligation. Unknown data cannot silently disappear. Unknown required
execution semantics cannot be made safe merely by forwarding their JSON: the
dependent operation stays unavailable until a compatible consumer understands
them. Do not classify obligations by substring matching.

Use **one explicit version transition**, not automatic format negotiation per
wake. New admission for a migrated binding requires compatible composition,
delivery and consuming readers. An unsupported consumer receives an actionable
unavailable result before dispatch, without creating a second live writer.
Already admitted work continues its recorded version and original receipts;
retain its historical decoder until that work and the supported recovery window
are reconciled. Rollback likewise separates outstanding work from new admission.
A version transition does not rewrite saved prompts, journals or source hashes
as if they had always used the new contract.

Retirement must distinguish reading recorded evidence from resuming execution.
An old native pre-claim capture without a verified live-guard binding remains
read-only even while its original lease is active. Recover its original bytes,
claim receipt and current lease first; an eligible current check cannot make
that old capture prove a new claim. Repeated hooks, restart and a different
ambient session do not supply the missing binding. A recorded ready result does
not renew a released, expired or replaced lease.

| Retained record | Recovery obligation | What the record does not establish |
| --- | --- | --- |
| Immutable capture | Read the original version and digest-bound bytes, including its original Turn. | Current execution eligibility, model retention or permission to replace the source. |
| Managed claim receipt | Read the original operation and its result without deriving another claim identity. | A new claim generation or renewed ownership. |
| Current lease | Read the actual owner, generation and validity through the existing lease owner. | A verified native capture binding merely because the owner matches. |
| Already recorded ready context | Keep its original source and recovery reader available. | New dispatch, automatic re-claim or another settlement/debit. |

A source rehearsal uses the real producer `172d80d630f4`, then main
`5f132dd66585` and candidate `f42dfd592e4f` as readers. Each reader passes 14
File/SQLite cases covering interruption before readiness, recorded readiness,
active/released/expired leases, and changed ambient/native sessions. Repeated
public hooks and restart preserve the original lease generation, claim receipt,
receipt cursor, capture bytes and rollout log; the current reader can recover
the original Turn and Todo from that old capture. These are isolated synthetic
source journeys, not installed Host adoption, model execution or full journal
recovery qualification.

Qualify this boundary with captures and receipts produced by the supported old
release, not only old-shaped fixtures created by current code. Reconcile the
remaining admitted work, actual installed readers and recovery window before
removing its decoder. Safe read-only recovery alone does not qualify automatic
upgrade of that execution or retire its last reader.

Code convergence is part of each migration slice, not only the final C9 cleanup.
Both entrypoints must call the same semantic implementation for composition,
reference validation, context reconstruction and settlement. Keep genuinely
host-specific launch/observation mechanics in thin adapters; do not force their
different lifecycles into a new general-purpose runner. The existing typed owners
remain responsible for their respective decisions.

For each slice, record the before/after callsites, shared owner, removed duplicate
algorithm and remaining compatibility caller with its removal condition. Qualify
both routes with the same independently authored semantic cases and their real
transport/recovery tests. A wrapper that only delegates can be transitional;
copying a rule into a newly named common module while leaving two implementations
is not convergence. The next change to a shared rule should be made and tested in
one owner and reach both routes. Do not make declining line counts an acceptance
metric or remove unique host coverage to meet one.

The first cross-entrypoint C1 cutover must include the two production composition callsites above and
at least one complete producer-to-final-input journey, with an explicit list of
remaining readers. Qualifying that bounded slice does not enable the new writer
for an unqualified host or close C1. The final cutover removes active format
choice only after all supported readers qualify. Temporary decoders have named
callers and removal conditions; they are not user-facing compatibility views.

A Turn lifecycle extraction may be reviewed separately under the parent C1
proposal when it includes planning, final Host input, capture publication,
journal inspection, original-session recovery and settlement consumers together.
The migrated Turn uses one complete source and its checked readers; the ordinary
quota writer and thin-heartbeat presentation retain their existing contract in
this staged boundary. Shared projection changes must preserve those unmigrated
callers. The historical v0 decoder serves recorded plans, requests and journals;
it cannot rescue an invalid supplied v1 source. Its removal still depends on
reconciling outstanding work and the supported recovery window.

This extraction is partial C1 delivery, not qualification of the cross-entrypoint
cutover or a context/cost improvement. The parent retains ordinary quota and
heartbeat migration, final-input/material-read coverage, all-host configuration,
and the original single-session outcome and complete-cost comparison. Review
must include matched baseline/head real-provider outcomes, rejection sensitivity
and packaged recovery. Adoption reduces the parent's diff only after the slice
lands on main and main is integrated into the parent; opening a PR does not.

For each normal, selection, replan, wait, recovery and capability-off fixture,
check independently authored obligations at **three boundaries**: composed
contract, reconstructed delivery, and final model input plus required material
reads. Include a source change outside the old action signature, a missing
required source, an incompatible consumer and a lost settlement response.
Reconstruction equality alone misses prompt-layer omission; a complete prompt
alone misses stale authority or a duplicate debit. These are integration
requirements, not authorization to run a model or launch an experiment.

1. Inventory actual producers, readers, writers, saved prompts, captures and
   external integrations. Map each old field and prose obligation to its retained
   owner/field, authoritative derivation or explicitly approved removal. No
   blanket claim of “same facts” suffices. Keep original failure fixtures.
2. Qualify the unified contract in single-session execution through real quota,
   thin-heartbeat and Turn entrypoints. Switch one writer per binding; old
   readers are explicit version-pinned migration adapters with an identified
   caller and removal condition. They do not become permanent product views.
3. Add role-aware sessions, shared lifecycle reuse and the opt-in two-session
   profile. Reuse existing same-session/native-resume and effect recovery code.
4. Adopt the shared product profile in benchmark workers and the packaged App
   heartbeat journey. Each host proves its own observation, stop and recovery
   boundaries. Neither a CLI test nor a model simulation qualifies installed App.
5. Remove old producers, format selectors, duplicate renderers and dual-format
   tests after the last supported active reader migrates. Keep only historical
   version decoders for supported receipt replay. End-state acceptance requires
   one emitted contract and no choice between quota and Turn packet formats.

Rollback disables new admission for the affected profile, drains or interrupts
its attempts, reconciles receipts and restores the prior qualified profile on
the next admitted work. Do not silently replace native sessions or reinterpret
pending receipts. Before field/decoder removal, demonstrate export, old-version
readback and downgrade behavior; retain the old reader until the supported
retention/upgrade boundary is satisfied. No destructive migration is authorized
by this document alone.

#### Recorded acceptance-index compatibility

The staged Turn extraction keeps recorded unversioned/v0 replan reads on their
original count-only acceptance index and 12 KiB index allowance. Complete v1
reads retain current owner/work facts and grouped exact requirement states,
with full original outcomes available through their digest-bound detail readers.
Grouping is presentation, not independent acceptance evidence. The earlier
rich-index measurement and its 20 KiB candidate allowance remain evidence for
the complete-source path; they do not increase the historical reader's budget.

#### Turn consumer preparation and instruction parity

The separately landed Turn slice left the ordinary quota producer on its recorded
contract. This parent C1 proposal also migrates the quota/heartbeat producer;
recorded requests retain their original version-specific reader. Current Turn planning consumes
its complete source directly and publishes its own immutable capture; pairing a
newly composed source with an older executor envelope is rejected.

In this C1 cutover, native selected-task preparation sends the same checked
complete source directly to `StewardTurnHost.prepare_native_task` and its
existing validation receiver through `work_contract`. It no longer regenerates
the co-deployed preparation-only v0 adapter. Ordinary lease preparation still
precedes the delivery guard; after preparation the same Turn and Todo are read
again before planning. Recorded v0 requests retain their original recovery
reader; this current-caller migration does not rewrite or re-admit them.
Identity-only preparation uses the existing checked-source lineage operation;
an explicitly supplied adapter or Host header still requires full validation.
Reading identity alone does not construct or authorize a Host request.

Native selected-task execution prepares its ordinary managed lease before the
kernel delivery guard and reuses that proof during post-model validation. A
claim label alone is insufficient, and another execution's lease remains
unavailable. Source checks precede the independent task argv; rejection and
original-result recovery retain the original session and once-only effects.

Complete Turn capability advice and its optional Host result fields bind to the
checked source content hash and Turn key. Changing a selected Todo's required
capabilities invalidates that advice even when the legacy action projection
matches. Publishing the same decision under a capture address preserves the
binding. On retry, Session resolution, Host input, cached-result task validation
and settlement use the journaled plan. A journal with an already validated result
can still read its original
bounded result schema under the historical binding. A new Host invocation
requires the complete binding to deliver that advice. Ordinary quota and
historical v0 retain their existing decoders.

Modern Codex and structured Host instructions share the existing audit read
rule: read a declared audit before judging Goal acceptance or proposing
closeout, including when the Goal stays open. Notification policy does not grant
or cancel execution authority; supplied delivery, attempt, quiet-noop, execution
and orchestration constraints still apply. Core retains state, quota and
scheduler effects. A complete contract can declare a pending original material
read: publication does not fulfill it, and tests must execute its reader and
preserve the original body and stops.

The disposable research-team launcher and initializer locate the actually loaded
Core together, including an isolated wheel, while retaining exclusive runtime
creation. Source and packaged consumer checks are distinct from installed App
adoption, live optional-provider/model behavior and original-quota complete-cost
benefit. These repairs do not close C1 or authorize rollout or merge.

The separately landed Turn slice qualifies the following inspection and
recovery boundaries; C1 retains its own version-bound caller measurements.

The default JSON Turn inspection retains the current route, permissions, stops,
selected work and executable Vision authoring. Supporting evidence and diagnostic
body remain addressable in one immutable, digest-bound capture of that same
complete source; a later live Goal change cannot redirect this read. An
unavailable private capture falls back to the complete source. The projection is
inspection-only and cannot be promoted into execution or source admission. This
retains the existing 26,000-character crowded default ceiling. Explicit v1
transaction detail carries the full source plus transaction and session metadata;
the matched small fixture measures 26,886 characters against the retained v0
15,000 failure. Only that declared v1 detail has a 30,000-character ceiling.
The frozen differential caller uses each checkout's supported Host argv and
recorded plan schema; ordinary quota remains unchanged in this extraction.
The matched migration observes route, actor/work, execution duties, scope/stops,
required reads, authoring and effects. The old Goal-file read and the new complete
inline file must have the same full-file digest. The observation records every
inspection-detail reader call and byte separately. Only this qualified v0-to-v1
transition permits 2,000 additional default characters/bytes or 16,000 for full
transaction detail; subsequent v1 growth and absolute ceilings remain guarded.
This is migration qualification, not a net context-efficiency claim.

Read-only capture publication uses the existing cross-platform private-IO owner:
new files receive private POSIX permissions or a protected Windows DACL, while
an unsafe existing transport directory is rejected without repair. This does
not enable native continuity or change its platform qualification. Native
Windows producer coverage is retained separately from local POSIX execution.

Failed Session retry also delivers its callback input by the recorded request
version after the existing complete-source check. Current requests supply the
checked complete source; historical v0 supplies its original envelope, including
when it carries a complete sidecar. Corrupt or mixed input is rejected before
provider lookup. Real File/SQLite retry checks exercise callbacks that read their
argument, original Session resume, actor/work mismatch refusal, unchanged saved
sources and once-only Host/result effects. None establishes live model adoption
or the parent C1's original-quota complete-cost benefit.

#### One settlement recipe in the ready first read

The progressive reader omits `next_cli_actions` only when it repeats the
recognized ordered settlement plan for the ready Todo and original effect.
New ordinary, progressive and native complete sources retain the same shared
v1 plan, including its writeback and spend owners, original commands, ordering
and preconditions. Delivery flags no longer select a second settlement policy.
The explicit `refresh-state --settle-turn --settlement-receipt` operation remains
available under its existing authority and recovery contract; a presentation
switch does not add it to the plan. Recorded v2 captures retain their original
recipe and identity through the checked historical reader, without being
rewritten or regenerated. A distinct fallback or unknown plan remains visible;
the exact original field is still readable, and direct output is unchanged.

This cutover removes the candidate-only settlement switch and prediction
parameters from capability binding, the unused native settlement-projection
operation, and the unpublished compressed-source bridge. Composition uses its
existing literal-source transport and typed settlement owner. Stale adapters
that supply either retired switch fail explicitly; historical capture decoding
is separate. The real File/SQLite regression compares off/on/auto and the bounded
legacy display under one original Turn, then checks unchanged captures and no
debit. Explicit combined-command success, partial failure and once-only replay
remain independently exercised. The earlier one-call saving is withdrawn from
the current candidate; model cost and full C1 acceptance remain unqualified.

#### Complete quota consumer qualification and needed-section recovery

The fixed 98-row main `36289306` / C1 `94cd16bb` comparison retains its original
28 failures. The updated consumer checks the complete v1 Markdown carrier and
19 matching original current-control values before applying bounded, one-time
carrier growth allowances. Same-version growth and changed/missing controls
remain guarded. The new qualification has no mechanical row failures and 28
explicit semantic/cost review rows; the producer's measured stdout costs are
unchanged. See the [owned budget decision](../../../reference/contracts/interface-budget-contract.md#complete-quota-carrier-differential-qualification).
This control subset is not complete-source parity, model adoption or a net
efficiency result, and does not close the parent C1 acceptance.

The candidate now migrates the ordinary Markdown `--turn-envelope` caller:
omitted delivery and `auto` use the existing complete-v1 Markdown reader,
matching ordinary JSON and Markdown without that flag. The format no longer
selects the bounded v0 view. `compose_quota_cli_source` calls the same typed
composition owner, captures its result once and passes it unchanged to the
existing renderer; no new writer, decoder or settlement rule is added.
The ordinary Markdown settlement check executes the delivered commands on
isolated File and SQLite: premature spend and unsupported progress are refused,
durable writeback precedes spend, and replay leaves one debit. The native Codex
profile still requires JSON and its capture destination; this ordinary-reader
migration does not make native Markdown an executable transport.

| Reader boundary | Candidate behavior and retirement condition |
| --- | --- |
| Ordinary Markdown, omitted delivery / `auto` | Complete-v1 block, with every captured field and the original binding. File/SQLite CLI checks compare its complete decoded content with its immutable capture and recover that same source without admission or spend. |
| Explicit `off` plus `--turn-envelope` | Retains the bounded compatibility display from the same composition. This explicit caller still owns the v0 display adapter; it is not removed by the ordinary-reader migration. |
| Native and managed handoff | Keep their checked complete-source presentation and original recovery contracts; a Markdown format does not downgrade a handoff. |
| Recorded v0 captures, plans and requests | Keep their pinned integrity/recovery reader until outstanding admitted work and the supported recovery window are reconciled. An invalid new source cannot select this fallback. |

This changes the candidate's default Markdown format contract. A complete body
can be materially larger than the previous action summary; the existing
same-capture progressive reader remains the bounded material-delivery path.
Do not call reader retirement a stdout or model-cost saving. Installed host
adoption, independent exact-head review and the original-quota single-session
outcome/full-cost comparison remain open. Explicit off and historical recovery
are separate supported boundaries, rather than evidence of an active second
decision owner.

Model-behavior qualification reads an explicitly supplied complete source with
its existing version/integrity owner; an experimental arm label does not select
v0 field instructions. Both schema and confidentiality boundaries finish before
either actor call. Retained-capture loading preserves the original binding and
numeric representation, while invalid new sources never fall back to historical
readers. The [independent mainline slice #8468](https://github.com/loopx-project/loopx/pull/8468) retains main's raw/v0 corpus and
actual-default portfolio; C1's candidate default remains part of this proposal.
Local API and scripted-provider validation do not qualify model adoption or
original-quota outcome/full-cost benefit.

Thin bootstrap now routes selection/replan and missing details to the returned
read/recovery instructions, instead of copying a second field-layout and
section-selection recipe. The existing Core work-context instruction owns
required reads and freshness; native navigation owns needed sections and
same-capture recovery; the supported v0 capture reference retains its integrity
and recovery instructions. No new decision owner or reader format is introduced.
Binding a Turn timestamp preserves the common settlement dispatch; neither view
owns a second settlement recipe.

| Obligation | Delivery after the bootstrap change |
| --- | --- |
| Before dependent work, read applicable goals, acceptance, constraints and stops | Bootstrap keeps this ordering; Core still delivers current sources and pending required reads. Summaries cannot replace full requirements. |
| Selection/replan, missing or truncated content/commands and detail references | Bootstrap routes these needs to the returned instructions. Native navigation selects necessary sections; bounded references recover omitted context from the original source. |
| Recover lost context without new admission or repeated effects | Bootstrap preserves the original capture/identity and forbids re-admission for missing details. Native navigation preserves Turn/capture/effect identities; historical references require integrity checks and prohibit blind guard replay. Actual changed authority retains its existing freshness checks. |
| Explicit full reads, capture/handoff flags, ordered settlement and ambiguous effects | Existing duties remain in the bootstrap and the same returned contract; timestamp binding does not replace their source. |

Matched complete-capability CLI inputs now fit the unchanged 5,100-character
limit: dynamic Turn 5,293 → 5,087, bound Turn 5,160 → 4,954. Longer-route negative
inputs remain over budget; scope, capability declarations, executable routes and
the rest of the body are preserved. File/SQLite worker checks exercise delivered
Core instructions, supported bounded references, selection/reentry, original
capture readback and once-only settlement. Reader checks retain digest, missing
pointer, truncation/paging and same-capture recovery failures. This qualifies the
caller boundary, not natural-model understanding of the referral. Neither wording
nor successful fixtures prove retention or authorize fresh admission.
Original-quota single-session outcomes/full costs, real caller retirement,
maintenance qualification and independent review remain open before dual runs.

#### MCP final-consumer boundary

Complete-v1 MCP delivery validates the original source with the existing complete
contract owner and numeric transport tokens. It sends the exact CLI JSON as the
first native text block, with the existing `host_mcp_interaction_v0` channel in a
separate block. The string structured result still carries that same original
source. The shared Claude/Kiro/Kunlun MCP adapter owns this transport; it does not
add a decision schema, change admission or re-sign a host-modified source.

For this consumer, the complete-v1 branch that inserted `mcp_channel` and removed
CLI settlement fields after binding is retired. The full original settlement
recipe and identity remain source facts. The host channel specifies that
`complete_task` executes that recipe instead of a second manual refresh/spend;
independent replan still follows the live original CLI actions. Unversioned and
recorded v0 inputs retain their compatibility projection. An invalid declared new
version, binding or numeric representation fails before successful MCP delivery,
without falling back to that historical branch or requesting fresh admission.

Real stdio checks cover both `should_run` and `list_todos`, File/SQLite authority,
unbound and bound Turns, exact source text, original settlement identity, and
invalid-source refusal. Completion/recovery checks separately protect claims,
writeback and once-only spend; a successful read is not settlement. Main's poll
recovery qualification is reused: a supported v0 display may differ from the
complete captured source, while its reader must recover that original source
without a second poll or Host dispatch.

This is final-consumer integrity qualification. Restoring complete source content
can increase bytes relative to the invalid pruned response; compare total MCP
content and structured result, not the first block alone. Natural-model adoption,
original-quota outcome/full-cost benefit and installed rollout remain unqualified.

### C1 baseline boundary and reuse inventory

The baseline is the **original caller-visible quota delivery**, not the raw
internal decision. In `cli_commands/quota.py`, the original emitter captures the
source, optionally applies `replan_cli_view` when a capture exists, then applies
`compact_quota_should_run_cli_payload`. Native context delivery adds its own
full/delta transport afterward. Record the exact revision, host flags, capture
availability and detail options for both arms. Comparing a compact candidate
against the larger internal source would overstate savings; comparing a full
candidate against only a deferred entry would omit baseline follow-up costs.

The existing paths already contain useful efficiency behavior. C1 should absorb
its qualified semantics into the shared TypeScript composition/context owners,
then retire the old caller-specific producer. Do not call the whole legacy
compactor as a shortcut: exact sharing, omission and deferred reads have different
information and failure consequences.

| Existing information / transformation | Fusion disposition and decisive check |
| --- | --- |
| Selected work, original requirements, acceptance, evidence, permissions and stops | Available before the dependent decision. Keep distinct revisions and contradictory evidence; neither lane labels nor action signatures establish completeness. |
| Identical acceptance copies, nested vision audits, authoring and optional guidance | Prefer one complete in-contract owner plus an exact reference where smaller. Compare JSON types and every clause; different/missing owners remain distinct. No extra read or new decision authority. Existing projection tests provide characterization, not new-consumer qualification. |
| Bounded Todo lanes, shortened suggestions and omitted boundary/history entries | Treat as deliberate omission, not lossless deduplication. Retain completeness/count facts and same-capture recovery; prove the omitted item cannot change the current decision, or load it before deciding. Never inherit the old display limits as semantic limits. |
| Captured replan review, acceptance detail and writeback readers | Separate decision facts from later authoring format while retaining the original identity and all required meaning. Bound reads to the same capture; count every required stage, error and recovery. A missing/stale reference must not trigger a new guard or authorize work. |
| Full/delta/resume delivery | Reuse the existing continuity owner after composition. Establish exact semantic reconstruction and required material availability before action; charge cold fallback and lost-baseline recovery. |
| Scheduler, admission and settlement identities | One authoritative instance before capture; transport must preserve host ownership, original Turn and once-only effects. Repeated representations do not justify weakening their checks. |

A standalone late move of acceptance sharing is not automatically a useful
prerequisite. A controlled source-level substitution against main
`99cfa3ac559bfa237f04f36fb7e926672fea44b2` moved only that rule from the local
CLI compactor into the existing `reference_context` call. On the same synthetic
fixture with 100 distinct requirements, serialized projection input grew from
102,680 to 344,010 bytes; the 102,189-byte caller-visible output stayed equal.
The ordinary path still made one projection call; all-details delivery changed
from local sharing to one call. These are projection-parameter measurements,
not framed traffic, latency, model billing or the integrated C1 comparison.
Retain this negative result rather than declaring a net efficiency win.

The compatibility owner also uses one local JSON serialization identity for
acceptance, Vision-audit and replan-authoring sharing. Python's ordinary value
equality must not turn distinct booleans/numbers, integer/float representations
or signed zero into an alias to another observation. Exact copies still share
one in-response owner, including after key reordering; different facts remain
complete. This repairs source fidelity without another projection RPC. It does
not qualify the unified writer, final consumers or original-quota outcome/cost
benefit.

Repair relocated acceptance aliases at the current compatibility owner without
that extra transport. Existing aliases must follow only inline owners actually
moved by the projection, including aliases visited before their owner. Preserve
complete values, original numeric types, carrier order and existing size checks;
never infer arbitrary reference chains or rewrite saved captures. The complete
C1 composition must absorb this rule at its source-owning boundary and retire
the Python compatibility algorithm with its callers, rather than repeatedly
shipping all acceptance copies across another bridge. Writer/consumer cutover
and the original-quota outcome/full-cost gate remain open.

A fixed-source synthetic replan audit of candidate `895f38fc5` illustrates why
the boundary matters. With stride off and no capture staging, the raw source was
21,461 compact UTF-8 JSON bytes, the original quota CLI projection 20,543, and
the complete candidate 21,738: **+5.82% against CLI**, versus +1.29% against raw
source. With capture staging, entry/review/writeback totaled 21,882 bytes versus
21,821 for the candidate. With `evidence_conditioned_v1`, those staged bytes
totaled 28,558 versus 33,368 for the candidate. These use the existing synthetic
replan fixture and production projection/read functions with a synthetic capture
route; they are not executed model trajectories or an installed-host trial.
Extra acceptance/Explore reads, actual request framing, cache behavior and
outcome differences are unmeasured. The legacy receipt toggle produced identical
data and does not add an independent workload. No net-benefit conclusion follows.

The next implementation slice should first qualify exact same-contract sharing
through composition, readers and recovery, then assess deferred-detail choices
with the matched workload above. Keep cold and staged totals separate and retain
negative results; do not force a byte target by dropping unique instructions.

Report the field/clause diff and its decision consequence together with actual
model input, cached/uncached tokens, added reads/round trips, reconstruction,
latency, failures/retries, verification and human intervention. A smaller first
packet with more total reads can be negative. Judge positive only with preserved
required semantics and comparable accepted outcomes at lower complete cost, or
better accepted outcomes at comparable budget. Mixed results remain a tradeoff;
transport-only passes and unmeasured outcomes remain inconclusive. Set numeric
criteria before running matched experiments, using the existing measurement
contract rather than inventing an arbitrary compression threshold. C1 cutover
requires this quota-baseline comparison; a serializer or byte reduction alone
cannot close it.

Use the [existing measurement model](../long-horizon-harness-benchmark-research-program-v0.md#6-measurement-model).
Include both sessions' input/output/cached tokens, tool and material reads,
planning/replan/recovery, wall time, verification, human attention and provider
cost; unknown usage is not zero. Persist causal attribution without duplicating
cost when an artifact is forwarded. Compare accepted outcomes under comparable
budgets, not only replan counts or executor speed. Freeze criteria before any
separately authorized experiment; this RFC specifies no automatic winner.

For the later two-session comparison, attribute controller reasoning and all
handoff/material, validation and recovery costs as well as executor work. The
break-even question is whether those **added complete costs** are outweighed by
avoided control/bookkeeping work, repeated execution or human intervention,
while preserving comparable accepted outcomes. Account for actual repeated
model consumption even when both sessions read the same artifact; forwarding
one usage receipt must not double-count that receipt. Report workload-specific
tradeoffs and unknown billing/cache coverage. No universal overhead threshold,
compression ratio or per-message token ceiling is proposed. This comparison
does not replace the prior original-quota versus unified single-session gate.
