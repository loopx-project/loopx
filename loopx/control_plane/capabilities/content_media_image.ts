/** One synchronous image request. A lost response is not a pollable video job. */
import {createHash} from "node:crypto";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";

type Obj = Record<string, unknown>;
function fail(message: string): never {throw new EffectRuntimeRequestError(message);}
function object(value: unknown): Obj {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("Expected an image intent object");
  return value as Obj;
}
function fields(raw: Obj, keys: string[]) {if (Object.keys(raw).some(key => !keys.includes(key))) fail("Unsupported image intent fields");}
function text(value: unknown, max = 240) {
  if (typeof value !== "string" || !value.trim() || value.length > max || /[\x00-\x08\x0b-\x1f]/.test(value)) fail("Invalid image intent text");
  return value.trim();
}
function number(value: unknown, min: number, max: number) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) fail("Image value is outside the supported range");
  return value;
}
function instant(value: unknown) {
  const s = text(value, 40);
  if (!/\dT/.test(s) || !/(Z|[+-]\d\d:\d\d)$/.test(s) || !Number.isFinite(Date.parse(s))) fail("Use a timezone-aware image observation");
  return s;
}
function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([k,v])=>`${JSON.stringify(k)}:${stable(v)}`).join(",")}}`;
  return JSON.stringify(value);
}
export const IMAGE_MODEL = "doubao-seedream-5-0-pro-260628";
export function validateImagePlan(input: unknown) {
  const raw = object(object(input).plan);
  fields(raw, ["schema_version","title","model","prompt","width","height","references","watermark","quote"]);
  if (raw.schema_version !== "content_ops_image_plan_v0" || raw.model !== IMAGE_MODEL) fail("Select the supported image schema and model");
  const width = number(raw.width, 720, 2560), height = number(raw.height, 720, 2560);
  if (!Number.isInteger(width) || !Number.isInteger(height) || width * height < 921600 || width * height > 4624220 || width / height < 1/16 || width / height > 16) fail("Unsupported image dimensions");
  if (typeof raw.watermark !== "boolean") fail("Declare the image watermark choice");
  if (!Array.isArray(raw.references) || raw.references.length > 1) fail("Use text or one explicitly scoped image reference");
  const references = raw.references.map(value => {
    const ref = object(value); fields(ref,["path","sha256","source_ref","visibility"]);
    const path = text(ref.path,400), source_ref = text(ref.source_ref);
    if (path.startsWith("/") || path.includes("\\") || path.split("/").some(part => !part || part === "." || part === "..")) fail("Image references must stay inside the explicit source directory");
    if (typeof ref.sha256 !== "string" || !BARE_SHA256_PATTERN.test(ref.sha256)) fail("Image reference requires exact SHA256");
    if (ref.visibility !== "public_safe" || !/^(?:source|synthetic|generation):[a-zA-Z0-9._:-]{1,200}$/.test(source_ref)) fail("Use an explicit public-safe image and opaque source reference");
    return {path,sha256:ref.sha256,source_ref,visibility:"public_safe"};
  });
  const quote = object(raw.quote); fields(quote,["cny_per_image","checked_at","source_url","max_estimated_cny"]);
  const source_url = text(quote.source_url,400);
  if (!/^https:\/\/(?:ark|console|docs)\.volcengine\.com\//.test(source_url)) fail("Use the checked official image price source");
  const cny = number(quote.cny_per_image,.01,10), maximum = number(quote.max_estimated_cny,.01,10);
  if (cny > maximum) fail("Image estimate exceeds the declared request budget");
  const plan = {schema_version:raw.schema_version,title:text(raw.title,100),model:IMAGE_MODEL,prompt:text(raw.prompt,4000),width,height,references,watermark:raw.watermark,
    quote:{cny_per_image:cny,checked_at:instant(quote.checked_at),source_url,max_estimated_cny:maximum}};
  return {ok:true,plan,plan_digest:`sha256:${createHash("sha256").update(stable(plan)).digest("hex")}`,
    estimate:{images:1,cny,basis:"one_output_image_first_reference_free",invoice:"unknown",guaranteed_billing_cap:false},
    capability_id:"content-ops",provider_id:"loopx-media",visibility:"local_private",publish_authorized:false};
}
export function transitionImage(input: unknown) {
  const raw = object(input), state = object(raw.state), observed_at = instant(raw.observed_at);
  if (state.revision !== raw.expected_revision) fail("Image revision changed; read the current intent");
  const checked = validateImagePlan({plan:state.plan});
  if (checked.plan_digest !== state.plan_digest) fail("Image intent bytes changed");
  let status: unknown;
  if (raw.action === "submit") {
    const age = Date.parse(observed_at) - Date.parse(checked.plan.quote.checked_at);
    if (state.status !== "prepared" || age < 0 || age > 86400000) fail("Do not resubmit an image intent; check the quote and saved response");
    status = "submitting";
  } else if (raw.action === "unknown" || raw.action === "reject") {
    if (state.status !== "submitting") fail("Only an active image submission can be unresolved or rejected");
    status = raw.action === "unknown" ? "submission_unknown" : "rejected";
  } else if (raw.action === "collect") {
    if (!["submitting","submission_unknown","ready"].includes(String(state.status))) fail("No submitted image response is available");
    const result = object(raw.result), files = object(result.files), projected: Obj = {};
    if (result.plan_digest !== state.plan_digest || result.model !== IMAGE_MODEL || result.full_decode !== "passed" || result.width !== checked.plan.width || result.height !== checked.plan.height) fail("Image bytes do not match the admitted request");
    const usage = object(result.usage);
    if (usage.generated_images !== 1) fail("The single-image request did not return exactly one output");
    for (const [name,value] of Object.entries(files)) {
      if (!["generated.png","original.png"].includes(name)) fail("Unexpected image artifact");
      const file = object(value);
      if (typeof file.sha256 !== "string" || !BARE_SHA256_PATTERN.test(file.sha256)) fail("Image artifact requires SHA256");
      projected[name]={sha256:file.sha256,bytes:number(file.bytes,1,32*1024*1024)};
    }
    if (!projected["generated.png"] || !projected["original.png"]) fail("Retain both original and normalized image bytes");
    if (state.result && object(object(object(state.result).files)["original.png"]).sha256 !== object(projected["original.png"]).sha256) fail("Recovery cannot replace the original image response");
    return {ok:true,state:{...state,status:"ready",error_code:null,updated_at:observed_at,result:{files:projected,width:result.width,height:result.height,full_decode:"passed",model:IMAGE_MODEL,
      usage:{generated_images:1,output_tokens:usage.output_tokens == null ? null : number(usage.output_tokens,0,1e8)},
      watermark_requested:checked.plan.watermark,watermark_visual_check:"not_tested",creative_review:"not_tested",invoice:"unknown"}}};
  } else fail("Unsupported image transition");
  return {ok:true,state:{...state,status,updated_at:observed_at}};
}
