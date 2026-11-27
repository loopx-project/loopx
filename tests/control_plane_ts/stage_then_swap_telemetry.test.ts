import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildAntiWhitewashingReceiptHeader,
  buildSemanticUnitFoldSwap,
  computeQuietTurnStateDigest,
  evaluateEpochPrefetchPromptGate,
  evaluateEpochPrefetchSwap,
  evaluateQuietTurn,
  planStageThenSwapPrefetch,
  recordSemanticUnitAnchor,
  resolvePlanQuietNoopEligibility,
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
    ['git', 'branch', 'evil-branch-created'],
    ['git', 'branch', '--track', 'evil', 'main'],
    ['git', 'branch', '-u', 'origin/main'],
    ['git', 'log', '--output=/tmp/evil-log.txt', '-n', '1'],
    ['git', 'diff', '--output', '/tmp/evil-diff.txt'],
    ['git', 'show', '--output=/tmp/evil-show.txt', 'HEAD'],
    ['git', 'diff', '--ext-diff'],
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
    ['git', 'branch'],
    ['git', 'branch', '--list'],
    ['git', 'branch', '--show-current'],
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
    raw_bytes_read: 192,
    prefetched_output: JSON.stringify({
      ok: true,
      schema_version: 'context_capture_page_v1',
      sha256: sourceSha,
      offset: 0,
      next_offset: null,
      total_bytes: Buffer.byteLength('{"summary":"narrow repro isolated"}', 'utf8'),
      eof: true,
      content: '{"summary":"narrow repro isolated"}',
    }),
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
  assert.ok(text.includes('narrow repro isolated'));

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
    source_ref: null,
    baseline_bytes: 100,
    exit_code: 0,
    prefetched_output: 'x'.repeat(200),
  });
  assert.equal(negativeSavings.operator, 'KeepRaw');
  assert.equal(negativeSavings.fallback_reason, 'negative_net_savings');

  const incompletePage = evaluateEpochPrefetchSwap({
    ...base,
    exit_code: 0,
    truncated: false,
    prefetched_output: JSON.stringify({
      ok: true,
      schema_version: 'context_capture_page_v1',
      eof: false,
      content: 'partial-page',
    }),
  });
  assert.equal(incompletePage.operator, 'KeepRaw');
  assert.equal(incompletePage.fallback_reason, 'incomplete_output');
  assert.equal(incompletePage.truncated, true);
  assert.equal(incompletePage.preserves_raw_state, true);
  assert.equal(incompletePage.round_trips_saved, 0);
  assert.equal(incompletePage.bytes_saved, 0);
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
    {type: 'compacted', payload: {reason: 'epoch_reset'}},
    {type: 'event_msg', payload: {type: 'token_count', info: {total_token_usage: usage(300, 130, 25), last_token_usage: usage(80, 40, 5)}}},
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
  // Request 1 (100/0) and post-compacted Request 3 (80/40) both bucket cold; Request 2 (120/90) buckets warm.
  assert.deepEqual(tel.prefix_kv?.cold, {requests: 2, uncached_prefix_kv_tokens: 140, cached_prefix_kv_tokens: 40});
  assert.deepEqual(tel.prefix_kv?.warm, {requests: 1, uncached_prefix_kv_tokens: 30, cached_prefix_kv_tokens: 90});
});

test("D8 safety admission rejects attached -O flag on git grep", () => {
  const attached = validatePrefetchCommandSafety({prefetch_command: ["git", "grep", "-Otouch /tmp/git-grep-o-poc", "loopx"]});
  assert.equal(attached.allowed, false);
  assert.equal(attached.reason, "policy_violation:git_output_or_external_diff_forbidden");
});

