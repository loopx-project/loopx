# Long-Horizon Agent Benchmark Research

This topic folder owns LoopX research on public long-horizon agent
benchmarks, external leaderboard strategy, operator-simulator study design, and
paper-oriented experiment planning.

The canonical claim ladder, experiment contract, benchmark portfolio, and
mechanism-research boundary live in the
[Long-Horizon Harness Benchmark and Research Program v0](../../../../../docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md)
RFC ([中文版](../../../../../docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.zh-CN.md)).
This folder contains executions and research artifacts under that contract; it
does not define a second benchmark authority model.

Keep this folder focused on research artifacts:

- benchmark and paper dossiers;
- runner setup notes and legality/protocol reviews;
- official leaderboard versus passive-control-plane versus assisted-simulator
  experiment plans;
- result summaries, failure taxonomies, and publication-readiness notes.

Do not implement LoopX product capability here. Foundational capability
work still belongs in the existing code, examples, and contract documents:

- CLI, quota, status, history, registry, and dashboard behavior belongs under
  `loopx/`, `scripts/`, and the existing contract docs.
- Deterministic smoke or regression coverage belongs under `examples/`.
- General LoopX control-plane specs belong under top-level `docs/`.
- This folder may link to those artifacts, but should not become a parallel
  implementation or a second product-spec tree.

## Current Artifacts

- `codex-cli-long-run-benchmark-design.md`: public-safe design contract for the
  deterministic Codex CLI long-run benchmark fixture, result schema, scoring
  split, and LoopX comparison protocol.
- `codex-cli-long-run-regression.md`: regression spec for the deterministic
  long-run worker shim, Goal Tick output protocol, and opt-in real Codex CLI
  worker contract.
- `benchmark-program-current-state-handoff-v0.md`: compact current-state
  runbook for fresh workers, summarizing the active benchmark evidence layer,
  docs-only projection decisions, allowed next transitions, and stop
  conditions without adding hot-path projection keys.
- `roadmap.md`: benchmark selection, passive baseline, operator-simulator, and
  publication-readiness roadmap.
- `benchmark-candidate-scan-2026-06-12.md`: post-ALE-blocker benchmark scan
  that pauses Agents' Last Exam until a local/non-GCP route is documented,
  records the upstream Mainland-China/GCP issue, and ranks SWE-Marathon,
  AgentIssue-Bench, PerfBench, SWE-Bench Pro, MLE-bench, TheAgentCompany, and
  APEX-Agents by LoopX leverage before any fresh quota spend.
- `wildclawbench-dossier-20260617.md`: public-safe no-run WildClawBench route
  dossier. It records why harness comparison and side-effect auditing are a
  stronger next LoopX hypothesis source than another same-policy
  SkillsBench rerun, while blocking any official run until setup-readiness
  proves a local no-upload slice without unauthorized API/search/judge calls.
- `benchmark-rotation-product-mode-protocol-20260617.md`: updated
  Terminal-Bench / SkillsBench / ALE rotation protocol after ALE PR #8 added a
  Local Docker Linux-subset route. It defines the compact 4-row base/test
  matrix, shared score metrics, trace-retention contract, and current ALE
  readiness blocker without running Docker, reading task material, or claiming
  score evidence.
- `benchmark-split-control-remote-executor-v0.md`: shared route contract for
  running Docker-heavy benchmarks with Codex/LoopX/auth/model invocation
  kept local while the remote development host only provides Docker, runner
  dependencies, task-data staging, bounded execution, and compact result
  reduction. It is now retained as fallback/research evidence rather than the
  default benchmark route; future split-control experiments should move to an
  explicitly labeled experimental branch or research issue unless the cloud
  Codex route is blocked.
- `cloud-codex-benchmark-route-20260619.md`: route-decision note that makes an
  exclusive cloud ECS benchmark host the default path for Terminal-Bench,
  SkillsBench, and ALE. It preserves the split-control work as fallback
  research, but moves near-term execution back to clean upstream-close
  benchmark checkouts with Codex CLI and containers on the same host.
- `benchmark-active-case-status-20260620.md`: current cloud-host case status
  queue after the agent-runtime refactor and app-server Goal pivot. It tracks
  running, superseded, and next-rerun case state without copying raw task text,
  logs, trajectories, verifier output, credentials, or remote host paths.
- `benchmark-goal-rollout-debug-20260620.md`: public-safe rollout/debug layer
  for the first cloud app-server Goal closeouts. It links compact benchmark
  outcomes, GH todo/status transitions, case routes, failure attribution, and
  next debug questions without copying raw task text, logs, trajectories,
  verifier output, credentials, or private paths.
- `benchmark-closeout-failure-attribution-20260620.md`: public-safe
  attribution layer for the Terminal-Bench `build-cython-ext`, SWE-Marathon
  `find-network-alignments`, and two SkillsBench closeouts. It records what a
  compact zero score does and does not prove, splits native app-server Goal
  failures from non-native ACP blind-loop no-uplift, and makes the next
  reducer/worker obligation explicit before another rotation.
- `benchmark-route-transition-retrospective-20260619.md`: retrospective and
  runbook for the local-Codex split-control to cloud-host Codex route pivot. It
  records why split-control was hard, which contracts/reducers remain useful,
  how to keep benchmark branches upstream-close, and when to retire bridge
  layers from the mainline path.
- `benchmark-priority-review-20260614.md`: corrected priority review using
  Codex CLI goal mode as the baseline. It separates direct low Codex baselines,
  selected hard-subset failures, and adjacent-agent-low benchmarks that still
  need a clean Codex goal-mode baseline mining pass.
- `benchmark-execution-route-selection-v0.md`: no-run route-selection packet
  after the SWE-Bench Pro and TheAgentCompany gate packets. It selects the
  SWE-Bench Pro one-instance private pilot as the first real e2e route after a
  future execution-scope approval, defers TheAgentCompany until after that
  pilot or a stall, and keeps private task material, Docker, Codex/model calls,
  credentials, raw artifacts, uploads, submit, and public ranking paths
  disabled.
- `benchmark-e2e-owner-decision-v0.md`: owner decision packet that resolves
  the execution-scope gate for one bounded SWE-Bench Pro e2e pilot. It permits
  local Docker and the shared remote GPU Route B helper surface, delegates the
  credential strategy to Codex, keeps Codex auth local-only, and forbids remote
  auth sync, uploads, submits, public ranking paths, raw private material, and
  benchmark score claims until compact local evidence exists.
