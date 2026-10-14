import assert from "node:assert/strict";
import test from "node:test";
import { buildGoalDirectionBaseline as build, GOAL_DIRECTION_LIMITS as limits } from "../../loopx/control_plane/goals/direction_baseline.ts";

function material(id = "design", revision = "revision-a") {
  return {id, revision, role: "direction_source", owner_status: "reviewed", conflict_rule: "owner_wins",
    boundary: "public_safe", gate_status: "ready", freshness: "current"};
}
function receipt(material_id = "design", overrides: Record<string, unknown> = {}) {
  return {schema_version: "material_usage_receipt_v0", receipt_id: `read-${material_id}`, goal_id: "goal-example",
    agent_id: "agent-a", material_id, observed_revision: "revision-a", outcome: "read", recorded_at: "2026-10-12T00:00:00Z", ...overrides};
}
function fixture() {
  return {goal_id: "goal-example", agent_id: "agent-a", generated_at: "2026-10-12T01:00:00Z",
    authority_registry: {goal_direction_requirements: [{requirement_id: "basis", topic: "architecture", purpose: "Guide the design"}],
      project_materials: {design: material(), acceptance: material("acceptance")}, topic_authority: {architecture: ["design", "acceptance"]}},
    receipts: [receipt(), receipt("acceptance")]};
}
function result(input: unknown) { const value = build(input); assert.ok(value); return value; }
function refused(input: unknown, code: string) {
  const value = result(input); assert.equal(value.direction_state, "blocked");
  assert.ok(value.reason_codes.includes(code as never), JSON.stringify(value.reason_codes)); return value;
}

// Independent semantic expectations; do not derive expected states from the reducer.
test("F1 exact own receipts produce current, an Agent-independent authority digest, and immutable inputs", () => {
  const input = fixture(), before = JSON.stringify(input); const value = result(input);
  assert.equal(value.direction_state, "current");
  assert.deepEqual(value.summary, {required_count: 2, current_count: 2, stale_count: 0, blocked_count: 0});
  assert.deepEqual(value.reason_codes, []); assert.equal(value.advisory, null);
  assert.equal(JSON.stringify(input), before);
  assert.equal(result({...input, agent_id: "agent-b"}).baseline_digest, value.baseline_digest);
  assert.equal(result({...input, generated_at: "2026-10-13T00:00:00Z"}).baseline_digest, value.baseline_digest);
  for (const outcome of ["read", "used", "verified"]) {
    assert.equal(result({...input, receipts: input.receipts.map(r => ({...r, outcome}))}).direction_state, "current");
  }
});

test("F2/F6 one exact revision mismatch produces drift, never write effects", () => {
  const input = fixture(), before = result(input);
  input.authority_registry.project_materials.design.revision = "revision-0"; // Opaque, not newer/older.
  const snapshot = JSON.stringify(input); const value = result(input);
  assert.equal(value.direction_state, "re_evaluation_required");
  assert.deepEqual(value.reason_codes, ["material_revision_changed"]);
  assert.deepEqual(value.summary, {required_count: 2, current_count: 1, stale_count: 1, blocked_count: 0});
  assert.notEqual(value.baseline_digest, before.baseline_digest);
  assert.deepEqual(value.advisory, {kind: "agent_vision_re_evaluation", creates_work: false, rewrites_vision: false, changes_goal_route: false});
  assert.equal(JSON.stringify(input), snapshot);
  assert.deepEqual(Object.keys(value).sort(), ["schema_version", "goal_id", "agent_id", "generated_at", "baseline_digest",
    "direction_state", "reason_codes", "summary", "items", "advisory", "truth_contract"].sort());
});

