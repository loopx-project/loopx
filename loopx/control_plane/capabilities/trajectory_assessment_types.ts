/** Browser-safe assessment vocabulary and data shapes; no runtime I/O. */
export const TRAJECTORY_METRICS = ['goal_alignment', 'benefit_test', 'discrimination', 'information_gain', 'evidence_adoption', 'redundancy'] as const;
export type TrajectoryMetric = typeof TRAJECTORY_METRICS[number];
export type AssessmentVerdict = 'yes' | 'no' | 'unknown';
/** Analysis presentation only; never the language or identity of source evidence. */
export type TrajectoryOutputLanguage = 'zh' | 'en';
export interface TrajectorySource {
  id: string; artifact: string; artifact_sha256: string; first_line: number; text: string;
}
export interface TrajectoryEpisode {
  id: string; question: string; source_ids: string[]; eligible_metrics: TrajectoryMetric[];
}
export interface TrajectoryInput {
  schema_version: 'trajectory_assessment_input_v1';
  scope: {goal_id: string; case_id: string; run_id: string; revision: string; window: string};
  objective: string;
  selection: {rule: string; population_episodes: number | null; exhaustive: boolean};
  sources: TrajectorySource[]; episodes: TrajectoryEpisode[];
  /** Trusted curator's complete Explore history capture, already in sources.
   * Omission keeps the existing assessment contract and identity unchanged. */
  display_snapshot?: {schema_version: 'research_display_snapshot_v1'; source_id: string; source_namespace: string;
    translation_encoding?: 'source_text_aliases_v1'};
}
export interface AssessmentCitation {source_id: string; first_line: number; last_line: number}
export interface EpisodeAssessment {
  episode_id: string; metric: TrajectoryMetric; verdict: AssessmentVerdict;
  reason: string; citations: AssessmentCitation[];
}
export interface TrajectoryAssessment {
  schema_version: 'trajectory_assessment_v1'; method_version: string; analysis_key: string;
  scope: TrajectoryInput['scope']; selection: TrajectoryInput['selection'];
  evidence_manifest: Omit<TrajectorySource, 'text'>[];
  calibration: 'uncalibrated_model'; assessments: EpisodeAssessment[];
  metrics: Array<{metric: TrajectoryMetric; yes: number; no: number; unknown: number; eligible: number;
    assessed_fraction: number | null; missing_data_bounds: [number, number] | null}>;
  limitations: string[];
  output_language?: TrajectoryOutputLanguage;
  episode_questions?: Array<{episode_id: string; question: string}>;
  display?: TrajectoryDisplay;
}

/** Presentation sidecar only. Original events, statuses, references and metrics
 * remain authoritative; a translation is never a new finding or acceptance. */
export interface TrajectoryDisplayField {event_id: string; field: string; source_text: string}
export interface TrajectoryDisplay {
  schema_version: 'research_display_v1'; snapshot_key: string; source_id: string; source_namespace: string;
  language: TrajectoryOutputLanguage;
  fields: Array<TrajectoryDisplayField & {text: string}>;
  coverage: {eligible: number; translated: number; missing: number; invalid: number};
}

/** Changes between two frozen inputs; all substantive changes are reassessed. */
export interface TrajectoryContinuation {
  base_analysis_key: string; base_scope: TrajectoryInput['scope']; current_scope: TrajectoryInput['scope'];
  sources: {added: string[]; revised: string[]; removed: string[]};
  episodes: {added: string[]; revised: string[]; removed: string[]};
  can_reuse: boolean; configuration_changed: boolean; objective_changed: boolean;
  reassessed_episode_ids: string[]; new_pair_count: number; removed_pair_count: number;
  verdict_changes: Array<{episode_id: string; metric: TrajectoryMetric; before: AssessmentVerdict; after: AssessmentVerdict}>;
}

/** Provider-observed facts, separate from inferred episode judgments. */
export interface TrajectorySolverUsage {
  session_id: string; method: 'codex_complete_session_counters_v1' | 'codex_observed_session_counters_v1';
  incomplete_turn?: {turn_id: string; start: string; observed_through: string; first_line: number; last_line: number;
    counter_observations: number; usage: {input_tokens: number; cached_input_tokens: number;
      uncached_input_tokens: number; output_tokens: number} | null} | null;
  start: string; end: string; completed_turns: number; counter_observations: number;
  input_tokens: number; cached_input_tokens: number; uncached_input_tokens: number; output_tokens: number;
  elapsed_seconds: number; turn_elapsed_seconds: number;
  turns: Array<{turn_id: string; start: string; end: string; first_line: number; last_line: number;
    elapsed_seconds: number; usage: {input_tokens: number; cached_input_tokens: number;
      uncached_input_tokens: number; output_tokens: number}}>;
  limitations: string[];
  /** Supplementary reconciliation, never a replacement for the session-counter basis. */
  request_coverage?: TrajectoryRequestCoverage;
}

export interface TrajectoryRequestTokens {
  input_tokens: number; cached_input_tokens: number; cache_write_input_tokens: number;
  output_tokens: number; reasoning_output_tokens: number; total_tokens: number;
}
export type TrajectoryRequestCoverage = {
  method: 'codex_request_reconciliation_v1';
} & ({status: 'verified'; unique_requests: number; compaction_requests: number;
  /** Absent on historical saved projections; never infer a basis for those. */
  counter_basis?: 'all_requests' | 'ordinary_requests' | 'undetermined';
  duplicate_records: number; counter_observations: number; observed_through: string;
  all_requests: TrajectoryRequestTokens; compaction: TrajectoryRequestTokens;
} | {status: 'unavailable'; reason: string});
