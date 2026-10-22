# LoopX Worker Bridge Install Contract

The worker bridge is a runner-neutral way to make the LoopX CLI available in
an isolated executor. It declares source/runtime mounts, a Python preflight,
compact counter tracing, and the optional active-user update channel. It does
not own benchmark result schemas, scoring, uploads, or submission.

Preview the contract locally:

```bash
loopx worker-bridge contract --format json
```

The default payload uses public placeholders and grants no execution authority:

```text
schema_version=loopx_worker_bridge_install_contract_v0
install_mode=source_mount_read_only_pythonpath
loopx_command_prefix=PYTHONPATH='<loopx-project-root>' python3 -m loopx.cli
loopx_counter_trace_json=/logs/agent/loopx-counter-trace.jsonl
```

An executor may translate the returned `mounts` and `agent_kwargs` into its own
container, virtual environment, or sidecar configuration. Private launchers may
substitute real host paths, but those paths, credentials, transcripts, and raw
tool output must not enter public status or evidence artifacts.

For an active-user collaboration lane, request a writable feed mount and use
the pull-based commands documented by the returned
`active_user_intervention_channel_contract`. This channel does not expose hidden
tests, expected solutions, evaluator answers, credentials, or private project
material, and it does not authorize score or leaderboard claims.

The same `worker-bridge` surface also carries the provider-neutral
[`attached Agent session broker`](attached-agent-session-broker.md). That
broker binds an already-running host session and lets that exact host claim and
complete queued Web or Connector messages. It never starts a replacement
runtime. Host session ids, message bodies, and response files remain
owner-local.

The retired benchmark result/writeback layer is preserved for source
archaeology in
[Git history](https://github.com/loopx-project/loopx/tree/54d42da44283bb414b24f71d16642c25cbcaf5dd/deprecate/benchmark-legacy),
outside the working tree.
New benchmark work should start from the
[`benchmark/`](https://github.com/huangruiteng/loopx/blob/main/benchmark/README.md) research workspace and keep runner and
verifier semantics benchmark-native.

## Validation

```bash
python3 examples/cli-worker-bridge-command-modularization-smoke.py
python3 examples/worker-bridge-active-user-feed-smoke.py
```
