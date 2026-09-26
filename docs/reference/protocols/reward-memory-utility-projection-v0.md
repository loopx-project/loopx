# Reward Memory Utility Projection v0

`memory_utility_projection_v0` is the read-only result of reducing a complete,
append-only stream of validated `memory_utility_observation_v0` records. It is a
local LoopX projection, not a provider ledger or a memory lifecycle record.

```bash
loopx reward-memory utility-project \
  --input utility-observations.json --format json
```

The input object contains exactly `observations`, `scope`,
`retrieval_snapshot_ref`, and `policy_snapshot_ref`, with optional
`reducer_version` and `previous_projection`. `previous_projection` is an
identity check only; callers still provide the complete observation stream so
reduction is reproducible after restart.

## Projection identity

The reducer identity is derived from:

- `scope.agent_id`, `project_id`, `corpus_id`, and `surface_id`;
- the retrieval and policy snapshot references; and
- the explicit `reducer_version`.

Changing any of these values produces a different `reducer_identity`. A
`previous_projection` with a different identity is rejected before any subject
state is returned. The default implementation version is
`memory_utility_reducer_v0`.

## Reduction rules

The reducer first validates every observation with the Stage-1 validator and
requires exact scope and snapshot matches. It then applies these rules:

1. An exact semantic replay with the same `observation_id` is counted once.
   Retry-only `created_at` differences do not create another observation.
2. Different payloads under one `observation_id` are conflicting deliveries.
   They add no support and produce a `review_required` projection with a
   rejection-level quarantine proposal (`quarantine_proposed: true`). Because
   the conflicting identity is not admitted as a subject, the proposal remains
   attached to its bounded rejection record.
3. Evidence precedence is deterministic:
   `owner_correction` > `controlled_replay` > `deterministic_effect` >
   `evaluator_inference` > `insufficient`.
4. The highest evidence tier governs the effective direction. If that tier is
   `unknown`, weaker directional observations cannot manufacture a utility
   direction; the subject becomes `unknown` and requires review when such
   weaker direction exists. Otherwise, only the strongest directional tier
   contributes the effective label and bounded utility estimate. Weaker
   observations remain in support counters and history. A same-tier
   directional disagreement becomes `unknown` and requires review.
5. `unknown` contributes lineage and support coverage but never creates a
   utility direction by itself.
6. `item` subjects contain exactly one memory digest. `set` subjects contain
   the complete applied set. Set-level evidence is never copied into item
   subjects. `none` subjects are lineage-only and remain separate.

Utility is bounded to `[-1.0, 1.0]`; confidence and uncertainty are bounded to
`[0.0, 1.0]`. Support counters retain all four labels and all five evidence
tiers. The projection records the latest accepted observation identity and
time, plus a bounded public-safe observation history. When the history budget
is exceeded, it retains the latest entry for every subject and fills the
remaining slots with the newest entries, so subject latest fields remain
auditable after truncation.

Readback validation remains fail-closed after truncation: it derives the
strongest evidence tier from the aggregate counters and requires every
effective directional label to have support (with an explicit same-tier
conflict exception for `unknown`). Projection timestamps must also remain
canonical; surrounding whitespace is rejected.

## Review and safety boundary

Negative utility creates an `attenuation_proposed` review state with the
suggested action `attenuate_or_review`. A conflict creates a `conflict` state;
unresolved attribution creates `unresolved_attribution`. These are proposals
only. Conflicting deliveries additionally carry a rejection-level
`quarantine_proposed` flag; it never authorizes deletion or mutation. Every
subject and the projection itself set:

```json
{
  "read_only": true,
  "automatic_deletion": false,
  "action_authority_granted": false,
  "provider_write_performed": false,
  "external_writes_performed": false,
  "raw_content_captured": false
}
```

Malformed observations, scope or snapshot mismatches, and projection identity
mismatches return `status: "rejected"` with no subjects. The reducer has no
provider calls, ranking hooks, quota or scheduler integration, and does not
change the main work result or settlement. Callers may inspect a rejected
packet while continuing their normal fail-open work lane.

The projection accepts only opaque references and canonical memory digests. It
does not retain raw memory text, prompts, transcripts, credentials, local
paths, or provider-private URIs.

## Public shape

The top-level packet is `memory_utility_projection_v0` and includes bounded
counters for accepted, duplicate, conflicting, and rejected deliveries;
`subjects`; subject-level `review_proposals`; conflict rejection records with
quarantine metadata; and `observation_history`. A subject carries
its attribution level, digest set, effective label and evidence basis, bounded
utility/confidence/uncertainty, support counters, evidence-strength counters,
latest observation, and review state.

The projection is deterministic for the same semantic observation stream,
scope, snapshots, and reducer version, independent of input order.
