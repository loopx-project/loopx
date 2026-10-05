# agent_scoped_evidence_ledger_v0

`agent_scoped_evidence_ledger_v0` defines a decision-oriented read model for
agents that need to replan, hand off, or explain progress without reading raw
rollout logs, private active state, or another agent's detailed working trail.

The contract is a read model. It does not replace `ACTIVE_GOAL_STATE.md`, todo
state, compact run history, status projection, review packets, quota routing, or
the append-only rollout event log.

## Current Sources

LoopX already has useful history and evidence surfaces, but they serve different
jobs:

| Surface | Current job | Gap for agent replan |
| --- | --- | --- |
| `rollout-event-log.jsonl` | Append-only structured events such as todo, quota, refresh, validation, and compact evidence events. | It is a low-level event source, not an agent-facing filtered chronology. |
| `loopx status` | Projects current state, todo index, attention queues, agent lanes, run history, and event summaries. | It answers "what is true now", not "what sequence should this agent review before replanning". |
| `loopx review-packet` | Packages status and attention items for review or handoff. | It is packet-shaped, not a general scoped event ledger. |
| `loopx history` | Reads compact run history and run indexes. | It is run-centric and not equivalent to rollout events. |
| `loopx quota should-run --agent-id ...` | Decides whether a specific agent lane should act and projects a compact coverage ledger plus uncovered frontier from the evidence source. | It does not ask the model to reconstruct history or treat a read receipt as progress. |

The resulting surface is one public-safe, bounded replan context. Operators
retain the existing history view for compact run diagnostics.

## Ownership Boundary

| Layer | Owns | Must Not Own |
| --- | --- | --- |
| Event sources | Durable append-only events, compact run records, ids, timestamps, and public-safe refs. | Prompt-ready planning summaries or cross-agent privacy policy. |
| Status and review packets | Current projections, attention queues, frontier summaries, and operator packets. | Raw chronological replay or write authority. |
| Quota | Lane routing, spend policy, scheduler hints, host context delivery, and the minimal replan action packet. | Storing replan rationale or accepting writeback. |
| Internal event/history adapter | Public-safe historical rows for supervisor and recovery consumers. | Agent-facing read rituals, semantic-delta validation or canonical writes. |
| Replan context policy | Selects dense scoped evidence and builds core Goal, coverage ledger, delivery receipt, and uncovered frontier. | Reimplementing typed progress comparison, terminal-closure truth or exposing full other-agent traces. |
| Semantic write gate | Validates typed progress, state-grounded successors, fresh vision outcomes, blockers, and coverage-backed terminal results against the current obligation. | Reconstructing the evidence ledger or interpreting classification prose. |
| Acting agent | Selects an uncovered direction from delivered context and submits a typed observation or vision outcome. | Treating context delivery, a manual read, or a legacy ACK alone as progress. |

## One Replan Entry

Routine replan consumes the host-projected `replan_context_v0` on the current
obligation returned by `loopx quota should-run --goal-id <goal-id> --agent-id
<agent-id>`. Full review/handoff packets embed the same read model; handoff-only forwarding
keeps its readable summaries and the normal quota guard without duplicating the
structured evidence packet. There is no
standalone `evidence-log` command or compatibility alias.

The TypeScript work-item owner scopes compact run records, sorts them by
normalized timestamp, removes exact replays and collapses repeated observations
while retaining their count and first/latest timestamps. It selects up to 24
distinct observations: preserve observed result classes, then distinct typed
surface/hypothesis/probe routes, then recent observations. This is a bounded
selection rule, not a relevance or best-score oracle. An unchanged score is
never interpreted as a disproved hypothesis from prose alone.

Actual quota and handoff routes read the complete decoded compact index before
selection; the ordinary status display limit cannot hide earlier evidence.
Snapshot-only callers remain limited to their supplied source and are marked by
`from_full_index=false`. Large inputs reuse the private, digest-checked replan
snapshot transport rather than truncating at the RPC size limit.

`core_goal` precedes the evidence and carries the current active-state Objective
(or the registered objective when absent), with explicit source and missing
state. The existing acceptance owner's scoped objective, criteria, non-goals,
revision and status remain a separate `acceptance_contract`: a selected-work
contract and an Agent's current task do not redefine the whole Goal. This read
model creates no new Goal or acceptance authority.

Evidence retains public-safe validation/action summaries linked to typed
`coverage_ledger` observations by fingerprint. Other Agents and unattributed
records cannot supply proof for a scoped Agent. Counts describe the available
source, not complete Goal acceptance. Omitted observations expose counts, time
bounds and a concrete scoped history read. Missing required fields, unsupported
typed versions and malformed evidence fail explicitly; valid empty sources
produce empty arrays.

Readable evidence has a display budget; writeback validation uses all supplied
scoped history observations, including omitted blockers and prior claims. A
truncated coverage projection alone cannot establish novelty. Reading context
or changing an evidence label never creates permission or semantic progress.

Each evidence row includes a content-bound `evidence_ref` and an exact
`read_action` using the existing history entry:

```bash
loopx --format json history --goal-id <goal-id> --agent-id <agent-id> --evidence-ref <projected-ref>
```

Execute the supplied action only when more detail is needed. It resolves one
public-safe compact record in the same Goal and Agent; a changed or unavailable reference fails explicitly and asks the caller to refresh context.
It never falls back to another Agent or the latest unrelated record. It reads no
raw transcript and creates no read receipt. Ordinary `history` remains the
operator's compact run-history view; `--agent-id` scopes that view as well.

The internal rollout/history adapter remains available to supervisor and native
recovery code. Its legacy receipt decoder preserves historical observability,
but no product path generates a new `evidence_log_read` event or read ritual.
Removing the CLI neither deletes event/history files nor changes settlement.

