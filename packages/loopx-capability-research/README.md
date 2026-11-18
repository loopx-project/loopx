# Optional research workflows for LoopX Core

Install this wheel to resume an evidence-ledger research run or use external
evidence with a matching standalone Core. The existing `deepresearch` and
`external-evidence` commands use their original owners and project records.
The ordinary `loopx` distribution already includes both workflows.

Build from the same checkout as the Core wheel and install it into that
environment:

```sh
uv run --extra test python packages/loopx-core/prepare.py \
  --distribution loopx-capability-research --output /tmp/loopx-research-source
uv build --wheel /tmp/loopx-research-source --out-dir /tmp/loopx-research-dist
uv pip install --python /tmp/loopx-core-env/bin/python /tmp/loopx-research-dist/loopx_capability_research-*.whl
```

Check availability with `loopx capability show deep-research` and
`loopx capability show external-evidence-research`. Resume with
`loopx deepresearch status --project <project>` or start with
`loopx deepresearch start --project <project> --question <question>`.
Use `loopx external-evidence discover` to inspect evidence methods and
`loopx external-evidence plan --help` to see the available planning inputs.
External provider execution and evidence admission still require their own
explicit commands and scope.

The wheel pins the exact Core version. To remove it, run
`uv pip uninstall --python /tmp/loopx-core-env/bin/python loopx-capability-research`
after exiting any open research command. Project ledgers and reports remain
available on disk and can be reopened by reinstalling the matching wheel.
