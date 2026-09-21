# LoopX Dashboard

This is the Personal Workspace and contributor tooling app for LoopX. It renders the
status data contract with a React/Vite control-plane UI.

## Current Status

Personal Workspace is the operator UI for Goals, Tasks, Chat, and outputs.
The CLI and versioned control-plane projections remain the sources of truth;
the UI does not create a second state authority.

## Fresh Clone Public Preview

No private LoopX state is required for the first dashboard preview. The
app bundles `examples/status.example.json` as its public-safe example source,
so a fresh checkout can validate and open the UI before starting any local
status server:

```bash
cd apps/presentation/dashboard
npm ci
npm run smoke:demo-readiness -- --skip-browser
npm run dev:web
```

Then open `http://127.0.0.1:5173/`. Use the bundled example source for a public
demo, or switch to a loopback status URL only after you have started
`loopx serve-status` locally. Do not commit `status.local.json` or live
status exports; they can contain local registry/runtime paths and private
project summaries.

`npm run dev:web` starts only the Vite UI with the bundled example. `npm run dev`
also starts the loopback status and Chat services and therefore requires a
Python 3.11+ interpreter; see the development section below.

Personal Workspace owns Goal, Task, Chat, output, and report workflows. Run
`loopx dashboard` for the installed local workspace; see the
[Personal Workspace guide](../../../docs/guides/personal-workspace-user-guide.md)
for its product demo and operating instructions.

Public cases live in the [case directory](../../../docs/showcases/index.en.html).
The old Frontstage showcase and Ops boards have been removed. Their URLs remain
only as migration bridges:

| Old URL | Destination |
| --- | --- |
| `/frontstage` | Public case directory; ignores all status parameters |
| `/frontstage?mode=developer` or `/frontstage/developer` | `/developers/projections` |
| `/frontstage?mode=ops` or `/deprecated/frontstage/ops` | Personal Workspace `/`, preserving Goal and relative/loopback status source |

The contributor cockpit at `/developers/projections` retains static contract
exploration, projection diffing, fixture rules, and component examples. It
loads no live status source and grants no write authority.

Hosted Pages aliases are static redirects. `/frontstage/` goes to the public
case directory, `/frontstage/developer/` to contributor tools, and the old Ops
path to the Personal Workspace guide. Hosted redirects discard all query
parameters; they never open or load a visitor's local workspace. Public
showcases and research remain separate from local operator state.

To create a public-safe static bundle for demos, Lark shares, or future GitHub
Pages hosting, export the frontstage with the sanitized fixture:

```bash
cd apps/presentation/dashboard
npm run export:frontstage-share
```

The default output is `/tmp/loopx-frontstage-share-bundle`. It includes a
compiled dashboard, `status.frontstage-share.json`, legacy `/frontstage/` redirects and a `/developers/projections/`
static route, a manifest, and a README with the local serve URL. The exporter
rejects local paths, private registry state, internal document hosts, raw-key
leaks, token assignments, and private key material before reporting success.
The share-bundle smoke also scans generated files for the synthetic `GH_FAKE_*`
trap markers so public exports cannot accidentally carry a live-status payload.
For repository Pages hosting later, rerun the same exporter with
`-- --base /loopx/ --out-dir <artifact-dir>` and publish only that
generated site artifact.

## Run

From any directory after installing LoopX:

```bash
loopx dashboard
```

This one command serves the packaged Personal Workspace, status projection, and
Agent Chat from the same process. It opens the browser by default. For a
headless launch, run `loopx dashboard --no-open` and open the URL printed by the
command; the default is `http://127.0.0.1:8767/chat/`. Installed use does not
require a separate `loopx serve-status` process or npm dependency installation.

The packaged UI and status projection can be read back from that same process:

```bash
curl -fsS http://127.0.0.1:8767/chat/ >/dev/null
curl -fsS http://127.0.0.1:8767/status.json
```

If a LoopX Chat service is already running on the default port (for example
started by the Tauri desktop shell), `loopx dashboard` detects it by its exact
capability fingerprint and reuses it instead of failing: it prints the running
URL and opens the browser/PWA route, then exits without starting a second
server. The desktop shell reuses the same services in the opposite order, so
the browser/PWA and native entry points can be started in either order.

### Built-In Chat Agents

The Agent picker is served by `/api/chat/capabilities`, so its rows come from
the running LoopX process rather than from browser state. The built-ins are
Codex (app-server), Claude Code, Kiro CLI, and the direct Claude/OpenAI API
lanes; owner-registered ACP endpoints are appended after them. A row's
`available` flag is a live probe, so an uninstalled host renders as needing
configuration instead of failing when a session opens.

