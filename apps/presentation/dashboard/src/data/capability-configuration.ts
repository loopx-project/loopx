export type CapabilityConfigurationFieldDescriptor = {
  key: string;
  nullable?: boolean;
};

export type CapabilityConfigurationEditorDescriptor = {
  fields: readonly CapabilityConfigurationFieldDescriptor[];
};

function configurationObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

/**
 * Project a public capability value onto the editor-owned write surface.
 *
 * Capability read models deliberately contain provenance and derived fields.
 * Those fields are useful to operators but are not write authority.  Keeping
 * this projection next to the transport contract prevents invisible read-only
 * values from being submitted by any form consumer.
 */
export function projectEditableCapabilityConfiguration(
  editor: CapabilityConfigurationEditorDescriptor,
  value: unknown,
  fallback?: unknown,
): Record<string, unknown> {
  const primary = configurationObject(value);
  const defaults = configurationObject(fallback);
  return Object.fromEntries(editor.fields.flatMap(({ key, nullable }) => {
    const primaryValue = primary[key];
    const defaultValue = defaults[key];
    // Explicit null clears an optional value; it must not resurrect a default
    // when switching between guided and JSON editors.
    if (Object.hasOwn(primary, key) && (primaryValue != null || (nullable && primaryValue === null))) return [[key, primaryValue]];
    if (Object.hasOwn(defaults, key) && defaultValue != null) return [[key, defaultValue]];
    return [];
  }));
}

/** JSON editing has the same registered-field authority as the form. */
export function parseEditableCapabilityJson(
  editor: CapabilityConfigurationEditorDescriptor,
  text: string,
): Record<string, unknown> | null {
  let value: unknown;
  try { value = JSON.parse(text); } catch { return null; }
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const allowed = new Set(editor.fields.map((field) => field.key));
  if (Object.keys(value).some((key) => !allowed.has(key))) return null;
  return value as Record<string, unknown>;
}

/** Read the legacy cadence without reinterpreting its unit or writing it back. */
export function replanCadenceEditorValue(value: unknown): unknown {
  const record = configurationObject(value);
  if (Object.hasOwn(record, "completed_todos") && !Object.hasOwn(record, "count_unit")) {
    return { count_unit: "completed_todos", count: record.completed_todos };
  }
  return value;
}
