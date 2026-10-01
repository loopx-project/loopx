# App conversations and reusable asynchronous work delivery

- Status: Accepted; design and work are claimable. Conversation-entry repair delivered; broader continuity and async-inbox acceptance remain open. No new provider, scheduler or authority.
- **Supersedes / closes:** none
- Baseline: `27f0fc93b`, inspected 2026-09-25. Implementation and live acceptance are separate.
- Owners: [overall roadmap](loopx-overall-roadmap-v0.md) R1–R3/G0–G2;
  [semantic handoff](capable-manager-semantic-handoff-v0.md) M1–M3;
  [conversation surface](intelligent-review-presentation-surfaces-v0.md#88-reusable-conversation-work-surface);
  [TS migration](typescript-control-plane-migration-v0.md) T0–T4.
- Evaluation: [steward golden queries](../../product/use-cases/steward/golden-queries.md).
- Language: [Chinese semantic mirror](app-conversation-and-async-inbox-v0.zh-CN.md).

## Decision: make the App the place where work conversations continue

Users should be able to say “接着做，结果给我” / “Keep going and bring me the result”
in LoopX, without finding another terminal, copying context, or ferrying answers
between Agents. Prioritize the installed App journey over Lark. Share semantics
and evidence; qualify each transport independently. Lark remains a supported
adapter with regression coverage, not the prerequisite for an App improvement.

This is more than displaying a remote transcript. LoopX must accept the request,
associate it with the existing responsible work and executor, deliver corrections,
observe real progress, and return a readable result to the same conversation.
The runtime still owns execution. Goal/Todo, lease, quota, acceptance and effect
owners keep their authority. Conversation membership creates no permission.

Five questions organize the experience: is my request still here; who is actually
working; did my correction or stop take effect; where is the checked result; and
how do I come back after failure without starting the work again?

### Managed and attached are different execution relationships

| Relationship | App promise | Required evidence and limit |
| --- | --- | --- |
| Managed | Start or continue an authorized LoopX worker from the conversation; observe and control its actual Turn | Stable request→work→session/Turn relation, effective model/effort, admission, supported interruption, result and restart readback. One execution driver per binding |
| Attached | Connect the existing registered work and session; move subsequent interaction into LoopX while retaining its context and native entry | Verify host/session binding and adapter capabilities. Deliver through a supported live control or inbox/next-Turn path; queued is not adopted. Do not silently create a second session or start a competing driver |
| Unbound or unavailable | Preserve the request and explain the missing connection, authentication, permission or capacity | Offer the existing supported connect/create/repair path; catalog presence is not readiness. Ask only for an actual ambiguity or missing authority |

“Move the conversation” means continuity of future requests, results and stable
links. It does not authorize copying raw host rollout databases, rebinding another
identity, or importing hidden/private history. Use authorized summaries and
artifact references. A native runtime can remain open; it must not become a
second unsynchronized source of work truth. App closure stops observation, not
execution. Stopping a conversation does not silently stop delegated work.

## Product expression: what to borrow and what remains unproven

The [Lorca release post](https://x.com/localhost_4173/status/2103454978220470708)
was inspected on 2026-09-25. It contains a **static screenshot**, not a verified
interactive or recovery demonstration. The screenshot shows a named conversation
list, one dominant conversation, compact delegation/return lines and readable
answer blocks. The [creator's product page](https://lorca.app/zh) describes
single/group chats, local execution, named agents and cross-device continuation.
Those are creator claims; this research did not install Lorca or qualify its
runtime, privacy, correctness or long-horizon guarantees.

The [creator's conversation documentation](https://lorca.app/zh/docs/chats)
claims streaming replies, direction updates after the current model/tool step,
and desktop interruption. Mentions are passed to the Agent to interpret. It
also describes cross-Agent recipients answering in their own conversations:
LoopX must additionally verify synthesis returned to the originating request.
These are documented claims, not observed execution. Do not import a fixed
forwarding limit or conceal tool activity merely to match the reference.

Transfer the hierarchy, not the artwork or untested claims:

- Stable human-readable roles and conversation identity should be easier to scan
  than runtime ids. Keep actual model, host and connection details one step away.
- Give the active conversation and its result the largest useful reading area.
  A concise delegation line says who owns what and links the actual work; a
  return line points to the current result. Do not send users hunting in another
  conversation for a requested answer.
- Put one useful next action beside the relevant failure or decision. Fold
  routine activity; preserve missing authority, stale information and failures.
  Decision notices use the request body, object and evidence rather than a short
  scheduling label. Keep distinct request identities and label bounded previews;
  users inspect the current request before deciding. Steward and Goal details
  share one readable request brief: full body, reason/recommendation and linked
  evidence first; identifiers and declared scope remain in a keyboard-accessible
  disclosure. The compact list label is not repeated as another detail card.
  A summary-only source is labelled as such rather than presented as a complete
  request. Existing fresh-preview, read-only and lifecycle fences still govern
  actions; Markdown presentation cannot infer authority. Provider notices use the
  same content distinction: missing request bodies are explicitly unavailable,
  never reconstructed from legacy action labels or free-form gate prompts.
  Retire obsolete presentation branches rather than preserving old data shapes
  without an active caller or a documented migration requirement.
- Use typography, spacing and restrained state accents from the existing design
  system. Motion explains verified transitions, never invents busy workers.
- Recent-completion previews sort all loaded Goals by recorded completion time
  before limiting rows. Sidebar order, subsequent edits and refresh times are
  not completion evidence. Undated work stays in cumulative totals; deferred
  work and monitor cycles do not masquerade as delivered results.
- Keep creation/connect, direct owner chat and team work discoverable. A simpler
  screen must not conceal unresolved work or reduce permitted owner discovery.

Use public/synthetic data for preview. Review populated, quiet, blocked and
unavailable desktop/narrow views. Retain keyboard access, reading position and
return context. First-screen changes still require the repository's preview gate.
No external screenshot, private incident transcript or proprietary asset is
redistributed by this proposal.

The decision-detail slice qualifies the packaged renderer with synthetic
desktop Chinese, narrow English and read-only sources: readable Markdown,
retained full text, evidence links, disclosure/return focus, one correctly
scoped preview, failed/missing/replaced source fences and inert source HTML.
This is a proposed App presentation improvement, not installed readback or
full GQ10 prioritization. Cross-project selection, at most two recommended
priorities and actual scoped adoption remain in the existing P1 attention work.

### Waiting is part of the conversation

One shared TypeScript activity surface serves manager and Goal conversations,
including the compact overview receipt. It appears when the user sends, before
executor session preparation can block. The shortest journey is send → visible
receipt → observed work or actionable failure → readable answer in the same place.

- Show the latest reported activity and request elapsed time on one quiet line.
  Time measures waiting, not percent complete or proof of active computation.
  View changes retain the request timestamp; recovery uses the recorded turn
  timestamp when available, and omits duration when it is unknown.
- Keep tool/phase events in an expandable recent-activity list. Render only
  upstream observations; never simulate phases, expose hidden reasoning, or
  infer progress from elapsed time. A period without new events has an explicit
  waiting caption, not an invented failure or continually changing animation.
- Before dispatch, cancel only session preparation and state that the request
  was not submitted. After acceptance, existing exact-turn steering/interrupt
  controls own effects; stopping observation is not stopping the worker.
- During a managed Codex Turn, the ordinary composer sends text instructions to
  that exact Turn, without a second adjustment form or another Turn submission.
  Attached-host messages keep their next-Turn queue semantics; unsupported
  managed adapters keep the draft without advertising native steering. The
  idle composer and initial presentation are unchanged. This changes the former
  managed-running Send lockout; LoopX mode retains its explicit delivery choice.
  A lost or mismatched receipt preserves draft and ingress identity, including
  retry after completion or page reload in the same browser tab. The shared
  composer persists the original Session/Turn/text/ingress before dispatch and
  reuses it only for the matching Session and text; restoring the cache never
  sends automatically. Only a confirmed non-delivery permits a new ingress.
  Replaying a delivered receipt reads the current stored transcript rather than
  reusing an earlier snapshot or inserting another local copy of the instruction.
  History keeps the initial user request and later instructions as distinct messages
  even while the initial request is still being reconciled with its stored identity.
  Unavailable browser storage keeps current-page retry behavior but cannot
  promise reload recovery. This tab-local cache is not delivery authority.
  Acceptance means the executor received the instructions, not that it adopted
  them or that delegated/team work stopped. Live adoption stays a release gate.
- The compact receipt and full conversation offer the same controls. Failure
  ends the live indicator, preserves the request/partial answer and names the
  next supported action. A completed delegation still shows receiver adoption
  separately; finishing the manager turn does not complete the delegated work.

Primary-source research (2026-09-29), not hands-on certification of other apps:
[Perplexity Pro Search](https://www.perplexity.ai/help-center/en/articles/10352903-what-is-pro-search)
documents research decomposition and source links;
[Cursor Agent](https://cursor.com/docs/agent/overview) distinguishes queued
follow-ups from steering an active run;
[Gemini Deep Research](https://support.google.com/gemini/answer/15719111?hl=en)
documents a reviewable plan and completion notification. Borrow observability,
control and clear completion boundaries, not provider-specific activity names.
Notifications and durable cross-restart timing remain separate acceptance;
a browser timer does not implement either.

Decisive regression: delay executor connection, cancel before dispatch, retry
through startup failure, then observe a streamed turn while switching between
overview and conversation. Verify stable elapsed time, quiet waiting, exact-turn
controls, preserved partial output and no duplicate submission. Exercise the
packaged UI with synthetic fixtures and qualify the chosen real query separately.

## Current owners and gaps

| Boundary inspected | Existing implementation | Gap to address through that owner |
| --- | --- | --- |
| App free text | `personal-workspace-page.tsx`, `workspace-action-form.tsx` | Retire browser intent classification for Goal creation, Todo changes, assignment and scheduling. All free text reaches Chat intact; explicit controls open typed forms and reviewed previews |
| Chat request and observation | `chat_ingress.py`, `chat_store.py`, dashboard `data/chat.ts` | Existing client ingress/Turn identity and event cursors are assets. Prove response-loss, same-key/different-payload and reload recovery before claiming durable end-to-end entry |
| Collaboration | `control_plane/collaboration/inbox.py`, typed collaboration rules and return-delivery owner | Already Agent-neutral despite legacy storage names. Preserve decision/read/return distinctions; do not build another manager-only inbox |
| Operator inbox | `control_plane/work_items/operator_inbox.py` | A source contract and shared urgency projection already exist; inspect and migrate the real pending/read/ack lifecycle rather than adding another generic wrapper |
| Lark transport | `extensions/lark/event_inbox.py`, `routed_inbox.py`, `inbox_reply.py` and reaction adapter | Provider normalization, idempotent capture, read/processed records and reply recovery coexist with Lark ids/policy. Extract only demonstrated reusable semantics; keep authentication, addressing, provider ids, reactions and message limits in the adapter |
| Execution and control | Existing managed Turn, attached-session/host binding, Chat steering/interrupt | Qualify the exact supported profile. Neither registered nor inbox-acknowledged means running; native steering, next-Turn queue and unsupported must stay distinct |
| Result | Answer-report, artifact/revision, review/adoption and return owners | Read the stored version; preserve source and independent review. A failed report read retries reading, not a new model run |

Lark's readable-answer exit also checks rendered emphasis, not only a matching
Markdown source receipt. Following the [CommonMark delimiter rules](https://spec.commonmark.org/0.31.2/#emphasis-and-strong-emphasis),
the shared inbox reply adapter normalizes a paired
strong span whose trailing punctuation is immediately followed by a word:
`**Done.**Next` becomes `**Done**.Next`. Visible text remains identical; the
punctuation moves outside the emphasis. Inline/fenced code, escaped markers and
link destinations stay opaque. Qualify this with a real post's rendered bold
styles and preserved inline-code syntax, alongside source/thread placement and
idempotent readback. This provider formatting stays in the Python Lark adapter
under the TS migration RFC; it adds no conversation state or decision owner.
App formatting and other Lark Markdown constructs remain separate acceptance.

No new capability is needed for the first repair: this is the existing App
conversation/action boundary, with built-in Chat/runtime providers unchanged.
The shared inbox work belongs under existing coordination/collaboration owners;
Lark remains an extension-delivered provider. Reconsider a public capability only
if a real provider-neutral caller outcome cannot fit those owners.

## Phased delivery and decisive queries

The ordering is evidence-based, not a promise that all phases ship in one PR.
A phase closes a useful user path, including negative cases and readback.

| Priority / phase | Natural query | Useful exit, existing owner and next dependency |
| --- | --- | --- |
| P0 / conversation entry | “解释一下 monitor 的工作原理。” / “Explain how a monitor works.” | The selected conversation receives the complete question and returns an answer; no unrelated monitor/heartbeat preview or scheduling write. Explicit scheduling controls remain usable. First bounded App repair under R1 |
| P0 / connect and continue | “用已经在跑的那个，接着做。” / “Continue with the one already running.” | GQ02 managed and attached variants: one verified owner/binding, retained context, actual dispatch or honest queued state, original-conversation result. Follow GQ01 for genuinely new work; don't force every question into a Goal |
| P0 / durable interaction | “先只看微软。” / “Focus on Microsoft.” | GQ07–09 on the same work: steering adoption or explicit next-Turn queue, scoped stop, reconnect/reload/restart recovery, no duplicate execution or lost result |
| P0 / small team | “组个小队，把分歧查清楚。” / “Get a small team to resolve the disagreement.” | GQ05/GQ11–13: 2–3 real workers, two cycles, dependency consumption, independent review, revision adoption and original-route synthesis; no manual copying |
| P1 / attention and transfer | “这周先做什么？” / “What comes first this week?” | GQ06/GQ10/GQ14–15: evidence-based priorities, materials and scoped replanning, model/cost constraints retained; routine progress quiet, requested results returned |
| P2 / breadth | “本机安排，云上跑。” / “Plan here and run in the cloud.” | GQ16/GQ17 retain separate cross-host and scale qualifications. Start only after the bounded local journey passes |

The first fix is not full GQ01/GQ02 or autonomous team acceptance. Continue the
existing creation/connect, conversation reliability, affinity handoff and
small-team Todos; do not create duplicate planning queues. Packaging/first-use
checks run with each usable phase, not at the end of an architectural rewrite.

### Nearest user-visible exit: one request, controllable work, returned result

Qualify one concrete G0/R3 journey before expanding the feature inventory. The
user says **“Prepare a community survey for LoopX; bring me a draft.”** The App
finds the qualified existing owner, retains the request, shows its actual
disposition, and returns a readable Markdown draft with sources. The correction
**“Chinese first; do not publish.”** must reach and be adopted by the actual
receiver. Publication is outside this draft-only pilot.

Freeze GQ02/GQ04/GQ08/GQ09 with these observable exits:

- No manual Agent-id lookup, old-session link, repeated context, reminder or
  result relay. Recipient identity and task purpose are visible.
- Accepted, deferred, rejected, executing and returned facts remain distinct.
  A delivered deferral is not task completion. Explain delay beside the request;
  private receiver reasoning never enters an external audience automatically.
- One authorized driver performs the work. Correction and scoped-stop variants
  require receiver/runtime readback, not only a transport ACK.
- The draft opens in the original App conversation after reload or session
  replacement. Record source, packaged UI and real native execution separately
  as passed, failed, blocked or not run.

Then qualify existing G1 with **“Get a small team to check the cash-flow numbers
and resolve the disagreement.”** Two or three real workers consume versioned
inputs, independently challenge a period/unit error, adopt the revision and
return a checked synthesis. A second cycle changes the consumed input basis.
This is GQ05/GQ11–13, not a new milestone or queue.

The deterministic integration in `tests/test_chat_delegation_journey.py` connects
the production Chat controller, scoped handoff, receiver inbox and result return
through a disposable file store for both steward and Goal Chat. It checks ingress
replay, a separately adopted correction, and Markdown return to the original
conversation after session replacement and store reload. Model responses and
receiver work are scripted: this does **not** qualify owner selection, native
execution, live steering/stop, the packaged App or G1. Release qualification must
exercise those remaining boundaries with the selected real executor; ordinary
test runs require no model credentials or paid calls.

App snapshot readback resolves a delegated request using the Goal instance in
its original receipt and the existing typed history-inspection decision. This
keeps receiver disposition visible after alias recreation, so the existing App
return watcher can retain the original session. A mismatched route/receipt never
substitutes another instance; unavailable readback preserves the saved message.
Production HTTP tests cover steward and Goal Chat with real disposable stores.
This is readback qualification, not native executor or model-routing acceptance.

Keep WIP on the first journey and demonstrated blockers. Reuse acceptance
recovery, GoalRef and late-return changes. Shared TS refactors accompany the
affected transaction; full migration, Lark visual parity, scale and promotional
film do not block this pilot. Component PR merges do not certify the journey.

## TS and generic async inbox: migrate with the user path

### Semantic boundary

Reuse existing persisted identities and contracts. The conceptual relation is
source request → addressed recipient/work → admitted execution → result →
original-route return. These are links across existing owners, not instructions
to merge every message, Todo and artifact into one database table.

| Fact | Meaning | Must never imply |
| --- | --- | --- |
| Accepted/queued | Durable owner accepted a scoped request or queued it | Worker started or message was adopted |
| Supplied/read | Request was exposed/read through a supported receiver path | Agreement, responsibility transfer or permission |
| Adopted/declined/deferred | Receiver recorded its actual disposition with basis | Independent acceptance of an artifact or task completion |
| Executing | Current owner supplies live execution evidence and observation time | Eligible quota, open Todo, online registration or ACK |
| Result ready | Versioned result exists; validation status remains separate | Original requester received it |
| Returned | Original route has the applicable delivery/readback fact | User read it, approved it, or downstream consumed it |

Work, execution, transport, freshness and acceptance are orthogonal facts.
Display their useful combination; do not invent one all-purpose `isActive` or
force every simple answer through an adoption workflow.

### Replacement cadence

1. **App caller first (R1/T0).** Reproduce input, message identity and scope races
   in the existing Chat path. Fix the selected complete conversation before any
   store migration. Delete browser effect heuristics when the existing semantic
   executor or explicit control owns the operation; do not replace them with a
   larger keyword blacklist or add a paid classifier before every message.
2. **Whole async lifecycle (R3/T1–T2).** Inventory producer, persisted record,
   consumer, retry, return and cleanup for Chat, collaboration inbox and Lark.
   Characterize current valid/invalid transitions first. Move one cohesive
   accept→pending→consume/disposition→return-recovery lifecycle into the nearest
   typed owner, with source IO/provider adapters around it. Admit App and one
   Lark adapter through this owner; prove adapter-off isolation. Do not introduce
   per-field RPCs, dual writes or a second durable queue.
3. **Recovery and retirement (T3–T4).** Migrate existing pending records with
   explicit schema/read compatibility and original ids. Inject failure between
   acceptance and dispatch, between provider acceptance and reply recording,
   and during restart. Only retire old transition logic/writers after real-path
   parity and schema-aware rollback evidence. Preserve legitimate historical
   facts; rollback cannot reactivate an old executor or resend committed effects.
4. **Expansion.** After App and Lark are independently qualified, apply the shared
   contract to another authorized ingress when a real caller needs it. Do not
   add a broker, scheduling engine, provider marketplace or third task ledger
   merely to name the abstraction. Broader persistence cutover retains D1–D3.

Keep provider authentication/signatures, external event decoding, addressing,
chat membership, rate limits, attachments and rendering in Lark. Generic pending
selection, stable request identity, replay/disposition and recovery rules should
not depend on `oc_`/`om_` identifiers or a bot reaction. Notification/attention,
Todo/lease, model admission and artifact acceptance keep their existing owners.
Dispatch events wake the existing driver within admission; polling repairs gaps.
An inbox is not permission to start another automation.

The return-verification slice uses the existing TS classification owner for
both adapter results and typed resolution failures. Exception text is diagnostic,
not route/authority evidence: a transient read failure retains its locator and
backoff, then reconciles the original result without another send. Explicit
revocation, lost routing and missing required provider acknowledgements remain terminal. This
also holds after a `source_session_v1` Goal is recreated: a crash-persisted
attempt recovers on the original GoalRef and conversation, transient verification
backs off without resending, and typed terminal blockers stay stopped. This
qualifies the persisted recovery boundary, not live provider availability or
the complete GQ09 journey.

Committed handoff recovery uses the existing typed collaboration lifecycle. A
failed, timed-out or interrupted originating Turn does not cancel work already
committed to a receiver's Inbox. Once that Turn settles, its receiver's saved
result may return through the same trusted route even when the caller lost its
handoff response. Recheck the current source grant, committed request identity,
route and exact Goal instance; a conflicting saved handoff receipt blocks
recovery rather than being ignored. Preserve the original failed Turn and append
one idempotent return. Active originating Turns still wait; source revocation,
missing entries and changed routes cannot authorize a send. Provider-specific
initial-reply acknowledgement and post-send verification remain separate gates.

The GQ07/GQ09 regression variant is “Keep going and bring me the result.” Inject
failure after Inbox/route commit but before the caller's response is saved,
reopen the real store, then publish the receiver's conclusion. Steward and Goal
conversations must show that conclusion once without rerunning the model or
rewriting the failed Turn. Synthetic production File/HTTP and typed counterexamples
qualify this recovery boundary; they do not establish live owner selection,
receiver adoption, native steering or two real team cycles.

The same commitment now supplies owner-private App readback while the caller is
active or its answer is missing. Prefer the saved answer; otherwise attach the
existing delegation card to the original user message, without appending a fake
answer or rewriting the failed Turn. The shared typed lifecycle admits observation
independently of result-return admission: pending/supplied/adopted/returned remain
separate facts. Match the exact saved conversation, client Turn, request and Goal
instance; ambiguous routes or conflicting receipts do not produce a card. External
sessions do not receive private brief or receiver rationale. Route discovery retains
the existing bounded historical scan; full backlog indexing and installed/live
journey qualification remain separate acceptance work.

App history recovery is a shared TS read boundary for steward and Goal channels.
One unavailable older Session must not hide readable messages, lose their
original result locators or restart the current stream. Show incomplete history,
retry missing snapshots with backoff, and preserve live text and drafts. An
unreadable current Session blocks ordinary send until its exact record recovers;
never infer an empty conversation or create a replacement driver from a read
failure. A recovered read does not prove receiver adoption or native GQ02/GQ09.

App 历史恢复由管家与 Goal 对话共用的 TS 读取边界负责。旧 Session 读取失败不应
隐藏可用消息、丢失原结果位置或重启当前流；应明确展示历史不完整，退避重读缺失
快照，并保留实时文本与草稿。当前 Session 无法读取时，普通发送等待原记录恢复，
不能据此推断空会话或新建执行驱动。读取恢复仍不代表接收方采用或原生 GQ02/GQ09
已经验收。

The pending-receipt checkpoint moves file-existence classification into the
shared `collaboration/inbox_receipts.ts` read model. Missing, unreadable and
identity-conflicting decisions/results are explicit; a damaged conclusion cannot
silently clear the receiver's request or remove the original App collaboration
card. Original-byte recovery returns once without rerunning work or changing
audience. Native private-file/CLI and production HTTP tests cover this boundary;
packaged browser checks cover its existing unverified-delivery presentation.
The adapter batches by count and encoded bytes rather than raising the bridge
limit. No new store or persisted schema is introduced. This does not complete
the accept/consume/cancel lifecycle migration or qualify native owner selection,
steering, team adoption or live Lark transport.

Before each extraction report base/head real-call latency, boundary crossings,
bytes, owners deleted/retained and compatibility callers. Product delivery must
not wait for full Python retirement. Python may retain IO; TS owns migrated
transitions and effects exactly once. Existing TS receipt and CAS machinery must
be reused where applicable, without pretending a Chat record is a Todo command.

## Acceptance and failure matrix

Freeze source/package/runtime/profile, public inputs, budget, timeout and
independent expected results before running. Keep passed, failed and untested
separate; a browser fixture cannot qualify a real attached host.

| Boundary | Required counterexample and observable result |
| --- | --- |
| Meaning | Explanation, quote, negation, mixed language and future conditional mention of scheduling stay conversational. Explicit UI schedule still reaches its reviewed typed path |
| Acceptance | Response lost after durable accept: recover by the same scoped identity; one logical Turn/provider call. Same id with different text conflicts; deliberately repeated new request remains possible |
| Dispatch | Fail after accept before start; existing recovery resumes the request. ACK without execution is queued with next trigger, never running |
| Scope | Navigate A→B→A, late response, old subscription terminal event: update the original source/session/Turn only. Full snapshots and delta streams have different merge rules |
| Stream | Duplicate/late events and hydrate overlap preserve one logical answer. A new event does not force scrolling while the user reads history |
| Correction/stop | During tool execution and at completion: actual receiver adopts the latest scope or reports queued/unsupported. Stop targets the original Turn, never its successor or all peers implicitly |
| Result | Replacing the active session must not hide a result owed by an older session in the same conversation. Readback updates only its own session, preserves streamed text and retires old reads after verified return. Missing file retries read only; v1 review cannot certify v2; opening a report is not adoption. Lost return ACK reconciles before another send |
| Attached | Native host offline, stale binding, unsupported steering, next-Turn-only adapter and restart: request remains visible; no guessed success or competing driver |
| Managed | Runtime start failure, quota denial, missing login and stop/restart: effective profile and actual condition readable; no silent model/account substitution |
| Authority | Revoked access or source change rejects stale effects; unrelated permitted branches continue. No private history enters a shared audience |
| Packaging | Supported local installed bundle completes first request and return; narrow layout, keyboard, reload, unavailable and quiet cases remain usable |

Measure time to first useful result, recovery, locating the answer, unnecessary
human relays, status misreads and cost per accepted outcome. Keep setup, first
result and recovery timings separate. Retain the golden pack's frozen attention
comparison; no measured improvement is claimed by this proposal.

## Delivery boundary

The delivered conversation-entry repair removes browser free-text action
classification. Shared queue preparation now settles accepted start failures
instead of leaving requests indefinitely queued. Neither change proves a whole
managed or attached journey.

The next qualified frontend slice retains late worker returns after active-session
replacement. The existing Chat snapshot supplies session/message lineage to a
shared TypeScript read model; only sessions still owing a conclusion or delivery
verification remain alongside the active session. No model replay, execution
driver, persisted schema or new inbox owner is introduced. Browser acceptance
covers a replacement session, one lost old-session read, automatic return and
cross-session isolation. Real host execution and receiver adoption retain their
separate acceptance requirements.

Keep work in this order: qualify the installed App's existing-owner-to-original-
conversation journey (GQ02–04, with entry/recovery companions); then G1's two real
small-team cycles (GQ05/GQ11–13, with correction and interruption). Shared report
polish, materials and attention summaries follow; promotional film and scale
follow product evidence. Reuse pending acceptance-recovery and GoalRef work
rather than implement a competing session or inbox lifecycle. Generic TS inbox
extraction remains incremental within those journeys, not their prerequisite.

The direct-group companion retains typed non-admission causes through the
provider and App. Feedback describes the last observed event and current trigger
separately: enabling direct messages does not prove an old message ran, and does
not scan and dispatch captured history. Consecutive concise requests and a
correction must retain their own object, constraints and return lineage. Transport
fixtures do not qualify receiver adoption or actual repair and merge.

The shared conversation intake resolves intent and verifies decision-relevant
facts before delegation. A currently satisfied outcome returns its evidence
without duplicate work or another protected-action proposal; a historical record
or unavailable read is not current proof. Direct analysis, existing-work reuse
and bounded peer verification are valid outcomes. This is general reasoning
guidance for every domain, not another classifier or authority owner. Core's
typed grants/effects remain authoritative; normal authorized host tools resolve
external facts, and restricted audiences retain explicit gaps. GQ03/GQ07 include
both already-satisfied and genuinely unfinished requests, alongside the equivalent
report cases. Actual lookup and model quality remain release qualification.

### Entry behavior compatibility

All ordinary input now uses the selected conversation, including requests to
create or change work. Runtime tool support, authorization and existing action
review still determine what actually executes; removing the browser classifier
does not certify a model's ability to complete GQ01–GQ17. Explicit creation and
scheduling controls open field-based forms and create only a reviewed preview.
Goal permissions are explicitly selected in the form (the existing workspace-write-on-confirmation default is retained);
permissions are no longer inferred from boundary prose. The permission selector
and the written execution boundary must both be respected downstream.
The explicit status-only profile returns a labelled snapshot, not a keyword-built
answer. Converting a reply into a task opens the complete editable text rather
than guessing its next-action sentence. Structured ID/date/resume-condition
validation remains. Lark routing is unchanged.

### Conversational goal preparation: integrating the team-workspace proposal

[PR #4376](https://github.com/loopx-project/loopx/pull/4376), contributed by
[KashiwaByte](https://github.com/KashiwaByte), contributes a useful interaction
idea: help the owner clarify a goal through one consequential question at a time,
with contextual reply suggestions. Integrate that idea into the existing App
conversation and Goal action path. Its independent workspace, JSON store,
subprocess runner and scheduler are not adopted; the unshipped prototype is
removed from this PR's final product delta. Preserve its contribution in history.

| Idea | Existing owner and acceptance |
| --- | --- |
| Conversational goal draft and contextual options | R1 / GQ01: shared typed `goal_draft` in Chat; reusable App card and editable Goal form. Suggestions fill the composer and require explicit send. Unknown requirements remain empty; no regex intent classifier or automatic creation |
| Ask a person to supply information, perform work or judge a result | Existing operator inbox, user gates and review/adoption contracts. Keep those different decisions visible; a reply does not imply delegated authority. End-to-end acceptance remains open |
| Remember corrections and collaborator strengths | Existing scoped brief/context and capability-memory owners. Corrections may inform subsequent work; they cannot mint permissions, prove capability or silently change an execution binding |
| Rolling plans and independent checks | R2/R3 work graph, managed/attached Turn and independent acceptance owners. Require real dependency adoption, correction, stop and result return; a conversational draft does not qualify a team |
| Optional remote executor | Existing extension and execution-profile contracts. No additional provider is admitted without a real caller, explicit binding and lifecycle qualification |

The bounded implementation adds a provider-response suggestion, not another
planner or source of Goal truth. TypeScript admits its structure in the existing
collaboration owner; Python performs transport redaction and persists it alongside
the completed message. App history and reconnect use that message. Both managed
and attached completion storage retain the same optional field. Ordinary answers
and malformed drafts preserve the old response contract. A provider must actually
emit the structured suggestion; storage/UI acceptance is not proof of model
intent quality or a live attached-host loop.

Before drafting, resolve the current conversation and inspect permitted existing
work. A continuation or correction uses the existing qualified owner and scoped
handoff; an owner already answering in Goal Chat keeps the conversation. Compare
all plausible candidates; ambiguous identity asks one useful question. Missing
grants or stopped work are explicit gaps, never reasons to create a replacement.
An explicitly separate goal may overlap an existing topic. This is semantic model
selection against host evidence, not keyword routing or a new discovery service.
TypeScript prevents a response with a handoff, protected action, proposal or gate
from also advertising a new Goal. The host still verifies recipient authority.

A complete draft opens the existing typed `goal.create` preview directly, with
one explicit apply. Optional editing reuses the existing form and the same request
builder. Incomplete drafts remain editable. Retry/reopen of the same source
message and draft preserves operation identity; a separate message is a separate
request. Draft-derived previews remain read-only with heartbeat disabled. The old
explicit form keeps its current default. Workspace, owner and permission checks
remain authoritative; creation is not proof of worker execution or delivery.
Lark receives the shared semantic answer/handoff behavior, while draft cards and
direct preview are App-only. No new capability, provider or scheduler is added.

The [public model evaluation](../../../examples/evaluations/chat-intake.py) runs
only during release-candidate qualification, with explicit paid-call opt-in and
at least two repeats per default or newly advertised model profile. Routine PR
work and heartbeats use offline regressions and affected browser scenarios; they
do not launch paid evaluation. Record candidate identity and retain failures or
skips rather than presenting an unqualified profile as passing. The suite uses
the production prompt/parser and fixed public contexts. Its frozen outcomes
cover new work, existing owners, ambiguity, stopped/ungranted recipients, scope
correction, current-Goal follow-ups, quotes, negation and ordinary questions in
Chinese/English. Output conflicts, omitted envelopes and truncated generations
fail rather than being counted as successful intent recognition. Report model,
prompt/case hashes, request settings, token usage and repeat count. This layer
qualifies model interpretation of supplied evidence, not live discovery, actual
dispatch, stop enforcement or full GQ01/GQ02 completion. Packaged browser and
real collaboration transport tests qualify those separate boundaries.

The same conversation surface preserves reading position during streaming: output
follows only while the reader stays near the bottom, and a latest-message action
restores following. Multiline drafts expand within a bounded composer; suggestions
remain on overview/empty entry states rather than displacing an active conversation.

### Image admission and current-conversation position

An ordinary request such as “Look at this screenshot and draft a community
thank-you” must reach the existing conversation with its image intact. The turn
HTTP budget includes the base64 representation of the existing attachment
allowance: at most four images, 5 MiB each and 12 MiB total. Text and metadata
retain their separate 64,000-byte budget; other JSON endpoints retain their
existing ceiling. Only a confirmed rejection before turn admission restores the
draft and images for manual resend. An uncertain or accepted submission must not
be presented as safe to replay.

Operation cards belong at their creation point among messages. Updating or
restoring an old operation must not append it after a new answer; undated history
must not masquerade as fresh activity. The shared TypeScript conversation read
model owns ordering, while the Python HTTP/attachment adapter owns wire admission.
Existing confirmation and effect authority remain unchanged.

Source qualification covers HTTP admission, durable session readback and a
synthetic Codex protocol process, plus packaged desktop/narrow-screen image send,
historical cards and rejected-draft recovery. These fixtures establish transport
and interface behavior, not live model quality, public posting or installed-host
acceptance. GQ06's material entry and GQ07–09's continuity remain subject to their
full delivery and recovery acceptance.
