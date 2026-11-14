import { test } from 'node:test';
import assert from 'node:assert/strict';
import { agentGroups, effortsForModel, imagesBlocked, modelLoadLabel, modelLoadPercent, modelName, modelRows } from '../src/components/Pickers.js';
import { isFastSpeed, type AgentInfo } from '../src/api.js';

test('the new-thread picker omits hidden single instances and accounts before grouping', () => {
  const agents = [
    { id: 'solo', hidden: true },
    { id: 'first', adapter: 'multi', hidden: true },
    { id: 'second', adapter: 'multi' },
    { id: 'third', adapter: 'multi', hidden: false },
    { id: 'gone-1', adapter: 'gone', hidden: true },
    { id: 'gone-2', adapter: 'gone', hidden: true },
    { id: 'legacy' },
  ] as AgentInfo[];
  assert.deepEqual(agentGroups(agents).map(group => group.map(agent => agent.id)), [['second', 'third'], ['legacy']]);
  assert.equal(agents.length, 7, 'settings and existing threads retain the complete catalog');
  assert.deepEqual(agentGroups(agents.map(agent => ({ ...agent, hidden: true }))), []);
  assert.deepEqual(agentGroups(agents.map(agent => ({ ...agent, hidden: false }))).map(group => group.map(agent => agent.id)), [
    ['solo'], ['first', 'second', 'third'], ['gone-1', 'gone-2'], ['legacy'],
  ]);
});

const claude = [
  { id: 'default', label: 'Default (recommended)', description: 'Opus 5.5' },
  { id: 'opus', label: 'Opus 5.5', description: 'For complex work' },
  { id: 'haiku', label: 'Haiku 5.5' },
];
const pi = [{ id: 'zai/glm', label: 'GLM (zai)' }];

test('with no override, the button shows the model the agent default points at', () => {
  assert.equal(modelName(claude, null), 'Opus 5.5');
  assert.equal(modelName(pi, null), '默认模型');
  assert.equal(modelName([], null), '默认模型');
});

test('a chosen model shows its own label, and an unknown id shows as-is', () => {
  assert.equal(modelName(claude, 'haiku'), 'Haiku 5.5');
  assert.equal(modelName(claude, 'gone-model'), 'gone-model');
});

test('model load follows the selected model and the agent default alias', () => {
  const models = [
    { id: 'default', label: 'Default', description: 'GPT Big', loadPercent: 184 },
    { id: 'big', label: 'GPT Big', loadPercent: 184 },
    { id: 'small', label: 'GPT Small', loadPercent: 0 },
    { id: 'quiet', label: 'Quiet' },
  ];
  assert.equal(modelLoadPercent(models, null), 184);
  assert.equal(modelLoadPercent(models, 'big'), 184);
  assert.equal(modelLoadPercent(models, 'small'), 0, 'zero is a reported load, not missing data');
  assert.equal(modelLoadPercent(models, 'quiet'), undefined);
  assert.equal(modelLoadPercent(models, 'gone'), undefined);
  assert.equal(modelLoadLabel(100), '负载100%');
});

test('the agent default is folded into one row, so there is no duplicate default', () => {
  const rows = modelRows(claude);
  assert.deepEqual(rows.map((r) => r.value), [null, 'opus', 'haiku']);
  assert.equal(rows[0].hint, '当前是 Opus 5.5');
  assert.equal(rows.filter((r) => r.label.includes('Default')).length, 0);
});

test('agents without their own default get a plain default row', () => {
  const rows = modelRows(pi);
  assert.deepEqual(rows.map((r) => r.value), [null, 'zai/glm']);
  assert.equal(rows[0].label, '默认模型');
});

test('effort levels follow the model, with the agent default looked up under its own id', () => {
  const models = [
    { id: 'default', label: 'Default', description: 'Opus 5.5', efforts: ['low', 'high', 'max'] },
    { id: 'old', label: 'Opus 4.6', efforts: ['low', 'high'] },
    { id: 'tiny', label: 'Haiku 4.5', efforts: [] },
    { id: 'unknown', label: 'No info' },
  ];
  const agentWide = ['low', 'medium', 'high'];
  assert.deepEqual(effortsForModel(models, null, agentWide), ['low', 'high', 'max']);
  assert.deepEqual(effortsForModel(models, 'old', agentWide), ['low', 'high']);
  assert.deepEqual(effortsForModel(models, 'tiny', agentWide), [], 'a model can have no effort setting');
  assert.deepEqual(effortsForModel(models, 'unknown', agentWide), agentWide, 'missing info falls back');
  assert.deepEqual(effortsForModel([], null, agentWide), agentWide);
});

