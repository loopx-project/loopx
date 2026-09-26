# Benchmark Toolkit

`benchmark-toolkit` is LoopX's built-in, provider-neutral surface for concurrency
admission, permission, artifact, integrity, and reusable agent-runtime boundaries
around benchmark experiments. It does not own benchmark-family runners, result
ledgers, or scoring adapters.

## Runner execution boundary

The former `loopx benchmark agent-phase` command and its external-agent v1
request/result implementation have been removed. There is no replacement
benchmark-specific subprocess launcher, containment declaration, or environment
variable interface in this toolkit.

- For ordinary benchmark execution, let the existing runner invoke its solver
  directly. The runner still owns workspace provisioning, credentials,
  containment, hard timeout, descendant cleanup, verification, and scoring.
- For LoopX-governed execution, use the existing
  [Turn contract](../../../docs/reference/protocols/loopx-turn-v0.md):
  inspect `loopx turn plan`, then explicitly execute
  `loopx turn run-once --execute` with a supported host or typed host adapter
  and independent validator. Turn is not an argv-compatible alias for
  `agent-phase`, and does not replace benchmark isolation or scoring.

Integrations using `LOOPSBENCH_EXTERNAL_AGENT_REQUEST`,
`LOOPSBENCH_EXTERNAL_AGENT_RESULT`, or
`LOOPX_EXTERNAL_AGENT_SOLVER_COMMAND_JSON` must remove that bridge before
upgrading. Existing result files are not rewritten or deleted, but this toolkit
no longer emits `external_agent_result_v1`. Execution functions under
`benchmark_toolkit.external_agent` are removed; callers of the retained
read-only helpers should use the existing `benchmark_toolkit` package exports
or `benchmark_toolkit.continuation`.

Experiment-board records, integrity qualification, public progress and the
continuation-decision CLI remain unchanged. No run is launched by this migration.

## Bounded continuation decision

When a benchmark treatment deliberately adds LoopX-governed continuation, keep
process launch and progress observation in the runner and ask LoopX only for the
next disposition:

```bash
loopx benchmark continuation-decision \
  --progress-json .local/private-run/public-progress.json \
  --expected-first-prompt-sha256 "$EXPECTED_PROMPT_SHA256" \
  --observed-first-prompt-sha256 "$OBSERVED_PROMPT_SHA256" \
  --expected-total-unit-count 5 \
  --previous-completed-unit-count 2 \
  --completed-segment-count 1 \
  --max-agent-segments 2 \
  --elapsed-ms 300000 \
  --total-budget-ms 7200000 \
  --format json
```

The command is read-only. It accepts only aggregate public progress counts and
returns `continue`, `stop_complete`, `stop_prompt_mismatch`,
`stop_progress_regression`, `stop_task_shape_mismatch`, `stop_round_limit`, or
`stop_time_budget`, plus a fair-share timeout for the next segment. The runner
must give the first solver
segment the complete original task prompt, freeze the initial unit count, and supply
matching independently calculated digests. Later prompts may add
only public progress; they must not disclose verifier output or hidden evaluation.
The runner remains responsible for invoking the next agent segment, measuring the
shared total budget, preserving containment, and collecting evidence.

Before accepting each segment's adapter receipt, bind it to a runner-generated
nonce and the observed segment window:

```bash
loopx benchmark segment-receipt \
  --expected-segment-nonce "$SEGMENT_NONCE" \
  --receipt-segment-nonce "$RECEIPT_NONCE" \
  --segment-started-at "$SEGMENT_STARTED_AT" \
  --receipt-written-at "$RECEIPT_WRITTEN_AT" \
  --segment-ended-at "$SEGMENT_ENDED_AT" \
  --prior-receipt-nonce "$PRIOR_RECEIPT_NONCE" \
  --require-qualified --format json
```

The runner must remove or rotate fixed-path transient artifacts before launch,
pass the fresh nonce into the agent process, and retain prior segment nonces for
replay detection. Missing, wrong-segment, outside-window, and replayed receipts
fail closed; they must not be promoted into terminal evidence. The reducer records
none of the nonces, timestamps, receipt content, paths, or run identity and grants
no process, retry, score, or verifier authority.

## Source revision admission

A long-running campaign can keep launching from an old installed checkout after
the tracked branch advances. Pinning the source once at controller startup does
not prevent that drift. Immediately before each new benchmark admission, obtain
the current reference head through the runner's provider or network boundary,
then compare it with the clean local checkout and intended pin:

```bash
loopx benchmark source-revision-fence \
  --source-checkout /path/to/pinned-source \
  --expected-revision "$PINNED_REVISION" \
  --observed-reference-revision "$OBSERVED_REFERENCE_REVISION" \
  --require-admitted \
  --format json
```

The command succeeds only when all three identities match and the source root has
no tracked or untracked changes. Its compact receipt records equality, cleanliness,
and a stable reason code without recording the checkout path or any revision value.
Invalid input is also reduced to a path-free fail-closed receipt.

The fence is an admission boundary, not a live-run mutation mechanism. A run that
already passed the fence keeps its immutable revision even if the reference moves
later; the new head blocks only subsequent admissions until the runner installs and
pins an updated source. The caller owns the freshness and authority of the observed
reference value. This capability performs no fetch, provider API call, checkout,
install, process launch, score write, or submission.

## Native Codex Goal runtime

Benchmark adapters that use the Codex app-server Goal API should import
`loopx.capabilities.benchmark_toolkit.native_codex_goal`. The module provides the
real stdio JSON-RPC process transport, the ordered Goal transaction, terminal event
correlation, Goal-status polling across automatic continuation turns, and a
public-safe receipt. A runner supplies its environment, sandbox policy, task bridge,
and timeout; it should not copy the Goal state machine.

On Linux, a host-side runner may use `native_codex_isolation` to build the isolated
process command. Its synthetic root contains a read-only system runtime, fresh
`/proc`, `/run`, and `/tmp`, runner-created work children, one explicitly selected
task workspace at the returned `host-visible` alias, and an optional formal LoopX
profile at its verified absolute path. The surrounding host root, original task
path, ambient host `/tmp`, nested host mounts, symlinked work children, and
`/proc/1/root` escape path are absent. The helper requires unprivileged user, mount,
and PID namespaces, `pivot_root`, and `tini`. It runs `tini` as the isolated PID 1
so long-lived workers reap orphaned command subprocesses, and fails closed when
its roots overlap or the init resolves from a mutable task/profile/work root.

Standalone Codex distributions also need their runtime companions after startup.
The envelope now exposes existing `codex-resources/bwrap` files beside the resolved
executable or at its distribution root, plus adjacent `codex-code-mode-host`, as
individual read-only mounts. It does not expose the containing directories or
unrelated neighboring files. Companions must be regular, non-symlinked files
outside the private controller and task workspace roots; invalid candidates fail
closed. Codex's own companion verification remains unchanged. Runners must stage
the executable and companions outside private roots before constructing the
envelope; executable-only custom launchers remain supported.