test("evaluateEpochPrefetchSwap enforces offset === 0, contiguous length, and source sha256 on capture pages", () => {
  const digest = "c".repeat(64);
  const base = {
    epoch_prefetch: {enabled: true, prefetch_command: ["loopx", "--format", "json", "quota", "context-read"]},
    context_epoch: "epoch-page-guard",
    reclaimed_turns: 4,
    failure_facts: ["build failed"],
    open_obligations: ["keep failing tests"],
    source_ref: {path: "/tmp/capture.json", sha256: digest},
    baseline_bytes: 14_175,
    latency_ms: 5,
    exit_code: 0,
    truncated: false,
  };

  // Tail slice (--offset > 0) with eof=true MUST fail closed to KeepRaw rather than truncating prefix!
  const nonZeroOffset = evaluateEpochPrefetchSwap({
    ...base,
    prefetched_output: JSON.stringify({
      ok: true,
      schema_version: "context_capture_page_v1",
      sha256: digest,
      offset: 14095,
      next_offset: null,
      total_bytes: 14175,
      eof: true,
      content: "t".repeat(80),
    }),
  });
  assert.equal(nonZeroOffset.operator, "KeepRaw");
  assert.equal(nonZeroOffset.fallback_reason, "incomplete_output");
  assert.equal(nonZeroOffset.truncated, true);
  assert.equal(nonZeroOffset.preserves_raw_state, true);
  assert.equal(nonZeroOffset.round_trips_saved, 0);
  assert.equal(nonZeroOffset.bytes_saved, 0);

  // Content byte count < total_bytes MUST also fail closed.
  const truncatedContent = evaluateEpochPrefetchSwap({
    ...base,
    prefetched_output: JSON.stringify({
      ok: true,
      schema_version: "context_capture_page_v1",
      sha256: digest,
      offset: 0,
      next_offset: null,
      total_bytes: 500,
      eof: true,
      content: "short",
    }),
  });
  assert.equal(truncatedContent.operator, "KeepRaw");
  assert.equal(truncatedContent.fallback_reason, "incomplete_output");

  // Mismatched sha256 vs source_ref.sha256 MUST fail closed.
  const wrongDigest = evaluateEpochPrefetchSwap({
    ...base,
    prefetched_output: JSON.stringify({
      ok: true,
      schema_version: "context_capture_page_v1",
      sha256: "b".repeat(64),
      offset: 0,
      next_offset: null,
      total_bytes: 8,
      eof: true,
      content: "full-cap",
    }),
  });
  assert.equal(wrongDigest.operator, "KeepRaw");
  assert.equal(wrongDigest.fallback_reason, "source_digest_mismatch");

  // Complete offset=0, next_offset=null, matching total_bytes and sha256 commits EpochReset cleanly.
  const validFull = evaluateEpochPrefetchSwap({
    ...base,
    prefetched_output: JSON.stringify({
      ok: true,
      schema_version: "context_capture_page_v1",
      sha256: digest,
      offset: 0,
      next_offset: null,
      total_bytes: 8,
      eof: true,
      content: "full-cap",
    }),
  });
  assert.equal(validFull.operator, "EpochReset");
  assert.equal(validFull.status, "committed");
  assert.equal(validFull.round_trips_saved, 1);
  assert.ok(Number(validFull.bytes_saved) > 0);

  // Non-JSON capture tail (mdTail) or unpaginated JSON with bound source_ref / quota context-read
  // MUST fail closed identically to jsonTail (KeepRaw incomplete_output, saved=0, round_trips_saved=0).
  const mdTail = evaluateEpochPrefetchSwap({
    ...base,
    prefetched_output: "# Capture tail markdown\n...tail bytes only...",
  });
  assert.equal(mdTail.operator, "KeepRaw");
  assert.equal(mdTail.fallback_reason, "incomplete_output");
  assert.equal(mdTail.truncated, true);
  assert.equal(mdTail.round_trips_saved, 0);
  assert.equal(mdTail.bytes_saved, 0);

  const unpaginatedJson = evaluateEpochPrefetchSwap({
    ...base,
    prefetched_output: JSON.stringify({ok: true, summary: "missing page metadata"}),
  });
  assert.equal(unpaginatedJson.operator, "KeepRaw");
  assert.equal(unpaginatedJson.fallback_reason, "incomplete_output");
  assert.equal(unpaginatedJson.round_trips_saved, 0);
  assert.equal(unpaginatedJson.bytes_saved, 0);

  // prefetch_command: null without source_ref MUST fail closed cleanly instead of throwing TypeError.
  const nullCmd = evaluateEpochPrefetchSwap({
    ...base,
    source_ref: null,
    epoch_prefetch: {enabled: true, prefetch_command: null},
    prefetched_output: "",
  });
  assert.equal(nullCmd.operator, "KeepRaw");
  assert.equal(nullCmd.fallback_reason, "policy_violation:missing_command");
  assert.equal(nullCmd.round_trips_saved, 0);
  assert.equal(nullCmd.bytes_saved, 0);

  // String quota context-read command without source_ref MUST still enforce verified typed pagination.
  const stringCmdMdTail = evaluateEpochPrefetchSwap({
    ...base,
    source_ref: null,
    epoch_prefetch: {enabled: true, prefetch_command: "loopx --format json quota context-read"},
    prefetched_output: "# Capture tail markdown\n...tail bytes only...",
  });
  assert.equal(stringCmdMdTail.operator, "KeepRaw");
  assert.equal(stringCmdMdTail.fallback_reason, "incomplete_output");
  assert.equal(stringCmdMdTail.truncated, true);
  assert.equal(stringCmdMdTail.round_trips_saved, 0);
  assert.equal(stringCmdMdTail.bytes_saved, 0);
});

