import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { request } from 'node:http';

export const help = `loochat preview-note ID
loochat references ID [--before CURSOR --turns N | --turn TURN_ID [--tool TOOL_ID] | --tools TURN_ID | --input TURN_ID | --answer INPUT_ID | --user-inputs] [continuation: --snapshot SNAPSHOT --offset N | --cursor CURSOR --turns N | --segment THREAD_ID | --via REF --parent-snapshot SNAPSHOT]
loochat notify --text-file FILE|-
loochat tools search [QUERY] [--json]
loochat tools skill
loochat tools describe TOOL [--json]
loochat tools run TOOL --input-file FILE|- --request-id KEY [--json]
loochat jobs get|wait|cancel JOB [--timeout SECONDS] [--json]
loochat jobs fetch JOB ARTIFACT_ID_OR_NAME --output FILE [--json]
loochat browser [AGENT_BROWSER_ARGS...]   (see: loochat tools describe loochat/browser)
Inside LooChat, thread-scoped credentials are provided automatically.
Outside LooChat, set LOOCHAT_URL and LOOCHAT_TOKEN. Output is JSON; fetch never overwrites a file.`;

function responseError(text, status) {
  try {
    const value = JSON.parse(text);
    if (typeof value.error === 'string' && value.error) return value.error;
  } catch { /* Proxies and older servers may return plain text. */ }
  return text.trim().slice(0, 1000) || `HTTP ${status}`;
}

export async function transport(path, body, binary = false, personal = false, socketOnly = false) {
  const socketPath = personal ? process.env.LOOCHAT_TODO_SOCKET : process.env.LOOCHAT_TOOLS_SOCKET;
  const key = personal ? process.env.LOOCHAT_TODO_KEY : process.env.LOOCHAT_TOOLS_KEY;
  if (socketPath || key) {
    if (!socketPath || !key) throw new Error('Incomplete LooChat tool credentials; start a new LooChat session.');
    return new Promise((resolve, reject) => {
      const payload = body ? JSON.stringify(body) : undefined;
      const req = request({ socketPath, path, method: body ? 'POST' : 'GET',
        headers: { Authorization: `Bearer ${key}`, ...(body ? { 'Content-Type': 'application/json' } : {}) } }, res => {
        const chunks = []; let size = 0;
        res.on('data', chunk => {
          size += chunk.length;
          if (size > 21 * 1024 * 1024) req.destroy(new Error('Response too large'));
          else chunks.push(chunk);
        });
        res.on('error', reject);
        res.on('end', () => {
          try {
            const bytes = Buffer.concat(chunks);
            if (res.statusCode >= 400) throw new Error(responseError(bytes.toString(), res.statusCode));
            resolve(binary ? bytes : JSON.parse(bytes.toString()));
          } catch (err) { reject(err); }
        });
      });
      req.setTimeout(30000, () => req.destroy(new Error('Request timed out. Retry with the same request-id.')));
      req.on('error', reject);
      req.end(payload);
    });
  }
  if (personal) throw new Error('Personal commands require a new thread in the personal directory; see loochat todo --help.');
  if (socketOnly) throw new Error('Signal commands require thread-scoped LooChat socket credentials; start a new LooChat session.');
  const base = process.env.LOOCHAT_URL;
  const token = process.env.LOOCHAT_TOKEN;
  if (!base || !token) throw new Error('Run inside a new LooChat agent session, or set LOOCHAT_URL and LOOCHAT_TOKEN.');
  const apiPath = path === '/command' ? '/api/tools/command' : path.replace('/artifacts/', '/api/tool-artifacts/');
  const res = await fetch(new URL(apiPath, base), {
    method: body ? 'POST' : 'GET', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    signal: AbortSignal.timeout(30000), ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!res.ok) throw new Error(responseError(await res.text(), res.status));
  return binary ? Buffer.from(await res.arrayBuffer()) : res.json();
}

/**
 * `loochat browser ARGS` is `agent-browser ARGS` against the person's own browser: LooChat starts it when
 * needed and says where it listens; the commands themselves are agent-browser's, passed through.
 */
export async function browserMain(args) {
  const browser = await transport('/command', { op: 'browser.ensure' });
  if (!args.length) { console.log(JSON.stringify(browser)); return 0; }
  const executable = process.env.LOOCHAT_AGENT_BROWSER || browser.agentBrowser || 'agent-browser';
  const env = { ...process.env, AGENT_BROWSER_CDP: String(browser.port) };
  // Page text is marked as untrusted data, and the helper daemon does not linger once unused.
  env.AGENT_BROWSER_CONTENT_BOUNDARIES ??= '1';
  env.AGENT_BROWSER_IDLE_TIMEOUT_MS ??= '600000';
  env.AGENT_BROWSER_SESSION ??= 'loochat';
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, { stdio: 'inherit', env });
    child.on('error', err => reject(err.code === 'ENOENT'
      ? new Error('这台机器还没装 agent-browser。先告诉用户，征得同意后运行 npm install -g agent-browser（约 115 MB），装好即可使用，不用重启 LooChat；也可以设置 LOOCHAT_AGENT_BROWSER 指向已有的可执行文件。') : err));
    child.on('exit', (code, signal) => resolve(code ?? (signal ? 1 : 0)));
  });
}

