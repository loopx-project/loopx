/** Work declaration codecs shared by public updates and Monitor successors.
 * These validate requirements, never grant capabilities or write authority. */
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { optionalNonEmptyString, requireStringArray } from "../runtime_decode.ts";
import { compactPythonWhitespace, stripPythonWhitespace } from "../coordination/todo_agents.ts";
import { normalizeWriteScopes } from "../work_items/task_lease_acquire.ts";
import { isAbsolute, resolve, sep } from "node:path";

/** Goal scope projection preserves existing relative scopes. Absolute grants
 * are projected only against the observed local Goal root, never by suffix or
 * against a different repository. This is a read projection, not a grant. */
export function projectGoalWriteScopes(value: JsonObject): JsonObject {
  const root = optionalNonEmptyString(value.project_root, "project_root");
  if (!root || !isAbsolute(root)) return { allowed_write_scopes: [] };
  const prefix = resolve(root).replaceAll(sep, "/").replace(/\/+$/, "") + "/";
  const allowed: string[] = [];
  for (const raw of requireStringArray(value.allowed_scopes ?? [], "allowed_scopes")) {
    const scope = raw.replaceAll(sep, "/");
    // Only strip the registered root. Preserve the existing relative/glob
    // semantics; the boundary guard, not this projection, checks coverage.
    const relative = isAbsolute(scope)
      ? (scope.startsWith(prefix) ? scope.slice(prefix.length) : "")
      : scope;
    if (normalizeWriteScopes([relative]).length && !allowed.includes(relative)) allowed.push(relative);
  }
  return { allowed_write_scopes: allowed };
}

function optionalText(value: unknown, label: string): string | null {
  const raw = optionalNonEmptyString(value, label);
  return raw === null ? null : stripPythonWhitespace(raw) || null;
}

// The node-independent repository/bootstrap codec remains in repository_identity.py.
// This pure transport codec is characterized against that public contract; do
// not use WHATWG's normalized pathname, which silently removes dot segments.
export function normalizeTodoRepository(value: unknown, label = "task_repository"): string | null {
  let raw = optionalText(value, label);
  if (!raw) return null;
  if (/[\\\s\u0000-\u001f\u007f]/u.test(raw)) {
    throw new EffectRuntimeRequestError(`${label} must be a credential-free Git remote without control characters or backslashes`);
  }
  let host: string, path: string;
  const canonical = /^git:([a-z0-9.-]+(?::[0-9]{1,5})?)\/([A-Za-z0-9._~+/-]+)$/.exec(raw);
  if (canonical) [host, path] = [canonical[1], canonical[2]];
  else {
    const scp = /^(?:[^@/:]+@)?([^:/]+):(.+)$/.exec(raw);
    if (scp && !raw.includes("://")) raw = `ssh://${scp[1]}/${scp[2]}`;
    try {
      const url = new URL(raw);
      if (!["git:", "http:", "https:", "ssh:"].includes(url.protocol) ||
        !url.hostname || url.password || url.search || url.hash) throw new Error();
      host = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
      const port = Number(url.port);
      const defaultPorts: Record<string, number> = {"git:": 9418, "http:": 80, "https:": 443, "ssh:": 22};
      if (url.port !== "" && port !== defaultPorts[url.protocol]) host += `:${port}`;
      const pathMatch = /^[^:]+:\/\/[^/?#]*([^?#]*)/.exec(raw);
      if (!pathMatch) throw new Error();
      path = pathMatch[1];
    } catch {
      throw new EffectRuntimeRequestError(`${label} must be a credential-free Git remote or canonical git:<host>/<path> identity`);
    }
  }
  path = path.replace(/\/+/g, "/").replace(/^\/+|\/+$/g, "").replace(/\.git$/, "");
  if (!/^[A-Za-z0-9._~+/-]+$/.test(path) || !/^[a-z0-9.-]+(?::[0-9]{1,5})?$/.test(host) ||
    path.split("/").some(part => part === "." || part === "..")) {
    throw new EffectRuntimeRequestError(`${label} must include a safe repository path`);
  }
  return `git:${host}/${path}`;
}

export function normalizeTodoCapabilities(value: unknown, label: string): string[] {
  const result: string[] = [];
  for (const raw of requireStringArray(value ?? [], label)) {
    const token = compactPythonWhitespace(raw).toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");
    if (!/^[a-z][a-z0-9_:-]{0,63}$/.test(token)) {
      throw new EffectRuntimeRequestError(`${label} must contain public-safe capability tokens; invalid entries cannot be dropped`);
    }
    if (!result.includes(token)) result.push(token);
  }
  return result;
}


export const TODO_WORK_REQUIREMENT_FIELDS = [
  "action_kind", "task_domain", "task_repository", "required_write_scopes",
  "required_capabilities", "target_capabilities", "explore_result_node_refs",
] as const;

/** Normalize only explicitly supplied edits. [] is a clear; omitted/blank
 * scalar text is unchanged. Reject invalid members, not just invalid totals. */
export function normalizeTodoWorkRequirements(intent: JsonObject): JsonObject {
  const result: JsonObject = {};
  for (const field of TODO_WORK_REQUIREMENT_FIELDS) {
    const value = intent[field];
    if (value === undefined || value === null) continue;
    if (field === "required_capabilities" || field === "target_capabilities") {
      result[field] = normalizeTodoCapabilities(value, field);
    } else if (field === "required_write_scopes") {
      const values = requireStringArray(value, field);
      const normalized = normalizeWriteScopes(values);
      if (values.some(raw => !normalizeWriteScopes([raw]).length)) {
        throw new EffectRuntimeRequestError("required_write_scopes must contain valid relative scope tokens; invalid entries cannot be dropped");
      }
      result[field] = normalized;
    } else if (field === "explore_result_node_refs") {
      const refs = [...new Set(requireStringArray(value, field).map(compactPythonWhitespace))];
      if (refs.length > 8 || refs.some(ref => !/^[A-Za-z][A-Za-z0-9_.:-]{0,95}$/.test(ref))) {
        throw new EffectRuntimeRequestError("explore_result_node_refs requires at most eight valid Explore node ids");
      }
      result[field] = refs;
    } else {
      if (typeof value !== "string") throw new EffectRuntimeRequestError(`${field} must be a string`);
      const text = stripPythonWhitespace(value);
      if (!text) continue;
      if (field === "task_repository") result[field] = normalizeTodoRepository(text);
      else {
        const normalized = text.toLowerCase();
        const pattern = field === "action_kind" ? /^[a-z][a-z0-9_-]{0,63}$/ : /^[a-z][a-z0-9_.-]{0,63}$/;
        if (!pattern.test(normalized)) throw new EffectRuntimeRequestError(`${field} must be a public-safe token`);
        result[field] = normalized;
      }
    }
  }
  return result;
}
