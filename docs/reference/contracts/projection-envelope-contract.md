# Projection Envelope Contract

Every operator- or agent-facing read model carries one `projection_envelope`
(`loopx_projection_envelope_v0`). It says when the projection observed its
sources, how old each source read is at the moment this copy is served, and
how much of the requested scope the projection covers. The rule that
[RFC §2.6](../../architecture/rfcs/typescript-control-plane-migration-v0.md#26-projection-envelope-is-a-kernel-read-contract)
makes kernel-owned is simple: `ok: true`, a passing check, a healthy host, or
a cache hit never implies fresh or complete sources.

The TypeScript kernel (`loopx/control_plane/projection_envelope.ts`, runtime
method `projection.envelope.seal`) is the only implementation that decides
freshness, alerts, and completeness. Producers pass compact read facts;
Python-owned projections do so through `loopx/control_plane/projection_envelope_facts.py`.

## Current carriers

| Projection | `projection` | Coverage scope |
| --- | --- | --- |
| `loopx status` | `status` | `registry`, `goal` with `--goal-id`, or `activation.<state>` |
| `loopx status --use-projection-cache` hit | `status`, `served_from_cache: true` | as stored |
| `loopx global-summary` | `global_summary` | `global` |
| `loopx global-gates` | `global_gates` | `global` |

Other read models adopt the envelope in the order listed in the RFC; until
then they carry no freshness guarantee and consumer rule 1 below applies.

## Shape

```json
{
  "schema_version": "loopx_projection_envelope_v0",
  "projection": "global_summary",
  "observed_at": "2026-09-26T10:00:05Z",
  "served_at": "2026-09-26T10:00:05Z",
  "age_seconds": 0,
  "served_from_cache": false,
  "fresh": true,
  "complete": false,
  "alert": true,
  "alert_reasons": ["incomplete_coverage"],
  "alert_source_ids": [],
  "sources": [
    {
      "source_id": "goal_quota",
      "required": true,
      "read_status": "read",
      "last_read_at": "2026-09-26T10:00:04Z",
      "source_updated_at": null,
      "window_seconds": 300,
      "staleness_seconds": 1,
      "status": "fresh",
      "item_count": 1,
      "missing_count": 0,
      "unreadable_count": 0,
      "alert": false,
      "alert_reasons": []
    },
    { "source_id": "registry", "via": "status", "...": "inherited from the status envelope" }
  ],
  "coverage": {
    "scope": "global",
    "expected_count": 48,
    "included_count": 1,
    "omitted": [{ "reason": "outside_current_registry", "count": 47, "refs": ["goal-b", "..."] }],
    "shown_count": 8,
    "available_count": 19,
    "truncated": true,
    "complete": false
  },
  "upstream": [{ "projection": "status", "observed_at": "2026-09-26T10:00:03Z", "complete": true }]
}
```

## Field semantics

- `observed_at`: when the projection finished reading its sources. A cached or
  persisted copy keeps it; `served_at` and `age_seconds` say when this copy
  was emitted.
- `last_read_at`: when this projection (or its upstream) last read the source.
  `staleness_seconds = served_at - last_read_at`; a read source is `stale` once
  that exceeds `window_seconds` (kernel default 300).
- `source_updated_at`: the source's own last write when the producer knows it,
  such as the newest run in the run indexes. An old value means the goal has
  not moved, not that the read is stale; it never raises an alert.
- `read_status` is `read`, `missing`, `unreadable`, or `not_read`. `unreadable`
  always alerts; `missing` and `not_read` alert only when `required` is true.
  A read aggregate with `unreadable_count > 0` alerts as `partially_unreadable`.
- `coverage` is relative to the requested scope. `complete` requires no
  `omitted` rows, `included_count >= expected_count`, and every upstream
  envelope complete. `truncated` only discloses a requested display limit and
  never alerts on its own.
- `via` marks a row inherited from an upstream envelope. A derived projection
  inherits every upstream source row, so its freshness is bounded by the oldest
  read it depends on, not by its own assembly time.
- Envelope-level `alert_reasons` is a sorted subset of `stale_sources`,
  `unreadable_sources`, `missing_required_sources`, and `incomplete_coverage`.

Source ids, scope names, and reason codes are lowercase identifiers. The
envelope never contains filesystem paths, so public-safe projections can carry
it unchanged. Omission `refs` hold at most 8 identifiers such as goal ids.

## Consumer rule

Before stating that something is the current or whole state, a consumer reads
the envelope and discloses it:

1. A missing envelope means freshness and coverage are unknown. Say so; do not
   infer freshness from `ok`, `generated_at`, or a recent command.
2. `alert: true` must be stated along with the reasons, the affected
   `alert_source_ids`, and the omissions before any conclusion that depends on
   them. Markdown renderers print this as a `🔴 projection alerts` line.
3. A copy read later than `served_at` (a pasted packet, a saved file, a chat
   quote) is as old as `now - observed_at`. Re-read the projection instead of
   re-serving it from memory.
4. `truncated: true` means only `shown_count` of `available_count` items are
   listed. Absence from the list is not absence from the scope.

## Cache and replay

`status --use-projection-cache` re-serves the stored envelope through the
kernel on every hit, which restamps `served_at` and recomputes staleness. A
cache record without an envelope, or with one the kernel decoder rejects, is a
cache miss (`missing_projection_envelope` / `invalid_projection_envelope`),
never an unlabeled hit.
