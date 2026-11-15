import { mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import type { McpServerInfo } from './adapters/types.js';
import { HttpError } from './errors.js';

/**
 * The three MCP config layouts the hub reads and, one entry at a time, writes back: the
 * `{"mcpServers": {…}}` JSON of Claude Code and pi, and the `[mcp_servers.<name>]` tables of
 * Codex's config.toml. Everything an entry says travels as the raw server object, so fields one
 * agent adds around `command`/`args`/`env` or `url` survive a reinstall unchanged.
 *
 * Writes are surgical: a JSON file keeps every other key, a TOML file keeps every other byte, and
 * both land through a same-directory temp file so a half write never replaces the real config.
 */

/** What an MCP server may be called where all three agents agree: no dots, so a TOML sub-table can
 * never be mistaken for part of the name. */
export const MCP_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

/** A server config bigger than this is not something to hand around. */
const MAX_CONFIG_BYTES = 64 * 1024;

function isPlain(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** A server entry as the page may save it: a local command or an http(s) endpoint, with optional
 * string args and env. Anything else in the object is kept but not interpreted. */
export function checkMcpConfig(config: unknown): Record<string, unknown> {
  if (!isPlain(config)) throw new HttpError(400, 'MCP 配置须是对象');
  const { command, url, args, env } = config;
  const hasCommand = typeof command === 'string' && command.trim() !== '';
  const hasUrl = typeof url === 'string' && /^https?:\/\//.test(url);
  if (!hasCommand && !hasUrl) throw new HttpError(400, 'MCP 配置须带 command 或 http(s) url');
  if (args !== undefined && (!Array.isArray(args) || args.some((a) => typeof a !== 'string'))) throw new HttpError(400, 'args 须是字符串数组');
  if (env !== undefined && (!isPlain(env) || Object.values(env).some((v) => typeof v !== 'string'))) throw new HttpError(400, 'env 须是字符串到字符串的对象');
  if (JSON.stringify(config).length > MAX_CONFIG_BYTES) throw new HttpError(413, 'MCP 配置超过 64 KB');
  return JSON.parse(JSON.stringify(config)) as Record<string, unknown>;
}

/** Same bytes, key order forgotten, so "same server" does not depend on who wrote the file. */
export function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  if (isPlain(value)) {
    const keys = Object.keys(value).sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${stableStringify(value[k])}`).join(',')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}

/** Write through a temp file in the same directory, keeping the original mode; new configs are
 * private, the way the agents themselves write them. */
function writeAtomic(path: string, content: string): void {
  mkdirSync(dirname(path), { recursive: true });
  let mode = 0o600;
  try {
    mode = statSync(path).mode & 0o777;
  } catch {
    // Not there yet: the default above applies.
  }
  const tmp = `${path}.tmp-${process.pid}-${Math.random().toString(36).slice(2)}`;
  writeFileSync(tmp, content, { encoding: 'utf8', mode });
  renameSync(tmp, path);
}

function readText(path: string): string | null {
  try {
    return readFileSync(path, 'utf8');
  } catch {
    return null;
  }
}

// ---------- The `{"mcpServers": {…}}` JSON of Claude Code and pi ----------

function readJsonDoc(path: string, forWrite: boolean): Record<string, unknown> {
  const text = readText(path);
  if (text === null) return {};
  try {
    const doc = JSON.parse(text) as unknown;
    if (isPlain(doc)) return doc;
    if (forWrite) throw new HttpError(500, `${path} 不是 JSON 对象，未改动`);
    return {};
  } catch (e) {
    if (e instanceof HttpError) throw e;
    if (forWrite) throw new HttpError(500, `${path} 暂时无法解析（可能正在被写入），未改动`);
    return {};
  }
}

export function jsonMcpServers(path: string): McpServerInfo[] {
  const servers = readJsonDoc(path, false).mcpServers;
  if (!isPlain(servers)) return [];
  return Object.entries(servers)
    .filter(([, config]) => isPlain(config))
    .map(([name, config]) => ({ name, config: config as Record<string, unknown>, sourcePath: path }));
}

export function installJsonMcpServer(path: string, name: string, config: Record<string, unknown>): void {
  const doc = readJsonDoc(path, true);
  const servers = isPlain(doc.mcpServers) ? doc.mcpServers : {};
  doc.mcpServers = { ...servers, [name]: config };
  writeAtomic(path, `${JSON.stringify(doc, null, 2)}\n`);
}

export function removeJsonMcpServer(path: string, name: string): boolean {
  const doc = readJsonDoc(path, true);
  const servers = isPlain(doc.mcpServers) ? doc.mcpServers : null;
  if (!servers || !(name in servers)) return false;
  delete servers[name];
  doc.mcpServers = servers;
  writeAtomic(path, `${JSON.stringify(doc, null, 2)}\n`);
  return true;
}

// ---------- The `[mcp_servers.<name>]` tables of Codex's config.toml ----------
//
// TOML is read with a parser kept to the shapes MCP tables use — strings, numbers, booleans,
// arrays, inline tables and dotted sub-tables — and written as whole blocks: a server's tables are
// located as byte ranges and replaced, so every other line of config.toml is left as it stood.

interface LineRange {
  start: number;
  end: number;
  text: string;
}

function lineRanges(text: string): LineRange[] {
  const out: LineRange[] = [];
  let pos = 0;
  while (pos <= text.length) {
    const nl = text.indexOf('\n', pos);
    const end = nl === -1 ? text.length : nl;
    out.push({ start: pos, end, text: text.slice(pos, end).replace(/\r$/, '') });
    if (nl === -1) break;
    pos = nl + 1;
  }
  return out;
}

/** A line that opens any table (`[a.b]`, `[[a]]`): where the previous table's body ends. */
function isTableBoundary(line: string): boolean {
  return /^\s*\[/.test(line);
}

/** A table header (`[a.b]`) split into its path segments; null for anything else. */
function parseHeader(line: string): string[] | null {
  const trimmed = line.trim();
  if (!trimmed.startsWith('[') || trimmed.startsWith('[[')) return null;
  const match = /^\[([^\]]*)\](?:#.*)?$/.exec(trimmed);
  if (!match) return null;
  return splitKeyPath(match[1]);
}

/** Split `a.b` / `"a.b".c` on dots, quotes respected. */
function splitKeyPath(text: string): string[] | null {
  const segments: string[] = [];
  let pos = 0;
  while (pos < text.length) {
    while (pos < text.length && /[\s.]/.test(text[pos])) pos++;
    if (pos >= text.length) break;
    const quote = text[pos];
    let segment: string;
    if (quote === '"' || quote === "'") {
      const close = text.indexOf(quote, pos + 1);
      if (close === -1) return null;
      segment = text.slice(pos + 1, close);
      pos = close + 1;
    } else {
      const match = /^[^\s.]+/.exec(text.slice(pos));
      if (!match) return null;
      segment = match[0];
      pos += match[0].length;
    }
    segments.push(segment);
  }
  return segments.length ? segments : null;
}

function skipSpace(text: string, pos: number, end: number, newlines: boolean): number {
  while (pos < end) {
    const c = text[pos];
    if (c === ' ' || c === '\t' || c === '\r' || (newlines && c === '\n')) pos++;
    else break;
  }
  return pos;
}

function skipLine(text: string, pos: number, end: number): number {
  const nl = text.indexOf('\n', pos);
  return nl === -1 || nl >= end ? end : nl + 1;
}

function parseBasicString(body: string): string {
  return body.replace(/\\(?:(["\\])|([btnfr])|(?:u([0-9a-fA-F]{4}))|(?:U([0-9a-fA-F]{8})))/g, (_, q, ctrl, u, U) => {
    if (q) return q;
    if (ctrl) return { b: '\b', t: '\t', n: '\n', f: '\f', r: '\r' }[ctrl as string] ?? ctrl;
    return String.fromCodePoint(parseInt(u ?? U, 16));
  });
}

/** One TOML value; `end` is where parsing stopped. Null where the shape is not one we read. */
function parseTomlValue(text: string, pos: number, limit: number): { value: unknown; end: number } | null {
  if (pos >= limit) return null;
  const c = text[pos];
  if (c === '"' || c === "'") {
    const triple = text.startsWith(c.repeat(3), pos);
    if (triple) {
      const close = text.indexOf(c.repeat(3), pos + 3);
      if (close === -1 || close >= limit) return null;
      const body = text.slice(pos + 3, close).replace(/^[ \t]*\r?\n/, '').replace(/\\\r?\n[ \t]*/g, '');
      return { value: c === '"' ? parseBasicString(body) : body, end: close + 3 };
    }
    const escaped = text.indexOf(c, pos + 1);
    if (escaped === -1 || escaped >= limit) return null;
    const body = text.slice(pos + 1, escaped);
    return { value: c === '"' ? parseBasicString(body) : body, end: escaped + 1 };
  }
  if (c === '[') {
    pos = skipSpace(text, pos + 1, limit, true);
    const items: unknown[] = [];
    while (pos < limit) {
      if (text[pos] === '#') {
        pos = skipLine(text, pos, limit);
        continue;
      }
      if (text[pos] === ']') return { value: items, end: pos + 1 };
      const item = parseTomlValue(text, pos, limit);
      if (!item) return null;
      items.push(item.value);
      pos = skipSpace(text, item.end, limit, true);
      if (text[pos] === ',') pos = skipSpace(text, pos + 1, limit, true);
    }
    return null;
  }
  if (c === '{') {
    pos = skipSpace(text, pos + 1, limit, false);
    const table: Record<string, unknown> = {};
    while (pos < limit && text[pos] !== '}') {
      const key = readKeyPath(text, pos, limit);
      if (!key) return null;
      pos = skipSpace(text, key.end, limit, false);
      if (text[pos] !== '=') return null;
      pos = skipSpace(text, pos + 1, limit, false);
      const value = parseTomlValue(text, pos, limit);
      if (!value) return null;
      setAtPath(table, key.segments, value.value);
      pos = skipSpace(text, value.end, limit, false);
      if (text[pos] === ',') pos = skipSpace(text, pos + 1, limit, false);
    }
    return pos < limit ? { value: table, end: pos + 1 } : null;
  }
  const rest = text.slice(pos, limit);
  if (rest.startsWith('true')) return { value: true, end: pos + 4 };
  if (rest.startsWith('false')) return { value: false, end: pos + 5 };
  const number = /^[+-]?(?:0x[0-9a-fA-F_]+|0o[0-7_]+|0b[01_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d[\d_]*)?|inf|nan)/.exec(rest);
  if (number) {
    const raw = number[0].replace(/_/g, '');
    const asNumber = Number(raw);
    return { value: Number.isNaN(asNumber) ? raw : asNumber, end: pos + number[0].length };
  }
  return null;
}

function readKeyPath(text: string, pos: number, limit: number): { segments: string[]; end: number } | null {
  const segments: string[] = [];
  for (;;) {
    pos = skipSpace(text, pos, limit, false);
    const c = text[pos];
    if (c === '"' || c === "'") {
      const close = text.indexOf(c, pos + 1);
      if (close === -1 || close >= limit) return null;
      segments.push(text.slice(pos + 1, close));
      pos = close + 1;
    } else {
      const match = /^[^\s=,.{}[\]]+/.exec(text.slice(pos, limit));
      if (!match) return null;
      segments.push(match[0]);
      pos += match[0].length;
    }
    const after = skipSpace(text, pos, limit, false);
    if (text[after] === '.') {
      pos = after + 1;
      continue;
    }
    return { segments, end: pos };
  }
}

function setAtPath(target: Record<string, unknown>, path: string[], value: unknown): void {
  let at = target;
  for (const segment of path.slice(0, -1)) {
    if (!isPlain(at[segment])) at[segment] = {};
    at = at[segment] as Record<string, unknown>;
  }
  at[path[path.length - 1]] = value;
}

/** The `key = value` lines of one table body, as a nested object. */
function parseTomlBody(text: string, start: number, end: number): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  let pos = start;
  while (pos < end) {
    pos = skipSpace(text, pos, end, true);
    if (pos >= end) break;
    if (text[pos] === '#' || text[pos] === '\n') {
      pos = skipLine(text, pos, end);
      continue;
    }
    const key = readKeyPath(text, pos, end);
    if (!key) {
      pos = skipLine(text, pos, end);
      continue;
    }
    pos = skipSpace(text, key.end, end, false);
    if (text[pos] !== '=') {
      pos = skipLine(text, pos, end);
      continue;
    }
    const value = parseTomlValue(text, skipSpace(text, pos + 1, end, false), end);
    if (!value) {
      pos = skipLine(text, pos, end);
      continue;
    }
    setAtPath(out, key.segments, value.value);
    pos = value.end;
  }
  return out;
}

interface TomlBlock {
  name: string;
  /** The path under the server table, `[]` for the table itself, `['env']` for a sub-table. */
  sub: string[];
  bodyStart: number;
  bodyEnd: number;
}

function tomlMcpBlocks(text: string): TomlBlock[] {
  const blocks: TomlBlock[] = [];
  const lines = lineRanges(text);
  let current: TomlBlock | null = null;
  for (const line of lines) {
    if (!isTableBoundary(line.text)) continue;
    if (current) current.bodyEnd = line.start;
    current = null;
    const header = parseHeader(line.text);
    if (header && header[0] === 'mcp_servers' && header.length >= 2 && MCP_NAME.test(header[1])) {
      current = { name: header[1], sub: header.slice(2), bodyStart: line.end + 1, bodyEnd: text.length };
      blocks.push(current);
    }
  }
  return blocks;
}

export function tomlMcpServers(path: string): McpServerInfo[] {
  const text = readText(path);
  if (text === null) return [];
  const servers = new Map<string, Record<string, unknown>>();
  for (const block of tomlMcpBlocks(text)) {
    let server = servers.get(block.name);
    if (!server) {
      server = {};
      servers.set(block.name, server);
    }
    for (const [key, value] of Object.entries(parseTomlBody(text, block.bodyStart, block.bodyEnd))) {
      setAtPath(server, [...block.sub, key], value);
    }
  }
  return [...servers].map(([name, config]) => ({ name, config, sourcePath: path }));
}

function tomlBasicString(value: string): string {
  return `"${value.replace(/[\\"\b\t\n\f\r]/g, (c) => ({ '"': '\\"', '\\': '\\\\', '\b': '\\b', '\t': '\\t', '\n': '\\n', '\f': '\\f', '\r': '\\r' })[c] ?? c)}"`;
}

