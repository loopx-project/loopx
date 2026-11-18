# LoopX Workspace

Add the local Chat and Dashboard to a matching source-built
[`loopx-core`](../loopx-core/README.md) environment. Both read the Core registry
and runtime state. Installing the wheel adds the commands and web assets. Start
the service explicitly when you want to use it.

The Workspace wheel also owns the native Steward session, monitor and report
adapters and the managed Session calendar. Existing App and Lark bindings use
the same session store and typed Core authority. Standalone Core provides the
peer Inbox and its result readback.

Build the frontend and the Workspace wheel from the same checkout.

```sh
uv run --extra test python scripts/chat_bundle.py build --install
uv run --extra test python packages/loopx-core/prepare.py --distribution loopx-workspace --output /tmp/loopx-workspace-source
uv build --wheel /tmp/loopx-workspace-source --out-dir /tmp/loopx-workspace-dist
uv pip install --python /tmp/loopx-core-env/bin/python /tmp/loopx-workspace-dist/loopx_workspace-*.whl
```

Open the existing Dashboard with the registry used to run the task.

```sh
/tmp/loopx-core-env/bin/loopx --registry .loopx/registry.json dashboard
```

If Core used an explicit `--runtime-root`, pass it here as well. The same
environment also provides `loopx chat` and `loopx chat-endpoint`. Use
`--no-open` on Chat or Dashboard to start the loopback service without opening
your browser. A host and its normal prerequisites are required to send a model
request. Existing work and checked results can be viewed without invoking a
model.

Stop the local server before removing Workspace. Your existing Core commands
continue to use the same registry and task records.

```sh
uv pip uninstall --python /tmp/loopx-core-env/bin/python loopx-workspace
```

This candidate covers the local browser surface. Split public releases, native
desktop installation and IM journeys retain their RFC acceptance requirements.
