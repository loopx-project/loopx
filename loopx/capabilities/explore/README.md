# Explore Harness

Status: supported optional capability; default-off harness execution contract.

## At a Glance

Explore Harness is a supported, default-off optional capability for
long-running exploration goals (software research, security attack-surface
mapping, domain studies). It turns "go look around" into a bounded,
observable, gated process with three pillars:

1. **Evidence graph** (Explore Graph) - an append-only, public-safe evidence topology
   (nodes / edges / findings) plus bounded projections, Mermaid export, and
   canonical/executive presentation. It answers: what has been explored,
   where the loop is blocked and why, and what was found.
2. **Optional planning** - deny-by-default, read-only branch planners
   (`todo-branch-plan`, `worker-branch-plan`) that rank and bundle next
   steps (DSpark-style confidence/prefix/load, `adaptive-resilient` and
   `moe-router` profiles, resource-aware portfolio), without claiming,
   launching, or spending.
3. **Combined-surface research** - worker lanes explore multiple surfaces
   in parallel; episode groups share expensive setup across variants;
   replay/counterfactual/trace runtimes compare routes; typed
   `supports` / `refutes` / `leads_to` edges merge findings back into one
   evidence topology. Closed, evidence-backed surfaces with an explicit
   reason to be tested together become **composition gaps** that derive
   joint-experiment successor todos (see "Composition Frontier").

**When to use it:** exploration goals that outgrow a todo list - where
"what did we try, what worked, what is blocked" must be readable as a graph
and where next steps should be planned across several parallel directions.

**What it is not:** not a resident scheduler, not a worker launcher, not a
process engine. Everything below is analysis or evidence unless an operator
executes it through the normal LoopX lifecycle.

## Quick Start

Choose a mode, record evidence, project, and plan:

```bash
loopx configure-goal --goal-id <id> --explore-mode planning \
  --explore-harness-profile adaptive-resilient --execute

loopx explore node --goal-id <id> --title "Attack surface A" --status exploring
loopx explore edge --goal-id <id> --from A --to B --type leads_to
loopx explore finding --goal-id <id> --title "Key finding" --node A --status confirmed

loopx explore summary --goal-id <id>
loopx explore graph --goal-id <id> --graph-format mermaid --out explore.mmd
loopx explore worker-branch-plan --goal-id <id> --harness-profile adaptive-resilient --worker-width 3
```

Explore Harness is off by default (see "Explore Harness Modes"). When closed, evidence-backed surfaces have an explicit reason to be
tested together, the next `quota should-run` / turn packet projects a
composition gap and can derive a joint-experiment successor todo (see
"Composition Frontier"). The detailed contract follows.

Long-running exploration goals (for example a Codex loop studying an external
software domain through LoopX) produce results that operators want to read as
a topology, not as an agent action log: what has been explored, where the loop
is blocked and why, and what was found.

## Role Boundaries

In one breath:

- **Explore capability (this layer)** owns the structured exploration
  EVIDENCE: a compact, public-safe, append-only node/edge/finding/blocked-
  frontier log plus bounded read-model projections. This is research
  evidence, not a display artifact -- its downstream consumers are vision
  checkpoints, replanning, successor-todo generation, and user gates first,
  and presentation second. That is why the log lives under
  `loopx/capabilities/explore/`, not under `loopx/presentation/`.
- **Presentation** renders the public-safe explore projection into operator
  surfaces (Mermaid graph, Feishu/Lark Base rows, cards). The reusable
  display implementation lives in
  `loopx.extensions.lark.presentation.explore_results`; core retains no Lark
  capability facade. Compatibility CLI delegates require explicit extension
  activation before they invoke this provider-owned display behavior.
- **Value connectors** remain the boundary for external signal input,
  permissions, and source authority. The Lark explore sink is display only
  and must never be conflated with a connector.

## State Contract

- Reads: `goals/<goal-id>/explore-result-log.jsonl` under the LoopX runtime
  root (`loopx_explore_result_event_v0` events appended by `loopx explore
  node|edge|finding`). Presentation sinks may additionally read local display
  config such as `.loopx/lark-explore.json`.
- Writes: the explore result log (append-only), the local board config
  (`loopx_lark_explore_local_config_v0`, including the result-id to Lark
  record-id map) from the presentation sink, and, only with `--execute`, Lark
  Base rows through `lark-cli`.
- Write owner: the operator-triggered CLI. Agents append result events; only
  an explicit `--execute` run touches the shared Lark surface.
- Proof of transition: every sync payload lists the exact `lark-cli` commands
  it ran or would run, per-row record ids, and the refreshed record map that
  the next sync reuses.

## Result Event Model

One JSONL event per line, `loopx_explore_result_event_v0`, three kinds:

| Kind | Identity | Purpose |
| --- | --- | --- |
| `node` | `--node-id` (or derived from title) | An explored question, area, hypothesis, experiment, or artifact. Status: `open`, `exploring`, `blocked` (requires `--blocked-reason`), `resolved`, `dead_end`. Re-record the same id to update it. |
| `edge` | derived from `from/type/to` | Typed relation: `subtopic_of`, `depends_on`, `answers`, `supports`, `refutes`, `leads_to`. |
| `finding` | `--finding-id` (or derived from title) | A discovery, optionally attached to a node. Status: `tentative`, `confirmed`, `refuted`. |

Events are sanitized at record time: compact text limits, credential-like
markers rejected, and evidence refs must be public relative refs or opaque ids
(for example `ov:doc:lustre-survey`), never local absolute paths.

The writer compacts summaries and blocked reasons at 2000 characters. The
strict v0 reader accepts canonical persisted text up to 2002 characters without
recompacting or rewriting it, including the historical compactor's two extra
ellipsis characters. Reader compatibility remains independent of the writer
budget. Unknown fields, unsafe text, stale event hashes and text beyond that
reader bound still fail closed. Display and page limits remain
separate from the stored evidence.

For a shared log, deploy compatible readers to every CLI, service and worker
that reads it before deploying an expanded writer. Older 1200-character
readers may reject a longer event and block the whole read. The source writer
has expanded to 2000 characters; this does not upgrade installed runtimes,
migrate existing events or grant write authority. Rolling back to an older
reader requires first retaining the longer log with a compatible reader; do not
silently clip its counterevidence.

## Results From Ordinary Work Writeback

An enabled evidence or planning mode accepts an explicit
`explore_result_attachment_v0` on a Todo/Turn-bound `refresh-state`:

```json
{
  "schema_version": "explore_result_attachment_v0",
  "node_id": "bounded-prefix-question",
  "question": "Does the tested prefix establish the tail bound?",
  "applicability": "Finite prefix only; no uniform tail estimate.",
  "input_revision": "fixture-v1",
  "observation": "A divergent tail shares the tested prefix.",
  "interpretation": "Require a uniform bound before transferring the result.",
  "status": "refuted",
  "evidence_refs": ["validation:counterexample-1"]
}
```

