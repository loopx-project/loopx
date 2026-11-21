import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildAntiWhitewashingReceiptHeader,
  evaluateEpochPrefetchSwap,
  evaluateQuietTurn,
  planStageThenSwapPrefetch,
  validatePrefetchCommandSafety,
} from '../../loopx/control_plane/quota/stage_then_swap.ts';
import {updateNativeContextLifecycle} from '../../loopx/control_plane/quota/native_context_lifecycle.ts';
import {evaluateTurnEnvelope} from '../../loopx/control_plane/quota/turn_envelope.ts';
import {summarizeTrajectoryCodexUsage} from '../../loopx/control_plane/capabilities/trajectory_codex_usage.ts';

test('stage-then-swap prefetch is default-off with zero behavior drift', () => {
  for (const config of [undefined, null, false, {enabled: false, prefetch_command: ['cat', 'a.txt']}]) {
    const plan = planStageThenSwapPrefetch({epoch_prefetch: config});
    assert.equal(plan.enabled, false);
    assert.equal(plan.eligible, false);
    assert.equal(plan.fallback_operator, 'KeepRaw');
  }
});

test('D8 safety admission enforces read-only allowlist and blocks passive plugins', () => {
  const passive = validatePrefetchCommandSafety({
    prefetch_command: ['loopx', 'quota', 'context-read', '--select', '/summary'],
    initiator: 'history_view_policy',
  });
  assert.equal(passive.allowed, false);
  assert.equal(passive.reason, 'policy_violation:passive_policy_cannot_emit_command');

  for (const unsafeCmd of [
    'git push origin main',
    'rm -rf /tmp/something',
    'bash -c "cat foo"',
    'cat a.txt && echo hi',
    'cat a.txt | grep foo',
    'loopx quota spend-slot --execute',
    'loopx quota context-read --execute',
    'find . -delete',
    ['python', '-c', 'print(1)'],
  ]) {
    const res = validatePrefetchCommandSafety({prefetch_command: unsafeCmd});
    assert.equal(res.allowed, false, `expected unsafe command rejected: ${String(unsafeCmd)}`);
  }

  for (const safeCmd of [
    ['loopx', 'quota', 'context-read', '--capture-file', 'dec.json', '--capture-sha256', 'a'.repeat(64), '--select', '/summary'],
    ['git', 'status'],
    ['cat', 'notes.md'],
    ['head', '-n', '20', 'notes.md'],
    ['find', '.', '-maxdepth', '2'],
  ]) {
    const res = validatePrefetchCommandSafety({prefetch_command: safeCmd});
    assert.equal(res.allowed, true, `expected safe command allowed: ${JSON.stringify(safeCmd)}`);
    assert.ok(res.command_sha256);
  }
});

test('EpochReset builds deterministic anti-whitewashing receipt header with tool trust', () => {
  const sourceSha = 'b'.repeat(64);
  const res = evaluateTurnEnvelope({
    operation: 'stage_then_swap_evaluate',
    epoch_prefetch: {
      enabled: true,
      prefetch_command: ['loopx', 'quota', 'context-read', '--select', '/summary'],
      timeout_ms: 2000,
      max_output_bytes: 1024,
    },
    context_epoch: 'epoch-2',
    reclaimed_turns: 7,
    dropped_failures: 1,
    failure_facts: ['pytest failed exit=2 on test_bar'],
    open_obligations: ['todo_1: fix test_bar'],
    source_ref: {path: '/tmp/capture.json', sha256: sourceSha},
    exit_code: 0,
    timed_out: false,
    truncated: false,
    raw_bytes_read: 64,
    prefetched_output: '{"summary":"narrow repro isolated"}',
    baseline_bytes: 4096,
    latency_ms: 12,
  });

  assert.equal(res.operator, 'EpochReset');
  assert.equal(res.status, 'committed');
  assert.equal(res.trust_level, 'tool');
  assert.equal(res.round_trips_saved, 1);
  assert.ok((res.bytes_saved as number) > 3000);
  const text = String(res.hydrated_text);
  assert.ok(text.includes('[Stage-then-Swap EpochReset Receipt | trust=tool]'));
  assert.ok(text.includes('reclaimed_turns: 7'));
  assert.ok(text.includes('dropped_failures: 1'));
  assert.ok(text.includes('exit_code: 0'));
  assert.ok(text.includes('truncated: false'));
  assert.ok(text.includes('todo_1: fix test_bar'));
  assert.ok(text.includes(`sha256:${sourceSha}`));
  assert.ok(text.endsWith('{"summary":"narrow repro isolated"}'));

  const headerAgain = buildAntiWhitewashingReceiptHeader({
    context_epoch: 'epoch-2',
    reclaimed_turns: 7,
    dropped_failures: 1,
    failure_facts: ['pytest failed exit=2 on test_bar'],
    open_obligations: ['todo_1: fix test_bar'],
    exit_code: 0,
    truncated: false,
    source_ref: {path: '/tmp/capture.json', sha256: sourceSha},
  });
  assert.equal(headerAgain.header, res.header);
});

