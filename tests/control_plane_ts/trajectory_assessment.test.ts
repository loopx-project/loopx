import assert from 'node:assert/strict';
import test from 'node:test';
import {prepareTrajectoryAssessment, qualifyTrajectoryAssessment, type TrajectoryInput} from '../../loopx/control_plane/capabilities/trajectory_assessment.ts';
import {compareTrajectoryAssessments} from '../../loopx/control_plane/capabilities/trajectory_assessment_continuation.ts';
import {createHash, webcrypto} from 'node:crypto';
import {parseExploreHistoryPage, projectExploreHistory} from '../../loopx/control_plane/presentation/explore_history.ts';
import {trajectoryDisplaySnapshotText, projectTrajectoryDisplay, maskTrajectoryDisplayText} from '../../loopx/control_plane/capabilities/trajectory_assessment_display.ts';
const input = (): TrajectoryInput => ({schema_version: 'trajectory_assessment_input_v1',
  scope: {goal_id:'goal', case_id:'case', run_id:'run', revision:'rev1', window:'0-1'}, objective:'Optimize native utility',
  selection:{rule:'all completed episodes in the frozen window',population_episodes:2,exhaustive:true},
  sources:[{id:'s',artifact:'measurement.json',artifact_sha256:'a'.repeat(64),first_line:10,text:'negative measurement\nnext action'}],
  episodes:[{id:'e1',question:'Does A help?',source_ids:['s'],eligible_metrics:['information_gain','evidence_adoption']},
    {id:'e2',question:'Does B help?',source_ids:['s'],eligible_metrics:['information_gain']}]});
const config = {model:'example-model',effort:'max',service_tier:'fast'};
const output = () => ({assessments:[
  {episode_id:'e1',metric:'information_gain',verdict:'yes',reason:'A was ruled out',citations:[{source_id:'s',first_line:10,last_line:10}]},
  {episode_id:'e1',metric:'evidence_adoption',verdict:'no',reason:'Action contradicts measured evidence',citations:[{source_id:'s',first_line:10,last_line:11}]},
  {episode_id:'e2',metric:'information_gain',verdict:'unknown',reason:'No valid measurement',citations:[]}],limitations:['Synthetic contract test, not evaluator calibration']});
const prepared = () => prepareTrajectoryAssessment({input:input(), config});

const displayInput = () => {
  const x = input();
  const event = {schema_version: 'loopx_explore_result_event_v0', goal_id: 'goal', recorded_at: '2026-01-01T00:00:00Z',
    status: 'tentative', evidence_refs: ['source-sha256:abc'], run_id: 'run', confidence: null};
  const page = {ok: true, goal_id: 'goal', total: 3, next_cursor: null, items: [
    {...event, event_id: 'q-old', result_id: 'question', event_kind: 'node', node_kind: 'question', title: 'Does V18 improve -6.2e-3ms?', summary: 'Not yet established.'},
    {...event, event_id: 'finding', result_id: 'finding', event_kind: 'finding', node_id: 'question', title: 'Negative evidence, not acceptance', status: 'refuted',
      summary: 'Input revision: sha256:abc Applicability: Fixed source only Observation: Gain was -6.2e-3ms, not +2ms Interpretation: Outcome: replan retained: - V18 changed: - Check `candidate.cpp` stopped: - Do not claim 100% success'},
    {...event, event_id: 'q-new', result_id: 'question', event_kind: 'node', node_kind: 'question', title: 'Does V19 help?', summary: 'Still unknown; do not infer adoption.', blocked_reason: 'The original source is unavailable.'},
  ]};
  const text = JSON.stringify(page);
  x.sources.push({id: 'history', artifact: 'history.json', artifact_sha256: createHash('sha256').update(text).digest('hex'), first_line: 1, text});
  x.display_snapshot = {schema_version: 'research_display_snapshot_v1', source_id: 'history', source_namespace: 'source-a'};
  return x;
};
const displayPrepared = () => prepareTrajectoryAssessment({input: displayInput(), config: {...config, output_language: 'zh'}});
const translatedOutput = (p = displayPrepared()) => ({...output(), episode_questions: [{episode_id: 'e1', question: 'A 是否有帮助？'}, {episode_id: 'e2', question: 'B 是否有帮助？'}],
  display_translations: JSON.parse(p.prompt.split('\n\ndisplay_fields:\n')[1]!).map((row: {field_id: string; text: string}) => ({...row, text: `【译文】${row.text}`}))});