for (const [field, other] of [["goal_id", "goal-peer"], ["agent_id", "agent-peer"], ["material_id", "other-material"], ["todo_id", "todo-peer"]]) {
  test(`F3 a different ${field} cannot transfer consumption`, () => {
    const input = fixture(); input.receipts = [receipt("design", {[field]: other}), receipt("acceptance")];
    const value = refused(input, "material_required_unread");
    assert.equal(value.items.find(item => item.material_id === "design")?.receipt_ref, null);
  });
}
test("applicable Todo requires the exact scope and Goal-wide reads do not borrow Todo receipts", () => {
  const input = fixture();
  refused({...input, todo_id: "todo-a"}, "material_required_unread");
  assert.equal(result({...input, todo_id: "todo-a", receipts: input.receipts.map(r => ({...r, todo_id: "todo-a"}))}).direction_state, "current");
});

test("F4 full registry, nonempty topics and revisions are required", () => {
  const input = fixture(), authority = input.authority_registry;
  for (const registry of [null, {...authority, project_materials: undefined}, {...authority, topic_authority: undefined}]) {
    refused({...input, authority_registry: registry}, "authority_registry_missing");
  }
  refused({...input, authority_registry: {...authority, project_materials: {}}}, "direction_requirement_unresolved");
  input.authority_registry.project_materials.design.revision = "";
  refused(input, "authority_revision_missing");
  refused({...fixture(), authority_registry: {...authority, topic_authority: {architecture: []}}}, "authority_registry_invalid");
  refused({...fixture(), authority_registry: {...authority, topic_authority: {}}}, "direction_requirement_unresolved");
});

for (const patch of [{boundary: "private"}, {boundary: ""}, {gate_status: "blocked"}, {gate_status: "future-gate"}, {gate_status: ""}]) {
  test(`F5 exact receipts cannot override authority access ${JSON.stringify(patch)}`, () => {
    const input = fixture(); Object.assign(input.authority_registry.project_materials.design, patch);
    refused(input, "material_inaccessible");
  });
}
test("explicit available boundary is scoped to this call and never changes authority", () => {
  const input = fixture(); input.authority_registry.project_materials.design.boundary = "private";
  assert.equal(result({...input, available_boundaries: ["private"]}).direction_state, "current");
  refused(input, "material_inaccessible");
});
for (const patch of [{owner_status: "conflicted"}, {owner_status: ""}, {conflict_rule: "newest wins"}, {freshness: "stale"}, {freshness: "future"}]) {
  test(`authority ${JSON.stringify(patch)} blocks even with an exact receipt`, () => {
    const input = fixture(); Object.assign(input.authority_registry.project_materials.design, patch);
    const value = result(input); assert.equal(value.direction_state, "blocked"); assert.equal(value.advisory, null);
  });
}
test("blocked required authority dominates another revision mismatch; optional success cannot hide it", () => {
  const input = fixture(); input.authority_registry.project_materials.design.revision = "revision-b";
  input.authority_registry.project_materials.acceptance.freshness = "unknown";
  const value = refused(input, "authority_freshness_stale"); assert.equal(value.advisory, null);
});

test("F7 deterministic union preserves all bindings and ignores input ordering and identical replay", () => {
  const input = fixture(); const first = result(input);
  const reordered = {...input, receipts: [...input.receipts].reverse(), authority_registry: {...input.authority_registry,
    project_materials: Object.fromEntries(Object.entries(input.authority_registry.project_materials).reverse()),
    topic_authority: {architecture: ["acceptance", "design", "design"]},
    goal_direction_requirements: [...input.authority_registry.goal_direction_requirements, ...input.authority_registry.goal_direction_requirements]}};
  assert.deepEqual(result(reordered), first);
  assert.deepEqual(result({...input, receipts: [...input.receipts, input.receipts[0]]}), first);
  const withDirect = {...input, authority_registry: {...input.authority_registry, goal_direction_requirements: [
    ...input.authority_registry.goal_direction_requirements, {requirement_id: "direct", material_id: "design", purpose: "Direct design basis", required: false}]}};
  const combined = result(withDirect); assert.deepEqual(combined.items[1].bound_by, ["basis", "direct"]);
  assert.equal(combined.items[1].required, true);
  assert.notEqual(combined.baseline_digest, first.baseline_digest);
});

