/** Read-only inventory, separate from request-token attribution and judgments. */
import {BARE_SHA256_PATTERN} from '../content_digest.ts';
import type {TrajectoryToolContext} from './trajectory_assessment_types.ts';

// Local observation buckets, not new execution or settlement vocabulary.
function operation(argv: unknown): string | null {
  if (!Array.isArray(argv) || !argv.length || argv.some(v => typeof v !== 'string')) return null;
  const args = [...argv] as string[];
  const executable = args.shift()!.split('/').at(-1);
  if (executable !== 'loopx') {
    if (!/^python(?:3(?:\.\d+)?)?$/.test(executable ?? '') || args.shift() !== '-m'
      || !['loopx', 'loopx.cli', 'loopx.entrypoint'].includes(args.shift() ?? '')) return null;
  }
  while (['--registry', '--runtime-root', '--format', '--cli-bin'].includes(args[0] ?? '')) {
    if (args.length < 2) return null;
    args.splice(0, 2);
  }
  const group = args[0];
  if (group === 'quota' && ['context-read', 'context-project', 'should-run', 'spend-slot', 'replan-context'].includes(args[1] ?? '')) return `quota ${args[1]}`;
  if (group === 'task-lease' && ['acquire', 'inspect', 'renew', 'release', 'transfer'].includes(args[1] ?? '')) return `task-lease ${args[1]}`;
  if (group === 'todo' && ['add', 'list', 'claim', 'complete', 'update'].includes(args[1] ?? '')) return `todo ${args[1]}`;
  return ['todo', 'refresh-state', 'explore', 'status', 'diagnose'].includes(group ?? '') ? group! : 'other loopx';
}

export function summarizeTrajectoryToolContext(value: unknown): TrajectoryToolContext | undefined {
  if (value === undefined) return undefined;
  const method = 'codex_loopx_tool_context_inventory_v1';
  try {
    if (!Array.isArray(value)) throw new Error();
    const calls = new Map<string, string | null>(), outputs = new Map<string, string>();
    const content = new Set<string>(), groups = new Map<string, {operation: string; calls: number; bytes: number}>();
    let previous = 0, total = 0, loopx = 0, repeats = 0, unmeasured = 0, duplicates = 0, outputCount = 0;
    const count = (n: unknown): number => {
      if (typeof n !== 'number' || !Number.isSafeInteger(n) || n < 0) throw new Error();
      return n;
    };
    for (const row of value) {
      if (!row || typeof row !== 'object' || !Number.isSafeInteger(row.line) || row.line <= previous) throw new Error();
      previous = row.line;
      if (row.kind === 'call') {
        if (typeof row.call_id !== 'string' || !row.call_id || calls.has(row.call_id)) throw new Error();
        calls.set(row.call_id, operation(row.argv));
      } else if (row.kind === 'unmeasured') unmeasured++;
      else if (row.kind === 'message') {
        total = count(total + count(row.bytes)); unmeasured = count(unmeasured + count(row.unmeasured_parts));
      } else if (row.kind === 'output') {
        if (typeof row.call_id !== 'string' || !row.call_id || typeof row.sha256 !== 'string' || !BARE_SHA256_PATTERN.test(row.sha256)) throw new Error();
        const bytes = count(row.bytes), missing = count(row.unmeasured_parts ?? 0);
        const identity = `${row.sha256}:${bytes}:${missing}`;
        const prior = outputs.get(row.call_id);
        if (prior !== undefined) {
          if (prior !== identity) throw new Error();
          duplicates++; continue;
        }
        outputs.set(row.call_id, identity); outputCount++; total = count(total + bytes);
        unmeasured = count(unmeasured + missing);
        const op = calls.get(row.call_id);
        if (!op) continue;
        loopx = count(loopx + bytes);
        if (content.has(identity)) repeats = count(repeats + bytes);
        content.add(identity);
        const group = groups.get(op) ?? {operation: op, calls: 0, bytes: 0};
        group.calls++; group.bytes = count(group.bytes + bytes); groups.set(op, group);
      } else throw new Error();
    }
    return {method, status: 'observed', observed_input_text_bytes: total, loopx_tool_output_bytes: loopx,
      loopx_tool_output_fraction: total > 0 ? loopx / total : null,
      unassigned_input_text_bytes: total - loopx, unmeasured_parts: unmeasured,
      tool_outputs: outputCount, duplicate_output_records: duplicates, repeated_loopx_output_bytes: repeats,
      operations: [...groups.values()].sort((a, b) => b.bytes - a.bytes || a.operation.localeCompare(b.operation))};
  } catch {
    return {method, status: 'unavailable', reason: 'Captured tool context could not be reconciled.'};
  }
}
