/** Version comparison for the existing observer; never a scientific score. */
import {EffectRuntimeRequestError} from '../effect_runtime_errors.ts';
import {prepareTrajectoryAssessment, qualifyTrajectoryAssessment} from './trajectory_assessment.ts';
import type {TrajectoryInput, TrajectoryContinuation, EpisodeAssessment} from './trajectory_assessment_types.ts';

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const ordered = <T extends {id: string}>(items: T[]) => [...items].sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
const episodeContent = (e: TrajectoryInput['episodes'][number]) => ({...e,
  source_ids: [...e.source_ids].sort(), eligible_metrics: [...e.eligible_metrics].sort()});
const sourceContent = ({artifact_sha256: _hash, ...source}: TrajectoryInput['sources'][number]) => source;
const evidenceContent = (input: TrajectoryInput) => ({scope: {...input.scope, revision: undefined},
  objective: input.objective, selection: input.selection,
  sources: ordered(input.sources).map(sourceContent), episodes: ordered(input.episodes).map(episodeContent),
  ...(input.display_snapshot ? {display_snapshot: input.display_snapshot} : {})});
function differences<T extends {id: string}>(before: T[], after: T[], content: (item: T) => unknown) {
  const old = new Map(before.map(item => [item.id, item]));
  const current = new Map(after.map(item => [item.id, item]));
  return {added: after.filter(item => !old.has(item.id)).map(item => item.id),
    revised: after.filter(item => old.has(item.id) && !same(content(old.get(item.id)!), content(item))).map(item => item.id),
    removed: before.filter(item => !current.has(item.id)).map(item => item.id)};
}
const pair = (a: EpisodeAssessment) => `${a.episode_id}\0${a.metric}`;

export function compareTrajectoryAssessments(params: Record<string, unknown>): TrajectoryContinuation {
  const current = prepareTrajectoryAssessment(params);
  if (params.analysis_key !== undefined && params.analysis_key !== current.analysis_key) throw new EffectRuntimeRequestError('analysis input/config key mismatch');
  const raw = params.previous;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new EffectRuntimeRequestError('previous analysis is required');
  const previous = raw as Record<string, unknown>;
  const base = prepareTrajectoryAssessment(previous);
  const oldResult = previous.output === undefined ? null : qualifyTrajectoryAssessment(previous);
  for (const key of ['goal_id', 'case_id', 'run_id'] as const) {
    if (current.input.scope[key] !== base.input.scope[key]) throw new EffectRuntimeRequestError('continuation belongs to a different Goal/case/run');
  }
  const sources = differences(base.input.sources, current.input.sources, sourceContent);
  const episodes = differences(base.input.episodes, current.input.episodes, episodeContent);
  // Excerpt identity includes path, source id, line range and literal text.
  // A capture's other bytes/revision alone do not establish new information.
  // Scope/window, selection, objective, eligibility or configuration changes
  // require full reassessment. No prose heuristic certifies an unaffected row.
  const can_reuse = oldResult !== null && same(base.config, current.config) && same(evidenceContent(base.input), evidenceContent(current.input));
  const oldPairs = new Map((oldResult?.assessments ?? []).map(a => [pair(a), a]));
  const eligibleBefore = new Set(base.input.episodes.flatMap(e => e.eligible_metrics.map(metric => `${e.id}\0${metric}`)));
  const newPairs = new Set(current.input.episodes.flatMap(e => e.eligible_metrics.map(metric => `${e.id}\0${metric}`)));
  const result = params.output === undefined ? null : qualifyTrajectoryAssessment({...params, analysis_key: current.analysis_key});
  return {base_analysis_key: base.analysis_key, base_scope: base.input.scope,
    current_scope: current.input.scope, sources, episodes, can_reuse,
    configuration_changed: !same(base.config, current.config), objective_changed: base.input.objective !== current.input.objective,
    reassessed_episode_ids: can_reuse ? [] : current.input.episodes.map(e => e.id),
    new_pair_count: [...newPairs].filter(key => !eligibleBefore.has(key)).length,
    removed_pair_count: [...eligibleBefore].filter(key => !newPairs.has(key)).length,
    verdict_changes: (result?.assessments ?? []).flatMap(a => {
      const old = oldPairs.get(pair(a));
      return old && old.verdict !== a.verdict ? [{episode_id: a.episode_id, metric: a.metric, before: old.verdict, after: a.verdict}] : [];
    })};
}
