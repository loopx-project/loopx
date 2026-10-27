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
const contextInput = () => {
  const x = displayInput(), source = x.sources.at(-1)!, page = JSON.parse(source.text);
  page.items[0].summary = page.items[2].summary = 'Complete24paired checks; native benefit remains unknown.';
  source.text = JSON.stringify(page);
  source.artifact_sha256 = createHash('sha256').update(source.text).digest('hex');
  x.sources.push({id: 'validation', artifact: 'validation.txt', artifact_sha256: 'c'.repeat(64), first_line: 40,
    text: 'The first record counts diagnostic calls, not independent experiments.\nThe later record counts independent input pairs, not diagnostic calls.'});
  x.display_snapshot!.translation_encoding = 'source_text_aliases_with_literal_values_v1';
  x.display_snapshot!.contexts = [
    {event_id: 'q-old', field: 'summary', citations: [{source_id: 'validation', first_line: 40, last_line: 40}]},
    {event_id: 'q-new', field: 'summary', citations: [{source_id: 'validation', first_line: 41, last_line: 41}]},
  ];
  return x;
};
const translatedOutput = (p = displayPrepared()) => ({...output(), episode_questions: [{episode_id: 'e1', question: 'A 是否有帮助？'}, {episode_id: 'e2', question: 'B 是否有帮助？'}],
  display_translations: JSON.parse(p.prompt.split('\n\ndisplay_fields:\n')[1]!).map((row: {field_id: string; text: string}) => ({field_id: row.field_id, text: `【译文】${row.text}`}))});

test('new generation selects faithful display defaults without upgrading frozen or explicit inputs', () => {
  for (const language of ['zh', 'en']) {
    const original = contextInput(), configForLanguage = {...config, output_language: language};
    delete original.display_snapshot!.translation_encoding;
    const before = structuredClone(original);
    const frozen = prepareTrajectoryAssessment({input: original, config: configForLanguage});
    const next = prepareTrajectoryAssessment({input: original, config: configForLanguage, generation_defaults: true});
    assert.equal(next.input.display_snapshot!.translation_encoding, 'source_text_aliases_with_literal_values_v3');
    assert.notEqual(next.analysis_key, frozen.analysis_key);
    assert.equal(next.config.output_language, language);
    assert.deepEqual(next.input.sources, original.sources);
    assert.deepEqual(next.input.episodes, original.episodes);
    assert.deepEqual(original, before, 'Writer normalization does not modify the captured input');
    assert.deepEqual(prepareTrajectoryAssessment({input: next.input, config: next.config}), next);
    assert.deepEqual(prepareTrajectoryAssessment({input: original, config: configForLanguage}), frozen);
    for (const encoding of ['source_text_aliases_v1', 'source_text_aliases_with_literal_values_v2'] as const) {
      const explicit = structuredClone(original);
      explicit.display_snapshot!.translation_encoding = encoding;
      assert.deepEqual(prepareTrajectoryAssessment({input: explicit, config: configForLanguage, generation_defaults: true}),
        prepareTrajectoryAssessment({input: explicit, config: configForLanguage}));
    }
    const invalid = {...original, display_snapshot: {...original.display_snapshot, translation_encoding: null}};
    assert.throws(() => prepareTrajectoryAssessment({input: invalid, config: configForLanguage, generation_defaults: true}),
      /unsupported display translation encoding/);
  }
  assert.deepEqual(prepareTrajectoryAssessment({input: input(), config, generation_defaults: true}), prepared());
});