In evidence or planning mode, the Todo-bound settlement plan exposes this
optional attachment next to its agent-owned CLI `durable_writeback` step.
Quota/heartbeat, compact envelopes and Todo terminal-recovery plans share the
same capability-owned guidance. Driver-owned callback plans do not advertise
an attachment command they cannot consume. The ordinary command stays executable without
an attachment; off mode adds no attachment metadata or evidence requirement.
This point-of-use hint complements the turn-start read hook; it does not inspect
local logs, generate findings, or require a no-evidence acknowledgement.

The hint includes an `attachment_template`, also returned as
`graph.result_attachment_template` by `turn-context`. Fill its blank fields
after validating a reusable constraint, counterexample, or a result that changes
or justifies the next route. Local notes and validation files are not ingested
automatically. The incomplete template is rejected; it is not a finding.
Goal, Agent, Todo and Turn already come from the ordinary writeback. Supply the
actual tested input revision yourself, since a code revision alone need not
identify the tested data. Keep raw logs local and reference bounded evidence.
These prompts guide optional capture, not a finding-per-turn requirement;
successful ingestion and later use must be measured separately.

Place the filled template in the top-level `explore_result` field of the vision
JSON already passed to `--agent-vision-json`. This explicit field saves a separate
result file; ordinary vision or `path_delta` text is never interpreted as a finding.
The CLI separates the attachment from the generic vision packet and sends it
through the same Explore validator and post-writeback hook.

When this same vision packet already contains a validated, evidence-linked
top-level `path_delta`, use `graph.path_delta_attachment_template` instead:
`schema_version=explore_result_from_path_delta_v0`, stable `node_id`, `question`,
`applicability`, tested `input_revision` and explicit `status`. The capability
reuses the complete observation and all retained, changed and stopped route
items. It does not infer a finding or its status from ordinary vision text.
If the delta includes local evidence-file pointers, optionally supply
`evidence_refs` selecting one or more opaque identifiers already present in
that same delta. Foreign identifiers, empty selections and local file pointers
are rejected; the hook never invents identifiers or silently drops refs.
Omitting the selection reuses all refs and requires all of them to be opaque.

Observation text allows 320 characters and interpretation text 1200; the complete
finding, including revision and applicability, fits the 2000-character stored
summary limit. This raises the previous 300/300 attachment and 1200 summary
limits to preserve legal Goal route decisions and their last negative condition.
The next-turn view preserves that entire summary, with the same three-result
default and existing paginated detail reads. Short findings and off mode retain
their behavior. This is a transport limit change, not evidence of model adoption.

Alternatively, add `--explore-result-json result.json` to the ordinary admitted work
writeback, retaining its Goal, Agent, Todo, Turn and delivery fields. When both
sources are supplied, their normalized contents must agree; equivalent sources
produce one result and conflicting or malformed sources fail before primary commit.
Omit both sources for ordinary work without new evidence. Off mode rejects an
explicit attachment before committing, while attachment-free work is unchanged.
Removing the field or file option disables capture for that writeback; it does
not erase existing evidence or change the Goal's Explore configuration. The
attachment is validated before primary commit and stored with that writeback.
The effect-free capability hook emits an ingestion intent; the consumer creates
the question only if absent, appends a finding, and adds the reference through
the existing Todo owner. This opt-in append may attach evidence to a completed
Todo while retaining its terminal status and completion metadata; claim and
lease checks still apply. A completed owner may use the retained released lease
key and current version solely for this additive evidence association; it grants
no renewed execution or other edit authority. Other completed-Todo edits remain rejected. It never acquires a claim, renews a lease, changes a
question's existing status, or launches another worker. An existing question id
must keep the same question and applicability; use a distinct id for a changed
scope. `input_revision` is the caller's recorded revision, not a claim that the
runtime independently verified the underlying artifact.

For an **open Todo under `hard_lease`**, keep its active task lease through
`refresh-state` and the graph/Todo-link delivery. Release it only after
`explore_result_delivery.ok=true`. Releasing early causes attachment preflight
to reject before primary commit; re-enter the normal guard/claim/lease path
before retrying. The completed-Todo exception above remains limited to additive
evidence association. An exact replay of already-successful delivery is
readback-only and needs no new lease; unfinished delivery still requires the
applicable claim/lease proof. None of these rules make capture mandatory.

Read back with `loopx explore turn-context --goal-id <id> --agent-id <agent>`
and `loopx explore summary --goal-id <id>`. The bounded next-turn view retains
up to three attached result summaries, including their applicability and input
revision. In planning mode, results linked to the selected work take precedence
over unrelated recent results within that same detail budget. One slot retains
the latest linked refutation; remaining slots retain recent linked results, so
new positive observations do not silently erase a counterexample’s scope. An
explicit revision of the same finding replaces its earlier status. This does not
change candidate scores or eligibility; read the full summary for additional
evidence beyond the budget. Evidence-only mode retains recency order. Relevant
Todo links continue through the existing branch planner.
Reading evidence does not prove adoption: the next work decision should explain
which result supports a changed route, continued work, or a justified replication.

Decision reads (`turn-context`, summary, branch planners and graph export) validate
the complete evidence log. Malformed, foreign-Goal or invalid-state records return
an error instead of silently presenting partial evidence as an empty or clean
route. A missing log still means no recorded evidence; disabled turn hooks do not
read it. Repair the source log before retrying. A failed graph export leaves any
previous output file intact.

A graph/link delivery failure leaves the primary writeback committed and returns
`explore_result_delivery.retryable=true`. Replay the **same** refresh command to
complete delivery; conflicting attachment changes cannot rewrite the original
Turn. Graph events are idempotent and Todo references merge against the owner's
locked snapshot. Durable Todo links no longer have an eight-reference ceiling;
all valid distinct node IDs survive append and exact Todo readback. The compact
turn view shows at most eight requested/unknown reference IDs per branch, with
explicit omission counts. Its `plan_command` now calls `todo-branch-plan` to
expand the same Todo audit (instead of grouping worker lanes),
and revision-bound result pages retain access to all scoped findings. Display
budgets do not remove links, exclude negative evidence from hazard classification,
or block valid writeback. Successful replay needs no second graph event or Todo
mutation. This changes enabled evidence capture and explicit Todo-link edits;
ordinary work and feature-off hooks retain their existing behavior.

Use `tentative` for inconclusive observations or prerequisite failures. Neither
compilation failure nor a score alone supplies a scientific interpretation.
Routine work without reusable evidence needs no attachment. Omitting the option
preserves ordinary writeback behavior; disabled Explore rejects an attachment
before committing it. Disable further ingestion through the existing
`configure-goal --explore-mode off --execute`; existing evidence remains readable.
No attachment authorizes external publication or changes quota/settlement rules.

This is a worker writeback integration slice. CLI result ingestion and existing
graph projections are covered; a packaged frontend attachment authoring/retry
journey is not delivered by this slice. Existing capability configuration remains
the mode owner, and Lark publication remains separately authorized. Full research
adoption and score benefit require a subsequent matched worker experiment.

## Projection And Topology

`loopx explore summary` folds the log into
`loopx_explore_result_projection_v0`: latest state per node/edge/finding,
status counts, the blocked list with reasons, the exploring frontier, a
parent/`subtopic_of` topology tree, and Mermaid flowchart source.
`loopx explore graph --graph-format mermaid|json [--out <file>]` exports the
topology for a Feishu doc, whiteboard, or any Mermaid renderer.

