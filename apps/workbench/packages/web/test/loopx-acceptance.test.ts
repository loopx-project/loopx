import assert from 'node:assert/strict';
import test from 'node:test';
import { criterionStates, displayStatus, parseContract, parseObservation, pickPreparedPlan, type Checks } from '../src/loopx/acceptance.js';

const checks = (over: Partial<Checks> = {}): Checks => ({
  revision: 2, digest: 'd2', objective: 'ship', status: 'accepted', scope: 'goal',
  criteria: [{ id: 'a', description: 'A', oracleState: null }, { id: 'b', description: 'B', oracleState: null }],
  receipt: { operationId: 'op', contractRevision: 2, contractDigest: 'd2', todoId: null, results: [{ criterionId: 'a', passed: true, exitCode: 0 }, { criterionId: 'b', passed: true, exitCode: 0 }] },
  ...over,
});

test('a receipt for the current contract passes every criterion it covers', () => {
  assert.deepEqual(criterionStates(checks()).map(c => c.state), ['accepted', 'accepted']);
  assert.equal(displayStatus(checks()), 'accepted');
});

test('a receipt for an older contract revision is stale, not accepted', () => {
  const old = checks({ receipt: { ...checks().receipt!, contractRevision: 1 } });
  assert.deepEqual(criterionStates(old).map(c => c.state), ['stale', 'stale']);
  assert.equal(displayStatus(old), 'stale');
});

test('a failed result, a one-task check and a stale oracle each override a plain pass', () => {
  const failed = checks({ receipt: { ...checks().receipt!, results: [{ criterionId: 'a', passed: false, exitCode: 1 }, { criterionId: 'b', passed: true, exitCode: 0 }] } });
  assert.deepEqual(criterionStates(failed).map(c => c.state), ['failed', 'accepted']);
  assert.equal(displayStatus(failed), 'failed');
  assert.deepEqual(criterionStates(checks({ receipt: { ...checks().receipt!, todoId: 't1' } })).map(c => c.state), ['partial', 'partial']);
  const oracle = checks({ criteria: [{ id: 'a', description: 'A', oracleState: 'stale' }, { id: 'b', description: 'B', oracleState: 'current' }] });
  assert.deepEqual(criterionStates(oracle).map(c => c.state), ['stale', 'accepted']);
  assert.equal(displayStatus(oracle), 'stale');
});

test('with no receipt nothing is accepted, whatever the header says', () => {
  assert.deepEqual(criterionStates(checks({ status: 'unverified', receipt: null })).map(c => c.state), ['unverified', 'unverified']);
  assert.deepEqual(criterionStates(checks({ receipt: null })).map(c => c.state), ['unverified', 'unverified']);
});

test('the default outcome requirements parse, with the agent review kept apart', () => {
  const contract = parseContract({ outcome_requirements: { outcomes: [{ id: 'o', objective: 'obj', requirements: [
    { id: 'r', description: 'do it', status: 'unverified', agent_review: { conclusion: 'met', note: 'looks fine' } },
    { id: 'q', description: 'also', status: 'nonsense' },
  ], work: [{ todo_id: 't', status: 'open' }, { todo_id: 'u', status: 'done' }] }] } });
  assert.equal(contract?.checks, null);
  assert.deepEqual(contract?.outcomes[0].requirements.map(r => [r.status, r.review?.conclusion ?? null]), [['unverified', 'met'], ['unverified', null]]);
  assert.equal(contract?.outcomes[0].openWork, 1);
  assert.deepEqual(parseContract({}), { lifecycleState: null, outcomes: [], checks: null });
  assert.equal(parseContract('x'), null);
});

test('the observation needs its schema marker and keeps gaps and guards', () => {
  assert.equal(parseObservation({ goal_id: 'g' }), null);
  const o = parseObservation({
    schema_version: 'goal_acceptance_observation_projection_v0', goal_id: 'g', coverage: 'partial', missing_sources: ['agent_vision'], truncated: false,
    acceptance_gaps: [{ reason: 'no evidence', evidence_required: 'a log', component_checks: { checkpoint_fresh: false, path_outcome_valid: true } }],
    guards: [{ reason: 'blocked', decision_scope: 'goal' }], next_action: 'do x', historical_progress: [{}, {}],
  });
  assert.equal(o?.gaps[0].failedChecks, 1);
  assert.equal(o?.guards[0].scope, 'goal');
  assert.equal(o?.nextAction, 'do x');
  assert.equal(o?.historicalCount, 2);
});

const proposal = (over: Record<string, unknown> = {}) => ({
  action_kind: 'goal.acceptance.check', context: { kind: 'goal', goal_id: 'g' }, proposal_id: 'p1', created_at: '2026-11-16T01:00:00Z',
  permission_classification: 'local_command_execution', status: 'preview_ready', available_transitions: ['apply', 'cancel'],
  normalized_parameters: { goal_id: 'g', document: { objective: 'ship', scope: { kind: 'goal' }, criteria: [{ id: 'a', description: 'A', validation_argv: ['test', '-f', 'x'], oracle: { output_refs: ['x'] } }] } },
  ...over,
});

test('only the newest applicable plan for this Goal is offered', () => {
  assert.equal(pickPreparedPlan([proposal()], 'g')?.criteria[0].command, '"test" "-f" "x"');
  assert.equal(pickPreparedPlan([proposal()], 'other'), null);
  assert.equal(pickPreparedPlan([proposal({ status: 'applied' })], 'g'), null);
  assert.equal(pickPreparedPlan([proposal({ gate: { reason: 'x' } })], 'g'), null);
  assert.equal(pickPreparedPlan([proposal({ available_transitions: ['cancel'] })], 'g'), null);
  assert.equal(pickPreparedPlan([proposal({ permission_classification: 'read' })], 'g'), null);
  // An older draft is not resurrected once a newer one exists, and two made at the same instant are ambiguous.
  assert.equal(pickPreparedPlan([proposal(), proposal({ proposal_id: 'p2', created_at: '2026-11-16T02:00:00Z', status: 'cancelled' })], 'g'), null);
  assert.equal(pickPreparedPlan([proposal(), proposal({ proposal_id: 'p2' })], 'g'), null);
});
