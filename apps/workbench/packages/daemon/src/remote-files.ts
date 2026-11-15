import { createHash } from 'node:crypto';
import { HttpError } from './errors.js';

/** One command run on another machine, through its relay connection. stdout is bytes because it may carry any text. */
export interface RemoteRun {
  stdout: Buffer;
  stderr: string;
  code: number;
}

export interface RemoteRunOptions {
  /** Written to the command's standard input. Paths and contents travel here, never in the shell string. */
  input?: string;
  timeoutMs?: number;
  maxBytes?: number;
}

/** Runs a shell script on a machine on the node. `HostRegistry.exec` is the real one; tests bind a local sh. */
export type RemoteExec = (command: string, opts?: RemoteRunOptions) => Promise<RemoteRun>;

/** The most text one read returns at once. */
export const MAX_READ_BYTES = 1024 * 1024;
/** Where a read starts when the caller names no length. */
export const DEFAULT_READ_BYTES = 256 * 1024;
/** The most text one write accepts. */
export const MAX_WRITE_BYTES = 1024 * 1024;
/** How many entries a listing shows before saying it was cut. */
const MAX_LIST_ENTRIES = 500;
/** Exchange ids are thread and message ids: plain enough to be a path component without quoting. */
const SAFE_ID = /^[A-Za-z0-9_-]{1,80}$/;

/**
 * Reads part of a text file under `root`. The script resolves both paths with `realpath -m` on
 * the machine and refuses anything outside the member's own directory, so `..` and symlinks that
 * leave it do not get through. Paths travel on stdin so user-supplied values never become shell syntax.
 */
const READ_SCRIPT = `set -eu
IFS= read -r root
IFS= read -r rel
IFS= read -r offset
IFS= read -r limit
root=$(realpath -m -- "$root")
case "$rel" in
  /*) target=$rel ;;
  *) target=$root/$rel ;;
esac
target=$(realpath -m -- "$target")
case "$target" in
  "$root"|"$root"/*) ;;
  *) printf 'CALLI outside\\n'; exit 3 ;;
esac
if [ ! -e "$target" ]; then printf 'CALLI not_found\\n'; exit 4; fi
if [ ! -f "$target" ]; then printf 'CALLI not_file\\n'; exit 8; fi
size=$(wc -c < "$target" | tr -d ' ')
printf 'CALLI ok %s\\n' "$size"
tail -c "+$((offset + 1))" -- "$target" | head -c "$limit"
`;

/**
 * Replaces a text file under `root`. The new text is written next to the old one and moved over
 * it, so a reader sees either version, never half of each; the old file's mode is kept. With an
 * expected hash, the write is refused if someone changed the file since it was read.
 */
const WRITE_SCRIPT = `set -eu
umask 077
IFS= read -r root
IFS= read -r rel
IFS= read -r expect
root=$(realpath -m -- "$root")
case "$rel" in
  /*) target=$rel ;;
  *) target=$root/$rel ;;
esac
target=$(realpath -m -- "$target")
case "$target" in
  "$root"|"$root"/*) ;;
  *) printf 'CALLI outside\\n'; exit 3 ;;
esac
if [ -d "$target" ]; then printf 'CALLI not_file\\n'; exit 8; fi
parent=$(dirname -- "$target")
if [ ! -d "$parent" ]; then printf 'CALLI no_parent\\n'; exit 7; fi
if [ -f "$target" ]; then
  if [ -n "$expect" ]; then
    actual=$(sha256sum -- "$target" | cut -d' ' -f1)
    if [ "$actual" != "$expect" ]; then printf 'CALLI hash_mismatch %s\\n' "$actual"; exit 6; fi
  fi
  mode=$(stat -c %a -- "$target")
else
  mode=644
fi
tmp="$target.calli-$$"
trap 'rm -f -- "$tmp"' EXIT
cat > "$tmp"
chmod "$mode" -- "$tmp"
mv -f -- "$tmp" "$target"
trap - EXIT
size=$(wc -c < "$target" | tr -d ' ')
printf 'CALLI ok %s\\n' "$size"
`;

