# Connect an existing Git project

Project onboarding is an independent track. You do not need to modify the LoopX Kernel or develop an
Extension first. This chapter establishes the project state and Git boundary; the next two chapters
activate work from Codex App and the visible Codex CLI TUI.

The recommended path is to delegate onboarding to the Agent already working in the repository. You define
the goal, Host, and authority boundaries. The Agent inspects the repository, reads the current LoopX
surface, executes the safe onboarding steps, and returns evidence you can review. The manual commands
remain useful for understanding, verification, and recovery.

!!! tip "Fast reading path"
    For basic onboarding, follow sections 1–6 and stop after Git-isolation verification. Read section 7 only
    when the project needs an optional Capability or Extension.

## Observable success

When onboarding is complete:

- `loopx doctor` reports a usable installation;
- `.loopx/registry.json` exists in the project;
- `.codex/goals/<goal-id>/ACTIVE_GOAL_STATE.md` exists;
- `loopx status` can show the active state and the current frontier; a first connection creates no
  onboarding Todo, so the Agent writes the first delivery Todo after you confirm it;
- `.loopx/` and `.codex/goals/` do not enter Git;
- reconnecting reuses the exact existing `goal_id` instead of overwriting the Goal;
- a new executor receives a fresh `agent_id` unless the user explicitly authorizes a takeover.

These files are local control-plane state, not project source. Do not commit them to a public repository.

## 1. Delegate onboarding to an Agent

Open your Agent development tool from the repository root. Adapt the goal and Host in this prompt, then
send it as one onboarding contract:

```text
Safely connect the current Git project to LoopX.

Goal:
- Establish a recoverable, verifiable release workflow for this project.
- The current Host is Codex App. If the environment is not that Host, tell me first; do not guess.

Execution contract:
1. Begin with a read-only inspection of the project root, current branch, git status, .gitignore, and any
   existing .loopx/registry.json, .codex/goals/, or other LoopX state. Do not overwrite, reset, or clean
   existing material.
2. Run loopx --version and loopx doctor, then read the current --help for every command you need. Do not
   rely on remembered arguments from an older version. If LoopX is not installed, report what is missing
   and where the official installer writes before asking for installation authority. Do not describe a
   discovered install command as a completed installation.
3. If LoopX state exists, read loopx registry, loopx status, and relevant history first. Prefer the exact
   existing goal_id. Do not force a reconnect or select a Goal from objective similarity.
4. Ensure .loopx/, .codex/goals/, and .local/ are ignored by Git. If those paths already serve another
   project purpose or are tracked, stop and report the conflict. Do not delete or untrack them yourself.
5. For a project that is not connected, run loopx connect --dry-run first and show the state it would
   create or change. Run loopx connect only after confirming there is no conflict. Do not bootstrap again
   merely to “start over” when a registry already exists.
6. If several Goals are possible, stop at the read-only goal_selection_gate and show me the choices and
   your recommendation. Before I choose, do not write Todos, register an Agent, or activate a Host loop.
7. For a new executor, choose a fresh public-safe agent_id. Preview registration, then use the command
   supported by the current CLI and read it back. Reuse an existing agent_id only when I explicitly
   authorize takeover.
8. Generate the transaction packet with loopx start-goal --guided --project . and the exact goal text.
   Pass the correct --host-surface when the Host is known. Execute only packet steps allowed by the
   current authority.
9. Stop at a Gate for user approval, external writes, credentials, wider permissions, Host selection, or
   destructive Git operations. Do not decide those for me.
10. Verify loopx status, todo list, history, quota should-run, git status, and
   git ls-files .loopx .codex/goals .local.
11. Do not commit or push. Finish with an "onboarding report" that names goal_id, agent_id, Host, changed
    files, current Todos and Gates, executed mutations, verification, unresolved issues, and the next
    action. If you completed only a preview, explicitly say that onboarding is not complete.
```

This prompt delegates execution, not authority. You still decide:

- which Goal to select when several exist;
- whether to take over an existing Agent identity;
- which Host surface owns activation;
- whether external writes, credentials, or a wider write scope are allowed;
- whether repository changes are committed or pushed.

### The onboarding report

An auditable onboarding report includes:

```yaml
onboarding:
  status: complete | blocked | preview_only
  project_root: <repository root>
  goal_id: <exact goal id>
  agent_id: <fresh id or explicitly approved takeover id>
  host_surface: <exact host or unresolved>
changes:
  - <changed path and why>
gates:
  - <decision still owned by the user>
verification:
  doctor: pass | fail
  status_readback: pass | fail
  local_state_ignored: pass | fail
  tracked_private_state: []
next_action: <one concrete next step>
```