## Replan Integration

When quota or status projects a replan obligation for an agent, the host folds
agent-scoped compact history into the decision evidence and coverage ledger and delivers
it with the current obligation:

```json
{
  "replan_action_packet": {
    "decision": "replan_required",
    "obligation_id": "replan-opaque-id",
    "uncovered_frontier": {
      "baseline": {"surface_id": "surface-auth", "result_class": "unchanged"},
      "required_any_of": ["new_surface", "new_hypothesis", "new_probe_family"]
    },
    "required_outcome": "semantic_delta",
    "writeback_contract": {
      "schema_version": "typed_progress_observation_v0",
      "transport": "loopx_refresh_state",
      "command_template": "loopx ... refresh-state ... --progress-result-class <typed-class> --progress-evidence-id <evidence-id> <typed-dimension-options>"
    },
    "allowed_terminal": ["exploration_exhausted", "blocked", "no_followup"]
  }
}
```

The full obligation also carries `replan_context_v0`: a bounded
`coverage_ledger`, the same uncovered frontier, and a
`replan_context_delivery_receipt_v0`. The control-plane responsibilities are
deliberately split and causally bound:

- compact run history and append-only rollout events remain the durable sources;
- quota owns context delivery and does not require a weak protocol-following
  model to discover or execute a read ritual;
- `typed_progress_observation_v0` owns work-slice identity and result semantics;
- quota and `refresh-state` use the same goal-frontier reducer, while the write
  gate closes only the current obligation with an accepted semantic delta.

When a turn identity makes the settlement chain executable,
`interaction_contract.cli_channel.replan_settlement_contract` names its one
causal binding. Its `semantic_obligation.settlement_bound` field is `false`
when a selected Todo owns the receipt: in that case the typed replan delta is
written and spent with `--todo-id` only, while the obligation id stays
available for semantic validation. Combining `--todo-id` and
`--replan-obligation-id` is never a valid settlement identity. Without a
selected Todo, the same contract marks the replan obligation as directly bound
and projects `--replan-obligation-id`. An unscoped diagnostic read keeps only
compact replan guidance; it does not advertise an executable settlement
contract or quota spend without the missing turn identity.

The agent should then write back one of:

- an `advanced` observation with a new surface, hypothesis, or probe family;
- an `advanced` observation naming a successor Todo that is actually runnable
  in current state;
- a new concrete blocker with evidence;
- coverage-backed `exploration_exhausted` or `no_followup`; or
- for a vision-derived duty, a fresh evidence-linked vision path outcome.

An accepted typed semantic ACK settles the corresponding projected obligation
even when the source acceptance gap remains visible. Terminal coverage inputs
fail at the CLI boundary when their required coverage scope is missing;
`exploration_exhausted` additionally requires explicit coverage completion.

Historical `evidence_log_read_receipt_v0` records remain readable. They are
observability facts only: a read, a failed read, a prose ACK, or a historical
repair-delta ACK cannot close the current obligation.

### Effect-program boundary

This flow uses the effect-program separation without adding a second settlement
executor. Host context projection is a repeatable read effect; the typed
progress writeback is a separately validated state transition. The delivery
receipt records context delivery, while an accepted semantic delta establishes
an eligible change against the obligation and its evidence. Neither proves
that the model read or understood every delivered observation, and neither
receipt is allowed to impersonate the other.

The live behavior qualification tests that causal handoff through an actual
function-tool conversation rather than a testing-only output field. A Doubao
actor receives the shipped Codex App heartbeat body and chooses the quota
command against a hermetic public-safe Goal. The harness runs that command
through the real LoopX CLI, returns its actual context/action packet, and asks
the actor to choose the next real tool action. The actor independently qualifies
the selected typed observation, then executes the real `refresh-state` command.
History-read-only, prose-only, pre-quota, equivalent-fingerprint, and ungrounded
successor actions do not pass. Only temporary fixture state may change, and the
receipt stores bounded command digests and typed outcomes rather than prompts,
packets, or output.

## Privacy Boundary

The ledger must preserve the rollout event boundary:

- no raw task text;
- no raw logs, stdout, stderr, trajectories, or verifier tails;
- no credentials, tokens, headers, or secrets;
- no absolute local paths;
- no private document body or chat transcript;
- no private source payload copied into public-safe rows.

Rows may contain compact ids, relative public artifact refs, redacted summaries,
omission notes, and private source counts. If a source is private, the row should
say that only a compact pointer or count was recorded.

## Current Implementation Status

The standalone evidence command and its generated read obligations are retired.
The existing replan context owns the bounded model view; the TypeScript owner
selects evidence and coverage while Python adapts historical codecs and public
safety. Handoff embeds that view, and history resolves exact references.
Supervisor/native recovery retain their internal event source. None of these
read paths grants execution, writeback, quota or Goal-completion authority.

This closes the duplicate evidence-entry gap in roadmap S3/S6. It does not
qualify long-horizon score improvement, full-history completeness, or research
observation settlement; those retain their existing S11 acceptance.

## Acceptance

- Real quota and handoff paths deliver readable scoped evidence, typed coverage
  and the current uncovered frontier without a mandatory read roundtrip.
- Sorting, replay deduplication and truncation are deterministic and bounded.
- A projected reference resolves through the real history CLI; stale, changed,
  wrong-Goal and wrong-Agent references fail explicitly.
- Contract errors remain distinct from valid empty evidence.
- Historical receipts do not grant semantic progress; equivalent observations,
  read-only actions and ungrounded successors still cannot close replan.
- Public tests use synthetic fixtures and retain the public/private boundary.
- Long-horizon model utility remains an evaluation result, not a claim inferred
  from passing deterministic tests.
