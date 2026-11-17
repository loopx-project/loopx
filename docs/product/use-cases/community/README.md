# Try LoopX with your AI assistant

[简体中文](README.zh-CN.md) · [Community FAQ](faq.md)

Start with a project where a test is failing. Ask the assistant you already
use to reproduce the failure, fix it, and show you the result.

1. **Install LoopX and its entry for your assistant.** Follow the
   [installation guide](../../../guides/installing-loopx.md) and the
   [skill setup for your host](../../../guides/getting-started.md#command-skill-registration).
   Open your project in that assistant. If the project is already connected
   to LoopX, keep using that connection.
2. **Send one task in the project conversation.** In Codex App, select the
   installed `loopx` skill with `$loopx` or from the skill menu and include
   the task below in the same message. In Claude Code, start that message
   with the installed `/loopx` entry:

    > Investigate the failing tests in this project. Reproduce the failure,
    > fix the confirmed problem, and rerun the tests. Show me what changed
    > and leave the patch for me to review.

3. **Review the diff and the tests that actually ran.** Check that the fix
   addresses the original failure. In the same conversation, ask the assistant
   to continue the existing work or adjust the patch as needed.

The assistant uses the LoopX entry to connect the project and track the work.
If the entry does not appear, check how the skill was installed for that
assistant and reopen it. A setup preview does not mean work has started. If
the host cannot activate continuation, ask the assistant to explain the
missing step before relying on it to resume.

These steps use the entries packaged in [LoopX 2.3.0](https://github.com/loopx-project/loopx/releases/tag/v2.3.0).
For other hosts, see [which entry and continuation each host supports](../../../guides/newcomer-command-path.md#choose-the-loop-driver).
If you need a project with known failures, use the
[bug-fixing practice project](../../../guides/first-bugfix-loop.md).

[Optional: inspect the state yourself with the SQLite CLI exercise](manual-cli.md).
