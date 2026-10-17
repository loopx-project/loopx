import { z } from "zod";

// Mirrors loopx/presentation/codex_activity.py. A step is what the executor did, already
// redacted by the adapter; "completed" means the host item ended, not that a
// check passed.
export const TURN_STEP_KINDS = ["reasoning", "command", "tool", "search", "file_change"] as const;
export const TURN_STEP_STATES = ["running", "completed", "failed"] as const;
export const TURN_COMMAND_VERBS = ["read", "search", "list", "run"] as const;
const TURN_STEP_LIMIT = 80;

const turnStepSchema = z.object({
  id: z.string().min(1).max(160),
  kind: z.enum(TURN_STEP_KINDS),
  state: z.enum(TURN_STEP_STATES),
  title: z.string().max(200),
  verb: z.enum(TURN_COMMAND_VERBS).optional(),
  detail: z.string().max(4200).optional(),
  reasoning_summary: z.literal(true).optional(),
  duration_ms: z.number().int().nonnegative().optional(),
  exit_code: z.number().int().optional(),
  count: z.number().int().nonnegative().optional(),
});

export type TurnStep = z.infer<typeof turnStepSchema>;

/** An unknown or malformed step is dropped; its legacy label still renders. */
export function parseTurnStep(value: unknown): TurnStep | null {
  const parsed = turnStepSchema.safeParse(value);
  if (!parsed.success) return null;
  const step = parsed.data;
  // Older events mixed raw reasoning and summaries. Keep their timing, but
  // never present unqualified text as a public summary on historical replay.
  if (step.kind === "reasoning" && !step.reasoning_summary) {
    const { detail: _detail, ...safe } = step;
    return { ...safe, title: "" };
  }
  return step;
}

/** One row per host item: later events for the same id update it in place. */
export function mergeTurnStep(steps: readonly TurnStep[] | undefined, step: TurnStep): TurnStep[] {
  const current = steps ?? [];
  const index = current.findIndex(item => item.id === step.id);
  if (index < 0) return [...current, step].slice(-TURN_STEP_LIMIT);
  const previous = current[index];
  // A legacy unqualified update may not inherit a public-summary marker, nor
  // may a later summary promote unqualified text retained by an older client.
  if (step.kind === "reasoning" && !step.reasoning_summary) {
    return current.map((item, position) => position === index ? step : item);
  }
  const previousDetail = previous.kind !== "reasoning" || previous.reasoning_summary ? previous.detail : undefined;
  const merged = { ...previous, ...step, detail: step.detail || previousDetail };
  return current.map((item, position) => position === index ? merged : item);
}

export function withTurnActivity<M extends { activity?: string[]; steps?: TurnStep[]; updatedAt?: number }>(
  message: M, label: string, step: TurnStep | null, now: number,
): M {
  return {
    ...message,
    updatedAt: now,
    activity: message.activity?.at(-1) === label ? message.activity : [...(message.activity ?? []), label].slice(-6),
    ...(step ? { steps: mergeTurnStep(message.steps, step) } : {}),
  };
}