Focused exports are bounded evidence views, not executive decision views by
default. They preserve machine-oriented node identity, edge semantics, and
ancestor context while reducing the amount of canonical topology rendered:

```bash
loopx explore graph \
  --goal-id <id> \
  --status exploring \
  --status blocked \
  --tag executive \
  --graph-format mermaid \
  --out explore-focused-evidence.mmd
```

Repeated statuses match any requested status, repeated tags match any exact
requested tag, and the status and tag groups are combined with AND. Matching
nodes keep their ancestors by default so the focused graph retains explanatory
context; pass `--no-include-ancestors` for a leaf-only view. Filtering changes
only the graph export. It does not mutate the full result projection or the
Lark node, edge, and finding tables.

An owner-facing executive graph is a separate display projection over that
canonical evidence, not a second evidence source. Do not sync a full or focused
canonical export directly into an executive whiteboard merely because it is
smaller. The projection should compress the evidence into the decision roles
the operator needs to see:

- decision contract and primary metric;
- baseline and current incumbent;
- decisive negative or retired evidence;
- active work or capacity slots;
- material risk and guardrails;
- terminal decision gate;
- next decision or evidence gap.

The default cardinality policy is graph growth. Preserve material decision and
evidence nodes and their relationships; semantic compression means tightening
labels, removing true duplication while retaining lineage, and organizing the
view into semantic sections or linked subgraphs. It does not mean dropping a
material node because the graph crossed a generic threshold such as 20 nodes.
Stable canonical ids must survive relayouts and movement between sections so
the owner can trace every displayed decision and evidence item back to source.

Hard `max_nodes` and `max_edges` limits are allowed only in an explicit opt-in
presentation policy. That policy must name its scope, rationale, overflow or
linked-subgraph behavior, and material-node preservation rule. Without such a
policy, treat both limits as unbounded; never infer a hard cap from renderer
convenience, an earlier graph size, or a generic executive-view convention.

Keep a fail-fast guard that rejects accidental identity with the canonical
export. Before syncing, render with the target renderer, run overlap and
text-overflow checks, and visually inspect the actual preview. Repair readability
through relayout, shorter labels, larger frames, or more semantic subgraphs
rather than deleting material evidence. After syncing, verify the remote source
or digest matches the validated projection. The canonical JSON and
Nodes/Edges/Findings tables remain complete and authoritative throughout this
presentation step.

`loopx explore presentation --goal-id <id>` builds a presentation bundle from
one canonical result projection. It always includes a complete `canonical`
view and a derived `executive` view with source-node lineage. Both views carry
the same timestamp-free `source_digest` and event-based `source_revision`.
The executive view selects active and decision-tagged nodes, representative
counterevidence neighborhoods, material one-hop relations, and ancestors; it
does not store facts independently.

The bundle recommends `presentation_mode=canonical_only|dual_view` from
multiple advisory signals rather than a single node-count cutoff. Current
reason codes are `low_decision_density`, `excessive_terminal_branches`,
`deep_decision_path`, and `readability_check_failed`. Static graph shape can
estimate readability risk, including excessively flat root topology; a caller
may also supply renderer observations for overlap, text overflow, or abnormal
canvas expansion. Both canonical and executive views use a top-to-bottom
evidence timeline: stable source order starts at the top, bounded epochs add
navigation, and later evidence extends the board downward instead of widening
the first rank. Every original canonical node and edge remains present. These
signals and layout choices only control presentation. They never authorize
canonical truncation.

## Optional Todo Branch Plan

`loopx explore todo-branch-plan` is a narrow opt-in harness for
exploration goals that need to try several plausible next todos at once. It
uses a CPU branch-prediction analogy plus a DSpark-inspired scheduler: rank
open agent todos, estimate branch confidence and expected evidence units,
choose a confidence-scheduled verification prefix, select one `primary` branch
plus safe `speculative` branches, and reject branches whose declared write
scopes overlap an already-selected branch.

Accuracy note on the DSpark citation (arXiv:2607.05147): real DSpark truncates
a semi-autoregressive draft block at the first per-step confidence below a
fixed threshold, and uses the cumulative product of per-step confidences only
as a calibration diagnostic. The prefix-survival theta model here (survival
product x throughput curve) is a loopx-specific extension for *serially
dependent* todo chains. It must not be used to size independent parallel
worker lanes -- that misuse capped an early calibration run's treatment arm
at 5 of 10 lanes; worker plans now use `schedule_independent_lanes` instead.

The command is read-only and sits behind the same per-goal opt-in gate as
`worker-branch-plan` (see "Per-Goal Opt-In Gate" below): without
`explore_harness.enabled=true` on the goal's orchestration boundary it returns
a disabled packet, and `--width` is capped by `max_children` in addition to
its own ceiling. It does not claim todos, acquire leases, launch agents,
spend quota, or change the active state. Instead it emits a prediction
packet with:

- selected branches, confidence, hazards, and reason codes;
- excluded `continuous_monitor` diagnostics, which remain visible but never
  enter the exploration scheduler or consume branch width;
- a dry-run A/B estimate comparing baseline serial execution with the
  DSpark-style selected prefix (`ab_result.estimated_speedup_vs_baseline`);
- suggested `loopx todo claim` and `loopx task-lease acquire` commands for a
  human operator or registered peer runner to execute explicitly;
- the safety boundary that keeps the packet advisory rather than an
  replacement for `quota should-run`.

An advancement todo may opt into typed result diagnostics by attaching one or
more explicit Explore node ids:

```bash
loopx todo add --goal-id <id> --role agent --text "Evaluate the rejected route" \
  --task-class advancement_task --explore-result-node-ref node_rejected_route
```

`todo-branch-plan` resolves only those explicit links. Its bounded
`typed_evidence_audit` reports linked node lifecycle, finding statuses,
relevant `supports`/`refutes` edges, unknown ids, and dead-end/refutation
hazards. The audit is diagnostic-only (`score_delta=0`) and cannot claim,
lease, launch, write state, or spend quota. Unlinked todos retain the prior
planner behavior. Repair a stale link by replacing it with another repeated
`--explore-result-node-ref`, or remove all links with
`loopx todo update ... --clear-explore-result-node-refs`.

Todos without declared write scopes are treated as speculative read or
coordination work by default, because many exploration tasks are read-only.
Use `--no-allow-unscoped-parallel` when the controller wants unknown scopes to
collapse back to single-branch execution.

Scope conflicts are based only on mutable `required_write_scopes`. Do not put a
shared base checkout or an already-built immutable input in that field merely
because multiple experiments read it. Represent reusable inputs with existing
public-safe capability labels such as `shared_implementation:<name>` or
`shared_artifact:<name>`, then give each experiment its own variant or launch
output scope. Those lanes may run in parallel. If the shared build itself is
still mutable, keep its path in `required_write_scopes`; the planner will
correctly serialize lanes that could write the same artifact.

## Optional Worker Branch Plan

`loopx explore worker-branch-plan` is the worker-lane version of the same
experiment. It does not treat a branch as one todo. A worker branch is a
predicted lane containing a small bundle of LoopX todos, an objective slice,
required capabilities, write scopes, dependency hints, expected evidence,
confidence, and suggested claim/lease commands.