Do not accept “the command succeeded” as sufficient evidence. Require state readback and Git-isolation
proof.

### Example: first onboarding

```text
Use the Agent onboarding contract in this chapter to connect the current project to LoopX.
The goal is "Create a recoverable build, approval, and Pages deployment flow for every release candidate."
The current Host is the visible Codex CLI TUI. Use a fresh public-safe agent_id.
Do not commit, push, or trigger a deployment. Stop for my decision on Goal selection, authority, or any
external write.
```

### Example: continue existing state safely

```text
First inspect the current LoopX registry, Goals, Todos, Gates, and history read-only, then help me continue
the project. Prefer an exact existing goal_id, but do not automatically take over an existing agent_id.
If you find multiple Goals, an active lease, an unfinished mutation, or a workspace-route mismatch, return
diagnosis and choices only. Do not write state, commit, or push.
```

## 2. Install and inspect LoopX

Prerequisites:

- Python 3.11 or later;
- Node.js 22.22.3 or later for the LoopX-managed TypeScript Effect runtime;
- a macOS or Linux shell, or Windows PowerShell 7;
- an existing Git project.

Install the PyPI release and its LoopX workflow skills:

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

!!! tip "Why not clone LoopX first?"
    Most users need a published CLI and workflow skills, not a Kernel source checkout. Clone-based installation is for
    developers who need live canaries or intend to contribute to LoopX.

Treat `loopx doctor` as the installation fact. A successful `which loopx` only proves that one executable
is on `PATH`; doctor also checks the release snapshot, Python import, installed skills, and Host
integration, and the TypeScript Effect runtime. LoopX starts that runtime automatically and lets it exit
when idle; users do not supervise a daemon manually. `stopped` is a healthy on-demand state, while
`missing`, `unsupported`, or `probe_failed` must be repaired first.

Use the deep check when you need to verify the real runtime and journal checkpoint path:

```bash
node --version
loopx doctor --deep
```

See [Installing LoopX](/loopx/docs/guides/installing-loopx/) for the complete native Windows installation,
upgrade, and rollback path. Do not require WSL merely to reproduce the POSIX examples.

## 3. Establish the Git boundary

Before connecting, add local control state to the project's `.gitignore`:

```text
.loopx/
.codex/goals/
.local/
```

If the project already uses any of these names, inspect the existing contents before changing the rule.
LoopX directories may contain active state, registry, leases, and local evidence pointers. `.local/` may
also contain unrelated private work.

Confirm the ignore rules:

```bash
git check-ignore -v .loopx/registry.json
git check-ignore -v .codex/goals/example/ACTIVE_GOAL_STATE.md
```

For paths that do not yet exist, Git may need `--no-index`:

```bash
git check-ignore -v --no-index .loopx/registry.json
```

## 4. Understand the connection flow

From the project root:

```bash
loopx connect --dry-run
loopx connect
loopx status
```

Inspect the project root, `goal_id`, state file, and Git boundary in the dry-run before performing the real
connection. `connect` should reuse an existing registry and active state. If the project has too little
state to continue, start with an explicit task:

```bash
loopx start-goal \
  --guided \
  --project . \
  --goal-text "Establish a verifiable release workflow for this project"
```

This produces a guided transaction packet. It is a preview, not proof that Todo writeback, Host activation,
or an Agent turn has already happened. The Host integration must execute the planning, state writeback, and
activation described by the packet.

`connect` / `bootstrap` register the Goal and write the active state only: they create no first-connect
onboarding Todo, owner-decision gate, or host-loop opt-in gate. A freshly connected goal therefore has no
executable Agent Todo; the Agent or the connected domain adapter writes the first delivery Todo, so
automation starts from the caller's own work queue instead of a generated onboarding queue.

### Choose the Goal before choosing the Agent

Guided start keeps two decisions separate:

1. **Goal selection:** when the project has one registered Goal, reuse that exact `goal_id`; when it has
   several, return a read-only `goal_selection_gate`. Select one exact rerun command from `choices`. Before
   that selection, do not write Todos, register an Agent, or activate a Host loop.