```python
from loopx.capabilities.benchmark_toolkit.native_codex_isolation import (
    build_native_codex_isolation_envelope,
    rebase_native_codex_loopx_workspace_state,
)

envelope = build_native_codex_isolation_envelope(
    executable="codex",
    process_args=["app-server", "--listen", "stdio://", "--enable", "goals"],
    work_dir=runner_work_dir,
    private_root=controller_private_root,
    workspace_source=task_workspace,
    profile_root=profile.root,
)
# If the selected workspace already contains LoopX control state, relocate its
# generated path references before launch and restore them after termination.
rebase_native_codex_loopx_workspace_state(
    task_workspace,
    source_root=task_workspace,
    target_root=envelope.workspace_alias,
)
# Pass envelope.process_command to probe_native_goal_process or
# run_native_goal_process_until_terminal, and use envelope.workspace_alias as cwd.
# In a finally block after the process terminates:
rebase_native_codex_loopx_workspace_state(
    task_workspace,
    source_root=envelope.workspace_alias,
    target_root=task_workspace,
)
```

The relocation helper is deliberately narrow: it rewrites only LoopX registries
and generated run-history JSON, JSONL, and Markdown under the selected workspace.
It validates every candidate before writing, updates files atomically, rejects
symlinked control-state paths, and leaves task files, model output, trajectories,
verifier evidence, and arbitrary workspace prose untouched. This keeps formally
installed LoopX state readable after the temporary `host-visible` alias disappears.
The two canonical registries form one consistency boundary: both absent means no
control state, while only one present fails closed. If an abrupt process kill skips
the reverse rewrite, a subsequent launch using the same deterministic work
directory first recovers stale alias references.

The profile bind is writable because Codex and an installed LoopX release may need
runtime state. It must therefore be a per-run profile or a runner-restored pinned
snapshot, never ambient state shared across trials.

This is a filesystem/process envelope, not a complete benchmark sandbox. It grants
no model credential, task-command bridge, shell-network policy, evaluator denial,
cross-trial denial, verifier ordering, upload, submission, or scoring authority.
The runner must still attest those boundaries independently. Platforms without the
required Linux namespace primitives must use an equivalent runner-owned isolation
boundary instead of silently falling back to the ambient host.

The runnable source example is
[`benchmark/deepswe/run_native_codex_goal.py`](../../../benchmark/deepswe/run_native_codex_goal.py).
Its `--preflight-only` mode proves a live Codex initialize/thread/Goal attachment
without invoking a model. Full mode starts one turn and waits for a correlated
terminal event, then keeps draining Codex-owned continuation turns until the Goal
leaves `active`. The same total timeout covers the full Goal lifecycle. Add
`--isolate`, `--isolation-work-dir`, and `--private-root` to make this envelope the
real process path; `--profile-root` adds the optional per-run formal profile. The
launcher performs recovery, pre-launch rebase, and `finally` restoration around
both preflight and full Goal modes.

The same adapter publishes `public_trajectory_summary_v0` from the compact
`native_codex_goal_turn_receipt_v0` lifecycle fields. The benchmark toolkit owns
the strict reducer because public/private evidence reduction is already part of
this capability; the DeepSWE research adapter is its first active caller. The
summary carries only typed counts, status labels, and content-free notification
kind counts. It never reopens event payloads, and it marks message and tool-call
semantics unavailable rather than guessing them. Missing, malformed, or
inconsistent lifecycle facts fail closed. The similarly named archived reducer
under `deprecate/benchmark-legacy/` is historical evidence, not a dependency or
compatibility entry point for this native-runner contract.

### Formal installed profile and skill discovery

A treatment that only supplies a Goal prompt and a source-checkout CLI has not
proved the real LoopX product path. The prompt, installed skills, and installed
CLI are three independent inputs. Use `native_codex_profile` to create an isolated
local release through LoopX's shipped `scripts/install-local.sh` instead of copying
skill files or importing an arbitrary checkout:

```python
from loopx.capabilities.benchmark_toolkit.native_codex_goal import NativeGoalConfig
from loopx.capabilities.benchmark_toolkit.native_codex_profile import (
    install_native_codex_profile,
    native_codex_app_server_shell_policy_args,
    native_codex_profile_environment,
    render_native_codex_goal_prompt,
)
from loopx.capabilities.benchmark_toolkit.provider_gateway import (
    serve_runner_owned_provider_gateway,
)

profile = install_native_codex_profile(loopx_source, isolated_profile_root)
prompt = render_native_codex_goal_prompt(
    profile,
    project_root=task_visible_cwd,
    goal_id=goal_id,
    agent_id=agent_id,
    runtime_registry_path=case_runtime_registry,
)
config = NativeGoalConfig(
    cwd=task_visible_cwd,
    objective=prompt.task_body,
    task_instruction=task_instruction,
    required_skill_ids=profile.required_skill_ids,
)
process_env = native_codex_profile_environment(profile, base_env=runner_environment)
process_env["LOOPX_MODEL_PROVIDER_SENTINEL"] = (
    "runner-owned-gateway-no-upstream-secret"
)
shell_policy = native_codex_app_server_shell_policy_args(
    excluded_env_keys=("LOOPX_MODEL_PROVIDER_SENTINEL",),
)
with serve_runner_owned_provider_gateway(
    upstream_base_url=runner_provider_base_url,
    upstream_bearer_token=runner_provider_credential,
) as gateway:
    # Configure app-server's provider with gateway.base_url and the sentinel,
    # then launch it inside build_native_codex_isolation_envelope(...).
    ...
```

The profile installer redirects the release, executable, manual, home, and Codex
skill roots into the supplied isolated directory. It uses the fixed installer path,
including its generated `$loopx` entry skill and packaged workflow-skill readback;
unrelated interactive slash-command surfaces are disabled for this non-interactive
worker. Inspection verifies a release-snapshot CLI, exact source revision, clean
source by default, skill-tree digests, and `doctor --agent-type codex-app-ssh`.

`render_native_codex_goal_prompt` calls `heartbeat-prompt --thin` through the
release-snapshot CLI, requires the `codex_app_ssh_goal` profile and interface budget,
and proves that the returned body names that installed CLI. For an isolated case it
also replaces the generic global-registry token with the explicit case registry.
Keep app-server on `native_codex_profile_environment`; it supplies only the
formal profile's `HOME`, `CODEX_HOME`, and `PATH`. The upstream provider value
must remain in `serve_runner_owned_provider_gateway`, while app-server receives
only the loopback gateway URL and a fixed non-secret sentinel. On Linux, place
app-server inside `native_codex_isolation` so its fresh PID namespace and
synthetic root hide the runner process, ambient HOME, provider files, and the
controller-private root. Environment filtering without that OS boundary is not
credential isolation: a danger-full-access child can otherwise inspect parent
process environments. `native_codex_app_server_shell_policy_args` keeps a small
model-created shell environment as defense in depth. Platforms without an
equivalent authority boundary must fail closed or use a container/VM path such
as Pier; they must not fall back to ambient native execution. Setting
`required_skill_ids` makes the native runtime call the real
app-server `skills/list` surface before `thread/start`;
missing skills, discovery errors, or a wrong cwd fail before any model turn. The
path-free profile, prompt, and Goal receipts can then prove all three inputs without
publishing installation paths, prompt text, or skill bodies.

Run the formal installer plus no-model readback smoke with:

```bash
python examples/benchmark-native-goal-installed-profile-smoke.py \
  --require-app-server
```

The helper installs only into its target directory. It grants no credential,
network, task, evaluator, upload, submission, or scoring authority; those remain
runner-owned boundaries.

