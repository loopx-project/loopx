import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { installTomlMcpServer, removeTomlMcpServer, tomlMcpServers } from '../src/mcp-format.js';

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

test('TOML table headers with spaces and trailing comments are recognized and replaced cleanly', (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'mcp-comments-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, 'config.toml');
  const initial = `model = "gpt-5.2-codex"

[ mcp_servers . example ] # local MCP
command = "node"
args = ["old.js"]

[mcp_servers.example.env]\t# local env
MODE = "dev"

[[other.items]] # unrelated array table
name = "keep"
`;
  writeFileSync(path, initial);
  assert.deepEqual(tomlMcpServers(path), [
    { name: 'example', config: { command: 'node', args: ['old.js'], env: { MODE: 'dev' } }, sourcePath: path },
  ]);

  installTomlMcpServer(path, 'example', { command: 'node', args: ['new.js'], env: { MODE: 'prod' } });
  const updated = readFileSync(path, 'utf8');
  assert.equal((updated.match(/\[mcp_servers\.example\]/g) ?? []).length, 1, 'exactly one server table remains');
  assert.ok(!updated.includes('old.js'), 'previous table body is replaced');
  assert.ok(updated.includes('[[other.items]] # unrelated array table'), 'unrelated tables remain intact');
  assert.deepEqual(tomlMcpServers(path), [
    { name: 'example', config: { command: 'node', args: ['new.js'], env: { MODE: 'prod' } }, sourcePath: path },
  ]);

  const py = spawnSync('python3', ['-c', 'import sys, tomllib, json; print(json.dumps(tomllib.load(open(sys.argv[1], "rb"))))', path], { encoding: 'utf8' });
  if (py.status === 0) {
    const parsed = JSON.parse(py.stdout);
    assert.deepEqual(parsed.mcp_servers.example, { command: 'node', args: ['new.js'], env: { MODE: 'prod' } });
  }

  assert.equal(removeTomlMcpServer(path, 'example'), true);
  assert.deepEqual(tomlMcpServers(path), []);
});
