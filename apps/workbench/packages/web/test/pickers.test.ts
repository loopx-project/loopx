import { test } from 'node:test';
import assert from 'node:assert/strict';
import { agentGroups, effortsForModel, imagesBlocked, modelLoadLabel, modelLoadPercent, modelName, modelRows, modelQueueLabel, modelStatusLabel } from '../src/components/Pickers.js';
import { setLocale } from '../src/i18n/index.js';
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

test('model status distinguishes unavailable queues from zero and follows the default model', () => {
  // Ferry metadata is visible before selection; missing numbers must never be presented as zero.
  const models = [
    { id: 'main', label: 'Ferry Main', isDefault: true, loadPercent: 128, queueSize: null },
    { id: 'zero', label: 'Ferry Zero', loadPercent: 0, queueSize: 0 },
    { id: 'unknown', label: 'Ferry Unknown', queueSize: null },
    { id: 'legacy', label: 'Legacy', loadPercent: 18 },
  ];
  assert.equal(modelStatusLabel(models[0]), '负载128% · 排队未提供');
  assert.equal(modelStatusLabel(models[1]), '负载0% · 排队0');
  assert.equal(modelStatusLabel(models[2]), '负载未提供 · 排队未提供');
  assert.equal(modelStatusLabel(models[3]), '负载18%');
  assert.equal(modelStatusLabel(undefined), '');
  assert.equal(modelStatusLabel({ id: 'failed', label: 'Failed', queueSize: null, loadError: true }), '负载读取失败 · 排队未提供');
  const rows = modelRows(models);
  assert.equal(rows[0].label, '默认 · Ferry Main');
  assert.equal(rows[0].hint, '负载128% · 排队未提供');
  assert.equal(rows.find(row => row.value === 'zero')?.hint, '负载0% · 排队0');
  assert.equal(rows.find(row => row.value === 'legacy')?.hint, undefined, 'older catalogs keep their hints unchanged');
});

test('queue positions include observation age without being presented as total queue sizes', () => {
  // A valid observation overrides unavailable total size; the default and named rows share its status.
  const now = 1_793_800_000_000;
  const queued = { id: 'main', label: 'Ferry Main', isDefault: true, loadPercent: 128, queueSize: null, queuePosition: 550, queueObservedAt: now - 120_000 };
  const nonqueued = { id: 'quiet', label: 'Ferry Quiet', loadPercent: 0, queuePosition: 0, queueObservedAt: now };
  assert.equal(modelStatusLabel(queued, now), '负载128% · 排550（2 分钟前）');
  assert.equal(modelStatusLabel(nonqueued, now), '负载0% · 未排队（刚刚）');
  assert.equal(modelStatusLabel({ ...queued, loadPercent: undefined, loadError: true }, now), '负载读取失败 · 排550（2 分钟前）');
  const rows = modelRows([queued, nonqueued], now);
  assert.equal(rows[0].label, '默认 · Ferry Main');
  assert.equal(rows[0].hint, rows[1].hint);
  assert.equal(rows[1].hint, '负载128% · 排550（2 分钟前）');
  assert.equal(rows[2].hint, '负载0% · 未排队（刚刚）');
});

test('queue observations expire even in cached catalogs, while missing and invalid data stay unknown', () => {
  // Stale/missing observations are never rendered as an empty queue, including a previously nonqueued model.
  const now = 1_793_800_000_000;
  const option = { id: 'main', label: 'Main', queuePosition: 5, queueObservedAt: now - 30 * 60_000 };
  assert.equal(modelQueueLabel(option, now), '排5（30 分钟前）');
  assert.equal(modelQueueLabel(option, now + 1), '排队已过期');
  assert.equal(modelQueueLabel({ ...option, queuePosition: 0 }, now + 1), '排队已过期');
  assert.equal(modelQueueLabel({ ...option, queuePosition: null }, now + 1), '排队已过期');
  for (const invalid of [
    { ...option, queuePosition: null, queueObservedAt: now },
    { ...option, queueObservedAt: undefined },
    { ...option, queueObservedAt: now + 1 },
    { ...option, queueObservedAt: NaN },
    { ...option, queuePosition: -1, queueObservedAt: now },
  ]) assert.equal(modelQueueLabel(invalid, now), '排队未知');
  assert.equal(modelStatusLabel({ id: 'unknown', label: 'Unknown', queuePosition: null }, now), '负载未提供 · 排队未知');
  assert.equal(modelQueueLabel({ id: 'legacy', label: 'Legacy' }, now), '');
  assert.equal(modelQueueLabel(undefined, now), '');
});

