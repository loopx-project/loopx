# App conversations and reusable asynchronous work delivery

- Status: Draft integration proposal; no new provider, scheduler or authority.
- Baseline: `27f0fc93b`, inspected 2026-09-25. Implementation and live acceptance are separate.
- Owners: [overall roadmap](loopx-overall-roadmap-v0.md) R1–R3/G0–G2;
  [semantic handoff](capable-manager-semantic-handoff-v0.md) M1–M3;
  [conversation surface](intelligent-review-presentation-surfaces-v0.md#88-reusable-conversation-work-surface);
  [TS migration](typescript-control-plane-migration-v0.md) T0–T4.
- Evaluation: [steward golden queries](../../product/use-cases/steward/golden-queries.md).

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
- Use typography, spacing and restrained state accents from the existing design
  system. Motion explains verified transitions, never invents busy workers.
- Keep creation/connect, direct owner chat and team work discoverable. A simpler
  screen must not conceal unresolved work or reduce permitted owner discovery.

Use public/synthetic data for preview. Review populated, quiet, blocked and
unavailable desktop/narrow views. Retain keyboard access, reading position and
return context. First-screen changes still require the repository's preview gate.
No external screenshot, private incident transcript or proprietary asset is
redistributed by this proposal.

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
| Result | Missing file retries read only; v1 review cannot certify v2; opening a report is not adoption. Lost return ACK reconciles before another send |
| Attached | Native host offline, stale binding, unsupported steering, next-Turn-only adapter and restart: request remains visible; no guessed success or competing driver |
| Managed | Runtime start failure, quota denial, missing login and stop/restart: effective profile and actual condition readable; no silent model/account substitution |
| Authority | Revoked access or source change rejects stale effects; unrelated permitted branches continue. No private history enters a shared audience |
| Packaging | Supported local installed bundle completes first request and return; narrow layout, keyboard, reload, unavailable and quiet cases remain usable |

Measure time to first useful result, recovery, locating the answer, unnecessary
human relays, status misreads and cost per accepted outcome. Keep setup, first
result and recovery timings separate. Retain the golden pack's frozen attention
comparison; no measured improvement is claimed by this proposal.

## Delivery boundary

Current candidate work removes all browser free-text action classification in the App and checks
its ordinary Chat path plus explicit scheduling controls. It changes no authority
or stored message schema. Managed/attached conversation continuity, generic TS
inbox extraction and live two-cycle small-team acceptance remain planned until
their own evidence is recorded. The candidate can roll back as an App routing
change; later persisted-contract migrations need their own compatibility plan.

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
