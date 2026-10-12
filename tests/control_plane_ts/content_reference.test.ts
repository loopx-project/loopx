import assert from "node:assert/strict";
import test from "node:test";
import {captureContentReference as capture, planReferenceDraft as draft, searchContentReferences as search} from "../../loopx/control_plane/capabilities/content_reference.ts";

const reference = () => ({id: "source-demo", title: "A measured open-source release", source_url: "https://example.org/post/1",
  author: "Example author", source_revision: "read:1", captured_at: "2026-09-01T10:00:00Z",
  style: {opening: "Lead with a concrete result", tone: "Direct, with limitations"},
  tags: ["release"], structure: ["result", "conditions", "try it"], uses: ["project announcement"],
  caveats: ["Claims are not independently reproduced"], reuse_boundary: "Reference structure only; credit source; no copied text",
  reading_boundary: "Synthetic metadata fixture; no source fetched",
  metrics: {observed_at: "2026-09-01T10:00:00Z", counts: {views: 100}}});

test("capture -> query -> attributed outline is a real metadata route without effects", () => {
  const prepared = capture({library: {entries: []}, reference: reference()});
  const result = search({library: prepared.library, query: "release", structure: "conditions"});
  assert.equal(result.references.length, 1);
  assert.equal(search({library: prepared.library, query: "unrelated"}).references.length, 0);
  const outline = draft({library: prepared.library, reference_id: "source-demo", expected_source_revision: "read:1", subject: "My project", facts: ["A measured result", "Same workload", "A runnable example", "Additional fact"]});
  assert.equal(outline.source_map[0]!.attribution, "Example author");
  assert.equal(outline.source_map[0]!.use, "structure_reference");
  assert.equal(outline.steps[1]!.fact, "Same workload");
  assert.deepEqual(outline.unused_facts, ["Additional fact"]);
  assert.equal(outline.store_write_performed, false);
  assert.equal(outline.publish_authorized, false);
  assert.equal(outline.visibility, "local_private");
});

test("identity collisions and changed revisions fail; corrections preserve legacy backing", () => {
  const old = {...reference(), source_revision: undefined, card: "cards/demo.md", evidence: ["evidence/demo.json"], lifecycle_state: "candidate",
    style: {opening: "Old opening", tone: "Old tone", legacy_pattern: {retain: ["sentinel", {version: 1}]}}};
  const library = {description: "Existing owner catalog", entries: [old]};
  assert.throws(() => capture({library, reference: reference()}), /provide expected_source_revision/);
  assert.throws(() => capture({library, reference: {...reference(), id: "new-id"}}), /reuse that identity/);
  assert.throws(() => capture({library, reference: {...reference(), source_url: "https://example.org/post/2"}}), /reassigned/);
  const corrected = capture({library, reference: reference(), expected_source_revision: null});
  assert.equal((corrected.library.entries as typeof old[])[0]!.card, old.card);
  assert.deepEqual((corrected.library.entries as typeof old[])[0]!.evidence, old.evidence);
  assert.equal((corrected.library.entries as typeof old[])[0]!.lifecycle_state, "candidate");
  assert.equal(corrected.reference.lifecycle_state, "candidate");
  assert.deepEqual((corrected.library.entries as typeof old[])[0]!.style, {...old.style, ...reference().style});
  assert.equal(library.entries[0]!.style.opening, "Old opening");
  assert.equal(library.entries[0]!.source_revision, undefined);
  assert.throws(() => draft({library: corrected.library, reference_id: "source-demo", expected_source_revision: null, subject: "X", facts: ["Y"]}), /revision changed/);
  assert.throws(() => search({library: {entries: [reference(), {...reference(), id: "duplicate"}]}}), /duplicate/);
  const anchored = search({library: {entries: [reference(), {...reference(), id: "section", source_url: "https://example.org/post/1#section"}]}});
  assert.equal(anchored.references[1]!.source_url, "https://example.org/post/1#section");
});

test("unknown permissions/lifecycle remain unknown; archived/unavailable sources cannot draft", () => {
  const legacy = {...reference(), reuse_boundary: undefined, source_revision: undefined};
  const result = search({library: {entries: [legacy]}});
  assert.equal(result.references[0]!.source_revision, null);
  assert.equal(result.unknown_lifecycle_count, 1);
  assert.throws(() => draft({library: {entries: [legacy]}, reference_id: legacy.id, expected_source_revision: null, subject: "X", facts: ["Y"]}), /boundary is unknown/);
  const archived = {...reference(), lifecycle_state: "archived"};
  assert.equal(search({library: {entries: [archived]}}).references.length, 0);
  assert.throws(() => draft({library: {entries: [archived]}, reference_id: archived.id, expected_source_revision: "read:1", subject: "X", facts: ["Y"]}), /archived/);
  assert.throws(() => draft({library: {entries: []}, reference_id: legacy.id, subject: "X", facts: ["Y"]}), /unavailable/);
});

test("capture rejects unsupported raw fields, invalid counters and credentialed locators", () => {
  for (const invalid of [{...reference(), raw_body: "not a reference"}, {...reference(), style: {opening: "x", tone: "y", raw_body: "z"}}, {...reference(), source_url: "https://user:pass@example.org/post"}, {...reference(), metrics: {counts: {likes: -1}, observed_at: "2026-09-01T10:00:00Z"}}, {...reference(), metrics: {counts: {likes: 1}}}, {...reference(), captured_at: "yesterday"}]) {
    assert.throws(() => capture({library: {entries: []}, reference: invalid}));
  }
  assert.throws(() => draft({library: {entries: [reference()]}, reference_id: "source-demo", expected_source_revision: "read:1", subject: "X", facts: []}), /caller-supplied facts/);
});


test("metrics correction retains backing but replaces the observed counter set atomically", () => {
  const old = {...reference(), metrics: {observed_at: "2026-09-01T10:00:00Z", counts: {views: 100, likes: 9}, source_evidence: {ref: "evidence:original"}}};
  const library = {entries: [old]};
  const update = {...reference(), source_revision: "read:2", metrics: {observed_at: old.metrics.observed_at, counts: {views: 120}}};
  const corrected = capture({library, reference: update, expected_source_revision: "read:1"});
  assert.deepEqual((corrected.library.entries as typeof old[])[0]!.metrics, {...old.metrics, ...update.metrics});
  assert.deepEqual(corrected.reference.engagement!.counts, {views: 120});
  assert.equal(library.entries[0]!.metrics.counts.views, 100);
  const later = capture({library, reference: {...update, metrics: {...update.metrics, observed_at: "2026-09-02T10:00:00Z"}}, expected_source_revision: "read:1"});
  assert.deepEqual(later.reference.engagement, {observed_at: "2026-09-02T10:00:00Z", counts: {views: 120}});
});

test("blank filters mean all matches; malformed filters retain actionable errors", () => {
  const library = {entries: [reference()]};
  assert.equal(search({library, query: " ", structure: "\t "}).references.length, 1);
  assert.throws(() => search({library, query: "invalid\0query"}), /query/);
  assert.throws(() => search({library, query: "x".repeat(2001)}), /query/);
  assert.equal(search({library, query: "release"}).references.length, 1);
});