Kiro CLI is reached through its own ACP agent (`kiro-cli acp`) using the same
ACP stdio adapter as owner-registered endpoints. Override the executable when
it is not on `PATH` under the documented name:

```bash
loopx dashboard --kiro-cli-bin /path/to/kiro-cli
```

The trust boundary is unchanged: LoopX Chat answers every ACP
`session/request_permission` with `cancelled` and exposes no client host tools,
so a Kiro tool call that needs approval is refused rather than auto-approved.
LoopX passes no `--trust-all-tools`. Reaching the host this way drives one
read-only Chat session; it is not the governed `/goal` loop, which is entered
from a Kiro CLI session through the
[Kiro CLI goal-mode adapter](../../../loopx/kiro_cli_goal_mode/README.md).

Source-checkout development is a separate mode:

```bash
npm ci
npm run build
npm run dev
```

`npm run dev` starts the Vite UI together with the loopback status and Chat
services on ports `5173`, `8766`, and `8767`. Use `npm run dev:web` when those
LoopX services are already running separately. Vite proxies the default
`/status.json` request to port `8766`, so an SSH user only needs to forward port
`5173` for the normal development page.

The full-stack launcher needs a Python 3.11+ interpreter for the status and
Chat services. It honors `LOOPX_PYTHON` first, then the Python recorded by the
LoopX installer in `.loopx-python`, then the repository `.venv`,
versioned interpreters discovered on `PATH` in descending numeric order, the
unversioned `python3`, and common Homebrew locations. Every discovered executable
must pass the Python compatibility probe; there is no fixed minor-version list.
Prepare the project environment and launch from the repository root:

```bash
uv sync --extra test
uv run --extra test bash scripts/dashboard-dev.sh
```

An explicit `LOOPX_PYTHON` or a valid installer-recorded interpreter still takes
precedence. To select the project environment explicitly after `uv sync`, set
`LOOPX_PYTHON` to the absolute path of `.venv/bin/python`. The launcher continues
to support existing compatible Python installations without requiring uv.

Both the root dashboard and the packaged `/chat/` route expose the same
installable PWA manifest and icons. The default `loopx dashboard` command opens
`/chat/`; its manifest therefore scopes the installed app to `/chat/`. This is
an installable standalone surface, not an offline-cache service worker.

The live `/status.json` route keeps repository-wide public-boundary scanning
out of the first-screen request. Its contract projection reports that scan as
deferred; run `loopx check` before publishing or pushing public surfaces to
perform the complete boundary audit.

The default screen is the Personal Workspace—LoopX's sole operator-facing frontend.
It provides a unified, coherent experience for managing long-running agent Goals:

- **LoopX Manager Overview (`/`)**:
  Cross-Goal triage answering operator priorities before raw drill-down:
  - 4-lane overview flow (`需要你` / `执行中` / `观察中` / `已安排`);
  - System Health diagnostics highlighting control-plane and registry status;
  - Unified conversation tray supporting global questions, Goal creation drafts, and progress summaries.

- **Goal Workspace (`/?goalId=<id>`)**:
  Dedicated workspace for an individual Goal:
  - **Chat**: Goal-scoped Agent communication, streaming turns, and action previews;
  - **Tasks**: 4-column kanban board (`待确认`, `待执行 / 进行中`, `定时与持续`, `已完成`) with quick status updates and one-click conversion of Agent replies to Task drafts;
  - **Files**: Repository artifact browser and file inspects;
  - **Context Drawer**: Goal diagnosis, repository bindings, Lark Topic connections, and session health.

- **Action Safety & Control Plane**:
  Durable modifications to Goals, Todos, Heartbeats, monitors, or settings follow the typed preview → governed apply → verified receipt protocol. Presentation follows risk and reversibility: protected or irreversible actions remain review-first, while the reversible Goal pause below applies a ready preview directly and escalates stale or newly gated results back to review. The browser never performs unmediated direct writes to control-plane truth.
  The Goal directory keeps only active Goals in its main list. Use the pause action
  beside a Goal to apply a reversible stop in one click; persistent feedback reports
  the result, and stopped Goals retain their
  Todos, history, and evidence in a collapsed **Stopped Goals** section and can be
  restored from the same section. Stopping a Goal pauses automatic Agent turns; it
  does not mark the Goal complete or delete state. A stopped Goal leaves active
  attention and projects zero effective quota, allowing the scheduler to stop host
  automation such as a Codex App heartbeat while retaining the configured quota.
  Goal stop and `quota.compute=0` share this shutdown path but keep distinct resume
  authority: only Goal lifecycle resume may reactivate a stopped Goal, while an
  explicit positive quota update resumes a quota pause. Stop does not force-kill an
  in-flight tool call; the next `quota should-run` packet tells the host to pause or
  delete the recurring heartbeat before another automatic turn. The equivalent CLI
  flow is:

  ```bash
  loopx goal-lifecycle --goal-id <goal-id> --operation stop
  loopx goal-lifecycle --goal-id <goal-id> --operation stop --actor-kind owner --execute
  loopx goal-lifecycle --goal-id <goal-id> --operation resume --actor-kind owner --execute
  loopx quota status --goal-id <goal-id>
  ```

  The first command is a zero-write preview. The executed commands write the
  authoritative source registry, refresh the shared registry projection, and verify
  both readbacks. Resume restores scheduling eligibility; quota, Gates, and Todos
  still decide whether work may run.

