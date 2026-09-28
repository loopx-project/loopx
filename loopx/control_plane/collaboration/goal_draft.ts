/** Conversation drafts are editable suggestions, never creation or execution receipts. */
export type GoalDraft = {
  objective: string;
  completion_criteria: string;
  execution_boundary: string;
  question: string;
  options: string[];
};

/** Competing operations must never also advertise creation of a new Goal.
 * Semantic relevance is the model's responsibility; this enforces output exclusivity,
 * not a keyword classifier or permission grant. Even malformed competing proposals
 * suppress the draft rather than turning a failed handoff into new work.
 */
export function admitGoalDraft(response: Record<string, unknown>): GoalDraft | null {
  if (response.context_handoff != null || response.protected_action != null || response.gate != null
    || (response.proposals != null && (!Array.isArray(response.proposals) || response.proposals.length > 0))) return null;
  return normalizeGoalDraft(response.goal_draft);
}

export function normalizeGoalDraft(value: unknown): GoalDraft | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  const fields = ["objective", "completion_criteria", "execution_boundary", "question"] as const;
  if (Object.keys(row).some(key => ![...fields, "options"].includes(key))) return null;
  if (fields.some(key => typeof row[key] !== "string" || Array.from(row[key] as string).length > 1000)) return null;
  if (!Array.isArray(row.options) || row.options.length > 5
    || row.options.some(option => typeof option !== "string" || !option.trim() || Array.from(option).length > 300)) return null;
  if (!(row.objective as string).trim()) return null;
  return {
    objective: (row.objective as string).trim(),
    completion_criteria: (row.completion_criteria as string).trim(),
    execution_boundary: (row.execution_boundary as string).trim(),
    question: (row.question as string).trim(),
    options: (row.question as string).trim() ? [...new Set(row.options.map(option => option.trim()))] : [],
  };
}
