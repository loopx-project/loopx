import { createReadStream } from 'node:fs';
import { open } from 'node:fs/promises';

/**
 * Readers for the whole prompt a conversation runs with. Each agent persists its own shape, and
 * only its adapter knows it; the parsing here is pure so tests can feed lines without files.
 */

/** How much of a session file is read looking for a prompt. The prompt sits near the start, so a
 * transcript that outgrows this keeps its prompt findable and the read stays quick. */
const MAX_PROMPT_SCAN = 8 * 1024 * 1024;

/**
 * Streams a JSONL session file line by line. Lines can be split across chunks; a single record
 * larger than 2 MiB ends the scan, as no prompt record is that big. Missing files return false
 * rather than throwing: a session that was never written has no prompt, which is not an error.
 */
export async function scanJsonl(path: string, onLine: (line: string) => void): Promise<boolean> {
  let handle;
  try {
    handle = await open(path, 'r');
  } catch {
    return false;
  }
  try {
    const stream = createReadStream(path, { encoding: 'utf8', start: 0, end: MAX_PROMPT_SCAN });
    let rest = '';
    let tooLong = false;
    await new Promise<void>((resolve, reject) => {
      stream.on('data', (chunk: string | Buffer) => {
        if (tooLong) return;
        rest += typeof chunk === 'string' ? chunk : chunk.toString('utf8');
        let cut: number;
        while ((cut = rest.indexOf('\n')) >= 0) {
          const line = rest.slice(0, cut);
          rest = rest.slice(cut + 1);
          if (line.length > 2 * 1024 * 1024) { tooLong = true; stream.destroy(); return; }
          if (line.trim()) onLine(line);
        }
        if (rest.length > 2 * 1024 * 1024) { tooLong = true; stream.destroy(); }
      });
      stream.on('end', () => resolve());
      stream.on('error', reject);
      stream.on('close', () => resolve());
    });
    if (!tooLong && rest.trim()) onLine(rest);
    return true;
  } finally {
    await handle.close().catch(() => undefined);
  }
}

/** A Claude Code transcript line, when it carries a snapshot of the system prompt. */
export function claudeSystemPromptLine(line: string): string | null {
  if (!line.includes('"prompt_snapshot"')) return null;
  try {
    const record = JSON.parse(line);
    const attachment = record?.attachment;
    if (attachment?.type !== 'prompt_snapshot' || !Array.isArray(attachment.systemPrompt)) return null;
    const text = attachment.systemPrompt.filter((part: unknown) => typeof part === 'string' && part.length > 0).join('\n\n');
    return text.length > 0 ? text : null;
  } catch {
    return null;
  }
}

/** A Codex rollout's first line, when its session metadata carries the base instructions. */
export function codexSystemPromptMeta(line: string): string | null {
  try {
    const record = JSON.parse(line);
    if (record?.type !== 'session_meta') return null;
    const instructions = record?.payload?.base_instructions;
    const text = typeof instructions === 'string' ? instructions : instructions?.text;
    return typeof text === 'string' && text ? text : null;
  } catch {
    return null;
  }
}

/** A pi transcript line, when it is the system message the model was started with. */
export function piSystemPromptLine(line: string): string | null {
  if (!line.includes('"role":"system"') && !line.includes('"role": "system"')) return null;
  try {
    const record = JSON.parse(line);
    if (record?.type !== 'message' || record?.message?.role !== 'system') return null;
    const sections = record.message.sections;
    if (!sections || typeof sections !== 'object') return null;
    const text = Object.values(sections).filter((part): part is string => typeof part === 'string' && part.length > 0).join('\n\n');
    return text.length > 0 ? text : null;
  } catch {
    return null;
  }
}
