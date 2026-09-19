# Installing LoopX

PyPI is the default LoopX release channel. Use Python 3.11 or later in an
active virtual environment, a managed user environment, or another environment
whose console scripts are on `PATH`:

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

Choose one installation owner and keep it authoritative:

| Use case | Owner | Install or acquire | Upgrade |
| --- | --- | --- | --- |
| Normal release | Python package environment | `python3 -m pip install loopx` | `loopx update apply` or the manual pip sequence below |
| Isolated CLI on an externally managed machine | `pipx` | `pipx install loopx` | `pipx upgrade loopx`, then refresh LoopX host material |
| Contributor or source qualification | Git checkout | clone/fetch plus `scripts/install-local.sh` | update the checkout explicitly, rerun the installer, validate `loopx-canary` before promotion |
| No-clone recovery fallback | LoopX archive snapshot | published archive installer | `loopx update apply` |

PyPI is the canonical source for normal releases. A source checkout is a
development and qualification surface, not a second implicit package channel.
The archive snapshot remains a recovery path rather than a competing default.

LoopX's Effect Program core runs in a managed, idle-exiting TypeScript runtime
and requires Node.js 22.18.0 or later; Node.js 24 LTS is the recommended primary
runtime. LoopX starts and reuses that local runtime automatically; users do not
run a daemon manually. The runtime binds only to loopback, authenticates
requests with a user-private token, rotates when the packaged Effect core
changes, and exits after an idle period. `loopx doctor`
reports it as `ready`, `missing`, `unsupported`, or `probe_failed`; a missing
or stale runtime fails closed instead of falling back to a second Python rule
engine. The same doctor projection exposes `runtime_lifecycle.state` as
`running`, `stopped`, or `unavailable`, plus a public-safe `diagnostic_code`;
the App can render this projection without inventing a second health model.
`stopped` is healthy and means the idle-exited runtime will restart on the next
control-plane request. Validate Node before installing or upgrading LoopX:

The CI and release lanes use Node.js 24 LTS. Node.js 26 remains a non-blocking
forward-compatibility probe and is not a supported-version promise. After the
Node.js 22 maintenance window ends, a separate policy change will raise the
minimum to Node.js 24.12 or newer and remove no-longer-needed experimental
flags.

```bash
node --version
# v22.18.0 or newer
```

Use `loopx doctor --deep` after installation to start the managed runtime and
exercise the packaged Effect semantics and native journal checkpoint handler.
`workflow-skills --install` copies the packaged LoopX workflow skills into the
user's Codex skill directory and writes a revision readback; it does not change
project state or grant repository, network, or merge authority. Restart the
host after first install so it reloads the new skills.

If Python packages are externally managed on the machine, use a dedicated tool
environment instead of modifying the system interpreter:

```bash
pipx install loopx
loopx workflow-skills --install
loopx doctor
```

## Native Windows PowerShell 7

The PyPI distribution is also the default native Windows path. From PowerShell
7, use a Python 3.11+ interpreter and validate the installed console script:

```powershell
py -3.11 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

Contributors and operators who deliberately need an immutable snapshot from a
trusted checkout can install the native PowerShell launcher without Bash,
POSIX symlinks, or WSL:

```powershell
git clone https://github.com/loopx-project/loopx.git "$HOME/loopx"
Set-Location "$HOME/loopx"
pwsh -NoLogo -NoProfile -File .\scripts\install-windows.ps1 `
  -Python (Get-Command python).Source `
  -AddToUserPath
