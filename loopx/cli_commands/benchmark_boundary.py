from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

from ..capabilities.benchmark_toolkit import (
    BENCHMARK_FOUR_ARM_CONTRACT_SCHEMA_VERSION,
    BENCHMARK_FOUR_ARM_QUALIFICATION_SCOPE,
    BENCHMARK_INTEGRITY_QUALIFICATION_SCHEMA_VERSION,
    BENCHMARK_MODEL_ROUTE_RECEIPT_SCHEMA_VERSION,
    BENCHMARK_RUNTIME_CONTINUITY_SCHEMA_VERSION,
    BENCHMARK_SEGMENT_RECEIPT_SCHEMA_VERSION,
    BENCHMARK_SOURCE_REVISION_FENCE_SCHEMA_VERSION,
    BENCHMARK_TREATMENT_CONTINUATION_RECEIPT_SCHEMA_VERSION,
    TRAE_BENCHMARK_EVIDENCE_SCHEMA_VERSION,
    BenchmarkEventWindowState,
    BenchmarkJobReceiptState,
    BenchmarkRunnerOwnerState,
    BenchmarkRuntimeContinuityClassification,
    BenchmarkRuntimeContinuityTransition,
    BenchmarkSegmentReceiptClassification,
    BenchmarkSegmentReceiptTransition,
    BenchmarkSourceRevisionFenceError,
    build_benchmark_candidate_source_boundary,
    build_benchmark_four_arm_contract_from_spec,
    build_benchmark_integrity_qualification,
    build_benchmark_runtime_continuity,
    build_benchmark_runtime_observation,
    build_benchmark_segment_receipt,
    build_benchmark_treatment_continuation_receipt,
    capture_traex_benchmark_evidence,
    compact_benchmark_four_arm_contract,
    compact_benchmark_source_revision_fence_receipt,
    filter_public_benchmark_artifact_paths,
    inspect_benchmark_source_revision_fence,
    verify_verifier_reward_file,
)

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
OutputFormat = Callable[[argparse.Namespace], str]

BENCHMARK_TOOLKIT_COMMANDS = {
    "candidate-source-boundary",
    "classify-artifacts",
    "integrity-qualification",
    "four-arm-contract",
    "runtime-continuity",
    "runtime-observation",
    "segment-receipt",
    "source-revision-fence",
    "traex-evidence",
    "treatment-continuation-receipt",
    "verify-verifier-reward",
}


def _read_json_object(path_text: str, label: str) -> dict[str, object]:
    raw = (
        sys.stdin.read()
        if path_text == "-"
        else Path(path_text).expanduser().read_text(encoding="utf-8")
    )
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise TypeError(f"{label} must contain a JSON object")
    return loaded


def _render_artifact_filter(payload: dict[str, object]) -> str:
    return (
        "# Benchmark Artifact Boundary\n\n"
        f"- Allowed: `{payload.get('allowed_to_read_count')}`\n"
        f"- Blocked: `{payload.get('blocked_count')}`\n"
        f"- Full paths recorded: `{payload.get('path_recorded')}`\n"
    )


def _render_candidate_boundary(payload: dict[str, object]) -> str:
    return (
        "# Benchmark Candidate Source Boundary\n\n"
        f"- Clean: `{payload.get('clean')}`\n"
        f"- Allowed: `{payload.get('allowed_source_count')}`\n"
        f"- Blocked: `{payload.get('blocked_source_count')}`\n"
        f"- Full paths recorded: `{payload.get('path_recorded')}`\n"
    )