2. **Agent identity:** for new onboarding with task text, omitting `--agent-id` defaults to fresh identity
   registration only when the Goal has no registered lanes (or `--new-peer` is explicit). When registered
   lanes exist, `start-goal` returns an identity gate that requires selecting one existing lane. Existing
   Agents are explicit takeover choices, not automatic defaults.

Do not select a Goal from objective similarity, and do not take over an Agent merely because it is the only
registered identity. Preview and then atomically register a new public-safe id:

```bash
loopx register-agent \
  --goal-id <selected-goal-id> \
  --agent-id <new-public-safe-agent-id>

loopx register-agent \
  --goal-id <selected-goal-id> \
  --agent-id <new-public-safe-agent-id> \
  --execute
```

The preview lets you inspect the plan. Before Todo writeback, confirm that the execute result reports
`ok`, `changed`, and `written` as true, global sync succeeded, and source/global registration readback was
verified. If the user explicitly requests an old lane, use the packet command bound to that exact
`agent_id` instead of pretending to create a fresh registration.

If you know the active Host, state it explicitly:

```bash
# Codex App
loopx start-goal --guided --project . \
  --goal-text "Establish a verifiable release workflow for this project" \
  --host-surface codex-app

# Visible Codex CLI TUI
loopx start-goal --guided --project . \
  --goal-text "Establish a verifiable release workflow for this project" \
  --host-surface codex-cli-tui
```

When the Host is unknown, omit `--host-surface`. LoopX should return a read-only selection Gate instead of
guessing.

## 5. Read current state

Use the shortest read paths first:

```bash
loopx registry
loopx status
loopx todo list --goal-id <goal-id>
loopx history --goal-id <goal-id>
loopx quota should-run --goal-id <goal-id> --agent-id <agent-id>
```

| Command | Primary question |
| --- | --- |
| `registry` | Which active states are connected to this project? |
| `status` | Who should act, and which Gates or risks are current? |
| `todo list` | What work units, owners, and lifecycle states exist? |
| `history` | Which bounded events were written back? |
| `quota should-run` | Is another delivery turn allowed now? |

Do not reduce `should_run: true` to permission for any arbitrary action. Also inspect the
`interaction_contract`, selected Todo, capability Gate, write scope, and scheduler hint.

## 6. Verify Git isolation

After connecting:

```bash
git status --short
git ls-files .loopx .codex/goals .local
```

The second command should print nothing. If it lists a path, Git is already tracking local control state;
adding `.gitignore` does not untrack it. Inspect the history before removing anything from the index so you
do not delete valuable local state.

## 7. Optional: enable Providers and Goal features

Basic onboarding is complete. Continue only when this project needs an optional capability.

Start with discovery and Goal configuration. Continue to the Extension example only when you have a
separately distributed Provider to activate.

### Discover Capabilities and optional features

The Capability catalog, Goal feature configuration, and Extension activation are three different
surfaces:

```bash
# 1. Product Capabilities implemented by the current release
loopx capability list --format json
loopx capability show <capability-id> --format json

# 2. Optional features and boundaries configured for this Goal
loopx --format json configure-goal --goal-id <goal-id>

# 3. Extension Providers installed and activated in this environment
loopx extension list --format json
```

`capability list/show` is a read-only catalog. It reports the caller outcome, entry command, protocol,
smoke, and boundary. It does not modify the Goal or install a Provider. Passing
`--extension-manifest` only declares a Provider for that catalog read; `declared=true` does not mean
installed, enabled, or ready.
Use `loopx capability list` for discovery and
`loopx capability show <capability-id>` for one contract.

`configure-goal` without a setting flag is also read-only and returns the current on-demand feature
catalog. There is no generic “enable any capability id” command. Every default-off feature has explicit
configuration fields and boundaries. Read the Goal catalog with
`loopx --format json configure-goal --goal-id <goal-id>`. For example:

```bash
loopx configure-goal \
  --goal-id <goal-id> \
  --change-quality-enabled

loopx configure-goal \
  --goal-id <goal-id> \
  --change-quality-enabled \
  --execute
```

For `multi_subagent`, Explore Graph, Explore Harness, Reward Memory, Lark inbox, and other optional
features, read the current `configure-goal --help` and the catalog's exact delta instead of guessing flags
from feature names. Use this sequence:

1. inspect the read-only catalog;
2. preview without `--execute`;
3. inspect `before`, `after`, `changed_fields`, and the boundary;
4. apply with explicit `--execute`;
5. reread Goal config, status/quota, and relevant Provider readiness.

Also keep the two Todo capability fields separate:

- `required_capabilities`: Host or runtime abilities that must already exist for this execution; a missing
  requirement Gates that candidate;
- `target_capabilities`: an ability this Todo is building, repairing, or validating; a missing target may
  enter repair mode and must not make the repair Todo impossible to run.

“Visible in catalog,” “enabled for this Goal,” “Provider doctor-ready,” and “available in this turn” are
four different facts. The onboarding report should state them separately instead of saying only “the
capability is enabled.”

### Enable an existing Extension during onboarding

Project onboarding may also activate an optional Provider locally, but `connect` must not do that
implicitly. The current `loopx-finance-value-discovery` package is a separately distributed,
zero-permission Extension. An Agent can install it only when you already have a LoopX source checkout, or
an equivalent provider source package, containing
[`packages/loopx-finance-value-discovery`](https://github.com/huangruiteng/loopx/tree/main/packages/loopx-finance-value-discovery).

Append this contract to the onboarding prompt:

```text
After project connection is complete, inspect whether loopx-finance-value-discovery is installed and
enabled in the current environment.

- Run loopx extension list --format json first. Do not infer activation from a directory.
- If the Extension is installed and enabled, execute a read-only doctor probe; do not install it again.
- If it is installed but disabled, explain that enable reruns doctor, then preview and execute enable.
- If it is absent, first confirm that the provider source package and
  packages/loopx-finance-value-discovery/extension.toml exist.
- Changing the Python environment is a local environment write. Show the pip install, extension install,
  and doctor commands and wait for my authority before execution.
- Install the package into the same Python environment that runs `loopx`, and make the Provider entrypoint
  visible on the current shell's `PATH`. Otherwise doctor should report `entrypoint_missing`; do not
  bypass it.
- If the provider source package is unavailable, stop and report that a release-only environment cannot
  download or enable this Extension implicitly.
- Do not describe it as a market-data collector or investment-advice capability. It only reduces frozen
  public-safe evidence supplied by the caller into a bounded research packet. It performs no network,
  account, trading, or continuous-monitoring action.
- Report package installation, Extension enablement, doctor readiness, and one example run separately.
```

The equivalent manual flow is:

```bash
# 1. Observe activation state
loopx extension list --format json

# 2. Only when the provider source package exists and Python-environment writes are authorized
python3 -m pip install ./packages/loopx-finance-value-discovery

# When using a venv, activate it and confirm both commands resolve from that environment
command -v loopx
command -v loopx-finance-value-discovery

# 3. Preview, then register and activate the installed Provider
loopx extension install \
  --manifest packages/loopx-finance-value-discovery/extension.toml \
  --format json

loopx extension install \
  --manifest packages/loopx-finance-value-discovery/extension.toml \
  --execute \
  --format json

# 4. Execute the read-only readiness probe
loopx extension doctor \
  loopx-finance-value-discovery \
  --execute \
  --format json
```

If `extension list` reports the Extension as installed with `enabled=false`, do not install it again:

```bash
loopx extension enable loopx-finance-value-discovery --format json
loopx extension enable loopx-finance-value-discovery --execute --format json
```

Invocation also needs a `finance_value_discovery_input_v0` file. The onboarding report may say “Extension
available” only after `extension list`, an executed doctor, and an example `extension run --execute` all
succeed. The placement case in the next section explains why this package does not register a capability
with the same name.

## Recovery paths

### `loopx doctor` fails

Read the command path, release snapshot, and skill status in the report. If a command skill is missing
after an upgrade:

```bash
loopx slash-commands
loopx slash-commands --install
```

Do not copy `.loopx/` from another checkout without understanding the failure.

### The project already has LoopX state

Reuse it by default. Run `loopx registry`, `loopx status`, and `loopx history` before deciding whether a
migration is necessary. Continue one exact `goal_id`; when several Goals exist, resolve the selection Gate
first. Then register a fresh `agent_id` for the new executor or take over a named identity only when the
user requests it. Do not force a reconnect over a Goal that still carries useful state, and do not confuse
an old Agent identity with the Goal itself.

### A linked worktree points at the wrong directory

The delivery workspace must match the worktree where files are actually changing. Inspect the registry and
repair the route with the supported `refresh-state --delivery-workspace-path` flow. Do not copy active state
to manufacture a second source of truth.

### The global registry is not writable

Project-local state and global visibility are separate layers. Use the registry permission report from
`loopx doctor`, repair ownership or permissions, and sync again. Never commit the global registry to the
project.
