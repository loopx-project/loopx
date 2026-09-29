# Research observation v0

Explore owns this optional evidence contract. Its first consumer is
`loopx explore observe`; `loopx explore summary` and the existing Lark Explore
node summary render the same derived facts. This is the M1 evidence substrate
and M2 **read-only shadow**, not M3 obligation/writeback enforcement.

## Record and read back

Create the public-safe input nodes through the existing `explore node` command.
Then record one JSON envelope on an existing node:

```sh
loopx explore observe --goal-id research-demo --observation-json observation.json
loopx explore summary --goal-id research-demo --format json
```

```json
{
  "schema_version": "typed_research_observation_v0",
  "explore_node_id": "node-a",
  "progress": {
    "schema_version": "typed_progress_observation_v0",
    "work_item_id": "todo-a",
    "result_class": "exploration_exhausted",
    "coverage_scope_id": "scope-a",
    "coverage_complete": true,
    "evidence_ids": ["ev-a"]
  },
  "closure_basis": {
    "schema_version": "research_closure_basis_v0",
    "disposition": "bounded",
    "constraints": [{"kind": "invariant", "id": "boundary", "role": "decisive"}],
    "evidence_ids": ["ev-a"]
  },
  "composition_candidates": [{
    "target_node_id": "node-b",
    "basis": "explicit",
    "interaction_kind": "state_interference",
    "evidence_ids": ["ev-a", "ev-b"]
  }]
}
```

Before recording this candidate, `node-b` must exist in the same Goal and
already attribute `ev-b` through its node evidence or research observation.
For a pending composition gap, both inputs must currently be `resolved` or
`dead_end`, with coverage-backed terminal research observations. Closed node
status, a finding count, an ACK or a read alone cannot establish that evidence.

## Wire and compatibility

The envelope composes the existing generic progress codec. It requires a
`work_item_id`; it does not change generic progress semantics. Research-only
unknown fields are rejected. Node and evidence identities are opaque tokens
of 1–128 characters (`A-Z`, `a-z`, digits, `.`, `_`, `:`, `-`, beginning with a
letter or digit). Raw text, local paths, credentials and source bodies do not
belong in the envelope; the shared public-safety validator also applies.

`closure_basis` has schema `research_closure_basis_v0`. Dispositions are
`bounded`, `exhausted`, `no_followup`, `blocked`. Constraints retain their
ordered path; matching uses exact `(kind, id)` identity. Kinds are `stage`,
`decision`, `invariant`, `dependency`, `resource`, `policy`; roles are
`decisive` or `supporting`. Duplicate identities fail closed. At most twelve
constraints/evidence ids are admitted. Closure evidence must be a nonempty
subset of this observation's progress evidence.

`exploration_exhausted` and `no_followup` require complete coverage, its scope,
evidence and at least one decisive constraint. A blocked disposition cannot
assert terminal coverage. `blocked`, `unchanged` and `advanced` observations
are recordable but cannot establish terminal composition input eligibility.

Candidates have `basis=explicit`, a distinct known target and evidence
attributable to both inputs. Interaction kinds are `shared_constraint`,
`producer_consumer`, `state_interference`, `order_dependency`,
`resource_coupling`, `unknown_interaction`. At most three candidates per
observation are accepted; excess input is rejected rather than truncated.
Candidate identity hashes the Goal and sorted binary input set. Reverse
declarations merge; shared constraints alone never generate pairs.

An accepted envelope receives a content fingerprint and is stored as an
optional `research_observation` on an append-only node revision. Existing
events without that field preserve their projection shape and require no
research runtime operation. Older readers that validate node fields strictly
must upgrade before reading a log containing the optional envelope. Existing
`#3173` composition/quota behavior is unchanged. No research policy, scheduler,
claim, lease, quota, generic settlement or Goal acceptance is enabled by this
command.

## Results, invalidation and replay

A binary experiment uses the existing `experiment` node and exactly two
outgoing `depends_on` edges. Before `observe`, take `input_observations` from
its current candidate card in `research_frontier.gaps[]` and include that array
in the experiment envelope. Each entry contains `node_id` and `fingerprint`.
The writer requires the exact current input observations. An evidence-backed
terminal experiment with matching lineage marks the shadow gap `observed`;
the sign of the conclusion is not a scoring criterion.

The shadow states are `pending`, `ineligible`, `observed`. An active experiment
identity is displayed but scheduling does not establish an observed result.
An untyped experiment closeout, a result on a different input set, or stale
input fingerprints cannot close the gap. Updating an input node through
`explore node` invalidates its current observation until fresh evidence is
recorded. Historical explicit claims remain visible as ineligible candidates.

Exact observation replay returns `replayed=true`, `written=false`, even if
inputs have since changed. It neither rebinds evidence nor appends a revision.
New writes validate attribution and append under the existing log lock. Both
the node writer and batch writer enforce attribution for typed envelopes.

The cold projection returns total counts and at most three cards, with explicit
`projected_count` and `omitted_count`. It makes no ranking-quality claim and
does not add a full candidate list to quota packets. Inspect the canonical
node observations to investigate omitted candidates; no obligations are lost
because this slice creates none. Dismissal, deferral, observation-to-Todo
lineage, shared write gates and hot status adoption remain M3 work.

## Disable and authority boundary

There is no new automatic activation. Stop issuing `explore observe` to stop
new evidence writes; reading existing observations is read-only. To invalidate
an input's research eligibility, use its normal `explore node` revision before
fresh investigation. Preserve the append-only log; do not delete evidence as a
rollback procedure. Disable existing harness activation through its existing
Goal configuration when applicable; this command does not enable it.

These receipts prove typed attribution and revision consistency, not that an
external experiment ran or that its scientific conclusion is true. Live model
and real research qualification remain separate RFC gates. Lark remains an
optional extension: projection does not authorize a remote sync, credential
access or network execution.
