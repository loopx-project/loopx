"""Launch a native trial; start its judge with benchmark.edgebench.serve.

CLI source options: --sforge-repository, --sforge-ref, --sforge-revision.
Defaults and the experiment-local source lock are owned by edgebench.source.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import logging
import ipaddress
import os
import signal
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from benchmark.edgebench.source import active_source_receipt, bootstrap

if __name__ == "__main__":
    bootstrap()  # Select SForge before any provider or adapter imports.

from sforge.harness.benchmark import BENCHMARK_FILENAME, load_benchmark
from sforge.harness.config import load_config
from sforge.harness.constants import get_admin_secret
from sforge.harness.run_agent import run_agent
from sforge.harness.task_spec import make_task_spec

from benchmark.runtime.codex import TASK_ENTRIES, turn_envelope_receipt
from loopx.reasoning_effort import REASONING_EFFORTS
from loopx.control_plane.work_items.stride_policy_codec import stride_strategy_options
from benchmark.runtime.sforge import (
    DEFAULT_TIMEOUT_SECONDS, PROFILES, SForgeWorker, profile_execution,
)
from benchmark.runtime.sforge_backend import RecordingDockerBackend
from benchmark.runtime.source import archive_source, source_pins
from benchmark.edgebench.prompts import (
    blind_task_prompt, best_only_task_prompt, native_task_instructions,
    research_artifact_instructions,
)
from benchmark.edgebench.online_sampling import OnlineSampler
from benchmark.edgebench.feedback import BestOnlyFeedback, FEEDBACK_MODES, validate_best_only
from benchmark.edgebench.feedback_hook import FEEDBACK_PAYLOAD
from benchmark.edgebench.result_publication import write_native_result as _write_native_final_result


def _observe_run(call):
    """Observe Ctrl+C even when the native runner consumes KeyboardInterrupt."""
    interrupted = False
    previous = signal.getsignal(signal.SIGINT)

    def on_interrupt(signum, frame):
        nonlocal interrupted
        interrupted = True
        if callable(previous):
            previous(signum, frame)
        else:
            signal.default_int_handler(signum, frame)

    # Respect an embedding host that intentionally ignores SIGINT.
    if previous != signal.SIG_IGN:
        signal.signal(signal.SIGINT, on_interrupt)
    started = time.monotonic()
    try:
        result = call()
        return result, interrupted, time.monotonic() - started
    finally:
        signal.signal(signal.SIGINT, previous)


def _result_status(*, interrupted, started, runtime_seconds, exit_code, timed_out=False,
                   execution_started=True, native_goal_outcome=None):
    if interrupted:
        return "cancelled"
    if not started:
        return "launch_failed"
    if native_goal_outcome == "cancelled":
        return "cancelled"
    if native_goal_outcome in {"failed", "usage_limited", "unknown"}:
        return "runner_failed"
    if not execution_started or runtime_seconds <= 0 or (not timed_out and exit_code != 0):
        return "runner_failed"
    return "terminal"


def _task_default(task_id: str, key: str, explicit: int | None, fallback: int) -> int:
    """Resolve EdgeBench task settings; explicit values retain runtime semantics."""
    if explicit is not None:
        return explicit
    defaults = json.loads(Path(__file__).with_name("task-defaults.json").read_text())
    value = defaults.get(task_id, {}).get(key, fallback)
    if type(value) is not int or value <= 0:
        raise ValueError(f"Invalid EdgeBench {key} default for {task_id}: {value!r}")
    return value


def _configure_provider_retries(config, seconds):
    if type(seconds) is not int or seconds < 0:
        raise ValueError("--provider-rate-limit-retry-seconds must be nonnegative")
    if seconds and (not config.agent_api_base_url or not config.agent_api_key):
        raise ValueError("--provider-rate-limit-retry-seconds requires an API key and base URL")
    if not hasattr(config, "agent_rate_limit_retry_seconds"):
        if seconds:
            raise ValueError("the selected SForge source does not support HTTP 429 retry configuration")
        return config
    # Explicit runner settings override ambient SForge defaults, including zero.
    return replace(config, agent_rate_limit_retry_seconds=seconds)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--tasks-dir", required=True, type=Path)
    parser.add_argument("--log-dir", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--worker", choices=PROFILES, required=True)
    parser.add_argument("--validation-command-json", type=json.loads,
                        help="For turn-resume, trusted guest argv that independently checks the work postcondition")
    parser.add_argument("--feedback", choices=FEEDBACK_MODES, default="native",
                        help="New-run default: native public feedback; blind/best-only are explicit controls")
    parser.add_argument("--managed-run-json", type=Path,
                        help="Opt-in private binding for native terminal proof, consumed by the controller")
    turn_view = parser.add_mutually_exclusive_group()
    turn_view.add_argument("--turn-envelope", action="store_true",
                        help="Opt-in short heartbeat context with same-invocation full captures")
    turn_view.add_argument("--complete-turn", action="store_true",
                        help="Explicit complete v1 Turn contract with shared exact-Turn settlement; heartbeat workers only")
    parser.add_argument("--context-delivery", choices=("off", "on"),
                        help="Native Codex context delivery: on by default for heartbeat workers; pass off to disable")
    parser.add_argument("--execution-observation", choices=("local_probe",),
                        help="Optional local execution probe for heartbeat workers")
    parser.add_argument("--native-result-comparison", action="store_true",
                        help="Opt-in read-only native result comparison for native Explore")
    parser.add_argument("--native-child-limit", type=int,
                        help="Explicit native child capacity and optional task delegation authorization, shared across study arms; omitted preserves existing behavior")
    parser.add_argument("--provider-request-admission", action="store_true",
                        help="Opt-in canonical admission before each heartbeat custom Responses request")
    parser.add_argument("--native-goal-continue", action="store_true",
                        help="Opt-in: when a native Goal declares completion with budget left, start "
                             "continuation Goals until the budget edge (native-goal worker only)")
    parser.add_argument("--native-readiness-total-seconds", type=float,
                        help="Opt-in fresh native receiver setup budget, at most 75s and clipped to "
                             "the existing outer allowance; only read-only account timeouts retry")
    parser.add_argument("--native-readiness-attempt-seconds", type=float,
                        help="Per-attempt cap for explicit native readiness setup, at most 25s; "
                             "omitted keeps 25s (not a model retry)")
    # Preserve the existing unique abbreviation after adding --managed-run-json.
    parser.add_argument("--model", "--m", required=True)
    parser.add_argument("--effort", choices=REASONING_EFFORTS, required=True)
    parser.add_argument("--provider-rate-limit-retry-seconds", type=int, default=0,
                        help="Host API gateway HTTP 429 retry window for new trials; "
                             "0 keeps pass-through behavior, e.g. 900 tolerates transient limits")
    parser.add_argument("--timeout", type=int,
                        help="Total trial seconds; task-defaults.json overrides the 18h fallback")
    parser.add_argument("--task-entry", choices=TASK_ENTRIES,
                        help="Heartbeat default: loopx-planned; seeded-todo is an explicit ablation")
    cadence = parser.add_mutually_exclusive_group()
    cadence.add_argument("--replan-after-turns", type=int, choices=range(1, 7),
                         help="Heartbeat default: 6 settled effective work Turns")
    cadence.add_argument("--replan-after-todos", type=int, choices=range(1, 6),
                         help="Explicit completed-Todo cadence ablation for heartbeat profiles")
    parser.add_argument("--planning-horizon-goal", action=argparse.BooleanOptionalAction, default=None,
                        help="Heartbeat workers include the original Goal before selection by default; --no-planning-horizon-goal opts out")
    parser.add_argument("--stride-strategy", choices=stride_strategy_options(),
                        help="Explicit heartbeat Goal-review strategy; omitted keeps the ordinary off treatment")
    parser.add_argument("--checkpoint-controller-file", type=Path,
                        help="Host-private native checkpoint controller; requires full fresh-replan handoff")
    parser.add_argument("--checkpoint-export-root", type=Path,
                        help="Existing physical host volume for full worker checkpoints; must preserve Linux filenames")
    parser.add_argument("--preserve-failed-worker", action="store_true",
                        help="Retain an Official worker after confirmed failure, or capture and retain a failed heartbeat worker with full controller capture")
    parser.add_argument("--checkpoint-request", type=Path,
                        help="Explicit host JSON declaration staged into a heartbeat worker; omitted disables capture")
    parser.add_argument("--restore-checkpoint", type=Path,
                        help="Explicit complete checkpoint to continue the retained Goal/Agent in an isolated replacement")
    parser.add_argument("--restore-sha256")
    retention = parser.add_mutually_exclusive_group()
    retention.add_argument("--checkpoint-retention-policy", type=Path,
                           help="Override default latest-only rotation in controller-enabled checkpoint banks")
    retention.add_argument("--no-checkpoint-retention", action="store_true",
                           help="Keep every captured point; disable default checkpoint rotation")
    parser.add_argument("--restore-config", type=Path)
    parser.add_argument("--restore-model-config", type=Path,
                        help="Explicit destination config for changing only model/effort in a new checkpoint receiver; --restore-config retains the sealed source config")
    parser.add_argument("--restore-goal")
    parser.add_argument("--restore-agent")
    parser.add_argument("--restore-source-todo")
    parser.add_argument("--restore-research-component", action="append", default=[],
                        help="Opt-in captured file component ID to restore at its original research path; repeat per component")
    parser.add_argument("--restore-fresh-replan", action="store_true",
                        help="Start a fresh conversation before the retained pending replan while preserving full Goal state")
    parser.add_argument("--restore-phase-extension-seconds", type=int, default=0,
                        help="Explicit authorized extra continuation time; omitted preserves the checkpoint's remaining phase")
    parser.add_argument("--restore-phase-remaining-cap-seconds", type=int,
                        help="Only reduce the warm continuation allowance to the audited remaining solver time")
    parser.add_argument("--eval-interval", type=int,
                        help="Native default: 0 (no periodic evaluation); other modes use task defaults; explicit 0 disables")
    parser.add_argument("--submission-cooldown", type=int,
                        help="Native default: 3600s between accepted submissions; other modes retain 120s")
    parser.add_argument("--judge-url", required=True)
    parser.add_argument("--api-proxy-url", help="Operator-owned, OpenAI-only CONNECT proxy")
    parser.add_argument("--docker-host-bridge", action="store_true",
                        help="Host services run on Docker's default bridge (e.g. runner inside a VM); "
                             "pin host.docker.internal there instead of Docker's host-gateway alias")
    args = parser.parse_args(argv)
    if args.native_result_comparison and (
            args.worker != "heartbeat-explore" or args.feedback != "native"):
        parser.error("--native-result-comparison requires native heartbeat-explore")
    try:
        execution = profile_execution(
            args.worker, task_entry=args.task_entry,
            context_delivery=args.context_delivery, turn_envelope=args.turn_envelope,
            complete_turn=args.complete_turn, validation_command=args.validation_command_json,
            native_child_limit=args.native_child_limit,
        )
    except ValueError as error:
        parser.error(str(error))
    args.context_delivery = execution.context_delivery
    if args.native_child_limit is not None and (
            args.native_child_limit < 1 or args.feedback != "native"):
        parser.error("--native-child-limit requires a positive limit and native feedback")
    managed_binding = None
    if args.managed_run_json is not None:
        if args.feedback != "native":
            parser.error("--managed-run-json requires native feedback")
        from benchmark.edgebench.managed_run import (
            persist_managed_run, publish_managed_terminal, read_managed_run, retain_managed_solver,
        )
        try:
            managed_binding = read_managed_run(args.managed_run_json, run_id=args.run_id, task_id=args.task)
        except OSError:
            parser.error("Could not read the private managed run binding")
        except ValueError as error:
            parser.error(str(error))
    api_base_url = os.environ.get("OPENAI_BASE_URL") or None
    api_key = os.environ.get("OPENAI_API_KEY") if api_base_url else None
    if api_base_url and not api_key:
        parser.error("OPENAI_BASE_URL requires OPENAI_API_KEY; no login fallback")
    from benchmark.runtime.codex_readiness import native_readiness_budget
    try:
        readiness_budget = native_readiness_budget(
            args.native_readiness_total_seconds, args.native_readiness_attempt_seconds)
    except ValueError as error:
        parser.error(str(error))
    if readiness_budget is not None and (
        args.worker in {"official", "single"} or api_base_url or args.restore_checkpoint is not None
    ):
        parser.error("Native readiness budget requires a fresh native Goal or heartbeat worker")
    args.timeout = _task_default(args.task, "timeout_seconds", args.timeout, DEFAULT_TIMEOUT_SECONDS)
    args.eval_interval = (0 if args.feedback == "native" and args.eval_interval is None else
                         _task_default(args.task, "eval_interval_seconds", args.eval_interval, 300))
    if args.submission_cooldown is None:
        args.submission_cooldown = 3600 if args.feedback == "native" else 120
    if args.eval_interval < 0 or args.submission_cooldown < 0:
        parser.error("Evaluation interval and submission cooldown must be non-negative")
    if args.execution_observation is not None and not args.worker.startswith("heartbeat-"):
        parser.error("--execution-observation requires a heartbeat worker")
    if args.provider_request_admission:
        if not args.worker.startswith("heartbeat-"):
            parser.error("--provider-request-admission requires a heartbeat worker")
        if not (api_base_url or "").strip() or not (api_key or "").strip():
            parser.error("--provider-request-admission requires OPENAI_BASE_URL and OPENAI_API_KEY")
        if (os.environ.get("CODEX_WIRE_API") or "responses") != "responses":
            parser.error("--provider-request-admission requires CODEX_WIRE_API=responses")
    if args.native_goal_continue and args.worker != "native-goal":
        parser.error("--native-goal-continue requires --worker native-goal")
    if (args.replan_after_turns is not None or args.replan_after_todos is not None
            ) and not execution.uses_loopx:
        parser.error("Replan cadence requires a heartbeat worker or turn-resume")
    if args.stride_strategy is not None and not execution.uses_loopx:
        parser.error("--stride-strategy requires a heartbeat worker or turn-resume")
    if args.planning_horizon_goal is not None and not execution.uses_loopx:
        parser.error("--planning-horizon-goal requires a heartbeat worker or turn-resume")
    checkpoint_request = None
    if args.checkpoint_request is not None:
        if not args.worker.startswith("heartbeat-"):
            parser.error("--checkpoint-request requires a heartbeat worker")
        from benchmark.runtime.checkpoint import read_checkpoint_request
        declaration, checkpoint_request = read_checkpoint_request(str(args.checkpoint_request))
    handoff = checkpoint_request is not None and declaration.get("controller_handoff", False)
    if bool(args.checkpoint_controller_file) != handoff or (handoff and args.feedback != "native"):
        parser.error("controller file and full controller_handoff must be enabled together with native feedback")
    if (args.checkpoint_retention_policy is not None or args.no_checkpoint_retention) and not handoff:
        parser.error("Checkpoint retention requires the explicit native controller capture path")
    retain_official_failure = args.preserve_failed_worker and args.worker == "official" and args.feedback == "native"
    if args.preserve_failed_worker and not (handoff or retain_official_failure):
        parser.error("Failed-worker preservation requires native Official feedback or full native controller capture")
    if args.preserve_failed_worker and args.eval_interval != 0:
        parser.error("Failed-worker preservation requires agent-only native submissions")
    if args.checkpoint_export_root is not None:
        if not handoff:
            parser.error("Checkpoint export storage requires full controller capture")
        from benchmark.edgebench.checkpoint_controller import _physical
        _physical(args.checkpoint_export_root)
        if not args.checkpoint_export_root.is_dir():
            parser.error("Checkpoint export root must be an existing directory")
    restore_values = (args.restore_sha256, args.restore_config, args.restore_goal,
                      args.restore_agent, args.restore_source_todo)
    if bool(args.restore_checkpoint) != all(restore_values) or (not args.restore_checkpoint and any(restore_values)):
        parser.error("Warm continuation requires checkpoint, digest, config, source Goal/Agent and source Todo together")
    if (not 0 <= args.restore_phase_extension_seconds < 2 ** 53 or
            (args.restore_phase_extension_seconds and not args.restore_checkpoint)):
        parser.error("A non-negative phase extension requires an explicit warm checkpoint continuation")
    if args.restore_phase_remaining_cap_seconds is not None and (
            not 0 <= args.restore_phase_remaining_cap_seconds < 2 ** 53 or not args.restore_checkpoint):
        parser.error("A non-negative phase cap requires an explicit warm checkpoint continuation")
    if args.restore_research_component and not args.restore_checkpoint:
        parser.error("Research component restoration requires an explicit warm checkpoint continuation")
    if args.restore_fresh_replan and not args.restore_checkpoint:
        parser.error("Fresh context requires an explicit warm checkpoint continuation")
    if args.restore_model_config is not None and not args.restore_checkpoint:
        parser.error("Model transition requires an explicit warm checkpoint continuation")
    if args.restore_checkpoint and (not handoff or args.eval_interval != 0 or args.replan_after_todos is not None):
        parser.error("Warm continuation requires full native checkpoint capture, no periodic evaluation and effective-Turn cadence")
    # One directory is one attempt: never reuse native registration or overwrite
    # source/profile evidence after an ambiguous launch.
    trial = args.log_dir / "runs" / args.run_id / args.task
    if (managed_binding is not None
            and trial.resolve() != args.log_dir.resolve() / "runs" / args.run_id / args.task):
        parser.error("Managed run requires its exact physical attempt directory")
    if args.restore_checkpoint is None:
        trial.mkdir(parents=True, exist_ok=False)
    else:
        # Native restore exclusively reserves runs/<run_id>. Host launch state
        # must not create that directory before admission, including its clock.
        if trial.parent.exists():
            raise FileExistsError("Warm destination run already exists")
        attempt = args.log_dir / ".warm-attempts" / args.run_id
        attempt.mkdir(mode=0o700, parents=True, exist_ok=False)
    product = Path(os.environ["LOOPX_SRC_DIR"]).resolve()
    runner = Path(os.environ.get("LOOPX_RUNNER_SRC_DIR", str(product))).resolve()
    pins = source_pins(product, runner, os.environ["LOOPX_EXPECTED_COMMIT"],
                       os.environ.get("LOOPX_EXPECTED_RUNNER_COMMIT"))
    if args.execution_observation is not None or args.native_result_comparison:
        from benchmark.runtime.source import require_candidate_content

        require_candidate_content(product, pins[0], work_finding=args.native_result_comparison)
    if args.restore_checkpoint is not None:
        # Activating the native capsule starts its remaining phase clock.
        # Qualify the actual install archives first, including ignored assets;
        # the adapter still checks them again when staging the worker.
        with tempfile.TemporaryDirectory(prefix="benchmark-restore-source-") as directory:
            archive_source(product, pins[0], Path(directory) / "source.tar")
            if runner != product:
                archive_source(runner, pins[1], Path(directory) / "runner.tar", ("benchmark/runtime",))
    task_file = args.tasks_dir / f"{args.task}.json"
    task = make_task_spec(task_file, load_benchmark(args.tasks_dir))
    if args.native_child_limit is not None and task.game_mode:
        parser.error("--native-child-limit requires a non-game task with workspace instructions")
    if managed_binding is not None and task.task_id != args.task:
        parser.error("Managed run task specification differs from the selected task")
    research_sidecar = args.feedback == "native" and not task.game_mode
    native_result_scope = None
    sforge_source = None
    if args.native_result_comparison:
        sforge_source = active_source_receipt()
        if not research_sidecar or not sforge_source or task.task_id != args.task:
            parser.error("--native-result-comparison requires a pinned native archive-scoring task")
        native_result_scope = {
            "judge_url": args.judge_url, "run": args.run_id, "task": task.task_id,
            "task_sha256": hashlib.sha256(task_file.read_bytes()).hexdigest(),
            "benchmark_sha256": hashlib.sha256((args.tasks_dir / BENCHMARK_FILENAME).read_bytes()).hexdigest(),
            "higher_is_better": task.judge.score_direction == "maximize",
        }
    if research_sidecar:
        from benchmark.runtime.research_artifacts import validate_research_scope, with_research_capture
        validate_research_scope(task.cwd, task.submit_paths)
        if checkpoint_request is not None:
            declaration = with_research_capture(declaration)
            checkpoint_request = json.dumps(declaration).encode("utf-8")
    blind_endpoint = None
    feedback_prompt = None
    workspace_instructions = None
    task_objective = None
    if args.feedback != "native":
        proxy = urlsplit(args.api_proxy_url or "")
        judge = urlsplit(args.judge_url)
        if task.internet or task.game_mode or not proxy.hostname or not proxy.port:
            raise ValueError("Restricted feedback requires a non-game isolated task and explicit API-only proxy")
        ipaddress.ip_address(proxy.hostname)  # No ambiguous DNS/network identity.
        judge_port = judge.port or (443 if judge.scheme == "https" else 80)
        if (proxy.hostname, proxy.port) == (judge.hostname, judge_port):
            raise ValueError("Restricted API and judge endpoints must be distinct")
        blind_endpoint = (proxy.hostname, proxy.port)
        if args.feedback == "best-only":
            validate_best_only(task, args.eval_interval)
        render_prompt = best_only_task_prompt if args.feedback == "best-only" else blind_task_prompt
        feedback_prompt = render_prompt(task.work.agent_query, task.submit_paths,
                                       eval_interval=args.eval_interval)
    elif not task.game_mode:
        native_policy = dict(submission_cooldown=args.submission_cooldown,
            eval_interval=args.eval_interval, internet=task.internet,
            selection=task.judge.selection, score_direction=task.judge.score_direction)
        workspace_instructions = native_task_instructions(task.submit_paths, **native_policy)
        workspace_instructions += research_artifact_instructions(
            checkpoint=checkpoint_request is not None,
            native_probe=args.execution_observation is not None,
            native_result_comparison=args.native_result_comparison,
        )
        # All native arms get the same storage policy. Checkpoint coverage is
        # explicit; neither collection nor a sidecar changes native submissions.
        feedback_prompt = workspace_instructions + f"---\n\n{task.work.agent_query}\n"
        task_objective = task.work.agent_query
    config = replace(
        load_config(),
        # This runner owns the OpenAI route selection.  Explicitly clearing the
        # route must also clear any ambient SFORGE_AGENT_API_* fallback.
        agent_api_base_url=api_base_url, agent_api_key=api_key,
        agent_model=args.model, agent_effort=args.effort,
        agent_timeout=args.timeout, log_dir=args.log_dir, tasks_dir=args.tasks_dir,
        work_cpu_limit=4, work_mem_limit="16g",
        # A restored judge already owns its captured resource profile. Cold
        # defaults must not replace or conflict with that live registration.
        judge_cpu_limit=None if args.restore_checkpoint else 4,
        judge_mem_limit=None if args.restore_checkpoint else "8g",
    )
    try:
        config = _configure_provider_retries(config, args.provider_rate_limit_retry_seconds)
    except ValueError as exc:
        parser.error(str(exc))
    if args.api_proxy_url:
        config.agent_extra_env = {
            "HTTPS_PROXY": args.api_proxy_url, "HTTP_PROXY": args.api_proxy_url,
            "NO_PROXY": f"localhost,127.0.0.1,{urlsplit(args.judge_url).hostname}",
        }
    warm_start = None
    restored_registration = None
    if args.restore_checkpoint is not None:
        from benchmark.runtime.sforge_warm_start import WarmStart
        from benchmark.edgebench.checkpoint_controller import restore
        # The native API version is a required provider prerequisite. Do not
        # cold-register if an installed provider cannot continue the checkpoint.
        from sforge.harness.restored_registration import RestoredRegistration
        # Validate session/workspace participants before native registration.
        candidate = WarmStart(args.restore_checkpoint, args.restore_sha256, args.restore_config,
            args.restore_goal, args.restore_agent, args.restore_source_todo,
            int(time.time()) + args.timeout, args.replan_after_turns or 6,
            tuple(args.restore_research_component), args.restore_fresh_replan,
            destination_config=args.restore_model_config, research_sidecar=research_sidecar,
            native_child_limit=args.native_child_limit)
        metadata = candidate.verify(task.cwd)
        if args.restore_model_config is not None:
            from loopx.hosts.codex_checkpoint import checkpoint_destination_config
            _, transition = checkpoint_destination_config(args.restore_config,
                metadata["native_session"]["config_sha256"], args.restore_model_config)
            if (transition["destination_model"] != args.model or
                    transition["destination_effort"] != args.effort):
                raise ValueError("Checkpoint destination model must match the explicit runner model/effort")
        snapshot = metadata["worker_file_snapshot"]
        capsule = args.restore_checkpoint / "components" / snapshot["native_capsule_component_id"]
        clock = attempt / "restored-phase-deadline"
        warm_start = candidate
    agent = SForgeWorker(config, profile=args.worker, cwd=task.cwd,
                         warm_start=warm_start, research_sidecar=research_sidecar,
                         timeout_seconds=args.timeout, feedback_prompt=feedback_prompt, feedback=args.feedback,
                         workspace_instructions=workspace_instructions,
                         native_child_limit=args.native_child_limit,
                         task_objective=task_objective,
                         task_entry=args.task_entry,
                         validation_command=execution.validation_command,
                         turn_envelope=args.turn_envelope,
                         **({"complete_turn": True} if args.complete_turn else {}),
                         context_delivery=args.context_delivery,
                         **({"execution_observation": args.execution_observation}
                            if args.execution_observation is not None else {}),
                         **({"native_result_scope": native_result_scope} if native_result_scope is not None else {}),
                         **({"provider_request_admission": True} if args.provider_request_admission else {}),
                         native_goal_continue=args.native_goal_continue,
                         **({"native_readiness_total_seconds": readiness_budget[0],
                             "native_readiness_attempt_seconds": readiness_budget[1]}
                            if readiness_budget is not None else {}),
                         replan_after_turns=args.replan_after_turns,
                         replan_after_todos=args.replan_after_todos,
                         planning_horizon_goal=args.planning_horizon_goal,
                         **({"stride_strategy": args.stride_strategy}
                            if args.stride_strategy is not None else {}),
                         **({"checkpoint_request": checkpoint_request}
                            if checkpoint_request is not None else {}))
    if args.api_proxy_url:
        agent.default_api_base_url = args.api_proxy_url
    logger = logging.getLogger("edgebench-runtime")
    sampler = None
    if args.feedback == "best-only":
        sampler = OnlineSampler(trial=trial, task=task, interval=args.eval_interval,
            judge_url=args.judge_url.replace("host.docker.internal", "127.0.0.1"),
            task_sha256=hashlib.sha256(task_file.read_bytes()).hexdigest(),
            secret=get_admin_secret(args.log_dir), logger=logger)
        sampler.qualify()  # Fail before native registration, solver creation or token spend.
    feedback = (BestOnlyFeedback(
        trial=trial, run_id=args.run_id, task_id=args.task,
        direction=task.judge.score_direction,
        selection=task.judge.selection,
        judge_url=args.judge_url.replace("host.docker.internal", "127.0.0.1"),
        admin_secret=get_admin_secret(args.log_dir), logger=logger, sampler=sampler,
    ) if args.feedback == "best-only" else None)
    checkpoint = None
    if args.checkpoint_controller_file is not None:
        from benchmark.edgebench.checkpoint_supervisor import CheckpointSupervisor
        destination = (args.checkpoint_export_root / args.run_id / args.task / "checkpoints"
                       if args.checkpoint_export_root is not None else trial / "checkpoints")
        terminal = None
        if args.preserve_failed_worker:
            from benchmark.edgebench.terminal_capture import terminal_components
            terminal = terminal_components(declaration, task.cwd)
        checkpoint = CheckpointSupervisor(args.checkpoint_controller_file, destination,
                                          retention_policy=args.checkpoint_retention_policy,
                                          retention_enabled=not args.no_checkpoint_retention,
                                          terminal_components=terminal,
                                          terminal_receipt=(trial / "terminal-preservation.json") if terminal else None)
    backend = RecordingDockerBackend(log_dir=trial / "collected", logger=logger,
                                     oauth_proxy=bool(args.api_proxy_url or api_base_url),
                                     blind_api_endpoint=blind_endpoint, feedback=feedback,
                                     docker_host_bridge=args.docker_host_bridge,
                                     research_sidecar=research_sidecar,
                                     retain_failed_native_worker=retain_official_failure,
                                     **({"checkpoint": checkpoint} if checkpoint is not None else {}))
    local_docker_images = backend.observe_local_images(
        work_key=task.work_image_key, judge_key=task.judge_image_key,
    )
    if warm_start is not None:
        activated = restore(args.checkpoint_controller_file, root=capsule,
            expected_sha256=snapshot["native_capsule_sha256"], run_id=args.run_id,
            phase_deadline_file=clock, phase_extension_seconds=args.restore_phase_extension_seconds,
            phase_remaining_cap_seconds=args.restore_phase_remaining_cap_seconds)
        epoch = int(clock.read_text())
        warm_start = replace(candidate, deadline_epoch=epoch)
        reference = json.loads((capsule / "checkpoint.json").read_bytes())["point_metadata"]["native_reference"]
        restored_registration = RestoredRegistration(reference["checkpoint_id"],
            reference["manifest_sha256"], epoch, capsule / "components/native_judge/manifest.json")
        # The native owner may already have materialized preserved submissions.
        trial.mkdir(parents=True, exist_ok=True)
        # Deliberately do not persist activation credentials in the public run receipt.
        del activated
        agent.warm_start = warm_start
    receipt = {
        "run_id": args.run_id, "task": args.task, "worker": args.worker,
        "task_entry": agent.task_entry,
        **execution.validation_receipt(),
        **({"planning_timeout_seconds": None}
           if agent.task_entry == "loopx-planned" else {}),
        "model": args.model, "effort": args.effort, "timeout_seconds": args.timeout,
        "provider_rate_limit_retry_seconds": args.provider_rate_limit_retry_seconds,
        **({"docker_host_bridge": True} if args.docker_host_bridge else {}),
        "loopx_commit": pins[0], "runner_commit": pins[1],
        "sforge_source": sforge_source if native_result_scope is not None else active_source_receipt(),
        "local_docker_images": local_docker_images,
        **turn_envelope_receipt(explicit=args.turn_envelope, complete=args.complete_turn),
        **({"context_delivery": "on"} if args.context_delivery != "off" else {}),
        **({"execution_observation": args.execution_observation}
           if args.execution_observation is not None else {}),
        **({"native_result_comparison": True} if native_result_scope is not None else {}),
        **({"provider_request_admission": "on"} if args.provider_request_admission else {}),
        **({"native_goal_continue": True} if args.native_goal_continue else {}),
        "task_sha256": hashlib.sha256(task_file.read_bytes()).hexdigest(),
        "feedback": args.feedback, "internet": task.internet,
        "eval_interval": args.eval_interval, "submission_cooldown": args.submission_cooldown,
        "status": "starting", "score_countable": False,
        **({"restore_phase_extension_seconds": args.restore_phase_extension_seconds}
           if args.restore_phase_extension_seconds else {}),
        **({"restore_phase_remaining_cap_seconds": args.restore_phase_remaining_cap_seconds}
           if args.restore_phase_remaining_cap_seconds is not None else {}),
        **({"restore_fresh_replan": True} if args.restore_fresh_replan else {}),
        **({"restored_checkpoint_sha256": warm_start.sha256,
            "original_deadline_epoch": warm_start.deadline_epoch,
            "restored_loopx_state": True,
            "normal_replan_cadence": warm_start.normal_cadence}
           if warm_start is not None else {}),
        **({"online_admission_epoch": sampler.epoch, "offline_scoring_complete": False}
           if sampler is not None else {}),
        **({"feedback_payload": FEEDBACK_PAYLOAD, "evaluator": sampler.evaluators[args.task]}
           if sampler is not None else {}),
        **({"replan_after_effective_turns": agent.replan_after_turns}
           if agent.replan_after_turns is not None else {}),
        **({"replan_after_completed_todos": agent.replan_after_todos}
           if agent.replan_after_todos is not None else {}),
        **({"planning_horizon_goal": agent.planning_horizon_goal}
           if agent.planning_horizon_goal is not None else {}),
        **({"stride_strategy": args.stride_strategy}
           if args.stride_strategy is not None else {}),
        **({"checkpoint_request_sha256": hashlib.sha256(checkpoint_request).hexdigest()}
           if checkpoint_request is not None else {}),
        **({"checkpoint_export_directory": str(checkpoint.destination)} if checkpoint is not None else {}),
        **({"preserve_failed_worker": True} if args.preserve_failed_worker else {}),
    }
    receipt_path = trial / "runtime-receipt.json"
    managed_launch = None
    if managed_binding is not None:
        managed_launch = persist_managed_run(trial, managed_binding, receipt=receipt, game_mode=task.game_mode)
    else:
        receipt_path.write_text(json.dumps(receipt, indent=2))
    try:
        result, was_interrupted, elapsed = _observe_run(lambda: run_agent(
            task_spec=task, agent=agent, config=config, backend=backend,
            run_id=args.run_id, model=args.model, timeout=None if warm_start else args.timeout,
            judge_url=args.judge_url, eval_interval=args.eval_interval,
            submission_cooldown=args.submission_cooldown, internet=task.internet,
            disable_auto_eval=args.feedback == "best-only" or warm_start is not None,
            **({"restored_registration": restored_registration} if restored_registration is not None else {}),
            disable_stop_hook=False, disable_auto_resume=agent.resume_cmd is None,
            max_submissions=0 if args.feedback != "native" else None,
            **backend.registration_options(run_agent),
        ))
    except Exception as error:
        receipt.update(status="runner_failed", error_kind=type(error).__name__)
        if provider_failure := getattr(backend, "provider_failure_receipt", None):
            receipt["provider_failure"] = provider_failure
        receipt_path.write_text(json.dumps(receipt, indent=2))
        if checkpoint is not None and args.preserve_failed_worker:
            checkpoint.finish_grading_from_receipt(trial, run_id=args.run_id, task_id=task.task_id)
        raise
    finally:
        if feedback is not None:
            feedback.close()
    if checkpoint is not None and args.preserve_failed_worker:
        checkpoint.finish_grading(getattr(result, "grading_status", None))
    # Native cancellation and swallowed Docker failures return runtime=0.
    # Observe the signal independently; never infer cancellation from output prose.
    execution = backend.execution_receipt or {}
    native = backend.native_goal_receipt or {}
    if args.worker == "native-goal" and not native.get("native_goal_outcome"):
        native["native_goal_outcome"] = "unknown"
    status = _result_status(interrupted=was_interrupted,
                            started=(trial / "started_at").is_file(),
                            runtime_seconds=result.runtime_seconds,
                            execution_started=execution.get("execution_started", False),
                            exit_code=execution.get("exit_code"),
                            native_goal_outcome=native.get("native_goal_outcome"),
                            timed_out=result.timed_out or execution.get("timed_out", False))
    receipt.update(status=status, controller_elapsed_seconds=elapsed,
                   timed_out=result.timed_out, runtime_seconds=result.runtime_seconds,
                   best_score=result.best_score, total_rounds=result.total_rounds)
    receipt["execution"] = execution
    if native:
        receipt.update(native)
    if provider_failure := getattr(backend, "provider_failure_receipt", None):
        receipt["provider_failure"] = provider_failure
    _write_native_final_result(trial, result, status=status, agent=agent.name,
                               task=task.task_id, run_id=args.run_id,
                               model=args.model, effort=args.effort, feedback=args.feedback,
                               native_goal_outcome=native.get("native_goal_outcome"))
    if managed_launch is None:
        receipt_path.write_text(json.dumps(receipt, indent=2))
    else:
        try:
            solver_retained = retain_managed_solver(trial, managed_binding, managed_launch, receipt=receipt, result=result)
        except (OSError, TypeError, ValueError) as error:
            logger.warning("Managed solver evidence unavailable (%s)", type(error).__name__)
        else:
            try:
                if not solver_retained or not publish_managed_terminal(trial, execute=True):
                    logger.warning("Managed terminal proof withheld: native solver termination or grading drain unconfirmed")
            except (OSError, TypeError, ValueError) as error:
                logger.warning("Managed terminal proof unavailable (%s)", type(error).__name__)
    print(json.dumps(receipt))
    return 0 if status == "terminal" else 130 if status == "cancelled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
