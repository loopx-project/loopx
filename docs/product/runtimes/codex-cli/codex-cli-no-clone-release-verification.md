# Codex CLI No-Clone Release Verification

Status: release verification note.

The Codex CLI first-run route can be advertised as the preferred interactive
path only when the shipped command surface matches the docs. The user should
not have to clone LoopX before trying it from a project repo.

## Verification Shape

The release verifier exercises the path as a fresh user would see it:

1. create a temporary HOME and a temporary project repo;
2. run `scripts/install-from-github.sh` with the same archive-installer
   mechanism used by the public no-clone command;
3. confirm the installed `loopx` wrapper can run `doctor`;
4. confirm the installed top-level help points at `loopx commands`, and the
   installed command reference exposes the first-run command
   `codex-cli-bootstrap-message`;
5. generate the one-message TUI bootstrap text from the fresh project.

The verifier does not launch Codex, read transcripts, read session files, read
credentials, mutate a Codex session, or spend LoopX quota.

Run it with:

```bash
python3 examples/release/codex-cli-no-clone-release-verification-smoke.py
```

## Current Result

Current archive route: **qualified as the recovery fallback, with one boundary**.

Ready:

- the archive installer creates a stable release snapshot without requiring a
  local LoopX checkout;
- the installed wrapper exposes the first-run TUI bootstrap command;
- the generated bootstrap text tells the agent to install, connect, run quota,
  preserve the visible TUI, write back evidence, and avoid raw transcripts.

Boundary:

- public install still depends on network access to GitHub archive endpoints
  unless the caller overrides `LOOPX_ARCHIVE_URL` with a trusted archive;
- same-TUI automation is not the default path; bounded isolated-headless runs
  use `loopx turn`.

That means the archive route can remain a tested recovery option while PyPI is
the default and contributor clone-plus-canary remains the development path.

## Release Checklist

Before promoting a new release snapshot, run:

```bash
python3 examples/release/codex-cli-no-clone-release-verification-smoke.py
python3 examples/codex-cli-first-run-rehearsal-smoke.py
python3 examples/codex-cli-bootstrap-message-smoke.py
```

If the first command fails, do not advertise the archive fallback as qualified
until the failure is reduced to a compact blocker: missing installer
dependency, archive layout mismatch, missing installed command, or broken
bootstrap generation.

## Boundary

Keep this verification public-safe:

- no raw Codex transcript or session material;
- no credentials, auth material, or private local paths;
- no benchmark logs, task text, trajectories, or production evidence;
- no Codex execution as part of the verifier.

See also:

- [Codex CLI packaged install path](codex-cli-packaged-install.md)
- [Codex CLI first-run rehearsal](codex-cli-first-run-rehearsal.md)
- [LoopX Turn quickstart](loopx-turn-codex-cli-quickstart.md)
