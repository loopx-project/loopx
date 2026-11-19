import assert from "node:assert/strict";
import test from "node:test";
import {validateGenerationPlan as plan, transitionGeneration as transition, GENERATION_MODEL} from "../../loopx/control_plane/capabilities/content_media_generation.ts";

const at = "2026-10-19T10:00:00Z";
const brief = () => ({schema_version: "content_ops_generation_plan_v0", title: "Return the artifact", model: GENERATION_MODEL,
  prompt: "A single gold artifact travels back to the blue requester. No text.", seconds: 5, resolution: "720p", references: [],
  watermark: true, generate_audio: false, return_last_frame: true,
  quote: {cny_per_million_tokens: 70, checked_at: at, source_url: "https://console.volcengine.com/ark/region:cn-beijing/docs/model-pricing", max_estimated_cny: 8}});
function event(state: any, action: string, extra = {}) {return transition({state, action, expected_revision: 1, observed_at: at, ...extra}).state;}
const observation = (status: string) => ({task_id: "cgt-example-001", model: GENERATION_MODEL, status, completion_tokens: 108900, duration_seconds: 5});

test("an immutable, digest-bound draft quotes nominal cost without claiming a bill or a spending grant", () => {
  const checked = plan({plan: brief()});
  assert.equal(checked.estimate.tokens, 108000);
  assert.equal(checked.estimate.cny, 7.56);
  assert.equal(checked.estimate.guaranteed_billing_cap, false);
  assert.equal(checked.estimate.invoice, "unknown");
  assert.equal(checked.publish_authorized, false);
  assert.equal(plan({plan: checked.plan}).plan_digest, checked.plan_digest);
  assert.notEqual(plan({plan: {...brief(), prompt: "A different mechanism"}}).plan_digest, checked.plan_digest);
  assert.throws(() => plan({plan: {...brief(), quote: {...brief().quote, max_estimated_cny: 7}}}), /budget/);
  for (const changes of [{seconds: -1}, {seconds: 30}, {resolution: "1080p"}, {model: "unopened-model"}, {seed: 123}, {prompt: "hello --duration 30"}]) {
    assert.throws(() => plan({plan: {...brief(), ...changes}}));
  }
});

test("cloud input scope is explicit; first and last frames cannot become mixed or escaping references", () => {
  const frame = {role: "first_frame", path: "frame.png", sha256: "a".repeat(64), source_ref: "synthetic:scene", visibility: "public_safe"};
  assert.equal(plan({plan: {...brief(), references: [frame]}}).plan.references.length, 1);
  for (const changes of [{path: "../secret"}, {path: "/secret"}, {path: "a\\b"}, {sha256: "wrong"}, {visibility: "local_private"}, {role: "reference_image"}, {role: "last_frame"}]) {
    assert.throws(() => plan({plan: {...brief(), references: [{...frame, ...changes}]}}));
  }
});

test("submission acknowledgement is not an observed queue state; duplicates and unknowns cannot create a new paid task", () => {
  const prepared = {...plan({plan: brief()}), status: "prepared", revision: 1};
  const sending = event(prepared, "submit");
  const submitted = event(sending, "acknowledge", {task_id: "cgt-example-001"});
  assert.equal(submitted.status, "submitted");
  assert.equal(submitted.observation, undefined);
  const unknown = event(sending, "unknown");
  assert.equal(unknown.status, "submission_unknown");
  for (const state of [sending, submitted, unknown]) assert.throws(() => event(state, "submit"));
  assert.throws(() => transition({state: prepared, action: "submit", expected_revision: 0, observed_at: at}), /revision/);
  assert.throws(() => transition({state: prepared, action: "submit", expected_revision: 1, observed_at: "2026-10-21T00:00:00Z"}), /price/);
});

test("remote task identity, observed time and terminal state survive recovery without provider-private fields", () => {
  const state = event({...plan({plan: brief()}), status: "prepared", revision: 1}, "submit");
  const running = event(state, "observe", {observation: {...observation("running"), updated_at: 10, private_url: "never-project"}});
  assert.equal(JSON.stringify(running.observation).includes("never-project"), false);
  assert.throws(() => event(running, "observe", {observation: {...observation("running"), task_id: "cgt-other"}}));
  assert.throws(() => event(running, "observe", {observation: {...observation("queued"), updated_at: 9}}));
  const done = event(running, "observe", {observation: observation("succeeded")});
  assert.throws(() => event(done, "observe", {observation: observation("running")}));
});

test("local readiness requires the same task, real decoded duration and hash-bound files; it never proves creative or watermark acceptance", () => {
  const state = event(event({...plan({plan: brief()}), status: "prepared", revision: 1}, "submit"), "observe", {observation: observation("succeeded")});
  const result = {task_id: state.task_id, plan_digest: state.plan_digest, full_decode: "passed", frames: 120, measured_seconds: 5, width: 1280, height: 720, fps: 24, has_audio: false,
    files: Object.fromEntries(["generated.mp4", "poster.png", "last-frame.jpg"].map(name => [name, {sha256: "b".repeat(64), bytes: 100}]))};
  const ready = event(state, "collect", {result});
  assert.equal(ready.result.creative_review, "not_tested");
  assert.equal(ready.result.watermark_visual_check, "not_tested");
  assert.equal(ready.publish_authorized, false);
  for (const changes of [{task_id: "cgt-other"}, {plan_digest: "sha256:" + "c".repeat(64)}, {measured_seconds: 30}, {full_decode: "partial"}, {files: {...result.files, "../outside": {sha256: "a".repeat(64), bytes: 2}}}]) {
    assert.throws(() => event(state, "collect", {result: {...result, ...changes}}));
  }
});

test("recollection repairs damaged local bytes without reopening submission or relaxing the requested output", () => {
  const state = event(event({...plan({plan: brief()}), status: "prepared", revision: 1}, "submit"), "observe", {observation: observation("succeeded")});
  const result = {task_id: state.task_id, plan_digest: state.plan_digest, full_decode: "passed", frames: 120, measured_seconds: 5,
    width: 1280, height: 720, fps: 24, has_audio: false,
    files: Object.fromEntries(["generated.mp4", "poster.png", "last-frame.jpg"].map(name => [name, {sha256: "b".repeat(64), bytes: 100}]))};
  const ready = event(state, "collect", {result});
  assert.throws(() => event(ready, "repair", {artifacts_verified: true}));
  const repair = event(ready, "repair", {artifacts_verified: false});
  assert.equal(repair.status, "succeeded");
  assert.equal(repair.task_id, ready.task_id);
  assert.equal(repair.result, null);
  assert.throws(() => event(repair, "submit"));
  for (const changes of [{width: 640}, {fps: 30}, {has_audio: true}, {files: {"generated.mp4": result.files["generated.mp4"], "poster.png": result.files["poster.png"]}}]) {
    assert.throws(() => event(state, "collect", {result: {...result, ...changes}}));
  }
});
