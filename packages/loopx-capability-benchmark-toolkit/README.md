# Benchmark Toolkit for LoopX Core

Install this source-built wheel when you need benchmark commands or registered
analysis packs. It uses the same version of [LoopX Core](../loopx-core/README.md)
and the same Goal registry and runtime state. Capability discovery reads its
catalog without loading the toolkit's execution modules.

Build both wheels from the same checkout and add the toolkit to your Core environment.

```sh
uv run --extra test python packages/loopx-core/prepare.py --distribution loopx-capability-benchmark-toolkit --output /tmp/loopx-bench-source
uv build --wheel /tmp/loopx-bench-source --out-dir /tmp/loopx-bench-dist
uv pip install --python /tmp/loopx-core-env/bin/python /tmp/loopx-bench-dist/loopx_capability_benchmark_toolkit-*.whl
```

Check readiness and discover a registered Goal's analysis packs using the same
registry and runtime you used to run it.

```sh
/tmp/loopx-core-env/bin/loopx capability show benchmark-toolkit
/tmp/loopx-core-env/bin/loopx --registry .loopx/registry.json benchmark trajectory-packs --goal-id "<goal-id>"
```

Pass the original `--runtime-root` if you supplied one. The toolkit exposes the
existing authority and evidence checks. See the
[capability guide](../../loopx/capabilities/benchmark_toolkit/README.md) for
benchmark families and execution prerequisites.

When using a matching Workspace wheel, restart Workspace after adding or
removing the toolkit. Research Analysis reads the same registered packs.
Remove the toolkit when you no longer need those commands. The existing Goal
registry, task history and analysis records remain in your runtime directory.

```sh
uv pip uninstall --python /tmp/loopx-core-env/bin/python loopx-capability-benchmark-toolkit
```