test('field-bound evidence disambiguates identical notes without sharing translations or changing source authority', () => {
  const input = contextInput(), p = prepareTrajectoryAssessment({input, config: {...config, output_language: 'zh'}});
  const rows = JSON.parse(p.prompt.split('\n\ndisplay_fields:\n')[1]!);
  const contextual = rows.filter((row: {context_citations?: unknown}) => row.context_citations);
  assert.equal(contextual.length, 2);
  assert.equal(contextual[0].text, contextual[1].text);
  assert.notEqual(contextual[0].field_id, contextual[1].field_id);
  assert.match(p.prompt, /40: The first record counts diagnostic calls/);
  assert.match(p.prompt, /Translate the field, not the context/);
  const raw = translatedOutput(p);
  for (const [index, row] of contextual.entries()) {
    raw.display_translations.find((translated: {field_id: string}) => translated.field_id === row.field_id)!.text =
      `${index ? '完成24组独立输入对' : '完成24次配对诊断调用'}（⟪LX0⟫）；原生收益仍未知。`;
  }
  const result = qualifyTrajectoryAssessment({...p, output: raw});
  const summaries = result.display!.fields.filter(row => row.field === 'summary' && row.context_citations);
  assert.deepEqual(summaries.map(row => row.context_citations), input.display_snapshot!.contexts!.map(row => row.citations));
  assert.match(summaries[0]!.text, /配对诊断调用/);
  assert.match(summaries[1]!.text, /独立输入对/);
  assert.deepEqual(result.assessments, output().assessments);
  const events = parseExploreHistoryPage(JSON.parse(input.sources.find(s => s.id === 'history')!.text), 'goal').items;
  assert.equal(projectTrajectoryDisplay(events, result.display, result.display!.snapshot_key, 'zh').events[0]!.summary, summaries[0]!.text);
  assert.equal(events[0]!.summary, 'Complete24paired checks; native benefit remains unknown.');
  // Literal integrity and provenance do not independently verify translation.
  assert.equal(result.calibration, 'uncalibrated_model');
});

test('display context rejects absent fields, duplicate bindings and evidence outside supplied ranges', () => {
  const mutations: Array<(x: TrajectoryInput) => void> = [
    x => {x.display_snapshot!.contexts![0]!.event_id = 'missing';},
    x => {x.display_snapshot!.contexts![0]!.field = 'status';},
    x => {x.display_snapshot!.contexts!.push(x.display_snapshot!.contexts![0]!);},
    x => {x.display_snapshot!.contexts![0]!.citations = [];},
    x => {x.display_snapshot!.contexts![0]!.citations.push(x.display_snapshot!.contexts![0]!.citations[0]!);},
    x => {x.display_snapshot!.contexts![0]!.citations[0]!.source_id = 'unverified';},
    x => {x.display_snapshot!.contexts![0]!.citations[0]!.first_line = 39;},
    x => {x.display_snapshot!.contexts![0]!.citations[0]!.last_line = 42;},
    x => {x.display_snapshot!.contexts![0]!.citations[0]!.last_line = 39;},
  ];
  for (const mutate of mutations) {
    const input = contextInput(); mutate(input);
    assert.throws(() => prepareTrajectoryAssessment({input, config}), /display context/);
  }
});

test('context edits, removal and source corrections invalidate reuse; old artifacts retain provenance', () => {
  const input = contextInput(), p = prepareTrajectoryAssessment({input, config: {...config, output_language: 'zh'}});
  const previous = {...p, output: translatedOutput(p)}, saved = qualifyTrajectoryAssessment(previous);
  for (const edit of [
    (x: TrajectoryInput) => {x.display_snapshot!.contexts![0]!.citations[0]!.last_line = 41;},
    (x: TrajectoryInput) => {delete x.display_snapshot!.contexts;},
    (x: TrajectoryInput) => {x.sources.find(s => s.id === 'validation')!.text += ' Corrected uncertainty.';},
  ]) {
    const changed = structuredClone(input); edit(changed);
    const next = prepareTrajectoryAssessment({input: changed, config: p.config});
    assert.notEqual(next.analysis_key, p.analysis_key);
    assert.equal(compareTrajectoryAssessments({...next, previous}).can_reuse, false);
  }
  assert.deepEqual(qualifyTrajectoryAssessment(previous), saved);
});

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

