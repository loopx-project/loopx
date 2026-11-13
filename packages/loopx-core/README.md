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
available for explicit setup. Create and claim work through the existing `todo`
commands. An ordinary managed claim
prepares the default SQLite lease without asking the task model to manage lease
flags. Completion carries the original captured lease generation; recovery
refuses a renewed, released or replaced generation rather than adopting it.
The installed-only qualification in
[`tests/test_core_distribution.py`](../../tests/test_core_distribution.py)
exercises setup, independent result validation, process loss, replay and refusal.
No model service, credentials or paid task execution is supplied by this wheel.

Goal-start help and preview use issue-fix command metadata without importing
its execution providers. An explicit `--capability-route issue-fix` retains
the existing candidate-preflight contract. The installed context qualification
in [`test_installed_core_context.py`](../../tests/control_plane/test_installed_core_context.py)
executes the existing `quota context-project`, `context-read` and `context-hook`
commands: incomplete delivery cannot establish a baseline, revisions preserve
complete constraints, compaction requires recovery, and changed capture bytes
are refused. Its native events are protocol fixtures; they do not establish
model retention or installed-host adoption.

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
independent capability packaging, other host/platform qualification and the
complexity/coverage hypothesis remain separate work. Smaller installation or a
passing process fixture alone does not establish model effectiveness.