function tomlKey(key: string): string {
  return /^[A-Za-z0-9_-]+$/.test(key) ? key : tomlBasicString(key);
}

function tomlValue(value: unknown): string {
  if (typeof value === 'string') return tomlBasicString(value);
  if (typeof value === 'number') return String(value);
  if (typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.length ? `[ ${value.map(tomlValue).join(', ')} ]` : '[]';
  if (isPlain(value)) return `{ ${Object.entries(value).map(([k, v]) => `${tomlKey(k)} = ${tomlValue(v)}`).join(', ')} }`;
  throw new HttpError(400, 'MCP 配置里有此格式写不下的内容');
}

/** A server as one `[mcp_servers.<name>]` table plus a sub-table per nested object. */
function serializeTomlServer(name: string, config: Record<string, unknown>): string {
  const sections: string[] = [];
  const queue: Array<{ path: string[]; body: Record<string, unknown> }> = [{ path: [], body: config }];
  while (queue.length) {
    const { path, body } = queue.shift()!;
    const lines = [`[${['mcp_servers', name, ...path].join('.')}]`];
    for (const [key, value] of Object.entries(body)) {
      if (isPlain(value)) {
        if (Object.keys(value).length) queue.push({ path: [...path, key], body: value });
        else lines.push(`${tomlKey(key)} = {}`);
      } else if (value !== undefined && value !== null) {
        lines.push(`${tomlKey(key)} = ${tomlValue(value)}`);
      }
    }
    sections.push(lines.join('\n'));
  }
  return sections.join('\n\n');
}

/** The byte ranges a server's tables occupy: its header line through the line before the next
 * header, for the table itself and every sub-table, in file order. */
function serverRanges(text: string, name: string): Array<[number, number]> {
  const ranges: Array<[number, number]> = [];
  const lines = lineRanges(text);
  let current: [number, number] | null = null;
  for (const line of lines) {
    if (!isTableBoundary(line.text)) continue;
    if (current) current[1] = line.start;
    current = null;
    const header = parseHeader(line.text);
    if (header && header[0] === 'mcp_servers' && header[1] === name && header.length >= 2) {
      current = [line.start, text.length];
      ranges.push(current);
    }
  }
  return ranges;
}

export function installTomlMcpServer(path: string, name: string, config: Record<string, unknown>): void {
  const block = serializeTomlServer(name, config);
  const text = readText(path) ?? '';
  const ranges = serverRanges(text, name);
  let out: string;
  if (!ranges.length) {
    const gap = text.length === 0 ? '' : text.endsWith('\n') ? '\n' : '\n\n';
    out = `${text}${gap}${block}\n`;
  } else {
    const first = ranges[0][0];
    let kept = '';
    let cursor = 0;
    for (const [start, end] of ranges) {
      kept += text.slice(cursor, start);
      cursor = end;
    }
    kept += text.slice(cursor);
    out = `${kept.slice(0, first)}${block}\n${kept.slice(first)}`;
  }
  writeAtomic(path, out);
}

export function removeTomlMcpServer(path: string, name: string): boolean {
  const text = readText(path);
  if (text === null) return false;
  const ranges = serverRanges(text, name);
  if (!ranges.length) return false;
  let out = '';
  let cursor = 0;
  for (const [start, end] of ranges) {
    out += text.slice(cursor, start);
    cursor = end;
  }
  out += text.slice(cursor);
  writeAtomic(path, out.replace(/\n{3,}$/, '\n\n'));
  return true;
}
