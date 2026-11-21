# issue_fix_reviewer_notification_sinks_v0

`issue_fix_reviewer_notification_sinks_v0` extends the canonical GitHub
reviewer request with bounded secondary delivery. The first adapter sends an
idempotent Lark/Feishu group message that mentions the same repository-grounded
reviewer and then reads the message back. The sink does not select a different
reviewer and never replaces GitHub review state as the source of truth.
The result contract and adapter injection point are provider-neutral; the
public CLI currently exposes only the bounded `lark_chat` adapter.

## Position In The Flow

The order is fixed:

1. `reviewer-plan` ranks candidates from repository-native evidence;
2. `reviewer-request` verifies author exclusion and establishes canonical
   GitHub coverage through a formal request or its permission-only comment
   fallback;
3. configured secondary sinks notify the same verified reviewer;
4. `pr-lifecycle` continues to derive review state from GitHub.

In no-write preview mode, a sink may validate its local configuration against
the selected reviewer. In execute mode, a secondary send is skipped unless the
canonical GitHub notification is already verified. A secondary failure is
reported separately and does not erase a successful GitHub request.

## Optional Reward Memory Gate

Reward Memory is a default-off, agent-scoped experiment. If the connected goal
enables it and the registered caller's experiment includes the exact
`reviewer_artifact.summary` surface with `automatic_recall=true`, reviewer
request planning runs the shared bounded hook and may produce a
read-only `reviewer_artifact_reward_memory_preview` even when secondary sinks
are absent or intentionally paused. Configured secondary notifications require
the same `issue_fix_reviewer_artifact_reward_memory_application_v0` packet to
pass its notification gate before sending. The caller supplies its own
`--agent-id`, a concise model-authored Chinese `--reviewer-summary`, and compact
`--reviewer-summary-reasoning`; LoopX never infers or impersonates another peer.

The adapter reuses the provider-neutral Reward Memory core and the goal's local
provider binding. OpenViking may back that binding, but it is not a protocol
dependency or hard-coded repository. Before external delivery, the gate checks
the exact surface, current PR identity and permalink, current-artifact
verification, memory readback, attribution digests, application state, and a
non-empty summary. A receipt for a different PR cannot be replayed.

Experiment resolution and read-authority construction happen before sink
routing. The normalized corpus supplies the read-authority kind, while the
normalized standing policy supplies `authority_source_ref`. A no-sink preview
may read the configured provider but never sends a notification, materializes a
notification lifecycle row, or performs an external write. Sink configuration
only changes whether the application receipt is mandatory for delivery. With
automatic recall disabled, this boundary performs zero provider calls.

This stricter behavior is bounded to the configured secondary effect. The
canonical GitHub review request or permission-only fallback executes first and
remains fail-open with respect to Reward Memory. Missing, stale, or unavailable
memory blocks only the secondary notification with
`reward_memory_reviewer_artifact_unverified`; it does not turn ordinary Issue
Fix work into a user gate or suppress the GitHub request.

The independent `reviewer_notification.before_send` surface may supply a
delivery policy immediately before a configured secondary adapter runs. It
performs one bounded automatic recall and accepts only a single distinct active
`hard_policy` whose `content_summary` is a compact JSON object with schema
`issue_fix_reviewer_notification_delivery_policy_v0` and a valid
`delivery_policy`. The application gate verifies the exact surface, current PR
identity, current-artifact check, result readback, and non-empty memory
attribution. A passed receipt has precedence over the explicit sink policy;
otherwise the explicit sink policy remains the fallback. With neither source,
delivery is unrestricted. Provider failure, no match, invalid content, or
conflicting policies fail open and never become a user gate. The existing sink
still owns queue receipts, idempotency, external send, and readback.

## Local-Private Input

`--notification-sinks-json` consumes
`issue_fix_reviewer_notification_sinks_input_v0`. The file is deliberately a
local capability packet rather than public issue-fix state:

```json
{
  "schema_version": "issue_fix_reviewer_notification_sinks_input_v0",
  "receipts": [],
  "delivery_policy": {
    "timezone": "Asia/Shanghai",
    "allowed_local_time": {"start": "09:00", "end": "21:00"},
    "outside_window": "queue_without_send"
  },
  "sinks": [
    {
      "sink_kind": "lark_chat",
      "sink_instance_key": "project-review-lane",
      "identity_scope": "project_dedicated",
      "reader_profile": "project-user-profile",
      "reader_identity": "user",
      "sender_profile": "project-review-bot-profile",
      "sender_identity": "bot",
      "bot_display_name": "Project Review Bot",
      "destination_id": "<private-chat-id>",
      "reviewer_identities": {
        "@service-owner": {
          "member_id": "<private-member-id>",
          "display_name": "Service Owner"
        }
      }
    }
  ]
}
```