test('exact-text aliases translate once and restore every original field, without merging numeric variants', () => {
  const input = displayInput(), source = input.sources.at(-1)!;
  const page = JSON.parse(source.text);
  page.items[2].title = page.items[0].title;
  page.items.push({...page.items[0], event_id: 'numeric-variant', result_id: 'numeric-variant',
    title: page.items[0].title.replace('V18', 'V19')});
  page.total++;
  source.text = JSON.stringify(page);
  source.artifact_sha256 = createHash('sha256').update(source.text).digest('hex');
  const direct = prepareTrajectoryAssessment({input, config: {...config, output_language: 'zh'}});
  input.display_snapshot!.translation_encoding = 'source_text_aliases_v1';
  const compact = prepareTrajectoryAssessment({input, config: direct.config});
  const rows = (p: typeof compact) => JSON.parse(p.prompt.split('\n\ndisplay_fields:\n')[1]!);
  assert(rows(compact).length < rows(direct).length);
  assert(compact.prompt.length < direct.prompt.length);
  assert.notEqual(compact.analysis_key, direct.analysis_key);
  const output = translatedOutput(compact), result = qualifyTrajectoryAssessment({...compact, output});
  const original = parseExploreHistoryPage(page, 'goal').items;
  const display = result.display!;
  assert.equal(display.coverage.eligible, direct.display_request!.eligible);
  assert.equal(display.coverage.translated, display.coverage.eligible);
  assert.equal(display.coverage.missing, 0);
  const shown = projectTrajectoryDisplay(original, display, display.snapshot_key, 'zh');
  assert.equal(shown.events[0]!.title, shown.events[2]!.title);
  assert.match(shown.events[0]!.title, /V18/);
  assert.match(shown.events[3]!.title, /V19/);
  assert.deepEqual(result.metrics, qualifyTrajectoryAssessment({...direct, output: translatedOutput(direct)}).metrics);
  // A duplicate shared-title row invalidates both aliases, without counting
  // either original event as translated.
  const raw = output.display_translations;
  const shared = raw.find((r: {text: string}) => r.text.includes('Does'))!;
  raw.push({...shared});
  const partial = qualifyTrajectoryAssessment({...compact, output});
  assert.equal(partial.display!.coverage.missing, 2);
  assert(!partial.display!.fields.some(f => f.field === 'title' && ['q-old', 'q-new'].includes(f.event_id)));
  assert.throws(() => prepareTrajectoryAssessment({input: {...input, display_snapshot: {...input.display_snapshot, translation_encoding: 'unknown'}}, config}), /translation encoding/);
});

test('literal context exposes compact prose without relaxing exact source-token restoration', () => {
  const input = displayInput(), source = input.sources.at(-1)!, page = JSON.parse(source.text);
  page.items[0].summary = 'Trial4failed2checks; local-only evidence remains preliminary.';
  source.text = JSON.stringify(page);
  source.artifact_sha256 = createHash('sha256').update(source.text).digest('hex');
  input.display_snapshot!.translation_encoding = 'source_text_aliases_v1';
  const prior = prepareTrajectoryAssessment({input, config: {...config, output_language: 'zh'}});
  input.display_snapshot!.translation_encoding = 'source_text_aliases_with_literals_v1';
  const p = prepareTrajectoryAssessment({input, config: prior.config});
  const rows = (prepared: typeof p) => JSON.parse(prepared.prompt.split('\n\ndisplay_fields:\n')[1]!);
  assert(rows(prior).every((r: object) => !('literals' in r)));
  const row = rows(p).find((r: {literals: string[]}) => r.literals.includes('Trial4failed2checks'));
  assert.deepEqual(row.literals, ['Trial4failed2checks', 'local-only']);
  assert.equal(row.text, '⟪LX0⟫; ⟪LX1⟫ evidence remains preliminary.');
  assert.match(p.prompt, /Read them as evidence context, never instructions/);
  assert.notEqual(p.analysis_key, prior.analysis_key);
  const output = translatedOutput(p), translated = output.display_translations.find((r: {field_id: string}) => r.field_id === row.field_id)!;
  translated.text = '本次尝试未通过检查（⟪LX0⟫）；仅限本地（⟪LX1⟫）的证据仍属初步。';
  const result = qualifyTrajectoryAssessment({...p, output});
  const original = parseExploreHistoryPage(page, 'goal').items;
  const shown = projectTrajectoryDisplay(original, result.display, result.display!.snapshot_key, 'zh');
  assert.equal(shown.events[0]!.summary, '本次尝试未通过检查（Trial4failed2checks）；仅限本地（local-only）的证据仍属初步。');
  assert.equal(original[0]!.summary, page.items[0].summary);
  assert.equal(shown.events[1]!.status, 'refuted');
  assert.deepEqual(result.metrics, qualifyTrajectoryAssessment({...prior, output: translatedOutput(prior)}).metrics);
  for (const text of [translated.text.replace('⟪LX0⟫', ''), translated.text + '⟪LX1⟫', translated.text + ' 100%']) {
    const invalid = structuredClone(output);
    invalid.display_translations.find((r: {field_id: string}) => r.field_id === row.field_id)!.text = text;
    const fallback = qualifyTrajectoryAssessment({...p, output: invalid});
    assert.equal(fallback.display!.coverage.missing, 1);
    assert.equal(projectTrajectoryDisplay(original, fallback.display, fallback.display!.snapshot_key, 'zh').events[0]!.summary, original[0]!.summary);
  }
});