test("F8 fresh selected-Agent read quiets drift without creating or approving a route change", () => {
  const input = fixture(); input.authority_registry.project_materials.design.revision = "revision-b";
  const drifting = result(input); assert.equal(drifting.direction_state, "re_evaluation_required");
  input.receipts.push(receipt("design", {receipt_id: "new-read", observed_revision: "revision-b", recorded_at: "2026-10-12T00:30:00Z"}));
  const current = result(input); assert.equal(current.direction_state, "current"); assert.equal(current.advisory, null);
  assert.equal(current.baseline_digest, drifting.baseline_digest);
  assert.equal("path_delta" in current, false); assert.equal("vision_patch" in current, false);
});

test("later unavailable receipt defeats older passing observation", () => {
  const input = fixture(); input.receipts.push(receipt("design", {receipt_id: "unavailable", outcome: "unavailable", observed_revision: null, recorded_at: "2026-10-12T00:30:00Z"}));
  const value = refused(input, "material_inaccessible"); assert.equal(value.items[1].receipt_ref, "material_receipt:unavailable");
});
for (const patch of [{schema_version: "future"}, {outcome: "unknown"}, {observed_revision: ""}, {observed_revision: 2},
  {receipt_id: ""}, {agent_id: ""}, {recorded_at: "2026-02-30T00:00:00Z"}, {recorded_at: "2026-10-12"}, {recorded_at: "2026-10-12T24:00:00Z"}]) {
  test(`malformed receipt cannot resurrect an older pass: ${JSON.stringify(patch)}`, () => {
    const input = fixture(); input.receipts.push(receipt("design", {receipt_id: "later", recorded_at: "2026-10-12T00:30:00Z", ...patch}));
    const value = refused(input, "material_receipt_invalid"); assert.deepEqual(value.items, []); assert.equal(value.baseline_digest, null);
  });
}
test("duplicate receipt ids and tied contradictory observations refuse regardless of ordering", () => {
  for (const patch of [{observed_revision: "other"}, {receipt_id: "another", outcome: "unavailable"},
    {receipt_id: "another", observed_revision: "other"}, {receipt_id: "another", outcome: "verified"}]) {
    const input = fixture(); input.receipts.push(receipt("design", patch));
    refused(input, "material_receipt_conflict"); refused({...input, receipts: [...input.receipts].reverse()}, "material_receipt_conflict");
  }
});
test("equivalent timestamp offsets agree; sub-millisecond observations stay ordered", () => {
  const input = fixture(); input.receipts.push(receipt("design", {receipt_id: "aaa", recorded_at: "2026-10-12T01:00:00+01:00"}));
  assert.equal(result(input).items[1].receipt_ref, "material_receipt:aaa");
  input.receipts.push(receipt("design", {receipt_id: "new", observed_revision: "other", recorded_at: "2026-10-12T00:00:00.000000001Z"}));
  assert.equal(result(input).direction_state, "re_evaluation_required");
});

test("opt-out preserves legacy behavior and does not validate unrelated receipt content", () => {
  assert.equal(build({authority_registry: {}, receipts: "legacy"}), null);
  refused({...fixture(), authority_registry: {...fixture().authority_registry, goal_direction_requirements: []}}, "authority_registry_invalid");
});
test("unknown declaration fields cannot hide invalid bindings or requirement identity conflicts", () => {
  const input = fixture(), authority = input.authority_registry;
  for (const requirement of [{requirement_id: "basis", material_id: "design", topic: "architecture", purpose: "Design"},
    {requirement_id: "basis", material_id: "design", purpose: "Design", required: "false"}]) {
    refused({...input, authority_registry: {...authority, goal_direction_requirements: [requirement]}}, "authority_registry_invalid");
  }
  refused({...input, authority_registry: {...authority, goal_direction_requirements: [authority.goal_direction_requirements[0],
    {...authority.goal_direction_requirements[0], purpose: "Changed"}]}}, "authority_registry_invalid");
});