loopx doctor --deep
```

The snapshot installer validates the candidate before promotion, writes an
atomic release pointer, and places `loopx.ps1` plus its release-pointer sidecar
in `$HOME/.local/bin` by default. `-InstallRoot`, `-BinDir`, and `-SkillsDir`
remain explicit overrides; a fresh shell discovers a custom install through
the sidecar, without requiring `LOOPX_CURRENT_RELEASE_FILE`. Use `-SkipSkills`
when another trusted host manager owns the LoopX skill files, and omit
`-AddToUserPath` when PATH changes are not authorized.

Native snapshot `loopx update` and automatic rollback fail closed. To upgrade
or roll back, check out the intended trusted revision, rerun
`scripts/install-windows.ps1`, then verify `loopx doctor --deep`. The previous
release directories remain under the selected install root until the operator
removes them; changing a pointer by hand is not the supported rollback path.

Before removing a native snapshot, run the managed host uninstallers while the
launcher is still available, then remove the launcher files and snapshot root:

```powershell
loopx slash-commands --uninstall
loopx workflow-skills --uninstall
Remove-Item -LiteralPath "$HOME/.local/bin/loopx.ps1" -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "$HOME/.local/bin/loopx-current-release.json" -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "$HOME/.local/share/loopx" -Recurse -Force -ErrorAction SilentlyContinue
```

If installation used `-AddToUserPath`, remove the chosen `BinDir` from the
Windows user PATH through Windows Environment Variables after uninstalling.
Installation and PATH opt-in only expose local command and skill files. They do
not grant repository, network, credential, external-system, or merge authority,
and uninstall does not delete project-local `.loopx/`, `.codex/goals/`, or
evidence state.

## Host Command Surfaces

The workflow-skill command installs the rich Codex workflows and managed
`$loopx` entry. Install additional command facades only for hosts that need
them:

```bash
loopx slash-commands --install
```

The default command-facade set covers Codex, Claude Code, and OpenCode. Other
surfaces remain explicit; inspect `loopx slash-commands --help` before enabling
one. Host integration changes command discovery only. It does not grant LoopX
permission to write a repository, contact external systems, or bypass a user
gate.

Codex installs expose only canonical `loopx-*` skills. Older managed
`loop-global-*` skill aliases are retired; their catalog entries and native
slash-host compatibility remain available. This changes the Codex picker,
not goal execution or write authority.

Both workflow and command installation reconcile managed duplicates between
`CODEX_HOME/skills` (default `~/.codex/skills`) and `~/.agents/skills`.
The selected installation root wins only when a replacement exists; a command
facade cannot replace a rich workflow. Modified receipts, user-owned metadata,
extra files, sole copies, and skill symlinks are preserved and reported under
`skill_reconciliation` or `codex_skill_reconciliation`. A custom Codex profile
does not authorize cleaning another profile. The fixed installer permits
`LOOPX_SKILL_DEDUPE_OTHER_ROOT=0` for intentionally separate host roots.

To inspect and repair command discovery for the current Codex profile:

```bash
loopx --format json slash-commands --surface codex --dry-run
loopx --format json slash-commands --surface codex --install
```

Read the reconciliation result and resolve preserved conflicts explicitly.

If duplicates return after successful reconciliation, inspect the host's
external-agent import history. Importing Claude Code skills can recreate
LoopX command facades in the shared `.agents/skills` root after installation,
including legacy aliases. Exclude LoopX from subsequent imports when its
Codex skills are already installed, then rerun the repair above. LoopX's
installer does not control a host's later import jobs or import settings.
Generated Codex entries include `agents/openai.yaml` with the `LoopX` display
name and explicit-invocation policy; `workflow-skills --install` also repairs
missing managed entry metadata while preserving user-owned metadata.

A running session may retain its original skill catalog; reload the host or
open a new task before checking discovery again. Installation cannot rewrite
instructions already loaded into a conversation.

## Skill Discovery Scope

Default workflow installation keeps `loopx-project`, `loopx-self-repair`,
`loopx-pr-review`, `loopx-pr-program`, `loopx-doc-registry`, and `loopx-benchmark`
globally visible in the selected host profile, alongside the generated
`loopx` entry. These are reusable LoopX instructions: connection and repair
must work before project setup, PR workflows can span repositories, and
document/benchmark workflows retain their existing connected-project or
LoopX-task triggers. Visibility does not activate a capability or authorize
state changes, external publication, or benchmark jobs.

The `loopx-global-summary`, `loopx-global-gates`, `loopx-global-todos`, and
`loopx-global-risks` command skills also belong in the global host root. Install
and inspect them through `loopx slash-commands --surface codex --install` and
`loopx slash-commands --surface codex --dry-run`. They inspect the registry
visible to the current user; discovery never expands account or project access.
They remain optional command facades, separate from default workflow delivery.

`loopx-material` and `loopx-change-quality` remain project-only by default.
The release's `.loopx-skill-scope` marker declares `global` or `project`;
both declared scopes support an explicit managed project copy:

```bash
loopx project-skill install --project . --skill loopx-self-repair --surface codex
loopx project-skill install --project . --skill loopx-self-repair --surface codex --execute
loopx project-skill status --project . --skill loopx-self-repair --surface codex
loopx project-skill uninstall --project . --skill loopx-self-repair --surface codex --execute
```

Use a project copy only for an intentional override or isolated host. It leaves
global copies untouched and retains project connection, ownership, digest,
and containment checks. Missing or unknown source markers still fail closed.
See [Project Skill Delivery](../../loopx/capabilities/project_skill_delivery/README.md)
for lifecycle details. `loopx doctor` continues to require the complete default
workflow set; a lone generated entry does not replace missing rich workflows.

## Upgrade And Repair

`loopx update` is the channel-aware upgrade entry point. Its actions have the
same meaning for humans and agents:

```bash
loopx update check       # read-only freshness and installation-owner check
loopx update plan        # read-only command, validation, and rollback plan
loopx update apply       # explicit local-environment mutation
```

For archive installs, `update apply` downloads the bootstrap installer to a
private temporary file before executing it. Downloading is limited to three
attempts, a 60-second total download budget (or the smaller command timeout),
and 20 seconds per transfer, with 1- and 2-second retry delays. Transient
403/408/429/500/502/503/504 responses and selected connection/transfer failures
are retried; 401/404 and certificate-verification failures stop immediately.
Installer execution is never retried, and partial downloads are never executed.
The command timeout covers downloading and installer execution together.

JSON `execution.installer_download` and the text execution report show the
stage and each attempt's HTTP status and curl exit code. HTTP `0` means no
usable HTTP status was received. Download diagnostics exclude URLs, response
bodies, headers, and raw curl errors so proxy credentials and signed parameters
cannot leak. These retries cover the bootstrap download, not subsequent
archive downloads or a failed installation. Pip/pipx and read-only plans keep
their existing behavior.

Bare `loopx update` remains a read-only plan. The older `--check`, `--dry-run`,
and `--execute` spellings remain compatibility aliases, but new instructions
should use the named actions.

Human-readable output starts with **No update was applied** and a copyable
**Next Action** command. JSON output exposes the same decision as
`requested_action`, `changes_applied`, and a typed `next_action` object with
mutation and explicit-approval fields. Agents should inspect those fields instead
of inferring authority from prose.

For a PyPI distribution installed by pip, `update apply` asks the exact Python
interpreter that owns LoopX to upgrade its environment, then starts fresh
processes to install workflow skills and slash commands, runs doctor, revalidates
enabled extensions, and restarts managed local LoopX services. It does not
switch the installation to an archive snapshot.

The equivalent manual sequence is:

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx slash-commands --install
loopx doctor
```