test('whole-history translation includes old revisions and decisions; versions, statuses and citations remain source facts', async () => {
  const p = displayPrepared(), result = qualifyTrajectoryAssessment({...p, output: translatedOutput(p)});
  const original = parseExploreHistoryPage(JSON.parse(p.input.sources.at(-1)!.text), 'goal').items;
  const key = createHash('sha256').update(trajectoryDisplaySnapshotText(original, 'goal', 'source-a')).digest('hex');
  assert.equal(key, Buffer.from(await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode(trajectoryDisplaySnapshotText(original, 'goal', 'source-a')))).toString('hex'));
  assert.equal(result.display!.snapshot_key, key);
  const fields = result.display!.fields;
  for (const pair of [['q-old', 'title'], ['q-old', 'summary'], ['finding', 'observation'], ['finding', 'applicability'], ['finding', 'retained.0'], ['finding', 'changed.0'], ['finding', 'stopped.0'], ['q-new', 'blocked_reason']]) {
    assert(fields.some(row => row.event_id === pair[0] && row.field === pair[1]), `Missing original narrative ${pair}`);
  }
  const projected = projectTrajectoryDisplay(original, result.display, key, 'zh'), shown = projected.events;
  assert.match(shown[0]!.title, /^【译文】Does V18 improve -6.2e-3ms\?$/);
  const decision = projected.sections.get('finding')!;
  assert.equal(decision.inputRevision, 'sha256:abc'); assert.equal(decision.decision!.outcome, 'replan');
  assert.deepEqual(decision.decision!.changed, ['【译文】Check `candidate.cpp`']);
  assert.match(decision.observation, /-6.2e-3ms, not \+2ms/);
  for (let i = 0; i < original.length; i++) {
    const {title: _t, summary: _s, blocked_reason: _b, ...facts} = shown[i]!;
    const {title: _ot, summary: _os, blocked_reason: _ob, ...oldFacts} = original[i]!;
    assert.deepEqual(facts, oldFacts);
  }
  assert.deepEqual(projectExploreHistory(shown, 'goal').nodes[0]!.revisions.map(e => e.event_id), ['q-old', 'q-new']);
  assert.deepEqual(result.metrics, qualifyTrajectoryAssessment({...prepared(), output: output()}).metrics);
  assert.equal(result.display!.coverage.missing, 0);
  assert.match(p.prompt, /never a replacement for the original/);
});

test('missing, duplicate or numeric-corrupt translations fall back per field without laundering the assessment', () => {
  const p = displayPrepared(), raw = translatedOutput(p);
  const first = raw.display_translations.shift()!;
  const corrupted = raw.display_translations.find((row: {text: string}) => row.text.includes('⟪LX'))!;
  corrupted.text = corrupted.text.replace(/⟪LX\d+⟫/, '999');
  raw.display_translations.push({...raw.display_translations[1]});
  const result = qualifyTrajectoryAssessment({...p, output: raw});
  assert(result.display!.coverage.missing >= 3); assert(result.display!.coverage.invalid >= 2);
  assert.equal(result.assessments.length, 3);
  assert(!result.display!.fields.some(row => row.event_id === 'q-old' && row.field === 'title'));
  const missing = qualifyTrajectoryAssessment({...p, output: {...raw, display_translations: []}}).display!;
  assert.equal(missing.coverage.translated, 0); assert.equal(missing.coverage.missing, missing.coverage.eligible);
  assert(first.field_id.length === 64);
});