test("Quiet-Turn enforces canonical work_contract and legacy turn_envelope precedence over caller flags", () => {
  const digest = "f".repeat(64);
  // 1. Canonical v1 work_contract with mandatory work blocks skipping even if quiet_noop_allowed=true is requested.
  const v1Mandatory = {
    interaction_contract: {agent_channel: {must_attempt: true, delivery_allowed: true, quiet_noop_allowed: false}},
  };
  assert.deepEqual(resolvePlanQuietNoopEligibility({work_contract: v1Mandatory}), {
    must_attempt: true,
    quiet_noop_allowed: false,
    source: "work_contract",
  });
  const blockedV1 = evaluateQuietTurn({
    enabled: true,
    quiet_noop_allowed: true,
    current_state_digest: digest,
    previous_state_digest: digest,
    work_contract: v1Mandatory,
  });
  assert.equal(blockedV1.skipped, false);
  assert.equal(blockedV1.quiet_noop_allowed, false);
  assert.equal(blockedV1.reason, "quiet_noop_disallowed");

  // 2. Legacy v0 turn_envelope with mandatory work also refuses skipping even with quiet_noop_allowed=true.
  const v0Mandatory = {
    interaction_contract: {agent_channel: {must_attempt: true, delivery_allowed: false, quiet_noop_allowed: false}},
  };
  assert.deepEqual(resolvePlanQuietNoopEligibility({turn_envelope: v0Mandatory}), {
    must_attempt: true,
    quiet_noop_allowed: false,
    source: "turn_envelope",
  });
  const blockedV0 = evaluateQuietTurn({
    enabled: true,
    quiet_noop_allowed: true,
    current_state_digest: digest,
    previous_state_digest: digest,
    turn_envelope: v0Mandatory,
  });
  assert.equal(blockedV0.skipped, false);
  assert.equal(blockedV0.quiet_noop_allowed, false);

  // 3. Idle v1 work_contract (must_attempt=false, quiet_noop_allowed=true) permits unchanged skip, while caller cap false restricts.
  const v1Idle = {
    interaction_contract: {agent_channel: {must_attempt: false, delivery_allowed: false, quiet_noop_allowed: true}},
  };
  const skippedIdle = evaluateQuietTurn({
    enabled: true,
    quiet_noop_allowed: true,
    current_state_digest: digest,
    previous_state_digest: digest,
    work_contract: v1Idle,
  });
  assert.equal(skippedIdle.skipped, true);
  assert.equal(skippedIdle.reason, "unchanged_state");

  const restrictedIdle = evaluateQuietTurn({
    enabled: true,
    quiet_noop_allowed: false,
    current_state_digest: digest,
    previous_state_digest: digest,
    work_contract: v1Idle,
  });
  assert.equal(restrictedIdle.skipped, false);
  assert.equal(restrictedIdle.quiet_noop_allowed, false);

  // 4. Empty contract fails closed (does not default quiet_noop_allowed to true).
  const emptyContract = evaluateQuietTurn({
    enabled: true,
    quiet_noop_allowed: true,
    current_state_digest: digest,
    previous_state_digest: digest,
    work_contract: {},
  });
  assert.equal(emptyContract.skipped, false);
  assert.equal(emptyContract.quiet_noop_allowed, false);
});

