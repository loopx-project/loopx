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
