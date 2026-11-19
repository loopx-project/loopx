import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
import {validateMediaPlan as plan, validateMediaResult as result, transitionMediaRender as transition} from "../../loopx/control_plane/capabilities/content_media.ts";

const brief = () => ({schema_version: "content_ops_media_plan_v0", title: "Return the evidence", width: 1280, height: 720, fps: 24,
  shots: [{id: "flow", kind: "diagram", seconds: 6, title: "A complete handoff", nodes: ["Intent", "Owner", "Evidence"], source_ref: "synthetic:handoff", boundary: "Mechanism illustration"}],
  assets: [], captions: [{start: 0, end: 6, text: "Return evidence to the requester.", language: "en"}], narration: {mode: "silent"}});

test("one composition digest binds story, timing, narration, source and scope", () => {
  const original = plan({plan: brief()});
  assert.equal(original.frame_count, 144);
  assert.equal(original.publish_authorized, false);
  assert.equal(original.visibility, "local_private");
  assert.equal(plan({plan: JSON.parse(JSON.stringify(brief()))}).plan_digest, original.plan_digest);
  assert.equal(plan({plan: original.plan}).plan_digest, original.plan_digest, "canonical plans must be replayable even with an empty optional body");
  for (const changed of [
    {...brief(), title: "Revised story"}, {...brief(), fps: 30},
    {...brief(), layout_template_id: "control-plane-hybrid"},
    {...brief(), captions: [{start: 0, end: 6, text: "Different claim", language: "en"}]},
    {...brief(), narration: {mode: "system_speech", voice: "Samantha"}},
    {...brief(), assets: [{id: "a", kind: "image", path: "screenshot.png", sha256: "a".repeat(64), source_ref: "synthetic:source", visibility: "local_private"}]},
  ]) assert.notEqual(plan({plan: changed}).plan_digest, original.plan_digest);
});

test("renderer readback cannot change the admitted narration, frame count or output scope", () => {
  const expected = {plan_digest: "sha256:" + "a".repeat(64), frame_count: 144, width: 1280, height: 720, fps: 24, narration_mode: "silent"};
  const output = {ok: true, protocol: "content_ops_media_renderer_v0", plan_digest: expected.plan_digest,
    files: Object.fromEntries(["video.mp4", "poster.png", "captions.vtt", "captions.srt", "transcript.txt"].map(name => [name, {sha256: "b".repeat(64), bytes: 16}])),
    qa: {full_decode: "passed", frames: 144, width: 1280, height: 720, fps: 24, source_hashes_verified: true, narration: {mode: "silent"}, creative_review: "passed"},
    cost: {render_wall_seconds: 1}, publish_authorized: false, private_path: "must-not-project"};
  const projected = result({...expected, result: output});
  assert.equal(projected.qa.creative_review, "not_tested");
  assert.equal("private_path" in projected, false);
  for (const changed of [
    {...output, publish_authorized: true},
    {...output, files: {...output.files, "../outside.mp4": {sha256: "b".repeat(64), bytes: 16}}},
    {...output, qa: {...output.qa, frames: 143}},
    {...output, qa: {...output.qa, narration: {mode: "supplied"}}},
    {...output, files: {...output.files, "video.mp4": {sha256: "invalid", bytes: 16}}},
  ]) assert.throws(() => result({...expected, result: changed}));
});

test("reject escaping assets, wrong kinds, ambiguous timing and overflow", () => {
  for (const path of ["../secret", "/etc/passwd", "a/../b", "a\\b", "a//b"]) {
    assert.throws(() => plan({plan: {...brief(), assets: [{id: "a", kind: "image", path, sha256: "a".repeat(64), source_ref: "source:1", visibility: "local_private"}]}}));
  }
  for (const invalid of [
    {...brief(), width: 1279}, {...brief(), fps: 240}, {...brief(), shots: []},
    {...brief(), shots: [{...brief().shots[0], seconds: 6.01}]},
    {...brief(), shots: [{...brief().shots[0], kind: ["diagram"]}]},
    {...brief(), assets: [{id: "a", kind: ["image"], path: "source.png", sha256: "a".repeat(64), source_ref: "synthetic:source", visibility: "local_private"}]},
    {...brief(), assets: [{id: "a", kind: "image", path: "source.png", sha256: "a".repeat(64), source_ref: "synthetic:source", visibility: ["public_safe"]}]},
    {...brief(), shots: [{...brief().shots[0], kind: "excerpt", asset_id: "missing"}]},
    {...brief(), captions: [{start: 3, end: 7, text: "Past the end", language: "en"}]},
    {...brief(), narration: {mode: "supplied", asset_id: "missing"}},
    {...brief(), publish_authorized: true},
  ]) assert.throws(() => plan({plan: invalid}));
});