test("STAGE-THEN-SWAP-NET-SAVINGS token & cold-prefill gate rejects byte-shrink token-growth counterfactuals", () => {
  const tokenizer = {name: "tiktoken", version: "0.12.0", encoding: "o200k_base"};

  // 1. Exact counterfactual: prompt UTF-8 bytes shrink (25,534 -> 23,182 B) while tokens grow (5,434 -> 5,878).
  const tokenGrowth = evaluateEpochPrefetchPromptGate({
    has_inline_exploratory: true,
    replace_exploratory_context: true,
    rendered_prompt_baseline_bytes: 25_534,
    rendered_prompt_delivered_bytes: 23_182,
    rendered_prompt_baseline_tokens: 5_434,
    rendered_prompt_delivered_tokens: 5_878,
    potential_bytes_saved: 2_352,
    tokenizer,
  });
  assert.equal(tokenGrowth.operator, "KeepRaw");
  assert.equal(tokenGrowth.fallback_reason, "negative_net_savings");
  assert.equal(tokenGrowth.bytes_saved, 0);
  assert.equal(tokenGrowth.round_trips_saved, 0);
  assert.equal(tokenGrowth.net_savings_tokens, -444);

  // 2. Small raw token savings (+100) that fail cold-prefill amortization on 5,400-token prompt -> KeepRaw.
  const failsColdPrefill = evaluateEpochPrefetchPromptGate({
    has_inline_exploratory: true,
    replace_exploratory_context: true,
    rendered_prompt_baseline_bytes: 26_000,
    rendered_prompt_delivered_bytes: 23_500,
    rendered_prompt_baseline_tokens: 5_500,
    rendered_prompt_delivered_tokens: 5_400,
    potential_bytes_saved: 2_500,
    tokenizer,
  });
  assert.equal(failsColdPrefill.operator, "KeepRaw");
  assert.equal(failsColdPrefill.fallback_reason, "negative_net_savings");
  assert.equal(failsColdPrefill.net_savings_tokens, 100);
  assert.equal(failsColdPrefill.cold_prefill_amortization_tokens, 270);
  assert.equal(failsColdPrefill.amortized_net_savings_tokens, -170);

  // 3. Missing tokenizer provenance / token counts fail closed to KeepRaw.
  const missingTokens = evaluateEpochPrefetchSwap({
    epoch_prefetch: {enabled: true, prefetch_command: ["cat", "summary.txt"]},
    context_epoch: "epoch-tok-missing",
    baseline_bytes: 8_192,
    exit_code: 0,
    prefetched_output: "short",
    require_token_gate: true,
  });
  assert.equal(missingTokens.operator, "KeepRaw");
  assert.equal(missingTokens.fallback_reason, "token_estimation_unavailable");

  // 4. Genuine net token savings clearing cold-prefill amortization + min_net_savings_tokens commits EpochReset.
  const genuineSavings = evaluateEpochPrefetchPromptGate({
    has_inline_exploratory: true,
    replace_exploratory_context: true,
    rendered_prompt_baseline_bytes: 28_000,
    rendered_prompt_delivered_bytes: 22_000,
    rendered_prompt_baseline_tokens: 6_800,
    rendered_prompt_delivered_tokens: 5_100,
    potential_bytes_saved: 6_000,
    tokenizer,
  });
  assert.equal(genuineSavings.operator, "EpochReset");
  assert.equal(genuineSavings.status, "committed");
  assert.equal(genuineSavings.bytes_saved, 6_000);
  assert.equal(genuineSavings.net_savings_tokens, 1_700);
  assert.equal(genuineSavings.cold_prefill_amortization_tokens, 255);
  assert.equal(genuineSavings.amortized_net_savings_tokens, 1_445);
  assert.equal(genuineSavings.round_trips_saved, 1);

  // 5. Unmeasurable host input fails closed to KeepRaw with typed fallback_reason=host_input_unmeasurable.
  const unmeasurable = evaluateEpochPrefetchPromptGate({
    has_inline_exploratory: true,
    replace_exploratory_context: true,
    host_input_measurable: false,
    rendered_prompt_baseline_bytes: null,
    rendered_prompt_delivered_bytes: null,
    rendered_prompt_baseline_tokens: null,
    rendered_prompt_delivered_tokens: null,
    potential_bytes_saved: 6_000,
    tokenizer,
  });
  assert.equal(unmeasurable.operator, "KeepRaw");
  assert.equal(unmeasurable.fallback_reason, "host_input_unmeasurable");
  assert.equal(unmeasurable.bytes_saved, 0);
  assert.equal(unmeasurable.round_trips_saved, 0);
});