test('wrong language/source/snapshot and source corrections cannot borrow a translation; incomplete capture is refused', () => {
  const p = displayPrepared(), result = qualifyTrajectoryAssessment({...p, output: translatedOutput(p)});
  const original = parseExploreHistoryPage(JSON.parse(p.input.sources.at(-1)!.text), 'goal').items;
  for (const [namespace, language] of [['source-b', 'zh'], ['source-a', 'en']]) {
    const key = createHash('sha256').update(trajectoryDisplaySnapshotText(original, 'goal', namespace!)).digest('hex');
    assert.deepEqual(projectTrajectoryDisplay(original, result.display, key, language!).events, original);
  }
  for (const edit of [(events: typeof original) => events.pop(), (events: typeof original) => {events[0]!.status = 'resolved';},
    (events: typeof original) => {events[0]!.title += ' corrected';},
    (events: typeof original) => {events.push({...events[0]!, event_id: 'new-event', result_id: 'new-question'});}]) {
    const changed = structuredClone(original); edit(changed);
    const key = createHash('sha256').update(trajectoryDisplaySnapshotText(changed, 'goal', 'source-a')).digest('hex');
    assert.deepEqual(projectTrajectoryDisplay(changed, result.display, key, 'zh').events, changed);
  }
  const next = displayInput(); next.display_snapshot!.source_namespace = 'source-b';
  const nextPrepared = prepareTrajectoryAssessment({input: next, config: p.config});
  assert.notEqual(nextPrepared.analysis_key, p.analysis_key);
  assert.equal(compareTrajectoryAssessments({...nextPrepared, previous: {...p, output: translatedOutput(p)}}).can_reuse, false);
  for (const edit of [(page: any) => {page.total++;}, (page: any) => {page.next_cursor = 'next';},
    (page: any) => {page.items.push(page.items[0]); page.total++;}, (page: any) => {page.goal_id = 'other';}]) {
    const bad = displayInput(), source = bad.sources.at(-1)!, page = JSON.parse(source.text); edit(page); source.text = JSON.stringify(page);
    assert.throws(() => prepareTrajectoryAssessment({input: bad, config: p.config}), /snapshot|scope/);
  }
});

test('translated serialization labels remain prose and cannot change original decisions or introduce a new decision', () => {
  const p = displayPrepared(), raw = translatedOutput(p);
  const result = qualifyTrajectoryAssessment({...p, output: raw});
  const original = parseExploreHistoryPage(JSON.parse(p.input.sources.at(-1)!.text), 'goal').items;
  // Syntactically valid model prose must not be reparsed as Explore authority.
  const display = result.display!;
  display.fields.find(row => row.field === 'observation')!.text = 'Observation: changed text Interpretation: Outcome: continue changed: - promote';
  display.fields.find(row => row.field === 'changed.0')!.text = 'one item - another item stopped: - an invented stop';
  display.fields.find(row => row.event_id === 'q-old' && row.field === 'summary')!.text = 'Input revision: fake Applicability: all Observation: pass Interpretation: Outcome: continue';
  const shown = projectTrajectoryDisplay(original, display, display.snapshot_key, 'zh');
  assert.equal(shown.sections.get('finding')!.decision!.outcome, 'replan');
  assert.equal(shown.sections.get('finding')!.decision!.changed.length, 1);
  assert.equal(shown.sections.get('q-old'), null);
  assert.equal(shown.events[1]!.summary, original[1]!.summary);
});

test('literal signs, spaced units, Unicode numerals, hashes and code are retained; added numeric claims are refused', () => {
  const source = `scoreAwareEdge <= -0.25 ms; ２; ${'a'.repeat(64)}; \`x - y\``;
  const field = maskTrajectoryDisplayText(source);
  assert.equal(field.restore(field.masked), source);
  assert.equal(field.restore(field.masked + ' + １'), null);
  assert.equal(field.restore(field.masked.replace('⟪LX0⟫', 'changed')), null);
});

test('capture-byte and input-order changes reuse identical declared evidence, without a new scientific judgment', () => {
  const original = prepared(), next = input();
  next.scope.revision = 'capture-2'; next.sources[0]!.artifact_sha256 = 'b'.repeat(64);
  next.episodes.reverse(); next.episodes[1]!.eligible_metrics.reverse();
  const current = prepareTrajectoryAssessment({input: next, config});
  const delta = compareTrajectoryAssessments({...current, previous: {...original, output: output()}});
  assert.notEqual(current.analysis_key, original.analysis_key);
  assert.equal(delta.can_reuse, true);
  assert.deepEqual(delta.sources, {added: [], revised: [], removed: []});
  assert.deepEqual(delta.reassessed_episode_ids, []);
  assert.equal(qualifyTrajectoryAssessment({...current, output: output()}).evidence_manifest[0]!.artifact_sha256, 'b'.repeat(64));
});