/** One directory, without `.git`; entries are NUL-separated so names with spaces or tabs survive. */
const LIST_SCRIPT = `set -eu
IFS= read -r root
IFS= read -r rel
root=$(realpath -m -- "$root")
case "$rel" in
  /*) target=$rel ;;
  *) target=$root/$rel ;;
esac
target=$(realpath -m -- "$target")
case "$target" in
  "$root"|"$root"/*) ;;
  *) printf 'CALLI outside\\n'; exit 3 ;;
esac
if [ ! -e "$target" ]; then printf 'CALLI not_found\\n'; exit 4; fi
if [ ! -d "$target" ]; then printf 'CALLI not_dir\\n'; exit 8; fi
printf 'CALLI ok\\n'
find "$target" -mindepth 1 -maxdepth 1 ! -name .git -printf '%y\\t%s\\t%f\\0' | head -z -n 501
`;

/** Writes the handoff document into an exchange directory the hub picked; no workspace scope applies. */
const WRITE_HANDOFF_SCRIPT = `set -eu
umask 077
IFS= read -r dir
mkdir -p -- "$dir"
parent=$(dirname -- "$dir")
# /tmp is shared: nobody else may have planted the folder or its parent as a link or as their own.
if [ -L "$dir" ] || [ ! -O "$dir" ] || [ -L "$parent" ] || [ ! -O "$parent" ]; then printf 'CALLI outside\\n'; exit 3; fi
chmod 700 -- "$dir"
cat > "$dir/handoff.md"
chmod 600 -- "$dir/handoff.md"
printf 'CALLI ok\\n'
`;

/** Reads the result the member wrote, if it wrote one. */
const READ_RESULT_SCRIPT = `set -eu
IFS= read -r dir
IFS= read -r limit
if [ -L "$dir" ] || [ ! -O "$dir" ]; then printf 'CALLI not_found\\n'; exit 4; fi
file="$dir/result.md"
if [ -L "$file" ] || [ ! -f "$file" ]; then printf 'CALLI not_found\\n'; exit 4; fi
size=$(wc -c < "$file" | tr -d ' ')
printf 'CALLI ok %s\\n' "$size"
tail -c +1 -- "$file" | head -c "$limit"
`;

interface Marker {
  kind: string;
  args: string[];
  body: Buffer;
}

function marker(run: RemoteRun): Marker {
  const end = run.stdout.indexOf(0x0a);
  if (end < 0) return { kind: 'bad', args: [], body: run.stdout };
  const line = run.stdout.subarray(0, end).toString('utf8');
  const match = /^CALLI (\S+)(?: (.*))?$/.exec(line);
  if (!match) return { kind: 'bad', args: [], body: run.stdout };
  return { kind: match[1], args: match[2] ? match[2].split(' ').filter(Boolean) : [], body: run.stdout.subarray(end + 1) };
}

function failed(kind: string, args: string[], run: RemoteRun): never {
  switch (kind) {
    case 'outside': throw new HttpError(403, '路径在成员工作目录之外');
    case 'not_found': throw new HttpError(404, '文件不存在');
    case 'not_file': throw new HttpError(400, '不是文件');
    case 'not_dir': throw new HttpError(400, '不是目录');
    case 'no_parent': throw new HttpError(400, '上级目录不存在');
    case 'hash_mismatch': throw new HttpError(409, '文件在读取后被改动', { actualHash: args[0] ?? null });
    default: break;
  }
  const detail = run.stderr.trim() || run.stdout.subarray(0, 400).toString('utf8').trim();
  throw new HttpError(502, detail || `远端命令失败（退出码 ${run.code}）`, { code: 'remote_failed' });
}

