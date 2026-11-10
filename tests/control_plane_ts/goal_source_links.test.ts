import assert from 'node:assert/strict';
import test from 'node:test';
import {planGoalSourceDocument, projectGoalSourceDocument, projectGoalSourceLinks, projectGoalSourceStatus, projectGoalSourceStrategies} from '../../loopx/control_plane/presentation/goal_source_links.ts';

const documentRegistration = () => ({goals: [{id: 'original', repo: '/source', authority_sources: [
  {kind: 'goal_doc', role: 'primary_goal_document', path: 'task.md'},
]}]});
const abcDigest = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad';

test('an original document needs an exact declared binding, not a display label or guessed basename', () => {
  const registration = documentRegistration();
  const plan = planGoalSourceDocument({goal_id: 'original', registration,
    snapshot: {declared_path: 'task.md', path: '/capture/task.md', sha256: abcDigest}});
  assert.deepEqual(projectGoalSourceDocument({plan, registration, document: {text: 'abc'}}),
    {document: {text: 'abc', sha256: abcDigest, captured: true}});
  assert.throws(() => projectGoalSourceDocument({plan, registration, document: {text: 'abd'}}));
  assert.throws(() => planGoalSourceDocument({goal_id: 'other', registration}));
  assert.throws(() => planGoalSourceDocument({goal_id: 'original', registration,
    snapshot: {declared_path: 'another/task.md', path: '/capture/task.md', sha256: abcDigest}}));
  for (const refs of [[], [...registration.goals[0].authority_sources, ...registration.goals[0].authority_sources]]) {
    assert.throws(() => planGoalSourceDocument({goal_id: 'original', registration: {goals: [
      {...registration.goals[0], display_name: 'Use this title instead', authority_sources: refs},
    ]}}));
  }
});

test('document reads retain whitespace, reject changed declarations and bound complete UTF-8 bytes', () => {
  const registration = documentRegistration(), plan = planGoalSourceDocument({goal_id: 'original', registration});
  const text = '\r\n# 原始目标\n\nKeep zero, negative results and `commands` unchanged.\n';
  assert.equal((projectGoalSourceDocument({plan, registration, document: {text}}).document as {text: string}).text, text);
  const changed = structuredClone(registration); changed.goals[0].authority_sources[0].path = 'new-task.md';
  assert.throws(() => projectGoalSourceDocument({plan, registration: changed, document: {text}}));
  for (const invalid of ['', '\0', '字'.repeat(44000)]) {
    assert.throws(() => projectGoalSourceDocument({plan, registration, document: {text: invalid}}));
  }
});

const link = () => ({goal_id: 'original-solver', parent_goal_id: 'research-parent',
  label: 'Original experiment', namespace: 'a'.repeat(64)});

test('an existing source Goal keeps its native id under the local parent', () => {
  const result = projectGoalSourceLinks({local_goal_ids: ['research-parent'], links: [link()]});
  assert.deepEqual(result.goals, [{id: 'original-solver', parent_goal_id: 'research-parent',
    display_name: 'Original experiment', registry_member: true, activation_state: 'active',
    source_access: {label: 'Original experiment', namespace: 'a'.repeat(64), read_only: true}}]);
});

test('ambiguous original ids and invalid namespaces are refused', () => {
  for (const input of [
    {local_goal_ids: ['research-parent'], links: [link(), {...link(), namespace: 'b'.repeat(64)}]},
    {local_goal_ids: ['research-parent'], links: [{...link(), namespace: 'unverified'}]},
  ]) assert.throws(() => projectGoalSourceLinks(input));
});