- `swe-marathon-setup-readiness-v0.md`: source-pinned SWE-Marathon
  setup-readiness scan. It identifies the required Harbor fork, Codex runner
  surface, CUA/GPU deferrals, CPU shell-only pilot candidates, and the next
  no-task Harbor CLI preflight before any scored run.
- `swe-marathon-full-suite-status-20260622.md`: public-safe 20-case
  SWE-Marathon sweep table. It combines upstream task manifest metadata with
  the compact public run ledger to classify resource lanes, existing evidence,
  and the next CPU/no-CUA case without copying task bodies or raw run evidence.
- `swe-marathon-rust-c-compiler-launch-packet-v0.md`: no-execution launch
  packet for the first SWE-Marathon CPU shell-only pilot candidate. It records
  compact `rust-c-compiler` routing metadata, no-upload command boundaries,
  artifact-reduction rules, and Docker/capacity stop gates without reading
  task bodies, tests, solution files, trajectories, screenshots, credentials,
  hidden refs, or starting a benchmark run.
- `swe-marathon-vliw-kernel-optimization-launch-packet-v0.md`: no-execution
  launch packet for the next fresh SWE-Marathon CPU/no-CUA case. It records
  the matched baseline/treatment launch shape, case-local LoopX active-todo
  completion rule, long-timeout envelope, no-upload boundary, and phase-counter
  evidence requirements before any scored run.
- `swe-marathon-rust-c-compiler-provider-capacity-preflight-v0.md`:
  no-execution Docker/Colima provider-capacity preflight for the
  `rust-c-compiler` packet. It finds the local benchmark Colima profile
  reachable but under-provisioned at 4 CPUs / 8 GiB memory, below the task's
  16 GiB memory requirement, with host disk headroom also tight before any
  task image build/start.
- `frontier-swe-setup-readiness-v0.md`: setup-readiness scan that adds
  FrontierSWE to the candidate set as a P1 frontier-stress lane. It records the
  official repo, public Codex leaderboard surface, 20-hour task framing,
  current runner/no-upload unknowns, and the required no-execution launch
  packet before any task container, model call, upload, or leaderboard path.
- `frontier-swe-no-execution-launch-packet-v0.md`: no-execution ECS launch
  packet for FrontierSWE. It keeps the route blocked before scored execution
  until a benchmark-host source commit, task-inventory summary, runner/reducer
  shape, and no-upload command boundary are materialized without reading task
  bodies, raw logs, trajectories, verifier output, credentials, private paths,
  uploads, or leaderboard paths.
- `remote-gpu-benchmark-route-v0.md`: credential-isolated route packet for
  evaluating the user's `to` SSH path to a shared remote GPU development host.
  It keeps Codex auth/session material local by default, uses an isolated
  remote workspace and public-source/rsync sync plan, and gates any real
  benchmark run on a no-auth provider-readiness probe plus an explicit future
  credential decision.
- `remote-gpu-noauth-readiness-probe-plan-v0.md`: no-auth probe plan for the
  `to` route. It defines SSH options, private remote workspace shape, redacted
  readiness commands, compact output fields, sync dry-run exclusions, isolated
  LoopX install env vars, and stop rules before any remote sync,
  install, Docker task, Codex/model call, credential transfer, upload, or
  benchmark execution.
- `remote-gpu-noauth-provider-probe-v0.md`: first redacted execution of the
  `to` route provider probe. It confirms SSH connectivity, private workspace
  permissions, Linux, 180 CPUs, 440 GiB memory, 153 GiB workspace headroom,
  Docker 24.0.9, two visible NVIDIA GPUs with about 191 GiB total memory,
  Python/git/rsync availability, and no Codex auth sync or benchmark start.
  It leaves the real e2e decision to two route proofs: local driver plus remote
  Docker provider, and local driver plus SSH command adapter.
- `remote-gpu-route-ab-proof-v0.md`: first A/B route proof after the provider
  probe. Route A verifies local Docker-over-SSH provider wiring with
  `DOCKER_API_VERSION=1.43` against the remote daemon, but stops because Harbor
  lacks a safe no-run task preflight. Route B verifies the SSH command adapter
  and a redacted rsync dry-run manifest, making it the more controllable next
  route before any real sync, install, task container, Codex/model call, or
  upload.
- `remote-gpu-route-b-sync-install-plan-v0.md`: one-time Route B real-sync and
  isolated-install plan. It preserves the redacted rsync excludes, defines
  compact-only real-sync evidence, adds a remote post-sync absence scan for
  forbidden private paths, and installs LoopX only under the private
  remote workspace with `CODEX_HOME` pointed at an empty isolated directory.
- `remote-gpu-route-b-sync-install-proof-v0.md`: first controlled Route B
  real-sync and isolated-install proof. It records compact-only sync counts,
  clean manifest and remote forbidden-path scans, isolated LoopX
  install success, passing same-environment remote doctor, and no Codex auth
  sync or benchmark start.
- `remote-gpu-route-b-runner-plumbing-preflight-v0.md`: first Route B
  no-score/no-upload runner plumbing preflight. It uses a temporary remote
  registry/runtime, records the CLI rule that bridge-contract and preflight
  guard checks must be split, and verifies both split dry-runs without Harbor,
  Terminal-Bench, Docker task start, Codex/model invocation, upload, or auth
  sync.
- `agentissue-bench-setup-readiness-v0.md`: public-safe setup-readiness scan
  for AgentIssue-Bench. It pins the official repository source, verifies the
  50-primary-tag Docker shape, updates the difficulty evidence to the current
  `0.67%` to `4.67%` correct-resolution range, and blocks immediate shared-host
  execution until a one-tag no-run launch packet replaces unsafe helper-script
  behavior such as all-image cleanup, all-tag loops, credential prompts, and
  pull-and-run helpers.
- `agentissue-bench-one-tag-launch-packet-v0.md`: no-run first-tag launch
  packet for AgentIssue-Bench. It selects `lagent_239` from public script
  constants plus Docker Hub metadata, keeps Codex auth local through a
  patch-file wrapper contract, and gates any real pilot on manifest-only
  preflight plus a task-context-source route before Docker pull/run or model
  work.
- `agentissue-bench-lagent239-manifest-context-gate-v0.md`: manifest-only
  preflight and task-context-source gate for the selected AgentIssue-Bench
  first tag. It verifies registry-visible `linux/amd64` image metadata without
  pulling/running Docker, confirms only existence of the probable public issue
  route, and blocks execution until public issue/container/task context access
  is explicitly approved.
