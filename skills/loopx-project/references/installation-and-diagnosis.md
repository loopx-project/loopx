# Installation And Diagnosis

Read when the CLI is missing, host/runtime versions disagree, or project/registry status needs diagnosis. Installer paths below refer to a trusted LoopX checkout, not the installed skill directory.

## Desktop Runtime

When asked to upgrade LoopX on a machine with the desktop App, inspect the App
bundle and its runtime as well as the CLI. `loopx doctor` exposes
`desktop_installation` for standard macOS install locations; a paired bundle
still does not prove which App process is running. Upgrade the App and its
bundled runtime together, then restart and verify the App, CLI and service
source revisions. Never claim a desktop upgrade from CLI/HTTP checks alone:
current App builds ask before replacing a different CLI runtime (update the
App, or align the CLI to the App's bundled runtime), while an older build can
still replace a separately upgraded CLI with its bundle.
Keep SSH host verification separate; a host without an App needs no App install.

## Ego Host-Skill Refresh

LoopX's normal host-skill upgrade/install path refreshes continuation guidance
for an already installed Ego browser skill. Reuse the same known idle TaskSpace
for the same authorized task without asking again merely because the previous
round finished or handed it back. Active user intervention, explicit pauses,
reserved control and unverified spaces retain the installed Ego skill's stop
rules. This is host guidance, not permission to bypass Ego's ownership checks.
After an independently authorized Ego upgrade, run `loopx workflow-skills --install`
and read `ego_browser`: it follows the current installed Ego source, preserves
custom edits and reports recovery steps. It does not upgrade Ego. App-private
runtime preparation that skips host integrations stays isolated.
Use `loopx workflow-skills --uninstall` to restore an unchanged managed Ego
skill to its current upstream link while removing LoopX-managed workflow skills.

## Preflight

Resolve the host command once before project work. On native Windows PowerShell
7, keep the installed `loopx.ps1` directory on the Windows user `PATH` and run:

```powershell
loopx doctor
```

When the current Windows executor is not PowerShell, preserve the same entry by
calling PowerShell 7 explicitly:

```text
pwsh.exe -NoLogo -NoProfile -File "$HOME/.local/bin/loopx.ps1" doctor
```

If the Windows entry is missing, run the trusted checkout installer from
PowerShell 7, then start a fresh host process so it inherits the user `PATH`:

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\install-windows.ps1 -Python (Get-Command python).Source -AddToUserPath
loopx doctor
```

On POSIX hosts, use the existing shell entry:

```bash
export PATH="$HOME/.local/bin:$PATH"
loopx doctor
```

If `loopx` is not on PATH:

```bash
install_script="$HOME/loopx/scripts/install-local.sh"
if [ -x "$install_script" ]; then
  "$install_script"
  export PATH="$HOME/.local/bin:$PATH"
fi
loopx doctor
```

If this still fails, report the exact missing piece and do not fake a successful
connection.

## Diagnose For The User

When the user asks whether LoopX is working, whether a project can
self-drive, why it is stuck, or says "diagnose LoopX", do not hand the
user shell commands. Run the diagnostic surfaces yourself and then reason from
the evidence.

Prefer the agent-facing packet:

```bash
loopx diagnose
```

If the target goal is known:

```bash
loopx diagnose --goal-id <STABLE_GOAL_ID>
```

The `diagnose` command is not the final judge. It returns compact
`status`, `quota should-run`, todo, interaction-contract, and boundary signals
plus a reasoning checklist. Use those signals as evidence, then answer in your
own words:

- whether the project can currently self-drive;
- what evidence supports that conclusion;
- what blocks autonomous delivery or self-repair;
- the exact user/controller question, if one is projected;
- what the agent will do next.

Only claim autonomous readiness when your reasoning confirms that the user gate
does not block the selected path, quota permits a turn, `goal_boundary` allows
the work, and there is a concrete agent todo or recommended action. If
`diagnose` cannot read status/quota, repair installation, PATH, registry path,
or project connection first; do not infer readiness from chat memory.

## Installed Runtime Drift

If the default `loopx` payload contradicts the just-merged source checkout or a
`PYTHONPATH=<checkout> python3 -m loopx.cli ...` cross-check, pause delivery and
run `loopx doctor` before trusting quota. The installed command is normally a
release snapshot wrapper, so a self-merged fix may require refreshing the local
install from latest trusted `origin/main` with `loopx update --execute --ref main`
or a clean main checkout's `scripts/install-local.sh`; rerun the default `loopx`
command after the refresh and only spend quota after the runtime payload matches
the repaired source behavior. A dirty or non-main checkout is canary-only by
default. Use `LOOPX_PROMOTE_DEFAULT=1 scripts/install-local.sh` only after the
checkout has passed its promotion validation and the default replacement is an
intentional write.

On native Windows, automatic archive update and rollback are fail-closed. Update
the trusted checkout, rerun `scripts/install-windows.ps1`, and verify the new
release with `loopx doctor --deep` before spending quota.

## Multi-Project Status

Inside a project:

```bash
loopx status
```

To focus the status projection on one goal while preserving global health
fields, pass the goal id:

```bash
loopx --format json status --goal-id <STABLE_GOAL_ID>
```

Outside a project, `loopx status` resolves the machine's runtime route; a fresh
installation defaults to `~/.loopx/registry.global.json`. Existing registered or
configured legacy routes remain in use until explicit migration. Diagnose route
conflicts instead of replacing a declared route with a hard-coded default.

Use explicit sync only for diagnosis or recovery:

```bash
loopx sync-global
```

If status shows `unregistered_runtime_goal`:

- If the project is active, run `loopx sync-global` or reconnect from
  that project's local registry.
- If the runtime record is obsolete, preview cleanup with
  `loopx archive-runtime --goal-id <GOAL_ID>` and only execute cleanup
  when that is clearly intended.