test('source namespaces retain the existing bare lowercase digest format', () => {
  for (const namespace of ['0'.repeat(64), '0123456789abcdef'.repeat(4)]) {
    const result = projectGoalSourceLinks({local_goal_ids: ['research-parent'], links: [{...link(), namespace}]});
    assert.equal((result.goals as {source_access: {namespace: string}}[])[0].source_access.namespace, namespace);
  }
  for (const namespace of ['A'.repeat(64), 'g'.repeat(64), 'a'.repeat(63), 'a'.repeat(65),
    'sha256:' + 'a'.repeat(64)]) {
    assert.throws(() => projectGoalSourceLinks({local_goal_ids: ['research-parent'], links: [{...link(), namespace}]}));
  }
});

test('a removed parent hides only its retained reference', () => {
  const other = {...link(), goal_id: 'other-solver', parent_goal_id: 'other-parent', namespace: 'b'.repeat(64)};
  assert.deepEqual(projectGoalSourceLinks({local_goal_ids: ['other-parent'], links: [link(), other]}).goals,
    projectGoalSourceLinks({local_goal_ids: ['other-parent'], links: [other]}).goals);
});

test('a native local Goal takes precedence over a retained source reference', () => {
  assert.deepEqual(projectGoalSourceLinks({local_goal_ids: ['research-parent', 'original-solver'],
    links: [link()]}), {goals: []});
});

test('the empty catalog preserves the local-only directory', () => {
  assert.deepEqual(projectGoalSourceLinks({local_goal_ids: ['research-parent'], links: []}), {goals: []});
});

test('distinct workspace views keep repeated original ids physically scoped', () => {
  const links = [{...link(), workspace_goal_id: 'run-a'},
    {...link(), workspace_goal_id: 'run-b', namespace: 'b'.repeat(64)}];
  const result = projectGoalSourceLinks({local_goal_ids: ['research-parent'], links});
  assert.deepEqual((result.goals as {id: string; source_access: {original_goal_id: string}}[])
    .map(goal => [goal.id, goal.source_access.original_goal_id]),
  [['run-a', 'original-solver'], ['run-b', 'original-solver']]);
  for (const invalid of [
    {...links[1], workspace_goal_id: 'run-a'},
    {...links[1], namespace: links[0].namespace},
    {...links[1], workspace_goal_id: 'research-parent'},
    {...links[1], workspace_goal_id: '../other'},
  ]) assert.throws(() => projectGoalSourceLinks({local_goal_ids: ['research-parent'], links: [links[0], invalid]}));
});

test('workspace routing preserves native record content and source bytes', () => {
  const status = {goal_filter: 'original-solver', run_history: {goals: [{id: 'original-solver',
    parent_goal_id: null, title: 'original-solver'}]}, todo_index: {items: [{goal_id: 'original-solver',
    todo_id: 'native-task', text: 'Read original-solver evidence'}]},
    agent_management_projection: {agents: [{goal_ids: ['original-solver'], current_todo: {goal_id: 'original-solver'}}]}};
  const before = structuredClone(status);
  const row = (projectGoalSourceLinks({local_goal_ids: ['research-parent'],
    links: [{...link(), workspace_goal_id: 'case-a'}]}).goals as object[])[0];
  const result = projectGoalSourceStatus({status, row} as never);
  assert.equal((result.todo_index as typeof status.todo_index).items[0].goal_id, 'case-a');
  assert.equal((result.todo_index as typeof status.todo_index).items[0].todo_id, 'native-task');
  assert.equal((result.todo_index as typeof status.todo_index).items[0].text, 'Read original-solver evidence');
  assert.deepEqual(status, before);
  assert.throws(() => projectGoalSourceStatus({status: {...status, run_history: {goals: [{id: 'another'}]}}, row} as never));
});

const strategyRun = (index: number) => ({goal_id: 'original-solver', agent_id: 'source-agent',
  generated_at: new Date(Date.UTC(2026, 0, 1, index)).toISOString(),
  json_path: `native-record-${index}.json`, classification: 'recorded_progress',
  agent_vision: {schema_version: 'goal_vision_replan_contract_v0', agent_id: 'source-agent',
    vision_patch: {vision_summary: `Original objective ${index}`, last_patch_summary: `Original observation ${index}`},
    path_delta: {outcome: 'continue', retained: ['Keep the accepted reference'],
      stopped: ['Candidate was rejected'], unresolved_questions: ['Contribution remains unknown']}}});