test("public projection drops arbitrary bodies/locators; private-looking projected fields refuse", () => {
  const input = fixture(); const clean = result(input);
  Object.assign(input.authority_registry.project_materials.design, {body: "synthetic-private-body", source_url: "https://example.invalid/private", private_prompt: "synthetic-prompt"});
  assert.deepEqual(result(input), clean);
  for (const revision of ["https://example.invalid/private", "/tmp/private-material", "/unregistered-mount/direction.md", ["Bearer", "synthetic-credential-123456"].join(" ")]) {
    input.authority_registry.project_materials.design.revision = revision;
    const value = refused(input, "authority_registry_invalid"); assert.equal(JSON.stringify(value).includes(revision), false);
  }
});

test("optional unread items cannot satisfy missing required material", () => {
  const input = fixture();
  const authority = {...input.authority_registry, goal_direction_requirements: [
    {requirement_id: "required", material_id: "design", purpose: "Required"},
    {requirement_id: "optional", material_id: "acceptance", purpose: "Optional", required: false}]};
  const optionalMissing = result({...input, authority_registry: authority, receipts: [receipt()]});
  assert.equal(optionalMissing.direction_state, "current"); assert.equal(optionalMissing.summary.required_count, 1);
  refused({...input, authority_registry: authority, receipts: [receipt("acceptance")]}, "material_required_unread");
});

function fanout(count: number, declarationCount = 1) {
  const input = fixture();
  const ids = Array.from({length: count}, (_, i) => `material-${i}`);
  return {...input, authority_registry: {project_materials: Object.fromEntries(ids.map(id => [id, material(id)])),
    goal_direction_requirements: Array.from({length: declarationCount}, (_, i) => ({requirement_id: `binding-${i}`, topic: "all", purpose: "Direction basis"})),
    topic_authority: {all: ids}}, receipts: ids.map(id => receipt(id))};
}
test("capacity: exact and one-over declaration/material/receipt counts, including optional fanout", () => {
  assert.equal(result(fanout(1, limits.requirements)).direction_state, "current");
  refused(fanout(1, limits.requirements + 1), "direction_capacity_exceeded");
  assert.equal(result(fanout(limits.materials)).items.length, limits.materials);
  refused(fanout(limits.materials + 1), "direction_capacity_exceeded");
  const input = fanout(1); input.receipts = Array.from({length: limits.receipts}, (_, i) => receipt("material-0", {receipt_id: `receipt-${i}`}));
  assert.equal(result(input).direction_state, "current");
  refused({...input, receipts: [...input.receipts, receipt("material-0")]}, "direction_capacity_exceeded");
  const optional = fanout(limits.materials + 1);
  refused({...optional, authority_registry: {...optional.authority_registry, goal_direction_requirements: optional.authority_registry.goal_direction_requirements.map(r => ({...r, required: false}))}}, "direction_capacity_exceeded");
});
test("complete UTF8 input byte budget counts ignored metadata, exact allowed and one-over refused", () => {
  const input = {...fixture(), ignored: ""};
  input.ignored = "x".repeat(limits.input_bytes - Buffer.byteLength(JSON.stringify(input)));
  assert.equal(Buffer.byteLength(JSON.stringify(input)), limits.input_bytes);
  assert.equal(result(input).direction_state, "current");
  refused({...input, ignored: input.ignored + "x"}, "direction_capacity_exceeded");
  refused({...input, ignored: input.ignored.slice(1) + "中"}, "direction_capacity_exceeded");
});
test("combined declaration fanout respects output budget without partial success", () => {
  const input = fanout(256, 64);
  input.authority_registry.goal_direction_requirements = input.authority_registry.goal_direction_requirements.map((r, i) => ({...r, requirement_id: `${i}-` + "x".repeat(175)}));
  assert.ok(Buffer.byteLength(JSON.stringify(input)) < limits.input_bytes);
  const value = refused(input, "direction_capacity_exceeded"); assert.deepEqual(value.items, []); assert.equal(value.baseline_digest, null);
});
test("field widths and non-ASCII revisions are measured without truncation", () => {
  const input = fixture(); input.authority_registry.project_materials.design.revision = "中".repeat(180);
  input.receipts[0].observed_revision = "中".repeat(180);
  assert.equal(result(input).direction_state, "current");
  input.authority_registry.project_materials.design.revision += "中";
  refused(input, "authority_registry_invalid");
});