- `agentissue-bench-lagent239-public-context-packet-v0.md`: owner-delegated
  public issue/task-context packet for `lagent_239`. It reads the selected
  public GitHub issue through public API access, records only hash/count/time
  metadata, emits a no-run `benchmark_run_v0` projection, and keeps raw issue
  text, patches, tests, Docker/model execution, credentials, uploads, submit,
  public ranking paths, trajectories, and screenshots absent from public
  artifacts.
- `agentissue-bench-lagent239-patch-producer-packet-v0.md`: no-run local
  Codex patch-producer packet for `lagent_239`. It defines trusted-local Codex
  CLI boundaries, code-source sync assumptions, future patch output path
  `Patches/lagent_239/attempt.patch`, compact `benchmark_run_v0` /
  `benchmark_result_v0` writeback fields, and stop gates before Codex/model
  invocation, patch generation/evaluation, Docker pull/run, uploads, submit,
  public ranking paths, credentials, raw issue text, source diffs, patches,
  tests, trajectories, and screenshots.
- `agentissue-bench-lagent239-code-source-sync-plan-v0.md`: no-run public
  `InternLM/lagent` source-sync plan for `lagent_239`. It records only public
  commit/tree metadata, root tree counts, and redacted file-selection rules,
  emits a compact `benchmark_run_v0` no-run projection, and keeps repository
  checkout, file contents, source diffs, Codex/model invocation, patch
  generation/evaluation, Docker pull/run, uploads, submit, public ranking
  paths, credentials, trajectories, and screenshots absent.
- `agentissue-bench-lagent239-local-execution-gate-packet-v0.md`: no-run local
  execution gate packet for `lagent_239`. It separates trusted-local Codex
  patch generation, public source checkout/cleanup, single-tag Docker
  evaluation, compact result reduction, and no-upload/no-submit/no-public
  ranking boundaries before any real Codex/model invocation, patch generation,
  Docker pull/run, patch evaluation, credentials, raw issue text, source diffs,
  patch content, tests, trajectories, or screenshots.
- `agentissue-bench-lagent239-bridge-preflight-v0.md`: bridge preflight for
  the same selected tag. It verifies trusted-local Codex CLI presence, Docker
  metadata reachability, registry-visible `linux/amd64` manifest routing, and
  private patch staging shape while still stopping before source checkout,
  Codex/model invocation, patch generation, Docker pull/run, evaluation,
  uploads, submit, public ranking, credentials, raw issue text, diffs, tests,
  trajectories, or screenshots.
- `agentissue-bench-lagent239-controlled-pilot-result-v0.md`: first real
  no-upload single-tag AgentIssue-Bench pilot result. It narrows the active
  benchmark program to only `agentissue-bench` / `lagent_239`, records a
  trusted-local Codex patch attempt plus selected Docker image evaluation, and
  reduces the unresolved result to compact hash/count/status evidence. The
  failure attribution is source alignment: the patch was generated against
  current public `InternLM/lagent` HEAD, while the selected container tests its
  own buggy snapshot, so the next step stays on the same tag and aligns patch
  generation to the benchmark buggy source before any benchmark broadening.
- `agentissue-bench-codex-cli-runner-contract-v0.md`: runner contract that
  replaces ad hoc agent execution with a Codex CLI benchmark flow for
  AgentIssue-Bench. It records that no official Codex CLI AgentIssue-Bench
  metric was found, freezes other benchmark candidates, and defines the
  correct runner sequence: fetch public issue context, pull one selected image,
  extract the container's buggy source, initialize a local git baseline, run
  host-local `codex exec --ephemeral` in that source tree, write
  `Patches/lagent_239/attempt.patch`, evaluate the same single tag, and reduce
  only compact hash/count/status evidence.
- `agentissue-bench-codex-cli-runner-flow-plan-v0.md`: no-execution command
  flow plan for the same selected tag. It turns the runner contract into
  deterministic host-Codex and single-tag Docker command shapes with absolute
  private-job-root placeholders, explicit phase ordering, compact reducer
  fields, and stop rules before Codex/model execution, Docker starts, auth
  sync, all-tag helpers, uploads, submits, public ranking paths, raw artifacts,
  fixed/oracle material, or current-HEAD patch generation.
- `agentissue-bench-codex-cli-runner-dry-run-wrapper-v0.md`: CLI
  materialization of that flow as
  `loopx benchmark agentissue-codex-runner-flow --tag lagent_239`.
  It defaults to dry-run and `--execute` appends only compact no-run
  `benchmark_run_v0` readiness, while still avoiding Codex/model execution,
  Docker starts, auth sync, patch generation/evaluation, uploads, submits,
  public ranking paths, raw artifacts, fixed/oracle material, or current-HEAD
  patch generation.
- `agentissue-bench-codex-cli-runner-synthetic-staging-v0.md`: opt-in
  `--synthetic-staging-root` fixture for the same CLI wrapper. It creates only
  synthetic private-job-root placeholders for `context/prompt.md`, extracted
  source, `Patches/lagent_239/attempt.patch` parent placement,
  `runner-flow-plan.public.json`, and `benchmark_run.compact.json`, while still
  avoiding real AgentIssue task material, Codex/model execution, Docker starts,
  auth sync, patch generation/evaluation, uploads, submits, public ranking
  paths, raw artifacts, fixed/oracle material, or current-HEAD patch
  generation.
- `agentissue-bench-codex-cli-runner-execution-gate-v0.md`: guarded
  no-execute `--execution-gate-root` packet for `lagent_239`. It materializes
  the synthetic staging files plus `execution-gate.public.json`, rendering the
  selected-container source extraction, private git baseline, host-local
  `codex exec --ephemeral`, patch export, and selected-tag eval command shapes
  while keeping real AgentIssue task material, Codex/model execution, Docker
  pull/start, auth sync, patch generation/evaluation, uploads, submits, public
  ranking paths, raw artifacts, fixed/oracle material, and current-HEAD patch
  generation behind a future run-specific gate.
- `agentissue-bench-codex-cli-runner-first-run-handoff-v0.md`: no-execute
  `--first-run-handoff-root` packet for `lagent_239`. It materializes the
  execution gate plus `first-run-handoff.public.json` and
  `first-run-handoff.md`, naming the exact command shape, private artifact
  boundary, expected compact outputs, budget/auth boundary, and safety
  checklist for a later operator-triggered e2e run without running Codex,
  Docker, source extraction, patch generation/evaluation, uploads, submits, or
  public ranking paths.
- `agentissue-bench-codex-cli-runner-workflow-check-v0.md`: no-execute
  `--workflow-check-root` packet for `lagent_239`. It materializes the
  first-run handoff plus `workflow-check.public.json`, checking phase order,
  host-Codex auth isolation, no worker network/Docker access, patch-source
  provenance, selected-tag eval boundaries, and compact/public artifact
  allowlists before any later operator-triggered e2e run.
