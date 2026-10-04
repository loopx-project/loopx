# LoopX for DeepSeek Harness

`dsh-loopx-plugin` is one independently versioned DSH package with three
separate Loader rows:

- the init row automatically installs or upgrades the LoopX CLI, installs the
  packaged workflow skills into `$DSH_AGENTS_HOME/skills` (default
  `~/.agents/skills`), and verifies the DSH-native `loopx` entry before DSH
  finishes loading it. `/loopx-init` remains the explicit repair command.
- a passive same-session Driver becomes eligible only after the exact current
  Session successfully invokes the exact `loopx` skill. It then asks LoopX
  whether another turn may run and queues the authoritative heartbeat task
  into that live DSH Agent.
- the package-root Host registers GoalBar at the authenticated
  `/api/loopx.goalbar` route required by DSH 0.1.5. Its web Client adds a compact
  GoalBar between DSH's native GoalBar and Queue dock rows, visible only for one
  exact live `(goalId, loopxAgentId)` binding.

Installing the plugin and starting DSH load and prepare these capabilities;
neither creates a binding nor activates the Driver. The GoalBar
does one bounded read when a browser row mounts, then watches exact-Session
`step/end` and `turn/end` boundaries. It rereads LoopX only when the opaque
revision of the authoritative binding or active Goal state changes; a watch
lease also detects changes written outside that Session. DSH Agent-status
events update an existing row without a LoopX business read. Until one exact
Session contains valid typed `loopx` invocation evidence, its Driver makes no
LoopX CLI, binding, quota, or heartbeat call, creates no timer, and queues no
followup.

The package does not expose LoopX model tools, a binding sidecar, or its own
Goal/Todo state. Models use the installed LoopX skills and call the LoopX CLI
directly. LoopX remains the only authority for Goal, Agent, Todo, quota,
activation, and durable thread-binding data. The GoalBar protocol and its
deferred atomicity limit are specified in the versioned
[DSH native LoopX design](../../docs/plans/2026-08-20-dsh-native-skill-driver.md).

## Install

Requirements are Node.js 22.19+, `pnpm`, Python 3.11+ with `pip`, and network
access for the first DSH start when no compatible LoopX CLI is already
installed. LoopX itself is deliberately not a prerequisite. The initializer
honors an explicit `PYTHON_BIN`; otherwise it tries `python3`, then discovers
`python3.<minor>` executables on the supplied `PATH` in descending numeric order.
Installation still checks the Python version and pip; reopening the managed
runtime uses the same discovery and validates the LoopX CLI. No hard-coded
minor-version list is maintained. When selecting Python for installation, an
invalid explicit interpreter fails instead of falling back. Managed-runtime
readback keeps the existing CLI fallback behavior. If the plugin must install
or upgrade LoopX, it writes an isolated
copy under `$DSH_AGENTS_HOME/runtime/dsh-loopx-plugin` (default
`~/.agents/runtime/dsh-loopx-plugin`) and never mutates the system Python
environment. This works with externally managed Python distributions that
enforce PEP 668; the plugin does not use `--break-system-packages`.
The published plugin requires LoopX 0.5.4 or newer. Although 0.5.3 carried the
workflow-skill files, 0.5.4 is the first release that discovers them after the
plugin's Linux `pip --target` managed-runtime install.
Install the prebuilt release into the web profile:

```bash
dsh plugin --profile web add \
  "https://github.com/loopx-project/loopx/releases/download/dsh-loopx-plugin-v0.1.1-beta.5/dsh-loopx-plugin-0.1.1-beta.5.tgz"
```

The prebuilt release above retains its original DSH compatibility. This source
checkout additionally targets the qualified DSH 0.2.0-rc.2 API; it does not
publish a new plugin release. The exported legacy RPC registration remains
available to explicit callers, but plugin startup always uses the shared API.

For a compatible DSH source build, use:

```bash
cd packages/dsh-loopx-plugin
./install.sh
```

Start DSH on loopback (port `0` asks the OS for a free port) and open the
printed URL. The plugin finishes its idempotent LoopX CLI and skill bootstrap
before DSH publishes the Web URL. Its typed `loopxBootstrap` service gates the
Web server and runtime rows until startup has either succeeded or failed
safely:

```bash
dsh --profile web --port 0
```

Use `/loopx <task>` immediately. If automatic initialization reports a safe
failure in the DSH log, fix the named Python or package-manager problem and run
`/loopx-init` once to retry; normal installation does not require that command.

Installation is the GoalBar opt-in: there is no separate remote endpoint or
per-session grant. A row remains hidden until the exact DSH Session has one
unique LoopX binding. After invoking the installed `loopx` skill for that
Session, run the minimum authority readback inside that Session's DSH shell
(or replace `$DSH_SESSION_ID` with the exact Session id):

```bash
loopx --registry .loopx/registry.json --format json \
  resolve-agent-thread \
  --host-surface deepseek-harness-native \
  --thread-id "$DSH_SESSION_ID"
```

`status=bound` with one exact pair admits the row; missing or ambiguous results
fail closed and render nothing. `Start` resumes a stopped Goal and asks the
Driver to evaluate only when that exact live Session already contains typed
`loopx` skill activation evidence. It never activates an inactive Session.
`Pause` stops the Goal and retires only future queued/scheduled continuation;
it does not abort a claimed or running turn.

Maintainers can validate the built and packed surfaces with:

```bash
pnpm build
pnpm smoke:artifact
pnpm smoke:profile
pnpm smoke:runtime
pnpm smoke:docker
```

The runtime smoke creates an isolated temporary DSH profile. Its real web
process proves profile composition, automatic initialization before readiness,
immediate skill-catalog visibility, boot-manifest discovery, bundle serving,
Client materialization, and the loopback Connection fence. Separately, a
packed supported-DSH Context, Connection, and WebServer with a live Host Session fixture
cover same-turn binding discovery, lease-time source reconciliation,
status-only updates, pending-watch cancellation, successful actions, and
handler disposal through the real HTTP carrier. The served Client is then
applied in DSH's real ClientModuleSystem with a VM document harness; that layer
covers slot order and coexistence, Session injection, and ordinary-unload/HMR
style cleanup, but it is not a browser-mounted React interaction. These layers
do not replace the owner-reviewed packed-browser gate: that separate manual
layer mounts the served Client at a real DSH URL and exercises Client-to-carrier
Start/Pause. Focused Client tests cover Session-generation replacement and old
request cancellation without duplicating that matrix in the packed smoke.
The Docker smoke packs the current plugin and builds the current LoopX
release-candidate wheel, then starts both in a clean Debian container with the
DSH 0.1.5-rc.2 release candidate. It proves PEP 668-compatible private installation,
the managed launcher, startup readiness, installed `loopx` skill files, launch-
token authentication, and an authenticated GoalBar read through DSH's shared
API carrier. It requires Docker, `uv`, and network access for base images and
never opens a browser or configures a model provider.

## Maintainer release and marketplace handoff

A DSH plugin release is complete only after the same prebuilt package is
published to npm, its immutable GitHub asset exists, and an update pull request
has been opened against the upstream
[`awesome-dsh-plugin`](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)
marketplace. Marketplace maintainers retain merge authority; publishing a
LoopX release does not grant authority over that catalog.

For every DSH plugin release:

1. Update the package version and this README's pinned install URL. Run the
   typecheck, tests, and the built, packed, runtime, profile, and Docker smokes
   listed above, plus `pnpm smoke:registry --tarball <packed-package.tgz>`.
   The registry smoke serves the real packed bytes on loopback and invokes
   DSH's unversioned package-name installer; it publishes nothing. CI repeats
   this installation and removal on Linux and Windows.
2. Prepare complete bilingual GitHub release notes. Run
   `examples/release/release-readiness-doc-smoke.py` with one `--surface` for
   every optional capability changed by the release.
3. Merge the exact reviewed commit, pack from that immutable tag target, and
   publish both the version tag and `dsh-loopx-plugin-<version>.tgz` asset.
