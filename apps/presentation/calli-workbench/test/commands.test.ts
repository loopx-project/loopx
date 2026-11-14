import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildMenu, commandDraft, searchCommands, slashQuery, type HubAction, type MenuRow } from '../src/commands.js';

const actions: HubAction[] = [
  { id: 'model', label: '模型与思考强度', hint: '', keywords: ['model', 'effort'] },
  { id: 'rename', label: '重命名', hint: '', keywords: ['rename'] },
];
const claude = [
  { name: 'compact', description: 'Free up context by summarizing', hint: '<instructions>' },
  { name: '/autocompact', description: 'Set the threshold' },
  { name: 'model', description: 'Set the AI model' },
  { name: 'review', description: 'Review a pull request' },
];
const pi = [
  { name: 'deploy', description: '', kind: 'command' as const },
  { name: 'skill:pdf', description: 'Work with PDF files', kind: 'skill' as const },
  { name: 'fix-tests', description: 'Fix failing tests', kind: 'prompt' as const },
];
const show = (rows: MenuRow[]) =>
  rows.map((r) => (r.type === 'heading' ? `# ${r.label}` : r.type === 'action' ? `@${r.action.id}` : `/${r.command.name}`));

test('the menu opens only for a slash at the start with no space yet', () => {
  assert.equal(slashQuery('/'), '');
  assert.equal(slashQuery('/Comp'), 'comp');
  assert.equal(slashQuery('/compact now'), null);
  assert.equal(slashQuery('hello /compact'), null);
  assert.equal(slashQuery(''), null);
  assert.deepEqual(buildMenu('hello', actions, claude), []);
});

test('a bare slash lists hub actions first, then every agent command under one honest heading', () => {
  assert.deepEqual(show(buildMenu('/', actions, claude)), ['# 操作', '@model', '@rename', '# 命令和技能', '/compact', '/autocompact', '/model', '/review']);
});

test('commands the hub replaces with its own control are left out', () => {
  const rows = show(buildMenu('/', actions, claude, ['model']));
  assert.equal(rows.includes('/model'), false);
  assert.equal(rows.includes('@model'), true);
});

test('name prefix beats name substring, which beats a match in the description', () => {
  assert.deepEqual(show(buildMenu('/comp', [], claude)), ['# 命令和技能', '/compact', '/autocompact']);
  // "pull" is only in a description.
  assert.deepEqual(show(buildMenu('/pull', [], claude)), ['# 命令和技能', '/review']);
  // A single letter does not search descriptions.
  assert.deepEqual(show(buildMenu('/w', [], claude)), ['# 命令和技能', '/review']);
});

test('actions are found by their Chinese label or an English keyword', () => {
  assert.deepEqual(show(buildMenu('/mod', actions, [])), ['# 操作', '@model']);
  assert.deepEqual(show(buildMenu('/重命', actions, [])), ['# 操作', '@rename']);
  assert.deepEqual(show(buildMenu('/zzz', actions, claude)), []);
});

test('when the agent says what each entry is, they are grouped by kind', () => {
  assert.deepEqual(show(buildMenu('/', [], pi)), ['# 命令', '/deploy', '# 模板', '/fix-tests', '# 技能', '/skill:pdf']);
  assert.deepEqual(show(buildMenu('/pdf', [], pi)), ['# 技能', '/skill:pdf']);
});

test('the add menu searches descriptions with spaces and respects hidden commands', () => {
  assert.deepEqual(show(searchCommands('pull request', [], claude)), ['# 命令和技能', '/review']);
  assert.deepEqual(show(searchCommands(' /MODEL ', actions, claude, ['model'])), ['# 操作', '@model']);
});

test('choosing a command preserves the draft and replaces only a recognized prefix', () => {
  assert.equal(commandDraft('review', '检查错误处理\n保留第二行', claude), '/review 检查错误处理\n保留第二行');
  assert.equal(commandDraft('/review', '/compact 检查错误处理', claude), '/review 检查错误处理');
  assert.equal(commandDraft('review', '/tmp/project 是目录', claude), '/review /tmp/project 是目录');
  assert.equal(commandDraft('skill:pdf', '/', pi), '/skill:pdf ');
  assert.equal(commandDraft('skill:pdf', '/skill:pdf 整理文件', pi), '/skill:pdf 整理文件');
});
