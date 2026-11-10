import assert from 'node:assert/strict';
import {test} from 'node:test';
import {loadedLensBudget} from './goal-workspace-m2.mjs';

const fast = () => Array.from({length:5}, () => [400,500,600]);

test('an isolated moderate outlier is retained without rejecting the render', () => {
  const samples = fast();
  samples[2] = [1200,500,600];
  const before = structuredClone(samples);
  const result = loadedLensBudget(samples);
  assert.equal(result.withinBudget,true);
  assert.equal(result.overTargetCount,1);
  assert.equal(result.maxMs,1200);
  assert.equal(result.mediansMs[2],600);
  assert.deepEqual(samples,before,'Qualification preserves sample order and values');
});

test('one repeatedly slow transition cannot hide in the overall median', () => {
  const samples = fast();
  samples[2] = [1100,1200,500];
  assert.equal(loadedLensBudget(samples).withinBudget,false);
});

test('a single severe stall fails even with fast medians', () => {
  const samples = fast();
  samples[2][0] = 2200;
  assert.equal(loadedLensBudget(samples).withinBudget,false);
});

test('the 1s median and 2s individual boundaries are strict', () => {
  for (const row of [[1000,1000,500],[2000,500,600]]) {
    const samples = fast(); samples[0] = row;
    assert.equal(loadedLensBudget(samples).withinBudget,false);
  }
  const samples = fast(); samples[0] = [1999.9,999.9,500];
  assert.equal(loadedLensBudget(samples).withinBudget,true);
});

test('missing transitions, missing repetitions and invalid timings cannot qualify', () => {
  assert.throws(() => loadedLensBudget(fast().slice(1)));
  for (const row of [[400,500], [NaN,400,500], [Infinity,400,500],[-1,400,500]]) {
    const samples = fast(); samples[0] = row;
    assert.throws(() => loadedLensBudget(samples));
  }
});
