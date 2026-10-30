# OAR-managed agent runtimes

LoopX uses **`@botiverse/oar@0.75.0`** for the sessions, events, controls and
cleanup of its bounded Codex, Pi and DeepSeek Harness executions. LoopX still
owns Turn admission, signed work authority, independent validation, quota and
durable work settlement. An OAR completion is a host observation, never proof
that a Goal or Todo is complete.

## Install and inspect

Use the Python environment and operating-system user that run LoopX. OAR needs
**Node.js 24+ and npm**; the rest of LoopX retains its Node.js 22.22.3 minimum.

```bash
python -m loopx.hosts.oar install
python -m loopx.hosts.oar status
```

Installation uses the packaged npm lock with lifecycle scripts disabled. Source
and dependencies are published together in a cache keyed by the source digest.
`LOOPX_OAR_HOME` can select the cache root. Normal Turns and availability probes
do not install, upgrade or contact npm. Missing dependencies produce an
unavailable result with the installation action. Updating LoopX requires running
the explicit install command again when its runtime source or lock changes;
previous source revisions remain independently usable.

Source contributors can run `npm ci --ignore-scripts --prefix loopx/hosts/oar`.
Python wheels include the TypeScript source, package manifest and lock; they do
not include `node_modules` or require a repository checkout at runtime.

## Select a runtime

| LoopX entry point | OAR runtime | Additional setup |
| --- | --- | --- |
| `loopx turn run-once --host codex-cli` | Built-in Codex app-server runtime | Existing Codex CLI and its configured authentication; existing `--codex-*` options remain available |
| `loopx turn run-once --host dsh` and `python -m loopx.dsh_goal_mode` | LoopX DSH runtime backed by the pinned Python SDK | Install `loopx[deepseek-harness]` and configure the existing provider/model credentials |
| Managed DSH Chat segments | Same DSH runtime | Select DSH through the existing restricted steward profile; segments retain `DSH_PERMISSION_MODE=read-only` |
| Explicit `generic-cli` Pi Turn | Built-in Pi runtime in subprocess mode | Configure Pi's provider and model; the pinned OAR dependency supplies the matching Pi runtime |

The Codex adapter preserves the requested sandbox, model, effort, service tier,
schema, MCP configuration and native session binding. A missing resumed session
does not silently become a new session. The native process receives the requested
sandbox explicitly, including when an ambient OAR default would be more
permissive. OAR controls must be accepted before LoopX treats them as submitted.

For Pi, select this command through the existing signed generic-cli contract:

```bash
loopx turn run-once \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --host generic-cli \
  --project "$PWD" \
  --host-adapter-command-json '["python","-m","loopx.hosts.oar","turn","--runtime","pi","--model","<provider>/<model>"]' \
  --validation-command-json '["./verify-postcondition"]' \
  --execute
```

The model option is optional when Pi already has a configured default. The
adapter also accepts `--effort`, `--cwd` and `--timeout-seconds`. Pi uses its
native tools with the operating-system permissions of the selected process;
OAR does not add an OS sandbox to Pi. The existing independent validator must
still accept a candidate before LoopX commits work. An arbitrary separately
installed Pi binary is not substituted for OAR's matching bundled version.

## Lifecycle and compatibility

`runtime.ts` lazily loads the selected runtime, subscribes before prompting,
keeps the root session's answer separate from child-agent events, checks control
responses, observes the terminal outcome and disposes the session. `bridge.py`
uses LoopX's shared process supervisor. Cancellation and timeout stop the native
work; an incomplete output or missing cleanup receipt cannot become success.
DSH's Python worker preserves full SDK results and typed provider failures.
Large native records use bounded stdio fragmentation without changing their
OAR event identity.

Each DSH OAR session owns one bounded Turn. The pinned SDK has no verified
durable-resume API, so this adapter explicitly rejects queued input and a
second prompt on that session. A caller-supplied native ID is preserved; an
existing-ID failure never triggers a fresh-session retry. A new DSH Chat
segment instead receives its own native ID before launch, while the Chat
binding and its bounded visible history remain stable across segments.

The default DSH Chat adapter can interrupt its running segment. A custom
`--dsh-runner` remains an explicit adapter override and makes only the lifecycle
claims supplied by that runner. DSH Chat still returns one final answer per
segment and does not claim persistent native sessions or token streaming.

Two existing integration boundaries remain native:

- The Pi and DSH plugins installed **inside an already running host session**
  keep their same-session commands, tools and governed continuation. They do
  not start a second OAR-managed host.
- Rich native Codex Chat and the existing Codex/DSH operation transports retain
  their adapters for dynamic host tools, history injection and background
  controls. OAR 0.75.0 does not supply all of those contracts. This migration
  does not advertise those features on the bounded OAR interface.

## Verification and upstream references

```bash
npm --prefix loopx/hosts/oar run typecheck
npm --prefix loopx/hosts/oar test
python -m pytest -q --no-cov tests/test_oar_transport.py tests/test_oar_pi_host.py
python examples/loopx-turn-codex-cli-e2e-smoke.py
python examples/loopx-steward-managed-chat-smoke.py
```

The tests use synthetic Codex app-server responses and local model fixtures with
the actual OAR, Pi and DSH transports. They do not qualify paid-provider results
or comparative long-horizon effectiveness.

Design references:

- [OAR](https://github.com/botiverse/oar/tree/1e8ae0e6c2da3e67e2ed5d358966eaccc9f25665):
  runtime/session/event contracts and built-in Codex/Pi adapters.
- [DeepSeek Harness subagent-oar](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/packages/experimental/subagent-oar/README.zh.md):
  DSH launching other OAR runtimes. Upstream OAR has no built-in DSH adapter,
  so LoopX supplies one over its existing pinned SDK.
- [Raft source](https://github.com/botiverse/raft-source/tree/db5e399f017d584e49597e3a28757645d6807893):
  lazy runtime loading and shared discovery. Its OAR usage does not imply that
  all of its native session transports have migrated.
