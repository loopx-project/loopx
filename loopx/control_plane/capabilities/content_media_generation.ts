/** Immutable cloud-footage intents inside content-ops. These states do not
 * authorize publication, retries of a paid submission, or autonomous polling. */
import {createHash} from "node:crypto";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";

type Obj = Record<string, unknown>;
function fail(message: string): never {throw new EffectRuntimeRequestError(message);}
function obj(value: unknown): Obj {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("Expected a footage object");
  return value as Obj;
}
function fields(value: Obj, names: string[]) {
  if (Object.keys(value).some(key => !names.includes(key))) fail("Unsupported footage fields");
}
function text(value: unknown, max = 240): string {
  if (typeof value !== "string" || !value.trim() || value.length > max || /[\x00-\x08\x0b-\x1f]/.test(value)) fail("Invalid footage text");
  return value.trim();
}
function num(value: unknown, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) fail("Footage number is outside its supported range");
  return value;
}
function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${JSON.stringify(k)}:${stable(v)}`).join(",")}}`;
  return JSON.stringify(value);
}
function instant(value: unknown): string {
  const s = text(value, 40);
  if (!/^\d{4}-\d\d-\d\dT/.test(s) || !/(Z|[+-]\d\d:\d\d)$/.test(s) || !Number.isFinite(Date.parse(s))) fail("Use a timezone-aware footage observation");
  return s;
}
export const GENERATION_MODEL = "doubao-seedance-2-5-260628";
// Local vocabulary, not a second generic job or publication lifecycle.
export const GENERATION_REMOTE_STATES = ["queued", "running", "succeeded", "failed", "cancelled", "expired"] as const;
export function validateGenerationPlan(input: unknown) {
  const raw = obj(obj(input).plan);
  fields(raw, ["schema_version", "title", "model", "prompt", "seconds", "resolution", "references", "watermark", "generate_audio", "return_last_frame", "quote"]);
  if (raw.schema_version !== "content_ops_generation_plan_v0" || raw.model !== GENERATION_MODEL) fail("Select the supported video model and footage schema");
  const seconds = num(raw.seconds, 4, 10);
  if (!Number.isInteger(seconds)) fail("Use whole seconds for bounded footage");
  if (raw.resolution !== "720p" && raw.resolution !== "480p") fail("Select 480p or 720p footage");
  if (typeof raw.watermark !== "boolean" || typeof raw.generate_audio !== "boolean" || typeof raw.return_last_frame !== "boolean") fail("Declare watermark, audio and last-frame choices explicitly");
  if (!Array.isArray(raw.references) || raw.references.length > 2) fail("Use a first frame and optional last frame");
  const references = raw.references.map(value => {
    const ref = obj(value);
    fields(ref, ["role", "path", "sha256", "source_ref", "visibility"]);
    if (ref.role !== "first_frame" && ref.role !== "last_frame") fail("Unsupported reference-frame role");
    const path = text(ref.path, 400);
    if (path.startsWith("/") || path.includes("\\") || path.split("/").some(part => !part || part === "." || part === "..")) fail("Reference frames must stay inside the explicit source directory");
    if (typeof ref.sha256 !== "string" || !BARE_SHA256_PATTERN.test(ref.sha256)) fail("Reference frames require exact SHA256");
    if (ref.visibility !== "public_safe") fail("Cloud reference frames require an explicit public-safe source boundary");
    const source_ref = text(ref.source_ref);
    if (!/^(?:source|synthetic|generation):[a-zA-Z0-9._:-]{1,200}$/.test(source_ref)) fail("Use an opaque source identifier rather than a private path or signed URL");
    return {role: ref.role, path, sha256: ref.sha256, source_ref, visibility: "public_safe"};
  });
  if (references.length && (references[0]!.role !== "first_frame" || (references.length === 2 && references[1]!.role !== "last_frame"))) fail("Order the first frame before the last frame");
  const quote = obj(raw.quote);
  fields(quote, ["cny_per_million_tokens", "checked_at", "source_url", "max_estimated_cny"]);
  const source = text(quote.source_url, 400);
  if (!/^https:\/\/(?:console|ark)\.volcengine\.com\//.test(source)) fail("Use the checked official price source");
  const rate = num(quote.cny_per_million_tokens, .01, 1000), checked_at = instant(quote.checked_at);
  // Only 16:9 still-frame/text inputs are supported by this bounded provider.
  // No video-input discount, flex pricing, free quota or invoice is inferred.
  const [width, height] = raw.resolution === "720p" ? [1280, 720] : [854, 480];
  const estimated_tokens = Math.ceil(seconds * width! * height! * 24 / 1024);
  const estimated_cny = Math.ceil(estimated_tokens * rate / 1e6 * 10000) / 10000;
  const maximum = num(quote.max_estimated_cny, .01, 100);
  if (estimated_cny > maximum) fail("The nominal footage estimate exceeds the declared request budget");
  const prompt = text(raw.prompt, 4000);
  // Ark interprets these prompt suffixes as weakly-validated API parameters.
  // Keep timing/dimensions/watermark bound to the admitted structured fields.
  if (/--(?:resolution|ratio|duration|frames|seed|camera_fixed|watermark)\b/i.test(prompt)) fail("Put video parameters in the structured footage plan, not prompt suffixes");
  const plan = {schema_version: raw.schema_version, title: text(raw.title, 100), model: GENERATION_MODEL,
    prompt, seconds, resolution: raw.resolution, references,
    watermark: raw.watermark, generate_audio: raw.generate_audio, return_last_frame: raw.return_last_frame,
    quote: {cny_per_million_tokens: rate, checked_at, source_url: source, max_estimated_cny: maximum}};
  return {ok: true, plan, plan_digest: `sha256:${createHash("sha256").update(stable(plan)).digest("hex")}`,
    estimate: {tokens: estimated_tokens, cny: estimated_cny, basis: "nominal_output_tokens", invoice: "unknown", guaranteed_billing_cap: false},
    capability_id: "content-ops", provider_id: "loopx-media", visibility: "local_private", publish_authorized: false};
}

export function transitionGeneration(input: unknown) {
  const request = obj(input), state = obj(request.state);
  if (state.revision !== request.expected_revision) fail("Footage revision changed; read the current draft");
  const observed_at = instant(request.observed_at), current = state.status;
  let status: unknown;
  if (request.action === "submit") {
    if (current !== "prepared") fail("This intent was already submitted; check its existing task instead of creating another");
    const checked = validateGenerationPlan({plan: state.plan});
    if (state.plan_digest !== checked.plan_digest) fail("Footage intent bytes changed");
    const age = Date.parse(observed_at) - Date.parse(checked.plan.quote.checked_at);
    if (age < 0 || age > 24 * 3600 * 1000) fail("Recheck the price before preparing a new footage intent");
    status = "submitting";
  } else if (request.action === "acknowledge") {
    const task_id = text(request.task_id, 100);
    if (current !== "submitting" || !/^cgt-[a-zA-Z0-9-]+$/.test(task_id)) fail("A task acknowledgement requires the active submission");
    return {ok: true, state: {...state, status: "submitted", task_id, updated_at: observed_at}};
  } else if (request.action === "observe") {
    const value = obj(request.observation);
    const task_id = text(value.task_id, 100);
    if (!/^cgt-[a-zA-Z0-9-]+$/.test(task_id) || (state.task_id && task_id !== state.task_id)) fail("Cloud task does not match this footage intent");
    if (value.model !== GENERATION_MODEL || !GENERATION_REMOTE_STATES.includes(value.status as typeof GENERATION_REMOTE_STATES[number])) fail("Unsupported cloud-task observation");
    if (["prepared", "ready", "rejected"].includes(String(current))) fail("This footage state cannot adopt a cloud observation");
    const usage = value.completion_tokens == null ? null : num(value.completion_tokens, 0, 1e9);
    const provider_seconds = value.duration_seconds == null ? null : num(value.duration_seconds, 0, 60);
    const previous = state.observation == null ? null : obj(state.observation);
    if (previous && previous.updated_at != null && value.updated_at != null && Number(value.updated_at) < Number(previous.updated_at)) fail("Cloud observation is older than the saved result");
    if (["succeeded", "failed", "cancelled", "expired"].includes(String(current)) && value.status !== current) fail("A terminal cloud observation cannot silently change");
    return {ok: true, state: {...state, status: value.status, task_id, updated_at: observed_at,
      observation: {status: value.status, model: GENERATION_MODEL, completion_tokens: usage, duration_seconds: provider_seconds,
        created_at: value.created_at == null ? null : num(value.created_at, 0, 1e11),
        updated_at: value.updated_at == null ? null : num(value.updated_at, 0, 1e11)}, error_code: null}};
  } else if (request.action === "unknown") {
    if (current !== "submitting") fail("Only an unconfirmed submission can become unknown");
    status = "submission_unknown";
  } else if (request.action === "reject") {
    if (current !== "submitting") fail("Only a refused submission can be rejected");
    status = "rejected";
  } else if (request.action === "repair") {
    if (current !== "ready" || request.artifacts_verified !== false) fail("Recollect only after a failed local artifact readback");
    return {ok: true, state: {...state, status: "succeeded", result: null, updated_at: observed_at}};
  } else if (request.action === "collect") {
    if (current !== "succeeded") fail("Collect only an observed successful cloud task");
    const result = obj(request.result), files = obj(result.files), projected: Obj = {};
    if (result.plan_digest !== state.plan_digest || result.task_id !== state.task_id || result.full_decode !== "passed") fail("Collected footage does not match the intent and task");
    for (const [name, raw] of Object.entries(files)) {
      if (!["generated.mp4", "poster.png", "last-frame.jpg"].includes(name)) fail("Unexpected generated artifact");
      const file = obj(raw);
      if (typeof file.sha256 !== "string" || !BARE_SHA256_PATTERN.test(file.sha256)) fail("Generated artifact requires its exact SHA256");
      projected[name] = {sha256: file.sha256, bytes: num(file.bytes, 1, 64 * 1024 * 1024)};
    }
    if (!projected["generated.mp4"] || !projected["poster.png"]) fail("Generated footage and poster are required");
    const measured_seconds = num(result.measured_seconds, 0.1, 12), frames = num(result.frames, 1, 360);
    const plan = obj(state.plan);
    if (plan.return_last_frame && !projected["last-frame.jpg"]) fail("The requested continuation frame is missing");
    const [width, height] = plan.resolution === "720p" ? [1280, 720] : [854, 480];
    if (result.width !== width || result.height !== height || result.fps !== 24 || result.has_audio !== plan.generate_audio) fail("Decoded footage does not match the admitted dimensions, rate and audio");
    if (measured_seconds < Number(plan.seconds) - .2 || measured_seconds >= Number(plan.seconds) + 1.2) fail("Decoded footage duration differs from the bounded request");
    return {ok: true, state: {...state, status: "ready", updated_at: observed_at, error_code: null,
      result: {files: projected, full_decode: "passed", measured_seconds, frames, width, height, fps: 24, has_audio: result.has_audio,
        watermark_requested: plan.watermark, watermark_visual_check: "not_tested", creative_review: "not_tested", invoice: "unknown"}}};
  } else fail("Unsupported footage transition");
  return {ok: true, state: {...state, status, updated_at: observed_at}};
}