test("computeQuietTurnStateDigest ignores volatile read envelopes and detects real state changes", () => {
  const baseContract = {
    goal_id: "g-quiet",
    effective_action: "terminal_no_followup",
    interaction_contract: {
      mode: "terminal_no_followup",
      agent_channel: {must_attempt: false, delivery_allowed: false, quiet_noop_allowed: true},
    },
    agent_todo_summary: {open_count: 1, monitor_due_count: 0},
  };
  const d1 = computeQuietTurnStateDigest({
    goal_id: "g-quiet",
    agent_id: "a1",
    route: {kind: "wait", would_invoke_host: false},
    work_contract: {
      ...baseContract,
      projection_envelope: {observed_at: "2026-11-24T10:00:00Z", age_seconds: 0},
      contract_binding: {source_decision_hash: "sha256:" + "1".repeat(64)},
      heartbeat_receipt: {turn_instance_id: "t-1"},
    },
  });
  const d2 = computeQuietTurnStateDigest({
    goal_id: "g-quiet",
    agent_id: "a1",
    route: {kind: "wait", would_invoke_host: false},
    work_contract: {
      ...baseContract,
      projection_envelope: {observed_at: "2026-11-24T10:05:00Z", age_seconds: 5},
      contract_binding: {source_decision_hash: "sha256:" + "2".repeat(64)},
      heartbeat_receipt: {turn_instance_id: "t-2"},
    },
  });
  assert.equal(d1.state_digest, d2.state_digest);
  assert.equal(d1.must_attempt, false);
  assert.equal(d1.quiet_noop_allowed, true);

  const dChanged = computeQuietTurnStateDigest({
    goal_id: "g-quiet",
    agent_id: "a1",
    route: {kind: "wait", would_invoke_host: false},
    work_contract: {
      ...baseContract,
      agent_todo_summary: {open_count: 2, monitor_due_count: 1},
    },
  });
  assert.notEqual(d1.state_digest, dChanged.state_digest);
});