Sharing `shared_implementation:*` or `shared_artifact:*` capabilities does not
make worker lanes mutually exclusive. This supports one shared implementation
or artifact-build stage followed by independent long/short-style experiment
lanes that write separate variant or launch directories. The shared inputs must
be immutable for that execution wave; an in-progress shared build remains a
write scope and therefore remains a real conflict.

`continuous_monitor` todos are observation/control-plane lanes, not exploration
work. The planner keeps them in `rejected_worker_branches` with
`selection_status=excluded_non_exploration_lane`, but never bundles them with
advancement todos or charges them against `worker_width`. A monitor transition
may create or unblock a successor advancement todo through the normal todo
lifecycle; that successor can participate in the next read-only planning call.

### Resource-Aware Portfolio Planning

Both branch planners can apply independent capacity ceilings to advancement
todos that declare one `resource_lane:<key>` capability. Capacities and current
occupancy are request inputs, not persisted control-plane state:

```bash
loopx explore worker-branch-plan --goal-id <id> --worker-width 5 \
  --resource-capacity long_pool=2 --resource-usage long_pool=1 \
  --resource-capacity short_pool=3 --resource-usage short_pool=1
```

The same repeatable flags work with `todo-branch-plan`; `--width` or
`--worker-width` remains the overall plan ceiling. In this example the packet
may assign one new `long_pool` slot and two new `short_pool` slots. Each selected
branch carries `resource_lane` plus a `resource_assignment`, and the top-level
`resource_portfolio` reports capacity, current usage, available, selected, and
remaining slots per lane.

Declaring resource capacities is an explicit portfolio-fill mode: the requested
overall width becomes the selection ceiling instead of the legacy confidence
prefix, while existing scores, hazards, and typed evidence remain unchanged.
An available slot therefore makes a ranked candidate eligible for the analysis
packet; it is not evidence that the candidate is valuable enough to execute.
The agent must still apply the goal's evidence, serving-cost, quota, claim, and
lease gates before launch.

When a higher-ranked candidate is rejected because its dependency is not in the
selected wave, its write scope conflicts with an already selected branch, or it
has another planner hazard, selection keeps scanning. A later safe candidate in
the same resource lane can backfill the released predicted slot in the same
call. `continuous_monitor` todos stay diagnostic-only and never consume a
resource slot, even if they carry a resource-lane capability.

Resource inputs are optional. With no `--resource-capacity`, unlaned and legacy
todos retain the existing width/scheduler behavior. In resource-aware mode,
untagged todos retain their previous unconstrained behavior, while a tagged lane
must have a matching declared capacity. Usage without a matching capacity fails
closed to catch misspelled lane keys.

This remains analysis-only evidence: `resource_portfolio.score_delta=0`, typed
evidence keeps `score_delta=0`, and the planner is read-only. Capacity and usage
do not claim todos, acquire leases, launch workers, write state, or grant quota
authority. They only constrain the predicted portfolio; execution still enters
the normal LoopX lifecycle described below.

This command is read-only and opt-in per goal. It is designed to sit on top of
the existing LoopX harness, not beside it and not instead of it:

1. LoopX supplies the harness inputs: quota/status context outside this command,
   the open agent todo projection, explore result projection, ownership,
   capabilities, and write-scope metadata.
2. The opt-in planner groups todos into worker-lane candidates and uses
   DSpark-style confidence/prefix/load scoring to pick a worker branch prefix.
3. Execution must return to the normal LoopX path: `quota should-run`,
   `todo claim`, `task-lease acquire`, worker execution, `explore node|edge|finding`,
   `refresh-state`, and `quota spend-slot`.

For goals that have opted in, the packet therefore contains
`harness_compatibility` and `boundary` fields: `replaces_loopx_runtime=false`,
`launches_workers=false`, and `claim_and_lease_are_suggested_only=true`; the
deny-by-default disabled packet carries the `boundary` block plus the opt-in
`required_contract` instead. The packet can be used by a controller or human
operator to decide which workers to start, but it cannot launch workers or
mutate the control plane on its own.

### Explore Harness Modes

Explore Harness is one optional capability with an evidence graph and optional
read-only planning. Goal settings offer one editor with three modes:

| Mode | Evidence graph | Branch planning |
| --- | --- | --- |
| `off` (default) | Inactive | Inactive |
| `evidence` | Active | Inactive |
| `planning` | Active | Active |

```bash
loopx configure-goal --goal-id <id> --explore-mode planning  # preview
loopx configure-goal --goal-id <id> --explore-mode planning --execute
loopx configure-goal --goal-id <id>                          # read back
loopx explore turn-context --goal-id <id> --agent-id <registered-agent>
loopx configure-goal --goal-id <id> --explore-mode evidence --execute
loopx configure-goal --goal-id <id> --explore-mode off --execute
```

Existing `explore_graph.enabled` and `spawn_policy.explore_harness` storage,
record ids and CLI aliases remain supported. A legacy Graph-only Goal maps to
`evidence`; a legacy Harness-enabled Goal maps to `planning`, now including the
graph. Enabling planning writes both internal flags. Disabling planning through
the legacy Harness flag retains the evidence layer. Disabling the Graph while
planning remains enabled fails with an actionable mode command. Do not combine
`--explore-mode` with the legacy enable flags in one request.

Both enabled modes register an `explore.turn_context` turn-start hook. Its
`required_reads` entry is a **before-work read obligation** in the normal packet.
The command returns at most three recent nodes/findings and, in planning mode,
three suggested Todo branches, plus structured commands for detail or evidence
recording. The read folds existing history but bounds the returned context;
it does not claim to reduce history IO. The agent chooses evidence-backed work;
planner suggestions do not require branching on every turn or recording empty
ceremonial nodes. Use the detail command when the short view is insufficient.

Explicit writeback results default to three full scoped details, not a hard
visibility limit. `graph.result_page` reports total/remaining counts and an
executable `next_command`. Follow that command to read further pages; its
revision binding rejects changed evidence instead of silently skipping results.
Restart the first page if the evidence changes. To expand a page or narrow it
to one question:

```sh
loopx explore turn-context --goal-id <id> --agent-id <agent> --result-limit 10
loopx explore turn-context --goal-id <id> --agent-id <agent> --result-node <node-id> --result-limit 10
```

Page size accepts 1–20; every detail retains applicability and interpretation.
Planning mode prioritizes one recent refutation per linked question before
repeated findings on the same question. Evidence-only mode retains recency
ordering. These reads do not alter planner scores, graph state, claims or quota.
The dashboard's saved-evidence reader also pages through history; the CLI
options above control the agent context, not dashboard page size.

Planning context also keeps a bounded `typed_evidence_audit` on suggested
branches with explicit Todo/node links, and up to three existing exploring
frontier nodes. Linked findings are resolved before the recent-history limit,
so unrelated newer results do not hide an older linked refutation. The audit
retains its diagnostic-only meaning and unchanged planner score; unknown links
are visible and omission counts refer to the bounded audit, not the whole log.
Use `todo update --goal-id <id> --agent-id <agent> --todo-id <todo>
--explore-result-node-ref <node>` under the ordinary update/lease contract to
link existing evidence. Before an experiment, use that evidence to explain the
route and discriminating probe; a repeat can test changed conditions or
uncertainty. These decision guidelines introduce no adoption/writeback gate,
automatic successor or spawn permission. A context read does not prove that the
model adopted the evidence or improved its result.