const strategyInput = (runs: object[]) => ({goal_id: 'original-solver', namespace: 'a'.repeat(64),
  history: {ok: true, goal_filter: 'original-solver', goals: [{id: 'original-solver',
    registry_member: true, unique_runs: runs.length, latest_runs: runs}]}});

test('all original strategy versions remain readable beyond the status window', () => {
  const runs = Array.from({length: 23}, (_, index) => strategyRun(index));
  const input = strategyInput(runs), before = structuredClone(input);
  const result = projectGoalSourceStrategies(input as never);
  const items = result.items as {recorded_at: string; agent_vision: unknown}[];
  assert.equal(result.total, 23);
  assert.equal(result.native_run_count, 23);
  assert.deepEqual(items.map(item => item.agent_vision), [...runs].reverse().map(run => run.agent_vision));
  assert.deepEqual(input, before);
  assert.equal(JSON.stringify(result).includes('native-record-'), false, 'private native file identities stay server-side');
});

test('run accounting is separate from strategy records and exact replays', () => {
  const one = strategyRun(1), two = strategyRun(2);
  const result = projectGoalSourceStrategies(strategyInput([one, two, {...one},
    {...strategyRun(3), agent_vision: undefined}]) as never);
  assert.equal(result.native_run_count, 4);
  assert.equal(result.total, 2);
});

test('strategy identities bind the original source and exact text version', () => {
  const one = strategyRun(1);
  const read = (runs: object[], namespace = 'a'.repeat(64)) => projectGoalSourceStrategies(
    {...strategyInput(runs), namespace} as never).items as {id: string; agent_vision: unknown}[];
  const first = read([one])[0]!;
  assert.equal(read([Object.fromEntries(Object.entries(one).reverse())])[0]!.id, first.id);
  assert.notEqual(read([one], 'b'.repeat(64))[0]!.id, first.id);
  assert.notEqual(read([{...one, json_path: 'another-native-record.json'}])[0]!.id, first.id);
  const revised = {...one, agent_vision: {...one.agent_vision,
    vision_patch: {...one.agent_vision.vision_patch, last_patch_summary: 'Later correction'}}};
  const revisions = read([one, revised]);
  assert.equal(revisions.length, 2);
  assert.notEqual(revisions[0]!.id, revisions[1]!.id);
  assert.deepEqual(revisions[0]!.agent_vision, one.agent_vision);
});

test('unrecorded time remains unknown without dropping the strategy', () => {
  const unknown = {...strategyRun(1), generated_at: undefined};
  const items = projectGoalSourceStrategies(strategyInput([unknown, strategyRun(2)]) as never).items as {recorded_at: string}[];
  assert.equal(items.length, 2);
  assert.equal(items[1]!.recorded_at, '');
});

test('partial or mismatched native history cannot masquerade as complete', () => {
  const input = strategyInput([strategyRun(1)]);
  const goal = input.history.goals[0]!;
  for (const change of [
    {...input, namespace: 'unverified'},
    {...input, history: {...input.history, goal_filter: 'other-goal'}},
    {...input, history: {...input.history, goals: [goal, {...goal, id: 'another'}]}},
    {...input, history: {...input.history, goals: [{...goal, unique_runs: 2}]}},
    {...input, history: {...input.history, goals: [{...goal, registry_member: false}]}},
    strategyInput([{...strategyRun(1), goal_id: 'another'}]),
    strategyInput([{...strategyRun(1), agent_vision: {...strategyRun(1).agent_vision, agent_id: 'another-agent'}}]),
  ]) assert.throws(() => projectGoalSourceStrategies(change as never));
});
