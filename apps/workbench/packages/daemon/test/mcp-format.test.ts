import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { installTomlMcpServer, tomlMcpServers } from '../src/mcp-format.js';

test('TOML writer and reader preserve quoted arguments, env and keys', (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'mcp-format-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, 'config.toml');
  const config = {
    command: 'python',
    args: ['-c', 'print("hello")', '', 'ends with\\', 'slash\\"quote', '\\"""', '\n\t\r\b\f', '你好'],
    env: { CONFIG: '{"name":"hello","path":"C:\\\\tools"}', 'quoted"key': 'value "with quotes"' },
  };
  installTomlMcpServer(path, 'quoted', config);
  const written = readFileSync(path, 'utf8');
  assert.deepEqual(tomlMcpServers(path), [{ name: 'quoted', config, sourcePath: path }]);
  assert.equal(readFileSync(path, 'utf8'), written, 'reading never rewrites the file');
});

test('TOML strings distinguish escaped basic quotes from literal backslashes', (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'mcp-strings-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, 'config.toml');
  writeFileSync(path, String.raw`[mcp_servers.quoted]
command = 'python'
args = ["escaped \"quote\"", 'literal\', "even\\", "unicode \u0022"]
basic = """a\"""b"""
literal = '''a\
b'''
`);
  assert.deepEqual(tomlMcpServers(path)[0].config, {
    command: 'python', args: ['escaped "quote"', 'literal\\', 'even\\', 'unicode "'],
    basic: 'a"""b', literal: 'a\\\nb',
  });
});
