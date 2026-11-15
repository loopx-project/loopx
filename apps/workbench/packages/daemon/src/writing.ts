import { createHash, randomUUID } from 'node:crypto';
import { readdirSync, readFileSync, realpathSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { extname, join, relative, sep } from 'node:path';
import { homeDirectory } from './config.js';
import { HttpError } from './errors.js';
import { MAX_TEXT_BYTES, readText } from './browse.js';

/**
 * Writing mode: the person talks an article out, the agent interviews, records and fills in, draft
 * by draft. The hub keeps the stage and appends that stage's way of working to each message, the
 * way goals and progress lines do; nothing is installed into the agent. The article is a Markdown
 * file under `writing/` in the thread's directory, which the person may also edit as plain text.
 * See docs/writing-design.md.
 */

export const WRITING_STAGES = ['draft', 'second', 'third', 'figures', 'polish'] as const;
export type WritingStage = (typeof WRITING_STAGES)[number];

export interface WritingState {
  stage: WritingStage;
  /** The article, relative to the thread's directory: the newest `.md` in `writing/` after a turn. */
  file: string | null;
  /** The person saved the article since the agent last heard about it. */
  edited?: boolean;
}

/** Where articles live, relative to the thread's directory. */
export const WRITING_DIR = 'writing';
/**
 * The person's own notes, kept across projects and agents: what reviews distil, which they confirm
 * and may edit. Not an agent's memory: plain Markdown the person owns. `CALLI_NOTES_DIR` moves it.
 */
export function notesDir(): string {
  return process.env.CALLI_NOTES_DIR || join(homeDirectory(), 'notes');
}

/** Writing habits: read before writing, grown by 复盘. */
export function habitsPath(): string {
  return join(notesDir(), 'writing.md');
}

export function isWritingStage(value: unknown): value is WritingStage {
  return typeof value === 'string' && (WRITING_STAGES as readonly string[]).includes(value);
}

const MARK = '<calli-writing>';
/** The mark from before the rename to Calli, still in older messages. */
const LEGACY_MARK = '<limi-writing>';

const common = (habits: string) => `你在写作模式里，帮我写一篇文章。观点必须是我的，哪怕偏激；你负责采访、记录、补充，不替我下结论，不把尖锐的话磨圆。
正文写在 ${WRITING_DIR}/<题目>.md（题目等初稿有了内容再起，之后不要改文件名），配图放 ${WRITING_DIR}/<题目>/，正文里用相对路径引用。
我的消息常常来自语音输入，会有同音错字：按上下文改正，但保留我的说法和语气。
我直接改过的正文以文件为准，不要改回去。
大师们的提醒只是建议：我说不接受，就记下，不再重复。
动笔前先读 ${habits}（我的写作习惯，有的话），照着写。
我说「复盘」「总结一下」时：回看这次对话里我的注释、我直接改过的地方、我否掉的写法，提炼成能用到以后文章的习惯（不是只针对这一句的改动），每条一行，写清要怎样、不要怎样，最好带一个 ×/✓ 的例子；追加到 ${habits}（没有就新建，开头一行「# 写作习惯」），每条新记的开头标〔新〕。已有的习惯不要重复记，和新的冲突时先问我。写完在回复里逐条列出新记的，请我确认：我说对，就去掉〔新〕；我说不要的，删掉那条。`;

const STAGES: Record<WritingStage, string> = {
  draft: `现在是初稿（关着门写）。
只记录、只采访：把我的原话整理进正文，不加你自己的观点、例子和资料。
我说不清、需要查的地方，在正文里标【待补：要补什么】，然后继续往下写，不要停。
每轮回复最多问一两个问题，问具体场景（哪天、哪件事、写到哪一句），不反驳。
提醒我：烂初稿没关系，一只鸟一只鸟地写（Lamott）；像说话一样写（Paul Graham）。`,
  second: `现在是二稿（开门补充）。
补齐【待补】和我的注释要的内容。查到的资料写明出处；拿不准的事实说拿不准，不要编。
你补进正文的段落，开头加〔补〕；我说「留」的，去掉〔补〕。
默认只补充不反驳；我说「这篇帮我挑刺」时，再质疑事实和论证、找反例和已有的工作。
提醒我：知识的诅咒，读者不知道你知道的东西（Pinker）。`,
  third: `现在是三稿（删）。
指出可以删的字、句、段，说明理由，我同意了再删；回复里说这一稿比上一稿短了多少。
提醒我：第二稿 = 第一稿 − 10%（Stephen King）；可有可无的删去，毫不可惜（鲁迅）；能用短词就不用长词（Orwell）；去掉杂物（Zinsser）。`,
  figures: `现在是配图。
先说哪里需要图、画什么，我同意了再画。流程、结构、关系用 \`\`\`mermaid 代码块写在正文里；自己设计的示意图画成 SVG；插画可以用 calli tools 里的生图工具（calli tools search image）。图片文件放 ${WRITING_DIR}/<题目>/。
提醒我：图是带读者去看一样东西，不是装饰（Pinker）。`,
  polish: `现在是润色。
只改语言，不改观点。先把改动列成清单给我：每条写原句和改后；改变了意思、磨掉了棱角的，单独列在最后。我确认的才改进正文。
提醒我：念一遍，念着别扭的就改（Paul Graham）。`,
};

/** Appended to what the person sent, like the progress line's request; never shown to them. */
export function writingInstructions(text: string, state: WritingState): string {
  if (text.includes(MARK) || text.includes(LEGACY_MARK)) return text;
  const edited = state.edited
    ? `\n${state.file ?? '正文'}在你上一轮之后被我直接改过：先重读再动手，不要改回去。`
    : '';
  return `${text}\n\n${MARK}\n${common(habitsPath())}\n\n${STAGES[state.stage]}${edited}\n</calli-writing>`;
}

/** The newest `.md` directly in `writing/`, relative to the thread's directory, or null. */
export function newestArticle(cwdReal: string): string | null {
  let best: { name: string; mtime: number } | null = null;
  let names: string[];
  try {
    names = readdirSync(join(cwdReal, WRITING_DIR));
  } catch {
    return null;
  }
  for (const name of names) {
    if (extname(name).toLowerCase() !== '.md') continue;
    try {
      const real = realpathSync(join(cwdReal, WRITING_DIR, name));
      if (!isEditableArticle(real, cwdReal)) continue;
      const stat = statSync(real);
      if (stat.isFile() && (!best || stat.mtimeMs > best.mtime)) best = { name, mtime: stat.mtimeMs };
    } catch {
      // Gone between listing and looking.
    }
  }
  return best ? `${WRITING_DIR}/${best.name}` : null;
}

/**
 * Whether a resolved file may be edited by the person: a `.md` under `writing/` in the thread's
 * own directory (symlinks already resolved by the caller, so a link cannot reach outside).
 */
export function isEditableArticle(real: string, cwdReal: string): boolean {
  const rel = relative(cwdReal, real);
  if (!rel || rel.startsWith('..') || rel.split(sep)[0] !== WRITING_DIR) return false;
  return extname(real).toLowerCase() === '.md';
}

/**
 * A Markdown file among the person's notes, by its real path (symlinks resolved), or null. Such a
 * file may be opened and edited from any thread: the notes are the person's, not a project's.
 */
export function noteFile(rawPath: string): string | null {
  let real: string, base: string;
  try {
    real = realpathSync(rawPath.startsWith('file://') ? rawPath.slice('file://'.length) : rawPath);
    base = realpathSync(notesDir());
  } catch {
    return null;
  }
  const rel = relative(base, real);
  if (!rel || rel.startsWith('..') || rel.startsWith(sep) || extname(real).toLowerCase() !== '.md') return null;
  try {
    return statSync(real).isFile() ? real : null;
  } catch {
    return null;
  }
}

export function contentHash(bytes: Buffer | string): string {
  return createHash('sha256').update(bytes).digest('hex');
}

/**
 * Writes the person's text over the article, only if the file still holds what they started from.
 * The new text goes to a temporary file beside it and is renamed into place: never half a file.
 */
export function saveArticle(real: string, text: unknown, baseHash: unknown): { hash: string } {
  if (typeof text !== 'string') throw new HttpError(400, 'text must be a string');
  if (typeof baseHash !== 'string' || !baseHash) throw new HttpError(400, 'baseHash is required');
  const bytes = Buffer.from(text, 'utf8');
  if (bytes.length > MAX_TEXT_BYTES) throw new HttpError(413, 'the article is larger than 1 MB');
  const opened = readText(real);
  if (opened.truncated) throw new HttpError(413, 'the article is larger than 1 MB');
  if (opened.hash === null) throw new HttpError(403, 'only text articles can be edited');
  const current = opened.hash;
  if (current !== baseHash) throw new HttpError(409, 'the article changed since it was opened', { hash: current });
  const temp = `${real}.${randomUUID()}.tmp`;
  try {
    writeFileSync(temp, bytes, { flag: 'wx', mode: statSync(real).mode });
    // An agent can write while this process prepares the replacement. Check again at commit.
    const latest = contentHash(readFileSync(real));
    if (latest !== baseHash) throw new HttpError(409, 'the article changed since it was opened', { hash: latest });
    renameSync(temp, real);
  } catch (error) {
    rmSync(temp, { force: true });
    throw error;
  }
  return { hash: contentHash(bytes) };
}
