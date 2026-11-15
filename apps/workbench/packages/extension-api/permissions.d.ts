// What an extension may ask the host for. See docs/extensions-design.md, 「权限」.
// These limit how the host calls extensions; they are not a sandbox: extension processes
// run as the daemon user.

/**
 * - `memory.read`      — generated memory documents of this machine's supported instances. (v1)
 * - `events.summary`   — whitelisted metadata of threads, turns and waits. No message text,
 *                        images, tool input/output, answers, `preview` or queued text. (v1)
 * - `progress.read`    — step snapshots agents report (`plan.updated`), per thread. (planned)
 * - `progress.report`  — the host asks every thread's agent to end each turn with a `[进度]` line
 *                        and hands views what it said. The host writes the request; the extension
 *                        cannot change it. (v1)
 * - `worksource.read`  — goals and work items from a work source, per source and thread. (planned)
 * - `files.read`       — read-only files in linked threads' directories. (planned)
 * - `commands.run`     — run commands declared in the manifest. (planned)
 * - `notify`           — send reminders through the host. (planned)
 * - `transcript.read`  — user messages and replies of linked threads, never reasoning. (planned)
 * - `toolresult.read`  — tool results and command output of linked threads. (planned)
 * - `task.control`     — send visible messages to threads the user authorised. (planned)
 * - `voice.control`    — own a bounded voice session on a selected thread. No approval answers.
 *                        Runtime guard implemented; browser API not yet exposed.
 * - `threads.rename`   — change a thread's title in Calli, never the agent's own session. (built-in)
 * - `assistant.ask`    — hand a text the person asked for to their personal assistant as their own
 *                        message, and open the assistant page. Content leaves the view. (built-in)
 * - `agents.ask`       — ask an agent instance the person chose a one-off question in an archived
 *                        thread of its own, and read the plain-text answer. (built-in)
 *
 * Not grantable at all: answering approvals, writing agent directories or config,
 * reading the hub token, the raw Api, writing prompts or rewriting messages (`progress.report` only
 * lets the host add its own fixed request).
 */
export type Permission =
  | 'memory.read'
  | 'events.summary'
  | 'progress.read'
  | 'progress.report'
  | 'worksource.read'
  | 'files.read'
  | 'commands.run'
  | 'notify'
  | 'transcript.read'
  | 'toolresult.read'
  | 'task.control'
  | 'voice.control'
  | 'threads.rename'
  | 'agents.ask'
  | 'assistant.ask';