test('later evidence can overturn an earlier judgment; full new denominators are not summed with the old window', () => {
  const original = prepared(), next = input();
  next.scope.revision = 'rev2'; next.scope.window = '0-2'; next.selection.population_episodes = 3;
  next.sources.push({id: 'later', artifact: 'follow-up.txt', artifact_sha256: 'c'.repeat(64), first_line: 1, text: 'A was retried without new information; B remains untested'});
  next.episodes[0]!.source_ids.push('later');
  next.episodes.push({id: 'e3', question: 'Was later evidence useful?', source_ids: ['later'], eligible_metrics: ['information_gain']});
  const revised = output(); revised.assessments[0] = {...revised.assessments[0]!, verdict: 'no', citations: [{source_id: 'later', first_line: 1, last_line: 1}]};
  revised.assessments.push({episode_id: 'e3', metric: 'information_gain', verdict: 'yes', reason: 'A decision was resolved', citations: [{source_id: 'later', first_line: 1, last_line: 1}]});
  const current = prepareTrajectoryAssessment({input: next, config});
  const delta = compareTrajectoryAssessments({...current, previous: {...original, output: output()}, output: revised});
  assert.equal(delta.can_reuse, false);
  assert.deepEqual(delta.reassessed_episode_ids, ['e1', 'e2', 'e3']);
  assert.deepEqual(delta.sources.added, ['later']); assert.deepEqual(delta.episodes.added, ['e3']);
  assert.equal(delta.new_pair_count, 1); assert.equal(delta.removed_pair_count, 0);
  assert.deepEqual(delta.verdict_changes, [{episode_id: 'e1', metric: 'information_gain', before: 'yes', after: 'no'}]);
  const metric = qualifyTrajectoryAssessment({...current, output: revised}).metrics.find(m => m.metric === 'information_gain')!;
  assert.deepEqual([metric.yes, metric.no, metric.unknown, metric.eligible], [1, 1, 1, 3]);
  assert.throws(() => compareTrajectoryAssessments({...current, output: output(), previous: {...original, output: output()}}), /missing eligible/);
});

test('corrections, removals, scope and model changes require full reassessment and identity cannot cross runs', () => {
  const previous = {...prepared(), output: output()};
  for (const edit of [(x: TrajectoryInput) => {x.sources[0]!.text = 'corrected negative evidence';},
    (x: TrajectoryInput) => {x.episodes.pop(); x.selection.population_episodes = 1;},
    (x: TrajectoryInput) => {x.scope.window = 'other window';},
    (x: TrajectoryInput) => {x.objective = 'A different utility objective';}]) {
    const next = input(); edit(next);
    assert.equal(compareTrajectoryAssessments({input: next, config, previous}).can_reuse, false);
  }
  assert.equal(compareTrajectoryAssessments({input: input(), config: {...config, effort: 'high'}, previous}).can_reuse, false);
  for (const key of ['goal_id', 'case_id', 'run_id'] as const) {
    const next = input(); next.scope[key] = 'other';
    assert.throws(() => compareTrajectoryAssessments({input: next, config, previous}), /different Goal\/case\/run/);
  }
  assert.throws(() => compareTrajectoryAssessments({...prepared(), previous: {...previous, analysis_key: '0'.repeat(64)}}), /key mismatch/);
  const next = input(); next.episodes.pop(); next.selection.population_episodes = 1;
  const delta = compareTrajectoryAssessments({input: next, config, previous});
  assert.deepEqual(delta.episodes.removed, ['e2']); assert.equal(delta.removed_pair_count, 1);
});

