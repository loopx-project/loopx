# Canonical terminal review and validation

On an explicitly promoted local Goal, Agent completion and Monitor stop now use
the same reviewed recovery path as Todo edits and User completion. The initiating
Chat action binds the complete provider revision and registry digest, preserves
one operation identity, and acknowledges display only after the existing
projection outbox confirms the current view.

## Operate and recover

For ordinary CLI completion, retain the same explicit completion identity after
a lost response:

```bash
loopx todo complete --goal-id example-goal --todo-id todo_work \
  --agent-id agent-a --completion-identity-key reviewed-result
loopx todo list --goal-id example-goal --todo-id todo_work
loopx todo project-markdown --goal-id example-goal --execute
```

Use `--no-follow-up` only when no successor is needed. At replan, review unmet
acceptance against the original authorized goal and current evidence. If a
reasonable in-scope next step remains, continue or replan; a preassigned successor
is not required. An empty Todo queue alone does not narrow the authorization.
Otherwise explain why no reasonable in-scope next step remains, preserving unmet
requirements rather than claiming them achieved. Do not invent work, expand
authority or consume the remaining budget merely to stay active.

This is agent decision guidance in the shared replan packet, including compact
CLI and host envelopes. It does not add a machine judge of free-text acceptance,
change lifecycle admission, or require a successor for every completed Todo.
Leased work additionally
requires its current `--task-lease-idempotency-key` and
`--task-lease-expected-version`; owner confirmation is not a lease or a lifecycle
grant. Chat users retry the same failed proposal. A stale proposal requires a
fresh preview, not a replacement identity that bypasses review.

| Boundary | Observable result |
| --- | --- |
| Provider/registration changes before a fresh reviewed completion | Reject before private validation execution; Chat marks the proposal stale |
| Provider changes during validation | Reject the old validation result; Todo remains unfinished |
| Lease expires during validation | Recheck runtime time and reject stale execution proof |
| Canonical commit succeeds, display delivery fails | Business remains committed; Chat reports recoverable failure without a successful display receipt |
| Response/action receipt is lost after commit | Retry recovers the original business receipt before checking current review freshness |
| Same operation carries a changed reviewed note, evidence, reason or basis | Reject identity reuse; never silently acknowledge the changed intent |
| Private declaration is unavailable after successful completion | Business receipt can recover from its public commitment; lossless display recovery still requires restoring the original declaration |

The TypeScript terminal owner performs admission, source checks, validation
planning, lease retirement, linked effects, CAS and receipt recovery. Python
transports facts, resolves private argv only when requested, executes declared
validation and drains projection. It does not decide whether a stale validation
can complete work. The preview executes no validator. Separate user-completion
edits retain their existing combined edit/terminal semantics and old stored Chat
proposals retain their existing protocol.

## Wire and migration boundary

The packaged Python adapter and TypeScript runtime use one request schema,
`loopx_local_coordination_todo_terminal_lifecycle_request_v3`, for complete and
supersede. `operation_identity` explicitly selects the operation's meaning:

- `{kind: "explicit", operation_id: "..."}` executes or recovers that named
  operation, including historical receipts created by earlier runtimes.
- `{kind: "completion_turn"}` completes a keyed `turn_settlement` deliverable
  or closes out its already accepted ordinary completion. TypeScript derives
  both stable phase ids; callers cannot supply an alternate operation id.
- `{kind: "current_monitor_cycle"}` completes a `continuous_monitor` without an
  explicit completion turn key. The TypeScript owner derives the operation id
  from Goal id, Todo id and authoritative `material_change_generation`.

Reopening the Monitor advances its generation, so earlier-cycle receipts cannot
complete the current open cycle. If an explicit operation already completed the
current cycle, the core records a generation-scoped no-change receipt. It never
infers cycle membership from an old unscoped receipt. All three identity modes
share one terminal transaction; unscoped completion and supersede use explicit identity.

The terminal method also accepts these bounded additions:

- `review_basis`, when present, contains exactly `provider_revision` and
  `registry_sha256`. It binds reviewed intent and is part of receipt identity.
- `validation_source_provider_revision` is null before an issued effect and is
  the returned revision on continuation. It is a freshness constraint, not new
  operation identity. Both caller validation and Goal acceptance validation
  require it in the current protocol.
- `validation_declaration_sha256` carries the canonical public commitment.
  Historical recovery precedes private declaration resolution. Fresh execution
  still requires the matching declaration and current authorization.

The existing method may return `resolve_validation` before `execute_validation`.
Both responses bind the source revision; neither commits the business operation.
For a validated fresh completion the host crosses the runtime boundary three
times (resolve, plan effects, commit), versus two before this change. Unvalidated
completion and historical recovery remain one terminal request. This bounded
extra crossing makes receipt recovery independent of host-local argv; it can
disappear when the native host owns declaration resolution and effect execution.

The old v0/v1/v2 request decoders are retired. These are internal, co-packaged
adapter/runtime requests, not stored operations: upgrade the pair together and
regenerate requests with the current runtime. Mismatched versions and the old
top-level `operation_id` shape fail before provider access. Persisted receipt
schemas, operation ids and request fingerprints are unchanged; receipt recovery
does not require keeping an old request decoder.
The public completion facade rejects a reviewed canonical request
if authority has reverted to an unpromoted legacy path.

