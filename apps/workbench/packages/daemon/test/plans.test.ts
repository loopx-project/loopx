import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { codexPlan, codexQuota } from '../src/adapters/codex.js';
import { ClaudeAcpAdapter, claudePlanName } from '../src/adapters/claude.js';

const here = dirname(fileURLToPath(import.meta.url));

test('Codex names its plans, telling the two Pro plans apart, and passes unknown ones on as they are', () => {
  assert.deepEqual(codexPlan('prolite'), { id: 'prolite', label: 'Pro 5x' });
  assert.deepEqual(codexPlan('pro'), { id: 'pro', label: 'Pro 20x' });
  assert.deepEqual(codexPlan('plus'), { id: 'plus', label: 'Plus' });
  assert.deepEqual(codexPlan('team_plus'), { id: 'team_plus', label: 'Team Plus' }, 'a new one is shown, not dropped or renamed');
  assert.equal(codexPlan(''), undefined);
  assert.equal(codexPlan(null), undefined);
  const limits = { primary: { usedPercent: 9, windowDurationMins: 10080 }, secondary: null };
  // The plan comes with the limits; no second request.
  assert.deepEqual(codexQuota({ rateLimits: { ...limits, planType: 'prolite' } }).plan, { id: 'prolite', label: 'Pro 5x' });
  assert.deepEqual(codexQuota({ rateLimitsByLimitId: { codex: { ...limits, planType: 'plus' } } }).plan, { id: 'plus', label: 'Plus' });
  assert.equal('plan' in codexQuota({ rateLimits: limits }), false, 'no plan reported, none shown');
});

test('Claude names Max by its multiple and the rest by their subscription', () => {
  assert.deepEqual(claudePlanName('max', 'default_claude_max_20x'), { id: 'max · default_claude_max_20x', label: 'Max 20x' });
  assert.deepEqual(claudePlanName('max', 'default_claude_max_5x')?.label, 'Max 5x');
  assert.deepEqual(claudePlanName('max'), { id: 'max', label: 'Max' }, 'without the tier, no multiple is made up');
  assert.deepEqual(claudePlanName('pro', 'default_claude_ai'), { id: 'pro', label: 'Pro' });
  assert.deepEqual(claudePlanName('team'), { id: 'team', label: 'Team' });
  assert.equal(claudePlanName(undefined), undefined);
});

test('Claude reads its plan from the login without carrying the login along, and asks the CLI where there is no file', async () => {
  const home = mkdtempSync(join(tmpdir(), 'calli-claude-plan-'));
  const old = { bin: process.env.CALLI_CLAUDE_QUOTA_BIN, plan: process.env.FAKE_CLAUDE_PLAN };
  process.env.CALLI_CLAUDE_QUOTA_BIN = join(here, 'fixtures', 'fake-claude-usage.sh');
  try {
    const withFile = join(home, '.claude');
    mkdirSync(withFile);
    writeFileSync(join(withFile, '.credentials.json'), JSON.stringify({ claudeAiOauth: {
      accessToken: 'sk-secret-access', refreshToken: 'sk-secret-refresh', subscriptionType: 'max', rateLimitTier: 'default_claude_max_20x',
    } }));
    const quota = await new ClaudeAcpAdapter(['claude'], { CLAUDE_CONFIG_DIR: withFile }).fetchQuota();
    assert.deepEqual(quota.plan, { id: 'max · default_claude_max_20x', label: 'Max 20x' });
    assert.ok(quota.windows.length > 0, 'the limits are read as before');
    assert.doesNotMatch(JSON.stringify(quota), /secret/, 'nothing of the login but the plan');

    process.env.FAKE_CLAUDE_PLAN = 'pro';
    const keychain = join(home, '.claude-keychain');
    mkdirSync(keychain);
    const asked = await new ClaudeAcpAdapter(['claude'], { CLAUDE_CONFIG_DIR: keychain }).fetchQuota();
    assert.deepEqual(asked.plan, { id: 'pro', label: 'Pro' });
  } finally {
    if (old.bin === undefined) delete process.env.CALLI_CLAUDE_QUOTA_BIN; else process.env.CALLI_CLAUDE_QUOTA_BIN = old.bin;
    if (old.plan === undefined) delete process.env.FAKE_CLAUDE_PLAN; else process.env.FAKE_CLAUDE_PLAN = old.plan;
    rmSync(home, { recursive: true, force: true });
  }
});
