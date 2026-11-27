# LoopX Issue Fix for Core

Add the existing `loopx issue-fix` commands to a standalone Core installation.
Use the wheel built from the same release as Core. Install it in the Core
environment and inspect the catalog before using the workflow.

```sh
python packages/loopx-core/prepare.py \
  --distribution loopx-capability-issue-fix --output /tmp/loopx-issue-fix-source
uv build --wheel /tmp/loopx-issue-fix-source --out-dir /tmp/loopx-issue-fix-dist
uv pip install --python "$LOOPX_CORE_PYTHON" /tmp/loopx-issue-fix-dist/loopx_capability_issue_fix-*.whl
loopx capability show issue-fix --format json
loopx issue-fix --help
```

Set `LOOPX_CORE_PYTHON` to the interpreter in the installed Core environment.
Use that same environment for the CLI. A guided start selects this workflow
only when the caller passes `--capability-route issue-fix`. The
`repo-branch-fixture` subcommand exercises the offline validation loop in a
temporary local repository. Real repository actions use existing evidence and
authority requirements.

Removing the wheel leaves original Goal and work state intact. Core retains its
existing PR dependency observations and acknowledgement recovery. Ordinary
LoopX also supplies the existing `content-ops` and `value-connectors`
compatibility commands.

See the [Issue Fix capability](../../loopx/capabilities/issue_fix/README.md) for
the workflow, authority and evidence gates.
