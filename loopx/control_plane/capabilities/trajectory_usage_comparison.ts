/** Descriptive comparison of two frozen captures. No arm inference, study
 * qualification, cross-run pooling, tokenization, billing or scientific score. */
import type {TrajectoryInput, TrajectoryLoopXInput, TrajectoryLoopXRequests, TrajectorySolverUsage} from './trajectory_assessment_types.ts';

type ComparisonProjection = Pick<TrajectorySolverUsage, 'session_id' | 'method' | 'start' | 'end' | 'request_coverage' | 'loopx_input'> & {
  loopx_requests?: Omit<Extract<TrajectoryLoopXRequests, {status: 'observed'}>, 'requests'> | Extract<TrajectoryLoopXRequests, {status: 'unavailable'}>;
};

export interface TrajectoryUsageCapture {
  pack_id: string; title: string; scope?: TrajectoryInput['scope'];
  solver_usage?: {artifact: string; artifact_sha256: string; projection: ComparisonProjection;
    applicability: 'current' | 'historical'; reason_code: string | null};
}

function values(capture: TrajectoryUsageCapture) {
  const usage = capture.solver_usage!.projection;
  const coverage = usage.request_coverage!;
  if (coverage.status !== 'verified') throw new Error('request_coverage_unavailable');
  const all = coverage.all_requests;
  const input = usage.loopx_input;
  const attributed = input && input.status !== 'unavailable' ? input : null;
  const requests = usage.loopx_requests?.status === 'observed' ? usage.loopx_requests : null;
  return {pack_id: capture.pack_id, title: capture.title,
    total: {input: all.input_tokens, cached: all.cached_input_tokens,
      uncached: all.input_tokens - all.cached_input_tokens, output: all.output_tokens, requests: coverage.unique_requests},
    input: {tokens: attributed?.loopx_input_tokens ?? null, fraction: attributed?.input_fraction ?? null,
      attributed_requests: attributed?.attributed_requests ?? 0, total_requests: coverage.unique_requests,
      /** Uncovered requests are not zero control input. */
      unattributed_requests: coverage.unique_requests - (attributed?.attributed_requests ?? 0),
      status: attributed?.status ?? 'unavailable', method: input?.method ?? null},
    commands: {input: requests?.tokens.input_tokens ?? null,
      cached: requests?.tokens.cached_input_tokens ?? null,
      uncached: requests ? requests.tokens.input_tokens - requests.tokens.cached_input_tokens : null,
      output: requests?.tokens.output_tokens ?? null, fraction: requests?.input_fraction ?? null,
      uncached_fraction: requests?.uncached_input_fraction ?? null,
      recognized_requests: requests?.recognized_requests ?? null, mixed_call_requests: requests?.mixed_call_requests ?? null,
      unclassified_calls: requests?.unclassified_calls ?? null, unmatched_calls: requests?.unmatched_calls ?? null,
      model_seconds: null, charged_cost: null},
    source: {scope: capture.scope!, artifact: capture.solver_usage!.artifact,
      sha256: capture.solver_usage!.artifact_sha256, session_id: usage.session_id,
      start: usage.start, end: usage.end, request_method: coverage.method, command_method: usage.loopx_requests?.method ?? null}};
}

function sameInputMethod(a: TrajectoryLoopXInput | undefined, b: TrajectoryLoopXInput | undefined) {
  return !!a && !!b && a.method === b.method && JSON.stringify(a.estimate_basis) === JSON.stringify(b.estimate_basis);
}