The toolkit borrows the useful contracts already established by modern benchmark
runners: an ATIF-compatible agent trajectory, a separately owned verifier phase,
explicit attempt accounting, and compact result reduction. LoopX adds the control-
plane pieces that a container runner cannot infer by itself: model-visible source
permissions, host and cross-trial isolation, credential propagation, canonical
case-local state, verifier ordering, public/private evidence reduction, and matched-
pair countability.

## Treatment fidelity boundary

Treatment fidelity proves that the preregistered experimental factor actually ran,
for example that guided startup preceded product edits and the solver authored its
own business decomposition. It must not require an implementation-classified Todo
merely because an arm uses LoopX. Todo role labels may be recorded as diagnostics,
but affect countability only when that exact role requirement was preregistered.

The former `plan-fidelity` command and Python API were removed because no shipped
benchmark runtime consumed them and a generic action-kind taxonomy is not a valid
treatment gate. A study that preregisters a role-based factor owns that narrow
adapter check; it must not promote the check into a default LoopX requirement.

## Integrity qualification

### TraeX evidence capture

TraeX `exec --json` emits an automation-facing stdout JSONL stream rather than a
complete copy of its archived session. Convert that private stream into ATIF before
integrity qualification, and optionally provide the matching private archived JSONL
for an independently observed runtime model route:

```bash
loopx benchmark traex-evidence \
  --source-jsonl .local/private-run/traex-stdout.jsonl \
  --route-source-jsonl .local/private-run/traex-session.jsonl \
  --atif-output .local/private-run/agent/trajectory.json \
  --route-receipt-output .local/private-run/public/model-route.json \
  --requested-model GPT-5.4 \
  --require-runtime-route \
  --execute --format json
```

Without `--execute`, the command validates and previews without writing. The private
ATIF retains tool arguments and observations for local integrity analysis. The route
receipt contains only compact requested and observed route labels and one of
`runtime_route_verified`, `runtime_route_mismatch`, `runtime_route_ambiguous`, or
`route_requested_not_runtime_audited`; it never contains prompts, raw tool content,
or paths. Stdout JSONL normally has no runtime route event, so omitting
`--route-source-jsonl` does not prove which model ran. When a separate archive is
supplied, its session id must exactly match the stdout `thread.started` id. The
converter covers the observed TraeX command and file-change stdout events plus
archived function-call and custom-tool-call pairs; an unknown action-bearing stdout
or archive item fails closed rather than producing a partial audit trajectory. This
command does not launch TraeX, read
verifier data, score a run, or publish either artifact.

Run integrity qualification after the agent phase and after the runner has produced
its isolation attestation. The trajectory and any sensitive values remain private
local inputs:

```bash
export BENCHMARK_PROVIDER_CANARY='a-private-value-known-to-the-controller'

loopx benchmark integrity-qualification \
  --trajectory-json .local/private-run/agent/trajectory.json \
  --runtime-attestation-json .local/private-run/runtime-attestation.json \
  --sensitive-value-env BENCHMARK_PROVIDER_CANARY \
  --require-qualified \
  --format json
```

Automated restricted-source and host-boundary probe matches are suspicion signals,
not a cheating verdict. They keep `integrity_qualified=true`, emit
`restricted_access_review.state=suspected`, and remain score-eligible while a
post-run analyst reviews the actual information flow. This includes a host-escape
marker such as `/proc/1/root`: the marker alone cannot prove that the request left
the isolated namespace, disclosed restricted material, or influenced the solution.
After the solver is terminal and scoring is complete, the analyst reads the real
solver trajectory, tool results, and final workspace and may attach this compact
decision:

```json
{
  "schema_version": "benchmark_restricted_access_adjudication_v0",
  "decision": "qualified_with_warning",
  "reviewer_role": "post_run_analyst",
  "reviewed_surfaces": [
    "solver_trajectory",
    "tool_results",
    "final_workspace"
  ],
  "restricted_material_disclosed": false,
  "causal_use_observed": false,
  "evidence_id": "case-integrity-review-1"
}
```

Pass it with `--restricted-access-adjudication-json <compact.json>`. The only
disqualifying decision is `confirmed_cheating`, and it is valid only when restricted
material was actually disclosed and the analyst found that it causally entered a
solving or validation decision. A blocked request, empty result, or disclosed content
with no observed causal use remains countable with an audit warning. The evidence id
is a public-safe pointer; raw trajectory content and private paths stay outside the
receipt.

The command emits `benchmark_integrity_qualification_v0`. It records only stable
labels, counts, reason codes, step ids, and SHA-256 digests. It never emits raw tool
arguments, observations, sensitive values, input paths, task text, verifier output,
or trajectory content. Invalid private input returns a generic fail-closed error so
JSON parser details cannot echo private data.

Qualification rejects a run when it detects any of the following:

- post-run agent confirmation that restricted answer, out-of-scope task-source,
  hidden-test, verifier, other-trial, or controller-private material was both
  disclosed and causally used during solving or validation;
- host escape, credential probing or exposure, or shell network access;
- malformed or incomplete ATIF tool evidence;
- missing runner authority or any required runtime isolation attestation.

Credential-probe detection reads typed command fields, then classifies direct
environment commands, runtime-language enumeration or sensitive-name lookups, and
procfs reads. Neighboring tool prose and commands that only inspect or write source
text are not executed credential reads. Launching a child with an explicit
environment is also not itself a credential read; sensitive values are still
scanned in every tool argument and observation.

Access-request markers are evaluated only on tool calls that can perform or request
resource access. Exact known controller-only calls such as `update_plan` carry
narrative metadata and are excluded from that scan; an actual sensitive value in
their arguments still fails qualification. Unknown tool names remain fail-closed.

Task-source boundaries are benchmark-owned rather than inferred from repository
names or shell prose. A runner that forbids solver access to an upstream checkout,
reference package, or other task source should add its private path or command marker
to `denied_argument_markers.restricted_task_source_request`. Matching inspects typed
argument strings before JSON escaping; the public receipt keeps only the category,
count, step id, tool name, and argument digest, never the configured marker or raw
command. This records an explicit access request even when it returns no content,
without adding a benchmark-specific substring denylist to LoopX core. The request
remains a countable suspicion until post-run causal adjudication confirms cheating.

`benchmark_cheating_detected` is narrower than `integrity_qualified=false`.
It becomes true only after the post-run analyst confirms both restricted-material
disclosure and causal use. A scanner hit, missing isolation proof, or credential leak
does not by itself become confirmed answer cheating; isolation and credential
failures can still make the run uncountable through their independent blockers.

### Network access policy

Offline coding benchmarks run with `network_access: "denied"` (the default): any
shell network use is a policy violation and the runner must attest
`shell_network_denied: true`. A benchmark that starts a case-local HTTP service may
opt in to `"network_access": "loopback_only"`; the runner then attests
`external_shell_network_denied: true`, literal `localhost` or loopback-IP HTTP
requests are admitted, and lookalike, malformed, mixed, or external hosts still
fail closed. This explicit mode prevents a local-service exception from silently
changing the default isolation contract:

```json
{
  "schema_version": "benchmark_integrity_policy_v0",
  "policy_id": "offline-loopback-only",
  "network_access": "loopback_only"
}
```