The explicit reader/user binding verifies access to the approved destination.
The sender/bot binding independently verifies the dedicated bot identity and,
in that app's `open_id` namespace, verifies mapped reviewer membership before
performing send plus readback. Neither
binding depends on the machine's active/default Lark profile. The legacy
`bot_profile` field remains accepted for explicit manual configs, but a
goal-default config requires both bindings.

Execute mode verifies the configured reader's user credential before any
destination read or send. An unavailable or unverified user credential returns
the content-free blocker `reviewer_notification_reader_auth_required` and
performs zero external writes. This is an authentication gate for the existing
reader binding, not evidence that the reader and sender profiles should be
collapsed or rewritten. The operator restores the configured reader user login
with `lark-cli auth login`; LoopX keeps profile names and credential details out
of the public result.

Before sending, LoopX deduplicates one PR notification through three bounded
evidence layers: the durable PR-lifecycle receipt, an exact PR-link match in
the persisted `configured_chat_all` inbox, and a user-identity search of the
configured chat. The remote search is an evidence enhancement, not an action
authority boundary: if that user profile lacks `search:message`, LoopX records
`permission_fallback` and continues from durable receipt/inbox evidence rather
than projecting a user gate. A successful remote match is written back as the
same stable receipt; non-permission provider failures remain fail-closed.

`delivery_policy` is optional and provider-neutral. Its effective source order
is a verified `reviewer_notification.before_send` application, then this
explicit sink value, then the unrestricted default. When configured, execute
mode sends only while the current local time is inside the half-open
`[start, end)` window; overnight windows are supported. Outside the window,
LoopX performs no provider call and returns `queued_until_window` with a
compact `issue_fix_reviewer_notification_queue_receipt_v1`. The v1 receipt also
persists the public-safe summary and whether it came from a verified Reward
Memory artifact, so a later drain cannot regress a Chinese reviewer summary to
the raw PR title. Invalid timezone, time, or outside-window policy fails closed.
Preview remains read-only and is never converted into a queued execution. The
execute path uses the trusted invocation clock for this decision; the public
`--generated-at` artifact field cannot move a send into or out of the delivery
window.

The grouped state monitor drains due receipts with:

```bash
loopx issue-fix reviewer-notification-drain \
  --goal-id <goal-id> \
  --project <project> \
  --execute \
  --format json
```

This is a deliberate queue-schema cutover: existing
`issue_fix_reviewer_notification_queue_receipt_v0` rows must be manually
migrated to v1 before enabling the grouped drain. The runtime does not retain a
v0 compatibility reader because a v0 row lacks the persisted Chinese summary
and therefore cannot satisfy the current reviewer-message contract. A detected
v0 row fails closed with `reviewer_notification_queue_v1_migration_required`;
it is never silently treated as an empty queue.

One bounded invocation scans every queued PR in the review-required state
bucket; it does not create one continuous monitor per PR. Before each message,
LoopX refreshes compact live GitHub state and cancels stale queues for closed,
merged, draft, approved, or fully-covered reviewer sets. A send remains one PR
per message (and at most one message per configured sink) and is complete only
after semantic readback and receipt persistence. If only some queued reviewers
already reviewed, the drain targets only the remaining reviewers. Temporary CI
or branch-state changes keep the queue intact, and the drain always reuses the
timezone and allowed local-time window frozen in the v1 receipt. A sink removed
from current configuration cancels only its own stale receipt; other configured
sinks for that PR continue independently.

Semantic history dedupe is sink-scoped. A single Lark sink may use the goal-level
`feedback_inbox_config`; multiple Lark sinks must each declare their own
`feedback_inbox_config`. If a multi-sink inbox cannot be attributed to one sink,
the drain fails closed and preserves the queue instead of suppressing another
chat's delivery. Bounded executions also report `remaining_due_pr_count` and
return `partial_drain` whenever verified or cancelled work coexists with due
rows held by a delivery window or left for the next `--limit` batch.

Profile names, `destination_id`, and `member_id` are execution inputs. They are
never copied into the result, domain state, todo, Kanban, PR, or public log.
The first contract requires a named, project-dedicated sender profile and expected
`bot_display_name`, verifies the live bot identity before every send, and
rejects shared/default or mismatched identities. This prevents a long-running
project employee from silently speaking as an unrelated application.

