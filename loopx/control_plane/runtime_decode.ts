import { EffectRuntimeRequestError } from "./effect_runtime_errors.ts";

export type JsonObject = Record<string, unknown>;

export function jsonObject(value: unknown): JsonObject | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

export function requireJsonObject(value: unknown, label: string): JsonObject {
  const result = jsonObject(value);
  if (!result) throw new EffectRuntimeRequestError(`${label} must be an object`);
  return result;
}

/** Sparse IO spellings, validated against the already decoded source.
 * Shared by context identity and durable journal publication; no carrier,
 * authority or projection decision is made here. */
export function sourceNumberTokens(source: JsonObject, supplied?: unknown):
    {path: (string | number)[]; token: string}[] {
  if (supplied === undefined) return [];
  if (!Array.isArray(supplied)) throw new EffectRuntimeRequestError("context source number tokens must be an array");
  const tokens: {path: (string | number)[]; token: string}[] = [], seen = new Set<string>();
  for (const entry of supplied) {
    if (!Array.isArray(entry) || entry.length !== 2 || !Array.isArray(entry[0]) || !entry[0].length ||
        typeof entry[1] !== "string" || !/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(entry[1])) {
      throw new EffectRuntimeRequestError("invalid context source number token");
    }
    const path: (string | number)[] = entry[0], token: string = entry[1];
    let value: unknown = source;
    for (const key of path) {
      if (value === null || typeof value !== "object" ||
          !(Array.isArray(value) ? typeof key === "number" && Number.isSafeInteger(key) && key >= 0
            : typeof key === "string") || !Object.hasOwn(value, key)) {
        throw new EffectRuntimeRequestError("context source number token path is unavailable");
      }
      value = (value as Record<string | number, unknown>)[key];
    }
    const key = JSON.stringify(path);
    if (seen.has(key) || typeof value !== "number" || !Number.isFinite(value) || !Object.is(JSON.parse(token), value)) {
      throw new EffectRuntimeRequestError("context source number token differs from input");
    }
    seen.add(key);
    tokens.push({path, token});
  }
  return tokens;
}

export function requireBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new EffectRuntimeRequestError(`${label} must be a boolean`);
  }
  return value;
}

export function requireInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new EffectRuntimeRequestError(`${label} must be an integer`);
  }
  return value;
}

export function requireNonEmptyString(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new EffectRuntimeRequestError(`${label} must be a non-empty string`);
  }
  return value;
}

export function optionalNonEmptyString(
  value: unknown,
  label: string,
): string | null {
  if (value === null || value === undefined || value === "") return null;
  return requireNonEmptyString(value, label);
}

export function requireStringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new EffectRuntimeRequestError(`${label} must be an array of strings`);
  }
  return [...value];
}

export function isStringLiteral<const Values extends readonly string[]>(
  value: string,
  allowed: Values,
): value is Values[number] {
  return allowed.some((candidate) => candidate === value);
}

export function requireStringLiteral<const Values extends readonly string[]>(
  value: unknown,
  allowed: Values,
  label: string,
  unsupportedMessage = `${label} is unsupported`,
): Values[number] {
  const candidate = requireNonEmptyString(value, label);
  if (!isStringLiteral(candidate, allowed)) {
    throw new EffectRuntimeRequestError(unsupportedMessage);
  }
  return candidate;
}

export function assertNever(value: never, message: string): never {
  void value;
  throw new Error(message);
}
