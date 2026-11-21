import test from 'node:test';
import assert from 'node:assert/strict';
import type { MachineState } from '@calli/extension-api/web';
import { parsePins, pillFilter, pillSelected, pinCount, togglePin } from '../src/pins';

test('saved pins read back in order and bad data gives none', () => {
  assert.deepEqual(parsePins('["/b","/a","/b",1,null,""]'), ['/b', '/a', '']);
  for (const input of [null, '', '{bad', 'null', '{"a":1}', '42']) assert.deepEqual(parsePins(input), []);
});

test('a new pin goes last and pinning again removes it', () => {
  assert.deepEqual(togglePin(['/a'], '/b'), ['/a', '/b']);
  assert.deepEqual(togglePin(['/a', '/b'], '/a'), ['/b']);
  assert.deepEqual(togglePin([], ''), ['']);
});

test('pills select one project, several, or all', () => {
  assert.equal(pillSelected([], null), true);
  assert.equal(pillSelected(['/a'], null), false);
  assert.equal(pillSelected(['/a', '/b'], '/b'), true);
  assert.equal(pillSelected(['/a'], '/c'), false);
  assert.deepEqual(pillFilter([], '/a'), ['/a']);
  assert.deepEqual(pillFilter(['/a', '/b'], '/a'), ['/a'], 'among several it narrows to the one');
  assert.deepEqual(pillFilter(['/a'], '/a'), []);
  assert.deepEqual(pillFilter(['/a'], null), []);
});

test('a pinned project with no threads shows 0 unless a machine is still loading', () => {
  const machine = (state: MachineState['state']): MachineState => ({ id: 'm', label: 'm', state, observedAt: null });
  assert.equal(pinCount(3, [machine('loading')]), 3);
  assert.equal(pinCount(undefined, [machine('live')]), 0);
  assert.equal(pinCount(undefined, [machine('live'), machine('loading')]), null);
});