- `agentissue-bench-codex-cli-runner-run-gate-v0.md`: no-execute
  `--run-gate-root` packet for `lagent_239`. It materializes the workflow
  check plus `run-specific-gate.public.json` and `run-specific-gate.md`,
  separating gates already covered by public no-run packets from the remaining
  real-run blockers: private job root selection, explicit real-run trigger,
  selected-container source extraction, private git baseline, and host-local
  Codex execution from the extracted buggy source.
- `agentissue-bench-codex-cli-runner-target-handoff-v0.md`: no-execute
  `--target-runner-handoff-root` packet for `lagent_239`. It materializes the
  run-specific gate plus `target-runner-handoff.public.json` and
  `target-runner-handoff.md`, turning the gate packet into a compact
  target-runner checklist for a separate benchmark execution thread while
  keeping the meta heartbeat no-execute/no-upload.
- `agentissue-bench-codex-cli-runner-real-result-reducer-v0.md`: compact-only
  `--real-result-root` reducer for an already completed private `lagent_239`
  real run. It reads only `benchmark_run.compact.json` and
  `benchmark_result.compact.json`, materializes `real-result.public.json`, and
  appends a compact control-plane `benchmark_run_v0` event without invoking
  Codex, Docker, model APIs, uploads, submits, public ranking paths, raw
  artifacts, patch content, credentials, or absolute private paths.
- `agentissue-bench-codex-cli-runner-private-script-v0.md`: no-execute
  `--private-runner-root` materializer for the repeatable trusted-local
  `lagent_239` runner script. It creates `run-lagent239.private.sh`,
  `private-runner.public.json`, and a compact no-run event that preserves the
  source-extraction, host-Codex, patch-export, selected-container eval, compact
  evidence, and real-result reducer phases without invoking Codex, Docker,
  model APIs, uploads, submits, public ranking paths, raw logs, patch content,
  credentials, or absolute private paths from the generator.
- `agentissue-bench-codex-cli-runner-pr-ready-packet-v0.md`: public-safe
  consolidation packet for the full `lagent_239` runner-flow chain. It ties
  together the contract, flow plan, dry-run wrapper, synthetic staging,
  execution gate, first-run handoff, workflow check, run-specific gate,
  target-runner handoff, real-result reducer, private-runner script, and the
  matching smokes into one reviewable route while preserving
  no-run/no-upload/no-submit/no-public-ranking boundaries.
- `agentissue-bench-codex-cli-runner-publication-change-set-v0.md`: staging
  and review packet for publishing only the AgentIssue runner-flow change set.
  It lists the docs and smokes that should move together, marks
  `loopx/benchmark.py`, `loopx/cli.py`, and this README as mixed
  tracked files that need careful staging, and excludes unrelated benchmark
  lanes, runtime state, credentials, raw artifacts, uploads, submits, and
  public ranking paths.
- `perfbench-setup-readiness-v0.md`: public-safe setup-readiness scan for
  PerfBench. It records the low public success rates and strong LoopX
  fit for .NET performance-bug validation, but blocks local/Docker setup today
  because the advertised `glGarg/PerfBench` repository is visible in browser
  metadata yet unavailable through git, raw GitHub, and GitHub API transport.
- `perfbench-alternate-source-selection-v0.md`: no-run alternate-source
  selection attempt for PerfBench. It confirms the advertised repository is
  still browser/search visible but unavailable through git, raw GitHub, and
  GitHub API transport, finds no alternate official public repository through
  public repository search, selects no alternate source, and keeps task rows,
  Docker/.NET execution, Codex/model invocation, uploads, submit, public
  ranking paths, credentials, trajectories, and screenshots untouched.
- `swe-bench-pro-setup-readiness-v0.md`: public-safe setup-readiness scan for
  SWE-Bench Pro. It records the official source, dataset, Docker image
  metadata, local-Docker beta route, current difficulty evidence, and a
  third-party direct OpenAI Codex result, while gating task-row access, Docker
  pulls/runs, model use, and leaderboard claims behind a separate explicit
  approval.
- `swe-bench-pro-runner-source-preflight-v0.md`: sparse no-task/no-Docker
  runner-source preflight for SWE-Bench Pro. It proves a root-anchored
  `--no-cone` sparse checkout can inspect the official evaluator and helper
  scripts without `run_scripts`, trajectories, task rows, gold/test patches,
  Docker images, model calls, uploads, or credentials, and records the
  evaluation CLI inputs that gate any real pilot.
- `swe-bench-pro-selected-row-compaction-v0.md`: owner-delegated public
  selected-row compaction for one SWE-Bench Pro instance. It records compact
  row metadata, field lengths, and hashes for the selected public row while
  keeping raw problem text, gold patches, test patches, test lists,
  requirements, setup commands, Docker execution, Codex/model invocation,
  uploads, submit, public ranking paths, credentials, trajectories, and
  screenshots absent.
- `swe-bench-pro-one-instance-launch-packet-v0.md`: no-run launch packet for
  the selected SWE-Bench Pro instance. It records public Docker Hub image
  metadata, local Docker metadata-only provider facts, platform mismatch risk,
  required evaluator input classes, and a compact no-run `benchmark_run_v0`
  projection while stopping before raw task material, image pull/run,
  Codex/model invocation, patch generation/evaluation, upload, submit, public
  ranking paths, credentials, trajectories, and screenshots.
- `swe-bench-pro-one-instance-execution-gate-packet-v0.md`: no-run execution
  gate packet for the selected SWE-Bench Pro instance. It enumerates private
  sample reduction, trusted-local patch production, selected image/container
  boundaries, explicit `linux/amd64` handling, official evaluator gates, and
  compact result reduction while keeping real execution, raw task material,
  patch content, Docker actions, Codex/model invocation, upload, submit,
  public ranking paths, credentials, trajectories, and screenshots disabled.
- `swe-bench-pro-prelaunch-blocker-v0.md`: post-approval compact blocker
  evidence for the selected SWE-Bench Pro pilot. It confirms host Codex CLI
  availability and Docker reachability, but blocks launch because private
  sample, attempt patch, evaluator scripts, and a LoopX launch wrapper
  are not ready, while the current local provider remains `aarch64` with tight
  CPU/memory/disk headroom for the selected `linux/amd64` image.
