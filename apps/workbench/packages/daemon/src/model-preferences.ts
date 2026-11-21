/** Preferences belong to a daemon instance; an empty model key denotes its native default. */
export interface ModelSettings {
  effort: string | null;
  speed: string | null;
}
export interface DraftModelPreference extends ModelSettings {
  model: string | null;
  perModel?: Record<string, ModelSettings>;
}
export type ModelPreferencePatch = Partial<Pick<DraftModelPreference, 'model' | 'effort' | 'speed'>>;
export type DraftModelPreferences = Record<string, DraftModelPreference>;
export const EMPTY_MODEL_PREFERENCE: DraftModelPreference = { model: null, effort: null, speed: null };

const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const text = (value: unknown): string | null => typeof value === 'string' && value ? value : null;

/** Reads legacy flat preferences too. Catalog availability must never erase a saved choice. */
export function readModelPreferences(raw: unknown): DraftModelPreferences {
  if (!record(raw)) return {};
  return Object.fromEntries(Object.entries(raw).flatMap(([agent, value]) => {
    if (!agent || !record(value)) return [];
    const perModel = record(value.perModel) ? Object.fromEntries(Object.entries(value.perModel)
      .filter(([, settings]) => record(settings))
      .map(([model, settings]) => [model, { effort: text((settings as Record<string, unknown>).effort), speed: text((settings as Record<string, unknown>).speed) }])) : {};
    const choice = { model: text(value.model), effort: text(value.effort), speed: text(value.speed), perModel };
    perModel[choice.model ?? ''] = { effort: choice.effort, speed: choice.speed };
    return [[agent, choice]];
  }));
}

/** Only explicit edits update preferences. A model switch restores that model's own settings. */
export function applyModelPreference(current: DraftModelPreference | undefined, patch: ModelPreferencePatch): DraftModelPreference {
  const before = current ?? EMPTY_MODEL_PREFERENCE;
  const perModel = { ...before.perModel, [before.model ?? '']: { effort: before.effort, speed: before.speed } };
  const model = patch.model === undefined ? before.model : patch.model;
  const settings = model === before.model ? before : perModel[model ?? ''] ?? { effort: null, speed: null };
  const effort = patch.effort === undefined ? settings.effort : patch.effort;
  const speed = patch.speed === undefined ? settings.speed : patch.speed;
  return { model, effort, speed, perModel: { ...perModel, [model ?? '']: { effort, speed } } };
}
