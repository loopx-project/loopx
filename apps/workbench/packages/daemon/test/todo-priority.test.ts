import test from 'node:test';
import assert from 'node:assert/strict';
import { needsPerson, stageOf, type StageFacts } from '../src/todo-priority.js';

const none: StageFacts = { workThreads: 0, threadWaiting: false, waitingOnYou: false, openDoneProposal: false, readyRun: false, lastTurnFailed: false, latestRunFailed: false, threadRunning: false };
const facts = (fields: Partial<StageFacts>) => ({ ...none, ...fields });

test('each stage row matches on its own fact', () => {
  assert.equal(stageOf(none), null);
  assert.equal(stageOf(facts({ threadWaiting: true })), 'answer');
  assert.equal(stageOf(facts({ waitingOnYou: true })), 'answer');
  assert.equal(stageOf(facts({ openDoneProposal: true })), 'review');
  assert.equal(stageOf(facts({ readyRun: true })), 'review');
  assert.equal(stageOf(facts({ lastTurnFailed: true })), 'blocked');
  assert.equal(stageOf(facts({ latestRunFailed: true })), 'blocked');
  assert.equal(stageOf(facts({ threadRunning: true, workThreads: 1 })), 'working');
  assert.equal(stageOf(facts({ workThreads: 1 })), 'idle');
});

test('the first matching row wins', () => {
  const all = facts({ workThreads: 1, threadWaiting: true, waitingOnYou: true, openDoneProposal: true, readyRun: true, lastTurnFailed: true, latestRunFailed: true, threadRunning: true });
  assert.equal(stageOf(all), 'answer');
  assert.equal(stageOf({ ...all, threadWaiting: false, waitingOnYou: false }), 'review');
  assert.equal(stageOf({ ...all, threadWaiting: false, waitingOnYou: false, openDoneProposal: false, readyRun: false }), 'blocked');
  assert.equal(stageOf(facts({ workThreads: 1, threadRunning: true })), 'working');
});

test('only answer, review and blocked need the person', () => {
  assert.deepEqual(['answer', 'review', 'blocked', 'working', 'idle', null, undefined].filter(stage => needsPerson(stage as never)), ['answer', 'review', 'blocked']);
});