def _render_integrity(payload: dict[str, object]) -> str:
    blockers = payload.get("blockers")
    blocker_text = ""
    if isinstance(blockers, list) and blockers:
        blocker_text = (
            "- Blockers: " + ", ".join(f"`{item}`" for item in blockers) + "\n"
        )
    review = payload.get("restricted_access_review")
    review = review if isinstance(review, dict) else {}
    return (
        "# Benchmark Integrity Qualification\n\n"
        f"- Classification: `{payload.get('classification')}`\n"
        f"- Integrity qualified: `{payload.get('integrity_qualified')}`\n"
        f"- Score claim eligible: `{payload.get('score_claim_eligible')}`\n"
        f"- Cheating detected: `{payload.get('benchmark_cheating_detected')}`\n"
        f"- Restricted access review: `{review.get('state')}`\n" + blocker_text
    )


def _render_four_arm_contract(payload: dict[str, object]) -> str:
    parity = payload.get("prompt_parity")
    parity = parity if isinstance(parity, dict) else {}
    return (
        "# Benchmark Four-Arm Contract\n\n"
        f"- Qualified: `{payload.get('qualified')}`\n"
        f"- Qualification scope: `{payload.get('qualification_scope')}`\n"
        f"- Execution qualified: `{payload.get('execution_qualified')}`\n"
        f"- Hint id: `{payload.get('hint_id')}`\n"
        f"- Plain Goal/LoopX prompt parity: `{parity.get('plain_pair_equal')}`\n"
        "- Domain-hint Goal/LoopX prompt parity: "
        f"`{parity.get('domain_hint_pair_equal')}`\n"
        f"- Prompt text recorded: `{payload.get('prompt_text_recorded')}`\n"
    )


def _render_source_revision_fence(payload: dict[str, object]) -> str:
    return (
        "# Benchmark Source Revision Fence\n\n"
        f"- Admitted: `{payload.get('admitted')}`\n"
        f"- Reason: `{payload.get('reason_code')}`\n"
        f"- Source clean: `{payload.get('source_clean')}`\n"
        f"- Local matches expected: `{payload.get('local_matches_expected')}`\n"
        "- Observed reference matches expected: "
        f"`{payload.get('observed_reference_matches_expected')}`\n"
        f"- Source path recorded: `{payload.get('source_path_recorded')}`\n"
        f"- Revision values recorded: `{payload.get('revision_values_recorded')}`\n"
    )


def _render_runtime_observation(payload: dict[str, object]) -> str:
    return (
        "# Benchmark Runtime Observation\n\n"
        f"- Classification: `{payload.get('classification')}`\n"
        f"- Healthy active: `{payload.get('healthy_active')}`\n"
        f"- Reconciliation required: `{payload.get('reconciliation_required')}`\n"
        f"- Recommended transition: `{payload.get('recommended_transition')}`\n"
        "- Admission ledger alone proves liveness: `False`\n"
    )


def _render_runtime_continuity(payload: dict[str, object]) -> str:
    return (
        "# Benchmark Runtime Continuity\n\n"
        f"- Classification: `{payload.get('classification')}`\n"
        f"- Qualified: `{payload.get('qualified')}`\n"
        f"- Closeout write allowed: `{payload.get('closeout_write_allowed')}`\n"
        f"- Recommended transition: `{payload.get('recommended_transition')}`\n"
    )


def _render_segment_receipt(payload: dict[str, object]) -> str:
    return (
        "# Benchmark Segment Receipt\n\n"
        f"- Classification: `{payload.get('classification')}`\n"
        f"- Qualified: `{payload.get('qualified')}`\n"
        f"- Segment result usable: `{payload.get('segment_result_usable')}`\n"
        f"- Recommended transition: `{payload.get('recommended_transition')}`\n"
    )


def _render_treatment_continuation(payload: dict[str, object]) -> str:
    return (
        "# Benchmark Treatment Continuation Receipt\n\n"
        f"- Classification: `{payload.get('classification')}`\n"
        f"- Startup: `{payload.get('startup_state')}`\n"
        "- Post-start control observed: "
        f"`{payload.get('post_start_control_observed')}`\n"
        f"- Terminal control: `{payload.get('terminal_control_state')}`\n"
        "- Score and countability changed: `False`\n"
    )