Web-research benchmarks legitimately need external network during the solving
phase. A custom policy may declare `"network_access": "permitted_solving"`; the
runner then attests `network_permitted_solving: true`, and loopback and external
network-request evidence is recorded but does not fail the run. The
restricted-resource denials (answer, hidden tests, verifier, other trials,
controller state, host escape, credentials) remain fail-closed in every mode:

```json
{
  "schema_version": "benchmark_integrity_policy_v0",
  "policy_id": "widesearch-permitted-solving",
  "network_access": "permitted_solving"
}
```

`network_access` is validated to `denied`, `loopback_only`, or
`permitted_solving`; any other value is rejected. The qualification receipt
exposes the resolved `network_access`, distinct loopback/external evidence counts,
and the attestation checks that actually applied to that mode.

## Runner attestation

The attestation is a compact runner-owned JSON object, not an agent assertion:

```json
{
  "schema_version": "benchmark_runtime_integrity_attestation_v0",
  "authority": "runner",
  "benchmark_id": "fixture@v0",
  "case_id": "case-1",
  "agent_phase_isolated": true,
  "evaluator_sources_denied": true,
  "other_trials_denied": true,
  "controller_state_denied": true,
  "host_escape_denied": true,
  "shell_network_denied": true,
  "provider_credential_shell_excluded": true,
  "case_local_control_state": true,
  "canonical_control_state_root": true,
  "independent_verifier": true,
  "verifier_started_after_agent": true,
  "official_feedback_blinded": true
}
```

Every boolean is required and must be true. A clean trajectory scan cannot prove a
filesystem or namespace permission boundary, so missing attestation fails closed.
Likewise, the attestation alone cannot prove what tool calls actually occurred; both
evidence channels are required.

For `loopback_only`, replace `shell_network_denied` with
`external_shell_network_denied`. For `permitted_solving`, replace it with
`network_permitted_solving`. The field names intentionally match the actual runner
claim; a runner must not attest that all shell networking was denied after allowing
a loopback socket.

`benchmark_id`, `case_id`, and a custom policy's `policy_id` are public labels, not
paths. Path-like values fail closed and are emitted only as `redacted`, so a runner
cannot move an operator directory into the public receipt through identifier fields.
`case_id` alone may use the canonical two-segment `namespace/name` form used by
public benchmark catalogs. Absolute paths, dot segments, backslashes, whitespace,
and deeper path shapes remain rejected; benchmark and policy ids remain single-token
labels.

### Exact-job container binding

Runtime evidence must belong to the same job as the score. An image-only Docker
lookup is ambiguous as soon as two benchmark arms use the same image concurrently.
Before inspecting isolation settings, bind the container with the runner-owned job
or trial label, the service label, and the expected image:

```python
from loopx.capabilities.benchmark_toolkit import (
    compact_docker_container_binding_receipt,
    select_exact_docker_container,
)

binding = select_exact_docker_container(
    ancestor_image="benchmark-runner:fixture",
    required_labels={
        "com.docker.compose.project": job_id,
        "com.docker.compose.service": "main",
    },
)
container_name = binding.container_name  # private runner state; do not publish
receipt = compact_docker_container_binding_receipt(binding)
```

The selector fails closed unless exactly one running container matches. The compact
`benchmark_exact_container_binding_v0` receipt records only the required label keys,
match count, and a SHA-256 selector digest; it excludes the raw container identity
and label values. The helper grants no Docker or runner authority. Callers that need
a privileged wrapper must supply their own `command_runner` and keep that authority
outside the receipt.

### Runtime closeout continuity

A terminal result is not sufficient evidence that the process closing a run uses
the same runtime artifact and attempt generation admitted at launch. Before writing
a terminal score, compare the runner-owned SHA-256 bindings and its typed event-
window qualification:

```bash
loopx benchmark runtime-continuity \
  --launch-runtime-digest "$LAUNCH_RUNTIME_DIGEST" \
  --closeout-runtime-digest "$CLOSEOUT_RUNTIME_DIGEST" \
  --launch-generation-digest "$LAUNCH_GENERATION_DIGEST" \
  --closeout-generation-digest "$CLOSEOUT_GENERATION_DIGEST" \
  --event-window-state qualified \
  --require-qualified \
  --format json
```

The gate allows a closeout only when both content-addressed bindings match and the
provider has qualified the required events within that run's launch-to-terminal
window. A generation mismatch is routed back to its launch generation; a runtime
artifact mismatch is rejected; missing, ambiguous, or out-of-window evidence stays
unqualified. The compact receipt exposes equality, typed reason codes, and route
guidance, but never the digests, run identity, event payloads, or paths. A false
`closeout_write_allowed` is a machine-enforced obligation when callers use
`--require-qualified`; `recommended_transition` remains provider guidance.

The runner remains responsible for creating immutable artifacts and generations,
classifying the event window from its private evidence, applying the route guidance,
and writing the terminal row. This reducer reads no files and grants no runner,
verifier, scoring, upload, or submission authority.

Benchmark-specific private roots can be added without committing them through an
ignored `benchmark_integrity_policy_v0` file:

```json
{
  "schema_version": "benchmark_integrity_policy_v0",
  "policy_id": "local-run-policy",
  "denied_argument_markers": {
    "other_trial_request": ["<private-other-trial-root>"],
    "controller_private_state_request": ["<private-controller-root>"]
  }
}
```

The policy values are used only for in-memory matching and are not copied into the
receipt.

## Four-arm Goal/LoopX studies

When a benchmark-specific solver hint may change outcomes independently of LoopX,
use a two-by-two study instead of comparing a plain Goal baseline with a hinted
LoopX treatment:

| Arm | LoopX startup | Domain hint | Experiment-board role and anchor |
| --- | --- | --- | --- |
| `goal_plain` | off | off | `baseline` |
| `loopx_plain` | on | off | `treatment`, anchored to `goal_plain` |
| `goal_<hint-id>` | off | on | `control`, anchored to `goal_plain` |
| `loopx_<hint-id>` | on | on | `treatment`, anchored to `goal_<hint-id>` |

The domain hint is benchmark-owned solver guidance. For a software-engineering
benchmark it may ask the solver to implement, validate, and review; another
benchmark supplies its own domain-appropriate hint. It must remain independent of
LoopX. LoopX guided startup is a separate provider-owned action and must never be
inserted into the task goal text.

Create a local spec and qualify it before preregistering runs:

```json
{
  "schema_version": "benchmark_four_arm_spec_v0",
  "base_goal_text": "Complete the requested benchmark task.",
  "domain_hint": "Apply the benchmark's declared domain workflow.",
  "hint_id": "domain_hint",
  "domain_hint_independent_of_loopx": true
}
```

```bash
loopx benchmark four-arm-contract \
  --spec-json <four-arm-spec.json> \
  --require-qualified \
  --format json
```

The default CLI receipt contains prompt hashes but not prompt text. Its
`qualified` field covers factor design and within-pair prompt parity only;
`execution_qualified` remains `false`. The listed runner obligations are requirements,
not evidence that launch-time parity, input pinning, or board registration occurred.
A trusted local
runner may use the Python builder or explicitly pass `--include-prompt-text`. At
launch it must compare the final task-goal hash with the selected arm, pin every
non-factor input (case, model, reasoning, deadline, permissions, runner, and scorer),
and record the arm on the experiment board. The plain Goal/LoopX pair and the hinted
Goal/LoopX pair must each have identical task-goal hashes. The contract grants no
runner, model, LoopX-startup, verifier, or scoring authority.

