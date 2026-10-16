/** Browser-safe assessment vocabulary and data shapes; no runtime I/O. */
export const TRAJECTORY_METRICS = ['goal_alignment', 'benefit_test', 'discrimination', 'information_gain', 'evidence_adoption', 'redundancy'] as const;
export type TrajectoryMetric = typeof TRAJECTORY_METRICS[number];
export type AssessmentVerdict = 'yes' | 'no' | 'unknown';
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
}
