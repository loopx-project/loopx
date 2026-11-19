import type { UsageSample } from '../telemetry.js';

/** Resolve documented CLI selectors without claiming they are upstream response model IDs. */
export function pricingModel(sample: UsageSample, configuredModel: string | null): { model: string; assumedAlias?: string } | null {
  const model = (sample.model || configuredModel)?.trim().toLowerCase();
  if (!model) return null;
  if (sample.source === 'agy.transcript') {
    // https://www.antigravity.google/docs/cli/headless/
    if (/^gemini-3\.(6|8)-flash-(low|medium|high)$/.test(model)) return { model: model.replace(/-(low|medium|high)$/, '') };
    if (/^claude-(opus|sonnet)-4-6-thinking$/.test(model)) return { model: model.replace(/-thinking$/, '') };
  }
  // Operator-supplied routing assumptions; never present these as verified upstream model IDs.
  if (sample.source === 'app-server.tokenUsage' && (model === 'openrouter-3o' || model === 'openrouter-4o')) {
    return { model: model === 'openrouter-3o' ? 'claude-opus-4-6' : 'claude-opus-4-7', assumedAlias: model };
  }
  return { model };
}