The primary comparisons are LoopX without the hint, the hint without LoopX, and
LoopX with the hint. The interaction contrast compares the two LoopX effects. A
historical arm that mixed startup guidance and domain guidance is diagnostic only;
renaming it does not make it a member of this factorial study.

After all four cells have board rows, pass the compact contract back to the board
read model:

```bash
loopx benchmark experiment-board-show \
  --goal-id <goal-id> \
  --four-arm-contract-json <compact-four-arm-contract.json> \
  --format json
```

The factorial projection selects exactly one score-countable run per declared cell,
checks exact anchors, non-factor parity, and distinct LoopX/non-LoopX runtime
cohorts, then reports the three conditional effects plus their
difference-in-differences. It fails closed on missing or ambiguous cells. This is
separate from the standard matched-pair projection: the hinted Goal cell remains a
`control`, not a baseline, and the hinted LoopX cell is qualified only through the
explicit factorial contract. The read model does not infer membership from arm
names and does not elevate design qualification into runner or scorer authority.

## Experiment lifecycle

A countable experiment uses the toolkit in this order:

1. When domain guidance is a factor, qualify the four-arm contract and freeze its
   task-goal hashes before preregistration.
2. Read the project experiment board before selecting or launching another case.
3. Read or configure the concurrency envelope, then reconcile its reservations with
   exact runner liveness.
4. Declare a `run_permission_policy_v0` and preflight the runner boundary.
5. Atomically admit a case slot immediately before the independently authorized
   runner launch.
6. Upsert the planned or running row, then launch one frozen case/arm; do not expose
   evaluator sources or official feedback.
7. Capture ATIF tool evidence and a runner-owned runtime attestation.
8. During active monitoring, classify exact-job runtime evidence; do not infer
   liveness from an occupied admission slot.
9. Before a terminal write, require runtime continuity between the launch artifact,
   closeout artifact, launch generation, closeout generation, and event window.
10. Run `integrity-qualification`; if restricted access is only suspected, keep the
    score eligible and queue post-run causal adjudication; stop on any actual blocker.
11. Run the independent verifier only after the agent phase.
12. Reduce the official result through the benchmark-owned scoring path.
13. Upsert terminal score, countability, effort, treatment fidelity, and insight
    status; release its reservation; then read the matched-comparison projection.
14. Apply attempt-countability, treatment-fidelity, and matched-pair gates before any
    comparison claim.

Integrity qualification is necessary but not sufficient for a score claim. It does
not establish task correctness, official score authority, experiment parity, or a
LoopX advantage. `score_claim_eligible=true` only permits the official score and
matched-pair gates to run; `score_claim_countable` and `matched_pair_countable` stay
false in this receipt. Those remain separate verifier and comparison contracts.

## Concurrency envelope

Configure one goal-scoped envelope before launching parallel cases. The total cap
is shared by baseline and test-group runs; `control`, `treatment`, and `explore`
consume test-group capacity. A reserved test count prevents baseline work from
starving the comparison lane, while a lower target permits a staged ramp below the
hard maximum.

```bash
loopx benchmark concurrency-configure \
  --goal-id <goal-id> \
  --max-active-cases 8 \
  --target-active-cases 6 \
  --max-baseline-cases 7 \
  --max-test-cases 4 \
  --reserved-test-cases 1 \
  --require-resource-headroom-receipt \
  --execute \
  --format json

loopx benchmark concurrency-status \
  --goal-id <goal-id> \
  --format json
```

Before each runner launch, atomically reserve a slot. If admission returns
`ok=false`, do not launch. Release the exact run only after it is confirmed terminal
or runner-invalid:

```bash
loopx benchmark concurrency-admit \
  --goal-id <goal-id> \
  --run-id <run-id> \
  --case-id <case-id> \
  --arm-role <baseline|control|treatment|explore> \
  --resource-headroom-json resource-headroom.json \
  --execute \
  --format json

loopx benchmark concurrency-release \
  --goal-id <goal-id> \
  --run-id <run-id> \
  --execute \
  --format json
```

Configuration, admission, and release are project-local, locked, and atomic.
`max-active-cases` is the hard ceiling; `target-active-cases` is desired occupancy.
Below target, status reports the exact gap, a preferred arm group, and
`next_action=backfill_to_target`. At target, new admission fails closed with
`target_capacity_exhausted`. When target is lowered below current occupancy, status
reports `next_action=drain_to_target`; no active run is terminated, and replacement
admission remains closed until occupancy falls below target. `active_counts` is an
admission ledger, not runtime proof. On each launch, terminal or runner-invalid
transition, and a bounded periodic cadence, pass exact-job receipt and runner-owner facts through
`runtime-observation`. Apply its typed terminal or runner-invalid transition before
releasing that reservation, then backfill the reported gap.

For hosts where parallel jobs can exhaust temporary storage, memory, process
capacity, file descriptors, persistent storage, or provider capacity, enable
`--require-resource-headroom-receipt`. Each new admission must then include a
fresh `benchmark_resource_headroom_receipt_v0`. The provider observes its own
environment and supplies only typed `sufficient`, `insufficient`, or `unresolved`
checks plus a validity window of at most 15 minutes. Missing, expired, future,
unresolved, or insufficient receipts fail closed before the slot is reserved. Each
check must observe the runner-resolved resource actually consumed by the launch—for
example its profile, cache, scratch, and artifact filesystems—not merely a generic
host default such as `/tmp`; if that binding cannot be proven, report `unresolved`.
LoopX never records raw metrics, paths, provider logs, or the receipt in the
envelope, and the receipt does not grant launch authority.

Read back the gate with `concurrency-status`. To disable it, rerun
`concurrency-configure` with the same capacity values and omit
`--require-resource-headroom-receipt`; existing active reservations are preserved.

To ramp toward the hard ceiling without guessing a new occupancy on every monitor
cycle, feed compact runner-owned health into the adaptive tuner. It uses additive
increase after consecutive saturated healthy windows and subtractive decrease on
launch, provider-capacity, runner-invalid, or typed resource-pressure evidence:

```bash
loopx benchmark concurrency-tune \
  --goal-id <goal-id> \
  --feedback-json concurrency-feedback.json \
  --resource-headroom-json resource-headroom.json \
  --saturated-healthy-windows-required 2 \
  --increase-step 1 \
  --decrease-step 1 \
  --execute \
  --format json
```

`concurrency-tune` changes only `target-active-cases`. The configured
`max-active-cases`, baseline/test caps, and reserved test slots remain
operator-owned. Lowering the target never terminates an active run; it only prevents
replacement admissions until occupancy falls below the new target. Missing, stale,
future, or unresolved feedback/headroom produces a hold; malformed input fails closed
without a write. Feedback also carries the exact `updated_at` revision of the
concurrency envelope it observed. Any configure, target change, admission, or release
invalidates that receipt, so one healthy window cannot be replayed across target
levels. A runner may preserve its healthy-window streak across ordinary campaign
churn only when the transition is a qualified terminal run followed by a successful
refill and the whole observation window has no launch failure, provider-capacity
rejection, runner-invalid transition, or typed resource pressure. It must then issue
new feedback bound to the post-refill envelope revision; the pre-transition receipt
remains invalid. Reset the streak for any failed refill, unresolved terminal state,
or pressure signal. Preview
is the default; `--execute` atomically writes the selected target. The runner remains
responsible for measuring resources, constructing `benchmark_concurrency_feedback_v0`,
and launching admitted work; raw metrics and receipts are never persisted.