The first command is the package transaction; the remaining commands are the
LoopX activation and readback contract. Running only `pip install` can leave
host material from the previous version active. Conversely, `loopx update
apply` does not replace pip as the owner of dependency resolution, environment
policy, package indexes, or uninstall.

### Verify The Active Layers

An upgrade is not fully qualified just because the package-manager step exits
successfully. Read back each layer that can remain stale independently:

| Layer | Readback | What success proves | Recovery when it is not ready |
| --- | --- | --- | --- |
| Installation owner and package | `loopx update check` | The active executable, package owner, freshness, and next action are identified without mutation. | Follow the reported owner command; do not mix pip, pipx, archive, and source-checkout upgrade paths. |
| Host material | `loopx --format json doctor` | `skill_delivery.status` describes the workflow-skill delivery used by the active host. | Run `loopx workflow-skills --install`; refresh `loopx slash-commands --install` when those facades are in use, then restart the host. |
| Managed Effect runtime | `loopx doctor --deep` | The packaged TypeScript Effect runtime can start and answer the deep probe. An idle-exited `stopped` lifecycle remains healthy. | Apply the doctor recommendation or reinstall the selected package version; do not substitute a second Python rule path. |
| Enabled extensions | `loopx extension doctor --all-enabled --execute --format json` | Every enabled extension has a current runtime identity and passes its readiness checks. | Repair the named provider or extension and rerun its doctor; a failed provider remains closed. |

