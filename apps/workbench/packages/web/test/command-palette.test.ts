import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { filterPalette, isPaletteChord, type PaletteItem } from '../src/command-palette.js';

const item = (id: string, group: PaletteItem['group'], label: string, extra: Partial<PaletteItem> = {}): PaletteItem => ({ id, group, label, ...extra });

describe('filterPalette', () => {
  const items = [
    item('s1', 'sessions', 'Fix the login bug', { hint: '/work/app' }),
    item('s2', 'sessions', 'Write release notes', { hint: '/work/docs' }),
    item('g1', 'goals', 'Ship the login redesign', { needsYou: true }),
    item('g2', 'goals', 'Login audit'),
    item('c1', 'commands', 'New session', { keywords: 'new thread' }),
  ];
  it('ranks a label that starts with the term above one that only contains it', () => {
    assert.deepEqual(filterPalette(items, 'login').map(i => i.id), ['g2', 'g1', 's1']);
  });
  it('needs every term and also looks at hints and keywords', () => {
    assert.deepEqual(filterPalette(items, 'login app').map(i => i.id), ['s1']);
    assert.deepEqual(filterPalette(items, 'thread').map(i => i.id), ['c1']);
    assert.deepEqual(filterPalette(items, 'nothing here'), []);
  });
  it('puts a Goal that needs the person first among equal matches', () => {
    const tied = [item('a', 'goals', 'Report'), item('b', 'goals', 'Report', { needsYou: true })];
    assert.deepEqual(filterPalette(tied, 'report').map(i => i.id), ['b', 'a']);
  });
  it('lists only a few per group when nothing is typed, but never hides a Goal that needs you', () => {
    const many = Array.from({ length: 9 }, (_, n) => item(`g${n}`, 'goals', `Goal ${n}`, { needsYou: n === 8 }));
    const idle = filterPalette(many, '  ').map(i => i.id);
    assert.deepEqual(idle, ['g0', 'g1', 'g2', 'g3', 'g4', 'g8']);
  });
});

describe('isPaletteChord', () => {
  const key = (over: Partial<KeyboardEvent>) => ({ key: 'k', metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, ...over });
  it('is Cmd/Ctrl+K and nothing else', () => {
    assert.equal(isPaletteChord(key({ metaKey: true })), true);
    assert.equal(isPaletteChord(key({ ctrlKey: true, key: 'K' })), true);
    assert.equal(isPaletteChord(key({})), false);
    assert.equal(isPaletteChord(key({ ctrlKey: true, shiftKey: true })), false);
  });
});
