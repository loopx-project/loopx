import { execFileSync } from 'node:child_process';

/** Execute advertised read commands inside the receiving agent, without exporting its environment. */
export function contextReaderFixture(message) {
  if (!message.includes('CONTEXT_READ') && !message.includes('CONTEXT_BOOT')) return null;
  const commands = message.includes('CONTEXT_READ') ? [...message.matchAll(/`((?:loochat|calli) (?:references|history|handover)\b[^`\n]*)`/g)].map(match => match[1]) : [];
  if (message.includes('CONTEXT_READ')) {
    for (const match of message.matchAll(/用 ((?:loochat|calli) history [^\n]+?) 读/g)) commands.push(match[1]);
  }
  const reads = [...new Set(commands)].filter(command => !command.includes('<')).map(command => {
    try { return { command, code: 0, value: JSON.parse(execFileSync('/bin/sh', ['-c', command], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })) }; }
    catch (error) {
      let value;
      try { value = JSON.parse(String(error.stdout)); } catch { value = { error: String(error.stderr || error.message).trim() }; }
      return { command, code: error.status || 1, value };
    }
  });
  return JSON.stringify({ pid: process.pid, reads });
}
