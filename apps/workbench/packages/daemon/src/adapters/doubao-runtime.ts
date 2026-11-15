import { constants } from 'node:fs';
import { access, chmod, copyFile, lstat, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import { createHash } from 'node:crypto';
import { homedir, tmpdir } from 'node:os';
import { basename, delimiter, dirname, isAbsolute, join, posix, resolve } from 'node:path';

export const DOUBAO_STREAM_BRIDGE = { platform: 'Linux x64', temporaryMegabytes: 106 };
const BUNDLES = ['entry-cli-router', 'entry-cli-chat'] as const;
const MAX_RUNTIME_BYTES = 512 * 1024 * 1024;

export interface DoubaoStreamRuntime {
  command: string[];
  directory: string;
  close(): Promise<void>;
}

/** The only changes are output selection and forwarding native tool snapshots, in our copy. */
export function patchDoubaoStreamBundle(source: string, bundle: typeof BUNDLES[number]): string {
  const output = 'output_format:"text"';
  if (source.split(output).length !== 2) throw new Error('Doubao 流式桥不兼容：输出入口结构已变化');
  const begin = source.indexOf('case"stream-json":');
  const end = source.indexOf('case"json":', begin);
  const hook = 'onEvent:e=>{switch(e.type){case"session.created":case"session.bound":';
  const at = source.indexOf(hook, begin);
  if (begin < 0 || end < 0 || at < begin || at >= end || (source.slice(begin, end).split(hook).length !== 2)) {
    throw new Error('Doubao 流式桥不兼容：事件输出结构已变化');
  }
  // The native printer flushes pending text first, preserving order with each tool snapshot.
  const emit = bundle === 'entry-cli-router' ? 'I' : 'm';
  const session = bundle === 'entry-cli-router' ? 'f' : 'p';
  const printer = bundle === 'entry-cli-router' ? 'I=e=>{i.flush(),n.stdout' : 'm=e=>{r.flush(),i.stdout';
  if (!source.slice(begin, end).includes(printer) || !source.slice(begin, end).includes(`subtype:"init",session_id:${session}`)) {
    throw new Error('Doubao 流式桥不兼容：原生输出函数已变化');
  }
  const forwarded = `onEvent:e=>{if(e.type==="tool.updated"){${emit}({type:"calli_bridge_tool",session_id:${session},message_id:e.messageId,phase:e.phase,step:e.step});return}switch(e.type){case"session.created":case"session.bound":`;
  return (source.slice(0, at) + forwarded + source.slice(at + hook.length)).replace(output, 'output_format:"stream-json"');
}

/** A read-only catalog entry in our copy; it never calls send, opens a session or reports choices. */
export function patchDoubaoModelBundle(source: string, bundle: typeof BUNDLES[number]): string {
  const router = bundle === 'entry-cli-router';
  const branch = `if("list"===o.kind)return ${router ? 'p' : 'b'}(e,t);`;
  const method = router
    ? 'resolveSettings(e,t){return G({im:this.im,botId:this.botId},e,t)}'
    : 'resolveSettings(e,t){return et({im:this.im,botId:this.botId},e,t)}';
  const load = router ? '(0,U.Fo)' : 'W';
  if (source.split(branch).length !== 2 || source.split(method).length !== 2
    || !source.includes('/alice/slot/action_bar_v3/brief_list')) {
    throw new Error('Doubao 模型目录不兼容：原生加载入口已变化，请升级 Calli');
  }
  return source.replace(method, `async calliReadModels(){return ${load}({im:this.im,botId:this.botId})}${method}`)
    .replace(branch, 'if("list"===o.kind){const catalog=await e.chat.calliReadModels();await e.output.stdout(JSON.stringify({type:"calli_models",models:catalog.models.map(model=>({...model.option}))})+"\\n");return{code:"ok"}}');
}

async function executable(token: string, env: NodeJS.ProcessEnv, cwd: string): Promise<string> {
  const candidates = isAbsolute(token) || token.includes('/')
    ? [resolve(cwd, token)] : (env.PATH ?? '').split(delimiter).filter(Boolean).map(dir => join(dir, token));
  for (const path of candidates) {
    try { await access(path, constants.X_OK); return await realpath(path); } catch { /* Try the next PATH entry. */ }
  }
  throw new Error('Doubao 流式桥没有找到原生 CLI');
}

/** Resolve this instance's installation, never another account's directory as a fallback. */
async function sourceRuntime(command: string[], env: NodeJS.ProcessEnv, cwd: string): Promise<{ root: string; base: string[] }> {
  const nodeWrapper = /^node(?:\.exe)?$/.test(basename(command[0])) && command[1] && !command[1].startsWith('-');
  const base = command.slice(nodeWrapper ? 2 : 1);
  if (env.CALLI_DOUBAO_RUNTIME) return { root: await realpath(resolve(cwd, env.CALLI_DOUBAO_RUNTIME)), base };
  const entry = await executable(command[nodeWrapper ? 1 : 0], env, cwd);
  const nearby = dirname(entry);
  try {
    const doc = JSON.parse(await readFile(join(nearby, 'manifest.json'), 'utf8'));
    if (doc.entrypoint === basename(entry)) return { root: nearby, base };
  } catch { /* An npm launcher has no native manifest next to it. */ }
  try {
    const pkg = JSON.parse(await readFile(join(nearby, '..', 'package.json'), 'utf8'));
    if (!['@bytedance/doubao-cli', '@bytedance-dev/doubao-cli'].includes(pkg.name) || basename(entry) !== 'run.cjs') throw new Error('unknown launcher');
  } catch {
    throw new Error('Doubao 流式桥无法识别此入口；自定义包装器请用 CALLI_DOUBAO_RUNTIME 指定原生运行目录');
  }
  const home = env.HOME || homedir();
  const parent = env.XDG_DATA_HOME || join(home, '.local', 'share');
  if (!isAbsolute(parent)) throw new Error('Doubao 流式桥需要绝对路径的 HOME / XDG_DATA_HOME');
  return { root: await realpath(join(parent, 'doubao_cli', 'app', 'current')), base };
}

function runtimePath(value: unknown): string {
  if (typeof value !== 'string' || !value || value.includes('\\') || value.includes('\0') || isAbsolute(value)
    || posix.normalize(value) !== value || value === '.' || value.startsWith('../') || value === 'manifest.json') {
    throw new Error('Doubao 流式桥的运行清单包含非法路径');
  }
  return value;
}

async function regularFile(root: string, path: string): Promise<void> {
  let current = root;
  const parts = path.split('/');
  for (let i = 0; i < parts.length; i++) {
    current = join(current, parts[i]);
    const stat = await lstat(current);
    if (stat.isSymbolicLink() || (i === parts.length - 1 ? !stat.isFile() : !stat.isDirectory())) {
      throw new Error('Doubao 流式桥只复制原生安装中的普通文件');
    }
  }
}

async function digest(path: string): Promise<string> {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest('hex');
}

/** Read only the selected installation's compatibility metadata. */
async function inspectDoubaoStreamRuntime(command: string[], env: NodeJS.ProcessEnv, cwd: string) {
  if (process.platform !== 'linux') throw new Error('Doubao 实验流式桥仅支持 Linux');
  const { root, base } = await sourceRuntime(command, env, cwd);
  const manifestPath = join(root, 'manifest.json');
  const info = await lstat(manifestPath);
  if (!info.isFile() || info.isSymbolicLink() || info.size > 1024 * 1024) throw new Error('Doubao 流式桥没有有效的原生运行清单');
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  // Compatibility is decided by the patch anchors, not the version number: a CLI update that
  // keeps the same output seams still works, and callers fall back to text output when it does not.
  if (manifest.target !== 'linux-x64' || process.arch !== 'x64') {
    throw new Error('Doubao 流式桥仅支持 Linux x64 的原生安装');
  }
  if (manifest.entrypoint !== 'doubao' || !Array.isArray(manifest.files) || !manifest.files.length || manifest.files.length > 512) {
    throw new Error('Doubao 流式桥的运行清单不兼容');
  }
  const seen = new Set<string>();
  let bytes = 0;
  for (const file of manifest.files) {
    const path = runtimePath(file?.path);
    if (seen.has(path) || !Number.isSafeInteger(file.size) || file.size < 0 || !/^[a-f0-9]{64}$/i.test(file.sha256 ?? '')) throw new Error('Doubao 流式桥的文件清单无效');
    seen.add(path); bytes += file.size;
  }
  if (bytes > MAX_RUNTIME_BYTES || !seen.has('doubao') || BUNDLES.some(bundle => !seen.has(`resources/js_app/${bundle}/main.js`))) throw new Error('Doubao 流式桥的运行文件不完整或过大');
  return { root, base, manifest };
}

/** Settings validation reads the output bundles only, without copying or starting a runtime. */
export async function validateDoubaoStreamRuntime(command: string[], env: NodeJS.ProcessEnv, cwd: string): Promise<void> {
  const { root, manifest } = await inspectDoubaoStreamRuntime(command, env, cwd);
  await regularFile(root, 'doubao');
  for (const bundle of BUNDLES) {
    const rel = `resources/js_app/${bundle}/main.js`, path = join(root, rel);
    const file = manifest.files.find((entry: { path: string }) => entry.path === rel);
    await regularFile(root, rel);
    if (file.size > 8 * 1024 * 1024 || (await lstat(path)).size !== file.size) throw new Error('Doubao 流式桥的输出文件不兼容');
    const source = await readFile(path);
    if (createHash('sha256').update(source).digest('hex') !== file.sha256.toLowerCase()) throw new Error('Doubao 原生文件校验失败；安装可能正在更新，请稍后重试');
    patchDoubaoStreamBundle(source.toString('utf8'), bundle);
  }
}

/** Anchor-checked local snapshot. No writes, hard links, config copies or downloads in the source. */
export async function prepareDoubaoStreamRuntime(command: string[], env: NodeJS.ProcessEnv, cwd: string, mode: 'stream' | 'models' = 'stream'): Promise<DoubaoStreamRuntime> {
  const { root, base, manifest } = await inspectDoubaoStreamRuntime(command, env, cwd);
  const directory = await mkdtemp(join(tmpdir(), 'calli-doubao-stream-'));
  const target = join(directory, 'runtime');
  try {
    await chmod(directory, 0o700);
    await mkdir(target);
    for (const file of manifest.files) {
      await regularFile(root, file.path);
      const from = join(root, file.path), to = join(target, file.path);
      const sourceStat = await lstat(from);
      if (sourceStat.size !== file.size) throw new Error('Doubao 原生文件校验失败；安装可能正在更新，请稍后重试');
      await mkdir(dirname(to), { recursive: true });
      await copyFile(from, to, constants.COPYFILE_FICLONE);
      const stat = await lstat(to);
      if (stat.size !== file.size || await digest(to) !== file.sha256.toLowerCase()) throw new Error('Doubao 原生文件校验失败；安装可能正在更新，请稍后重试');
      await chmod(to, sourceStat.mode & 0o777);
    }
    for (const bundle of BUNDLES) {
      const rel = `resources/js_app/${bundle}/main.js`;
      const path = join(target, rel);
      const patch = mode === 'models' ? patchDoubaoModelBundle : patchDoubaoStreamBundle;
      await writeFile(path, patch(await readFile(path, 'utf8'), bundle));
      const file = manifest.files.find((entry: { path: string }) => entry.path === rel);
      file.size = (await lstat(path)).size;
      file.sha256 = await digest(path);
    }
    await writeFile(join(target, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
    return { directory, command: [join(target, manifest.entrypoint), ...base], close: () => rm(directory, { recursive: true, force: true }) };
  } catch (error) {
    await rm(directory, { recursive: true, force: true });
    throw error;
  }
}