Identity mapping is advisory until both sides are verified: the GitHub handle
must come from the live author-excluding reviewer packet, and the messaging
member must be resolved in the approved destination. Missing or ambiguous
mapping produces `reviewer_notification_identity_unresolved` without sending.

## Authority, Idempotency, And Verification

The same standing reviewer-notification authority that permits the canonical
request may permit an explicitly configured secondary sink. `--execute` is the
write assertion; preview never calls the provider.

Each logical `(repository, PR, sink instance, reviewer set)` produces a stable
`sha256:` idempotency key. The Lark adapter derives a provider-bounded key from
that digest without exposing it in the human-visible message. The message uses
public PR metadata to name the PR, linked issue when available, and a compact
summary of the fix. The full verified key is returned as a receipt. Callers
store only that compact receipt in existing issue-fix state and pass it back on
retry; a matching receipt returns `already_notified` without a provider call.

For connected goals, register only the repo-relative local-private pointer:

```bash
loopx configure-goal \
  --goal-id example-goal \
  --issue-fix-reviewer-notification-config \
  .loopx/config/issue-fix/reviewer-notification-sinks.json \
  --execute
```

Then `reviewer-request --goal-id example-goal --project ...` discovers the
config automatically. Execute mode loads the PR's existing lifecycle row or
auto-materializes it from a fresh compact GitHub lifecycle read before any
external notification, merges verified hashed receipts into the private
input, and writes new verified receipts or compact queued delivery metadata
back to that same row. A restart preserves a queued item; a later verified send
removes the matching queue entry while retaining the stable receipt. Retry
outside the window returns `already_queued` with the original queue receipt,
so neither the provider nor local state changes. Retry therefore remains
idempotent without a second ledger. The live reviewer route is authoritative
for unsent work: an existing GitHub-covered reviewer may be notified only as
that same reviewer, never replaced by a different mapped sink identity. Each
execute reconciliation atomically replaces the PR's unsent queue, cancelling
stale targets when coverage changes, a review completes, or no current sink
identity remains. Verified receipts are append-only and are never cancelled by
this queue replacement. A provider failure before any external write preserves
the matching current queue entry for retry; an unverified post-write result does
not requeue and risk a duplicate send. Goal boundary/status
projections expose only that the capability and pointer are configured; they
never expose the pointer value or profiles
(`config_pointer_registered=true`).

A zero exit status is insufficient. The adapter requires a message id from the
send response, fetches that message with the same dedicated bot profile, and
verifies both the id and PR URL. Results distinguish `preview_ready`,
`queued_until_window`, `already_queued`, `sent_verified`, `already_notified`,
`sent_unverified`, and `gate_required`.
Permission or group-membership errors become the concrete
`lark_bot_group_access_required` gate.

## Dedicated Bot Setup

For a Lark sink, provision one app/bot identity for the project lane, grant only
the scopes required for sending and group/member resolution, publish the app,
and have the target group's owner or administrator install it. A local named
CLI profiles select reader and sender credentials explicitly. The contract
never falls back to the machine's default user or bot profile.

The setup gate should tell the owner exactly which missing invariant to repair:

- dedicated app/bot exists and has the intended visible name;
- bot capability is enabled and the version is published;
- send and approved chat/member-read scopes are granted;
- the bot is a member of the approved destination;
- each GitHub reviewer maps to one verified destination member.

## Public-Safety Boundary

Every public result keeps these fields false:

- `private_destination_captured`
- `private_member_ids_captured`
- `private_bot_profile_captured`
- `raw_provider_payload_captured`

Only the public PR URL, reviewer handles, sink kind, status, compact blocker,
and hashed receipts may leave the adapter. Credentials, raw member rosters,
chat identifiers, message identifiers, provider errors, and local config paths
remain private.

## Validation

Run:

```bash
python3 examples/issue-fix-reviewer-notification-sink-smoke.py
python3 examples/issue-fix-reviewer-request-smoke.py
python3 examples/issue-fix-reviewer-notification-drain-postwrite-smoke.py
```

The provider-neutral fixture covers preview, dedicated-identity enforcement,
author exclusion, identity-resolution gates, one send plus readback, stable
receipt retry, permission classification, unverified writes, and public-safety
redaction. It also covers normal and overnight delivery windows, zero-call
queuing, and invalid-policy fail-closed behavior. The reviewer-request smoke
proves the sink is a real post-canonical call site rather than a disconnected
adapter and verifies queue persistence/removal across lifecycle restarts.