`loopx update apply` runs the host-material refresh, core doctor, and enabled
extension doctor after the owning package or archive update succeeds. The
standalone commands above are still useful as independent readbacks and
recovery entry points; rerunning them does not change the installation owner.

These checks also prevent a common release mistake: code merged to `main` is
not necessarily active in an installed release. Archive maintainers may use
`loopx update check --ref main` to inspect
`runtime_activation_qualification`; ordinary pip and pipx users should stay on
the tagged package channel and wait for the corresponding release instead of
trying to switch an installed distribution to `main`.

An install taken from a pinned full commit SHA needs no separate lineage
lookup: `loopx update check --ref <40-hex-commit>` compares that commit with the
installed manifest source commit and reports `runtime_active` when they match,
so the readback you would use to confirm your own pinned install stays
actionable.

For an installation owned by another Python package manager, `update plan`
reports that owner and its command; LoopX fails closed instead of guessing a
pip mutation. For a live source checkout, it reports the contributor installer
and never performs `git pull` or rewrites the worktree. Source acquisition and
repository mutation remain explicit human or authorized-agent actions.

`loopx doctor` reports `install_kind: python_distribution` for this path and
returns the same pip-native repair sequence when packaged skills are missing or
stale. It also verifies the required TypeScript Effect runtime before a
control-plane upgrade is considered healthy. Runtime metadata is fingerprinted
by the installed sources, so an upgraded LoopX starts a matching process while
an older process exits after becoming idle. When a release needs to be rolled
back, reinstall the previously selected version, refresh the packaged host
material, and validate again:

```bash
python3 -m pip install "loopx==<previous-version>"
loopx workflow-skills --install
loopx slash-commands --install
loopx doctor
```

## Archive Fallback

The GitHub Pages installer remains a fallback for machines where an appropriate
Python package environment is unavailable or the CLI is too damaged to run its
own repair path:

```bash
curl -fsSL https://loopx-project.github.io/loopx/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
loopx doctor
```

This fallback installs an archive snapshot, wrapper, man page, and host
materials together. LoopX owns that snapshot lifecycle, so its update path is:

```bash
loopx update check
loopx update plan
loopx update apply
```

Archive apply retains the atomic release pointer, doctor validation, extension
readback, managed-service restart, and first-class snapshot rollback. The
installation-owner projection prevents this path from being mixed with an
active PyPI executable.

## Uninstall

Remove LoopX-owned host material before uninstalling the Python package:

```bash
loopx slash-commands --uninstall
loopx workflow-skills --uninstall
python3 -m pip uninstall loopx
```

Both host uninstallers preserve same-name files whose content changed after
LoopX installed them. Project-local `.loopx/`, `.codex/goals/`, evidence, and
runtime state are not deleted by package uninstall.

Contributors who need a live canary should use a real checkout and
`scripts/install-local.sh`; see [Getting Started](getting-started.md).
