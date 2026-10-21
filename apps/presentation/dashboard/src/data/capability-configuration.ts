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

/** A saved model override belongs to the Runtime that was selected with it. */
export function changeStewardExecutorDraft(
  current: Record<string, unknown>, key: string, value: unknown,
): Record<string, unknown> {
  const changed = { ...current, [key]: value };
  return key === "executor_endpoint" && value !== current.executor_endpoint
    ? { ...changed, executor_model: null, executor_reasoning_effort: null }
    : changed;
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
    return { count_unit: "completed_todos", count: record.completed_todos, stride_strategy: "off" };
  }
  return { ...record, stride_strategy: record.stride_strategy ?? "off" };
}

/** Fill omitted review interval fields from the currently effective value. */
export function replanCadenceEditorDefaults(effective: unknown, fallback: unknown): Record<string, unknown> {
  const defaults = { ...configurationObject(fallback) };
  const live = configurationObject(replanCadenceEditorValue(effective));
  if (Object.hasOwn(live, "count_unit")) defaults.count_unit = live.count_unit;
  if (Object.hasOwn(live, "count")) defaults.count = live.count;
  if (Object.hasOwn(live, "planning_horizon_goal")) defaults.planning_horizon_goal = live.planning_horizon_goal;
  // Strategy omission keeps the existing default-off meaning.
  return defaults;
}

/** Show the live interval when a Goal stores only its strategy choice. */
export function goalReplanCadenceEditorValue(current: unknown, effective: unknown): Record<string, unknown> {
  const inherited = configurationObject(replanCadenceEditorValue(effective));
  return current == null ? inherited : {
    ...inherited,
    ...configurationObject(replanCadenceEditorValue(current)),
  };
}

/** Persisted or explicit JSON interval fields represent a Goal override. */
export function hasReplanCadenceOverride(value: unknown): boolean {
  const record = configurationObject(value);
  return ["count_unit", "count", "completed_todos"].some((key) => Object.hasOwn(record, key));
}

/** Displaying an inherited interval must not turn it into a pinned write. */
export function goalReplanCadenceWriteValue(
  draft: Record<string, unknown>,
  cadenceOverride: boolean,
  horizonOverride = Object.hasOwn(draft, "planning_horizon_goal"),
): Record<string, unknown> {
  const value = { ...draft };
  if (!cadenceOverride) {
    delete value.count_unit;
    delete value.count;
  }
  if (!horizonOverride) delete value.planning_horizon_goal;
  return value;
}

/** A v0 storage selector did not enable creation; opening Settings must not. */
export function goalStorageEditorValue(value: unknown): unknown {
  const record = configurationObject(value);
  return record.schema_version === "loopx_goal_storage_defaults_v0"
    ? { ...record, canonical_creation: false }
    : value;
}