test('failed, timed-out, policy-denied, truncated-strict, or negative-savings prefetch fails closed to KeepRaw', () => {
  const base = {
    epoch_prefetch: {
      enabled: true,
      prefetch_command: ['cat', 'status.txt'],
      timeout_ms: 500,
      max_output_bytes: 256,
    },
    context_epoch: 'epoch-3',
    reclaimed_turns: 4,
    dropped_failures: 2,
    failure_facts: ['failure-A', 'failure-B'],
    open_obligations: ['obligation-X'],
    source_ref: {path: '/tmp/capture.json', sha256: 'c'.repeat(64)},
    baseline_bytes: 2048,
    latency_ms: 5,
  };

  const failed = evaluateEpochPrefetchSwap({...base, exit_code: 2, prefetched_output: 'partial'});
  assert.equal(failed.operator, 'KeepRaw');
  assert.equal(failed.fallback_reason, 'command_failed');
  assert.equal(failed.preserves_raw_state, true);
  assert.deepEqual(failed.preserved_failure_facts, ['failure-A', 'failure-B']);
  assert.deepEqual(failed.preserved_open_obligations, ['obligation-X']);

  const timedOut = evaluateEpochPrefetchSwap({...base, exit_code: null, timed_out: true});
  assert.equal(timedOut.operator, 'KeepRaw');
  assert.equal(timedOut.fallback_reason, 'timeout');

  const unsafe = evaluateEpochPrefetchSwap({
    ...base,
    epoch_prefetch: {...base.epoch_prefetch, prefetch_command: ['rm', '-rf', '/tmp/nope']},
    exit_code: 0,
  });
  assert.equal(unsafe.operator, 'KeepRaw');
  assert.match(String(unsafe.fallback_reason), /^policy_violation:/);

  const strictTruncated = evaluateEpochPrefetchSwap({
    ...base,
    epoch_prefetch: {...base.epoch_prefetch, reject_on_truncation: true},
    exit_code: 0,
    truncated: true,
    prefetched_output: 'x'.repeat(256),
  });
  assert.equal(strictTruncated.operator, 'KeepRaw');
  assert.equal(strictTruncated.fallback_reason, 'output_truncated');

  const negativeSavings = evaluateEpochPrefetchSwap({
    ...base,
    baseline_bytes: 100,
    exit_code: 0,
    prefetched_output: 'x'.repeat(200),
  });
  assert.equal(negativeSavings.operator, 'KeepRaw');
  assert.equal(negativeSavings.fallback_reason, 'negative_net_savings');
});

test('native lifecycle keeps epoch_prefetch across idempotent PostCompact and clears on new epoch rotation', () => {
  const afterPre = updateNativeContextLifecycle({
    state: {
      session_id: 's1',
      cwd: '/tmp/ws',
      context_epoch: 'epoch-1',
      candidate: {capture_bytes_verified: true},
      epoch_prefetch: {context_epoch: 'epoch-1', operator: 'EpochReset'},
    },
    event: {session_id: 's1', cwd: '/tmp/ws', hook_event_name: 'PreCompact'},
    new_epoch: 'epoch-2',
    pages: [],
  });
  assert.equal(afterPre.context_epoch, 'epoch-2');
  // Candidate was cleared by PreCompact; rotating from epoch-1 -> epoch-2 invalidated stale epoch_prefetch.
  assert.equal('epoch_prefetch' in afterPre, false);

  const populated = {...afterPre, epoch_prefetch: {context_epoch: 'epoch-2', operator: 'EpochReset'}};
  const afterPost = updateNativeContextLifecycle({
    state: populated,
    event: {session_id: 's1', cwd: '/tmp/ws', hook_event_name: 'PostCompact'},
    new_epoch: 'epoch-3',
    pages: [],
  });
  // Idempotent invalidation keeps epoch-2 and preserves epoch-2 prefetch receipt.
  assert.equal(afterPost.context_epoch, 'epoch-2');
  assert.deepEqual(afterPost.epoch_prefetch, {context_epoch: 'epoch-2', operator: 'EpochReset'});
});

