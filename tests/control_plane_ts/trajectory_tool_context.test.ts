import assert from 'node:assert/strict';
import test from 'node:test';
import {summarizeTrajectoryToolContext as summarize} from '../../loopx/control_plane/capabilities/trajectory_tool_context.ts';
const sha = 'a'.repeat(64);
const call = (line: number, call_id: string, argv: unknown) => ({line, kind: 'call', call_id, argv});
const output = (line: number, call_id: string, bytes: number, sha256 = sha) => ({line, kind: 'output', call_id, bytes, sha256});

test('inventory denominator includes unassigned input; byte share is not provider tokens', () => {
  const result = summarize([{line: 1, kind: 'message', bytes: 100, unmeasured_parts: 1},
    call(2, 'a', ['/venv/bin/loopx', '--format', 'json', 'quota', 'context-read']), output(3, 'a', 60),
    call(4, 'b', ['pytest']), output(5, 'b', 40)])!;
  assert.equal(result.status, 'observed'); if (result.status !== 'observed') return;
  assert.equal(result.observed_input_text_bytes, 200); assert.equal(result.loopx_tool_output_fraction, 0.3);
  assert.equal(result.unassigned_input_text_bytes, 140); assert.equal(result.unmeasured_parts, 1);
  assert.deepEqual(result.operations, [{operation: 'quota context-read', calls: 1, bytes: 60}]);
  assert.equal('input_tokens' in result, false);
});

test('one mirrored output counts once; identical content from distinct calls is real repeated input', () => {
  const result = summarize([call(1, 'a', ['loopx', 'status']), output(2, 'a', 60), output(3, 'a', 60),
    call(4, 'b', ['loopx', 'status']), output(5, 'b', 60)])!;
  assert.equal(result.status, 'observed'); if (result.status !== 'observed') return;
  assert.equal(result.loopx_tool_output_bytes, 120); assert.equal(result.tool_outputs, 2);
  assert.equal(result.duplicate_output_records, 1); assert.equal(result.repeated_loopx_output_bytes, 60);
});

test('Python module argv is recognized; arbitrary prose, scripts and quoted loopx are not', () => {
  const yes = [['python3.13', '-m', 'loopx.entrypoint', 'quota', 'context-project'], ['python', '-m', 'loopx', 'todo'],
    ['python3', '-m', 'loopx.cli', '--registry', '/synthetic/registry.json', '--format', 'json', 'task-lease', 'inspect']];
  const no = [null, ['echo', 'loopx', 'status'], ['python', '-c', 'import loopx'], ['not-loopx', 'status'], ['loopx-lookalike', 'status'],
    ['python', '-m', 'loopx.cli_lookalike', 'task-lease', 'inspect']];
  for (const argv of [...yes, ...no]) {
    const r = summarize([call(1, 'a', argv), output(2, 'a', 50)])!;
    assert.equal(r.status, 'observed'); if (r.status !== 'observed') continue;
    assert.equal(r.loopx_tool_output_bytes, yes.includes(argv as string[]) ? 50 : 0);
  }
});

test('lease preparation, proof reads and completion are separate observed costs', () => {
  const ops = ['task-lease acquire', 'task-lease inspect', 'task-lease renew', 'task-lease release', 'task-lease transfer',
    'todo claim', 'todo complete', 'todo list', 'todo update', 'todo add'];
  const rows = ops.flatMap((op, index) => [call(index * 2 + 1, `call-${index}`, ['loopx', ...op.split(' ')]),
    output(index * 2 + 2, `call-${index}`, 10 + index)]);
  const result = summarize(rows)!;
  assert.equal(result.status, 'observed'); if (result.status !== 'observed') return;
  assert.deepEqual(new Set(result.operations.map(row => row.operation)), new Set(ops));
  assert.equal(result.loopx_tool_output_bytes, 145);
  assert.equal(result.observed_input_text_bytes, 145);
  assert.equal(result.operations.reduce((total, row) => total + row.bytes, 0), 145);
  assert.equal(result.operations.reduce((total, row) => total + row.calls, 0), 10);
  // Categories name returned CLI text, not successful state transitions or
  // avoidable recovery. Unknown subcommands stay in their existing bucket.
  for (const [group, expected] of [['task-lease', 'other loopx'], ['todo', 'todo']]) {
    const unknown = summarize([call(1, 'unknown', ['loopx', group, 'future-op']), output(2, 'unknown', 11)])!;
    assert.equal(unknown.status, 'observed');
    if (unknown.status === 'observed') assert.equal(unknown.operations[0].operation, expected);
  }
});

test('conflicting mirrors, line order, counts and call identities withhold only the observation', () => {
  for (const rows of [
    [call(1, 'a', ['loopx']), output(2, 'a', 50), output(3, 'a', 51)],
    [call(1, 'a', ['loopx']), call(2, 'a', ['pytest'])],
    [output(1, 'a', -1)], [output(1, 'a', 0.5)], [output(1, 'a', 50, 'bad')],
    [{line: 1, kind: 'message', bytes: Number.MAX_SAFE_INTEGER, unmeasured_parts: 0}, output(2, 'a', 1)],
    [output(1, 'a', 1), output(1, 'b', 1)],
  ]) assert.equal(summarize(rows)?.status, 'unavailable');
  assert.equal(summarize(undefined), undefined);
  const empty = summarize([])!;
  assert.equal(empty.status, 'observed'); if (empty.status === 'observed') assert.equal(empty.loopx_tool_output_fraction, null);
});