- **Public Frontstage (`/frontstage`)**:
  Public `/frontstage` continues to serve as an unauthenticated, read-only showcase and public-safe presentation surface. Real local operator workflows belong exclusively in the Personal Workspace.

## Load Live Status

For the canonical multi-project home, start a global status server. This is the
normal operator view for all projects connected into the shared registry:

```bash
loopx serve-status --global-registry --port 8766 --limit 80
```

On macOS, keep the status feed and the Chat service running after login with
the user-level LaunchAgent helper:

```bash
../../scripts/macos-dashboard-launchagent.sh install
```

The helper starts:

```text
http://127.0.0.1:8766/status.json
http://127.0.0.1:8767/chat/
```

Use `../../scripts/macos-dashboard-launchagent.sh restart|stop|uninstall|status`
for local service operations. `status` also probes
`http://127.0.0.1:8766/status.json` and prints the
`status_contract.schema_version`; if it is missing or below the expected
dashboard version, run `restart` before a demo so the live feed is not served by
an older daemon. Logs live under `~/Library/Logs/loopx/`.
The status output path is covered without touching real macOS services by
`python3 examples/macos-dashboard-launchagent-status-smoke.py`.

Then open the packaged personal workspace:

```text
http://127.0.0.1:8767/chat/
```

### Named local and SSH-tunnel sources

The Personal Workspace source switcher keeps a browser-local catalog with one
built-in **Local** source and any number of named SSH-tunnel sources. Start the
status server on each remote host, then forward each host to a distinct local
port:

```bash
# On the remote host
loopx serve-status --global-registry --host 127.0.0.1 --port 8766

# On the operator machine; choose a different local port for every source
ssh -N -L 8876:127.0.0.1:8766 <remote-host>
```

The add-source panel reads only explicit, shell-safe `Host` aliases from the
operator machine's OpenSSH config through the current loopback Dashboard
origin. Packaged `loopx dashboard` serves this endpoint from its Chat runtime,
so a custom Dashboard port works without a fixed discovery port; development
mode proxies the same path to the local Chat service. Select an alias, choose a
local port, copy and run the generated tunnel command, then add the source.
Wildcard hosts, negated patterns, `IdentityFile`, `ProxyCommand`, hostnames,
credentials, and config paths are never projected to the browser. The manual
loopback-URL path remains available for custom forwarding setups.

The browser catalog stores the selected alias, label, and loopback URL; LoopX
does not store SSH credentials or open the tunnel. The active source reports
its connection health. Local stays interactive. A source bound to an exact
configured alias may stop or resume a Goal through the same typed
`goal-lifecycle` contract on that remote host. Manual URLs and all other
remote controls stay read-only; remote lifecycle never falls back to a local
Goal with the same id.

The switcher intentionally has no synthetic **All** source. Independent status
feeds do not yet share authority, identity, or deduplication semantics, so
combining them would imply cross-host coordination that the control plane has
not established.

For project-local debugging or a disposable `loopx demo`, start a local
status server from the project you want to inspect:

```bash
loopx serve-status --port 8765
```

`--global-registry` is intentionally explicit: it keeps the multi-project home
on the shared registry even when you launch it from inside a project checkout,
while plain `serve-status` remains useful for project-local debugging.

Keep the dashboard app running and use `?view=ops`, the `Live` source button,
or load this project-local URL from the source control:

```text
http://127.0.0.1:8765/status.json
```

The status server binds to `127.0.0.1` by default and sends no-store JSON with
local CORS headers for the Vite dashboard.

It also serves `POST /reward/dry-run` for validating the selected goal/run and
public-safe reward text. To allow direct local dashboard submission, start the
server with the explicit write flag:

```bash
loopx serve-status --port 8765 --enable-reward-write-api
```

The write flag is loopback-only. Without it, the dashboard can validate a
reward draft but cannot append feedback.

## Load Static Status

Use a local static export:

```bash
python3 -m loopx.cli --format json status > apps/presentation/dashboard/public/status.local.json
cd apps/presentation/dashboard
npm run dev
```

Then load `/status.local.json` from the dashboard source control.

`status.local.json` is intentionally git-ignored because live status exports can
contain local registry/runtime paths and private project summaries. Keep it as a
local inspection file only. For public demos, use the sanitized
`examples/status.example.json` fixture instead of committing a live export.

You can also import a JSON file directly in the browser, or load a local API
URL that returns the same `loopx --format json status` shape.

## Live Single-Page Session Dash

The primary way to watch session task progress is a loopback single-page panel:

```bash
loopx dash                 # serve at http://127.0.0.1:8767/ (auto-refresh every 10s)
loopx dash --goal-id <goal-id>   # narrow the panel to one goal
```

Open the printed URL in any browser and keep it open while the agents work.
The page is a human-focused fleet view: an overview strip of sessions, goals,
active / needs-you / blocked / done buckets, open todos and run statistics,
followed by one card per session with the goals it owns and each goal's
status badge, todo progress bar, waiting reason, and latest run. It refreshes
itself in place every 10 seconds by re-fetching the `/panel` fragment.
Internal control machinery (decision frames, work-lane contracts, quota slot
math, source warnings) is intentionally not rendered. The panel is
read-only: no write controls, no browser write authority. The server binds
loopback only and exposes no write routes.

A one-shot static snapshot is also available for demos or sharing:

```bash
loopx dash generate [--goal-id <goal-id>] --out dash.html
```

Open `dash.html` in any browser. The command runs the public/private
boundary scan before reporting success and withholds output on failure.

```bash
# print the projection + html as JSON instead
loopx --format json dash generate --goal-id <goal-id>
```

See [the session dash panel design](../../../docs/product/surfaces/session-dash-panel-design.md)
for the layout, data boundary, and validation contract.

## Browser Smokes

Dashboard browser smokes are explicit because they start a temporary Vite
server. For demo readiness, run the grouped public-safe smoke:

```bash
npm run smoke:demo-readiness
```

That command runs the LaunchAgent status-output smoke, the structured
`promotion-gate` fresh/warning contract smoke, the source-contract smokes, and
the current Home and Personal Workspace browser smokes. The decision-freshness
and promotion-readiness read models remain covered by focused control-plane
smokes instead of browser tests for the retired legacy Ops view. In CI
environments without Playwright/Chrome,
use:

```bash
python3 ../../../examples/dashboard-demo-readiness-smoke.py --skip-browser
```

The individual browser smokes are still available when you want to debug one
surface:

```bash
npm run smoke:home-browser
npm run smoke:personal-workspace
npm run smoke:frontstage-share-bundle
node examples/dashboard-throttled-browser-smoke.mjs
node examples/dashboard-operator-gate-browser-smoke.mjs
```

The home browser smoke protects the canonical control-plane home. It uses a
public-safe four-project fixture, opens the root route without `view=share`,
checks the Chinese operator copy for user todos, agent priorities, showcase
activity, quota guard state, per-project top-4 todo status, and state
writeback, and rejects raw machine tokens such as `single_surface`,
`focus_wait`, or raw internal slot constraints on the first screen. It also captures desktop
and mobile first-screen / decision-frame screenshots under
`output/playwright/dashboard-home-visual-acceptance/` and fails on horizontal
overflow so density regressions are visible before calling the frontend broadly
usable. It uses an installed Playwright package or the Codex bundled runtime
when available, and starts Vite through the local `vite` package rather than
depending on `npm` / `npx` being on `PATH`.

The ops decision-freshness smoke protects the detailed `?view=ops` panel with
two public fixtures: a live-like zero-item summary and a stale/rebase-required
decision example. It verifies the rendered Chinese/English operator copy,
counts, top affected goal, and exact-replay wording instead of relying only on
source-string checks.

The promotion-readiness smoke protects the detailed `?view=ops` panel with
fresh, stale, and missing readiness fixtures. It verifies the status badges,
readiness/rerun decision, artifact window, age, reason, and source-of-truth copy
for canary promotion readiness. The canonical fixture/browser script is
`examples/dashboard-promotion-readiness-browser-smoke.mjs`; use the npm script
above instead of calling ad hoc duplicate filenames.
The grouped demo-readiness path also runs `examples/promotion-gate-smoke.py`
before browser checks, so the structured `gate_state`, `can_promote`, and
`should_warn` contract is covered even when browser smokes are skipped.

The throttled smoke protects the "quiet scheduling state" first screen. The
operator-gate smoke protects planned high-complexity goals: they should appear
as controller/user actions, not Codex-ready work. Those older browser smokes
still use the local Playwright CLI wrapper.