test('an agent that flags its default model (pi) gets the same treatment as one with a "default" alias', () => {
  const models = [
    { id: 'z/vision', label: 'GLM-4.6V', efforts: ['off', 'low'], images: true },
    { id: 'z/main', label: 'GLM-5.3', efforts: ['low', 'high', 'max'], images: false, isDefault: true },
  ];
  assert.equal(modelName(models, null), 'GLM-5.3');
  assert.deepEqual(effortsForModel(models, null, ['medium']), ['low', 'high', 'max']);
  const rows = modelRows(models);
  assert.deepEqual(rows.map((r) => r.value), [null, 'z/vision', 'z/main'], 'the real model stays selectable by name');
  assert.equal(rows[0].hint, '当前是 GLM-5.3');
});

test('imagesBlocked says why pictures cannot be sent, and nothing when they can or it is unknown', () => {
  const models = [
    { id: 'a', label: 'Vision', images: true },
    { id: 'b', label: 'TextOnly', images: false, isDefault: true },
    { id: 'c', label: 'Unknown' },
  ];
  assert.equal(imagesBlocked(models, 'a'), null);
  assert.match(imagesBlocked(models, 'b') ?? '', /TextOnly 不能读图片/);
  assert.match(imagesBlocked(models, null) ?? '', /TextOnly/, 'no override means the default model');
  assert.equal(imagesBlocked(models, 'c'), null);
  assert.equal(imagesBlocked([], null), null);
});

test('permissionHint: the middle level means edits only for an agent that cannot ask', async () => {
  const { permissionHint } = await import('../src/permissions.js');
  assert.match(permissionHint('auto_edit', true), /询问/);
  assert.match(permissionHint('auto_edit', false), /拒绝/);
  assert.equal(permissionHint('full_access', false), permissionHint('full_access', true));
  assert.equal(permissionHint('unknown', true), '');
});

test('startingPermission: the agent\'s own default when it has one it supports, else auto_edit', async () => {
  const { startingPermission } = await import('../src/permissions.js');
  assert.equal(startingPermission({ permissionModes: ['auto_edit', 'full_access'], defaultPermissionMode: 'full_access' }), 'full_access');
  assert.equal(startingPermission({ permissionModes: ['ask', 'auto_edit', 'full_access'] }), 'auto_edit');
  assert.equal(startingPermission({ permissionModes: ['auto_edit'], defaultPermissionMode: 'full_access' }), 'auto_edit');
  assert.equal(startingPermission({ permissionModes: ['ask', 'full_access'] }), 'ask');
  assert.equal(startingPermission({ permissionModes: [] }), 'auto_edit');
});

test('a model that cannot see is still offered pictures when the agent can show them to it', async () => {
  const { imagesNote } = await import('../src/components/Pickers.js');
  const models = [
    { id: 'p/plain', label: 'Plain', images: false },
    { id: 'p/blind', label: 'Blind', images: false, imagesVia: 'describe_image' },
    { id: 'p/eyes', label: 'Eyes', images: true },
  ];
  assert.match(imagesBlocked(models, 'p/plain') ?? '', /Plain 不能读图片/);
  assert.equal(imagesBlocked(models, 'p/blind'), null);
  assert.match(imagesNote(models, 'p/blind') ?? '', /Blind 不能直接看图，会用 describe_image/);
  assert.equal(imagesNote(models, 'p/eyes'), null);
  assert.equal(imagesNote(models, 'p/plain'), null);
});

test('speed options follow the selected model and advertise adjustable models', async () => {
  const { speedsForModel } = await import('../src/components/Pickers.js');
  const speeds = [{ id: 'default', label: '标准' }, { id: 'priority', label: '快速', isFast: true }];
  const models = [{ id: 'fast-capable', label: 'Main', isDefault: true, speeds }, { id: 'plain', label: 'Plain' }, { id: 'economy', label: 'Economy', speeds: [{ id: 'flex', label: '经济', isFast: false }] }];
  assert.deepEqual(speedsForModel(models, null), speeds);
  assert.deepEqual(speedsForModel(models, 'plain'), []);
  assert.deepEqual(speedsForModel(models, 'unknown'), []);
  assert.match(modelRows(models).find(row => row.value === 'fast-capable')?.hint ?? '', /可加速/);
  assert.equal(modelRows(models).find(row => row.value === 'economy')?.hint, undefined);
});

test('fast controls accept older Hub catalogs without treating economy or unknown tiers as acceleration', () => {
  for (const id of ['priority', 'fast', 'ultrafast']) {
    const speed = { id, label: id };
    assert.equal(isFastSpeed(speed), true);
    assert.match(modelRows([{ id: 'model', label: 'Model', speeds: [speed] }])[1].hint ?? '', /可加速/);
    assert.equal(isFastSpeed({ ...speed, isFast: false }), false, 'an explicit capability takes precedence');
  }
  for (const id of ['default', 'flex', 'custom-tier']) assert.equal(isFastSpeed({ id, label: id }), false);
  assert.equal(isFastSpeed({ id: 'custom-tier', label: 'Custom', isFast: true }), true);
});