function textFrom(body: Buffer, truncated: boolean): string {
  if (body.includes(0)) throw new HttpError(415, '是二进制文件，只支持文本');
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(body);
  } catch {
    // A chunk cut at the byte limit can end in the middle of a character; drop the partial one.
    if (truncated) {
      for (let cut = 1; cut < 4 && cut < body.length; cut++) {
        try {
          return new TextDecoder('utf-8', { fatal: true }).decode(body.subarray(0, body.length - cut));
        } catch { /* try one byte shorter */ }
      }
    }
    throw new HttpError(415, '是二进制文件，只支持文本');
  }
}

function value(value: unknown, what: string): string {
  if (typeof value !== 'string' || !value || value.length > 4096) throw new HttpError(400, `${what}不能为空`);
  if (value.includes('\0') || /[\r\n]/.test(value)) throw new HttpError(400, `${what}含有无效字符`);
  return value;
}

function count(value: unknown, fallback: number, what: string): number {
  if (value === undefined || value === null || value === '') return fallback;
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isSafeInteger(n) || n < 0) throw new HttpError(400, `${what}需要是非负整数`);
  return n;
}

function hashOf(bytes: Buffer): string {
  return createHash('sha256').update(bytes).digest('hex');
}

export interface RemoteRead {
  path: string;
  /** The file's size on that machine. */
  size: number;
  offset: number;
  /** The text read, which may be shorter than the file. */
  text: string;
  /** SHA-256 of the text read, not of the whole file. A full read hashes the whole file. */
  hash: string;
  truncated: boolean;
}

export async function readRemoteText(
  exec: RemoteExec,
  root: string,
  path: string,
  opts: { offset?: number; limit?: number } = {},
): Promise<RemoteRead> {
  const scope = value(root, '工作目录');
  const rel = value(path, '路径');
  const offset = count(opts.offset, 0, 'offset');
  const limit = count(opts.limit, DEFAULT_READ_BYTES, 'limit');
  if (limit === 0 || limit > MAX_READ_BYTES) throw new HttpError(400, `limit 需要是 1 到 ${MAX_READ_BYTES} 之间的整数`);
  const run = await exec(READ_SCRIPT, { input: `${scope}\n${rel}\n${offset}\n${limit}\n` });
  const got = marker(run);
  if (got.kind !== 'ok') failed(got.kind, got.args, run);
  const size = Number(got.args[0]);
  if (!Number.isSafeInteger(size) || size < 0) throw new HttpError(502, '远端没有返回文件大小');
  const truncated = offset + got.body.length < size;
  return { path: rel, size, offset, text: textFrom(got.body, truncated), hash: hashOf(got.body), truncated };
}

export async function writeRemoteText(
  exec: RemoteExec,
  root: string,
  path: string,
  text: string,
  opts: { expectHash?: string | null } = {},
): Promise<{ path: string; size: number; hash: string }> {
  const scope = value(root, '工作目录');
  const rel = value(path, '路径');
  if (typeof text !== 'string' || text.includes('\0')) throw new HttpError(400, '只能写入文本');
  const bytes = Buffer.byteLength(text);
  if (bytes > MAX_WRITE_BYTES) throw new HttpError(413, `文件不能超过 ${MAX_WRITE_BYTES} 字节`);
  const expect = typeof opts.expectHash === 'string' ? opts.expectHash.trim().toLowerCase() : '';
  if (expect && !/^[a-f0-9]{64}$/.test(expect)) throw new HttpError(400, 'hash 需要是 64 位十六进制');
  const run = await exec(WRITE_SCRIPT, { input: `${scope}\n${rel}\n${expect}\n${text}` });
  const got = marker(run);
  if (got.kind !== 'ok') failed(got.kind, got.args, run);
  const size = Number(got.args[0]);
  if (!Number.isSafeInteger(size) || size < 0) throw new HttpError(502, '远端没有返回文件大小');
  return { path: rel, size, hash: hashOf(Buffer.from(text)) };
}