test('semantic-unit anchor & FoldToReceipt contract preserves parent KV prefix and rolls back atomically to KeepRaw', () => {
  const prefixDigest = 'a'.repeat(64);
  const receiptSha = 'b'.repeat(64);
  const sourceSha = 'c'.repeat(64);
  const anchor = recordSemanticUnitAnchor({
    todo_id: 'todo_unit_1',
    turn_instance_id: 'turn_inst_1',
    anchor_step_index: 4,
    anchor_surface_seq: 10,
    prefix_digest: prefixDigest,
    context_epoch: 'epoch-todo-1',
    goal_id: 'goal-1',
    agent_id: 'agent-1',
  });
  assert.equal(anchor.schema_version, 'loopx_semantic_unit_anchor_v0');
  assert.equal(anchor.prefix_digest, prefixDigest);
  assert.match(String(anchor.anchor_sha256), /^[a-f0-9]{64}$/);

  const validReceipt = {
    todo_id: 'todo_unit_1',
    completion_receipt_id: 'cqr_unit_1',
    exit_code: 0,
    sha256: receiptSha,
    failure_facts: ['check-1 previously warned on stale fixture'],
    open_obligations: ['verify downstream caller readback'],
    source_ref: {path: 'captures/todo-unit-1.json', sha256: sourceSha},
  };
  const validTokenizer = {name: 'tiktoken', encoding: 'o200k_base'};

  // 1. Default-off keeps raw state intact cleanly.
  const disabled = buildSemanticUnitFoldSwap({
    enabled: false,
    anchor,
    todo_id: 'todo_unit_1',
    turn_instance_id: 'turn_inst_1',
    context_epoch: 'epoch-todo-1',
    prefix_digest: prefixDigest,
    now_step_index: 12,
    now_surface_seq: 28,
    completion_receipt: validReceipt,
  });
  assert.equal(disabled.operator, 'KeepRaw');
  assert.equal(disabled.status, 'disabled');
  assert.equal(disabled.preserves_raw_state, true);
  assert.equal(disabled.preserves_prefix_kv, true);
  assert.equal(disabled.reclaim_range, null);

  // 2. Positive receipt-gated FoldToReceipt with bounded Obs(next_cmd) & token savings.
  const committed = buildSemanticUnitFoldSwap({
    enabled: true,
    anchor,
    todo_id: 'todo_unit_1',
    turn_instance_id: 'turn_inst_1',
    context_epoch: 'epoch-todo-1',
    prefix_digest: prefixDigest,
    now_step_index: 12,
    now_surface_seq: 28,
    completion_receipt: validReceipt,
    baseline_tokens: 1800,
    replacement_tokens: 160,
    baseline_bytes: 7200,
    tokenizer: validTokenizer,
    obs_next_cmd: {
      command: ['git', 'status', '--short'],
      exit_code: 0,
      output: 'M loopx/control_plane/quota/stage_then_swap.ts',
      truncated: false,
      latency_ms: 14,
    },
  });
  assert.equal(committed.operator, 'FoldToReceipt');
  assert.equal(committed.status, 'committed');
  assert.equal(committed.preserves_raw_state, false);
  assert.equal(committed.preserves_prefix_kv, true);
  assert.deepEqual(committed.reclaim_range, [4, 12]);
  assert.equal(committed.round_trips_saved, 1);
  assert.equal(committed.net_savings_tokens, 1640);
  assert.equal(committed.amortized_net_savings_tokens, 1632);
  assert.match(String(committed.replacement_text), /\[Semantic-Unit Fold Receipt mu \| trust=tool\]/);
  assert.match(String(committed.replacement_text), /--- obs_next_cmd ---/);
  assert.match(String(committed.replacement_text), /verify downstream caller readback/);

  // 3. Atomic fail-closed KeepRaw rollback across critical invariants.
  const failures: Array<[Record<string, unknown>, string]> = [
    [{completion_receipt: null, baseline_tokens: 1800, replacement_tokens: 160, tokenizer: validTokenizer}, 'missing_completion_receipt'],
    [{completion_receipt: {...validReceipt, exit_code: 2}, baseline_tokens: 1800, replacement_tokens: 160, tokenizer: validTokenizer}, 'command_error'],
    [{
      completion_receipt: validReceipt,
      baseline_tokens: 1800,
      replacement_tokens: 160,
      tokenizer: validTokenizer,
      obs_next_cmd: {command: ['git', 'push', 'origin', 'main'], exit_code: 0, output: 'bad'},
    }, 'policy_violation:git_subcommand_not_read_only'],
    [{
      completion_receipt: validReceipt,
      baseline_tokens: 200,
      replacement_tokens: 160,
      tokenizer: validTokenizer,
    }, 'negative_net_savings'],
    [{
      completion_receipt: validReceipt,
      prefix_digest: 'd'.repeat(64),
      baseline_tokens: 1800,
      replacement_tokens: 160,
      tokenizer: validTokenizer,
    }, 'prefix_digest_mismatch'],
    [{
      completion_receipt: validReceipt,
      context_epoch: 'epoch-other',
      baseline_tokens: 1800,
      replacement_tokens: 160,
      tokenizer: validTokenizer,
    }, 'context_epoch_mismatch'],
    [{
      completion_receipt: validReceipt,
      tool_pairing_valid: false,
      baseline_tokens: 1800,
      replacement_tokens: 160,
      tokenizer: validTokenizer,
    }, 'unpaired_tool_calls'],
    [{
      completion_receipt: validReceipt,
      quiescent_boundary: false,
      baseline_tokens: 1800,
      replacement_tokens: 160,
      tokenizer: validTokenizer,
    }, 'non_quiescent_boundary'],
  ];
  for (const [override, expectedReason] of failures) {
    const rolledBack = buildSemanticUnitFoldSwap({
      enabled: true,
      anchor,
      todo_id: 'todo_unit_1',
      turn_instance_id: 'turn_inst_1',
      context_epoch: 'epoch-todo-1',
      prefix_digest: prefixDigest,
      now_step_index: 12,
      now_surface_seq: 28,
      ...override,
    });
    assert.equal(rolledBack.operator, 'KeepRaw', `expected KeepRaw for ${expectedReason}`);
    assert.equal(rolledBack.status, 'fallback_keep_raw');
    assert.equal(rolledBack.fallback_reason, expectedReason);
    assert.equal(rolledBack.preserves_raw_state, true);
    assert.equal(rolledBack.preserves_prefix_kv, true);
    assert.equal(rolledBack.reclaim_range, null);
    assert.equal(rolledBack.replacement_text, null);
  }

  // 4. Native context lifecycle retains semantic_unit_anchors across idempotent PreCompact->PostCompact->SessionStart(compact)
  //    and clears them when context_epoch rotates.
  const s0 = updateNativeContextLifecycle({
    state: {},
    event: {hook_event_name: 'SessionStart', session_id: 'sess-1', cwd: '/repo', source: 'startup'},
    new_epoch: 'epoch-1',
    pages: [],
    semantic_unit_anchor: recordSemanticUnitAnchor({
      todo_id: 'todo_unit_1',
      turn_instance_id: 'turn_inst_1',
      anchor_step_index: 2,
      prefix_digest: prefixDigest,
      context_epoch: 'epoch-1',
    }),
  });
  assert.ok((s0.semantic_unit_anchors as Record<string, unknown>)?.todo_unit_1);
  const sCompact = updateNativeContextLifecycle({
    state: s0,
    event: {hook_event_name: 'PreCompact', session_id: 'sess-1', cwd: '/repo'},
    new_epoch: 'epoch-2',
    pages: [],
  });
  assert.equal(sCompact.context_epoch, 'epoch-1');
  assert.ok((sCompact.semantic_unit_anchors as Record<string, unknown>)?.todo_unit_1);

  const sRotated = updateNativeContextLifecycle({
    state: {...sCompact, candidate: {basis: 'native_hook_assumption', capture_bytes_verified: true}},
    event: {hook_event_name: 'PreCompact', session_id: 'sess-1', cwd: '/repo'},
    new_epoch: 'epoch-2',
    pages: [],
  });
  assert.equal(sRotated.context_epoch, 'epoch-2');
  assert.equal(sRotated.semantic_unit_anchors, undefined);
});