test("render transitions are revision-bound and separate from publication", () => {
  const state = {status: "prepared", revision: 2, publish_authorized: false};
  const running = transition({state, expected_revision: 2, action: "start", observed_at: "2026-09-01T00:00:00Z"}).state;
  assert.equal(running.status, "rendering");
  for (const action of ["success", "failure", "cancel", "interrupt"]) {
    const result = transition({state: running, expected_revision: 2, action, observed_at: "2026-09-01T00:00:01Z"}).state;
    assert.equal(result.publish_authorized, false);
  }
  assert.throws(() => transition({state, expected_revision: 1, action: "start", observed_at: "now"}), /revision/);
  assert.throws(() => transition({state, expected_revision: 2, action: "success", observed_at: "now"}), /unavailable/);
  assert.throws(() => transition({state: running, expected_revision: 2, action: "publish", observed_at: "now"}));
});

test("caption admission preserves plain text and refuses structural paragraph separators", () => {
  const caption = (text: string) => ({...brief(), captions: [{start: 0, end: 6, text, language: "en"}]});
  const plain = "x & y\nQuoted \"literal\" text";
  assert.equal(plan({plan: caption(plain)}).plan.captions[0]?.text, plain);
  for (const text of ["FIRST LINE\n\nSECOND LINE", "FIRST LINE\n \t\nSECOND LINE"]) {
    assert.throws(() => plan({plan: caption(text)}), /blank lines/);
  }
  for (const text of ["x < y", "x > y", "<b>literal</b>", "&lt;literal&gt;", "&#60;", "&#x3c;"]) {
    assert.throws(() => plan({plan: caption(text)}), /markup and entity/);
  }
});

const spatialBrief = () => JSON.parse(readFileSync(new URL("../../packages/loopx-media/examples/blender-handoff.json", import.meta.url), "utf8"));

test("full-frame is an explicit, source-bound spatial choice; editorial defaults retain their digest", () => {
  const original = spatialBrief(), before = plan({plan: original});
  original.shots[1].framing = "editorial";
  assert.deepEqual(plan({plan: original}), before);
  original.shots[1].framing = "full_frame";
  const framed = plan({plan: original});
  assert.notEqual(framed.plan_digest, before.plan_digest);
  assert.equal(framed.plan.shots[1]?.framing, "full_frame");
  assert.equal(plan({plan: framed.plan}).plan_digest, framed.plan_digest);
  for (const bad of ["cover", null, ["full_frame"], true]) {
    original.shots[1].framing = bad;
    assert.throws(() => plan({plan: original}), /framing/);
  }
  const diagram = {...brief(), shots: [{...brief().shots[0], framing: "full_frame"}]};
  assert.throws(() => plan({plan: diagram}), /requires an excerpt or Blender/);
});

test("an older provider cannot silently ignore full-frame framing and return ready", () => {
  const expected = {plan_digest: "sha256:" + "a".repeat(64), frame_count: 12, width: 640, height: 360, fps: 12, narration_mode: "silent"};
  const output = {ok: true, protocol: "content_ops_media_renderer_v0", plan_digest: expected.plan_digest,
    files: Object.fromEntries(["video.mp4", "poster.png", "captions.vtt", "captions.srt", "transcript.txt"].map(name => [name, {sha256: "b".repeat(64), bytes: 16}])),
    qa: {full_decode: "passed", frames: 12, width: 640, height: 360, fps: 12, source_hashes_verified: true, narration: {mode: "silent"}},
    cost: {render_wall_seconds: 1}, publish_authorized: false};
  assert.equal("full_frame_shots" in result({...expected, result: output}).qa, false);
  assert.throws(() => result({...expected, full_frame_shots: ["view"], result: output}), /upgrade/);
  for (const ids of [["other"], ["view", "view"], []]) {
    assert.throws(() => result({...expected, full_frame_shots: ["view"], result: {...output, qa: {...output.qa, full_frame_shots: ids}}}), /full-frame/);
  }
  const revised = {...output, qa: {...output.qa, full_frame_shots: ["view"]}};
  assert.deepEqual(result({...expected, full_frame_shots: ["view"], result: revised}).qa.full_frame_shots, ["view"]);
  assert.throws(() => result({...expected, result: revised}), /full-frame/);
});