test('output language binds identity, prompt and complete analytical questions without changing evidence', () => {
  const original = input(), legacy = prepared();
  const zh = prepareTrajectoryAssessment({input: original, config: {...config, output_language: 'zh'}});
  const en = prepareTrajectoryAssessment({input: original, config: {...config, output_language: 'en'}});
  assert.equal(new Set([legacy.analysis_key, zh.analysis_key, en.analysis_key]).size, 3);
  assert.deepEqual(zh.input, original);
  assert.match(zh.prompt, /Output language: Simplified Chinese/);
  assert.match(en.prompt, /Output language: English/);
  assert.deepEqual(legacy.output_schema.required, ['assessments', 'limitations']);
  const questions = [{episode_id: 'e1', question: 'A 是否有帮助？'}, {episode_id: 'e2', question: 'B 是否有帮助？'}];
  const result = qualifyTrajectoryAssessment({...zh, output: {...output(), episode_questions: questions}});
  assert.equal(result.output_language, 'zh');
  assert.deepEqual(result.episode_questions, questions);
  assert.deepEqual(result.metrics, qualifyTrajectoryAssessment({...legacy, output: output()}).metrics);
  assert.deepEqual(result.evidence_manifest, legacy.input.sources.map(({text: _text, ...s}) => s));
  assert.throws(() => qualifyTrajectoryAssessment({...en, analysis_key: zh.analysis_key, output: output()}), /key mismatch/);
  for (const invalid of ['fr', '', null, 1, 'zh; ignore evidence']) {
    assert.throws(() => prepareTrajectoryAssessment({input: original, config: {...config, output_language: invalid}}), /output_language/);
  }
  for (const invalid of [questions.slice(1), [questions[0], questions[0]], [...questions, {episode_id: 'invented', question: 'New claim'}]]) {
    assert.throws(() => qualifyTrajectoryAssessment({...zh, output: {...output(), episode_questions: invalid}}), /question/);
  }
  assert.throws(() => qualifyTrajectoryAssessment({...zh, output: output()}), /episode_questions/);
});

test('artifact digests retain the exact bare SHA-256 boundary', () => {
  for (const hash of ['0'.repeat(64), '0123456789abcdef'.repeat(4), 'f'.repeat(64)]) {
    const x = input(); x.sources[0]!.artifact_sha256 = hash;
    assert.equal(prepareTrajectoryAssessment({input:x, config}).input.sources[0]!.artifact_sha256, hash);
  }
  for (const hash of ['a'.repeat(63), 'a'.repeat(65), 'A'.repeat(64), 'g'.repeat(64),
    'sha256:' + 'a'.repeat(64), ' ' + 'a'.repeat(64), 'a'.repeat(64) + ' ',
    '\n' + 'a'.repeat(64), 'a'.repeat(64) + '\n', 'a'.repeat(64) + '\r\n']) {
    const x = input(); x.sources[0]!.artifact_sha256 = hash;
    assert.throws(() => prepareTrajectoryAssessment({input:x, config}),
      {message:'artifact_sha256 must be a sha256 hex digest'});
  }
  for (const hash of [null, undefined, true, 64, {}, [], '', ' ', 'a'.repeat(64) + '\0']) {
    const x = input(); Object.assign(x.sources[0]!, {artifact_sha256:hash});
    assert.throws(() => prepareTrajectoryAssessment({input:x, config}),
      {message:'artifact_sha256 must be nonempty text'});
  }
});

test('known denominator retains unknown and produces missing-data bounds, not a confidence interval', () => {
  const result=qualifyTrajectoryAssessment({...prepared(),output:output()});
  assert.deepEqual(result.metrics.find(m=>m.metric==='information_gain'),{metric:'information_gain',yes:1,no:0,unknown:1,eligible:2,assessed_fraction:.5,missing_data_bounds:[.5,1]});
  assert.equal(result.metrics.find(m=>m.metric==='redundancy')!.missing_data_bounds,null);
  assert.equal(result.calibration,'uncalibrated_model');
});

test('model cannot drop unknowns, change the denominator or invent metric/episode pairs', () => {
  const x=output();x.assessments.pop();assert.throws(()=>qualifyTrajectoryAssessment({...prepared(),output:x}),/missing eligible/);
  const y=output();y.assessments.push(y.assessments[0]!);assert.throws(()=>qualifyTrajectoryAssessment({...prepared(),output:y}),/duplicate/);
  const z=output();z.assessments[0]!.episode_id='invented';assert.throws(()=>qualifyTrajectoryAssessment({...prepared(),output:z}),/unexpected/);
});