No provider default, promotion, permission, retention or storage format changes.
Rollback restores compatible code while retaining provider data, receipts and
writer fences. Code that does not recognize the request version cannot execute
it; regenerate a preview with compatible code instead of stripping its review
fields or changing the operation identity. Markdown stays a permanent display.
These changes close the terminal review/recovery family, not all leased metadata
updates, executor-held external-effect fencing, D1–D3 or whole-Goal cutover.

Shared provider conformance uses the complete production-scale fixture, both
native and imported records, stale review/validation, expired proof, lost commit
response and unchanged non-target state. Real File/SQLite Chat HTTP tests exercise
the packaged entrypoint and retry feedback. The frontend runtime decoder and shared action-review plan now recognize the
terminal basis for exactly Agent completion and Monitor stop. The packaged Chat
bundle includes the original-operation retry path and distinguishes pending
display from verified completion; no new configuration or visual control is required. Lark receives no new
command or transport in this slice.

## Quota-bound CLI completion

A declared deliverable passing its validator is not the same event as its Turn
being settled. The shared TypeScript settlement plan orders ordinary Todo
completion, durable `refresh-state` and one `quota spend-slot`, all bound to the
original Goal, Agent, Todo and Turn. Final `todo complete --no-follow-up` applies
only when the current contract permits final scope closeout. Ordinary completion
retains `active_goal` continuation without creating
an artificial successor. Its command has the condition
`todo_deliverable_complete`; required validation is never conditional or waived.
Qualified `in_flight_continuation` leaves unfinished work open and omits ordinary
completion rather than pretending the deliverable passed.

The shared Todo summary keeps missing-successor diagnostics and counts, but its
warning is review guidance, not an obligation to create a successor or terminate
a completed stage. Review remaining authorized Goal acceptance and the runnable
frontier; continue existing work or replan while scope remains. An empty queue
alone does not certify final scope closeout. Graph validation, empty-frontier
replan and terminal proof remain governed by their existing typed rules. This
guidance change is shared by CLI, status and quota readback.

If final closeout is attempted before writeback/accounting, CLI JSON and Markdown
return that same recovery plan. Supplied registry/runtime, project/state routes
and original lease proof are retained where the corresponding command accepts
them. A command template is guidance, not a lease or validation receipt.

The typed `completion_turn` identity retains the historical ordinary operation
id and names a separate deterministic closeout phase, with the same completion
key. Upgrading `active_goal` to `no_followup` requires the exact original ordinary
receipt, including its actor, lease and declaration commitment; the original
caller validator is not rerun and a retired lease is not reacquired. Revision-zero
receipts remain recoverable through their exact request commitment. Newer
declarations still require the matching receipt digest. Existing terminal
receipts recover before reading current authority, and current Goal Acceptance
criteria retain their freshness rules. No validation or spending gate is weakened.

This addition changes the CLI/managed guidance and canonical response readback,
not configuration. Reviewed Chat actions continue to use their explicit operation
identity and existing shared projection; no new frontend setting, visual control
or Lark transport is introduced.

## Retiring an original Turn with an existing successor

`todo supersede --turn-instance-id <original-turn>` now accepts the same exact
Goal/Agent/Todo/Turn guard identity as ordinary completion. It validates that
identity before the existing terminal authority admits retirement; lease,
actor and unchanged-intent recovery rules still apply. For promoted canonical
authority, pass `--successor-todo-id <existing-id>` directly to `todo supersede`.
The existing typed transaction links the successor, retires the original Todo
and releases its original lease in one commit. The successor keeps its declared
scope, owner, status and due time; the command neither rewrites leased work
requirements nor creates another replacement. Multiple existing links are
accepted, but cannot be mixed with `--next-agent-todo` or `--next-user-todo`.
Missing, other-Goal and self links fail before mutation. Exact replay recovers
the original receipt; changed intent is rejected. A fresh terminal operation
cannot append a new successor to an already retired Todo.

Compared with `todo update --successor-todo-id` followed by `todo supersede`,
the direct path removes the intermediate canonical mutation and CLI round trip.
It does not promise a provider-wide latency improvement. The old prelinked and
generated-successor paths remain supported. Unpromoted Markdown Goals reject
the new direct-link option with migration guidance; omitting the option keeps
their existing behavior. This extends the CLI and public lifecycle facade using
the existing transaction, without changing Chat/Lark actions or adding a
configuration switch.

Once that scoped retirement, the original durable writeback and one original
quota spend all exist, same-Turn `quota should-run` returns
`heartbeat_settled_skip`. Retirement does not certify the deliverable's validator,
does not close the Goal, and does not consume or advance the future Monitor.
Its due time, owner and successor relation remain canonical facts; independent
work is evaluated on the next fresh Turn.

Earlier unscoped supersede receipts are deliberately not inferred to belong to
a Turn. The original caller can retry the same supersede intent with its original
Turn ID and original lease proof to recover the committed retirement and append
the scoped receipt. This does not repeat research, validation or quota spending.
Unknown or mismatched Turn identities fail before lifecycle effects. The new
accepted CLI input is also documented by `todo --help`; existing unscoped CLI,
reviewed Chat and Lark behavior is unchanged.
