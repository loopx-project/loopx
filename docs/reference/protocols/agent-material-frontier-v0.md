# agent_material_frontier_v0

`agent_material_frontier_v0` is a read-only, agent-scoped view over
goal-owned material authority. It answers which registered material refs an
agent needs, which revision it observed, and whether each ref is current,
stale, missing, unread, or inaccessible.

The frontier is not a second material registry. Material ids, revisions,
topics, freshness, boundaries, and gates remain owned by the goal's canonical
`authority_registry.project_materials` and `topic_authority` maps.

## Inputs

The pure builder accepts six bounded input groups:

- canonical goal authority: material metadata and topic-to-material mappings;
- agent profile requirements: explicit material refs or default material
  topics;
- todo requirements: explicit material refs for current work;
- an agent vision requirement set;
- handoff material refs inherited by a successor;
- `material_usage_receipt_v0` rows for the selected agent and todo.

Requirement precedence is:

```text
explicit todo or handoff > vision > profile topic default
```

Multiple bindings for the same material are preserved in `bound_by`. The
highest-precedence binding selects the relation and purpose, while the current
revision and boundary are always re-read from goal authority.

## Shape

```json
{
  "schema_version": "agent_material_frontier_v0",
  "goal_id": "example-goal",
  "agent_id": "agent-reviewer",
  "generated_at": "2026-07-19T00:00:00Z",
  "summary": {
    "required_count": 2,
    "current_count": 1,
    "stale_count": 0,
    "missing_count": 0,
    "inaccessible_count": 0,
    "required_unread_count": 1
  },
  "items": [],
  "required_reads": [],
  "truth_contract": {
    "authority_is_goal_owned": true,
    "projection_is_read_only": true,
    "introduces_task_runtime": false,
    "grants_cross_agent_authority": false,
    "evidence_log_implies_material_read": false,
    "raw_source_body_recorded": false
  }
}
```

Each item may contain:

- `material_id` and authority-derived `topics`;
- `relation`: `required`, `producer`, `reviewer`, `maintainer`, or `watcher`;
- `bound_by`: compact `profile`, `todo`, `vision`, or `handoff` refs;
- `purpose` and `todo_id`;
- `required_revision` and `observed_revision`;
- `state`;
- `boundary` and `gate_status`;
- a compact `receipt_ref` and `last_verified_at`.

The projection never copies source URLs, document bodies, comments,
credentials, local absolute paths, or another agent's expanded event stream.

## State Rules

State is derived in this order:

1. The material id is absent from canonical authority: `missing`.
2. Its boundary or gate is unavailable to the current execution environment:
   `inaccessible`.
3. No matching receipt exists for the current agent and todo:
   `required_unread`.
4. Authority freshness is stale or the observed revision differs from the
   authority revision: `stale`.
5. The observed revision equals the current authority revision: `current`.

A compact authority summary is not enough to derive the frontier. The builder
fails closed unless canonical `project_materials` are present, so a projected
count can never be mistaken for an empty authority registry.
An omitted boundary is also treated as inaccessible rather than implicitly
public.

Evidence and receipts have different semantics. A run-history or replan-evidence
row may prove that an agent changed or validated an artifact, but it does not
prove the agent consumed a material revision. Only a matching
`material_usage_receipt_v0` can move a material from unread or stale to
current. Receipt-like evidence without the receipt schema, agent, goal, todo,
stable id, outcome, and timestamp fails closed.

## Handoff Semantics

A successor may receive a bounded set of material refs through handoff, then
rebuild its own complete frontier from goal authority plus its profile, todo,
and vision requirements. The predecessor's receipt does not make the successor
current, and the handoff does not transfer source permissions or authority
ownership.

This supports durable handoff without introducing a dispatcher or an
agent-owned material cache.

The bounded handoff projection emits:

- `schema_version=agent_material_handoff_projection_v0`;
- the six frontier summary counts;
- `material_ref_count` and `material_refs_truncated`;
- at most four refs containing only `material_id`, `relation`, and optional
  public-safe `purpose`.

It deliberately omits observed and required revisions, receipt refs,
boundaries, gate status, authority metadata, and available permissions. The
bounded refs are context, not a complete material manifest. A successor uses
its own profile, todo, vision, and handoff requirements to rebuild the full
frontier against current goal authority.

When a typed `handoff_note_v0` and a full frontier are both present, the agent
read-model layer may emit `handoff_note_v1` by attaching
`material_frontier_summary` and the bounded refs. The legacy todo handoff
producer remains unchanged for rows without a frontier.

## Current Delivery Boundary

The implementation includes the pure frontier builder, a bounded material
handoff projector, and an optional agent-management cold-path consumer. The
consumer reads prebuilt packets from `status.agent_material_frontiers`; it does
not load canonical authority or material bodies by itself. The consumer emits
material fields only when its current execution envelope declares the
goal-scoped `material_lifecycle` capability. Missing capability silently keeps
the material frontier off the agent projection; it does not become a user
gate.

The implementation intentionally does not add:

- profile/todo material-requirement authoring commands;
- a material body cache;
- a receipt append command;
- automatic authorization or gate creation;
- a cross-agent dispatcher;
- an MA- or runtime-specific field.

## Acceptance Checks

A durable fixture should prove:

- profile topics and explicit requirements merge deterministically;
- todo or handoff requirements override a profile watcher relation;
- unread, current, stale, missing, and inaccessible states are distinct;
- an authority revision change makes an older receipt stale;
- another agent's receipt never satisfies the current agent;
- a successor gets the same material refs without inherited authority;
- compact authority summaries fail closed;
- the emitted packet contains no raw source material or private paths.
