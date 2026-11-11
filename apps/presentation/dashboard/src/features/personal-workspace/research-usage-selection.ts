import type {TrajectorySolverUsage} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js";

/** Presentation arithmetic over one already-qualified capture, not a ledger. */
export function researchUsageRows(usage: TrajectorySolverUsage) {
  const complete = usage.turns.map(turn => ({...turn, incomplete: false}));
  const partial = [...(usage.interrupted_turns ?? [])];
  if (usage.incomplete_turn) partial.push(usage.incomplete_turn);
  return [...complete, ...partial.map(turn => ({...turn, end: turn.observed_through,
    elapsed_seconds: Math.max(0, (Date.parse(turn.observed_through) - Date.parse(turn.start)) / 1000),
    incomplete: true}))]
    .sort((left, right) => left.first_line - right.first_line)
    .map((turn, index) => ({...turn, ordinal: index + 1}));
}

export function selectResearchUsage(usage: TrajectorySolverUsage, first: number, last: number) {
  const all = researchUsageRows(usage);
  if (!Number.isInteger(first) || !Number.isInteger(last) || first < 1 || last < first || last > all.length) {
    throw new Error("Select a valid captured Turn range");
  }
  const turns = all.slice(first - 1, last);
  const observed = turns.filter(turn => turn.usage !== null);
  const sum = (key: keyof NonNullable<typeof turns[number]["usage"]>) => observed.length
    ? observed.reduce((total, turn) => total + turn.usage![key], 0) : null;
  return {
    first_turn: first, last_turn: last, captured_turns: all.length,
    selected_turns: turns.length, completed_turns: turns.filter(turn => !turn.incomplete).length,
    turns_without_counters: turns.length - observed.length,
    observed_input_tokens: sum("input_tokens"), observed_cached_input_tokens: sum("cached_input_tokens"),
    observed_uncached_input_tokens: sum("uncached_input_tokens"), observed_output_tokens: sum("output_tokens"),
    // Sum observed intervals; gaps between Turns are not execution time.
    observed_turn_seconds: turns.reduce((total, turn) => total + turn.elapsed_seconds, 0),
    start: turns[0].start, observed_through: turns.at(-1)!.end, turns,
  };
}

export function exportResearchUsage(usage: TrajectorySolverUsage, first: number, last: number,
  source: {pack_id: string; goal_id: string; artifact_sha256: string; applicability: "current" | "historical"},
  exportedAt: string) {
  return JSON.stringify({
    ...source, exported_at: exportedAt, session_id: usage.session_id, method: usage.method,
    capture_start: usage.start, capture_end: usage.end,
    selection: selectResearchUsage(usage, first, last),
    limitations: [...usage.limitations,
      "Observed counters from one capture, not complete study or monetary cost. Missing counters are null, not zero.",
      "Elapsed intervals include tools and waiting. This selection does not establish avoidable cost or causal benefit.",
      "Do not add overlapping captures or separate request-reconciliation counters to these totals."],
  }, null, 2) + "\n";
}
