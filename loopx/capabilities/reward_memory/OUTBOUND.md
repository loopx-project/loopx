# Outbound guidance recall

[中文版](OUTBOUND.zh-CN.md)

This optional Reward Memory surface recalls reviewed operating preferences
before an agent sends a message. It is not a transport, a text classifier, or
permission to send. The first shipped caller is goal/agent-bound
`loopx lark-inbox send` and `reply`; other tools and Goal Topic auto-replies are
not intercepted by this integration.

## Enable and validate

Use the existing agent-scoped Reward Memory experiment configuration. Add
`outbound_message.before_send` to the corpus and standing-policy surface scopes
and to `surfaces`, using adapter `scoped_feedback`. Set the exact
`peer_ref: agent:<agent-id>` and `automation.automatic_recall: true`. Use a
function-boundary profile with one query and a small result limit. Ingest only
explicitly reviewed, public-safe operating guidance as `soft_preference`;
do not upload draft messages or private incident transcripts.

```sh
loopx configure-goal --goal-id <goal-id> \
  --reward-memory-config .loopx/config/reward-memory.json \
  --reward-memory-agent <agent-id> --execute
loopx reward-memory experiment-status --goal-id <goal-id> --agent-id <agent-id>
loopx lark-inbox send --goal-id <goal-id> --agent-id <agent-id> \
  --route-key <configured-route> --text '<message>' \
  --message-purpose help --provider-preflight --format json
```

Provider preflight verifies identity, membership, mentions and the provider's
dry-run rendering before recall. A plain preview without `--provider-preflight`
does not call either provider. The result includes `outbound_guidance` when the
surface is active. It performs no send. With relevant guidance, even an initial
`--execute` returns `agent_review_required` and zero writes.

The executing agent must read the guidance, check current facts, alternatives,
recipient and duplicates, and decide whether a message is still appropriate.
If it is, repeat the same send/reply command with `--execute` and
`--reviewed-guidance-digest <returned-digest>`. This is an agent review step,
**not a user approval gate**. The digest binds the reviewed guidance, purpose,
scope, sender, destination, placement and message. Changes invalidate it.
It proves acknowledgement, not the quality or truth of the agent's reasoning.
Transport idempotency and readback remain owned by the existing sender.

## Destination recall and required guidance

For a verified destination, send and reply now run two separate bounded recall
boundaries: destination experiences first, then message-purpose guidance. Each
uses the existing one-query profile. Results are merged by candidate reference;
the receipt reports the total calls and all application receipts. This changes
enabled outbound recall from one query to two; feature-off behavior is unchanged.

Add optional `destinations` to the outbound surface configuration:

```json
"destinations": [{
  "destination_digest": "<64 lowercase hex characters: SHA-256 of the exact chat ID>",
  "query_label": "Example Team",
  "required_candidate_refs": ["candidate:reviewed-record"]
}]
```

The sender binds the actual verified chat ID, not a caller-supplied group name.
Only its hash and the explicitly configured query label enter retrieval; labels
must be appropriate for the configured provider. A renamed group keeps the same
identity; same-named groups have distinct digests. Update the label when renamed.
Without a label, the destination hash is still queried. Store that identifier or
label in relevant reviewed guidance so destination search can find it.

Up to three required references get independent exact-reference queries. Every
reference must resolve to an active, in-scope record and reach the merged output.
Missing records or a provider failure that leaves one missing return
`required_guidance_missing` and stop delivery, including urgent messages and
reused review digests. Present required guidance always requires agent review,
even for urgent messages. At most five recall boundaries run per send. This is
an explicit **required-read contract**, not a text-based permission classifier:
the agent must still obey prohibitions, and acknowledging a digest does not
grant permission. It does not enforce bans through unrelated senders.

Enabled agents with missing/invalid configuration or inconsistent corpus scope
now receive `configuration_error` and zero sends, before any memory-provider call.
Previously these failures silently removed the hook. Repair and check `reward-memory experiment-status`.
do not discard invalid required fields or automatically disable the capability.
Explicitly disabled agents, valid `automatic_recall: false` configurations and
valid configurations without this surface still preserve the original sender.

OpenViking reward retrieval requests L2 bodies and overfetches at most 32 hits,
canonicalizes chunk URIs, and skips summaries, unreadable and malformed records
before filling the bounded result budget. Other provider namespaces retain their
previous retrieval behavior. Scope/lifecycle checks and candidate deduplication
precede the final guidance limit; this is bounded refill, not an exhaustive scan.

Validate with the real destination and purpose preview above, without putting
the desired answer or candidate ID in the message. Check that required refs are
present, `required_guidance_complete` is true, and external writes remain false.

Purposes are `help`, `progress`, `urgent`, and default `unspecified`; there is
no substring inference from message text. Urgent notices recall guidance but
do not wait for review unless required guidance is configured. Empty/unavailable
advisory memory preserves the existing send
path and never asks the user to repair the provider. Permission or sender
validation failures still block normally. Hard-policy memories are not
interpreted by this advisory adapter.

The generic implementation lives in `reward_memory.outbound`; the Lark adapter
receives a callback over an opaque intent digest with optional destination binding. It owns no memory store or
project-specific escalation rule. Raw text, chat ids and sender profiles never
enter the recall query. Guidance is returned in the caller's private response,
not sent to the recipient or persisted into the public registry.

## Disable and coverage

Set `automation.automatic_recall: false` to disable recall for the experiment,
or remove this surface from the experiment to disable only outbound recall.
Unconfigured agents and existing direct provider calls preserve their previous
behavior. Activation grants no provider credentials or external-write authority.

Tests cover real recall machinery, readback, agent scope, intent invalidation,
unavailable providers, urgent notices and both actual sender paths with a
synthetic transport. A live read-only provider check is separate from those
tests; no test should send a group message as a smoke side effect.