- `swe-bench-pro-launch-wrapper-contract-v0.md`: public-safe wrapper/preflight
  contract for retrying the selected SWE-Bench Pro pilot. It defines the
  redacted descriptors required for private sample, attempt patch, evaluator
  scripts, provider choice, no-auth Route B helper use, and compact result
  reduction while keeping raw task material, patch content, credentials, local
  paths, trajectories, screenshots, uploads, submits, and public ranking paths
  out of public artifacts.
- `swe-bench-pro-route-b-provider-descriptor-v0.md`: public-safe provider
  descriptor for the selected SWE-Bench Pro pilot. It consumes the prior
  redacted Route B provider, sync/install, and runner-plumbing proofs to select
  `remote_gpu_route_b_noauth_helper` for no-auth helper use, while leaving the
  pilot blocked on private sample, attempt patch, evaluator scripts, and final
  image/container preflight descriptors.
- `swe-bench-pro-private-descriptor-blocker-v0.md`: compact fallback evidence
  for the remaining selected SWE-Bench Pro launch gate. It records a
  filename-only descriptor probe, confirms launch-wrapper and Route B provider
  readiness, and blocks retrying the pilot until private sample, attempt patch,
  and evaluator scripts descriptors can be staged through a private-only
  surface.
- `mle-bench-setup-readiness-v0.md`: public-safe setup-readiness scan for
  MLE-bench. It records healthy source transport, current public leaderboard
  evidence including a `gpt-5-codex` agent row, and the real-run gates:
  Kaggle credentials, Git LFS, Docker, 24h/GPU-scale resources, 158 GB Lite
  data, and a temporarily closed public leaderboard submission process.
- `mle-bench-source-preflight-v0.md`: no-LFS/no-data source preflight for
  MLE-bench. It proves a root-anchored sparse checkout can inspect the package
  and CLI source while excluding `runs`, competition data, LFS-managed files,
  Kaggle credentials, Docker actions, model calls, grading, uploads, and raw
  run reports; it also records that bare CLI help is blocked by eager
  dependency imports unless an isolated dependency environment is prepared.
- `theagentcompany-setup-readiness-v0.md`: public-safe setup-readiness scan for
  TheAgentCompany. It records healthy official source transport, the
  company-like multi-service Docker setup, the 175-task image surface, current
  paper difficulty evidence, lack of a direct Codex CLI score, and the real-run
  gates around Docker service bootstrap, root/OpenHands evaluation,
  credential-bearing LLM config, task instruction access, trajectories, and
  screenshots.
- `theagentcompany-source-preflight-v0.md`: sparse no-task source preflight for
  TheAgentCompany. It proves a root-anchored `--no-cone` checkout can inspect
  official runner/docs surfaces while excluding `workspaces/tasks`, task
  instructions, evaluator/checkpoint/scenario files, configs, outputs, Docker
  actions, model calls, uploads, and raw artifacts; it also records why the
  official all-task OpenHands script is not a safe first LoopX runner
  and sketches the gated host-Codex single-task wrapper shape.
- `theagentcompany-single-task-host-codex-gate-v0.md`: no-run single-task
  host-Codex gate packet for TheAgentCompany. It refreshes public source
  metadata, records aggregate task counts only, defines service-stack,
  single-task selection, local-Codex worker, private artifact, and compact
  reducer gates, and emits no-run `benchmark_run_v0` /
  `benchmark_result_v0` projections while keeping task material, Docker,
  Codex/model calls, credentials, trajectories, screenshots, uploads, submit,
  and public ranking paths disabled.
- `apex-agents-setup-readiness-v0.md`: public-safe setup-readiness scan for
  APEX-Agents. It records the official paper, Mercor leaderboard, gated
  Hugging Face dataset card, Archipelago runner source, current difficulty
  evidence, lack of a direct host-Codex CLI score, and the real-run gates
  around gated dataset access, task/rubric/gold/world material, Docker
  Compose, LLM/Codex credentials, grading, trajectories, snapshots, and
  leaderboard boundaries.
- `apex-agents-source-preflight-v0.md`: sparse no-task source preflight for
  APEX-Agents / Archipelago. It validates a narrow source checkout at
  `Mercor-Intelligence/archipelago@77a872577ce1b33cb71817465e844e52eadd3cbe`,
  confirms runner/environment/grading/config syntax and JSON boundaries,
  records that no built-in Codex CLI agent exists today, and sketches the
  safer host-Codex external adapter plus compact reducer route before any
  gated dataset, Docker, model, grading, trajectory, screenshot, or upload
  work.
- `apex-agents-codex-bridge-reducer-packet-v0.md`: no-run bridge/reducer
  packet for APEX-Agents. It selects the external host-Codex MCP adapter as
  the first route, defines provider/action-bridge/grading-reducer boundaries,
  maps compact public `benchmark_run_v0` and `benchmark_result_v0` fields,
  and makes the next safe implementation slice a deterministic fixture/smoke
  without dataset access, Docker, Codex/model calls, grading, trajectories,
  screenshots, credentials, or leaderboard interaction.
- `apex-agents-bridge-reducer-fixture-v0.md`: deterministic no-run fixture for
  the APEX-Agents host-Codex bridge/reducer route. It adds executable smoke
  coverage for reducing a redacted private bridge observation into public
  `benchmark_run_v0` and `benchmark_result_v0` events while preserving the
  official-score versus control-plane-score boundary and asserting that private
  task, grading, trajectory, credential, Docker, model, upload, submit, and
  public ranking surfaces remain absent.
- `paper-runner-dossier.md`: first evidence-backed ranking of benchmark papers,
  runner surfaces, Codex compatibility signals, and the next Terminal-Bench
  probe slice.
- `terminal-bench-probe-v0.md`: first public-safe runner-boundary probe for
  Terminal-Bench and Harbor, including Codex CLI integration surfaces, output
  files for passive LoopX ingestion, and the stop condition before paid
  or leaderboard execution.
- `benchmark-run-v0-ingest.md`: first passive `benchmark_run_v0` ingestion
  contract for Harbor job outputs, with deterministic fixture coverage and no
  default Docker, model, cloud, or leaderboard execution.
- `passive-baseline-protocol-v0.md`: paired Codex CLI goal-mode baseline
  versus passive LoopX wrapper protocol, connecting local
  `benchmark_result_v0` comparison evidence to compact `benchmark_run_v0`
  history rows without operator simulation.
- `operator-simulator-overlay-v0.md`: assisted operator-simulator overlay
  protocol after the passive baseline, including active user injection,
  comparison modes, simulator matrix, visibility limits, intervention budget,
  failure taxonomy, and the `operator_simulator_run_v0` row shape.