```json
{
  "schema_version": "benchmark_concurrency_feedback_v0",
  "observed_envelope_updated_at": "2026-09-01T03:49:30Z",
  "window_started_at": "2026-09-01T03:50:00Z",
  "observed_at": "2026-09-01T04:00:00Z",
  "expires_at": "2026-09-01T04:05:00Z",
  "saturated_healthy_window_streak": 2,
  "launch_attempts": 1,
  "launch_failures": 0,
  "provider_capacity_rejections": 0,
  "runner_invalid_transitions": 0
}
```

```json
{
  "schema_version": "benchmark_resource_headroom_receipt_v0",
  "observed_at": "2026-08-23T04:00:00Z",
  "expires_at": "2026-08-23T04:05:00Z",
  "checks": [
    {"kind": "temporary_storage", "state": "sufficient"},
    {"kind": "process_capacity", "state": "sufficient"}
  ]
}
```

Every participant must resolve the same goal repository and envelope file on a
filesystem that supports LoopX's inter-process lock and atomic replacement. Separate
checkouts or host-local copies do not share capacity; this envelope is not a
distributed semaphore. A multi-host campaign must route admission through one
shared authority instead of configuring one envelope per host.

At campaign startup, create the capability packet's
`concurrency_occupancy.monitor_todo_template` as one goal-scoped
`continuous_monitor` todo. This preserves the obligation to notice and fill safe
capacity without granting launch authority. On material monitor windows, preview
`concurrency-tune`; execute its target change only when the runner-authorized campaign
has opted into adaptive occupancy. The runner still owns launch, liveness,
termination, credentials, verifier ordering, scoring, upload, and submission.

## Experiment board

The project-local experiment board keeps baseline, standard control or treatment,
and diagnostic explore runs in one compact projection. It is not a second score
authority and never stores raw task text, trajectories, logs, hidden evaluation,
verifier output, credentials, or local paths.

An agent using this capability should start or resume a study by reading the board:

```bash
loopx benchmark experiment-board-show \
  --goal-id <goal-id> \
  --format json
```

For a preregistered four-arm study, add
`--four-arm-contract-json <compact-four-arm-contract.json>` to project conditional
effects and the interaction contrast alongside ordinary matched comparisons.

Preview and then execute an idempotent row update when a run starts or reaches a
terminal state:

```bash
loopx benchmark experiment-board-upsert \
  --goal-id <goal-id> \
  --row-json <compact-row.json> \
  --format json

loopx benchmark experiment-board-upsert \
  --goal-id <goal-id> \
  --row-json <compact-row.json> \
  --execute \
  --format json
```

The default ledger is locked, atomically updated, and keyed by benchmark, study,
case, and run identity. A compact row carries arm role, exact and comparison
protocol ids, model, score metrics, countability, treatment fidelity, bounded
effort, and an optional insight status or public-safe handle. When an arm uses an
orchestration/control runtime, record its public-safe `provider_id`, exact
`revision`, and optional package `version` in `orchestrator_runtime`; keep this
separate from `runner_revision`, which identifies the benchmark runner. The board
summary groups rows by that exact runtime identity so version cohorts are not
silently pooled. Unknown fields and path-like references fail closed.

Every non-baseline row names an exact `comparison_anchor_run_id`. Standard control
or treatment rows anchor to a baseline. Explore rows use `diagnostic_only` claim
scope and may anchor to the baseline or fixed standard arm they are examining.
Matched comparisons require compatible benchmark, study, case, model, primary
metric, comparison protocol, score countability, and treatment fidelity. Exact
protocol revisions remain visible as a warning even when a declared comparison
protocol says an older credible score remains semantically comparable.

Full post-run analysis stays in private `benchmark_case_insight_v0` storage. The
board records only its compact status or handle, so reading the board cannot widen
the solving agent's evidence boundary.

Providers may keep separate public-safe ledgers for independent runner queues. Fold
those shards into the canonical project board before agent readback:

```bash
loopx benchmark experiment-board-reconcile \
  --goal-id <goal-id> \
  --source-ledger <provider-a.jsonl> \
  --source-ledger <provider-b.jsonl> \
  --execute \
  --format json
```

Without `--execute`, the command previews the reconciled board. Reconciliation is
idempotent and monotonic: newer legal lifecycle transitions advance a stable run,
late non-terminal rows cannot reopen a terminal run, and conflicting terminal
states fail closed. Receipts report only compact row counts and never record source
paths. The command validates the complete candidate set before its first write;
executed batches are replay-safe, while providers remain responsible for retrying a
batch that is interrupted before its receipt is returned.

## Post-run case insight monitor

Benchmark startup should create one `continuous_monitor` todo that owns both the
campaign score update and the post-run case analysis. Whenever a case reaches a new
material scored state, the monitor first reads the public-safe experiment-board
projection and refreshes the countable baseline, treatment, and matched-pair totals.
It then reports material aggregate changes to the user and, after the solver is
terminal, runs a post-run analyst brief and writes one private
`benchmark_case_insight_v0` artifact. A bounded periodic review while a campaign is
active prevents a long run from silently accumulating results. This monitor is part
of the benchmark lifecycle, not an optional cleanup pass. The catalog entry is a
guidance template rather than a scheduler: the benchmark startup provider creates
the todo, and the registered monitor runtime owns its cadence.

### Monitor-to-advancement handoff

A benchmark `continuous_monitor` is an observation and control-plane lane. Do not
put repository delivery, runner repair, experiment redesign, or PR work only in
the monitor text and expect it to execute. When a monitor poll discovers material
bounded work, record the transition and create an independent executable successor
in one writeback:

```bash
loopx quota monitor-poll \
  --goal-id <goal-id> \
  --todo-id <monitor-todo-id> \
  --agent-id <registered-agent> \
  --result-hash <public-safe-hash> \
  --material-change \
  --next-agent-todo "<bounded public-safe work>" \
  --next-action-kind <action-kind> \
  --next-task-repository <git-repository> \
  --next-required-capability <capability> \
  --execute \
  --format json
```

The monitor remains open, while the new `advancement_task` enters ordinary claim,
lease, validation, and delivery lifecycle. The poll itself spends no delivery
quota. An unchanged poll creates no successor.

When the main campaign Todo must remain visible but cannot advance until a monitor
generation changes—for example, target occupancy is full—keep that Todo `open` and
pair the wait with an already-created independent runnable successor:

```bash
loopx todo update \
  --goal-id <goal-id> \
  --todo-id <waiting-advancement-todo-id> \
  --agent-id <registered-agent> \
  --status open \
  --resume-when monitor_changed:<monitor-todo-id> \
  --successor-todo-id <independent-runnable-successor-id> \
  --reason "<public-safe external-wait rationale>" \
  --format json
```

The resume condition removes the waiting Todo from runnable selection until the
monitor records a newer material-change generation. Do not mark this typed external
wait `blocked`, and do not use the monitor itself as the runnable successor.