Mode selection does not grant spawn, claim, lease, execution, quota or external
publication authority. `spawn_allowed=false` retains analysis-only planning.
Feature-off adds no Explore hook or evidence reads. Turning off preserves all
recorded evidence and existing display state.

The evidence layer reuses the material-refresh projection and any separately
configured, authorized sink. Semantic digests avoid unchanged writes. Row sinks
must read back result ids; failed sync/readback leaves the digest retryable.
Visual sinks reconcile deterministic delivery markers before retrying writes.

When a single run may update local state but is not authorized to write any
configured external sink, keep the graph enabled and pass
`refresh-state --suppress-external-sinks`. LoopX still updates the canonical
local Explore projection, reports the suppression boundary in the refresh
packet, and leaves row/visual digests unchanged so a later authorized refresh
can retry delivery. This run-scoped boundary does not change the goal's Graph
or Harness opt-in settings.

Graph-on is a material-delivery postcondition, not a best-effort reminder.
An authorized `refresh-state` fails when a configured sink cannot sync and
read back; the caller must retry before claiming delivery. A suppressed run
may still commit canonical local state, but its packet reports an unsatisfied,
retryable postcondition and requires a concrete authorized-sync successor.
With no configured sink, local projection satisfies the postcondition. This
contract does not enable Explore Harness.

#### Explore Harness planning gate

Both opt-in planners — `todo-branch-plan` and `worker-branch-plan` —
are deny-by-default. The gate lives on the registered goal's `spawn_policy`,
the single writable source that the quota/status pipeline projects into
`quota should-run` as `goal_boundary.orchestration`. No other registry key is
honored: a second source would be an authorization surface invisible to the
quota boundary.

```yaml
# inside the registered goal entry
spawn_policy:
  spawn_allowed: false    # "allowed" is the accepted alias
  max_children: 3
  explore_harness:
    enabled: false        # default: both explore planners are disabled;
                          # must be boolean true — anything else fails closed
    profile: generic      # optional pin; overrides the CLI-requested profile
```

Use the incremental configuration path instead of editing the registry:

```bash
loopx configure-goal \
  --goal-id <id> \
  --explore-harness-enabled \
  --explore-harness-profile adaptive-resilient \
  --execute
```

This is analysis-only while spawn permission remains disabled. Use
`--no-explore-harness-enabled` to close the gate again, or
`--clear-explore-harness-profile` to let each planner request its own profile.
Preview without `--execute` shows the exact orchestration delta and preserves
unrelated `spawn_policy` keys.

The planner folds this boundary into an `orchestration_gate` section of the
packet and behaves as follows:

| Boundary state | Planner behavior |
| --- | --- |
| `enabled=false` (or goal unregistered / no boundary) | Explicit disabled packet with `required_contract`; no branches are emitted. |
| `enabled=true`, `spawn_allowed=false` | Read-only ranking and bundle analysis only; every `suggested_commands` list is emptied. |
| `enabled=true`, `spawn_allowed=true`, `max_children>0` | Suggested claim/lease commands are emitted, still dry-run only. |
| any enabled state | Lane width (`--width` / `--worker-width`) is capped by `max_children` in addition to the planner's own ceiling (`MAX_BRANCH_WIDTH` / `MAX_WORKER_LANES`); the binding cap is recorded in `orchestration_gate.width_cap_source`. |

`spawn_allowed=true` with `max_children=0` is treated as a contradiction and
degrades to the analysis-only state rather than granting capacity.

The gate is defense-in-depth for the planning surface, not a substitute for
runtime authority: permission, quota, gates, claims, leases, spend, and state
projection remain owned by the normal LoopX lifecycle regardless of the gate
state. `examples/explore-worker-plan-gate-smoke.py` covers the four states
for both planners, the `max_children` cap, and the CLI default-off path end
to end.

Use this worker-lane planner when the experiment is about dynamic branching:
several Codex workers exploring different routes, each route managing multiple
todos, then verified results merging back into the explore graph. Use
`todo-branch-plan` for the smaller micro-kernel case where the branch is just
one candidate todo.

## Composition Frontier

Typed research evidence is available through `loopx explore observe --goal-id
<goal> --observation-json <file>`. Its
[versioned contract](../../../docs/reference/protocols/research-observation-v0.md)
defines closure basis, explicit binary candidates, input-revision binding and
read-only `research_frontier` shadow in `explore summary`. The existing Lark
node Summary includes these same facts. This command grants no experiment,
Todo, replan, quota or Goal-acceptance authority; M3 enforcement is still pending.

The harness also projects **composition gaps** - the explicit combined-surface
todo derivation. When two individually covered surfaces have an
evidence-linked reason to be tested together, LoopX preserves that untested
relation as a gap instead of treating each surface as finished.

A composition experiment is an existing open Explore `experiment` node with at
least two outgoing `depends_on` edges to closed (`resolved` / `dead_end`),
evidence-backed input nodes. Only explicit graph edges qualify; the projection
never infers arbitrary node pairs, so the runtime and reviewer surface stay
linear in the recorded graph.

`project_live_explore_composition_frontier` folds this into
`loopx_explore_composition_frontier_v0` during `quota should-run` and turn
packets when `spawn_policy.explore_harness.enabled=true`:

- `gaps[]` (`loopx_explore_composition_gap_v0`): `gap_id`,
  `experiment_node_ref`, `input_node_refs`, status `pending|scheduled`,
  `required_outcome=joint_experiment_result`, and a `successor_summary`
  ("Run the bounded joint experiment: ...") with a `successor_binding` whose
  `explore_result_node_refs` points at the experiment node.
- `selected_gap`: the first pending gap (pending sorts before scheduled, then
  by input count descending, then by stable gap id); at most 3 gaps are
  projected (`MAX_PROJECTED_GAPS`).
- This legacy projection only considers open experiments; dropping a closed
  experiment or marking all gaps scheduled does not certify research closure.
  The separate typed research shadow requires attributable terminal evidence
  and current input fingerprints. Evidence-backed dismissal and shared
  write-time enforcement remain RFC milestones.

The gap becomes a normal runnable successor: a todo bound to the experiment
node (`--explore-result-node-ref <experiment-node>`), executed through the
normal LoopX lifecycle. The conceptual contract is in
[`research-exploration-control-plane-v0`](../../../docs/architecture/rfcs/research-exploration-control-plane-v0.md).

Create one explicitly:

```bash
loopx explore node --goal-id <id> --title "Combine A and B" --kind experiment --status open
loopx explore edge --goal-id <id> --from <experiment> --to A --type depends_on
loopx explore edge --goal-id <id> --from <experiment> --to B --type depends_on
```

Once A and B are `resolved` / `dead_end` with evidence, the next quota/turn
packet projects a pending composition gap and can derive the joint-experiment
successor todo.

### Adaptive Resilient Harness Profile

