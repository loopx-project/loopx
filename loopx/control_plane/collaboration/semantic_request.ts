import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireJsonObject, requireNonEmptyString } from "../runtime_decode.ts";

const ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/;

function text(value: unknown, label: string, limit: number): string {
  const result = requireNonEmptyString(value, label).trim();
  if (result.length > limit) throw new EffectRuntimeRequestError(`${label} exceeds ${limit} characters`);
  return result;
}

function exactKeys(value: JsonObject, allowed: string[], required: string[]): void {
  if (Object.keys(value).some((key) => !allowed.includes(key))
      || required.some((key) => !(key in value))) {
    throw new EffectRuntimeRequestError("collaboration request has unsupported or missing fields");
  }
}

function lines(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.length > 12) {
    throw new EffectRuntimeRequestError(`${label} must contain at most 12 items`);
  }
  return value.map((item, index) => text(item, `${label}[${index}]`, 1000));
}

function workspaceRef(value: unknown): string {
  const ref = text(value, "input.ref", 512);
  // References never fetch URLs or expose a sender's absolute filesystem path.
  if (ref.startsWith("/") || ref.includes("\\") || ref.includes(":")
      || ref.split("/").some((part) => !part || part === "." || part === "..")
      || /[\x00-\x1f]/.test(ref)) {
    throw new EffectRuntimeRequestError("input.ref must be a relative workspace file path");
  }
  return ref;
}

/** Semantic content is context, never a Todo edit, execution grant or priority. */
export function normalizeCollaborationBrief(value: unknown): JsonObject {
  const brief = requireJsonObject(value, "brief");
  if (new TextEncoder().encode(JSON.stringify(brief)).length > 16000) {
    throw new EffectRuntimeRequestError("collaboration brief exceeds 16000 bytes");
  }
  const keys = ["schema_version", "purpose", "context", "constraints", "inputs", "acceptance", "return_requirement"];
  exactKeys(brief, keys, keys);
  if (brief.schema_version !== "collaboration_brief_v0") {
    throw new EffectRuntimeRequestError("unsupported collaboration brief schema");
  }
  if (!Array.isArray(brief.inputs) || brief.inputs.length > 12) {
    throw new EffectRuntimeRequestError("brief.inputs must contain at most 12 items");
  }
  const inputs = brief.inputs.map((raw) => {
    const input = requireJsonObject(raw, "brief input");
    exactKeys(input, ["ref", "description", "sha256", "delegation"], ["ref", "description"]);
    const ref = workspaceRef(input.ref);
    const result: JsonObject = { ref, description: text(input.description, "input.description", 1000) };
    if (input.sha256 !== undefined) {
      if (typeof input.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(input.sha256)) {
        throw new EffectRuntimeRequestError("input.sha256 must be a SHA256 digest");
      }
      result.sha256 = input.sha256;
    }
    if (input.delegation !== undefined) {
      const source = requireJsonObject(input.delegation, "input.delegation");
      exactKeys(source, ["operation_id", "ref", "relation"], ["operation_id", "ref", "relation"]);
      if (typeof source.operation_id !== "string" || !ID.test(source.operation_id)
          || !["responds_to", "revises", "uses"].includes(String(source.relation)) || !result.sha256) {
        throw new EffectRuntimeRequestError("delegation input requires operation, artifact, relation and digest");
      }
      result.delegation = {...source, ref: workspaceRef(source.ref)};
    }
    return result;
  });
  const acceptance = lines(brief.acceptance, "brief.acceptance");
  if (!acceptance.length) throw new EffectRuntimeRequestError("brief.acceptance must name an observable result");
  return {
    schema_version: "collaboration_brief_v0",
    purpose: text(brief.purpose, "brief.purpose", 2000),
    context: text(brief.context, "brief.context", 6000),
    constraints: lines(brief.constraints, "brief.constraints"),
    inputs, acceptance,
    return_requirement: text(brief.return_requirement, "brief.return_requirement", 2000),
  };
}

export function normalizeCollaborationRequest(value: unknown): JsonObject {
  const request = requireJsonObject(value, "request");
  exactKeys(request, ["goal_id", "agent_id", "brief"], ["goal_id", "agent_id"]);
  const result: JsonObject = {};
  for (const key of ["goal_id", "agent_id"]) {
    const id = requireNonEmptyString(request[key], key);
    if (!ID.test(id)) throw new EffectRuntimeRequestError(`invalid collaboration ${key}`);
    result[key] = id;
  }
  if (request.brief !== undefined) result.brief = normalizeCollaborationBrief(request.brief);
  return result;
}