- `active-user-assisted-pilot-v0.md`: deterministic active-user assisted pilot
  shape for a previously failed compact Terminal-Bench case, keeping proactive
  user interventions, no-oracle audits, frequency budgets, and official score
  separation in one public-safe fixture. The Terminal-Bench wrapper now also
  has a deterministic active-user observation fixture that proves a worker can
  observe one post-start simulator intervention without running the real
  benchmark or claiming an official score.
- `benchmark-experiment-report-template-v0.md`: paper-ready
  `benchmark_experiment_report_v0` template that keeps official scores,
  passive control-plane metrics, assisted operator-simulator ablations,
  overhead, failure taxonomy, reproducibility artifacts, claim boundaries, and
  negative results in separate report sections.
- `benchmark-report-chain-map-v0.md`: compact reviewer-facing chain map that
  ties `benchmark_run_v0`, `benchmark_result_v0`, `benchmark_comparison_v0`,
  `benchmark_comparison_decision_note_v0`,
  `benchmark_experiment_report_v0`,
  `benchmark_experiment_report_readiness_note_v0`, and
  `benchmark_experiment_report_replay_decision_v0` into one fixture/status
  handoff boundary.
- `benchmark-result-control-plane-score-v0.md`: minimal
  `control_plane_score_core_v0` schema for `benchmark_result_v0`, separating
  official task score from restartability, stale-state avoidance, evidence
  discipline, boundary safety, writeback quality, gate compliance, failure
  attribution, and overhead.
- `mini-control-plane-repair-with-interrupt-v0.md`: deterministic recovery
  fixture slice for `mini_control_plane_repair_with_interrupt_v0`, proving
  worker interruption, stale latest-run avoidance, validation failure capture,
  human-gate resume recheck, and side-effect audit before any real benchmark
  runner path.
- `mini-control-plane-interrupt-comparison-summary-v0.md`: compact fixture-only
  comparison between the non-interrupt and interrupt mini control-plane repair
  modes, preserving official-score versus control-plane-score separation and
  claim boundaries before any status/review-packet projection.
- `mini-control-plane-interrupt-projection-decision-v0.md`: fixture-only
  decision to keep `benchmark_interrupt_comparison_summary_v0` research-only
  until a real consumer or passive benchmark run justifies status/review-packet
  projection.
- `terminal-bench-official-pilot-readiness-v0.md`: local-only readiness
  fixture for `terminal_bench_official_pilot_decision_packet_v0`, proving the
  `benchmark_result_v0` comparison shell and control-plane checklist before any
  real Terminal-Bench, Docker, Codex/model API, cloud, paid compute, or
  leaderboard path.
- `terminal-bench-no-submit-boundary-probe-v0.md`: local-only
  `runner_boundary_probe_v0` contract that records runner identity, planned
  command boundaries, submit eligibility, future event shape, and hard stop
  conditions without running Terminal-Bench, Harbor, Docker, Codex/model APIs,
  cloud sandboxes, paid compute, or leaderboard upload paths.
- `terminal-bench-no-submit-approval-packet-v0.md`: smallest
  `terminal_bench_no_submit_approval_packet_v0` operator packet for a future
  no-submit setup check, listing exact candidate commands, forbidden surfaces,
  public artifact shapes, side-effect budgets, stop conditions, and compact
  `benchmark_run_v0` / `benchmark_result_v0` ingestion rules without executing
  the runner path.
- `terminal-bench-no-submit-approval-packet-projection-decision-v0.md`:
  fixture-only decision to keep the no-submit approval packet research/docs-only
  until an agent consumer, approved no-submit setup check, passive wrapper, or
  repeated re-derivation justifies a compact hot-path projection.
- `terminal-bench-treatment-arm-taxonomy-v0.md`: no-run taxonomy correction
  separating `hardened_codex_baseline`, Codex runtime `codex_goal_mode`,
  true `codex_loopx`, and `passive_loopx_observer`. It records
  that `create_goal` / `update_goal` are Codex runtime goal-tool calls, not
  LoopX CLI calls, and requires future results to count
  `codex_runtime_goal_tool_calls`, `loopx_cli_calls`,
  `loopx_state_reads`, and `loopx_state_writes` separately.
- `public-safe-trajectory-summary-v0.md`: cross-benchmark compact trajectory
  summary contract. It exposes event/tool/action category counts, normalized
  LoopX CLI state-usage buckets, and protected-path edit signals without
  copying raw task text, prompts, verifier output, tool output, or trajectory
  bodies.
- `terminal-bench-loopx-access-packet-v0.md`: no-run access-packet and
  interaction-counter fixture for the true `codex_loopx` arm. It defines
  the public worker packet, keeps Codex runtime goal tools separate from Goal
  Harness CLI/state calls, and adds compact `benchmark_run_v0` counter
  projection before any fake-worker or real benchmark repeat.
- `terminal-bench-loopx-cli-bridge-contract-v0.md`: host-agent bridge
  contract for the future true `codex_loopx` arm. It maps
  `status`, `quota_should_run`, `todo_list`, `history`, `check`, and
  `append_benchmark_run` to executable LoopX CLI templates, with a smoke
  that runs those templates against a temporary registry. The same contract is
  now wired into `loopx benchmark run terminal-bench --mode
  codex-loopx --cli-bridge-contract`, producing compact runner-side
  bridge availability and `loopx_cli_calls.total=6` counters while
  keeping Terminal-Bench/Codex/model execution disabled.
- `terminal-bench-codex-loopx-active-cli-bridge-v0.md`: core
  `codex_loopx` treatment surface. It adds
  `loopx_cli_bridge_enabled=true` to `GoalHarnessManagedCodex`, injects
  worker-side `loopx ... status/quota/todo/history/check/append` command
  templates into the Codex instruction, and keeps worker in-case
  `loopx_cli_calls.total=6` separate from runner-side bridge probes. It
  also exposes the no-run `--preflight-guard --active-cli-bridge` route for the
  next private repeat and records a claim gate requiring nonzero worker-side
  LoopX CLI calls before any in-case use claim.
- `terminal-bench-codex-loopx-fake-worker-v0.md`: first executable
  fixture mode for the true `codex_loopx` arm:
  `loopx benchmark run terminal-bench --mode codex-loopx
  --fake-worker`. It appends a no-run/no-submit `benchmark_run_v0` event with
  nonzero LoopX CLI/state interaction counters, while keeping Codex
  runtime goal-tool calls separate and preserving no-uplift/no-leaderboard
  boundaries.
