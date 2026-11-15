/** Shared test helpers: fixture loading. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

/**
 * In-app demo fixture (main-agent ruling): a copy of LoopX
 * examples/status.example.json plus synthetic needs-you / running / error
 * entries, so offline review sees all three triage groups without any network
 * or cross-repo path.
 */
export const FIXTURE_PATH =
  join(import.meta.dirname, '..', 'src', 'loopx', 'fixtures', 'status.demo.json');

export function loadFixture(): unknown {
  return JSON.parse(readFileSync(FIXTURE_PATH, 'utf8'));
}