4. Read the remote release body back and rerun the release-readiness smoke.
   Download the remote asset and verify that its SHA-256 matches the local
   package before advertising it. Publish that **same tarball**, including its
   compiled Host, Client, Driver and initializer, to npm:

   ```bash
   npm whoami
   npm publish ./dsh-loopx-plugin-VERSION.tgz --access public --tag latest
   node scripts/verify-distribution.mjs --tarball ./dsh-loopx-plugin-VERSION.tgz
   ```

   `latest` intentionally names the qualified plugin candidate, because the
   marketplace installs its unversioned npm name. Do not substitute a
   beta-only tag or publish the monorepo root. The readback checks metadata,
   tag, downloaded bytes and the repository search used by DSH Hub. A missing
   package, wrong artifact or search-index delay fails verification; publishing
   alone does not complete marketplace recovery. npm account authentication
   or configured trusted publishing belongs to the release operator.

   For token-free CI publication, configure the package's npm Trusted Publisher
   with organization `loopx-project`, repository `loopx`, and workflow filename
   `dsh-plugin-publish.yml`, permitting `npm publish`. Then dispatch **Publish
   DSH Plugin to npm** on the existing published plugin tag, using that same tag
   for the `release_tag` input. This keeps npm provenance on the artifact's commit.
   The workflow requires a merged tag, consumes its GitHub asset without
   rebuilding, and verifies npm bytes and discovery. It neither creates a
   GitHub release nor replaces npm account ownership or the release guide gate.
5. In a clean fork branch of `awesome-dsh-plugin`, update only
   `data/plugins/huangruiteng__loopx--packages-dsh-loopx-plugin.yml` to the new
   immutable asset URL. Confirm the URL resolves, then run
   `node scripts/generate-readme.mjs --check` and `git diff --check`.
6. Open an upstream marketplace pull request and link it from the release
   closeout. Do not describe the release as marketplace-published until that
   pull request is merged by the upstream maintainers.

## Shadow observer (default off)

