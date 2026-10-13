# Research observation v0

Explore owns this optional evidence contract. Its first consumer is
`loopx explore observe`; `loopx explore summary` and the existing Lark Explore
node summary render the same derived facts. This is the M1 evidence substrate
and M2 **read-only shadow**. The M3 development boundary adds optional execution
attribution, an explicit-only live replan gate, and legacy/native File/SQLite
Todo closeout checks, source-qualified duty retirement, lease-fenced resumption,
and shared status/Explore presentation. Maintainer-reviewed integration and live
model/scientific or remote Lark qualification remain open.

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
`#3173` composition/quota behavior is unchanged without the new policy. No research policy, scheduler,
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
node observations to investigate omitted candidates. Cold cards do not select
obligations. The live policy below uses the full internal candidate set;
compaction cannot erase an enforceable gap.

## Execution attribution

An experiment may additionally record `execution_lineage`:

```json
{
  "schema_version": "research_execution_lineage_v0",
  "goal_id": "research-demo",
  "gap_id": "research-composition-0123456789abcdef",
  "replan_obligation_id": "replan-0123456789abcdef",
  "successor_todo_id": "todo_joint",
  "agent_id": "research-agent"
}
```

Use the actual gap, obligation and Todo identities supplied by the current
work contract; the example tokens do not establish an obligation. Include this
object in the experiment envelope and issue `explore observe --agent-id` with
the same actor. `progress.work_item_id` must equal `successor_todo_id`.
The source registry must contain that Goal. The writer reads the exact Todo
through the existing Todo reader, including promoted canonical authority when
configured; it does not accept a caller-supplied Todo snapshot.

A new write requires a same-agent, currently runnable `advancement_task` with
`action_kind=joint_probe`, the exact `replan_obligation_id`, and exactly this
experiment in `explore_result_node_refs`. A declared `target_key` must also
equal the experiment id. Deferred, blocked, rejected by the existing acceptance
guard, archived, executor-excluded, other-agent or unrelated work cannot supply
execution attribution. The experiment must belong to this pending binary gap
and match its current input observations. An unchanged/read/ACK observation or
an execution result without evidence is rejected.

The fingerprint includes this object, research schema, experiment identity,
input fingerprints and generic progress/evidence. Node and batch writers
reject new execution-lineage writes; use `explore observe` for its task read.
Exact historical replay still writes nothing, even after task or input changes.
It does not refresh current task authority. Presentation compaction does not
limit the internal candidate set used for attribution.

This is a task snapshot attribution check under the Explore log lock, not an
atomic task lease/effect authorization or a Todo/Goal completion receipt.
Any subsequent shared settlement must independently validate its current
authority and research evidence. Envelopes without `execution_lineage` retain
their diagnostic behavior; they cannot be promoted into execution lineage by
reading or replaying them. The live replan gate below adopts these facts;
the same typed rule also guards current legacy and native File/SQLite Todo closeout.

Completed Todo records retain evidence lineage after archival or claim clearing.
The projection reads retained canonical history rather than a compact active
Todo list. An archived open/deferred row cannot schedule or attribute a new
observation; missing or mismatched history and invalidated inputs still reject
current coverage. Historical evidence grants no current claim, lease or execution
authority.

## Explicit-only live replan gate (M3 development)

Preview and apply the policy through the existing Goal configuration owner:

```sh
loopx configure-goal --goal-id research-demo --explore-harness-enabled \
  --explore-composition-mode explicit_only --explore-composition-scope-id joint-scope
loopx configure-goal --goal-id research-demo --explore-harness-enabled \
  --explore-composition-mode explicit_only --explore-composition-scope-id joint-scope --execute
loopx quota should-run --goal-id research-demo --agent-id research-agent --turn-instance-id research-turn
```

The Goal capability editor exposes the same activation, policy and scope fields
through its existing revision-checked preview/apply API. Both harness activation
and `composition_mode=explicit_only` are required. The opaque
`composition_scope_id` identifies experiment coverage; it does not replace the
Goal vision or grant claim, lease, effect, provider or external-execution rights.

Quota and `refresh-state` read the same live Explore/Todo frontier. A compact
capability guard pins the selected gap and input/policy revision to the original
Turn. The existing common replan owner computes its obligation id. User/handoff
gates, runnable work, vision acceptance and ordinary succession/review duties
retain priority before this capability gap enters monitor fallback.

Only an exact same-agent runnable `joint_probe` successor with the current
obligation and one current binary experiment suppresses duplicate planning.
`todo add --replan-obligation-id` rejects unrelated or deferred successors.
Scheduling does not observe the gap. A terminal experiment result must include
execution lineage, current input fingerprints and the configured coverage scope
before it becomes live observed evidence. Its complete generic progress and
research fingerprint bind writeback; omitted semantics, unrelated progress,
reads/ACKs, stale inputs and replay cannot discharge the selected duty.