The `adaptive-resilient` worker harness profile captures the useful design
lessons from long-horizon exploration campaigns without copying an
experiment's incidental controls. It is not any single calibration run's
configuration made permanent. The profile keeps the parts that generalized well:

- independent-lane admission for lane count, where `--worker-width` is a
  ceiling and the planner may select fewer lanes -- but only for auditable
  reasons (queue exhaustion or measured interference), recorded per refusal
  in `admission_audit`. Expected evidence across parallel lanes is additive;
  the old cross-lane survival product treated independent worker processes as
  a serial speculative chain and structurally under-filled the width;
- value-first branch packing, where `--max-todos-per-branch` is a ceiling and
  branches are not padded just to look full;
- lane start staggering as runner guidance, because staggered launches reduced
  correlated infrastructure pressure;
- retry/backoff and infrastructure-family cooldown hints for repeated
  transient failures such as a provider service being unreachable;
- explicit A/B metadata so future runs can compare the profile against the
  priority-order baseline.

It deliberately does not control segment duration, does not force N=10, does
not saturate every available branch, and does not enable the earlier
coverage-floor calibration arm by default. Those remain runner or future-experiment decisions, not part of
the generalized harness design.

Retry/backoff and infrastructure cooldown are planner metadata for an external
runner; the generic runtime does not enforce them. Runtime results expose this
boundary explicitly instead of implying that selecting the profile activates a
hidden retry loop.

```text
loopx explore worker-branch-plan \
  --goal-id <id> \
  --harness-profile adaptive-resilient \
  [--worker-width <ceiling>] \
  [--max-todos-per-branch <ceiling>]
```

Use `--branch-fill-policy value-first` explicitly when you want the same
no-forced-fill behavior without the rest of the profile metadata. Use
`bundle-by-affinity` for the older compact grouping behavior.

### MoE Router Harness Profile

The `moe-router` profile treats worker-lane planning as MoE-style routing
under a fixed worker ceiling: task families (affinity keys such as
`scope:artifacts/<task>`) are the experts, todos are the routed tokens, and
lanes are just serving slots. It extends `adaptive-resilient` with a learned,
cross-epoch routing layer fed through `--router-state`:

