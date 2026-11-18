# LoopX pull-request review for Core

Add the existing `loopx pr-review` command to a standalone Core installation.
Use a wheel built from the same LoopX release as Core. Core reads the catalog
and retains Goal and machine review settings on its own. The wheel supplies
queue observation, exact-head review packets and readiness checks.

Build from the repository root. Set `LOOPX_CORE_PYTHON` to the interpreter in
the installed Core environment and use that environment for the commands below.

```sh
python packages/loopx-core/prepare.py \
  --distribution loopx-capability-pull-request-review --output /tmp/loopx-review-source
uv build --wheel /tmp/loopx-review-source --out-dir /tmp/loopx-review-dist
uv pip install --python "$LOOPX_CORE_PYTHON" /tmp/loopx-review-dist/loopx_capability_pull_request_review-*.whl
loopx capability show pull-request-review --format json
loopx pr-review --help
```

The catalog reports availability once the matching wheel owns the command.
Review uses the existing explicit CLI and existing Goal configuration.
Installation grants no GitHub authority. Use `--fixture` for an offline queue
observation. Live observations require the existing GitHub access, and external
review publication and merge remain separate operations.

Remove the wheel from the Core environment to disable the command. Saved
checkpoints and Goal or machine settings remain available to a later matching
install. Core task execution and recovery use their existing owners.

See the [pull-request review capability](../../loopx/capabilities/pr_review_queue/README.md)
for the command, input contract and exact-head review semantics.
