// Each test worker and its fake agents use an isolated home, never the developer's home.
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const testHome = mkdtempSync(join(tmpdir(), 'loochat-test-home-'));
process.env.HOME = testHome;
// Tests may run inside an agent session; never let its gateway or credentials reach the fakes.
for (const name of Object.keys(process.env)) {
  if (/^(ANTHROPIC_|CLAUDE_CODE_|CLAUDE_CONFIG_DIR$|XDG_CONFIG_HOME$|GH_CONFIG_DIR$|GIT_CONFIG_GLOBAL$)/.test(name)) delete process.env[name];
}
process.on('exit', () => rmSync(testHome, { recursive: true, force: true }));