/** Compatibility selection only; this does not calculate a measurement. */
export function trajectoryUsageComparisonReason(left: TrajectoryUsageCapture, right: TrajectoryUsageCapture): string | null {
  const a = left.scope, b = right.scope, l = left.solver_usage, r = right.solver_usage;
  if (!a || !b || ['goal_id', 'case_id', 'revision', 'window'].some(k => !a[k as keyof typeof a]
    || a[k as keyof typeof a] !== b[k as keyof typeof b])) return 'capture_scope_mismatch';
  if (!l || !r || l.applicability !== 'current' || r.applicability !== 'current') return 'source_unavailable_or_changed';
  if (left.pack_id === right.pack_id || l.projection.session_id === r.projection.session_id) return 'overlapping_capture';
  if (l.projection.method !== r.projection.method) return 'usage_method_changed';
  if (l.projection.request_coverage?.status !== 'verified' || r.projection.request_coverage?.status !== 'verified') return 'request_coverage_unavailable';
  if (l.projection.request_coverage.method !== r.projection.request_coverage.method) return 'usage_method_changed';
  return null;
}

export function compareTrajectoryUsage(left: TrajectoryUsageCapture, right: TrajectoryUsageCapture) {
  const method = 'trajectory_usage_comparison_v1' as const;
  const unavailable = (reason: string) => ({method, status: 'unavailable' as const, reason});
  const reason = trajectoryUsageComparisonReason(left, right);
  if (reason) return unavailable(reason);
  const l = left.solver_usage!, r = right.solver_usage!;
  const leftValue = values(left), rightValue = values(right);
  const difference = (x: number | null, y: number | null) => x === null || y === null ? null : x - y;
  const inputComparable = sameInputMethod(l.projection.loopx_input, r.projection.loopx_input);
  const commandsComparable = !!l.projection.loopx_requests && !!r.projection.loopx_requests
    && l.projection.loopx_requests.method === r.projection.loopx_requests.method;
  const pp = (x: number | null, y: number | null) => {
    const delta = difference(x, y); return delta === null ? null : delta * 100;
  };
  return {method, status: 'observed' as const, qualification: 'descriptive_only' as const,
    left: leftValue, right: rightValue,
    delta: {total_input: leftValue.total.input - rightValue.total.input,
      cached: leftValue.total.cached - rightValue.total.cached, uncached: leftValue.total.uncached - rightValue.total.uncached,
      output: leftValue.total.output - rightValue.total.output,
      input_tokens: inputComparable && leftValue.input.status === 'complete' && rightValue.input.status === 'complete'
        ? difference(leftValue.input.tokens, rightValue.input.tokens) : null,
      input_percentage_points: inputComparable ? pp(leftValue.input.fraction, rightValue.input.fraction) : null,
      command_input: commandsComparable ? difference(leftValue.commands.input, rightValue.commands.input) : null,
      command_percentage_points: commandsComparable ? pp(leftValue.commands.fraction, rightValue.commands.fraction) : null},
    input_share_down_absolute_up: inputComparable && leftValue.input.status === 'complete' && rightValue.input.status === 'complete'
      && leftValue.input.fraction !== null && rightValue.input.fraction !== null
      ? leftValue.input.fraction < rightValue.input.fraction && leftValue.input.tokens! > rightValue.input.tokens! : null,
    command_share_down_absolute_up: commandsComparable && leftValue.commands.fraction !== null && rightValue.commands.fraction !== null
      ? leftValue.commands.fraction < rightValue.commands.fraction && leftValue.commands.input! > rightValue.commands.input! : null,
    // Fractions above are ratios of native request sums, never means of percentages.
    // Percentage point deltas are computed here as well, not by the renderer.
    input_method_comparable: inputComparable, command_method_comparable: commandsComparable};
}

export type TrajectoryUsageComparison = ReturnType<typeof compareTrajectoryUsage>;
export function projectTrajectoryUsageComparison(params: Record<string, unknown>): TrajectoryUsageComparison {
  if (!params.left || !params.right || typeof params.left !== 'object' || typeof params.right !== 'object') {
    return {method: 'trajectory_usage_comparison_v1', status: 'unavailable', reason: 'capture_required'};
  }
  return compareTrajectoryUsage(params.left as TrajectoryUsageCapture, params.right as TrajectoryUsageCapture);
}