test("spatial shots refuse body text instead of producing a movie that loses a qualifier", () => {
  const excerpt = {...brief(), assets: [{id: "still", kind: "image", path: "source.png", sha256: "a".repeat(64), source_ref: "synthetic:still", visibility: "local_private"}],
    shots: [{id: "view", kind: "excerpt", seconds: 6, title: "Source", asset_id: "still", source_ref: "synthetic:still", boundary: "Illustration"}]};
  assert.equal(plan({plan: excerpt}).plan.shots[0]?.body, "");
  assert.throws(() => plan({plan: {...excerpt, shots: [{...excerpt.shots[0], body: "IMPORTANT SYNTHETIC QUALIFIER"}]}}), /do not support body text/);
  const blender = spatialBrief(); blender.shots[1].body = "IMPORTANT SYNTHETIC QUALIFIER";
  assert.throws(() => plan({plan: blender}), /do not support body text/);
  const title = brief(); title.shots[0] = {...title.shots[0], kind: "title", body: "A supported visible qualifier"};
  assert.equal(plan({plan: title}).plan.shots[0]?.body, "A supported visible qualifier");
});

test("Blender scene geometry and camera belong to the same source-bound composition", () => {
  const raw = spatialBrief(), checked = plan({plan: raw});
  assert.equal(checked.frame_count, 60);
  assert.equal(checked.publish_authorized, false);
  assert.equal(plan({plan: checked.plan}).plan_digest, checked.plan_digest);
  for (const change of [(p: any) => p.shots[1].scene.camera.end_position[0] += 1,
    (p: any) => p.shots[1].scene.objects[3].keyframes[2].position[2] += .2,
    (p: any) => p.shots[1].scene.lights[0].energy += 1]) {
    const revised = spatialBrief(); change(revised);
    assert.notEqual(plan({plan: revised}).plan_digest, checked.plan_digest);
  }
  // The old scene-free plan stays scene-free; no default geometry/provider setting.
  assert.equal("scene" in plan({plan: brief()}).plan.shots[0]!, false);
});

test("Blender refuses executable input, escaping IDs and undeclared animation", () => {
  const changes = [(p: any) => p.shots[1].scene.script = "arbitrary Python",
    (p: any) => p.shots[1].scene.blend_path = "outside.blend",
    (p: any) => p.shots[1].id = "../escape",
    (p: any) => p.shots[1].scene.objects[0].shape = "script",
    (p: any) => p.shots[1].scene.objects[0].color = [0, 1, 2],
    (p: any) => p.shots[1].scene.objects[3].keyframes[0].frame = 2,
    (p: any) => p.shots[1].scene.objects[3].keyframes[2].frame = 10,
    (p: any) => p.shots[1].scene.objects[3].keyframes.at(-1).position[0] = Infinity,
    (p: any) => p.shots[1].scene.camera.position = p.shots[1].scene.camera.target,
    (p: any) => p.shots[1].scene.connections[0].to = "unknown",
    (p: any) => p.shots[1].scene.lights = [],
    (p: any) => p.shots[1].seconds = 30];
  for (const change of changes) {const p = spatialBrief(); change(p); assert.throws(() => plan({plan: p}));}
});

test("Blender readiness requires the admitted passes and a reopened project, not only MP4 bytes", () => {
  const expected = {plan_digest: "sha256:" + "a".repeat(64), frame_count: 60, width: 640, height: 360, fps: 12,
    narration_mode: "silent", blender_shots: [{id: "route", frames: 36}]};
  const scene = {id: "route", frames: 36, rgba_frames: 36, depth_frames: 36,
    project_reopened: true, engine: "CYCLES", blender_version: "4.5.12 LTS"};
  const output = {ok: true, protocol: "content_ops_media_renderer_v0", plan_digest: expected.plan_digest,
    files: Object.fromEntries(["video.mp4", "poster.png", "captions.vtt", "captions.srt", "transcript.txt", "blender-project.zip"].map(name => [name, {sha256: "b".repeat(64), bytes: 16}])),
    qa: {full_decode: "passed", frames: 60, width: 640, height: 360, fps: 12, source_hashes_verified: true,
      narration: {mode: "silent"}, blender_scenes: [scene]}, cost: {render_wall_seconds: 45}, publish_authorized: false};
  assert.equal(result({...expected, result: output}).qa.blender_scenes?.[0]?.project_reopened, true);
  for (const changed of [{...scene, frames: 35}, {...scene, depth_frames: 35}, {...scene, id: "other"},
    {...scene, project_reopened: false}, {...scene, blender_version: "5.0.0"}]) {
    assert.throws(() => result({...expected, result: {...output, qa: {...output.qa, blender_scenes: [changed]}}}));
  }
  assert.throws(() => result({...expected, result: {...output, files: {...output.files, "blender-project.zip": undefined}}}));
  assert.throws(() => result({...expected, blender_shots: [], result: output}), /scope/);
});