- `terminal-bench-codex-loopx-custom-agent-v0.md`: custom-agent prompt
  surface for the true `codex_loopx` arm. It wires the LoopX
  access packet into `GoalHarnessManagedCodex` through
  `loopx_mode=codex_loopx`, adds compact trace-audited counter
  extraction, and verifies the Harbor command preview before any real repeat.
- `terminal-bench-codex-loopx-preflight-guard-v0.md`: no-upload
  preflight guard for `loopx benchmark run terminal-bench --mode
  codex-loopx --preflight-guard`. It checks runner/Codex/local execution
  surfaces, access-packet prompt injection, trace-counter contract availability,
  and compact preflight/status projection without running Harbor tasks, Codex
  workers, model APIs, uploads, or leaderboard paths.
- `terminal-bench-runner-mode-contract-v0.md`: no-run mode contract for the
  future `loopx benchmark run terminal-bench ...` wrapper, separating
  parent runner control-plane behavior from per-case worker modes
  `codex-goal-mode` and `codex-loopx`. The contract treats Codex CLI
  goal mode as the true paired baseline for this experiment and
  `codex-loopx` as the core `goal-mode model + harness` pair; hardened
  or bare Codex evidence is calibration only. The current no-upload launch
  summary records the baseline as `/goal` slash-command surface with
  `access_packet=none` and no worker bridge.
- `benchmark-run-ledger-v0.md`: cross-benchmark run inventory schema. It makes
  each case attempt visible by benchmark, case, arm, compact score/failure class,
  and artifact reference, and defines the `--update-run-ledger` closeout path for
  automatic ledger updates after a case ingest.
- `benchmark-run-ledger.json` / `benchmark-run-ledger.md`: generated local
  case-run ledger for current benchmark work. JSON is the machine source;
  Markdown is the operator view.
- `benchmark-case-analysis-v0.md`: durable interpretation schema for benchmark
  case lessons. It sits above the run ledger and records what good, bad, and
  no-uplift cases teach us about treatment design and routing.
- `benchmark-case-analysis.json` / `benchmark-case-analysis.md`: current
  public-safe case-analysis asset. It records `multi-source-data-merger` as the
  positive-control uplift case and `debug-trl-grpo` as the negative-control
  regression case.
- `terminal-bench-official-hard-case-selection-v0.md`: no-run selection
  contract that moves the next evidence target from `terminal-bench-sample@2.0`
  to official `terminal-bench@2.0`, selects a three-case hard/long-horizon
  primary batch (`fix-code-vulnerability`, `modernize-scientific-stack`, and
  `llm-inference-batching-scheduler`), defines a backup queue, and preserves
  paired-run invariants for `codex-goal-mode` versus `codex-loopx`,
  claim boundaries, metrics, goal-mode invocation preflight, and stop
  conditions before any full 89-task run or leaderboard path.
- `terminal-bench-next-candidate-selection-20260614.md`: no-run P0 routing
  packet after the scheduler verifier-attribution blocker. It excludes
  treatment/same-task repeat for the blocked task, strict-preflights unused
  cached official task ids with the Codex goal-mode baseline shape, rejects
  `compile-compcert` after cross-history compact evidence shows it was already
  completed, and selects `install-windows-3.11` as the next private no-upload
  paired pilot.
- The `terminal-bench-next-candidate*` routing packets are historical
  public-safe decision records, not one smoke per document. Shared invariants
  are validated by
  `python3 examples/terminal-bench-candidate-routing-packets-smoke.py`.
- `terminal-bench-next-candidate-after-install-windows-20260614.md`:
  public-safe P0 routing packet after the `install-windows-3.11` paired compact
  result. It records the baseline verifier-attribution caveat, blocks
  same-task repeat or treatment claims, selects `financial-document-processor`
  as the next material-ready case, and preserves the no-upload paired protocol
  for Codex goal-mode versus `codex-loopx`.
- `terminal-bench-next-candidate-after-financial-document-processor-20260614.md`:
  public-safe P0 routing packet after the `financial-document-processor` paired
  compact result. It records the both-pass/no-uplift outcome, blocks same-task
  repeat or treatment claims, selects `multi-source-data-merger` as the next
  material-ready case, and preserves the no-upload paired protocol for Codex
  goal-mode versus `codex-loopx`.
- `terminal-bench-next-candidate-after-multi-source-data-merger-20260614.md`:
  public-safe P0 routing packet after the `multi-source-data-merger` paired
  compact result. It records the both-pass/no-uplift outcome, rejects exhausted
  backup candidates, selects `db-wal-recovery` as a protocol-calibration case
  for Codex goal-mode versus `codex-loopx`, and preserves the no-upload
  paired protocol.
- `terminal-bench-next-candidate-after-db-wal-recovery-20260614.md`:
  public-safe P0 routing packet after the `db-wal-recovery` paired compact
  review. It records the baseline-pass/treatment-fail outcome, blocks same-task
  repeat or treatment claims, selects `build-cython-ext` as the next
  material-ready setup-heavy case, and preserves the no-upload paired protocol
  for Codex goal-mode versus `codex-loopx`.
- `terminal-bench-next-candidate-after-build-cython-ext-20260614.md`:
  public-safe P0 routing packet after the `build-cython-ext` paired compact
  review. It records the both-pass/no-uplift outcome, excludes recently
  consumed material-ready candidates and old paired/blocker evidence, selects
  `pytorch-model-recovery` as a current-protocol calibration case with old
  bare-Codex failure signal, and preserves the no-upload paired protocol for
  Codex goal-mode versus `codex-loopx`.
- `terminal-bench-next-candidate-after-pytorch-env-setup-20260614.md`:
  public-safe P0 routing packet after the `pytorch-model-recovery`
  environment setup gate. It records the repeated pre-worker setup blocker,
  rejects a stale `db-wal-recovery` open todo because that case already has a
  current paired compact closeout, strict-preflights `make-doom-for-mips` and
  `regex-log`, selects `make-doom-for-mips` as the next material-ready
  system/build candidate, and preserves the no-upload paired protocol for Codex
  goal mode versus `codex-loopx`.
- `terminal-bench-next-candidate-after-regex-log-20260614.md`: public-safe P0
  routing packet after the restarted `regex-log` paired compact closeout. It
  runs the candidate-source-boundary guard before selection, uses a name-only
  cached task-id scan plus strict no-upload preflights, selects
  `large-scale-text-editing` as a fresh long-context editing candidate, and
  preserves the no-upload paired protocol for Codex goal mode versus
  `codex-loopx`.