A material user update should include the current countable arm and pair coverage,
aggregate primary metric by arm, binary outcomes when the benchmark exposes them,
feature and preservation guardrail totals when the benchmark exposes them,
improved/flat/regressed pair counts, and the new causal insight or next probe.
When effort stratification is useful, preregister benchmark-appropriate fixed
boundaries and assign every matched case from the baseline arm's
`effort.duration_ms`. Reuse that same case bucket for every candidate arm; candidate
duration must not define difficulty because it is itself a treatment outcome. Per
bucket, report pair count, primary/binary/feature/preservation metrics, and
improved/flat/regressed counts. Treat these strata as descriptive sensitivity
analysis unless the study preregistered a causal subgroup claim. Derive score fields
from the experiment board or benchmark-owned scoring projection, not from raw
private evidence. Do not send a repetitive update when no score, coverage,
direction, insight, or material runner state changed.
Only public-safe conclusions from the private post-run insight may enter that user
update; raw evaluation evidence remains private.

During an active-campaign review, distinguish a clean worktree from a lack of
solver progress. `git status` observes only uncommitted changes. Bind the readback
to the exact job, compare its current `HEAD` with the start revision recorded at
admission, and combine that committed delta with the current worktree status.
Correlate those facts with Goal/event freshness, typed runner errors, and the
solver's trajectory phase. A clean worktree or a high raw log-error count alone is
not evidence that a run is stuck. The provider-owned classifier may mark a run
stalled only when committed and uncommitted progress are both absent and either the
trajectory is stale or typed fatal runner evidence is present.

Admission-ledger occupancy is not process liveness. On each bounded active review,
the provider should reduce compact facts through:

```bash
loopx benchmark runtime-observation \
  --admission-active \
  --job-receipt-state resolved \
  --runner-owner-state alive \
  --require-healthy \
  --format json
```

Only a resolved exact-job receipt plus a live exact runner owner is healthy active.
A terminal result, typed fatal runner error, or exact owner missing after the
provider's startup grace produces a reconciliation transition; the provider must
write the terminal classification before releasing its slot. Missing or ambiguous
runtime authority fails closed. The reducer performs no process discovery, writes,
or slot release, and its receipt contains no run identity, process arguments, raw
error, or path.

Every due active-campaign monitor cycle must also advance at least one bounded
solver-trajectory slice, even when no case became terminal. This readback is for
campaign supervision and insight discovery only; it must not expose hidden
evaluator evidence to the solving arm.

When that readback produces a useful case-level runtime finding before terminal
scoring, preserve it as a private provisional observation rather than waiting for
the final grader or overstating it as a scored insight:

```json
{
  "schema_version": "benchmark_case_observation_v0",
  "case": {
    "benchmark_id": "<public-id>",
    "case_id": "<public-id>",
    "arm": "<baseline-or-treatment>"
  },
  "run_status": "running",
  "runtime_outcome": "<ok-error-in_progress-or-unknown>",
  "duration_ms": null,
  "evidence_refs": [
    {
      "kind": "trace",
      "trace_id": "<opaque-id-or-null>",
      "span_id": "<opaque-id-or-null>",
      "env": "<environment-token-or-null>",
      "artifact_ref": "<private-pointer-or-null>"
    }
  ],
  "hypothesis": "<provisional-causal-explanation>",
  "confidence": "medium",
  "promotion_state": "pending_terminal_score_review"
}
```

`trace_id`, `span_id`, and `env` are provider-neutral optional traceability fields.
Provider-specific session identifiers belong in private provider extensions, not in
this common contract. Raw evidence references can still disclose sensitive runtime
topology, so the artifact stays in private benchmark storage. The public experiment
board records only a compact classification or private artifact handle; it never
copies trace IDs, spans, URLs, paths, or provider-specific session identifiers.

The provisional observation must not invent a score or treat request success,
progress, or runtime status as case quality. Once the run is terminal and scoring
is complete, the analyst re-reads the complete authorized evidence and writes a
separate `benchmark_case_insight_v0`; it does not relabel the provisional artifact
as final.

This is a provider obligation, not an effect performed by the reducer: the
runtime-observation command only returns a typed classification and recommended
transition. The provider remains responsible for the monitor cycle, trajectory
readback, terminal write, reconciliation, and slot release.

Use this analyst hint:

> After the solver has stopped and scoring is complete, read the task, real
> trajectory, final patch or workspace, hidden tests, grader or verifier, and full
> failure and score details; explain the decisive evidence, why the outcome
> happened, whether it was expected, and what LoopX should test or change next.

The solver and analyst are separate roles. The solver remains unable to access
hidden tests, evaluator sources, expected answers, or official feedback. Only the
post-run analyst may read the complete private evaluation evidence, and only after
the solver is terminal and scoring is complete.

The active-campaign monitor may inspect the solver-owned trajectory and exact-job
runtime while the solver is active, but it must not read hidden evaluator evidence
or send its findings back into the solving arm.

Record the result in this compact shape:

```json
{
  "schema_version": "benchmark_case_insight_v0",
  "case": {
    "benchmark_id": "<public-id>",
    "case_id": "<public-id>",
    "arm": "<baseline-or-treatment>"
  },
  "outcome": {
    "status": "<completed-or-runner-invalid>",
    "score": "<official-score-or-null>",
    "countable": "<true-or-false>"
  },
  "evidence_reviewed": [
    "task",
    "real_trajectory",
    "final_patch_or_workspace",
    "hidden_tests",
    "grader_or_verifier",
    "failure_and_score_details"
  ],
  "evidence_refs": [
    {
      "kind": "<trace-log-artifact-report-or-other>",
      "trace_id": "<opaque-id-or-null>",
      "span_id": "<opaque-id-or-null>",
      "env": "<environment-token-or-null>",
      "artifact_ref": "<private-pointer-or-null>"
    }
  ],
  "insight": {
    "approach_summary": "<what-the-solver-tried>",
    "decisive_evidence": ["<specific-observation>"],
    "why_this_outcome": "<causal-explanation>",
    "expectedness": "<expected-surprising-mixed-or-unknown>",
    "baseline_treatment_difference": "<difference-or-not-yet-compared>",
    "loopx_implication": "<reusable-product-or-experiment-insight>",
    "next_probe": "<smallest-discriminating-next-step>"
  },
  "confidence": "<high-medium-or-low>",
  "reuse_boundary": "<diagnostic-only-heldout-generalization-or-declared-feedback>"
}
```

Keep the artifact and its raw evidence in private benchmark storage. Publish only a
redacted reusable conclusion. Do not feed case-specific hidden evidence into a
later scored solver unless the experiment explicitly declares that feedback loop;
use held-out cases before making a general product claim.

### Treatment continuation receipt

A qualified treatment startup and a countable score do not prove that the treatment
control remained active after startup. After the terminal analyst has reviewed the
authorized evidence, reduce only compact mechanism facts:

```bash
loopx benchmark treatment-continuation-receipt \
  --observation-json <compact-post-run-observation.json> \
  --format json
```

```json
{
  "schema_version": "benchmark_treatment_continuation_observation_v0",
  "treatment_applicable": true,
  "startup_state": "qualified",
  "observation_complete": true,
  "post_start_control_events": {
    "todo_transition_count": 1,
    "technical_replan_count": 0,
    "control_closeout_count": 1
  },
  "terminal_control_state": "settled",
  "precommit_validation_state": "observed"
}
```