/** CLI syntax is deliberately small; schemas and provider instructions load on demand. */
export async function toolsMain(args) {
  const [group, command, ...rest] = args;
  const positional = [], options = {};
  while (rest.length) {
    const part = rest.shift();
    if (part === '--json') continue;
    if (part.startsWith('--')) {
      if (!['--input-file', '--request-id', '--timeout', '--output'].includes(part) || !rest.length || rest[0].startsWith('--')) throw new Error(`Invalid option: ${part}`);
      if (options[part] !== undefined) throw new Error(`Duplicate option: ${part}`);
      options[part] = rest.shift();
    } else positional.push(part);
  }
  const check = (min, max, flags = []) => {
    if (positional.length < min || positional.length > max || Object.keys(options).some(k => !flags.includes(k))) throw new Error(help);
  };
  const call = body => transport('/command', body);
  let result;
  if (group === 'tools' && command === 'skill') {
    check(0, 0); result = { name: 'loochat-tools', content: readFileSync(new URL('../../../skills/loochat-tools/SKILL.md', import.meta.url), 'utf8') };
  } else if (group === 'tools' && command === 'search') {
    check(0, 1); result = await call({ op: 'tools.search', query: positional[0] ?? '' });
  } else if (group === 'tools' && command === 'describe') {
    check(1, 1); result = await call({ op: 'tools.describe', tool: positional[0] });
  } else if (group === 'tools' && command === 'run') {
    check(1, 1, ['--input-file', '--request-id']);
    if (!options['--input-file'] || !options['--request-id']) throw new Error('Give --input-file FILE|- and --request-id KEY. Reuse the ID only for retries of this request.');
    const file = options['--input-file'];
    result = await call({ op: 'tools.run', tool: positional[0], requestId: options['--request-id'], input: JSON.parse(readFileSync(file === '-' ? 0 : file, 'utf8')) });
  } else if (group === 'jobs' && ['get', 'wait', 'cancel'].includes(command)) {
    check(1, 1, command === 'wait' ? ['--timeout'] : []);
    const seconds = Number(options['--timeout'] ?? 300);
    if (!Number.isFinite(seconds) || seconds <= 0 || seconds > 3600) throw new Error('--timeout must be between 0 and 3600 seconds');
    const until = Date.now() + seconds * 1000;
    do {
      result = await call({ op: command === 'cancel' ? 'jobs.cancel' : 'jobs.get', id: positional[0] });
      if (command !== 'wait' || !['queued', 'running'].includes(result.state) || result.waiting) break;
      if (Date.now() >= until) { result = { ...result, timedOut: true }; break; }
      await new Promise(resolve => setTimeout(resolve, 1000));
    } while (true);
    if (command === 'wait' && result.state !== 'succeeded') process.exitCode = 2;
  } else if (group === 'jobs' && command === 'fetch') {
    check(2, 2, ['--output']);
    if (!options['--output']) throw new Error('Give --output FILE; existing files are never overwritten.');
    const job = await call({ op: 'jobs.get', id: positional[0] });
    const artifact = job.artifacts.find(a => a.id === positional[1] || a.name === positional[1]);
    if (!artifact) throw new Error('No such artifact');
    const bytes = await transport(`/artifacts/${encodeURIComponent(positional[0])}/${encodeURIComponent(artifact.id)}`, undefined, true);
    const sha256 = createHash('sha256').update(bytes).digest('hex');
    if (bytes.length !== artifact.size || sha256 !== artifact.sha256) throw new Error('Artifact integrity check failed');
    writeFileSync(options['--output'], bytes, { flag: 'wx', mode: 0o600 });
    result = { path: options['--output'], mimeType: artifact.mimeType, size: bytes.length, sha256 };
  } else throw new Error(help);
  console.log(JSON.stringify(result));
}

export async function notifyMain(args) {
  if (args.length !== 2 || args[0] !== '--text-file' || !args[1]) throw new Error('Use loochat notify --text-file FILE|-');
  if (!process.env.LOOCHAT_TOOLS_SOCKET || !process.env.LOOCHAT_TOOLS_KEY) throw new Error('notify requires thread-scoped credentials');
  const text = readFileSync(args[1] === '-' ? 0 : args[1], 'utf8');
  console.log(JSON.stringify(await transport('/command', { op: 'notify', text })));
}

/** Read only the snapshot explicitly attached by the person to this conversation. */
export async function referencesMain(args) {
  const [id, ...rest] = args;
  if (!id) throw new Error('reference ID is required');
  const input = { op: 'references.read', id };
  const names = { '--before': 'before', '--turns': 'turns', '--turn': 'turn', '--offset': 'offset', '--tool': 'tool', '--tools': 'tools', '--snapshot': 'snapshot', '--input': 'input', '--answer': 'answer', '--cursor': 'cursor', '--segment': 'segment', '--via': 'via', '--parent-snapshot': 'parentSnapshot' };
  while (rest.length) {
    const flag = rest.shift();
    if (flag === '--user-inputs') {
      if (input.userInputs) throw new Error('duplicate reference option');
      input.userInputs = true; continue;
    }
    const key = names[flag], value = rest.shift();
    if (!key || value === undefined || input[key] !== undefined) throw new Error('invalid reference option');
    input[key] = value;
  }
  console.log(JSON.stringify(await transport('/command', input, false, false, true)));
}

export async function previewNoteMain(args) {
  if (args.length !== 1) throw new Error('usage: loochat preview-note ID');
  console.log(JSON.stringify(await transport('/command', { op: 'preview.note', id: args[0] })));
}
