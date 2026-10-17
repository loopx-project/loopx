# Materials, Explore And Change Quality

Read before registering durable material, changing an owner-facing Explore view or qualifying a final diff with enabled change-quality policy.

## Register Project Authority And Material Sources

When a project agent discovers a durable design document, research note,
benchmark paper, owner packet, migration report, or external material that
future agents may need for routing, validation, or conflict resolution, treat
that as a doc-registry skill trigger. Identify the target project and goal
first; do not register material into the current meta goal just because this
worker found it.

For material owned by the current project, update the project-local doc registry
or equivalent authority map first, then register the compact redacted source
contract in the same project's ignored `.loopx/registry.json`:

```bash
loopx register-authority-source --goal-id <STABLE_GOAL_ID> ...
```

For another project's DOC_REGISTRY-style map, import only the compact authority
summary instead of copying raw paths, document ids, URLs, comments, or source
bodies:

```bash
loopx import-doc-registry-authority --goal-id <STABLE_GOAL_ID> ...
```

After registration, refresh status or state so review packets, read-only maps,
and heartbeat workers can find the new authority without relying on chat
memory. Stop and write a project-local todo or blocker when the target project
is ambiguous, the source cannot be represented as public-safe metadata, or the
next step would require reading a gated source body.

## Owner-Facing Explore Views

Treat the canonical Explore topology and an owner-facing decision graph as two
different read models over one evidence source:

- canonical JSON, Mermaid, and Nodes/Edges/Findings tables preserve complete
  public-safe node identity, evidence, and lineage;
- focused status/tag exports are bounded evidence subsets, not executive views
  by default;
- an executive graph applies semantic compression for operator decisions. It
  should show the decision contract, baseline/incumbent, decisive negative
  evidence, active work or capacity, material risk, terminal gate, and next
  decision. Semantic compression may tighten labels and remove true
  duplication, but it must preserve material decision and evidence nodes plus
  their lineage.

Never overwrite an executive whiteboard directly from a full or focused
canonical export unless a declared presentation contract proves that the
export already carries those semantic roles. For a recurring sync, keep a
project-local display contract with required roles, stable canonical ids,
semantic sections or linked subgraphs, and a non-identity guard. The default
cardinality policy is graph growth: do not omit or merge away a material node
because the graph crossed a generic count such as 20 nodes. A hard
`max_nodes` or `max_edges` limit is valid only as an explicit opt-in
presentation policy with declared scope and overflow behavior; absent that
policy, treat those limits as unbounded.

Render with the target renderer, run overlap and text-overflow checks, inspect
the actual preview, then sync and verify the remote source or digest. Repair a
failed readability check by relayout, shorter labels, larger frames, or more
semantic subgraphs, not by deleting material evidence. If any check still
fails, keep the previous owner view; do not publish a structurally valid but
unreadable graph.

This separation does not transfer quota, todo, launch, stop, or promotion
authority into the presentation layer. Record material experiment transitions
in canonical Explore state first, then refresh the executive projection.

## Qualify Non-Trivial Final Diffs

Before a non-trivial delivery or merge, inspect the current goal configuration.
When `change_quality_qualification.enabled` is true, load
`loopx-change-quality` and follow its exact-scope workflow:

```bash
loopx --format json change-quality prepare \
  --goal-id <STABLE_GOAL_ID> \
  --repo-path .
```

The policy keeps two decisions separate: `safe_fix` permits at most one bounded
repair pass, while `strict_receipt` requires a passing receipt for the exact
final diff. Any edit invalidates the old fingerprint. Record and verify the
final receipt, then pass `--goal-id <STABLE_GOAL_ID>` to `canary premerge`.

If the host cannot load skills, use the self-contained prepare packet as the
review contract. Turn may carry the packet or receipt reference, but it does
not own policy or enforcement. Do not invent a receipt, reuse one from an older
diff, or turn subjective style advice into a blocker.
