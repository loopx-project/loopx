# Agent Turn Recall for LoopX Core

This optional wheel supplies the existing built-in Agent Turn Recall command and
implementation. It is built from the same checkout as `loopx-core` and requires
that exact Core version. Install the two wheels in one isolated environment.
The ordinary `loopx` wheel supplies its own bundled recall implementation and
uses a separate environment.

From the repository root, stage and build the matched artifacts outside the
checkout:

```sh
build_dir="$(mktemp -d)"
python packages/loopx-core/prepare.py --output "$build_dir/core"
python packages/loopx-core/prepare.py --distribution loopx-capability-agent-turn-recall \
  --output "$build_dir/recall"
uv build --wheel "$build_dir/core" --out-dir "$build_dir/wheels"
uv build --wheel "$build_dir/recall" --out-dir "$build_dir/wheels"
uv venv "$build_dir/env"
uv pip install --python "$build_dir/env/bin/python" \
  "$build_dir"/wheels/loopx_core-*.whl "$build_dir"/wheels/loopx_capability_agent_turn_recall-*.whl
"$build_dir/env/bin/loopx" capability show agent-turn-recall
"$build_dir/env/bin/loopx" agent-turn-recall --help
```

Installing the wheel makes the command available. Recall uses the existing
per-Goal/Agent configuration, provider authorization, and receipt scope.
Follow the [capability guide](../../loopx/capabilities/agent_turn_recall/README.md)
for preview, explicit enablement, and validation. Uninstall
`loopx-capability-agent-turn-recall` from the same environment and start a new
LoopX process to return to Core-only execution. The Core catalog reports recall
as unavailable and retains the existing task, configuration, and receipt
owners.