- **Router state** (`loopx.capabilities.explore.router_state`, schema
  `loopx_explore_router_state_v0`): per-family EMAs of raw value rate
  (deliberately NOT novelty-discounted, so the estimator measures the
  environment rather than the router's own rerun policy), probe duration,
  acceptance rate, and infra failures, plus a global first-seen
  observation-key ledger that supplies each family's novelty prediction.
  The runner owns persistence and calls `observe_epoch` /`advance_epoch` at
  epoch boundaries -- the same cadence as the existing infra cooldown.
- **Routing score vs value bookkeeping** (the DeepSeek-V3 aux-loss-free
  invariant): each branch carries `routing_score = static score x
  (1 + UCB + coverage bonus + bias - infra penalty)` used ONLY for ordering,
  while `calibrated_confidence` (x family accept rate) and
  `novelty_adjusted_evidence_units` (x predicted novelty) feed admission and
  stay bias-free. The bias is a per-family scalar updated +/-gamma from
  coverage/novelty debt and surplus -- not load equality, which has no
  intrinsic value here -- with decay and clamping against windup.
- **Bundle length** is the faithful DSpark analog (arXiv:2607.05147): a
  lane's serial todo bundle is the draft block, and it truncates at the first
  todo whose calibrated acceptance confidence drops below
  `bundle_confidence_threshold` (`confident-prefix` fill policy). A
  wall-clock straggler guard (`bundle_straggler_factor` x median measured
  probe duration) caps the serial tail; it binds only on measured durations
  so cold-start defaults cannot silently force every bundle to length 1.
- **Load calibration**: pass the previous epoch's observed
  `{parallel_wall_minutes, max_branch_minutes, branch_count}` via
  `--load-profile` and lane admission prices measured interference through
  `calibrate_load_factor` instead of the hardcoded 0.2 prior.
- **Opportunistic expansion**: after calibration showed `moe-router` had better
  active-lane efficiency but wasted too many worker slots, the profile keeps
  the theta-peak core lanes and then admits additional positive-yield lanes up
  to a utilization floor. This is not saturated fill: each extra lane must
  clear an auditable independent lane-value floor, and refusals remain in
  `admission_audit`.

```text
loopx explore worker-branch-plan \
  --goal-id <id> \
  --harness-profile moe-router \
  --worker-width <ceiling> \
  [--router-state <router_state.json>] \
  [--load-profile <observed_profile.json>]
```

Without `--router-state` the profile still plans (router disabled, cold
static scoring); passing state to a non-router profile is ignored, which
keeps `adaptive-resilient` clean as the B-min ablation arm.

### Recoverable Execution Episodes

The budget-arm runtime has an optional, software-agnostic execution seam for
experiments that share an expensive setup prefix. A seed and its scheduled
variants become one **episode group**: the adapter prepares the base state
once, then executes each baseline or variant suffix from that same state.
LoopX owns grouping, observation accounting, and router feedback; the adapter
owns every application-specific fact, including how to restore isolation.

An adapter opts in only by implementing all three methods:

- `prepare_episode_group(seed_item, episode_items, **context)` returns a dict
  with an in-memory `handle`, one suffix-free `prefix_record`, and optionally
  an opaque, public-safe `checkpoint_ref`;
- `execute_episode(handle, item, **context)` restores or clones the prepared
  state as needed and returns observations produced only by that item suffix;
- `release_episode_group(handle, **context)` releases the adapter-owned state
  and is called on every path where a handle crossed the boundary, including
  suffix failure. (If prepare returns a malformed dict without a `handle`,
  the adapter kept ownership and no release call is possible.)

The legacy `execute` method stays optional for episode adapters: the runtime
only consults it when `prepare_episode_group` returns `None` for a group.

Suffix calls are currently sequential *within* a group, but distinct groups
with disjoint concurrency keys run concurrently on separate workers against
the same adapter instance. The three episode methods must therefore be
thread-safe across groups, and concurrently active groups must never alias
mutable execution state. Sequential handle reuse, immutable shared handles,
and adapter-managed shared resources remain valid when their isolation and
lifecycle are safe. Before every suffix call, including the baseline suffix,
the adapter must restore or clone the same prepared state; changes made by one
suffix must never leak into the next. Because a group serializes its suffixes
into one worker lane, an epoch's parallelism is bounded by its group count: an
adapter whose prepare is cheap for a given group (for example a single-item
group with no variants) should return `None` there to keep the legacy path and
avoid paying prepare/release for nothing.

The core has no VM, GUI, browser, process, or industrial-software type. For a
black-box desktop application, an adapter might implement the handle with a VM
snapshot, an application restart plus deterministic action replay, or an
isolated profile copy. A different exploration domain can use an API sandbox,
filesystem snapshot, simulator state, or any other recoverable mechanism
without changing the harness runtime.

`prepare_episode_group` may return `None` before making side effects to request
legacy, fresh `execute` calls for that group. Those fallback calls remain
sequential inside the already-admitted group. A prepare exception never falls
back silently because the environment may already be partially changed; it is
handled by the configured item failure policy. A partial three-method
implementation also fails closed. If prepare itself raises after making side
effects, cleanup remains the adapter's responsibility because no valid handle
has crossed the boundary; LoopX guarantees only that it will not silently run
fresh items in that uncertain state.

Grouping validates seed identities before fatal-mode planning and validates
the compiled epoch before any episode lifecycle call. Seed and variant ids
must be non-empty and globally unambiguous, and every variant's `seed_item_id`
must name a valid seed in that epoch. `list_seed_items` and, for variant checks,
`compile_variant` necessarily run before the corresponding validation. Under
the `fatal` policy, structural preflight raises `ValueError` before any
prepare, suffix execute, or release call. Under the default `record` policy,
each malformed item becomes one structured error record with
`episode_stage="group_validation"`, while every well-formed group still runs.
Because record mode completes the epoch, its checkpoint records catalog
consumption and resume does not re-pick the same malformed spec into a crash
loop.

Failure records stay truthful about which stage failed. A cleanup failure
after a successful group does not rewrite history: the prefix record keeps its
own `execution_status` and `accepted` flag, and the release failure travels in
`episode_release_error` plus `episode_stage="release"` (with
`retryable_infra_error` propagated). Under the fatal policy, when a suffix
error and a release error occur together, the suffix error propagates with the
cleanup failure chained as its `__cause__` — neither failure is swallowed.

Records carry generic execution lineage only:
`execution_group_id`, `record_kind=shared_prefix|episode_suffix|standalone`,
`seed_item_id`, `prefix_reused`, and optional `checkpoint_ref`. The novelty
ledger sees the shared prefix once and each suffix separately. Router feedback
folds one group's prefix and suffixes into one probe, so sibling branches do
not masquerade as independent family runs. The folded probe carries integer
`accepted_count` and `attempt_count`; the router sums those counts across
same-family groups, so its acceptance sample is suffix-count-weighted instead
of giving a small group and a large group equal weight.

Runtime results report shared-prefix, suffix, and standalone compute with two
explicit reuse views. `avoided_recompute_minutes = prefix_minutes *
(attempted_episode_count - 1)` measures structural prefix reuse, including an
attempted suffix that later failed. `successful_avoided_recompute_minutes =
prefix_minutes * (successful_episode_count - 1)` is the conservative result
view and excludes `adapter_error` suffixes; those remain visible through
`episode_error_count`.

Two metric caveats when comparing an episode arm against a standalone arm:
`novel_value` totals and AUC stay comparable (the first-seen ledger dedupes
identically in both modes), but `raw_value_total` does not — a standalone arm
re-reports base-state observations inside every item record while an episode
arm reports them once per group. And because groups serialize suffixes,
`requested_worker_minutes` charges workers the scheduler structurally cannot
engage when groups are fewer than workers; `execution_unit_count` per epoch
records the real dispatch width. `effective_compute_minutes` sums the reported
prefix, suffix, and standalone durations but excludes release/cleanup; use
`epoch_wall_minutes` and arm `elapsed_minutes` for end-to-end timing that also
includes lifecycle and scheduler overhead. The standard `aggregate_arms`
comparison exposes each arm's `execution_metrics` alongside its value metrics.

This adapter checkpoint is deliberately distinct from the harness restart
manifest below. The adapter handle is live execution state and is never
serialized by LoopX; the epoch-boundary manifest restores scheduler and
accounting state after a process restart.

### Runtime Restart And Item Failures

`run_budget_arm` can write an atomic epoch-boundary checkpoint manifest.
Restart is opt-in: start an arm with `resumable=True` (or an explicit
`checkpoint_path`), then pass `resume=True` to restore completed epochs,
novelty keys, router state, catalog consumption,
cumulative metrics, coverage timestamps, and the next epoch. Missing, corrupt,
or runtime-incompatible manifests fail closed with a concrete `ValueError`;
loose rolling progress files are observability only and are never restart
authority.

Adapter exceptions default to the `record` item failure policy: the failed item
becomes a zero-value structured observation and independent queue lanes keep
running. Concurrency keys are released in every path. Pass
`item_failure_policy="fatal"`, or set the adapter's `item_failure_policy`
attribute to `"fatal"`, to retain exception propagation. These policies isolate
work-item failures; they do not implement the planner profile's retry/backoff or
cooldown guidance.

## Presentation Sink: Lark Mapping

| LoopX concept | Lark surface |
| --- | --- |
| node | `Nodes` table row keyed by `LoopX Result ID`; `Status=blocked` rows carry `Blocked Reason` |
| edge | `Edges` table row keyed by `LoopX Result ID`; `From Node Link` and `To Node Link` are linked-record cells pointing at `Nodes`, so the Base data model itself carries the topology |
| finding | `Findings` table row keyed by `LoopX Result ID`; latest event wins |
| row lineage | `Row Lifecycle`, `Supersedes`, `Superseded By`, `Source ID` columns |
| dashboard card | transport-free interactive card content from the same projection |

Record identity follows the Lark Kanban adapter contract: rows are matched by
the `LoopX Goal ID` + `LoopX Result ID` columns, remembered in the local
config as `result_records`, and the map is rebuilt from all goal-filtered
remote pages before executed upserts. Executed sync compares canonical values
with the remote row and skips unchanged records. Newly created record ids are
persisted immediately, so an interrupted large-graph sync can resume without
recreating rows that were already delivered.

For the issue-fix domain, the default `lark-kanban sync-loopx-todos` call also
projects material domain-state, todo, and rollout transitions into this result
layer. It invokes remote Explore sync only when a timestamp-free semantic graph
digest differs from the last successful sink digest. This keeps the graph
continuously current without spending writes on unchanged CI/review polls. It
uses the result layer only and does not enable or depend on Explore Harness
worker orchestration.

An optional owner-facing stage document is configured separately because linked
Base rows and rendered graphs are different delivery receipts. Configure the
Docx and its first whiteboard with `explore feishu-visual-configure`; the Docx
may be a root-level resource inside the same Base so the graph and Kanban share
one operator entry point. Each bounded Evidence Stage owns one document section
and one independent whiteboard. Missing sections and blank whiteboards are
created automatically when the sink has a Docx token. Stage capacity is
configurable from 10 through 20 nodes and defaults to 14. Full Nodes, Edges, and
Findings always remain in the canonical Base.

`board_style` is the first-class layout contract and is independent from
`projection_mode`, which controls evidence selection. Two styles are supported:

| Board style | Best fit | Rendering behavior |
| --- | --- | --- |
| `auto_flow` | Generic or single-lane Explore graphs | Mermaid chooses the graph layout while LoopX preserves stage order, lanes, statuses, and real directed edges. |
| `semantic_lane_columns` | Operator boards with meaningful parallel lanes such as PR issue-fix and capability work | LoopX emits deterministic SVG columns, keeps each lane top-to-bottom, and draws the real within-stage directed edges. |

The renderer (`mermaid` or `stage_svg`) is an implementation detail derived
from `board_style`. Existing local configs that only store `renderer=mermaid`
remain readable as `auto_flow`.

On first configuration, omitting `--board-style` defaults the new visual role
to `auto_flow`. On later calls for the same role, omission preserves the stored
style and its validated renderer. This makes Docx or Evidence Stage token
maintenance a patch operation instead of an implicit style reset. Pass an
explicit `--board-style` only when intentionally switching the layout.

A material sync checkpoints `canonical_rows_semantic_digest`
and `visual_semantic_digest` independently. If whiteboard publication fails
after Base rows succeed, the next run retries only the visual sink instead of
rewriting unchanged rows. `status=synced` therefore means every configured sink
completed; callers can inspect `canonical_rows_status` and `visual_status`
separately.

The default `canonical_filtered` projection obeys configured status/tag filters.
Projects define lanes with `lane-<name>` tags on work nodes or their ancestors.
Each stage groups its nodes by lane and keeps real directed relations visible
inside that board, including cross-lane edges. Issue-fix projections therefore
show their PR delivery and LoopX capability lanes together, while a one-lane
project such as zjxmt renders one lane without synthetic empty structure. This
changes presentation only, never evidence state.

For a same-source dual view, configure stage documents by role. Repeat
`--stage-whiteboard-token` for already-created stage boards; missing boards are
created under matching `Evidence Stage NN` sections when `--docx-token` is set:

```bash
loopx explore feishu-visual-configure \
  --view-role canonical \
  --projection-mode canonical_full \
  --whiteboard-token <canonical-token> \
  --docx-token <canonical-doc-token> \
  --stage-capacity 14 \
  --execute
loopx explore feishu-visual-configure \
  --view-role executive \
  --projection-mode executive_auto \
  --whiteboard-token <executive-token> \
  --docx-token <executive-doc-token> \
  --stage-capacity 14 \
  --board-style semantic_lane_columns \
  --execute
```

`feishu-sync` then generates both views in one local projection step and
publishes one whiteboard per stage. It always publishes the canonical role and
publishes the executive role when the bundle recommends `dual_view`. A derived
view whose source revision or digest differs from the current canonical
projection is rejected before any whiteboard command runs. Legacy grid/SVG
renderer configuration fails with an explicit migration message instead of
silently publishing the wrong visual form.

The visual sync is not satisfied when any recommended role is missing from the
configured sinks. Its top-level receipt stays `published=false`, names the
missing roles, and returns a retryable configuration action; successful
per-role diagnostics may still be inspected, but they cannot make the overall
sink look current or advance its delivery checkpoint.

Executable sync is singleflight per local board config across both the direct
`feishu-sync` command and automatic material refresh. An overlapping process
fails before row, visual, or checkpoint writes with `status=sync_busy`,
`retryable=true`, and `external_write_performed=false`; dry-runs remain
concurrent because they cannot mutate the sink. Retry after the active process
exits instead of allowing two upsert scans to create duplicate Result IDs or
overwrite each other's local checkpoint snapshot.

The lock is reentrant inside one execution context, so a batch that already
owns the board lock can call the direct command or automatic material sync
without rejecting itself as `sync_busy`. Reuse never crosses an execution
context or process; independent writers still fail fast.

The text `From Node` / `To Node` columns remain stable public ids for
automation and review, while the linked-record columns are the Feishu-native
graph substrate. A Base plugin, relationship-aware view, or Feishu dashboard
component can read those links directly; LoopX must not downgrade the graph
back to a screenshot-only artifact.

This sink is a presentation boundary, not a value connector. Value connectors
own external signal input, permissions, and source authority; presentation
sinks render public-safe explore projections for operators.

## CLI Surface

```text
loopx explore schema
loopx explore node --goal-id <id> --title <t> [--node-id ...] [--status ...] [--blocked-reason ...] [--parent ...]
loopx explore edge --goal-id <id> --from <node> --to <node> --type <edge-type>
loopx explore finding --goal-id <id> --title <t> [--node ...] [--status ...] [--confidence ...]
loopx explore summary --goal-id <id>
loopx explore presentation --goal-id <id>
loopx explore graph --goal-id <id> [--graph-format mermaid|json] [--out <file>]
loopx explore todo-branch-plan --goal-id <id> [--agent-id <agent>] [--width 3]
loopx explore worker-branch-plan --goal-id <id> [--agent-id <agent>] [--harness-profile generic|adaptive-resilient|moe-router] [--worker-width 3] [--max-todos-per-branch 3] [--router-state <file>] [--load-profile <file>]
loopx explore feishu-setup [--base-url ...] [--execute]
loopx explore feishu-visual-configure [--whiteboard-token <token>] --docx-token <token> [--stage-whiteboard-token <token> ...] [--stage-capacity 10..20] [--board-style auto_flow|semantic_lane_columns] [--view-role canonical|executive] [--projection-mode canonical_filtered|issue_fix_two_lane|canonical_full|executive_auto] [--tag <tag>] [--status <status>] [--execute]
loopx explore feishu-sync --goal-id <id> [--sink-visibility owner-only|shared] [--execute]
loopx explore feishu-card --goal-id <id> [--card-file <file>] [--message-id om_...]
```

`feishu-setup` and `feishu-sync` are dry-run unless `--execute` is set; the
dry-run payload contains the full command plan for review.

## Review Boundary

Rows and cards deliberately exclude raw agent transcripts, worker commands,
credentials, and local absolute paths. Evidence lives behind compact public
refs; the private material itself stays in the goal's normal local state or
memory backend. `--sink-visibility shared` additionally redacts private
links and external ids through the shared Kanban redaction rules before rows
leave the machine. Card content is build-only: sending or updating the actual
Lark message is the job of an approved gateway (bot or lark-cli) after the
operator permits the write.

## Validation

```bash
python3 examples/explore-result-layer-smoke.py
python3 examples/issue-fix-explore-projection-smoke.py
python3 examples/explore-harness-runtime-resume-smoke.py
python3 -m pytest -q \
  tests/test_explore_episode_runtime.py \
  tests/test_explore_router_acceptance.py
```

The smoke proves the projection contract (folding, blocked reasons, tree,
Mermaid), record-time path rejection, dry-run default, paginated discovery,
zero-write idempotent resync, single-row drift repair, nested create-receipt
handling, shared-visibility redaction, transport-free card
content, the opt-in todo branch-plan packet, the adaptive resilient
worker harness profile, and the CLI surface against a temp registry, without
live Lark credentials. It additionally proves the worker-lane router
contracts: requested width is no longer silently clamped below the worker
ceiling, idle lanes are queue-exhaustion (not a cap) under independent-lane
admission, the routing bias reorders lanes without touching value
bookkeeping, confident-prefix bundles truncate at the calibrated threshold
and collapse for reject-heavy families, the router-state novelty ledger
dedupes across epochs while coverage debt accrues bias, and observed load
profiles calibrate admission through the CLI flags.

The runtime smoke and focused pytest modules also cover recoverable prefix
reuse, prefix/suffix novelty and dual reuse accounting, suffix-count-weighted
router acceptance, explicit legacy fallback, restart compatibility, cleanup on
recorded and fatal failures, concurrent groups without mutable-state aliasing,
fatal structural preflight, record-mode malformed-item isolation with resume
continuity, aggregate metric projection, and episode-only adapters without a
legacy `execute` method.
