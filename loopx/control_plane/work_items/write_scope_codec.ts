/** Lexical request/scope codecs. No lease IO, admission or authority reads. */
export const TODO_WRITE_SCOPE_MAX_CHARS = 160;

function compactString(value: unknown): string {
  if (value === null || value === undefined) return "";
  switch (typeof value) {
    case "string":
      return value;
    case "number":
    case "bigint":
    case "symbol":
      return value.toString();
    case "boolean":
      return value ? "true" : "false";
    case "function":
      return Function.prototype.toString.call(value);
    case "object":
      return Array.isArray(value) ? value.join(",") : Object.prototype.toString.call(value);
  }
  return "";
}

export function compactScopeInput(value: unknown): string {
  return compactString(value).trim().split(/\s+/u).filter(Boolean).join(" ");
}

export function characterClassEnd(pattern: string, start: number): number {
  let end = start + 1;
  if (pattern[end] === "!") end += 1;
  if (pattern[end] === "]") end += 1;
  return pattern.indexOf("]", end);
}

/**
 * Canonicalize one repository-relative scope without resolving it against a
 * host filesystem. Glob character classes retain their own slash semantics.
 */
export function canonicalWriteScope(value: string): string | null {
  const scope = value.trim();
  if (
    scope.length === 0 || scope.length > TODO_WRITE_SCOPE_MAX_CHARS ||
    scope.startsWith("/") || scope.startsWith("~") || scope.includes("\\") ||
    /[\s<>]/u.test(scope)
  ) {
    return null;
  }
  const parts: string[] = [];
  let start = 0;
  for (let index = 0; index < scope.length; index += 1) {
    if (scope[index] === "[") {
      const closing = characterClassEnd(scope, index);
      if (closing >= 0) {
        index = closing;
        continue;
      }
    }
    if (scope[index] !== "/") continue;
    parts.push(scope.slice(start, index));
    start = index + 1;
  }
  parts.push(scope.slice(start));
  const trailingSlash = scope.endsWith("/");
  const canonical: string[] = [];
  for (const part of parts) {
    if (part.length === 0 || part === ".") continue;
    if (part === "..") return null;
    canonical.push(part);
  }
  if (canonical.length === 0) return trailingSlash ? "./" : null;
  return `${canonical.join("/")}${trailingSlash ? "/" : ""}`;
}

export function writeScopeCandidates(value: unknown): unknown[] {
  if (value === null || value === undefined) return [];
  let raw: unknown[];
  if (Array.isArray(value)) {
    raw = value;
  } else if (typeof value === "string") {
    raw = value.split(/[,;|]/u);
  } else {
    raw = [value];
  }
  return raw;
}

/** Keep request spellings stable for immutable receipts and declared work. */
export function normalizeWriteScopes(value: unknown): string[] {
  const raw = writeScopeCandidates(value);
  const scopes: string[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    const scope = compactScopeInput(item);
    if (
      canonicalWriteScope(scope) === null || seen.has(scope)
    ) {
      continue;
    }
    seen.add(scope);
    scopes.push(scope);
  }
  return scopes;
}