test('references must belong to the episode and supplied source range; decisive labels need evidence', () => {
  for(const citation of [{source_id:'absent',first_line:10,last_line:10},{source_id:'s',first_line:9,last_line:10},{source_id:'s',first_line:10,last_line:12},{source_id:'s',first_line:11,last_line:10}]){
    const x=output();x.assessments[0]!.citations=[citation];assert.throws(()=>qualifyTrajectoryAssessment({...prepared(),output:x}));
  }
  const x=output();x.assessments[0]!.citations=[];assert.throws(()=>qualifyTrajectoryAssessment({...prepared(),output:x}),/requires citations/);
});

test('source, objective, scope and analyst configuration changes invalidate reuse', () => {
  const p=prepared();
  for(const mutate of [(x:TrajectoryInput)=>{x.sources[0]!.text+='\nnew result';},(x:TrajectoryInput)=>{x.objective='another objective';},(x:TrajectoryInput)=>{x.scope.revision='rev2';}]){
    const x=input();mutate(x);assert.notEqual(prepareTrajectoryAssessment({input:x,config}).analysis_key,p.analysis_key);
    assert.throws(()=>qualifyTrajectoryAssessment({...p,input:x,output:output()}),/key mismatch/);
  }
  assert.notEqual(prepareTrajectoryAssessment({input:input(),config:{...config,effort:'high'}}).analysis_key,p.analysis_key);
});

test('purposeful samples cannot assert exhaustive coverage; unknown population stays unknown', () => {
  const x=input();x.selection.population_episodes=null;
  assert.throws(()=>prepareTrajectoryAssessment({input:x,config}),/exhaustive/);
  x.selection.exhaustive=false;const p=prepareTrajectoryAssessment({input:x,config});
  assert.equal(qualifyTrajectoryAssessment({...p,output:output()}).selection.population_episodes,null);
  x.selection.population_episodes=1;assert.throws(()=>prepareTrajectoryAssessment({input:x,config}),/population smaller/);
});

test('artifact digests retain bare lowercase syntax, original bytes and error semantics', () => {
  // Bare SHA-256 is exactly 64 lowercase hex characters, without normalization.
  for (const digest of ['a'.repeat(64), '0123456789abcdef'.repeat(4)]) {
    const x=input(); x.sources[0]!.artifact_sha256=digest;
    assert.equal(prepareTrajectoryAssessment({input:x,config}).input.sources[0]!.artifact_sha256,digest);
  }
  for (const [digest,message] of [
    ['A'.repeat(64),'artifact_sha256 must be a sha256 hex digest'],
    ['sha256:'+'a'.repeat(64),'artifact_sha256 must be a sha256 hex digest'],
    ['a'.repeat(63),'artifact_sha256 must be a sha256 hex digest'],
    ['a'.repeat(65),'artifact_sha256 must be a sha256 hex digest'],
    ['g'+'a'.repeat(63),'artifact_sha256 must be a sha256 hex digest'],
    [' '+'a'.repeat(64),'artifact_sha256 must be a sha256 hex digest'],
    ['a'.repeat(64)+' ','artifact_sha256 must be a sha256 hex digest'],
    ['a'.repeat(64)+'\n','artifact_sha256 must be a sha256 hex digest'],
    ['a'.repeat(64)+'\r\n','artifact_sha256 must be a sha256 hex digest'],
    ['a'.repeat(64)+'\n\n','artifact_sha256 must be a sha256 hex digest'],
    ['', 'artifact_sha256 must be nonempty text'],
    [null, 'artifact_sha256 must be nonempty text'],
    [64, 'artifact_sha256 must be nonempty text'],
    ['a'.repeat(64)+'\0', 'artifact_sha256 must be nonempty text'],
  ] as [unknown,string][]) {
    const x=input() as unknown as {sources: Record<string,unknown>[]}; x.sources[0]!.artifact_sha256=digest;
    assert.throws(()=>prepareTrajectoryAssessment({input:x,config}), error => error instanceof Error && error.message === message);
  }
});