The observation names startup state, whether the review is complete, counts of
post-start Todo transitions, technical replans, and control closeouts, terminal
control settlement, and whether pre-commit validation was observed. Count a Todo
transition only when it advances or revises task-facing technical work before the
result is fixed. Count a technical replan only when it changes that technical
course. Record terminal-only Todo settlement, replan bookkeeping, and final
closeout under `control_closeout_count`; those events are visible but do not prove
continued technical control. The observation contains no task text, trajectory
content, paths, run identity, verifier output, or score.

The receipt classifies the mechanism as `sustained`, `startup_only`, `unknown`, or
`not_applicable`. Here, `sustained` means at least one qualifying task-facing Todo
transition or technical replan was observed after qualified startup and before the
result was fixed. Terminal-only control never establishes `sustained`, even when
terminal settlement succeeds; the existing total and per-kind event counts still
record that closeout activity. Absence becomes `startup_only` only when the
authorized post-run observation is complete. This receipt is analysis-only: it
never changes score countability, integrity qualification, treatment fidelity, or
matched-pair eligibility.

## Study manifest, local upload simulation, and dashboard packet

Use `benchmark_study_manifest_v0` when a benchmark adapter needs to declare its
case set, arms, factors, native metric meanings, and pinned source revisions once.
The manifest describes the study; it does not score, launch, retry, or mutate a run.
A simple baseline/treatment study normally declares one two-level factor. A
factorized study declares each factor independently and assigns every arm to one
level of every factor.

Validate the public-safe manifest before producing upload records:

```bash
loopx benchmark study-validate \
  --manifest-json <study-manifest.json> \
  --format json
```

An adapter can then wrap one allowlisted record at a time: the manifest, an existing
`benchmark_experiment_board_row_v0`, a redacted
`benchmark_case_insight_projection_v0`, or an existing
`benchmark_runtime_observation_v0`.

```bash
loopx benchmark upload-envelope \
  --payload-json <public-safe-record.json> \
  --record-kind experiment_board_row \
  --producer-id <adapter-id> \
  --producer-version <adapter-version> \
  --benchmark-id <benchmark-id> \
  --study-id <study-id> \
  --idempotency-key <stable-key> \
  --observed-at <iso-8601-timestamp> \
  --source-revision <adapter-revision> \
  --format json > <upload-envelope.json>
```

Before implementing a remote provider, exercise the transport lifecycle against
the built-in local simulation. Preview is the default and performs no write;
`--execute` appends to the explicitly named JSONL store under a file lock. Neither
mode performs network access or grants upload/submission authority.

```bash
loopx benchmark upload-local \
  --envelope-json <upload-envelope.json> \
  --store <simulation.jsonl> \
  --format json

loopx benchmark upload-local \
  --envelope-json <upload-envelope.json> \
  --store <simulation.jsonl> \
  --execute --format json

loopx benchmark upload-readback \
  --store <simulation.jsonl> \
  --record-id <record-id> \
  --format json
```

Retries using the same producer, benchmark, study, and idempotency key are accepted
only when the payload digest is unchanged. A corrected record uses a new idempotency
key and explicitly names `--supersedes-record-id`; experiment-board corrections must
also obey existing legal run-state transitions. A study manifest is immutable
comparison intent: change its design under a new `study_id` instead of superseding it.
Supersession also stays within the producer that authored the prior record.

### Upload a terminal case insight

`benchmark_case_insight_projection_v0` is the public-safe child record for one
exact run. Upload the run's terminal `benchmark_experiment_board_row_v0` first;
its `insight.status` must be `complete`, and the projection's `case_id`, `run_id`,
and `outcome_status` must match that active terminal row. The run identity already
resolves its arm, so the insight cannot invent a second arm binding. Because the
projection has no metric, countability, integrity, or treatment-fidelity fields,
accepting it cannot change the run's score authority.

This is an intentionally strict upload-ordering rule: orphan, pre-terminal, and
outcome-mismatched insight records that older local simulations accepted are now
rejected. Re-upload the terminal run row before uploading its insight; no existing
score or experiment-board authority is rewritten.

```json
{
  "schema_version": "benchmark_case_insight_projection_v0",
  "benchmark_id": "example-benchmark@1",
  "study_id": "example-study-v1",
  "case_id": "case-1",
  "run_id": "treatment-case-1-r1",
  "outcome_status": "completed",
  "failure_class": "none",
  "causal_summary": "The implementation satisfied the declared contract after an independent boundary check.",
  "expectedness": "expected",
  "implication": "Retain the independent boundary check in this arm.",
  "next_probe": "Repeat on a different public case family.",
  "confidence": "high",
  "evidence_refs": ["public-receipt:abc123"],
  "privacy_classification": "public_safe",
  "producer_redaction_attested": true
}
```

Wrap it with the same `benchmark upload-envelope` command above using
`--record-kind case_insight_projection`, then preview, execute, and read it back
through the same local provider flow. The private analyst may use task text,
trajectory, final workspace, hidden evaluation, and verifier details only after
the run is terminal; those sources are reduced into the bounded fields and
public-safe evidence handles above and are never uploaded themselves.

Finally, derive a read-only `benchmark_study_dashboard_v0` packet. It exposes
campaign, arm, case, and run projections with explicit denominators and provisional
coverage, while delegating scores and matched comparisons to the experiment board.
For a qualified Goal/LoopX four-arm study, pass the compact four-arm contract to
reuse the existing factorial reducer.

```bash
loopx benchmark study-dashboard \
  --manifest-json <study-manifest.json> \
  --store <simulation.jsonl> \
  [--four-arm-contract-json <compact-four-arm-contract.json>] \
  --format json
```

Adapters preserve their benchmark's native metric names, units, directions, and
totals. Core fields are not software-engineering specific, so the same flow applies
to two-arm, four-arm, and other declared benchmark studies. Raw tasks, trajectories,
logs, hidden evaluator material, verifier tails, credentials, and local paths have
no upload schema slot; producers must reduce post-run analysis to the redacted
insight contract.

### Exploratory behavior findings

Share a selected pattern with settings, sample selection, observations, evidence
digests, limitations and counterexamples using `--record-kind behavior_finding`.
It requires no complete study or run-row upload and has no score authority.
`loopx benchmark behavior-report` projects active findings through the existing
local provider. See [the bilingual contract and workflow](../../../docs/reference/benchmark-behavior-findings.md)
for the required fields, evidence boundary and revision commands.

## Related commands

```bash
loopx benchmark classify-artifacts <paths...> --format json
loopx benchmark candidate-source-boundary <paths...> --require-clean --format json
```

All commands are local and no-upload by default. `benchmark-toolkit` grants no model,
Docker, runner, upload, submission, publication, or production authority.

The active benchmark research program and current public-safe practice live under
[`benchmark/`](https://github.com/loopx-project/loopx/blob/main/benchmark/README.md). Retired implementations, superseded runners, and dated research
packets are retained under [`deprecate/benchmark-legacy/`](https://github.com/loopx-project/loopx/blob/main/deprecate/benchmark-legacy/README.md)
for source archaeology only.
Immutable experiment snapshots follow the canonical
[archive placement rules](../../../benchmark/README.md#archive-placement),
which permit explicitly identified, inert snapshots in `benchmark/`.