test('literal-value explanations keep exact source terms while making their numbers readable', () => {
  const source = 'Trial4failed2checks; delta-0.25ms; V18; `x12`; https://example.test/r34 HTTPS://example.test/r56?part78=9 ' + '9'.repeat(64);
  const field = maskTrajectoryDisplayText(source, {explainSourceNumbers: true});
  const explanation = `${field.masked} 第4次尝试有2项检查未通过；变化为-0.25毫秒，版本18。`;
  assert.equal(field.restore(explanation), source + ' 第4次尝试有2项检查未通过；变化为-0.25毫秒，版本18。');
  assert.equal(maskTrajectoryDisplayText(source).restore(explanation), null, 'Existing encodings remain strict');
  for (const invalid of [
    explanation.replace('第4次', '第5次'), explanation.replace('2项', '3项'),
    explanation.replace('-0.25毫秒', '0.25毫秒'), explanation.replace('-0.25毫秒', '-0.3毫秒'),
    explanation + ' 4', explanation + ' 12', explanation + ' 34', explanation + ' 56', explanation + ' 78', explanation + ' 9999',
    explanation.replace('第4次', 'HTTPS://example.test/r4 次'),
    explanation.replace('第4次', 'mailto:x4@example.test 次'),
    explanation.replace('2项', 'ftp://example.test/r2 项'),
    explanation.replace('⟪LX0⟫', ''), explanation + ' ⟪LX0⟫',
  ]) assert.equal(field.restore(invalid), null, `Reject changed or unbound source values: ${invalid}`);
  const units = maskTrajectoryDisplayText('Budget4ms and Budget4s', {explainSourceNumbers: true});
  assert.equal(units.restore(`${units.masked} 数值为4和4。`), 'Budget4ms and Budget4s 数值为4和4。');
  assert.equal(units.restore(`${units.masked} 数值为4和4和4。`), null, 'Restatement multiplicity is source-bound');
});

