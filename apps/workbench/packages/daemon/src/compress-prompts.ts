/**
 * What the compression agent is asked. Kept apart from the pipeline in auto-compact.ts so the
 * wording can be tuned without touching it.
 *
 * The reader of a summary is another agent that cannot see the conversation: either the successor
 * of a handover whose source could not write its own brief, or the fresh session of a compaction.
 * It gets nothing but this text, so the prompts ask for specifics (paths, commands, numbers, the
 * user's own words) and for what was already tried, and tell the writer that the record holds the
 * questions and answers only, no tool output, so gaps are named instead of guessed at.
 *
 * Wording follows the checkpoint prompts of Codex ("a handoff summary for another LLM that will
 * resume the task": progress, constraints, next steps) and Claude Code (explicit requests, concrete
 * code and file names, errors with their fixes), trimmed to the sections a successor acts on.
 */

/** The sections of a summary, in reading order. */
export const SUMMARY_SECTIONS = ['任务', '已完成', '未完成·下一步', '改过的文件', '验证情况', '用户要求与注意事项'] as const;

const sectionList = SUMMARY_SECTIONS.map(s => `## ${s}`).join(' / ');

/** One chunk of the record, written up as a summary of its own. */
export const compressPrompt = (index: number, of: number, body: string): string => [
  `这是同一个编码 agent 会话记录的第 ${index}/${of} 段。写一份交接摘要，给接手的另一个 agent 用：它看不到原对话，只能靠你写的这份接着干。`,
  `分小节写：${sectionList}。`,
  '具体到文件路径、命令、数字、报错原文；已经试过且失败的做法也写，免得接手方重走。用户的明确要求和偏好尽量照原话。',
  '记录只含每轮的提问和回答，不含工具输出：没有依据的不要猜，写「记录里没有」。直接输出摘要，不要解释你在做什么。',
  '',
  '<记录>',
  body,
  '</记录>',
].join('\n');

/** The summaries of several chunks, merged into one. */
export const mergePrompt = (notes: string[]): string => [
  '以下是同一个会话分段整理的交接摘要。合并去重成一份，给看不到原对话的接手 agent 用，小节：',
  sectionList,
  '保留所有具体信息（文件名、命令、数字、报错、失败过的做法、用户原话），只删重复；以后面的段为准处理前后矛盾。直接输出摘要。',
  '',
  ...notes.map((n, i) => `<段${i + 1}>\n${n}\n</段${i + 1}>`),
].join('\n\n');
