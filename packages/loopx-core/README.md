# Standalone core distribution candidate

This opt-in source build installs the existing CLI and TypeScript task runtime
without the Chat package, web assets or canary fleet. It uses the same state,
authority, validation and recovery owners as the ordinary distribution. It does
not start a workspace, install a model host or change existing defaults.

Build from a source checkout, then install into a **new** virtual environment:

```sh
uv run --extra test python packages/loopx-core/prepare.py --output /tmp/loopx-core-source
uv build /tmp/loopx-core-source --out-dir /tmp/loopx-core-dist
uv venv /tmp/loopx-core-env
uv pip install --python /tmp/loopx-core-env/bin/python /tmp/loopx-core-dist/loopx_core-*.whl
/tmp/loopx-core-env/bin/loopx --help
/tmp/loopx-core-env/bin/loopx doctor --installation-only --deep --format json
```

Python 3.11+ is required. Import and help do not need Node. Executing governed
tasks requires a qualified Node runtime (22.22.3+ and the existing SQLite
reliability checks), an explicitly selected host and its own prerequisites.
Use the existing `turn run-once --host generic-cli` entry, a separate validation
command, `turn inspect-journal` and `--resume-turn-key` for execution and recovery.

When recovering a generic CLI Turn with a saved result, use the original
`--resume-turn-key`, Goal, Agent, and approved project path. Omit
`--host-adapter-command-json` and `--host-adapter-request-schema`. If the result
still needs validation, supply its independent `--validation-command-json`.
An already settled result can be read back without either command. The recovery
decision asks for the adapter if the original Turn needs another Host attempt.

Start with one natural-language goal in your existing model host:

```sh
loopx start-goal --guided --project . --goal-text "Create and check an inventory report."
```

Follow its returned host choice and ordered transaction. The preview preserves
the exact goal text and writes no state. Its project bootstrap, fresh identity
registration, session binding and subsequent Todo/guard commands execute in
this standalone distribution through their existing owners. Generated commands
use this environment's Python with `-I -m loopx.entrypoint`, so continuation and
installation diagnosis keep using the same core even when PATH or PYTHONPATH
points to another installation. An explicit global `--cli-bin` retains the
caller's selected executable. Deep diagnosis still requires recorded module and
console entrypoints; redirected or missing records fail. The host supplies
the model, tools and scheduler; the wheel does not provide them or activate an
optional capability from goal wording. `connect` and `configure-goal` remain
available for explicit setup. Create work through `todo add`. With an explicitly
selected `generic-cli` v1 adapter and an independent validator, `turn run-once
--execute` prepares a missing lease for the eligible Todo before dispatch.
Completion and recovery use the original captured lease generation. Recovery
refuses a renewed, released or replaced lease. The ordinary `todo claim`
command is available for explicit preparation.
The installed-only qualification in
[`tests/test_core_distribution.py`](../../tests/test_core_distribution.py)
exercises setup, independent result validation, process loss, replay and refusal.
No model service, credentials or paid task execution is supplied by this wheel.

Ordinary Core goal previews omit Issue Fix command templates. Install the
[matching workflow wheel](../loopx-capability-issue-fix/README.md) to select
`--capability-route issue-fix` or run `loopx issue-fix`. Core retains existing
PR-wait observations, confirmed review reconciliation, and their state.
The installed context qualification
in [`test_installed_core_context.py`](../../tests/control_plane/test_installed_core_context.py)
executes the existing `quota context-project`, `context-read` and `context-hook`
commands: incomplete delivery cannot establish a baseline, revisions preserve
complete constraints, compaction requires recovery, and changed capture bytes
are refused. Its native events are protocol fixtures; they do not establish
model retention or installed-host adoption.

`capability show agent-turn-recall` reports the optional recall implementation
as unavailable in Core alone. Install the [matching recall wheel](../loopx-capability-agent-turn-recall/README.md)
in the Core environment to make its existing command available. Recall starts
only with the existing explicit Goal/Agent configuration. When the implementation
is unavailable, an enabled Goal records that fact and continues the task.
Outcome ingest uses its own configuration and recovery path.

`capability show pull-request-review` reports whether the [matching PR review
wheel](../loopx-capability-pull-request-review/README.md) is installed. Core
retains its catalog and settings. The add-on provides the existing `pr-review`
command on explicit use. Removing it preserves review settings and checkpoints.

### Standard CLI composition

Install `loopx-core[standard]` to select Agent Turn Recall, Pull Request Review
and Issue Fix together. Build the three companion wheels into the same
directory as the Core wheel, using the same checkout. Set `LOOPX_CORE_PYTHON`
to the interpreter of a new environment or the matching Core environment.

```sh
for capability in agent-turn-recall pull-request-review issue-fix; do
  distribution="loopx-capability-$capability"
  python packages/loopx-core/prepare.py --distribution "$distribution" --output "/tmp/$distribution-source"
  uv build --wheel "/tmp/$distribution-source" --out-dir /tmp/loopx-core-dist
done
uv pip install --no-index --find-links /tmp/loopx-core-dist \
  --reinstall-package loopx-core --python "$LOOPX_CORE_PYTHON" 'loopx-core[standard]'
"$LOOPX_CORE_PYTHON" -I -m loopx.entrypoint capability list --format json
```

The reinstall option reads the selected wheel's metadata when an earlier
source build of the same version is already installed.

Each ability can also be installed on its own. Use the existing commands and
Goal settings to select a workflow. To return to Core-only installation,
uninstall the three companion wheels in the same environment:

```sh
uv pip uninstall --python "$LOOPX_CORE_PYTHON" \
  loopx-capability-agent-turn-recall loopx-capability-pull-request-review loopx-capability-issue-fix
```

The candidate owns the same `loopx` import namespace and console command as the
ordinary distribution. Do not co-install or overlay them. Return to the ordinary
installation by switching back to its environment; preserve task state and
reconcile the original attempt before changing an execution owner. There is no
new default, migration, automatic upgrade, PyPI publication or platform release.

This is the first independently installable core-artifact slice of R1/M3 in the
[small-kernel RFC](../../docs/architecture/rfcs/monorepo-distribution-split-v0.md).
It still retains built-in capability implementations, policy helpers and several
host/CLI dependencies because the current imports are coupled. This makes it a
workspace-free installation prerequisite, not a qualified minimal mechanism
closure. Those dependencies are migration debt, not evidence that every
capability belongs in the kernel. Full workspace/meta-package splitting,
the remaining capability packages, other host/platform qualification and the
complexity/coverage hypothesis remain separate work. Smaller installation or a
passing process fixture alone does not establish model effectiveness.