`src/observer.ts`, exported as `dsh-loopx-plugin/observer`, is the
`dsh-session-events` provider for the LoopX
[Reliability Diagnostics](../../loopx/capabilities/reliability_diagnostics/README.md)
capability: an L1 shadow observer that consumes read-only harness events and
appends compact, public-safe envelopes plus an observer stats record to
`<loopx-runtime-root>/reliability_diagnostics/by-goal/<sha256>.ndjson`, where
`sha256` is the lowercase digest of the exact UTF-8 Goal id. Upgrade the CLI and
plugin together. An existing legacy filename blocks flushes with
`LegacyDiagnosticLedgerError` until the
[offline upgrade](../../loopx/capabilities/reliability_diagnostics/docs/local-retention-v0.md#upgrading-a-legacy-ledger-offline)
preserves its history; readback never silently migrates it. It is a
separate Cordis row and bundle from the Driver, with no Driver or Agent
injection and no shared send path. It never calls `agent.send`, touches the
inbox, invokes the LoopX CLI, schedules, retries, stops, or resumes anything.
Every hook body and every flush is isolated, so an observer failure is counted
into the receipt instead of reaching DSH. This is module and hook isolation,
not an OS-process-isolation claim.

Before its first append, the producer applies the same recursive local-path,
credential-like value, and credential-field guard as the Python contract.
Unsafe event tokens or ids are counted as `public_safety_violation` and never
reach ledger bytes; CLI ingest independently re-validates persisted records.

It is off unless one exact goal, DSH session, and complete run identity are
declared before DSH starts:

```bash
export LOOPX_DSH_SHADOW_OBSERVER_GOAL_ID=<goal-id>
export LOOPX_DSH_SHADOW_OBSERVER_SESSION_ID=<session-id>
export LOOPX_DSH_SHADOW_OBSERVER_RUN_IDENTITY_JSON='{"worker_id":"<worker>","model_id":"<model>","task_id":"<task>","environment_id":"<environment>","tools_id":"<tools>","budget_id":"<budget>","adapter_revision":"<adapter-revision>","observer_revision":"<observer-revision>"}'
# optional: LOOPX_DSH_SHADOW_OBSERVER_LEDGER_DIR, LOOPX_DSH_SHADOW_OBSERVER_BUFFER_BOUND (default 256)
loopx reliability-diagnostics receipt --goal-id <goal-id> --format json
loopx reliability-diagnostics status  --goal-id <goal-id> --format json
```

Unless all required variables are valid, the independent observer row
registers no hook and writes no file. When enabled, it consumes only
`session/created`, `session/event`, and `session/disposed`; it skips the retired
token-level `assistant/chunk` rows older logs still replay, and records tool
names, turn and step numbers, typed end reasons, and ids only, never arguments,
outputs, prompts, or paths. Events for
any session other than the exact configured session are rejected as
`identity_invalid`. The stats record pins worker/model/task/environment/tools/
budget plus adapter and observer revisions, declares source coverage, and
proves count conservation. Sequence gaps, bounded-buffer drops, flush attempts,
declared clock uncertainty, and the empty outbound and influence fields make
the run's admissibility auditable from the receipt.

This is an experimental adapter implementing the RFC's P0 prototype
components. It does not establish the RFC's P0 exit: C0 fidelity, a qualifying
C1 run, and measured observer overhead remain separate evidence gates.

## GoalBar authority and privacy boundary

The GoalBar carrier uses Connection's authenticated `/api/loopx.goalbar`
Fetch route. Explicit callers of the deprecated RPC registration can still
register `/loopx`; plugin startup does not select it automatically. Both pass
Connection's Host/Origin trust fence and browser authentication, and this
package adds no second credential of its own. The deployment's reachability
policy — loopback by default, or declared `trustedHosts` — decides which
browsers can reach the host at all; Phase 1 does not support LAN or remote
browsers. The browser supplies only its injected DSH Session id and, for an
action, the last validated Goal/Agent pair. The Host re-derives cwd and thread
identity from the live DSH Agent, freshly resolves the binding, and executes
only fixed LoopX argv.

The wire allowlist contains ids, activation, live Agent status, full-lane
counts, cursors, opaque source revisions, and fixed error codes. It excludes
Todo text, Goal objective, quota, evidence, CLI output, exception messages,
registry paths, credentials, and binding candidates. A source revision is only
a change token, not authorization or a compare-and-swap guard. Installing the
package grants no model tool authority, does not create or repair bindings, and
does not change LoopX core state by itself.

## Automatic initialization and `/loopx-init` repair

When DSH loads the plugin, the init row runs the same typed initialization
routine and publishes the `loopxBootstrap` readiness service only after it
settles. The plugin's profile patch makes DSH's Web server and runtime depend
on that service, so the printed URL is a real bootstrap boundary. A safe
failure is logged without raw subprocess output or local paths, releases the
Web rows instead of stopping DSH, and leaves `/loopx-init` registered for an
explicit retry. Automatic startup does not create Agent followups or model
calls.

The repair command has no arguments. Extra input returns a usage error before any
model work or CLI probe. A valid invocation queues a bounded start followup on
the exact receiving Agent, then probes the current LoopX installation. When the
CLI is missing or lacks the DSH-native skill contract, it runs exactly one
fixed-argv `pip install --upgrade --target <plugin-runtime> 'loopx>=0.5.4'`, writes a
small managed Python launcher beside that target, then uses that same
interpreter and launcher to install and read back the skills. Driver and
GoalBar resolve this same managed runtime, including after an explicit repair.
It never constructs a shell command, mutates the system Python environment,
edits a registry, or retries the install mutation.

Unless the command is cancelled, it queues a second bounded followup for the
typed success or failure result. These are ordinary Agent turns, so a valid,
uncancelled invocation normally adds two model calls; cancellation leaves only
the already queued start turn, while invalid input adds none. The prompts do
not authorize tools, commands, or another installation. Followup delivery is
best effort and is not retried. The native `CommandResult` rendered by the
command UI remains authoritative: a followup failure or model reply cannot
change the installation result or repeat its mutation.

On success, DSH's filesystem skill provider invalidates its catalog and loads
created or updated skills without a restart. Missing or unknown skill status
still fails initialization instead of being guessed as unchanged.

After initialization, invoke the `loopx` skill with the task text. The skill
uses the exact DSH-managed `$DSH_SESSION_ID`, passes
`--host-surface deepseek-harness-native`, and follows the typed commands
returned by LoopX. The historical external connector remains the distinct
`deepseek-harness` / `dsh` surface.

## Driver activation boundary

Activation is per Session, not per plugin, process, Agent, Goal, or project.
The Driver accepts only either of these durable typed Session facts:

- a `user/message` whose source is exactly `skill-invocation`, whose name is
  exactly `loopx`, and whose form is exactly `instructions`;
- a model `tool/call` named exactly `skill`, with JSON object arguments whose
  `name` is exactly `loopx`, paired by call id with a subsequent successful
  `tool/result`.

A skill catalog, ordinary prose, shell text, `/loopx-init`, plugin-authored
init or heartbeat messages, a failed, malformed, unmatched, or superseded
model call, and the presence of a CLI, registry, Goal, binding, or project file
do not activate the Driver. Recovery folds only the current Session's existing
typed event history in memory and performs no external probe. Replacing or
clearing a Session recomputes from the replacement history; activation never
inherits across that boundary and has no plugin-owned durable store.

Existing Sessions upgraded from an older plugin version remain inactive when
their retained history has no recognizable invocation evidence. Invoke the
installed `loopx` skill once in every Session that should continue
automatically. Activation records intent only: it does not create a binding,
select a Goal or Agent, spend quota, or grant tool authority. After activation,
the existing exact binding, fresh quota, scheduler/heartbeat, reservation, and
pre-step revalidation order remains authoritative.

## Driver retry boundary

DSH's LLM retry plugin owns provider-request retries. This Driver retries only
safe fixed-argv LoopX reads and an idempotent `quota should-run` receipt, at
most twice beyond the first attempt. Every retry uses the same
`--turn-instance-id`. Typed authority denials, incompatible schemas,
cancellation, and human input are never retried. There is no cumulative
eight-failure breaker or plugin-owned durable activation state. The small
process-local activation projection is bound to one exact Session and is
reconstructed only from its typed event history. Exhausting the three attempts
stops that evaluation; it does not create a periodic error-retry loop. Only a
typed LoopX local-scheduler plan can schedule another wakeup, and its limit on
unchanged polls is enforced.

For an admitted automatic continuation, that exact turn id is also carried in
the LoopX-owned canonical heartbeat body and the typed DSH continuation source.
The source is attribution for reservation matching, not authority. Before work
enters a model step, the Driver replays the same receipt and the LoopX guard
returns the typed settlement plan for that exact id. Accountable work writes
back and spends through that plan's single deterministic effect identity; it
does not fall back to an unbound generic spend command. Human priority, Pause,
dispose, or a failed pre-step check before work begins does not fabricate a
writeback, spend, or void receipt.

The Driver resolves the current project registry with
`resolve-agent-thread`, requires one exact Goal/Agent match for the live DSH
session, and rechecks binding plus quota before and after downstream pre-step
listeners. A human message removes an unclaimed automatic reservation; a
mixed batch rejects the automatic message and restores the human work.
The Driver queues at most one Driver-owned followup per automatic admission.
Initialization messages use the distinct `dsh-loopx-plugin/init-command`
source and never satisfy a Driver reservation.

## Installation and upgrade recovery

Check `dsh --version` before choosing an artifact. The current source accepts
DSH 0.1.5 release candidates from rc.1, stable 0.1.x from 0.1.5, and the explicitly
qualified 0.1.7-rc.2 and 0.2.0-rc.2 candidates. It excludes earlier 0.2 candidates,
0.2.1 alpha builds and other, unqualified prerelease tuples. A stable-version
range alone does not admit every prerelease.
The immutable beta.5 download above contains the earlier integration; building
this checkout produces beta.6. A source build is not a published beta.6 release.

If a marketplace reports `loopx-repository: entry file missing: index.js`, it
selected the monorepo root instead of this plugin package. Installing
`git+https://github.com/loopx-project/loopx.git` cannot select the nested, built
plugin. The marketplace's npm route requires a published `dsh-loopx-plugin`
whose repository points here and whose keywords include `dsh-plugin`; otherwise
it falls back to that invalid Git target. After the release operator completes
the registry readback above, the platform-independent install command is
`dsh plugin --profile web add dsh-loopx-plugin`. Replace `web` with `desktop`
when that is your selected profile. Until publication is verified, use a
compatible prebuilt plugin tarball, or build this package and
install it with your supported DSH executable:

```bash
cd packages/dsh-loopx-plugin
DSH_BIN="$(command -v dsh)" ./install.sh
dsh --profile web --dump-config
```

The installer rejects an incompatible host before changing its profile. A
successful profile contains `loopx-goalbar`, `loopx-init-command`, `loopx-driver`,
and the passive `loopx-shadow-observer` in that order. The installer also reads
the installed package version back. Restart DSH after upgrading a running host; then
check the native GoalBar and `/loopx-init` in that same Session. Profile
registration alone does not prove that a Client loaded or that work continued.

A GitHub URL download timeout occurs before plugin loading. Download the exact
compatible release asset on a network that can reach GitHub, verify its SHA256
against the GitHub release asset's digest, then transfer and install that file:

```bash
dsh plugin --profile web add ./dsh-loopx-plugin-VERSION.tgz --ignore-scripts
dsh --profile web --dump-config
```

Replace `VERSION` with the verified asset's version. Local-file installation
avoids downloading that asset again; DSH may still need its configured registry
to resolve dependencies. It does not make an incompatible host supported or
establish recovery of the original Windows network failure. Before upgrading,
retain the previous compatible tarball for the rollback below. Marketplace
catalog correction, release publication, browser qualification, and native
Windows verification remain distinct delivery steps.

On Windows, the same `dsh plugin ... add` command accepts a local `.tgz` file;
the Bash source installer is optional. Use `--profile desktop` for a desktop
profile and `--profile web` for a web profile throughout install, readback and
removal. The old beta.5 asset is not a 0.2 compatibility claim. To build the
current source using native PowerShell, run `pnpm install --frozen-lockfile
--ignore-scripts` and `npm pack` in this package, then add the resulting `.tgz`
with the native DSH command.

For release qualification, run `pnpm smoke:install` with `DSH_BIN` pointing to
each supported host executable. To qualify an upgrade rather than a fresh
installation, run `pnpm smoke:install --previous-tarball /path/to/retained.tgz`.
It invokes the actual source installer in an isolated profile, verifies the
installed version, rejects an incompatible host before profile operations,
and removes the plugin. It makes no model calls and does not qualify a browser
or the user's existing profile.

## Uninstall

There is no separate GoalBar switch in Phase 1. Disable all package faces by
removing the plugin from the web profile, then restart the running DSH process:

```bash
dsh plugin --profile web remove dsh-loopx-plugin
```

This removes the GoalBar Host/Client row, stops the Driver, and removes
`/loopx-init`; it does not remove LoopX, its registry, bindings, skills, or the
plugin-managed runtime. To remove only LoopX-managed skills, run:

```bash
loopx workflow-skills --uninstall \
  --skills-dir "${DSH_AGENTS_HOME:-$HOME/.agents}/skills"
```

After DSH has stopped and the plugin has been removed, the isolated CLI copy
can be removed independently without touching LoopX state:

```bash
DSH_LOOPX_RUNTIME="${DSH_AGENTS_HOME:-$HOME/.agents}/runtime/dsh-loopx-plugin"
test -f "$DSH_LOOPX_RUNTIME/loopx_cli.py" && rm -rf -- "$DSH_LOOPX_RUNTIME"
```

To roll back the plugin while preserving LoopX state, remove it and install a
previously retained package tarball, then read the profile back. Replace the
placeholder value with the exact path of the old tarball retained before the
upgrade:

```bash
RETAINED_PREVIOUS_DSH_LOOPX_TARBALL=/absolute/path/to/retained/previous-dsh-loopx-plugin.tgz
dsh plugin --profile web remove dsh-loopx-plugin
dsh plugin --profile web add "$RETAINED_PREVIOUS_DSH_LOOPX_TARBALL" --ignore-scripts
dsh --profile web --dump-config
```