def _render_traex_evidence(payload: dict[str, object]) -> str:
    route = payload.get("model_route")
    route = route if isinstance(route, dict) else {}
    return (
        "# TraeX Benchmark Evidence\n\n"
        f"- Captured: `{payload.get('ok')}`\n"
        f"- Steps: `{payload.get('step_count')}`\n"
        f"- Tool calls: `{payload.get('tool_call_count')}`\n"
        f"- Runtime route: `{route.get('status')}`\n"
        "- Raw content recorded in receipt: `False`\n"
    )


def register_benchmark_boundary_commands(
    benchmark_subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    four_arm_parser = benchmark_subparsers.add_parser(
        "four-arm-contract",
        help="Qualify a Goal/LoopX by plain/domain-hint factorial contract.",
    )
    add_subcommand_format(four_arm_parser)
    four_arm_parser.add_argument(
        "--spec-json",
        required=True,
        help="benchmark_four_arm_spec_v0 JSON path, or - for stdin.",
    )
    four_arm_parser.add_argument(
        "--include-prompt-text",
        action="store_true",
        help="Include runner prompt text; default output contains hashes only.",
    )
    four_arm_parser.add_argument("--require-qualified", action="store_true")

    artifact_parser = benchmark_subparsers.add_parser(
        "classify-artifacts",
        help="Classify compact benchmark artifact paths without reading files.",
    )
    add_subcommand_format(artifact_parser)
    artifact_parser.add_argument("artifact_paths", nargs="+")
    artifact_parser.add_argument("--allow-public-filename", action="append", default=[])

    source_parser = benchmark_subparsers.add_parser(
        "candidate-source-boundary",
        help="Reject raw/private candidate-selection sources before reading them.",
    )
    add_subcommand_format(source_parser)
    source_parser.add_argument("source_paths", nargs="+")
    source_parser.add_argument("--allow-public-filename", action="append", default=[])
    source_parser.add_argument("--require-clean", action="store_true")

    revision_parser = benchmark_subparsers.add_parser(
        "source-revision-fence",
        help="Fail closed when a clean pinned source no longer matches its ref head.",
    )
    add_subcommand_format(revision_parser)
    revision_parser.add_argument("--source-checkout", required=True)
    revision_parser.add_argument("--expected-revision", required=True)
    revision_parser.add_argument("--observed-reference-revision", required=True)
    revision_parser.add_argument("--require-admitted", action="store_true")

    runtime_parser = benchmark_subparsers.add_parser(
        "runtime-observation",
        help="Classify exact-job runtime evidence without trusting ledger occupancy.",
    )
    add_subcommand_format(runtime_parser)
    runtime_parser.add_argument("--admission-active", action="store_true")
    runtime_parser.add_argument(
        "--job-receipt-state",
        choices=[item.value for item in BenchmarkJobReceiptState],
        required=True,
    )
    runtime_parser.add_argument(
        "--runner-owner-state",
        choices=[item.value for item in BenchmarkRunnerOwnerState],
        required=True,
    )
    runtime_parser.add_argument("--terminal-result-present", action="store_true")
    runtime_parser.add_argument("--typed-fatal-runner-error", action="store_true")
    runtime_parser.add_argument("--require-healthy", action="store_true")

    continuity_parser = benchmark_subparsers.add_parser(
        "runtime-continuity",
        help="Require launch and closeout runtime evidence to remain continuous.",
    )
    add_subcommand_format(continuity_parser)
    continuity_parser.add_argument("--launch-runtime-digest", required=True)
    continuity_parser.add_argument("--closeout-runtime-digest", required=True)
    continuity_parser.add_argument("--launch-generation-digest", required=True)
    continuity_parser.add_argument("--closeout-generation-digest", required=True)
    continuity_parser.add_argument(
        "--event-window-state",
        choices=[item.value for item in BenchmarkEventWindowState],
        required=True,
    )
    continuity_parser.add_argument("--require-qualified", action="store_true")

    segment_parser = benchmark_subparsers.add_parser(
        "segment-receipt",
        help="Reject stale, replayed, or wrong-segment benchmark receipts.",
    )
    add_subcommand_format(segment_parser)
    segment_parser.add_argument("--expected-segment-nonce", required=True)
    segment_parser.add_argument("--receipt-segment-nonce", required=True)
    segment_parser.add_argument("--segment-started-at", required=True)
    segment_parser.add_argument("--receipt-written-at", required=True)
    segment_parser.add_argument("--segment-ended-at", required=True)
    segment_parser.add_argument(
        "--prior-receipt-nonce", action="append", default=[]
    )
    segment_parser.add_argument("--require-qualified", action="store_true")

    integrity_parser = benchmark_subparsers.add_parser(
        "integrity-qualification",
        help="Reduce private trajectory and runner attestations to a compact receipt.",
    )
    add_subcommand_format(integrity_parser)
    integrity_parser.add_argument("--trajectory-json", required=True)
    integrity_parser.add_argument("--runtime-attestation-json", required=True)
    integrity_parser.add_argument("--policy-json")
    integrity_parser.add_argument(
        "--restricted-access-adjudication-json",
        help=(
            "Optional compact post-run analyst decision. Suspicion remains "
            "countable unless this confirms disclosure and causal use."
        ),
    )
    integrity_parser.add_argument("--sensitive-value-env", action="append", default=[])
    integrity_parser.add_argument("--require-qualified", action="store_true")

    treatment_continuation_parser = benchmark_subparsers.add_parser(
        "treatment-continuation-receipt",
        help=(
            "Separate treatment control persistence from score and countability."
        ),
    )
    add_subcommand_format(treatment_continuation_parser)
    treatment_continuation_parser.add_argument(
        "--observation-json",
        required=True,
        help="Compact post-run mechanism observation JSON path, or - for stdin.",
    )

    traex_parser = benchmark_subparsers.add_parser(
        "traex-evidence",
        help="Convert private TraeX JSONL to ATIF and a safe route receipt.",
    )
    add_subcommand_format(traex_parser)
    traex_parser.add_argument("--source-jsonl", required=True)
    traex_parser.add_argument(
        "--route-source-jsonl",
        help=(
            "Optional archived TraeX JSONL containing runtime token-count route "
            "events. Stdout JSONL alone normally cannot audit the model route."
        ),
    )
    traex_parser.add_argument("--atif-output", required=True)
    traex_parser.add_argument("--route-receipt-output", required=True)
    traex_parser.add_argument("--requested-model", required=True)
    traex_parser.add_argument("--requested-provider", default="trae")
    traex_parser.add_argument("--require-runtime-route", action="store_true")
    traex_parser.add_argument(
        "--execute",
        action="store_true",
        help="Write the private ATIF and public-safe route receipt.",
    )

    reward_parser = benchmark_subparsers.add_parser(
        "verify-verifier-reward",
        help="Validate a verifier reward.json against the numeric-only contract.",
    )
    add_subcommand_format(reward_parser)
    reward_parser.add_argument("reward_json", help="Path to a verifier reward.json.")
    reward_parser.add_argument("--require-valid", action="store_true")


def _invalid_integrity_input() -> dict[str, object]:
    return {
        "ok": False,
        "schema_version": BENCHMARK_INTEGRITY_QUALIFICATION_SCHEMA_VERSION,
        "classification": "trajectory_audit_input_invalid",
        "integrity_qualified": False,
        "integrity_countable": False,
        "score_claim_eligible": False,
        "score_claim_countable": False,
        "matched_pair_countable": False,
        "benchmark_cheating_detected": False,
        "blockers": ["benchmark_integrity_input_invalid"],
        "public_boundary": {
            "raw_content_recorded": False,
            "input_paths_recorded": False,
            "sensitive_values_recorded": False,
        },
    }


def _render_reward_contract(payload: dict[str, object]) -> str:
    return (
        "# Verifier Reward Contract\n\n"
        f"- Valid: `{payload.get('valid')}`\n"
        f"- Reason: `{payload.get('reason_code')}`\n"
        f"- Entries: `{payload.get('entry_count')}`\n"
        f"- Invalid keys: `{','.join(payload.get('invalid_keys') or [])}`\n"
    )


def _invalid_source_revision_fence_input() -> dict[str, object]:
    return {
        "schema_version": BENCHMARK_SOURCE_REVISION_FENCE_SCHEMA_VERSION,
        "admitted": False,
        "reason_code": "source_revision_fence_input_invalid",
        "source_clean": False,
        "local_matches_expected": False,
        "observed_reference_matches_expected": False,
        "source_path_recorded": False,
        "revision_values_recorded": False,
        "network_access_performed": False,
        "write_performed": False,
    }


def _invalid_runtime_continuity_input() -> dict[str, object]:
    return {
        "schema_version": BENCHMARK_RUNTIME_CONTINUITY_SCHEMA_VERSION,
        "classification": BenchmarkRuntimeContinuityClassification.INPUT_INVALID.value,
        "qualified": False,
        "closeout_write_allowed": False,
        "runtime_artifact_matches": False,
        "generation_matches": False,
        "event_window_state": "invalid",
        "event_window_qualified": False,
        "recommended_transition": (
            BenchmarkRuntimeContinuityTransition.REPAIR_CONTINUITY_EVIDENCE.value
        ),
        "public_boundary": {
            "runtime_artifact_digest_recorded": False,
            "generation_digest_recorded": False,
            "event_payload_recorded": False,
            "run_identity_recorded": False,
            "path_recorded": False,
        },
        "write_performed": False,
    }


def _invalid_segment_receipt_input() -> dict[str, object]:
    return {
        "schema_version": BENCHMARK_SEGMENT_RECEIPT_SCHEMA_VERSION,
        "classification": BenchmarkSegmentReceiptClassification.INPUT_INVALID.value,
        "qualified": False,
        "segment_result_usable": False,
        "segment_identity_matches": False,
        "receipt_within_segment_window": False,
        "receipt_unique": False,
        "prior_receipt_count": 0,
        "recommended_transition": (
            BenchmarkSegmentReceiptTransition.REPAIR_RECEIPT_EVIDENCE.value
        ),
        "public_boundary": {
            "nonce_recorded": False,
            "timestamps_recorded": False,
            "receipt_content_recorded": False,
            "run_identity_recorded": False,
            "path_recorded": False,
        },
        "write_performed": False,
    }


def _invalid_treatment_continuation_input() -> dict[str, object]:
    return {
        "schema_version": BENCHMARK_TREATMENT_CONTINUATION_RECEIPT_SCHEMA_VERSION,
        "ok": False,
        "classification": "input_invalid",
        "startup_state": "unknown",
        "observation_complete": False,
        "post_start_control_observed": False,
        "post_start_control_event_count": 0,
        "terminal_control_state": "unknown",
        "precommit_validation_state": "unknown",
        "reason_codes": ["treatment_continuation_input_invalid"],
        "score_semantics": {
            "score_countability_unchanged": True,
            "integrity_qualification_unchanged": True,
            "treatment_fidelity_unchanged": True,
            "claim_scope": "post_run_mechanism_analysis_only",
        },
        "public_boundary": {
            "raw_content_recorded": False,
            "path_recorded": False,
            "run_identity_recorded": False,
        },
        "write_performed": False,
    }


def _invalid_traex_evidence_input() -> dict[str, object]:
    return {
        "ok": False,
        "schema_version": TRAE_BENCHMARK_EVIDENCE_SCHEMA_VERSION,
        "source_runtime": "traex",
        "status": "input_invalid",
        "event_count": 0,
        "route_event_count": 0,
        "route_source_bound": False,
        "step_count": 0,
        "tool_call_count": 0,
        "private_atif_written": False,
        "route_receipt_written": False,
        "write_performed": False,
        "model_route": {
            "schema_version": BENCHMARK_MODEL_ROUTE_RECEIPT_SCHEMA_VERSION,
            "runtime": "traex",
            "status": "route_requested_not_runtime_audited",
            "runtime_audited": False,
            "matched": False,
            "observed_route_count": 0,
            "raw_content_recorded": False,
            "input_path_recorded": False,
        },
        "public_boundary": {
            "raw_content_recorded": False,
            "input_path_recorded": False,
            "output_path_recorded": False,
        },
    }


def handle_benchmark_boundary_command(
    args: argparse.Namespace,
    *,
    print_payload: PrintPayload,
    output_format: OutputFormat,
) -> int | None:
    if args.benchmark_command not in BENCHMARK_TOOLKIT_COMMANDS:
        return None

    if args.benchmark_command == "classify-artifacts":
        payload = filter_public_benchmark_artifact_paths(
            args.artifact_paths,
            extra_public_filenames=args.allow_public_filename,
        )
        print_payload(payload, output_format(args), _render_artifact_filter)
        return 0

    if args.benchmark_command == "candidate-source-boundary":
        payload = build_benchmark_candidate_source_boundary(
            args.source_paths,
            extra_public_filenames=args.allow_public_filename,
        )
        print_payload(payload, output_format(args), _render_candidate_boundary)
        return 1 if args.require_clean and not payload.get("clean") else 0

    if args.benchmark_command == "four-arm-contract":
        try:
            spec = _read_json_object(args.spec_json, "--spec-json")
            contract = build_benchmark_four_arm_contract_from_spec(spec)
            payload = (
                contract
                if args.include_prompt_text
                else compact_benchmark_four_arm_contract(contract)
            )
        except (OSError, TypeError, ValueError):
            payload = {
                "schema_version": BENCHMARK_FOUR_ARM_CONTRACT_SCHEMA_VERSION,
                "qualified": False,
                "qualification_scope": BENCHMARK_FOUR_ARM_QUALIFICATION_SCOPE,
                "execution_qualified": False,
                "reason_code": "four_arm_contract_input_invalid",
                "prompt_text_recorded": False,
            }
        print_payload(payload, output_format(args), _render_four_arm_contract)
        return 1 if args.require_qualified and not payload.get("qualified") else 0

    if args.benchmark_command == "source-revision-fence":
        try:
            fence = inspect_benchmark_source_revision_fence(
                args.source_checkout,
                expected_revision=args.expected_revision,
                observed_reference_revision=args.observed_reference_revision,
            )
            payload = compact_benchmark_source_revision_fence_receipt(fence)
        except BenchmarkSourceRevisionFenceError:
            payload = _invalid_source_revision_fence_input()
        print_payload(payload, output_format(args), _render_source_revision_fence)
        return 1 if args.require_admitted and not payload.get("admitted") else 0

    if args.benchmark_command == "runtime-observation":
        payload = build_benchmark_runtime_observation(
            admission_active=args.admission_active,
            job_receipt_state=args.job_receipt_state,
            runner_owner_state=args.runner_owner_state,
            terminal_result_present=args.terminal_result_present,
            typed_fatal_runner_error=args.typed_fatal_runner_error,
        )
        print_payload(payload, output_format(args), _render_runtime_observation)
        return 1 if args.require_healthy and not payload.get("healthy_active") else 0

    if args.benchmark_command == "runtime-continuity":
        try:
            payload = build_benchmark_runtime_continuity(
                launch_runtime_digest=args.launch_runtime_digest,
                closeout_runtime_digest=args.closeout_runtime_digest,
                launch_generation_digest=args.launch_generation_digest,
                closeout_generation_digest=args.closeout_generation_digest,
                event_window_state=args.event_window_state,
            )
        except (TypeError, ValueError):
            payload = _invalid_runtime_continuity_input()
        print_payload(payload, output_format(args), _render_runtime_continuity)
        return 1 if args.require_qualified and not payload.get("qualified") else 0

    if args.benchmark_command == "segment-receipt":
        try:
            payload = build_benchmark_segment_receipt(
                expected_segment_nonce=args.expected_segment_nonce,
                receipt_segment_nonce=args.receipt_segment_nonce,
                segment_started_at=args.segment_started_at,
                receipt_written_at=args.receipt_written_at,
                segment_ended_at=args.segment_ended_at,
                prior_receipt_nonces=args.prior_receipt_nonce,
            )
        except (TypeError, ValueError):
            payload = _invalid_segment_receipt_input()
        print_payload(payload, output_format(args), _render_segment_receipt)
        return 1 if args.require_qualified and not payload.get("qualified") else 0

    if args.benchmark_command == "treatment-continuation-receipt":
        try:
            observation = _read_json_object(
                args.observation_json,
                "--observation-json",
            )
            payload = build_benchmark_treatment_continuation_receipt(observation)
        except (OSError, UnicodeError, TypeError, ValueError):
            payload = _invalid_treatment_continuation_input()
        print_payload(payload, output_format(args), _render_treatment_continuation)
        return 0 if payload.get("ok") else 1

    if args.benchmark_command == "verify-verifier-reward":
        if args.reward_json == "-":
            raise ValueError("verify-verifier-reward requires a file path")
        payload = verify_verifier_reward_file(args.reward_json)
        print_payload(payload, output_format(args), _render_reward_contract)
        return 1 if args.require_valid and not payload.get("valid") else 0

    if args.benchmark_command == "traex-evidence":
        try:
            payload = capture_traex_benchmark_evidence(
                source_jsonl=args.source_jsonl,
                atif_output=args.atif_output,
                route_receipt_output=args.route_receipt_output,
                requested_model=args.requested_model,
                requested_provider=args.requested_provider,
                route_source_jsonl=args.route_source_jsonl,
                execute=args.execute,
            )
        except (OSError, UnicodeError, TypeError, ValueError):
            payload = _invalid_traex_evidence_input()
        print_payload(payload, output_format(args), _render_traex_evidence)
        if not payload.get("ok"):
            return 1
        route = payload.get("model_route")
        route = route if isinstance(route, dict) else {}
        if (
            args.require_runtime_route
            and route.get("status") != "runtime_route_verified"
        ):
            return 1
        return 0

    try:
        trajectory = _read_json_object(args.trajectory_json, "--trajectory-json")
        attestation = _read_json_object(
            args.runtime_attestation_json,
            "--runtime-attestation-json",
        )
        policy = (
            _read_json_object(args.policy_json, "--policy-json")
            if args.policy_json
            else None
        )
        restricted_access_adjudication = (
            _read_json_object(
                args.restricted_access_adjudication_json,
                "--restricted-access-adjudication-json",
            )
            if args.restricted_access_adjudication_json
            else None
        )
        sensitive_values: list[str] = []
        for env_name in args.sensitive_value_env:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
                raise ValueError("invalid sensitive value environment name")
            value = os.environ.get(env_name)
            if not value:
                raise ValueError("missing sensitive value environment variable")
            sensitive_values.append(value)
        payload = build_benchmark_integrity_qualification(
            trajectory=trajectory,
            runtime_attestation=attestation,
            policy=policy,
            restricted_access_adjudication=restricted_access_adjudication,
            sensitive_values=sensitive_values,
        )
    except (OSError, UnicodeError, TypeError, ValueError):
        payload = _invalid_integrity_input()
    print_payload(payload, output_format(args), _render_integrity)
    if not payload.get("ok"):
        return 1
    return 1 if args.require_qualified and not payload.get("integrity_qualified") else 0