- `terminal-bench-next-candidate-after-large-scale-text-editing-20260614.md`:
  public-safe P0 routing packet after the `large-scale-text-editing` compact
  paired closeout and follow-up `require_existing_codex` worker-startup
  blocker. It blocks immediate same-task repeat, reruns strict no-run
  preflights for the first fallback candidates, selects `git-multibranch` as
  the next material-ready case, and preserves the no-upload paired protocol
  for Codex goal mode versus `codex-loopx`.
- `agents-last-exam-triage-v0.md`: source-backed triage note adding Agents'
  Last Exam to the benchmark backlog. It records the Xiaohongshu discovery
  signal, verifies the arXiv and public GitHub surfaces, and keeps ALE behind
  the current Terminal-Bench paired pilot until an adapter dossier exists.
- `agents-last-exam-host-codex-cli-route-v0.md`: public-safe route gate for
  using the host machine's already authorized Codex CLI with an ALE CUA/MCP
  sandbox bridge. It avoids the upstream sandbox/OpenRouter-key-only path,
  records only compact auth-existence and bridge-readiness booleans, and
  requires a no-task host Codex CUA/MCP E2E preflight before task-level ALE
  execution.
- `agents-last-exam-local-docker-host-codex-route-v0.md`: source-backed
  non-GCP ALE route contract for local Docker/Colima plus host Codex CLI. It
  distinguishes the official supported GCP path from the scoped local Docker
  validation path, records the `demo/tool_smoke` score `1.0` canary as route
  evidence rather than uplift, and blocks formal tasks until CUA and task-data
  gates are ready.
- `agents-last-exam-local-source-readiness-smoke.py`: redacted ALE checkout
  source-lock gate. For formal candidates, use `--fetch-origin` plus
  `--require-upstream-current` so a newly released benchmark update makes the
  preflight fail closed instead of running from a stale local checkout.
- `agents-last-exam-local-launch-packet-smoke.py`: no-execution local launch
  packet gate covering source, runner, image, and experiment-spec readiness.
  It now carries the same upstream-current freshness check through the packet
  used by validation-run preflights.
- `agents-last-exam-task-material-readiness-smoke.py`: generic ALE task
  material gate covering local task directory, `task_card.json`, scripts,
  scorer scripts, and public selected-task-list membership without reading task
  card content, task bodies, script content, trajectories, screenshots,
  credentials, raw output, or recording local paths. It also covers
  `ale-baked-task-input-readiness`, `ale-baked-task-input-scan`, and the
  `--baked-task-input-readiness-json` handoff into
  `ale-task-material-readiness`, so formal local runs can prove or reject a
  `baked_in_sandbox` data route before model work.
- `agents-last-exam-candidate-task-data-scan-smoke.py`: selected-task-list
  scanner for ALE formal-candidate routing. It extracts only the compact
  `REQUIRES_TASK_DATA` boolean signal from task config lines and reports
  public task-id/count blockers, so local/no-upload runs can stop before
  repeating a task-data-substrate failure.
- `agents-last-exam-validation-run-gate-smoke.py`: compact ALE pre-run gate
  combining task-material readiness, host-Codex no-task E2E readiness, exact
  dry-run matrix readiness, optional launch packet readiness, and compact
  reducer readiness before allowing a local/no-upload validation run.
- `terminal-bench-cli-dry-run-fake-worker-v0.md`: public CLI skeleton for
  `loopx benchmark run terminal-bench`. The command defaults to dry-run,
  exposes `codex-goal-mode`, `codex-loopx`, `hardened-codex`, passive
  observation, and `loopx-managed-codex`, and can append only compact
  fixture `benchmark_run_v0` rows when `--execute` is passed. The current
  fake-worker path is allowed only for managed mode and records
  `loopx_managed_codex_fake_worker_wrapper` without invoking real
  Harbor, Terminal-Bench, Docker, Codex, model APIs, uploads, or leaderboard
  paths.
- `terminal-bench-managed-real-run-preflight-guard-v0.md`: no-run guard packet
  for the first managed LoopX Terminal-Bench case. It rechecks runner,
  local Docker/Colima, Codex CLI, auth-surface-name, no-upload, and artifact
  redaction boundaries, appends only a compact readiness `benchmark_run_v0`
  when executed, and stops before Harbor, Terminal-Bench, Codex worker,
  benchmark task container, model API, uploads, or leaderboard paths.
- `terminal-bench-managed-codex-custom-agent-v0.md`: first concrete Harbor
  custom-agent bridge for the core managed treatment, using
  `--agent-import-path loopx.terminal_bench_agent:GoalHarnessManagedCodex`
  to subclass Harbor's built-in Codex adapter, inject a minimal LoopX
  policy envelope, defer public-safe managed metadata until Codex post-run
  session ingestion, and preserve no-upload/private pilot boundaries while
  stopping before leaderboard, uplift, or paper-ready claims.
- `benchmark-history-reconstructability-v0.md`: restartability fixture proving
  compact benchmark run history can reconstruct official-score,
  control-plane-score, claim-boundary, readiness, authorization,
  replay-decision, next-run-mode, and stop-condition state without raw logs,
  private traces, local artifact paths, chat history, or extra hot-path keys.
- `benchmark-result-control-plane-score-v0.md`: compact score and attribution
  boundary contract. It keeps official task score separate from LoopX
  control-plane value and defines `benchmark_verifier_attribution_review_v0`
  routing fields so verifier dependency/platform failures block treatment and
  same-task repeat while still allowing a new material-ready candidate.
- `benchmark-restart-actionability-v0.md`: restarted-worker actionability
  fixture proving a compact reconstructed decision can produce exactly one
  bounded local fixture command or a public-safe blocker while preserving
  fixture-only authorization, no-leaderboard claims, and real-run stop
  conditions.
- `benchmark-restart-actionability-projection-decision-v0.md`: fixture-only
  decision to keep `benchmark_restart_actionability_v0` research/docs-only
  until a real restarted-worker consumer, approved no-submit setup evidence, or
  repeated re-derivation justifies a compact hot-path projection.
- `codex-app-parity-posthoc-check-v0.md`: lightweight posthoc check for compact
  `benchmark_run_v0` rows. It verifies canonical case active-state evidence,
  LoopX CLI call counters, public-safe Codex trajectory summaries, and
  reward/verifier/raw-material non-leakage before allowing a full Codex App
  product-path attribution claim.

## Relationship To LoopX Work

The research track should discover what to measure and which public benchmark
protocols are credible. Once the work requires a LoopX feature, that
feature should be split into a normal product todo and implemented in the
existing public capability surface, with this folder retaining only the research
motivation, protocol, and result evidence.
