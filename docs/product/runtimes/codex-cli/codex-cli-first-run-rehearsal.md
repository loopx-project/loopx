# Codex CLI First-Run Rehearsal

Status: product route and release rehearsal.

LoopX should feel easy before it feels powerful. A fresh Codex CLI user
should be able to stay in the TUI, paste one message, and see current goal
state, gates, todos, and the next safe action without cloning this repository
or reading LoopX internals first.

This route connects three shipped surfaces:

1. PyPI install/update with packaged workflow skills and an archive fallback;
2. one-message Codex CLI TUI bootstrap;
3. bounded isolated-headless turns via `loopx turn` when explicit non-TUI execution is requested.

## Fresh User Path

From the user's project repo:

1. Open Codex CLI TUI.
2. Paste one start message:

   ```text
   Start LoopX for this repo. If `loopx` is missing, install it from PyPI,
   install the packaged workflow skills, then connect this project. Show
   me the current goal, concrete user gate if any, top todos, and next safe
   action before running longer work. Keep me in this Codex CLI TUI unless I
   explicitly accept a headless fallback. After I paste this, begin the Goal
   Harness loop; do not stop after only explaining what LoopX is.
   ```

3. The agent installs or repairs LoopX when needed, using:

   ```bash
   python3 -m pip install --upgrade loopx
   loopx workflow-skills --install
   loopx doctor
   ```

4. The agent connects the repo, runs quota/status, surfaces any concrete user
   gate, and then starts one bounded validated segment if the guard permits.

The user's first useful response should fit on one screen:

- current goal id;
- user gate, or none;
- top user todo, or none;
- top agent todo;
- next safe action.

## After Install

Once `loopx` is on PATH, generate a stricter project-specific TUI message:

```bash
loopx codex-cli-bootstrap-message --project . --goal-id <goal-id> --message-only
```

Use the [no-clone release verification](codex-cli-no-clone-release-verification.md)
when validating that the packaged installer and fresh-project command surface
match this route before promotion.

## Later Automation Gate

Same-TUI automation stays optional until a safe attach primitive is proven.
Manual paste into the visible Codex CLI TUI remains primary; automated
isolated-headless execution uses `loopx turn` (`turn_driver/codex_cli.py`) as an
explicit opt-in path.

## Boundary

This first-run route must not:

- require cloning the LoopX repo;
- launch Codex as part of install/bootstrap packet generation;
- read raw Codex transcripts, session files, stdout, stderr, credentials, or
  private paths;
- spend LoopX quota before validated writeback;
- treat headless `codex exec` as the default user experience;
- claim same-open-TUI scheduled attach without an explicit attach primitive.

See also:

- [Codex CLI packaged install path](codex-cli-packaged-install.md)
- [Codex CLI TUI-first loop](codex-cli-tui-loop.md)
- [LoopX Turn quickstart](loopx-turn-codex-cli-quickstart.md)