test('queue positions and observation ages use localized wording and English minute plurals', () => {
  // Position is not a request count; observation time uses the locale's relative-time grammar.
  const now = 1_793_800_000_000;
  const option = { id: 'a', label: 'A', queuePosition: 550, queueObservedAt: now - 60_000 };
  try {
    setLocale('en');
    assert.equal(modelQueueLabel(option, now), 'Queue #550 (1 minute ago)');
    assert.equal(modelQueueLabel(option, now + 60_000), 'Queue #550 (2 minutes ago)');
    assert.equal(modelQueueLabel({ ...option, queuePosition: 0, queueObservedAt: now }, now), 'Not queued (Just now)');
    assert.equal(modelQueueLabel({ ...option, queuePosition: null, queueObservedAt: undefined }, now), 'Queue unknown');
    assert.equal(modelQueueLabel(option, now + 30 * 60_000), 'Queue outdated');
    setLocale('ja');
    assert.equal(modelQueueLabel(option, now), '待機550番（1 分前）');
    assert.equal(modelQueueLabel({ ...option, queuePosition: 0, queueObservedAt: now }, now), '待機なし（たった今）');
    assert.equal(modelQueueLabel({ ...option, queuePosition: null, queueObservedAt: undefined }, now), '待機状態不明');
    assert.equal(modelQueueLabel(option, now + 30 * 60_000), '待機情報期限切れ');
  } finally { setLocale('zh'); }
});

test('model status labels are localized and English queue counts use the right noun', () => {
  // Status wording follows the same locale as the rest of the picker.
  try {
    setLocale('en');
    assert.equal(modelStatusLabel({ id: 'a', label: 'A', loadPercent: 0, queueSize: 1 }), 'Load 0% · 1 queued request');
    assert.equal(modelStatusLabel({ id: 'a', label: 'A', queueSize: 0 }), 'Load unavailable · 0 queued requests');
    assert.equal(modelStatusLabel({ id: 'a', label: 'A', queueSize: null }), 'Load unavailable · Queue unavailable');
    setLocale('ja');
    assert.equal(modelStatusLabel({ id: 'a', label: 'A', loadPercent: 25, queueSize: null }), '負荷25% · 待機数未提供');
  } finally { setLocale('zh'); }
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

test('context rings stay compact and distinguish unknown, empty and full usage in every locale', async () => {
  const { createElement } = await import('react');
  const { renderToStaticMarkup } = await import('react-dom/server');
  const { UsageMeter } = await import('../src/components/Pickers.js');
  try {
    for (const locale of ['zh', 'en', 'ja'] as const) {
      setLocale(locale);
      for (const percent of [null, 0, 25, 80, 100]) {
        const html = renderToStaticMarkup(createElement(UsageMeter, {
          usage: percent === null ? null : { used: percent * 1000, size: 100000 },
          sample: null,
          api: null,
          threadId: 'thread',
        }));
        assert.match(html, /class="usage/);
        assert.match(html, /aria-label="[^"]+"/);
        assert.equal(html.includes(' high'), percent !== null && percent >= 80);
        assert.equal(html.includes(' unknown'), percent === null);
        assert.equal(html.includes('class="usage-fill"'), percent !== null && percent > 0);
        if (percent !== null) {
          assert.ok(html.includes(`${percent}%`), 'the accessible name includes the current proportion');
          if (percent > 0) assert.ok(html.includes(`stroke-dasharray="${percent} 100"`));
        }
        assert.equal(html.includes('<span>'), false, 'only the ring takes toolbar space');
        assert.equal(html.includes('context-compact'), false, 'details mount only when opened');
      }
    }
  } finally { setLocale('zh'); }
});

test('a CLI without a native catalog exposes no model or effort picker, including member controls', async () => {
  const { createElement } = await import('react');
  const { renderToStaticMarkup } = await import('react-dom/server');
  const { ModelPicker } = await import('../src/components/Pickers.js');
  const props = { catalog: false, models: [], model: null, efforts: [], effort: null, onModel: () => undefined, onEffort: () => undefined };
  assert.equal(renderToStaticMarkup(createElement(ModelPicker, props)), '');
  assert.equal(renderToStaticMarkup(createElement(ModelPicker, { ...props, quietFor: 'CLI instance' })), '');
});