test('explicit literal-value encoding restores source-bound numbers without changing the assessment', () => {
  const input = displayInput(), source = input.sources.at(-1)!, page = JSON.parse(source.text);
  page.items[0].summary = 'Trial4failed2checks; local-only evidence remains preliminary.';
  source.text = JSON.stringify(page);
  source.artifact_sha256 = createHash('sha256').update(source.text).digest('hex');
  input.display_snapshot!.translation_encoding = 'source_text_aliases_with_literals_v1';
  const prior = prepareTrajectoryAssessment({input, config: {...config, output_language: 'zh'}});
  input.display_snapshot!.translation_encoding = 'source_text_aliases_with_literal_values_v1';
  const p = prepareTrajectoryAssessment({input, config: prior.config});
  assert.notEqual(p.analysis_key, prior.analysis_key, 'New translation semantics have a separate cache identity');
  const rows = JSON.parse(p.prompt.split('\n\ndisplay_fields:\n')[1]!);
  const row = rows.find((r: {literals: string[]}) => r.literals.includes('Trial4failed2checks'));
  assert.deepEqual(row.literals, ['Trial4failed2checks', 'local-only']);
  assert.deepEqual(row.explanation_numbers, ['4', '2']);
  const output = translatedOutput(p);
  output.display_translations.find((r: {field_id: string}) => r.field_id === row.field_id)!.text =
    '第4次尝试有2项检查未通过（⟪LX0⟫）；仅限本地（⟪LX1⟫）的证据仍属初步。';
  const result = qualifyTrajectoryAssessment({...p, output});
  const revised = structuredClone(input);
  revised.display_snapshot!.translation_encoding = 'source_text_aliases_with_literal_values_v2';
  const next = prepareTrajectoryAssessment({input: revised, config: p.config});
  assert.notEqual(next.analysis_key, p.analysis_key, 'Quantity rendering is explicitly versioned rather than relabelling saved translations');
  assert.deepEqual(next.display_request, p.display_request, 'The captured history and eligible fields are unchanged');
  assert.equal(compareTrajectoryAssessments({...next, previous: {...p, output}}).can_reuse, false, 'A new display version cannot reuse the old translation');
  const nextResult = qualifyTrajectoryAssessment({...next, output});
  assert.deepEqual(nextResult.assessments, result.assessments, 'Display encoding does not change the assessment oracle');
  assert.deepEqual(nextResult.display!.fields, result.display!.fields, 'Exact permitted source numbers remain readable');
  assert.deepEqual(prepareTrajectoryAssessment({input, config: p.config}), p, 'Preparing the new encoding preserves the prior request');
  const original = parseExploreHistoryPage(page, 'goal').items;
  const shown = projectTrajectoryDisplay(original, result.display, result.display!.snapshot_key, 'zh');
  assert.equal(shown.events[0]!.summary, '第4次尝试有2项检查未通过（Trial4failed2checks）；仅限本地（local-only）的证据仍属初步。');
  assert.equal(original[0]!.summary, page.items[0].summary);
  assert.deepEqual(result.metrics, qualifyTrajectoryAssessment({...prior, output: translatedOutput(prior)}).metrics);
  const invalid = structuredClone(output);
  invalid.display_translations.find((r: {field_id: string}) => r.field_id === row.field_id)!.text += ' 新增100%收益。';
  const fallback = qualifyTrajectoryAssessment({...p, output: invalid});
  assert.equal(fallback.display!.coverage.missing, 1);
  assert.equal(projectTrajectoryDisplay(original, fallback.display, fallback.display!.snapshot_key, 'zh').events[0]!.summary, original[0]!.summary);
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

test('display fidelity revision preserves exact numeric checks, originals and historical requests', () => {
  const input = contextInput(), source = input.sources.find(s => s.id === 'history')!, page = JSON.parse(source.text);
  page.items[0].summary = 'Gain+2.50; about4 percent; span3.20/6.40%; reuse8+3items. Limit remains unspecified.';
  source.text = JSON.stringify(page);
  source.artifact_sha256 = createHash('sha256').update(source.text).digest('hex');
  input.display_snapshot!.translation_encoding = 'source_text_aliases_with_literal_values_v2';
  const prior = prepareTrajectoryAssessment({input, config: {...config, output_language: 'zh'}});
  const revised = structuredClone(input);
  revised.display_snapshot!.translation_encoding = 'source_text_aliases_with_literal_values_v3';
  const p = prepareTrajectoryAssessment({input: revised, config: prior.config});
  assert.notEqual(p.analysis_key, prior.analysis_key);
  assert.deepEqual(p.display_request, prior.display_request);
  assert.deepEqual(p.output_schema, prior.output_schema);
  const rows = JSON.parse(p.prompt.split('\n\ndisplay_fields:\n')[1]!);
  const row = rows.find((r: {literals: string[]}) => r.literals.includes('Gain+2.50'));
  assert.deepEqual(row.explanation_numbers, ['+2.50', '4', '3.20', '6.40%', '8', '+3']);
  const raw = translatedOutput(p);
  const translated = raw.display_translations.find((r: {field_id: string}) => r.field_id === row.field_id)!;
  const faithful = '增量为+2.50（⟪LX0⟫）；约百分之4（⟪LX1⟫）；范围为3.20／6.40%（⟪LX2⟫）；复用8加+3项（⟪LX3⟫）。上限具体所指仍不明确。';
  translated.text = faithful;
  const result = qualifyTrajectoryAssessment({...p, output: raw});
  const original = parseExploreHistoryPage(page, 'goal').items;
  assert.equal(projectTrajectoryDisplay(original, result.display, result.display!.snapshot_key, 'zh').events[0]!.summary,
    '增量为+2.50（Gain+2.50）；约百分之4（about4）；范围为3.20／6.40%（span3.20/6.40%）；复用8加+3项（reuse8+3items.）。上限具体所指仍不明确。');
  for (const invalid of [faithful.replace('+2.50', '2.50'), faithful.replace('百分之4', '4%'),
    faithful.replace('3.20／', '3.20%／'), faithful.replace('加+3项', '加3项'), faithful.replace('6.40%', '6.4%')]) {
    translated.text = invalid;
    const rejected = qualifyTrajectoryAssessment({...p, output: raw});
    assert.equal(rejected.display!.coverage.missing, 1);
    assert.equal(projectTrajectoryDisplay(original, rejected.display, rejected.display!.snapshot_key, 'zh').events[0]!.summary,
      page.items[0].summary, 'Invalid restatements keep the full original rather than weakening literal checks');
    assert.deepEqual(rejected.metrics, result.metrics);
  }
  assert.deepEqual(prepareTrajectoryAssessment({input, config: prior.config}), prior);
  assert.equal(original[0]!.summary, page.items[0].summary);
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

test('comparison denominator distinguishes unchanged judgments, disjoint units and unavailable results', () => {
  const current = prepared(), previous = {...prepared(), output: output()};
  const unchanged = compareTrajectoryAssessments({...current, previous, output: output()});
  assert.equal(unchanged.compared_pair_count, 3);
  assert.deepEqual(unchanged.verdict_changes, []);
  for (const args of [
    {...current, previous},
    {...current, previous: prepared(), output: output()},
    {...current, previous: prepared()},
  ]) assert.equal(compareTrajectoryAssessments(args).compared_pair_count, null, 'Missing qualified output is unknown, not zero comparisons');

  const next = input(), renamed = output();
  next.scope.revision = 'different-research-units';
  for (const episode of next.episodes) episode.id = `new-${episode.id}`;
  for (const assessment of renamed.assessments) assessment.episode_id = `new-${assessment.episode_id}`;
  const disjoint = compareTrajectoryAssessments({input: next, config, previous, output: renamed});
  assert.equal(disjoint.compared_pair_count, 0);
  assert.equal(disjoint.new_pair_count, 3);
  assert.equal(disjoint.removed_pair_count, 3);
  assert.deepEqual(disjoint.verdict_changes, [], 'No shared rows is not evidence of unchanged conclusions');
});

test('comparison denominator counts shared episode and metric pairs, not total new judgments', () => {
  const next = input(), revised = output();
  next.episodes[0]!.eligible_metrics = ['information_gain'];
  next.episodes.push({id: 'e3', question: 'Does the new intervention help?', source_ids: ['s'], eligible_metrics: ['information_gain']});
  next.selection.population_episodes = 3;
  revised.assessments.splice(1, 1);
  revised.assessments[0]!.verdict = 'no';
  revised.assessments.push({episode_id: 'e3', metric: 'information_gain', verdict: 'yes', reason: 'New evidence', citations: [{source_id: 's', first_line: 10, last_line: 10}]});
  const delta = compareTrajectoryAssessments({input: next, config, previous: {...prepared(), output: output()}, output: revised});
  assert.equal(delta.compared_pair_count, 2);
  assert.equal(delta.new_pair_count, 1);
  assert.equal(delta.removed_pair_count, 1);
  assert.deepEqual(delta.verdict_changes, [{episode_id: 'e1', metric: 'information_gain', before: 'yes', after: 'no'}]);
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

test('scoped output schema is explicit, versioned and leaves frozen configurations unchanged', () => {
  const legacy = prepared(), scoped = prepareTrajectoryAssessment({input: input(), config: {...config, output_schema_revision: 2}});
  assert.notEqual(scoped.analysis_key, legacy.analysis_key);
  assert.equal(scoped.prompt, legacy.prompt);
  assert.deepEqual(scoped.input, legacy.input);
  assert.deepEqual(prepared(), legacy);
  assert.deepEqual(qualifyTrajectoryAssessment({...scoped, output: output()}).metrics,
    qualifyTrajectoryAssessment({...legacy, output: output()}).metrics);
  for (const revision of [null, 1, 3, true, '2']) {
    assert.throws(() => prepareTrajectoryAssessment({input: input(), config: {...config, output_schema_revision: revision}}), /output_schema_revision/);
  }
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

for (const revision of [undefined, 2, 3, 4]) test(`research synthesis revision ${revision ?? 1} preserves episode citation boundaries`, () => {
  const x = input();
  x.sources.push({id: 'other', artifact: 'other.txt', artifact_sha256: 'b'.repeat(64), first_line: 4, text: 'A competing explanation remains open'});
  x.episodes[1]!.source_ids = ['other'];
  const p = prepareTrajectoryAssessment({input: x, config: {...config, research_synthesis: true, output_language: 'zh',
    ...(revision !== undefined ? {research_synthesis_revision: revision} : {})}});
  const citation = {source_id: 's', first_line: 10, last_line: 10};
  const synthesis = {
    summary: {text: 'Negative evidence narrowed the question; the mechanism remains unresolved.', episode_ids: ['e1'], citations: [citation]},
    findings: [{title: 'The negative result is informative', observation: 'A did not improve the measurement.',
      interpretation: 'The route may not address the bottleneck.', alternative_explanations: ['Noise may hide a smaller gain.'],
      episode_ids: ['e1', 'e2'], citations: [citation, {source_id: 'other', first_line: 4, last_line: 4}]}],
    next_experiments: [{question: 'Does A change the bottleneck under matched conditions?', why_priority: 'It separates the remaining explanations.',
      intervention: 'Change only A.', controls: 'Same checkpoint, inputs, random sequence and total budget.',
      prediction: 'A repeatable gain with the bottleneck change supports the interpretation.',
      disconfirming_result: 'No bottleneck change despite matched repeated gains refutes it.', episode_ids: ['e1'], citations: [citation]}],
  };
  const raw = {...output(), episode_questions: x.episodes.map(e => ({episode_id: e.id, question: e.question})), synthesis};
  const result = qualifyTrajectoryAssessment({...p, output: raw});
  assert.deepEqual(result.synthesis, synthesis);
  assert.equal(result.calibration, 'uncalibrated_model');
  assert.notEqual(p.analysis_key, prepareTrajectoryAssessment({input: x, config: {...config, output_language: 'zh'}}).analysis_key);
  const previous = {...prepared(), output: output()};
  assert.equal(compareTrajectoryAssessments({...p, previous}).can_reuse, false);
  for (const mutate of [
    (s: typeof synthesis) => {s.summary.citations = [];},
    (s: typeof synthesis) => {s.summary.episode_ids = ['missing'];},
    (s: typeof synthesis) => {s.summary.episode_ids = ['e1', 'e1'];},
    (s: typeof synthesis) => {s.summary.citations[0]!.first_line = 9;},
    (s: typeof synthesis) => {s.findings[0]!.episode_ids = ['e1'];},
    (s: typeof synthesis) => {s.next_experiments[0]!.disconfirming_result = '';},
    (s: typeof synthesis) => {s.findings.push(...s.findings, ...s.findings, ...s.findings);},
  ]) {
    const invalid = structuredClone(raw); mutate(invalid.synthesis);
    assert.throws(() => qualifyTrajectoryAssessment({...p, output: invalid}));
  }
  const invalidMetric = structuredClone(raw);
  invalidMetric.assessments[0]!.citations = [{source_id: 'other', first_line: 4, last_line: 4}];
  assert.throws(() => qualifyTrajectoryAssessment({...p, output: invalidMetric}), /outside episode evidence/);
  assert.throws(() => qualifyTrajectoryAssessment({...p, output: {...raw, synthesis: undefined}}), /synthesis/);
});

test('synthesis off preserves legacy prompt, schema, key and qualified output', () => {
  const original = prepared(), off = prepareTrajectoryAssessment({input: input(), config: {...config, research_synthesis: false}});
  assert.deepEqual(off, original);
  assert.deepEqual(qualifyTrajectoryAssessment({...off, output: output()}), qualifyTrajectoryAssessment({...original, output: output()}));
  assert.equal(qualifyTrajectoryAssessment({...original, output: output()}).synthesis, undefined);
  assert.throws(() => prepareTrajectoryAssessment({input: input(), config: {...config, research_synthesis: 'yes'}}), /must be boolean/);
});

test('synthesis revision separates new guidance while frozen and disabled requests keep their identities', () => {
  const legacy = prepareTrajectoryAssessment({input: input(), config: {...config, research_synthesis: true}});
  // Frozen public fixture identities from before the revision: upgrading the
  // producer must not strand saved jobs, including language-specific requests.
  assert.equal(prepared().analysis_key, 'bafc1cd4c93f25a274a6f6789eda79f3a1f9e197ff21a651bfd7f677f36cb607');
  assert.equal(legacy.analysis_key, '96c07478771c8bdb4db5e79999a31a25f8acc86cbc182bc8cb09ad52ac8a7a91');
  const next = prepareTrajectoryAssessment({input: input(), config: {...legacy.config, research_synthesis_revision: 2}});
  assert.equal(next.analysis_key, '2d68cdd187eb5961ce022a39b8a68a67a363b6d376d3523381397febf1b935e0');
  assert.equal(next.method_version, 'research-episodes-synthesis-v2');
  assert.notEqual(next.analysis_key, legacy.analysis_key);
  assert.deepEqual(next.input, legacy.input);
  assert.deepEqual(next.output_schema, legacy.output_schema, 'No new result fields or scientific qualification gate');
  assert.equal(next.prompt.split('\n\nEvidence pack:\n')[1], legacy.prompt.split('\n\nEvidence pack:\n')[1]);
  const raw = {...output(), synthesis: {summary: {text: 'The mechanism is unresolved.', episode_ids: ['e1'],
    citations: [{source_id: 's', first_line: 10, last_line: 10}]}, findings: [], next_experiments: []}};
  assert.deepEqual(qualifyTrajectoryAssessment({...next, output: raw}).metrics,
    qualifyTrajectoryAssessment({...legacy, output: raw}).metrics);
  assert.equal(compareTrajectoryAssessments({...next, previous: {...legacy, output: raw}}).can_reuse, false,
    'A guidance revision needs an explicit new analysis; do not relabel a saved judgment');
  assert.deepEqual(prepareTrajectoryAssessment({input: input(), config: legacy.config}), legacy);
  const selection = prepareTrajectoryAssessment({input: input(), config: {...next.config, research_synthesis_revision: 3}});
  assert.equal(selection.method_version, 'research-episodes-synthesis-v3');
  assert.equal(selection.analysis_key, 'e348081086ca231be5aa9f22e3d695e66306410053afaaff3e9d2a5719cef033');
  assert.notEqual(selection.analysis_key, next.analysis_key);
  assert.deepEqual(selection.input, next.input);
  assert.deepEqual(selection.output_schema, next.output_schema, 'Selection guidance reuses the report fields and evidence gates');
  assert.equal(selection.prompt.split('\n\nEvidence pack:\n')[1], next.prompt.split('\n\nEvidence pack:\n')[1]);
  assert.deepEqual(qualifyTrajectoryAssessment({...selection, output: raw}).metrics,
    qualifyTrajectoryAssessment({...next, output: raw}).metrics);
  assert.equal(compareTrajectoryAssessments({...selection, previous: {...next, output: raw}}).can_reuse, false);
  assert.deepEqual(prepareTrajectoryAssessment({input: input(), config: next.config}), next,
    'Explicit case-selection guidance must not upgrade a frozen revision 2 request');
  const decision = prepareTrajectoryAssessment({input: input(), config: {...next.config, research_synthesis_revision: 4}});
  assert.equal(decision.method_version, 'research-episodes-synthesis-v4');
  assert.notEqual(decision.analysis_key, selection.analysis_key);
  assert.deepEqual(decision.input, selection.input);
  assert.deepEqual(decision.output_schema, selection.output_schema, 'Decision guidance adds no fields or scientific gate');
  assert.equal(decision.prompt.split('\n\nEvidence pack:\n')[1], selection.prompt.split('\n\nEvidence pack:\n')[1]);
  assert.deepEqual(qualifyTrajectoryAssessment({...decision, output: raw}).metrics,
    qualifyTrajectoryAssessment({...selection, output: raw}).metrics);
  assert.equal(compareTrajectoryAssessments({...decision, previous: {...selection, output: raw}}).can_reuse, false);
  assert.deepEqual(prepareTrajectoryAssessment({input: input(), config: selection.config}), selection,
    'A saved revision 3 report remains bound to its original instructions and job');
  for (const revision of [null, true, '2', 0, 1, 5]) {
    assert.throws(() => prepareTrajectoryAssessment({input: input(), config: {...config,
      research_synthesis: true, research_synthesis_revision: revision}}), /supported revision/);
  }
  assert.throws(() => prepareTrajectoryAssessment({input: input(), config: {...config,
    research_synthesis: false, research_synthesis_revision: 2}}), /enabled synthesis/);
  assert.throws(() => prepareTrajectoryAssessment({input: input(), config: {...config,
    research_synthesis: false, research_synthesis_revision: 3}}), /enabled synthesis/);
  assert.throws(() => prepareTrajectoryAssessment({input: input(), config: {...config,
    research_synthesis: false, research_synthesis_revision: 4}}), /enabled synthesis/);
});
