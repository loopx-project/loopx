# Content reference artifacts v0

Content Ops owns source/style metadata, retrieval and attributed outline
preparation. The shared TypeScript owner is
`loopx/control_plane/capabilities/content_reference.ts`; Python is transport
and local artifact IO. This extends the existing built-in `content-ops`
capability, not a new capability, provider registration or enablement switch.

This is a local artifact preparation slice of the direct-first
[capability portfolio RFC](../../architecture/rfcs/goal-scoped-capability-portfolio-v0.md).
It is not a material-store migration or a completed Portfolio runtime.
The original catalog remains authoritative. Material Lifecycle owns inventory,
lifecycle, ranking and apply/rollback; this slice reuses its read-only inventory
SDK. A concrete source verifier/write provider remains a separate prerequisite.

## Existing catalog and input

Import a caller-owned JSON object with `entries`. Existing entries have stable
`id`, `title`, HTTPS `source_url`, `author`, and timezone-qualified `captured_at`.
Optional metadata includes `source_revision`, `published_at`, `tags`, ordered
`structure`, `uses`, `caveats`, `reuse_boundary`, `reading_boundary`, `style.opening`, `style.tone`,
`metrics.observed_at` and nonnegative integer `metrics.counts`. Existing backing
references and unknown legacy fields remain in the original catalog; they are
not copied into search or outline results. Unknown revision, style, permission
or lifecycle state stays null, never inferred from a URL or preference.

Capture input is `{ "reference": { ... }, "expected_source_revision": ... }`.
It accepts only the fields above (excluding lifecycle state). It requires a
source revision, reuse and reading boundaries, opening, tone, nonempty structure and caveats.
Write an explicit uncertainty in descriptive fields when not established.
An opaque revision identifies the caller's actual read artifact; it is not
proof that reading occurred. These commands never fetch or verify the source.
Engagement requires the actual observation time and is not proof of virality,
adoption or causal writing effectiveness. Reading coverage belongs in the
original backing and caveats; omitted coverage remains unknown.

A source URL can belong to only one stable ID. A correction must retain that
identity and URL, and match `expected_source_revision`; an unversioned legacy
entry requires explicit null. The prepared artifact retains old backing fields.
A mismatched version fails before preparation. No command silently merges
conflicting sources, changes lifecycle state or overwrites the original file.

`content_ops_reference_v0` projects only the typed metadata above and a stable
`content-reference:<id>:<revision-or-unversioned>` reference.
`content_ops_reference_result_v0` wraps search, capture or outline results. All
are `local_private`, with `source_read_performed`, `store_write_performed`,
`lifecycle_write_performed`, and `publish_authorized` false. A prepared capture
includes `library`, preserving unknown legacy data, so the complete result must
remain private. It is not a public-safe export or a committed apply receipt.

## CLI and frontend

```sh
loopx content-ops reference search --library-json catalog.private.json \
  --query release --structure conditions --format json
loopx content-ops reference capture --library-json catalog.private.json \
  --input-json capture.private.json --output-json capture-result.private.json \
  --format json
loopx content-ops reference draft --library-json catalog.private.json \
  --input-json outline-request.private.json --format json
loopx content-ops reference inventory --library-json catalog.private.json \
  --goal-id example-goal --store-id existing-library \
  --observed-at 2026-09-01T11:00:00Z --format json
```

Search matches topic/use/title and an optional structure substring, preserving
original order. Archived entries are excluded; missing lifecycle state is
counted separately, not treated as active. Draft input supplies `reference_id`,
the retrieved `expected_source_revision`, `subject`, and caller-owned `facts`.
It returns a structure outline, unused facts, original credit/URL/revision,
reuse boundary and caveats. It refuses an unavailable/archived reference,
unknown reuse boundary, version mismatch or empty facts. It never substitutes
the source's assertions for facts about the caller's subject.

Discover this route through the existing capability catalog. In the packaged
frontend, open **Settings → Capabilities → a Goal → Reference styles**.
Import an authorized catalog, filter references, choose a structure and supply
your own facts to prepare an attributed outline. Capture/correction is an
optional request-JSON section. Downloads are new private review artifacts;
they do not write back to the catalog. Imported content stays in browser page
memory, is not sent to the Chat server, and is cleared on leaving the tool,
changing Goal, or selecting **Clear page materials**. Invalid replacement
imports clear the old view before showing the error.

`--output-json` creates a separate file with exclusive creation and owner-only
permissions; an existing path is refused. Do not use it as an in-place store
writer. Stop using this operation or clear the page to disable participation;
no capability setting, skill install, source activation or background task is
created. There is no published-source read or publishing operation to disable.

`content_ops_reference_material_inspection_v0` reports the original byte digest
and the existing `material_store_inventory_v0`. Known lifecycle counts come
from existing explicit fields; unknown legacy counts are separate, so the
inventory count may be smaller than the original catalog's item count.
No backup is created: `backup_verified=false`, `backup_ref=backup:unverified`.
`apply_available=false` explains the missing qualified provider. A Goal name
scopes this metadata packet and does not grant source or store access.

## Delivery boundary and next owner

This slice delivers reusable preparation/search/outline operations and packaged
frontend access. It does not implement canonical material writes, lifecycle
mutation, ranking, rollback or independent Agent adoption. Before claiming the
managed-store journey complete, qualify the existing Material Lifecycle source
provider against current Core workspace/source authority, original-file parity,
immutable backup, CAS, failure recovery and revoked permission. Use that owner's
apply/readback/rollback receipts; do not add another catalog, configuration
switch or effect store. An independent qualified Agent must then use the
supported route and return the actually attributed artifact. A CLI/UI test is
not that independent adoption evidence.

Validation targets identity collision, correction with preserved backing,
unversioned data, stale/unavailable sources, unknown reuse boundaries, dated
counters, private artifact overwrite refusal, default-off inventory and
browser-local failure/clearing. Synthetic fixtures do not establish source
rights, factual accuracy or live publishing effectiveness.
