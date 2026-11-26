---
name: loopx-doc-registry
description: Use when a connected LoopX project is asked to read, remember, record, index, register, or use a durable project material such as a Lark/wiki/design doc, research note, SOP, owner packet, migration report, benchmark paper, or external material source. Use even when the user does not mention LoopX or doc registry.
---

# LoopX Doc Registry

Use this skill for durable project materials. The goal is to make future agents
find the material from the project authority surface, not only from chat or
personal memory.

## Default Route

1. Resolve the target project and stable `goal_id` from the current repo or the
   user's named project. Prefer `.loopx/registry.json` and
   `.codex/goals/<goal-id>/ACTIVE_GOAL_STATE.md`.
2. If the material belongs to that project, register it in that project's own
   authority surface. Do not register it into `loopx-meta` just because
   the current worker discovered it.
3. If the project has a tracked `docs/meta/DOC_REGISTRY.yaml` or equivalent,
   update that first. If it does not, use `.loopx/registry.json` as the
   project-local doc registry through `authority_registry.topic_authority` and
   `authority_registry.project_materials`.
4. Run `loopx register-authority-source` with a redacted source contract.
   Raw URLs, doc ids, local private paths, comments, and source bodies must not
   be stored in public files.
5. Refresh status or state so review packets, read-only maps, dashboards, and
   heartbeat workers can find the material.

Memory extensions are allowed only as secondary personal reminders. They are
not a substitute for project-local authority registration when the project is
connected to LoopX.

## Command Shape

From the target project:

```bash
loopx --registry .loopx/registry.json register-authority-source \
  --goal-id <goal-id> \
  --source-id <stable-source-id> \
  --source-ref "<raw-url-or-private-path-to-hash>" \
  --source-kind <doc|lark_doc|wiki|paper|owner_packet|migration_report> \
  --role <public-safe-role> \
  --freshness <current|historical|unknown> \
  --owner-status <public-safe-owner-status> \
  --gate-status <readable|needs_access|owner_review_pending> \
  --boundary private_redacted \
  --revision "<public-safe-revision-label>" \
  --conflict-rule "<public-safe-conflict-rule>" \
  --topic <topic-key>
```

Use `--dry-run` first when the source classification or target project is not
obvious.

## Find Registered Materials

Before registering a duplicate, inspect the target project's existing material
metadata through the same local registry:

```bash
loopx --registry .loopx/registry.json --format json authority-sources \
  --goal-id <goal-id> --query "<topic or revision terms>" --limit 20
```

The query matches all whitespace-separated terms across source IDs, registered
topics and metadata, ignoring case and Unicode compatibility differences. Add
`--topic <exact-topic-key>` to restrict a query to a topic. Results are ordered by
source ID; repeat the same filters with the returned `next_offset` as `--offset`
to continue. `source_count`, `matched_count` and `returned_count` distinguish an
empty inventory, no matches and a partial page. The page limit bounds returned
rows, not registry scan cost or bytes; the complete local registry is read.

This read does not open documents, check current access, refresh freshness or
grant authority. It displays the stored revision, gate and freshness labels as
metadata, including `needs_access` and historical material. Register/update the
source through its existing workflow when those facts change. The command
reads the explicitly selected registry snapshot; select the project's source
registry rather than a global summary when looking for its complete inventory.
Summary-only snapshots return `authority_source_details_unavailable` with the
recorded summary count and an explicit project-registry recovery instruction,
even when that count is zero; unavailable details are not an empty inventory.
Topic lookup uses the material frontier's existing string, object and list
reference interpretation for displayed topics, exact filters and text search.
Registration retains only a source-reference kind and digest, not its original
URL/path. Use the original DOC_REGISTRY or authorized connector to locate and
check the document, then reuse its listed source ID with
`update-authority-source` to update the recorded metadata. An inventory match
is not evidence that the document was read or is still accessible.

## Maintain Existing Material Metadata

After checking a material, preview a change to its recorded lifecycle metadata:

```bash
loopx --registry .loopx/registry.json update-authority-source \
  --goal-id example-goal --source-id design-note \
  --freshness stale --gate-status needs_access --dry-run
```

Remove `--dry-run` to apply and refresh the shared registry summary; use
`--no-global-sync` to update only the project registry. Read back with
`authority-sources --goal-id example-goal --topic design` (using the registered
topic), or search for the source ID. Optional update fields are `--freshness`,
`--owner-status`, `--gate-status`, `--revision` and `--conflict-rule`. At least one
nonempty field is required. Omitted fields, original registration time, reference
kind/digest, imported summary metadata and topic mappings remain unchanged.
The source must already exist in the selected project registry. A shared summary
is insufficient; select the authoritative project registry explicitly.

This records observations, not proof of reading, current access or permission to
access a source. No source is opened. Registration keeps its existing full-entry
replacement semantics; use it for deliberate replacement, supplying the complete
contract. Project writes use the existing registry transaction; shared-summary
sync follows separately. If sync fails after the project update, the failure
reports `ok: false` and `written: true`. Inspect the project readback; after
restoring shared registry access, sync the current project snapshot with the same
Goal and runtime root instead of replaying the metadata patch:

```bash
loopx --registry .loopx/registry.json --runtime-root /path/to/runtime \
  sync-global --goal-id example-goal
```

Use the original resolved runtime root. Sync-only recovery preserves newer
metadata observations recorded since the failure.

## Stop Conditions

Stop and write a project-local todo or blocker instead of registering when:

- the target project or goal is ambiguous;
- the material cannot be represented as public-safe metadata;
- registering would require reading private content that was not requested or
  permitted;
- the source conflicts with a newer owner-approved material and the conflict
  rule is unclear.
