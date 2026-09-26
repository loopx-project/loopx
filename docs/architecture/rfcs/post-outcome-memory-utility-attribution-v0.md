# RFC: Post-Outcome Memory Utility Attribution v0

- Status: Draft; Stage 1 shipped, Stage 2 implemented in follow-up issue #3824, Stage 3+ remain proposed
- Date: 2026-08-15
- Tracking issues: [#3214](https://github.com/huangruiteng/loopx/issues/3214), [#3824](https://github.com/huangruiteng/loopx/issues/3824)
- Decision boundary: how LoopX attributes later, verified work outcomes to previously recalled memory and exposes a bounded utility projection
- Capability owner: existing `reward_memory`
- Provider boundary: optional evaluator providers and context-provider adapters, including OpenViking

## 0. Summary

A recalled memory can be relevant without being useful. An agent can say that it
applied a memory without the memory improving the result. A successful task can
also contain several recalled memories whose individual contributions cannot be
distinguished.

This RFC adds a post-outcome attribution sidecar to the existing reward-memory
contract:

```text
recall receipt
  -> application receipt
  -> verified work outcome
  -> optional utility evaluator
  -> append-only utility observation
  -> deterministic scoped projection
  -> optional provider rank-prior effect + readback receipt
```

The sidecar is default-off and fail-open. It never blocks the work lane. Its
evaluator proposes typed observations; it does not gain authority to choose
work, edit memory, spend another lane's quota, or directly mutate provider
ranking.

The word "global" may describe one consistent evaluator contract over multiple
registered lanes. It does not describe an omniscient supervisor or a
cross-scope utility pool.

## 1. Problem

The current reward-memory architecture already separates recall from
application. Stage 3 records `applied`, `ignored`, or `refuted`, retains exact
provider references in process, and emits only opaque public references. Stage
5 then maps `applied` to a dogfood `hit`, `refuted` to `refute`, and other
outcomes to `miss`.

That mapping establishes a useful execution trace, but it conflates four
different facts:

1. **Retrieval relevance**: did semantic or structured retrieval select the
   memory for this context?
2. **Application disposition**: did the working agent say it used, ignored, or
   rejected the memory?
3. **Work outcome**: did the intended effect or task succeed?
4. **Memory utility**: did this memory improve or harm that outcome, compared
   with a reasonable alternative?

The fifth fact, memory lifecycle and authority, must also remain separate. A
high-utility memory is still observation. It cannot grant permission or replace
current repository, goal, or user authority.

Without this separation, the system can reinforce memories that are merely
frequent, correlated with easy tasks, or confidently cited by a model. It can
also assign the same terminal reward to every memory in a trajectory even when
only one helped.

## 2. Decision

LoopX should extend the existing `reward_memory` capability with a
provider-neutral, post-outcome utility-attribution contract.

The contract has three new logical surfaces:

- `memory_utility_observation_v0`: an append-only statement that binds one
  application receipt, one later outcome, and an attribution judgment;
- `memory_utility_projection_v0`: deterministic, scoped state reduced from
  accepted observations;
- an optional provider effect/readback seam for applying a bounded rank prior.

This does not create a new built-in `supervisor` capability. Recall receipts,
application receipts, and dogfood settlement already belong to reward memory,
so attribution belongs to the same change reason and lifecycle. A
`memory_utility_evaluator` is an optional provider role. OpenViking remains an
optional context provider; its URI, lineage, and policy-snapshot mechanics stay
inside that adapter boundary.

## 3. Required semantic separation

The following fields MUST NOT be collapsed into one score or status:

| Concern | Example values | Owner |
| --- | --- | --- |
| Retrieval | score, rank, response digest | context provider |
| Application | `applied`, `ignored`, `refuted` | working agent receipt |
| Outcome | effect status, rubric score, task result | LoopX effect/outcome evidence |
| Attribution | `helpful`, `harmful`, `neutral`, `unknown` | evaluator observation |
| Utility state | bounded prior, support, uncertainty | deterministic LoopX reducer |
| Lifecycle | retain, edit, retire, quarantine | existing owner-authorized memory lifecycle |

In particular:

- `applied + success` does not prove `helpful`;
- `refuted` does not by itself prove `harmful`;
- retrieval frequency and recency do not prove utility;
- a model's confidence is not outcome evidence;
- a negative utility observation does not authorize deletion;
- a higher rank never turns memory into instruction or authority.

## 4. Utility observation contract

A public-safe `memory_utility_observation_v0` should contain the smallest
fields needed for replay and audit:

```json
{
  "schema_version": "memory_utility_observation_v0",
  "observation_id": "muo_<stable_digest>",
  "scope": {
    "agent_id": "agent_opaque",
    "project_id": "project_opaque",
    "corpus_id": "corpus_opaque",
    "surface_id": "reward_memory"
  },
  "application_receipt_id": "rma_<opaque>",
  "memory_ref_digests": ["sha256:<digest>"],
  "retrieval_snapshot_ref": "snapshot_opaque",
  "policy_snapshot_ref": "policy_opaque",
  "outcome_ref": "effect_opaque",
  "utility_label": "unknown",
  "attribution_level": "set",
  "evidence_basis": "evaluator_inference",
  "confidence": 0.42,
  "reason_codes": ["multiple_memories_not_disambiguated"],
  "evidence_refs": ["evidence_opaque"],
  "evaluator_ref": "evaluator_opaque",
  "evaluation_version": "evaluation_v0",
  "created_at": "2026-08-15T00:00:00Z",
  "grants_new_action_authority": false,
  "provider_write_performed": false,
  "external_writes_performed": false,
  "raw_content_captured": false
}
```

The contract MUST NOT include raw memory text, raw trajectories, provider
credentials, private paths, or unredacted transcripts. Exact provider
references may remain inside the owning process or private provider adapter;
public projections use opaque digests.

`observation_id` MUST be reproducible from the attribution subject and
evaluation version so retry is idempotent. A changed evaluator version or new
evidence creates a new observation rather than silently replacing history.

### 4.1 Attribution level

`attribution_level` is one of:

- `item`: evidence distinguishes one memory's contribution;
- `set`: the recalled or applied set shares an outcome, but individual credit
  is unresolved;
- `none`: the evaluator cannot establish even set-level relevance.

When several memories were applied, the default is `set`. The reducer MUST NOT
copy a set-level reward into per-item utility state.

### 4.2 Evidence basis

Evidence strength is typed rather than inferred from prose:

1. `owner_correction`: explicit scoped human feedback; may override earlier
   inferred utility but grants no broader execution authority;
2. `controlled_replay`: a bounded counterfactual or local rerollout from the
   same relevant state;
3. `deterministic_effect`: direct artifact, test, or effect evidence that
   distinguishes the memory contribution;
4. `evaluator_inference`: a model judgment over public-safe receipts and
   outcomes;
5. `insufficient`: lineage exists but attribution does not.

The three stronger bases require at least one opaque `evidence_ref` in the
proposal; `insufficient` may have no evidence reference. An unknown label does
not waive this provenance requirement.

Model inference alone is weak evidence. It may be retained for calibration and
review, but profiles MUST be able to prevent it from moving a strong rank
prior.

## 5. Evaluator role and authority

The optional evaluator reads only explicitly registered, public-safe or
owner-scoped projections:

- the recall and application receipt;
- the verified outcome or compact rubric;
- permitted artifact evidence;
- the utility history for the same scope;
- the evaluator and policy snapshot identifiers.

It emits an observation proposal. It MUST NOT:

- select, cancel, or promote a todo;
- turn uncertainty into a user gate;
- block the main work result or settlement;
- spend quota assigned to another lane;
- edit, delete, or publish memory;
- directly alter provider scores;
- combine user, project, or corpus scopes that were not explicitly registered;
- reinterpret recalled content as authority.

The evaluator may be shared operationally, but every observation and projection
remains scope-partitioned. This preserves the equal-peer rule in
[Peer Supervisor v0](../../reference/protocols/peer-supervisor-v0.md).

## 6. Deterministic reducer

Utility-attribution Stage 2 now implements the reducer as the
`loopx.capabilities.reward_memory.utility_reducer` module and exposes it through
`loopx reward-memory utility-project`. The reducer accepts only schema-valid,
in-scope observations and emits a versioned, read-only
`memory_utility_projection_v0`. It maintains, per eligible memory or set:

- bounded utility estimate;
- positive, negative, neutral, and unknown support counts;
- evidence-strength distribution;
- uncertainty and last-observed time;
- last accepted observation id and reducer version;
- quarantine or review proposal state, never implicit deletion.

The v0 update is intentionally bounded and evidence-tiered rather than a claim
of a learned value function:

- `owner_correction` > `controlled_replay` > `deterministic_effect` >
  `evaluator_inference` > `insufficient`;
- the highest evidence tier governs the effective direction; an `unknown` at
  that tier blocks weaker directional evidence, while otherwise only the
  strongest directional tier contributes the effective label and utility
  estimate; weaker observations remain support and history;
- same-tier directional disagreement becomes `unknown` with a review proposal;
- conflicting deliveries are excluded from subject state and carry a
  rejection-level `quarantine_proposed` proposal with no mutation authority;
- `item`, `set`, and `none` subjects are separate, and set-level credit is
  never copied into item subjects.

The implementation satisfies these invariants:

- an observation cannot move utility outside configured bounds;
- weak repeated inference cannot overwhelm a stronger correction or replay;
- `unknown` improves lineage coverage without changing utility direction;
- historical evidence remains append-only; any future time-decay policy must
  be introduced as a separately versioned reducer contract;
- scope mismatch fails closed for the observation while the main lane remains
  fail-open;
- replaying the same observation is a no-op;
- a reducer version change is explicit and reproducible.

The projection includes bounded accepted/duplicate/conflicting/rejected
counters, label and evidence-tier support counters, latest accepted observation
identity and time, bounded public-safe history, and review proposals. Exact
semantic retries under one `observation_id` are a no-op; retry-only
`created_at` differences do not create another support record. The duplicate
counter counts only additional deliveries with the same semantic fingerprint;
conflicting deliveries are counted separately and the rejected counter counts
all deliveries for a conflicting identity. A different payload under the same
identity is a conflicting delivery and is excluded from the effective state.
Scope, retrieval snapshot, policy snapshot, malformed observation, and
reducer-identity mismatches return a fail-closed rejected packet with no
subjects. The packet carries no provider effect, ranking hook, authority,
lifecycle transition, raw content, credential, transcript, or local path.

If utility later participates in retrieval ranking, semantic relevance remains
the anchor. One admissible shape is:

```text
rank_score = semantic_score * bounded_utility_modifier
```

The modifier has configured lower and upper bounds and cannot rescue an
unrelated candidate that failed the semantic candidate stage. Freshness,
lifecycle, permission, and authority remain separate filters.

## 7. Scheduling and cost

Attribution runs after an eligible outcome is settled, not inside the critical
path. Profiles may sample, batch, or cadence evaluations. Evaluation has its
own bounded quota and dedupe key.

The scheduler MUST NOT create recursive supervisor work on every heartbeat.
An unavailable evaluator records no utility update; it does not change the
already produced work result. High-cost controlled replay is reserved for
ambiguous, high-impact cases selected by an explicit policy.

## 8. OpenViking integration analysis

OpenViking is a good source of memory identity and lineage, but its current
interfaces should not be mistaken for a causal utility service.

At OpenViking main commit
[`eeff5a4`](https://github.com/volcengine/OpenViking/commit/eeff5a497360aa4481cf32e18a0d9376f4412f4c):

- search/context results expose URI, category, retrieval score, detail level,
  origin, and response digest;
- [Agent Evolution](https://github.com/volcengine/OpenViking/blob/eeff5a497360aa4481cf32e18a0d9376f4412f4c/docs/en/api/19-agent-evolution.md)
  lists trajectories associated with an Experience and aggregates terminal
  outcome tags;
- [experience lineage](https://github.com/volcengine/OpenViking/blob/eeff5a497360aa4481cf32e18a0d9376f4412f4c/openviking/session/memory/experience_lineage.py)
  detects completed explicit read tool parts; automatic context injection may
  therefore need LoopX's own recall receipt;
- the training domain exposes `Rollout`, `RubricEvaluation`, trajectory outcome,
  and `policy_snapshot_id`;
- [hotness](https://github.com/volcengine/OpenViking/blob/eeff5a497360aa4481cf32e18a0d9376f4412f4c/openviking/retrieve/memory_lifecycle.py)
  is derived from access count and recency, and can be blended into thinking-mode
  retrieval; it is not outcome utility;
- there is no first-class external utility or Q-value update API.

OpenViking's trajectory outcome is useful evidence about the set of consumed
Experiences. It does not prove the marginal contribution of each Experience,
because all consumed items can inherit the same final outcome tag.

### 8.1 Ownership split

| Surface | LoopX | OpenViking | Evaluator provider |
| --- | --- | --- | --- |
| Goal, quota, authority | owns | does not infer | does not change |
| Recall identity and content access | records opaque receipt | owns URI/search/read | observes permitted receipt |
| Exact application | owns receipt | may supply read lineage | does not invent |
| Verified work outcome | owns reference | may supply trajectory outcome | interprets permitted evidence |
| Utility ledger and reducer | owns | no v0 write | proposes observation only |
| Memory content/lifecycle | requests through existing effects | owns provider operation | no direct write |
| Optional rank-prior effect | authorizes and receipts | future adapter contract | no direct write |

### 8.2 Smallest OpenViking slice

The first integration is read-only:

1. retain the OpenViking result digest and opaque URI digests in the LoopX
   recall receipt;
2. bind the exact LoopX application receipt to a later verified outcome;
3. optionally query Agent Evolution as secondary lineage and outcome evidence;
4. preserve `policy_snapshot_id` or an equivalent retrieval snapshot when
   available so later evaluation uses the state visible during execution;
5. store utility in the LoopX sidecar projection.

Do not write utility into memory prose, `active_count`, or hotness. Provider
writeback can be proposed later only when OpenViking exposes a clear external
rank-prior or utility contract with effect and readback receipts. Updating the
Experience Policy Set through training is a separate, owner-reviewed content
change, not a ranking-weight shortcut.

## 9. Smallest useful implementation slice

Stage 0 is this RFC and tracking issue only.

Stage 1 repairs the semantic seam without changing retrieval:

- define and validate `memory_utility_observation_v0`;
- bind existing Stage 3 application receipts to verified outcome refs;
- stop treating `applied` as sufficient utility evidence in Stage 5;
- emit `unknown` when attribution evidence is insufficient;
- keep existing main-lane behavior unchanged.

Stage 2 adds an idempotent reducer and read-only utility projection. It does not
change provider ranking.

Stage 3 adds OpenViking readback and, only if a provider contract exists, a
bounded rank-prior effect plus readback receipt.

Stage 4 runs a bounded pilot with held-out or counterfactual evaluation before
any default-on ranking influence.

## 10. Validation criteria

Focused fixtures must prove:

- a memory can be `applied` and `harmful` when artifact evidence contradicts it;
- `applied + success` remains `unknown` without attribution evidence;
- an ambiguous multi-memory trajectory stays set-level;
- a user correction supersedes weaker inference in the projection without
  deleting history;
- stale policy/retrieval snapshots and scope mismatches reject the observation;
- duplicate delivery is idempotent;
- evaluator timeout, malformed output, or absence leaves the main output and
  settlement unchanged;
- semantic candidate filtering cannot be bypassed by utility;
- public packets contain no raw memory, transcript, local path, or exact private
  provider reference;
- negative utility proposes attenuation or review, not deletion;
- provider writeback requires an explicit effect and readback receipt.

Before ranking influence is enabled, evaluation should compare at least:

- semantic retrieval alone;
- semantic retrieval plus access hotness;
- semantic retrieval plus bounded utility;
- the same conditions with evaluator inference removed.

The evaluation must report quality, task cost, false attenuation, scope leaks,
and evaluator disagreement. A higher correlation with terminal success alone
is not sufficient evidence of causal utility.

## 11. Non-goals

- an omniscient global leader agent;
- a new execution or approval authority;
- cross-user, cross-project, or cross-corpus utility transfer by default;
- automatic memory rewriting, deletion, or publication;
- using access frequency as a utility proxy;
- reviewing every trajectory on every heartbeat;
- claiming online reinforcement learning before controlled qualification;
- replacing OpenViking's retrieval or memory lifecycle ownership.

## 12. Alternatives considered

### Map `applied` directly to positive utility

Rejected. It rewards self-report and cannot detect confidently applied harmful
memory.

### Give one LLM supervisor direct score-update access

Rejected. It mixes judgment with mutation, is difficult to replay, and creates
an authority escalation path.

### Reuse OpenViking hotness as utility

Rejected. Hotness measures access and recency. A frequently recalled bad memory
could become hotter.

### Assign the final trajectory reward to every recalled memory

Rejected. This produces noisy credit when several memories or policy states are
involved.

### Rewrite or delete memory after a negative judgment

Rejected. Utility weighting and owner-authorized memory lifecycle are separate
effects with different evidence and rollback needs.

### Run counterfactual replay for every outcome

Rejected for v0. It is expensive and can itself drift from the original policy
state. It remains a stronger evidence tier for selected high-impact cases.

## 13. Research basis

- [OpenViking Experience Policy Set training RFC](https://github.com/volcengine/OpenViking/discussions/2533)
  provides rollout, evaluation, gradient, optimizer, updater, and concurrent
  policy-update seams.
- [OpenViking on-policy and memory-versioning RFC](https://github.com/volcengine/OpenViking/discussions/2277)
  motivates evaluating against the policy view used during execution.
- [MemRL](https://arxiv.org/abs/2601.03192) separates semantic candidate
  retrieval from bounded utility-guided selection and updates utility from
  environmental feedback.
- [Memory-R2](https://arxiv.org/abs/2605.21768) identifies unfair credit from
  trajectory-level rewards and studies local rerollouts from the same
  intermediate memory state.
- [Mem-π](https://arxiv.org/abs/2605.21463) separates task execution from a
  guidance model that can choose whether to provide memory guidance.

These works motivate the separation and validation plan. They do not establish
that the proposed LoopX contract is already qualified for default-on use.

## 14. Relationship to existing contracts

- [Reward Memory Architecture v0](../../../loopx/capabilities/reward_memory/README.md)
  remains the stable recall, application, and lifecycle owner. This RFC defines
  the post-outcome attribution seam; its Stage 1 observation contract and the
  Stage 2 read-only reducer are implemented, while later provider effects remain
  proposed.
- [Peer Supervisor v0](../../reference/protocols/peer-supervisor-v0.md) supplies
  the equal-peer, public-safe, proposal-only authority boundary.
- [Agent IM, LoopX, and OpenViking collaboration v0](agent-im-openviking-collaboration-v0.md)
  keeps LoopX responsible for goals, authority, and evidence while OpenViking
  owns context and recall.
- [Human Attention Wishlist v0](human-attention-wishlist-v0.md) remains a
  non-blocking request for optional human leverage. Utility uncertainty becomes
  a wish only when human input has incremental value; it is not automatically a
  gate.

The Stage 1 observation contract and Stage 2 reducer/projection are now backed by
stable reference contracts and focused validation. They remain read-only and do
not change default retrieval, ranking, provider state, authority, or the main
work lane. OpenViking writeback, ranking influence, and qualification remain
future Stage 3/4 work and require separate versioned contracts and review.