The supported replan exits are a new runnable experiment successor, its typed
observed result, an evidence-backed candidate dismissal or a fresh exact blocker
with a typed resume condition. A negative scientific conclusion is valid evidence.
Generic blocked/terminal claims do not certify research closure. Current legacy
and native File/SQLite closeout require the task's exact terminal experiment or
dismissal evidence. An invalidated admitted duty uses the lifecycle retirement
below; a Todo becoming done never certifies Goal closure.

An observation may include `composition_resolution` with schema
`research_composition_resolution_v0`. It requires execution lineage and nonempty
`evidence_ids` attributable to the same progress observation:

- `disposition=dismissed` requires coverage-backed `no_followup`, a matching
  `no_followup` closure basis, an experiment node in `dead_end` and typed `basis`
  (`duplicate`, `invalid`, `unsafe` or `outside_scope`). It counts as `dismissed`,
  never an observed experiment. The exact evidence permits normal Todo completion
  or supersession without pretending the experiment ran.
- `disposition=deferred` requires `blocked` progress and its canonical
  `blocker_id`; it cannot assert complete terminal coverage. The linked experiment
  is blocked, its current Todo is blocked/deferred with
  `resume_when=todo_done:<blocker_id>`, and the same-agent active blocker Todo
  names that experiment Todo in `unblocks_todo_id`. The common Todo resume owner
  must prove the prerequisite is still pending. Missing, unrelated, archived or
  already-resolved blockers cannot suppress the gap. Reusing a previously claimed
  blocker cannot provide fresh writeback progress.

These are caller-declared evidence dispositions, not independent scientific
verification or execution permission. Resolving the prerequisite exposes the
pending gap; reopening the task and experiment makes it scheduled, not observed.
Native hard-lease callers must release their active lease before the existing
blocked lifecycle accepts a typed wait. Resume clears the wait and still requires
a fresh execution lease. Arbitrary deferred successors remain rejected.

When refreshing a replan-bound Turn from a canonical experiment observation,
use `--progress-work-item-id` for the observation's exact source Todo and repeat
its complete generic progress fields. This selector does not rebind settlement
away from the original obligation. The shared gate still verifies the actual
Todo, current inputs, scope, evidence and fingerprint; changed or incomplete
observations are rejected. A new concrete blocker is an explicit replan progress
exit; it does not use the Todo-bound `outcome_gap` no-spend closeout contract.

The native transaction evaluates capability eligibility against its actual
provider Todo after actor/lease admission and before caller validation effects.
A fixed source-selected Python adapter holds the canonical Explore log lock
through the native CAS. It only reads the graph; TypeScript decides whether
the observation, task and input lineage match. The model cannot supply an
approval flag, snapshot or executable to bypass this guard. Accepted evidence
is retained in the terminal operation receipt. Replaying that receipt reports
history even after later input invalidation, without rewriting state or evidence.
Completing through a different terminal verb cannot avoid the same check;
typed candidate dismissal above is a legal retirement without experimental outcome evidence.

The writer holds the Explore log lock while qualifying and persisting its
research writeback. The persisted guard uses bounded scalar rollout fields;
both TS and Python receipt adapters retain the same selected facts. Public
quota cards omit internal task joins, transition candidates and full result
observations, with counts for omitted display cards.

`status --goal-id research-demo --agent-id research-agent` displays these same
live counts and bounded gaps in `project_asset.bounded_research_frontier`.
`explore summary --goal-id research-demo --agent-id research-agent` exposes
`research_execution_frontier` and annotates existing node summaries. The same
summaries feed Lark projection fields; `feishu-sync` and `feishu-card` accept
the same Agent selector. A live-policy Goal with multiple registered Agents
requires that explicit selector; a single Agent is selected automatically for
presentation only. These read paths do not start a Turn, mutate Todo state or
authorize remote writes. Inactive policy preserves the existing projections.

Disable only the new policy while retaining the existing planner:

```sh
loopx configure-goal --goal-id research-demo --explore-composition-mode disabled --execute
```

Removing the policy fields also restores the existing behavior. Changing scope,
inputs or activation cannot retroactively erase an admitted Turn's guard; stale
writeback remains rejected. Its rejection supplies an exact `retirement_contract`
when current source facts prove invalidation. Reuse its blocked progress fields
with the original `--replan-obligation-id` and `--turn-instance-id`, and set
`--delivery-outcome outcome_gap`. The capability owner revalidates the current
source and generates `capability_obligation_retirement_v0`; caller approval flags
or arbitrary blocker/evidence ids cannot provide this proof. Runnable Todos bound
to that duty prevent retirement until their owning lifecycle pauses them.
Invalid, missing or unavailable evidence sources cannot stand in for invalidation.

The common settlement owner verifies the original guard, durable writeback,
current revision and exact progress fingerprint, then returns
`closeout_kind=capability_duty_retired_no_spend`. It closes only that admitted
Turn. No Todo or Goal is completed, and no quota slot is spent; already committed
debits remain ordinary historical debits. Old-Turn reentry and spend requests
return the same no-debit closeout without appending accounting. The next Turn
reassesses the current frontier, including remaining acceptance and paused work.
Keep the original receipt and evidence rather than deleting them to manufacture
settlement. No policy setting authorizes Goal
termination, live model qualification, benchmark launch, deployment or release.

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