test('Quiet-Turn emits zero-tax skip metadata on unchanged state and clears on state change', () => {
  const digest = 'd'.repeat(64);
  const unchanged = evaluateQuietTurn({
    enabled: true,
    quiet_noop_allowed: true,
    current_state_digest: digest,
    previous_state_digest: digest,
  });
  assert.equal(unchanged.skipped, true);
  assert.equal(unchanged.zero_tax, true);
  assert.equal(unchanged.context_delivery_skipped, true);
  assert.equal(unchanged.prefetch_skipped, true);

  const changed = evaluateTurnEnvelope({
    operation: 'quiet_turn_evaluate',
    enabled: true,
    quiet_noop_allowed: true,
    current_state_digest: 'e'.repeat(64),
    previous_state_digest: digest,
  });
  assert.equal(changed.skipped, false);
  assert.equal(changed.zero_tax, false);
});

test('trajectory usage telemetry tracks operator counts, prefetch latency/bytes, and cold/warm KV prefix tokens', () => {
  const usage = (input: number, cached: number, output: number) => ({
    input_tokens: input,
    cached_input_tokens: cached,
    output_tokens: output,
  });
  const events = [
    {type: 'session_meta', payload: {id: 's1'}},
    {type: 'event_msg', payload: {type: 'task_started', turn_id: 't1'}},
    {type: 'event_msg', payload: {type: 'token_count', info: {total_token_usage: usage(100, 0, 10), last_token_usage: usage(100, 0, 10)}}},
    {type: 'event_msg', payload: {type: 'token_count', info: {total_token_usage: usage(220, 90, 20), last_token_usage: usage(120, 90, 10)}}},
    {type: 'event_msg', payload: {type: 'task_complete', turn_id: 't1'}},
  ].map((item, idx) => ({...item, line: idx + 1, timestamp: `2026-11-21T00:00:0${idx}Z`}));

  const baseline = summarizeTrajectoryCodexUsage({events, session_id: 's1'});
  assert.equal('context_policy_telemetry' in baseline, false);

  const enriched = summarizeTrajectoryCodexUsage({
    events,
    session_id: 's1',
    include_prefix_kv_regimes: true,
    context_policy: [
      {operator: 'EpochReset', latency_ms: 14, retained_bytes: 180, bytes_saved: 2400, round_trips_saved: 1},
      {operator: 'KeepRaw', latency_ms: 8, retained_bytes: 0, bytes_saved: 0, quiet_turn_skipped: true},
      {operator: 'FoldToReceipt'},
    ],
  });
  const tel = enriched.context_policy_telemetry!;
  assert.equal(tel.schema_version, 'loopx_context_policy_telemetry_v0');
  assert.deepEqual(tel.operator_counts, {KeepRaw: 1, FoldToReceipt: 1, EpochReset: 1});
  assert.equal(tel.quiet_turn_skips, 1);
  assert.equal(tel.prefetch.latency_ms, 22);
  assert.equal(tel.prefetch.output_bytes, 180);
  assert.equal(tel.prefetch.bytes_saved, 2400);
  assert.equal(tel.prefetch.round_trips_saved, 1);
  assert.deepEqual(tel.prefix_kv?.cold, {requests: 1, uncached_prefix_kv_tokens: 100, cached_prefix_kv_tokens: 0});
  assert.deepEqual(tel.prefix_kv?.warm, {requests: 1, uncached_prefix_kv_tokens: 30, cached_prefix_kv_tokens: 90});
});
