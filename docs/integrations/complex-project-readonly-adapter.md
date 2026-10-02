# Complex Project Read-Only Adapter

Some projects are too large for a single goal tick to understand safely. They
may have many docs, TODO systems, reports, tests, external sync surfaces, and
active branches. For these projects, the first LoopX adapter should not
edit files. It should build a read-only map.

The adapter's job is to answer:

- What is the current goal?
- Which files or systems are authoritative?
- Which work clusters are active?
- Which validation surfaces prove progress?
- Which peer task scopes are safe to run in parallel?
- Which open peer should claim or coordinate the next task bundle?

## Read-Only Map Shape

A read-only adapter map should contain:

```json
{
  "goal_id": "complex-project-main-control",
  "classification": "read_only_map_ready",
  "recommended_action": "ask the operator to opt in before mutations",
  "authority_sources": [],
  "work_clusters": [],
  "validation_surfaces": [],
  "peer_task_scopes": [],
  "boundary_findings": [],
  "handoff_packet": {}
}
```

The map is evidence, not a command. Claims, gates, and operator decisions still
decide what happens next.

## Authority Sources

List the files or systems that define project truth. Examples:

- project TODO or issue board,
- document registry,
- design docs,
- current git status,
- recent run reports,
- test or CI entrypoints,
- managed external-doc manifest.

For documentation-heavy projects, prefer an explicit authority registry over a
flat list of files. The registry should name default entry docs, topic
authority, document status, conflict rules, and update rules. A read-only map
should report authority coverage before proposing sub-agent work:

```json
{
  "authority_registry": {
    "path": "docs/meta/DOC_REGISTRY.yaml",
    "read_status": "read",
    "default_entry_count": 3,
    "default_entries_checked": 3,
    "default_entries_present": 3,
    "topic_authority_count": 24,
    "project_material_count": 6,
    "project_material_repository_count": 2,
    "project_material_owner_review_required_count": 1,
    "project_material_stale_count": 1,
    "project_material_current_authority_count": 1,
    "deprecated_source_count": 2,
    "conflict_risk": "low"
  }
}
```

This prevents a complex project from being driven by whichever old design doc
or diagnostic report the agent happened to read first.
For multi-material migration work, the public compact map should expose counts
for material roles, repository links, owner-review gaps, stale sources, and
current authorities. Keep exact URLs, repository roots, product configs, and raw
review text in the project-local registry or adapter payload.

Each source should include:

- `path` or stable identifier,
- `source_type`,
- `read_status`,
- `why_it_matters`,
- `privacy_level`: `public`, `project-local`, or `private`.

## Work Clusters

Group active work by the type of evidence needed to finish it:

- docs or design cleanup,
- benchmark or eval work,
- runtime or adapter work,
- external-doc sync,
- PR or CI work,
- governance or public/private boundary work.

The current-priority source should be written as current belief, not only as a
chronological task dump. The most useful shape is: what we believe now, why,
what changes the decision, and the next bounded action.

Each cluster should include:

- current status,
- likely owner,
- blocking condition,
- safe next probe,
- whether sub-agents can inspect it independently.

## Validation Surfaces

Complex projects rarely have one pass/fail metric. A useful adapter names the
surfaces explicitly:

| Work type | Validation surface |
| --- | --- |
| Docs | markdown structure, links, registry entry, review note |
| External sync | manifest, remote fetch, comment/highlight preservation |
| Benchmark/eval | run artifact, metric file, trace, hidden/eval split |
| Code | unit tests, type checks, integration smoke test |
| PR/CI | branch status, CI checks, review comments |
| Public release | sensitive scan, README quickstart, examples |

## Sub-Agent Scopes

The read-only adapter should propose child scopes, not launch them by itself:

```json
[
  {
    "id": "docs-map",
    "role": "explorer",
    "work_scope": ["docs/**", "README.md"],
    "write_allowed": false,
    "expected_output": "task clusters and authoritative docs"
  },
  {
    "id": "validation-map",
    "role": "validator",
    "work_scope": ["tests/**", "scripts/**", ".github/**"],
    "write_allowed": false,
    "expected_output": "available validation commands and coverage gaps"
  },
  {
    "id": "boundary-map",
    "role": "explorer",
    "work_scope": ["docs/**", "examples/**", "scripts/**"],
    "write_allowed": false,
    "expected_output": "public/private boundary risks"
  }
]
```

The controller may accept, edit, or reject these scopes before spawning agents.

## Handoff Packet

The final output to an eligible peer or operator should be short:

- current classification,
- one recommended action,
- active work clusters,
- proposed peer task scopes,
- validation surfaces,
- hard guards,
- files inspected,
- residual risk.

Do not include raw private evidence in a handoff packet intended for another
thread or public artifact.

## Upgrade Path

Use staged adapter status:

1. `planned`: goal exists in registry, no run yet.
2. `read-only-map-ready`: adapter can produce a current map.
3. `connected-read-only`: the operator has opted in to read-only runs.
4. `selective-assist`: controller may ask LoopX for bounded edits with
   explicit write scopes.

`loopx read-only-map --dry-run` is allowed at `planned` as a controller
opt-in preview. It reads only registry metadata, active-state sections, and the
bounded file inventory, returns `opt_in_required=true`, and appends no run.
Running without `--dry-run` still requires `read-only-map-ready`,
`connected-read-only`, or `connected`.

The preview also returns `residual_risks`, using stable labels such as
`planned_adapter_requires_controller_opt_in` and
`project_local_goal_state_not_detected`, so the target controller can review
one shared risk vocabulary.
For repositories with more than one goal, the preview checks the selected
goal's own `.loopx/goals/<goal-id>/` directory. If a side bypass has not yet
been connected locally, the risk list includes
`project_goal_state_dir_not_detected:<goal-id>` even if the main control goal in
the same repository is already healthy.

Skipping directly to editing creates avoidable coordination risk.