test("complete output at exactly 256KiB is allowed; one byte more refuses all items", () => {
  const input = fanout(128, 64);
  const initial = result(input); assert.equal(initial.direction_state, "current");
  let extra = Math.floor((limits.output_bytes - Buffer.byteLength(JSON.stringify(initial))) / 128);
  for (const binding of input.authority_registry.goal_direction_requirements) {
    const growth = Math.min(extra, 180 - binding.requirement_id.length);
    binding.requirement_id += "x".repeat(growth); extra -= growth;
  }
  assert.equal(extra, 0);
  const remainder = limits.output_bytes - Buffer.byteLength(JSON.stringify(result(input)));
  assert.ok(remainder >= 0 && remainder < 128);
  input.agent_id += "x".repeat(remainder);
  input.receipts = input.receipts.map(r => ({...r, agent_id: input.agent_id}));
  const exact = result(input); assert.equal(exact.direction_state, "current");
  assert.equal(Buffer.byteLength(JSON.stringify(exact)), limits.output_bytes);
  input.agent_id += "x"; input.receipts = input.receipts.map(r => ({...r, agent_id: input.agent_id}));
  const over = refused(input, "direction_capacity_exceeded"); assert.deepEqual(over.items, []);
});

test("unknown/conflicting material and topic identities fail closed, including list representation", () => {
  const input = fixture(); const authority = input.authority_registry;
  assert.deepEqual(result({...input, authority_registry: {...authority, project_materials: Object.values(authority.project_materials)}}), result(input));
  for (const project_materials of [{design: {...material(), id: "other"}}, [material(), material()], {design: {...material(), source_id: "other"}}, "summary"]) {
    refused({...input, authority_registry: {...authority, project_materials}}, "authority_registry_invalid");
  }
  refused({...input, authority_registry: {...authority, topic_authority: {architecture: [{id: "design", material_id: "acceptance"}]}}}, "authority_registry_invalid");
});

test("an unknown authority boundary is not made readable by an available-boundary string", () => {
  const input = fixture(); input.authority_registry.project_materials.design.boundary = "unknown";
  refused({...input, available_boundaries: ["unknown"]}, "material_inaccessible");
});
test("a newer peer read cannot displace the selected Agent's older revision observation", () => {
  const input = fixture(); input.authority_registry.project_materials.design.revision = "revision-b";
  input.receipts.push(receipt("design", {receipt_id: "peer-current", agent_id: "agent-b", observed_revision: "revision-b", recorded_at: "2026-10-12T00:30:00Z"}));
  const value = result(input); assert.equal(value.direction_state, "re_evaluation_required");
  assert.equal(value.items[1].receipt_ref, "material_receipt:read-design");
});
test("digest changes for authority-owned binding/metadata, never for receipt or provider locator", () => {
  const input = fixture(), baseline = result(input).baseline_digest;
  assert.equal(result({...input, receipts: []}).baseline_digest, baseline);
  input.authority_registry.goal_direction_requirements[0].purpose = "Revised design basis";
  assert.notEqual(result(input).baseline_digest, baseline);
  const afterPurpose = result(input).baseline_digest;
  input.authority_registry.project_materials.design.freshness = "stale";
  assert.notEqual(result(input).baseline_digest, afterPurpose);
});