export interface RemoteEntry {
  name: string;
  type: 'dir' | 'file' | 'link' | 'other';
  size: number;
}

export async function listRemoteDir(
  exec: RemoteExec,
  root: string,
  path: string,
): Promise<{ path: string; entries: RemoteEntry[]; truncated: boolean }> {
  const scope = value(root, '工作目录');
  const rel = value(path, '路径');
  const run = await exec(LIST_SCRIPT, { input: `${scope}\n${rel}\n` });
  const got = marker(run);
  if (got.kind !== 'ok') failed(got.kind, got.args, run);
  const raw = got.body.toString('utf8').split('\0').filter(Boolean);
  const entries = raw.slice(0, MAX_LIST_ENTRIES).map((line): RemoteEntry => {
    const [kind, size, ...rest] = line.split('\t');
    const name = rest.join('\t');
    return {
      name,
      type: kind === 'd' ? 'dir' : kind === 'f' ? 'file' : kind === 'l' ? 'link' : 'other',
      size: Number(size) || 0,
    };
  });
  entries.sort((a, b) => (a.type === 'dir' ? 0 : 1) - (b.type === 'dir' ? 0 : 1) || a.name.localeCompare(b.name));
  return { path: rel, entries, truncated: raw.length > MAX_LIST_ENTRIES };
}

/** The hub-owned directory where one delivery's handoff and result live, on the other machine. */
export function exchangeDirectory(root: string, rootId: string, deliveryId: string): string {
  const safe = (part: string) => {
    if (!SAFE_ID.test(part)) throw new HttpError(400, '无效的协作标识');
    return part;
  };
  const base = value(root, '交换目录').replace(/\/+$/, '');
  return `${base}/${safe(rootId)}/${safe(deliveryId)}`;
}

function checkExchangeDirectory(dir: string): string {
  const path = value(dir, '交换目录');
  if (!path.startsWith('/')) throw new HttpError(400, '交换目录需要绝对路径');
  for (const part of path.split('/')) {
    if (!part) continue;
    if (part === '.' || part === '..' || !/^[A-Za-z0-9._-]+$/.test(part)) throw new HttpError(400, '交换目录含有无效字符');
  }
  return path;
}

export function handoffFile(dir: string): string {
  return `${checkExchangeDirectory(dir)}/handoff.md`;
}

export function resultFile(dir: string): string {
  return `${checkExchangeDirectory(dir)}/result.md`;
}

export async function writeHandoff(exec: RemoteExec, dir: string, text: string): Promise<void> {
  const path = checkExchangeDirectory(dir);
  if (text.includes('\0')) throw new HttpError(400, '交接内容只能是文本');
  if (Buffer.byteLength(text) > MAX_WRITE_BYTES) throw new HttpError(413, '交接文件太大');
  const run = await exec(WRITE_HANDOFF_SCRIPT, { input: `${path}\n${text}` });
  const got = marker(run);
  if (got.kind !== 'ok') failed(got.kind, got.args, run);
}

export async function readResult(
  exec: RemoteExec,
  dir: string,
  opts: { timeoutMs?: number } = {},
): Promise<{ text: string; size: number; truncated: boolean } | null> {
  const path = checkExchangeDirectory(dir);
  const run = await exec(READ_RESULT_SCRIPT, { input: `${path}\n${MAX_READ_BYTES}\n`, timeoutMs: opts.timeoutMs });
  const got = marker(run);
  if (got.kind === 'not_found') return null;
  if (got.kind !== 'ok') failed(got.kind, got.args, run);
  const size = Number(got.args[0]);
  if (!Number.isSafeInteger(size) || size < 0) throw new HttpError(502, '远端没有返回文件大小');
  const truncated = got.body.length < size;
  return { text: textFrom(got.body, truncated), size, truncated };
}
