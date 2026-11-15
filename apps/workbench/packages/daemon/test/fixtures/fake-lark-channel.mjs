#!/usr/bin/env node
import { appendFileSync, readFileSync } from 'node:fs';
const args = process.argv.slice(2);
if (Object.keys(process.env).some(key => key.startsWith('CALLI_'))) throw new Error('Hub credentials must not reach Lark');
const config = process.env.FAKE_LARK_DATA ? JSON.parse(readFileSync(process.env.FAKE_LARK_DATA, 'utf8')) : {};
if (process.env.FAKE_LARK_LOG) appendFileSync(process.env.FAKE_LARK_LOG, JSON.stringify(args) + '\n');
const key = args.slice(0, 2).join(' ');
const arg = name => args[args.indexOf(name) + 1];
/** An optional flag's value: undefined when the flag is absent, never args[0]. */
const optional = name => (args.includes(name) ? args[args.indexOf(name) + 1] : undefined);
await new Promise(r => setTimeout(r, (config.delays ?? {})[key] ?? config.delay ?? 0));
if (args[0] === '--help') { console.log('Readonly Lark fixture'); process.exit(0); }
if (key === 'auth status') { console.log(JSON.stringify({ identities: { user: { available: config.loggedIn !== false, status: 'needs_refresh', openId: config.identity ?? 'ou_self' } } })); process.exit(0); }
if (key === 'event consume') {
  const mode = config.realtime ?? {};
  if (arg('--as') !== (args[2].startsWith('im.') ? 'bot' : 'user')) throw new Error('Wrong event identity');
  if (args.includes('--dry-run')) {
    console.log(JSON.stringify({ ok: true, data: { decision: { status: mode.consoleOff ? 'blocked' : 'ready',
      preconditions: [{ name: 'console_event_published', status: mode.consoleOff ? 'blocked' : 'ok' }] } } }));
    process.exit(0);
  }
  if (mode.busy) { console.error('another event bus is already connected to this app'); process.exit(1); }
  if (mode.stderr) console.error(mode.stderr);
  console.error('event consumer ready');
  for (const event of mode.events ?? []) console.log(JSON.stringify(event));
  if (mode.exitCode !== undefined) process.exit(mode.exitCode);
  setInterval(() => {}, 60_000);
  await new Promise(() => {});
}
const materialTargets = { 'vc +detail': '--meeting-ids', 'note +detail': '--note-id', 'minutes +detail': '--minute-tokens', 'docs +fetch': '--doc' };
if (materialTargets[key]) {
  const target = arg(materialTargets[key]);
  if (typeof target !== 'string' || target.length > 200 || !(key === 'minutes +detail' ? /^[a-z0-9]+$/ : /^[A-Za-z0-9][A-Za-z0-9_-]*$/).test(target)) throw new Error('Invalid material target');
  const expected = key === 'docs +fetch'
    ? ['docs', '+fetch', '--doc', target, '--scope', 'keyword', '--keyword', arg('--keyword'), '--context-before', '1', '--context-after', '2', '--doc-format', 'markdown', '--as', 'user']
    : [args[0], '+detail', materialTargets[key], target, ...(key === 'minutes +detail' ? ['--summary', '--todo'] : []), '--format', 'json', '--as', 'user'];
  if (JSON.stringify(args) !== JSON.stringify(expected) || (key === 'docs +fetch' && !['待办', 'TODO'].includes(arg('--keyword')))) throw new Error('Only bounded readonly summary/TODO commands are allowed');
  const material = config.material?.[key]?.[target] ?? {};
  const response = key === 'docs +fetch' && material.keywords ? material.keywords[arg('--keyword')] ?? {} : material;
  if (response.delay) await new Promise(r => setTimeout(r, response.delay));
  if (response.stderr) console.error(response.stderr);
  if (response.stdout !== undefined) console.log(response.stdout);
  else console.log(JSON.stringify({ ok: response.ok ?? true, identity: response.identity ?? 'user', data: response.data ?? (
    key === 'vc +detail' ? { meetings: [{ meeting_id: target }] }
      : key === 'note +detail' ? { note: { note_id: target } }
        : key === 'minutes +detail' ? { minutes: [{ minute_token: target, status: 'processing', retryable: true }] }
          : { document: { document_id: target, content: '' } }) }));
  process.exit(response.exitCode ?? 0);
}
const allowed = ['task +get-my-tasks', 'im +messages-search', 'im +flag-list', 'calendar +agenda', 'contact +search-user', 'im +chat-search', 'im +threads-messages-list', 'contact +get-user', 'im +chat-list'];
if (!allowed.includes(key) || arg('--as') !== 'user') throw new Error('Only explicit readonly user commands are allowed');
if (config.fail === key) { console.error('sensitive_cli_output_must_not_escape'); process.exit(1); }
const tasks = config.tasks ?? [{ guid: 'task_fixture', summary: '准备季度评审材料', due: { timestamp: String(Date.now() + 36e5) } }];
let data;
if (key === 'task +get-my-tasks') {
  if (!args.includes('--complete=false')) throw new Error('Only incomplete tasks');
  data = { tasks: args.includes('--page-token') ? config.taskPage2 ?? [] : tasks, has_more: !args.includes('--page-token') && Boolean(config.taskPage2), page_token: 'tasks-page-2' };
} else if (key === 'im +messages-search') {
  if (!args.includes('--sender') && !args.includes('--chat-id') && !args.includes('--is-at-me')) throw new Error('Unscoped message search');
  if (!args.includes('--start') || !args.includes('--end')) throw new Error('Missing message cursor');
  // The live CLI rejects a bare Z here: the bounds must name an explicit offset.
  for (const flag of ['--start', '--end']) if (config.explore && !/[+-]\d{2}:\d{2}$/.test(arg(flag) ?? '')) throw new Error(`field validation failed: ${flag}`);
  const messages = args.includes('--is-at-me') ? config.atMeMessages ?? [] : config.messages ?? (config.threaded ? [{ message_id: 'message_fixture', thread_id: 'omt_fixture', root_id: 'om_fixture_root', sender: { id: 'ou_watch' }, content: '请确认材料' }, { message_id: 'reply_fixture', root_id: 'om_fixture_root', sender: { id: 'ou_watch' }, content: '请看回复链' }] : []);
  data = { messages, has_more: false };
} else if (key === 'im +threads-messages-list') {
  if (!args.includes('--thread') || arg('--order') !== 'asc') throw new Error('Missing thread or ascending order');
  const topic = config.topics?.[arg('--thread')] ?? {};
  if (topic.fail) { console.error('sensitive_topic_error'); process.exit(1); }
  data = { messages: args.includes('--page-token') ? topic.page2 ?? [] : topic.messages ?? [],
    has_more: !args.includes('--page-token') && Boolean(topic.page2), page_token: 'topic-page-2' };
} else if (key === 'im +flag-list') data = { flag_items: config.flags ?? [], messages: config.flagMessages ?? [], has_more: false };
else if (key === 'calendar +agenda') data = { events: config.events ?? [] };
else if (key === 'contact +search-user') data = { users: [{ open_id: 'ou_fixture', localized_name: '示例同事', department: '示例团队' }] };
else if (key === 'contact +get-user') {
  const id = optional('--user-id');
  const user = id === undefined ? config.self ?? { open_id: config.identity ?? 'ou_self', name: '示例我' }
    : (config.contacts ?? {})[id] ?? { open_id: id, name: '未知同事' };
  data = { user };
} else if (key === 'im +chat-list') {
  if (arg('--types') !== 'p2p,group' || arg('--sort') !== 'active_time') throw new Error('Explore expects p2p plus group chats sorted by active_time');
  data = { chats: config.chatList ?? [], has_more: false };
} else data = { chats: [{ chat_id: 'oc_fixture', name: '示例项目群', description: '测试用群' }] };
console.log(JSON.stringify({ ok: true, identity: 'user', data }));
