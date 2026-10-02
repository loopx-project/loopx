import { z } from "zod";

// Mirrors loopx/chat_activity.py. A step is what the executor did, already
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
  duration_ms: z.number().int().nonnegative().optional(),
  exit_code: z.number().int().optional(),
  count: z.number().int().nonnegative().optional(),
});

export type TurnStep = z.infer<typeof turnStepSchema>;

/** An unknown or malformed step is dropped; its legacy label still renders. */
export function parseTurnStep(value: unknown): TurnStep | null {
  const parsed = turnStepSchema.safeParse(value);
  return parsed.success ? parsed.data : null;
}

/** One row per host item: later events for the same id update it in place. */
export function mergeTurnStep(steps: readonly TurnStep[] | undefined, step: TurnStep): TurnStep[] {
  const current = steps ?? [];
  const index = current.findIndex(item => item.id === step.id);
  if (index < 0) return [...current, step].slice(-TURN_STEP_LIMIT);
  const previous = current[index];
  const merged = { ...previous, ...step, ...(step.detail || !previous.detail ? {} : { detail: previous.detail }) };
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
