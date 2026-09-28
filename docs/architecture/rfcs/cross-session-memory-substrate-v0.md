# Explicit Todo continuation: Stage A

- **RFC status:** Accepted (Stage A shipped in #4094; the historical filename is retained on purpose)
- **Supersedes / closes:** none

## Delivery status and integration direction (2026-09-13)

Stage A shipped in [#4094](https://github.com/huangruiteng/loopx/pull/4094), merge `2ebd921ee989f7c696a7214ba1176d3bd5de6fb3`. The historical filename does not imply that a generic memory substrate shipped. The [capable manager and semantic handoff RFC](capable-manager-semantic-handoff-v0.md#513-integrate-shipped-explicit-continuation-4094) includes this implementation in its M2/M3 refactor scope. That successor remains proposed; this document remains the shipped CLI compatibility and authority reference until its replacement qualifies.

Reuse rich/legacy context and the existing note validator/claim transfer boundary. Receiver acceptance of a suggestion is distinct from `handoff adopt` ownership mutation. Stage A's note is replaceable current Todo state, not a private immutable history. The successor maps authorized context to a recoverable brief, references current work, and supplies general assessment/result/automatic-return relations without adding a memory ledger or copying claim authority. CLI `prepare/inspect/adopt` remains usable during migration; frontend/Lark and automatic host continuation must be qualified separately. Same-host and registered-agent restrictions continue to apply to this adapter, not every general request. Leased execution uses the explicit transfer path below. See successor §5.13 for mappings, migration conditions and retained negative cases.

The successor [§5.7](capable-manager-semantic-handoff-v0.md#57-session-and-product-continuity) now distinguishes same-session resume, same-Agent session replacement and cross-Agent takeover. Only the last may require this adapter’s ownership mutation. Automatic brief capture, source-loss recovery, executable-session fencing and original-route result return are explicit future integration work; optional Obelisk recall supplies missing historical evidence, not a replacement transfer grant.

Language mirror: [中文版](cross-session-memory-substrate-v0.zh-CN.md).

Status: a bounded local CLI workflow. The former general memory-substrate
proposal is replaced by a cross-agent continuation workflow over the
canonical Todo authority. It does not freeze a public memory schema.

## Problem: switching coding agents mid-work

When a developer switches from one coding agent to another (e.g. Claude Code
to Codex), the target agent has no context about what was done, what was
tried and failed, and what comes next. The developer must re-explain the
entire project state. This workflow gives the source agent a structured way
to dump complete working context so the target agent can continue without
re-explanation.

Stage A solves the **same-machine, different registered agent** scenario: an
explicit handoff where the source prepares a rich continuation note and the
target inspects, verifies, and adopts the Todo. It does not implement automatic
context capture, agent-agnostic protocols, or zero-config onboarding. Those
require a different product layer beyond this control-plane primitive.

## Ownership and placement

The existing Todo coordination boundary owns current execution state, stable
Todo IDs, revision checks and claim/lease decisions. The selected canonical File/SQLite authority supplies persistence; no new capability, provider, database,
index, discovery API, recovery service or ownership protocol is introduced.
The CLI is a host adapter to that TypeScript boundary.

Historical retrieval and long-term memory belong to optional providers through
existing `decision_context` / `agent_turn_recall` capability boundaries. Their
absence does not affect this workflow. Stage A neither invokes those providers
nor grants permission to index a workspace or read source-reference bodies.

## First usable path

Prerequisites: an **already explicitly promoted canonical authority** and an
open, active Agent Todo claimed by a registered Agent. Lease-free adoption
retains the existing claim transaction. Hard-lease work requires the exact
current execution proof; ownership moves only through the existing atomic
`task-lease transfer --transfer-claim` command. This adapter never promotes a
Goal, changes its handoff mode or falls back to Markdown.

The user explicitly hands a Todo from one session to another session of a
**different registered agent on the same host**. The source writes a revision-
guarded continuation note (legacy rationale or rich context) into the existing
Todo note; the target inspects current authority, verifies local artifact
availability, and continues through the existing claim transaction with
current-authority readback. Stop source execution before continuing in the
target. Session IDs below are provenance, not authentication or a session
lease. This slice does not implement automatic host launch.

Use the same registry/runtime arguments as the existing Todo CLI. Substitute
an existing goal, Todo and registered agents for these example identifiers:

```sh
# Source: inspect current state, including its provider_revision.
loopx --format json handoff inspect --goal-id demo --todo-id todo_a \
  --agent-id agent-a --session-id source-session --workspace .

# Copy the exact returned revision into REVISION, then explicitly replace the
# current Todo note with rich handoff context from a JSON file.
loopx handoff prepare --goal-id demo --todo-id todo_a \
  --agent-id agent-a --session-id source-session --operation-id handoff-1 \
  --expected-revision "$REVISION" --from-context ./handoff-context.json

# Or use the legacy compact form (rationale + source references):
loopx handoff prepare --goal-id demo --todo-id todo_a \
  --agent-id agent-a --session-id source-session --operation-id handoff-1 \
  --expected-revision "$REVISION" --rationale 'The narrow change preserves the existing authority boundary' \
  --source-ref 'artifact:decision.md'

# Target: inspect again as the receiving agent; verify evidence and current artifact availability.
loopx handoff inspect --goal-id demo --todo-id todo_a \
  --agent-id agent-b --session-id target-session --workspace . --artifact decision.md

# Target: inspect with a readable handoff digest (any registered agent, any session).
loopx --format json handoff inspect --goal-id demo --todo-id todo_a \
  --agent-id agent-a --session-id target-session --workspace . --format digest

# Use the target inspect revision, then claim through the existing boundary.
# --target-agent-id specifies the receiving agent (defaults to --agent-id).
loopx handoff adopt --goal-id demo --todo-id todo_a \
  --agent-id agent-b --session-id target-session --operation-id resume-1 \
  --expected-revision "$REVISION" --target-agent-id agent-b --workspace . --artifact decision.md
```

Inspect reports the current goal/Todo/revision, decision rationale, source
pointers, local workspace/artifact availability and next action from the current
Todo. With `--format digest`, inspect renders a readable handoff summary that any
target agent can directly consume, including work summary, approaches tried,
next steps, files touched, key decisions, and open questions. References are
evidence pointers supplied by the source, not verified remote content. Artifact
checks prove local existence, not content integrity. Adopt preserves the Todo
ID, invokes the existing claim transaction and reads back current authority.
A same-owner claim may correctly be a no-op; it does not create a Todo or
manufacture a new lease.

## Leased execution continuation

Prepare while the sender still owns the claim and lease. Pass the current
`--task-lease-idempotency-key` and `--task-lease-expected-version` pair to prepare.
Use actual readback versions; the example assumes source version 3.

```bash
loopx handoff prepare --goal-id demo --todo-id todo_a \
  --agent-id agent-a --session-id source-session --operation-id prepare-context \
  --expected-revision "$SOURCE_REVISION" --from-context ./handoff-context.json \
  --task-lease-idempotency-key execution-a --task-lease-expected-version 3
loopx task-lease transfer --goal-id demo --todo-id todo_a --owner agent-a \
  --idempotency-key execution-a --expected-version 3 --new-owner agent-b \
  --new-idempotency-key execution-b --ttl-seconds 600 --transfer-claim
loopx handoff inspect --goal-id demo --todo-id todo_a \
  --agent-id agent-b --session-id target-session --workspace . \
  --task-lease-idempotency-key execution-b --task-lease-expected-version 4
loopx handoff adopt --goal-id demo --todo-id todo_a \
  --agent-id agent-b --session-id target-session --operation-id accept-context \
  --expected-revision "$TARGET_REVISION" --workspace . \
  --task-lease-idempotency-key execution-b --task-lease-expected-version 4
```

Inspect remains read-only without proof, but cannot report `can_adopt=true`
for leased work without current execution authority. Transfer carries a note
forward only when it was valid before the authorized claim change; it updates
that note's owner-bound fingerprint in the same CAS. Changed work requirements
and previously stale notes remain stale. Arbitrary metadata edits cannot
rebind a note. An already-committed historical transfer is replayed unchanged.

Leased adopt seals a context receipt bound to Agent, session, note, revision and
lease proof. It does not transfer, acquire or renew a lease. Its `adoption`
result has `changed=false`; `current_authority_verified` additionally requires
fresh authority readback. The old lease-free result retains its `claim` field.
Historical receipts never override a released/expired lease, a different owner,
changed requirements or a Goal acceptance hold. Session IDs remain provenance,
not host authentication or grants to execute external tools.

Prepare/adopt deliver current canonical Todo state to the existing Markdown
projection. Display failure returns `projection_delivery=pending` with
`retry_business_mutation=false`; repair the local display inputs and retry the
same operation or use `todo project-markdown`. Inspect never writes a display.
The feature does not create frontend/Lark controls or launch a target session;
it is an explicit CLI workflow, not automatic manager-to-worker delegation.

## Rich handoff context

The `--from-context` flag accepts a JSON file with structured handoff context:

```json
{
  "work_summary": "Implementing cross-agent handoff with typed transfer grant. Need to enforce note invariant.",
  "approaches_tried": [
    {"approach": "Unstructured note with hash only", "outcome": "failed", "reason": "Any caller can compute public facts"},
    {"approach": "Typed transfer_grant without note validation", "outcome": "partial", "reason": "Still accepts arbitrary JSON"}
  ],
  "next_steps": ["Add shared validateContinuationNote predicate", "Add regression tests"],
  "files_touched": [
    {"path": "todo_claim.ts", "action": "edited", "summary": "Added validateContinuationNote"},
    {"path": "todo_continuation.ts", "action": "edited", "summary": "Adopt uses noteValidation.noteFacts"}
  ],
  "key_decisions": [
    {"decision": "Typed transfer_grant", "rationale": "Binds source/target/todo/revision/note"}
  ],
  "open_questions": ["Stage B should support agent-agnostic handoff?"]
}
```

Fields:
- `work_summary` (max 2000 chars): primary description of what was done and why.
  At least one of `work_summary` or `rationale` is required.
- `approaches_tried` (max 20): what was attempted, the outcome (`success`,
  `partial`, or `failed`), and why.
- `next_steps` (max 20): what the target agent should do next.
- `files_touched` (max 50): files read/edited/created/deleted, with optional
  summaries.
- `key_decisions` (max 20): decisions made and their rationale.
- `open_questions` (max 20): unresolved issues the target should address.
- Legacy: `rationale` (max 600 chars) and `source_refs` (max 20) are still
  supported for backward compat.

## Record lifecycle and failures

The source agent produces a note **only on explicit prepare**. It saves the
non-reconstructable context into the existing Todo note in one revision-guarded
update. It replaces that current note; it does not append a history or retain
an additional store. The note's marker and execution-fact fingerprint are
internal details, not a general-purpose versioned memory API.

Missing, malformed or overwritten notes permit inspection but block adoption:
ask the source to prepare a current note. A changed Todo execution fact expires
the note on read. Completion, archive and a different claim owner reject the
flow. Source prepare replaces stale notes; there is no background expiry worker.
Any provider revision change after inspect rejects a fresh adopt, even if the
Todo itself did not change. Reinspect and reconsider before using a new operation.

Normal restart reads the note from the existing durable file authority. Reuse
both the operation ID and original expected revision after an uncertain write.
A failed write leaves the previous note intact; committed-but-lost responses
recover through existing receipts. Historical receipt replay never substitutes
for current open status, owner, note and revision readback. Missing artifacts
block adoption without mutation. Do not resume execution unless `ok` and
`current_authority_verified` are both true.

There is no enablement toggle: only explicit commands run this workflow. Stop
using them to disable it; replace the note with `loopx todo update --note ...`
if it is obsolete. Existing Todo/claim commands keep their defaults. Never put
credentials or raw private transcripts in context fields; these fields share
the existing Todo projection visibility, not a private memory ACL.

## Validation and bounded next step

The focused test runs separate source and target Python CLI processes against
a disposable real file authority, plus restart, lost acknowledgment, failed
write, missing artifact, stale revision, changed owner, completed Todo, rich
context, and legacy backward-compat cases. Existing claim/update suites cover
default behavior and rejection of missing, stale or foreign execution proofs.

```sh
node --experimental-strip-types --test tests/control_plane_ts/todo_continuation.test.ts
```

The shared `continuation_note` module is the single source of truth for
continuation-note validation. Both the handoff helper (`todo_continuation.ts`)
and the final claim authority (`todo_claim.ts`) use `validateContinuationNote`,
which enforces the typed `loopx-explicit-continuation` marker, bounded fields,
source session, and current `todo_facts`. An arbitrary JSON note with matching
hash is rejected because it lacks the typed invariant.

Cross-agent transfer is implemented through a typed `transfer_grant` that
binds source owner, target owner, Todo ID, exact revision, and current note facts.
Only the handoff flow (`handoff prepare/inspect/adopt`) can produce this grant;
regular `loopx todo claim` without a grant still rejects foreign-owner Todos with
`claim_owner_mismatch`, preserving the default claim behavior. Lease-bearing note
updates remain deferred to their owning boundaries.
