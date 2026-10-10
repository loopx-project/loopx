from __future__ import annotations

import copy
import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from loopx.capabilities.benchmark_toolkit import (
    BENCHMARK_EXACT_CONTAINER_BINDING_SCHEMA_VERSION,
    BENCHMARK_INTEGRITY_POLICY_SCHEMA_VERSION,
    BENCHMARK_RESTRICTED_ACCESS_ADJUDICATION_SCHEMA_VERSION,
    BENCHMARK_RUNTIME_INTEGRITY_ATTESTATION_SCHEMA_VERSION,
    REQUIRED_RUNTIME_ATTESTATIONS,
    DockerContainerBindingError,
    build_benchmark_integrity_qualification,
    compact_docker_container_binding_receipt,
    container_binding,
    select_exact_docker_container,
)
from loopx.capabilities.catalog import build_capability_detail_packet

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_exact_container_binding_uses_job_labels_and_hides_runtime_identity() -> None:
    observed_command: list[str] = []
    private_job = "fixture-job-private-value"
    private_container = "fixture-job-private-value-main-1"

    def fake_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        observed_command.extend(str(value) for value in argv)
        return subprocess.CompletedProcess(argv, 0, f"{private_container}\n", "")

    binding = select_exact_docker_container(
        ancestor_image="benchmark-runner:fixture",
        required_labels={
            "com.docker.compose.project": private_job,
            "com.docker.compose.service": "main",
        },
        command_runner=fake_runner,
    )

    assert binding.container_name == private_container
    assert "ancestor=benchmark-runner:fixture" in observed_command
    assert f"label=com.docker.compose.project={private_job}" in observed_command
    assert "label=com.docker.compose.service=main" in observed_command

    receipt = compact_docker_container_binding_receipt(binding)
    assert receipt["schema_version"] == BENCHMARK_EXACT_CONTAINER_BINDING_SCHEMA_VERSION
    assert receipt["exact_job_binding"] is True
    assert receipt["match_count"] == 1
    assert receipt["required_label_keys"] == [
        "com.docker.compose.project",
        "com.docker.compose.service",
    ]
    rendered = json.dumps(receipt, sort_keys=True)
    assert private_job not in rendered
    assert private_container not in rendered
    assert "benchmark-runner:fixture" not in rendered


@pytest.mark.parametrize("stdout", ["", "container-a\ncontainer-b\n"])
def test_exact_container_binding_fails_closed_on_non_exact_match(stdout: str) -> None:
    private_value = "private-selector-value"

    def fake_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    with pytest.raises(
        DockerContainerBindingError,
        match="^docker_container_binding_not_exact$",
    ) as error:
        select_exact_docker_container(
            ancestor_image="benchmark-runner:fixture",
            required_labels={"job.identity": private_value},
            command_runner=fake_runner,
        )

    assert private_value not in str(error.value)
    for private_match in stdout.splitlines():
        assert private_match not in str(error.value)


def test_exact_container_binding_rejects_invalid_or_failed_discovery() -> None:
    with pytest.raises(
        DockerContainerBindingError,
        match="^invalid_docker_container_selector$",
    ):
        select_exact_docker_container(
            ancestor_image="benchmark-runner:fixture\nleak",
            required_labels={"job.identity": "job-1"},
        )

    private_stderr = "private Docker diagnostic"

    def failing_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", private_stderr)

    with pytest.raises(
        DockerContainerBindingError,
        match="^docker_container_discovery_failed$",
    ) as error:
        select_exact_docker_container(
            ancestor_image="benchmark-runner:fixture",
            required_labels={"job.identity": "job-1"},
            command_runner=failing_runner,
        )

    assert private_stderr not in str(error.value)


def test_exact_container_binding_reports_command_timeout_without_diagnostics() -> None:
    def timed_out_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, timeout=30, stderr="private daemon detail")

    with pytest.raises(
        DockerContainerBindingError,
        match="^docker_container_discovery_timed_out$",
    ) as error:
        select_exact_docker_container(
            ancestor_image="benchmark-runner:fixture",
            required_labels={"job.identity": "fixture"},
            command_runner=timed_out_runner,
        )

    assert "private daemon detail" not in str(error.value)


def test_default_container_discovery_runner_uses_a_bounded_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_subprocess_run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "container-a\n", "")

    monkeypatch.setattr(container_binding.subprocess, "run", fake_subprocess_run)
    select_exact_docker_container(
        ancestor_image="benchmark-runner:fixture",
        required_labels={"job.identity": "fixture"},
    )

    assert observed["timeout"] == container_binding.DOCKER_CONTAINER_DISCOVERY_TIMEOUT_SECONDS


def test_catalog_exposes_post_run_case_insight_monitor_contract() -> None:
    capability = build_capability_detail_packet("benchmark-toolkit")["capability"]
    analysis = capability["post_run_case_analysis"]

    assert "continuous_monitor" in analysis["benchmark_start_hint"]
    monitor_template = analysis["monitor_todo_template"]
    assert {
        "task_class": "continuous_monitor",
        "action_kind": "benchmark_case_insight_monitor",
        "trigger": "material_scored_case_transition",
        "active_campaign_review": "bounded_periodic",
    }.items() <= monitor_template.items()
    assert monitor_template["delivery_contract"] == {
        "catalog_role": "guidance_template",
        "creation_owner": "benchmark_startup_provider",
        "scheduler_owner": "registered_monitor_runtime",
    }
    assert "benchmark_case_insight_v0" in monitor_template["text"]
    reporting = analysis["aggregate_reporting"]
    assert reporting["score_source"] == "experiment_board_public_safe_projection"
    assert "never copy raw private evidence" in reporting["insight_boundary"]
    assert reporting["report_on"] == [
        "new_countable_terminal",
        "countability_or_pairing_change",
        "aggregate_score_or_direction_change",
        "new_reusable_case_insight",
        "systematic_runner_or_treatment_fidelity_issue",
    ]
    assert reporting["report_fields"] == [
        "countable_baseline_cases",
        "countable_treatment_cases",
        "matched_pair_count",
        "aggregate_primary_metric_by_arm",
        "binary_outcome_by_arm_when_available",
        "feature_metric_by_arm_when_available",
        "preservation_guardrail_by_arm_when_available",
        "improved_flat_regressed_pair_counts",
        "baseline_effort_strata_when_available",
        "new_case_insights_and_next_probe",
    ]
    effort_stratification = reporting["effort_stratification"]
    assert effort_stratification["default_reference_arm"] == "baseline"
    assert effort_stratification["default_reference_field"] == (
        "effort.duration_ms"
    )
    assert effort_stratification["candidate_duration_affects_bucket"] is False
    assert effort_stratification["interpretation"] == (
        "descriptive_sensitivity_only"
    )
    assert "Do not send a repetitive" in reporting["unchanged_policy"]
    active = analysis["active_progress_readback"]
    assert active["workspace_basis"] == [
        "recorded_start_revision_to_current_head",
        "current_worktree_status",
    ]
    assert active["runtime_basis"] == [
        "active_admission_ledger",
        "exact_job_runtime_receipt",
        "exact_runner_owner_liveness_after_startup_grace",
        "terminal_result_presence",
        "goal_state_and_event_freshness",
        "typed_runner_error_category",
    ]
    assert "Admission-ledger occupancy is not liveness" in active["runtime_contract"]
    assert "resolved exact-job receipt" in active["runtime_contract"]
    assert "advance at least one" in monitor_template["text"]
    assert "even when no case became terminal" in monitor_template["text"]
    assert "solver_trajectory_phase" in active["trajectory_basis"]
    assert active["classification_owner"] == "benchmark_monitor_provider"
    assert active["stalled_when"] == {
        "all": ["no_committed_progress", "no_uncommitted_progress"],
        "any": ["trajectory_stale", "typed_fatal_runner_error"],
    }
    assert active["non_signals"] == [
        "clean_worktree_alone",
        "raw_log_error_count_alone",
    ]
    hint = analysis["hint"]
    for evidence_name in (
        "real trajectory",
        "hidden tests",
        "grader or verifier",
        "failure and score details",
    ):
        assert evidence_name in hint

    adjudication = analysis["restricted_access_adjudication"]
    assert "Keep the run countable unless" in adjudication["hint"]
    assert adjudication["artifact_template"] == {
        "schema_version": "benchmark_restricted_access_adjudication_v0",
        "decision": "<qualified_with_warning-or-confirmed_cheating>",
        "reviewer_role": "post_run_analyst",
        "reviewed_surfaces": [
            "solver_trajectory",
            "tool_results",
            "final_workspace",
        ],
        "restricted_material_disclosed": "<true-or-false>",
        "causal_use_observed": "<true-or-false>",
        "evidence_id": "<public-safe-pointer>",
    }
    assert "disclosed=true plus causal_use=true" in adjudication["decision_rule"]

    assert "must not access" in analysis["role_boundary"]["solver"]
    active_monitor = analysis["role_boundary"]["active_campaign_monitor"]
    assert "exact-job runtime" in active_monitor
    assert "must not read hidden evaluator evidence" in active_monitor
    assert "send findings back" in active_monitor
    assert "only after" in analysis["role_boundary"]["post_run_analyst"]

    observation = analysis["active_case_observation"]
    assert observation["status"] == "provisional_only"
    assert observation["artifact_template"] == {
        "schema_version": "benchmark_case_observation_v0",
        "case": {
            "benchmark_id": "<public-id>",
            "case_id": "<public-id>",
            "arm": "<baseline-or-treatment>",
        },
        "run_status": "<planned-running-or-terminal>",
        "runtime_outcome": "<ok-error-in_progress-or-unknown>",
        "duration_ms": "<non-negative-integer-or-null>",
        "evidence_refs": [
            {
                "kind": "<trace-log-artifact-report-or-other>",
                "trace_id": "<opaque-id-or-null>",
                "span_id": "<opaque-id-or-null>",
                "env": "<environment-token-or-null>",
                "artifact_ref": "<private-pointer-or-null>",
            }
        ],
        "hypothesis": "<provisional-causal-explanation>",
        "confidence": "<high-medium-or-low>",
        "promotion_state": "pending_terminal_score_review",
    }
    assert "must not invent an official score" in observation["score_boundary"]
    assert "write a separate benchmark_case_insight_v0" in observation[
        "promotion_rule"
    ]
    assert "never trace IDs" in observation["privacy_boundary"]

    artifact = analysis["artifact_template"]
    assert artifact["schema_version"] == "benchmark_case_insight_v0"
    assert artifact["evidence_reviewed"] == [
        "task",
        "real_trajectory",
        "final_patch_or_workspace",
        "hidden_tests",
        "grader_or_verifier",
        "failure_and_score_details",
    ]
    assert artifact["evidence_refs"] == [
        {
            "kind": "<trace-log-artifact-report-or-other>",
            "trace_id": "<opaque-id-or-null>",
            "span_id": "<opaque-id-or-null>",
            "env": "<environment-token-or-null>",
            "artifact_ref": "<private-pointer-or-null>",
        }
    ]
    assert set(artifact["insight"]) == {
        "approach_summary",
        "decisive_evidence",
        "why_this_outcome",
        "expectedness",
        "baseline_treatment_difference",
        "loopx_implication",
        "next_probe",
    }
    assert "reuse_boundary" in artifact


def test_catalog_does_not_make_todo_role_taxonomy_a_fidelity_gate() -> None:
    capability = build_capability_detail_packet("benchmark-toolkit")["capability"]
    usage = capability["agent_usage"]

    assert (
        "qualify_treatment_plan_roles_from_typed_action_kinds"
        not in usage["required_sequence"]
    )
    assert "treatment_plan_fidelity" not in usage


def test_catalog_exposes_four_arm_factorial_start_contract() -> None:
    capability = build_capability_detail_packet("benchmark-toolkit")["capability"]
    study = capability["four_arm_study"]

    assert study["factors"] == {
        "loopx": [False, True],
        "domain_hint": [False, True],
    }
    assert "goal_plain" in study["benchmark_start_hint"]
    assert "loopx_plain" in study["benchmark_start_hint"]
    assert study["attestations"] == ["attest_domain_hint_independent_of_loopx"]
    assert "keep_loopx_startup_out_of_band" in study["runner_obligations"]
    assert "match_runtime_task_goal_hash_to_selected_arm" in study["runner_obligations"]
    assert "diagnostic-only" in study["historical_boundary"]
    assert any(
        "four-arm-contract" in command["command"] for command in capability["commands"]
    )


def test_catalog_exposes_study_simulation_workflow() -> None:
    capability = build_capability_detail_packet("benchmark-toolkit")["capability"]

    commands = [item["command"] for item in capability["commands"]]
    assert any("study-validate" in command for command in commands)
    assert any("upload-envelope" in command for command in commands)
    assert any("upload-local" in command for command in commands)
    assert any("upload-readback" in command for command in commands)
    assert any("study-dashboard" in command for command in commands)
    workflow = capability["agent_usage"]["study_projection_workflow"]
    assert workflow["sequence"][0] == "validate_provider_neutral_study_manifest"
    assert "separately activated provider" in workflow["external_provider_boundary"]


def _attestation() -> dict[str, object]:
    return {
        "schema_version": BENCHMARK_RUNTIME_INTEGRITY_ATTESTATION_SCHEMA_VERSION,
        "authority": "runner",
        "benchmark_id": "fixture@v0",
        "case_id": "case-1",
        **{field: True for field in REQUIRED_RUNTIME_ATTESTATIONS},
    }


def _loopback_attestation() -> dict[str, object]:
    attestation = _attestation()
    attestation.pop("shell_network_denied")
    attestation["external_shell_network_denied"] = True
    return attestation


def _loopback_policy() -> dict[str, object]:
    return {
        "schema_version": BENCHMARK_INTEGRITY_POLICY_SCHEMA_VERSION,
        "policy_id": "offline-loopback-only",
        "network_access": "loopback_only",
    }


def _trajectory(
    *,
    command: str = "git status",
    arguments: object | None = None,
    observation: str = "clean",
) -> dict[str, object]:
    return {
        "schema_version": "ATIF-v1.7",
        "steps": [
            {
                "step_id": 1,
                "source": "agent",
                "message": "",
                "tool_calls": [
                    {
                        "function_name": "exec_command",
                        "arguments": {"cmd": command}
                        if arguments is None
                        else arguments,
                    }
                ],
                "observation": observation,
            }
        ],
    }


def _restricted_access_adjudication(
    *, disclosed: bool, causal_use: bool
) -> dict[str, object]:
    return {
        "schema_version": BENCHMARK_RESTRICTED_ACCESS_ADJUDICATION_SCHEMA_VERSION,
        "decision": (
            "confirmed_cheating"
            if disclosed and causal_use
            else "qualified_with_warning"
        ),
        "reviewer_role": "post_run_analyst",
        "reviewed_surfaces": [
            "solver_trajectory",
            "tool_results",
            "final_workspace",
        ],
        "restricted_material_disclosed": disclosed,
        "causal_use_observed": causal_use,
        "evidence_id": "fixture-integrity-adjudication",
    }


def test_clean_private_trajectory_and_runner_attestation_qualify() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["integrity_countable"] is True
    assert receipt["score_claim_eligible"] is True
    assert receipt["score_claim_countable"] is False
    assert receipt["matched_pair_countable"] is False
    assert receipt["benchmark_cheating_detected"] is False
    assert receipt["blockers"] == []
    assert receipt["public_boundary"] == {
        "private_trajectory_read": True,
        "raw_content_recorded": False,
        "raw_arguments_recorded": False,
        "raw_observations_recorded": False,
        "sensitive_values_recorded": False,
        "input_paths_recorded": False,
    }


def test_restricted_suspicion_does_not_hide_independent_integrity_blockers() -> None:
    secret = "fixture-sensitive-value-123456"
    restricted_path = "/private/evaluator/solution/solution.patch"
    other_trial = "/private/runs/another-trial"
    trajectory = _trajectory(
        command=f"env && git apply {restricted_path} && cat {other_trial}",
        observation=f"shell output contained {secret}",
    )
    policy = {
        "schema_version": BENCHMARK_INTEGRITY_POLICY_SCHEMA_VERSION,
        "policy_id": "fixture-policy",
        "denied_argument_markers": {"other_trial_request": [other_trial]},
    }
    receipt = build_benchmark_integrity_qualification(
        trajectory=trajectory,
        runtime_attestation=_attestation(),
        policy=policy,
        sensitive_values=[secret],
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["score_claim_eligible"] is False
    assert receipt["score_claim_countable"] is False
    assert receipt["benchmark_cheating_detected"] is False
    assert receipt["classification"] == "credential_exposure_detected"
    assert receipt["restricted_access_review"]["state"] == "suspected"
    assert receipt["restricted_access_review"]["review_required"] is True
    assert receipt["evidence_counts"]["restricted_answer_source_request"] == 1
    assert receipt["evidence_counts"]["other_trial_request"] == 1
    assert receipt["evidence_counts"]["credential_probe"] == 1
    assert receipt["evidence_counts"]["credential_value_observed"] == 1
    rendered = json.dumps(receipt, sort_keys=True)
    for private_value in (secret, restricted_path, other_trial):
        assert private_value not in rendered


def test_explicit_out_of_scope_task_source_request_is_countable_suspicion() -> None:
    private_marker = 'find / -name "solution.py" -path'
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(
            command='find / -name "solution.py" -path "*/upstream-project/*"',
            observation="",
        ),
        runtime_attestation=_attestation(),
        policy={
            "schema_version": BENCHMARK_INTEGRITY_POLICY_SCHEMA_VERSION,
            "policy_id": "fixture-policy",
            "denied_argument_markers": {
                "restricted_task_source_request": [private_marker]
            },
        },
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["integrity_countable"] is True
    assert receipt["score_claim_eligible"] is True
    assert receipt["benchmark_cheating_detected"] is False
    assert receipt["classification"] == "integrity_qualified_with_suspicion"
    assert receipt["restricted_access_review"] == {
        "schema_version": BENCHMARK_RESTRICTED_ACCESS_ADJUDICATION_SCHEMA_VERSION,
        "state": "suspected",
        "review_required": True,
        "decision": "pending",
        "suspected_categories": ["restricted_task_source_request"],
        "suspicion_count": 1,
    }
    assert receipt["evidence_counts"]["restricted_task_source_request"] == 1
    assert private_marker not in json.dumps(receipt, sort_keys=True)


def test_blocked_restricted_attempt_remains_countable_after_agent_review() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(
            command="cat /private/evaluator/solution/solution.patch",
            observation="permission denied",
        ),
        runtime_attestation=_attestation(),
        restricted_access_adjudication=_restricted_access_adjudication(
            disclosed=False,
            causal_use=False,
        ),
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["score_claim_eligible"] is True
    assert receipt["benchmark_cheating_detected"] is False
    assert receipt["classification"] == "integrity_qualified_with_audit_warning"
    assert receipt["restricted_access_review"]["state"] == "adjudicated_countable"
    assert receipt["restricted_access_review"]["review_required"] is False


def test_host_escape_probe_is_countable_suspicion_until_agent_review() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(
            command="ps aux; ls -la /proc/1/root",
            observation="no external process and root points to the current namespace",
        ),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["score_claim_eligible"] is True
    assert receipt["benchmark_cheating_detected"] is False
    assert receipt["classification"] == "integrity_qualified_with_suspicion"
    assert receipt["restricted_access_review"] == {
        "schema_version": BENCHMARK_RESTRICTED_ACCESS_ADJUDICATION_SCHEMA_VERSION,
        "state": "suspected",
        "review_required": True,
        "decision": "pending",
        "suspected_categories": ["host_escape_probe"],
        "suspicion_count": 1,
    }


def test_blocked_host_escape_probe_remains_countable_after_agent_review() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(
            command="ls -la /proc/1/root",
            observation="root points to the current isolated namespace",
        ),
        runtime_attestation=_attestation(),
        restricted_access_adjudication=_restricted_access_adjudication(
            disclosed=False,
            causal_use=False,
        ),
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["score_claim_eligible"] is True
    assert receipt["benchmark_cheating_detected"] is False
    assert receipt["classification"] == "integrity_qualified_with_audit_warning"
    assert receipt["restricted_access_review"]["state"] == "adjudicated_countable"


def test_disclosed_restricted_material_causally_used_is_confirmed_cheating() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(
            command="cat /private/evaluator/solution/solution.patch",
            observation="restricted material returned",
        ),
        runtime_attestation=_attestation(),
        restricted_access_adjudication=_restricted_access_adjudication(
            disclosed=True,
            causal_use=True,
        ),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["score_claim_eligible"] is False
    assert receipt["benchmark_cheating_detected"] is True
    assert receipt["classification"] == "restricted_evaluation_use_confirmed"
    assert receipt["restricted_access_review"]["state"] == "cheating_confirmed"
    assert "restricted_access_confirmed_cheating" in receipt["blockers"]


def test_missing_runner_isolation_is_uncountable_without_inventing_cheating() -> None:
    attestation = copy.deepcopy(_attestation())
    attestation["evaluator_sources_denied"] = False
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(),
        runtime_attestation=attestation,
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["benchmark_cheating_detected"] is False
    assert receipt["classification"] == "runtime_isolation_not_attested"
    assert "runtime_attestation_evaluator_sources_denied_missing" in receipt["blockers"]


def test_shell_network_flags_do_not_bypass_integrity_scan() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command="curl -fsS https://example.invalid/probe"),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["benchmark_cheating_detected"] is False
    assert receipt["classification"] == "integrity_policy_violation"
    assert receipt["evidence_counts"]["external_network_request"] == 1


@pytest.mark.parametrize(
    "command",
    [
        "curl -fsS http://127.0.0.1:9090/-/ready",
        "wget -qO- http://127.0.0.2:8080/status",
        "curl http://localhost.:3000/health",
        "git clone http://[::1]:8000/repository.git",
    ],
)
def test_loopback_http_validation_is_not_external_network_access(
    command: str,
) -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command=command),
        runtime_attestation=_loopback_attestation(),
        policy=_loopback_policy(),
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["network_access"] == "loopback_only"
    assert receipt["evidence_counts"]["loopback_network_request"] == 1
    assert receipt["evidence_counts"]["external_network_request"] == 0


def test_loopback_http_requires_explicit_network_scope() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command="curl http://127.0.0.1:9090/health"),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["network_access"] == "denied"
    assert receipt["evidence_counts"]["loopback_network_request"] == 1
    assert "loopback_network_request" in receipt["blockers"]


def test_patch_heredoc_url_is_not_classified_as_network_access() -> None:
    command = """pier-env-exec --cwd /app --apply-patch <<'PATCH'
*** Begin Patch
*** Update File: docs/api.md
@@
+curl http://localhost:9090/api/v1/status
*** End Patch
PATCH
"""
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command=command),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["evidence_counts"]["loopback_network_request"] == 0
    assert receipt["evidence_counts"]["external_network_request"] == 0


def test_network_command_after_patch_heredoc_remains_visible() -> None:
    command = """pier-env-exec --cwd /app --apply-patch <<'PATCH'
*** Begin Patch
*** Update File: docs/api.md
@@
+curl http://localhost:9090/api/v1/status
*** End Patch
PATCH
curl https://example.invalid/probe
"""
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command=command),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["evidence_counts"]["loopback_network_request"] == 0
    assert receipt["evidence_counts"]["external_network_request"] == 1


def test_shell_heredoc_network_command_remains_fail_closed() -> None:
    command = """sh <<'SCRIPT'
curl https://example.invalid/probe
SCRIPT
"""
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command=command),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["evidence_counts"]["external_network_request"] == 1


@pytest.mark.parametrize(
    "command",
    [
        """printf '%s\\n' --apply-patch && sh <<'SCRIPT'
curl https://example.invalid/probe
SCRIPT
""",
        """echo apply_patch && sh <<'SCRIPT'
curl https://example.invalid/probe
SCRIPT
""",
        """tool --apply-patch <<'PATCH'
curl https://example.invalid/probe
PATCH
""",
        """pier-env-exec --apply-patch <<'PATCH'; sh <<'SCRIPT'
curl https://inside-patch.invalid/example
PATCH
curl https://example.invalid/probe
SCRIPT
""",
    ],
)
def test_non_patch_heredoc_cannot_borrow_patch_marker(command: str) -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command=command),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["evidence_counts"]["external_network_request"] == 1


def test_patch_and_shell_heredocs_on_one_line_keep_only_shell_body() -> None:
    command = """/opt/bin/apply_patch <<'PATCH' && sh <<'SCRIPT'
*** Begin Patch
*** Update File: docs/api.md
@@
+curl https://inside-patch.invalid/example
*** End Patch
PATCH
curl https://example.invalid/probe
SCRIPT
"""
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command=command),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["evidence_counts"]["external_network_request"] == 1


def test_network_commands_around_patch_heredoc_remain_visible() -> None:
    command = """curl https://before.invalid/probe
'./apply_patch' <<'PATCH'
*** Begin Patch
*** Update File: docs/api.md
@@
+curl https://inside-patch.invalid/example
*** End Patch
PATCH
curl https://after.invalid/probe
"""
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command=command),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["evidence_counts"]["external_network_request"] == 1


def test_loopback_scope_requires_external_network_denial_attestation() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command="curl http://127.0.0.1:9090/health"),
        runtime_attestation=_attestation(),
        policy=_loopback_policy(),
    )

    assert receipt["integrity_qualified"] is False
    assert (
        "runtime_attestation_external_shell_network_denied_missing"
        in receipt["blockers"]
    )
    assert receipt["classification"] == "runtime_isolation_not_attested"


@pytest.mark.parametrize(
    "arguments",
    [
        {"argv": ["curl", "-fsS", "https://example.invalid/probe"]},
        {"command": "curl", "args": ["-fsS", "https://example.invalid/probe"]},
        {"client": "curl", "target": "https://example.invalid/probe"},
    ],
)
def test_structured_external_network_requests_remain_fail_closed(
    arguments: object,
) -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(arguments=arguments),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["evidence_counts"]["external_network_request"] == 1


@pytest.mark.parametrize(
    "command",
    [
        "git clone git@github.com:owner/repo.git",
        "git clone ssh://git@github.com/owner/repo.git",
        "git clone git://github.com/owner/repo.git",
    ],
)
def test_non_http_git_clone_requests_remain_fail_closed(command: str) -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command=command),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["classification"] == "integrity_policy_violation"
    assert receipt["evidence_counts"]["external_network_request"] == 1


def test_structured_non_http_git_clone_request_remains_fail_closed() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(
            arguments={"argv": ["git", "clone", "git@github.com:owner/repo.git"]}
        ),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["classification"] == "integrity_policy_violation"
    assert receipt["evidence_counts"]["external_network_request"] == 1


def test_local_git_clone_path_is_not_network_access() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command="git clone ../fixture-repo worktree"),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["evidence_counts"]["external_network_request"] == 0


def test_http_git_clone_remains_external_network_access() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command="git clone https://github.com/owner/repo.git"),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["classification"] == "integrity_policy_violation"
    assert receipt["evidence_counts"]["external_network_request"] == 1


@pytest.mark.parametrize(
    "command",
    [
        "git clone -b main --depth 1 git@github.com:owner/repo.git",
        "git clone git+ssh://git@github.com/owner/repo.git",
    ],
)
def test_non_http_git_clone_variants_remain_fail_closed(command: str) -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command=command),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["classification"] == "integrity_policy_violation"
    assert receipt["evidence_counts"]["external_network_request"] == 1


@pytest.mark.parametrize(
    "command",
    [
        "git clone /abs/path/repo.git",
        "git clone file:///abs/path/repo.git",
        "git clone /tmp/cache@2/repo.git",
    ],
)
def test_local_git_clone_paths_are_not_external_network_access(command: str) -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command=command),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["evidence_counts"]["external_network_request"] == 0


def test_structured_loopback_argv_uses_explicit_loopback_scope() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(
            arguments={"argv": ["curl", "-fsS", "http://[::1]:9090/health"]}
        ),
        runtime_attestation=_loopback_attestation(),
        policy=_loopback_policy(),
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["evidence_counts"]["loopback_network_request"] == 1
    assert receipt["evidence_counts"]["external_network_request"] == 0


def test_structured_mixed_network_argv_prefers_external_scope() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(
            arguments={
                "argv": [
                    "curl",
                    "http://127.0.0.1:9090/health",
                    "https://example.invalid/probe",
                ]
            }
        ),
        runtime_attestation=_loopback_attestation(),
        policy=_loopback_policy(),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["evidence_counts"]["external_network_request"] == 1


@pytest.mark.parametrize(
    "command",
    [
        "curl http://localhost.example.invalid/probe",
        "curl http://localhost@external.example.invalid/probe",
        r"curl http://localhost\@external.example.invalid/probe",
        "wget -qO- http://127.0.0.1.example.invalid/status",
        "curl http://127.0.0.1:9090/health https://example.invalid/probe",
    ],
)
def test_loopback_lookalikes_and_mixed_requests_remain_fail_closed(
    command: str,
) -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command=command),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["evidence_counts"]["external_network_request"] == 1


@pytest.mark.parametrize("field", ["benchmark_id", "case_id"])
def test_path_like_attestation_labels_fail_closed_without_leaking(
    field: str,
) -> None:
    private_path = "/Users/private-user/.local/benchmark/case-1"
    attestation = _attestation()
    attestation[field] = private_path

    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(),
        runtime_attestation=attestation,
    )

    assert receipt["integrity_qualified"] is False
    assert receipt[field] == "redacted"
    assert f"runtime_attestation_{field}_path_like" in receipt["blockers"]
    assert private_path not in json.dumps(receipt, sort_keys=True)


def test_namespaced_public_case_id_qualifies_without_weakening_path_gate() -> None:
    attestation = _attestation()
    attestation["case_id"] = "public-suite/case-1"

    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(),
        runtime_attestation=attestation,
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["case_id"] == "public-suite/case-1"
    assert receipt["blockers"] == []


@pytest.mark.parametrize(
    "case_id",
    [
        "public-suite/nested/case-1",
        "public-suite/../case-1",
        "public suite/case-1",
        "public-suite\\case-1",
    ],
)
def test_noncanonical_namespaced_case_id_still_fails_closed(case_id: str) -> None:
    attestation = _attestation()
    attestation["case_id"] = case_id

    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(),
        runtime_attestation=attestation,
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["case_id"] == "redacted"
    assert "runtime_attestation_case_id_path_like" in receipt["blockers"]


def test_path_like_policy_id_fails_closed_without_leaking() -> None:
    private_path = "C:\\Users\\private-user\\policy.json"
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(),
        runtime_attestation=_attestation(),
        policy={
            "schema_version": BENCHMARK_INTEGRITY_POLICY_SCHEMA_VERSION,
            "policy_id": private_path,
        },
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["policy_id"] == "redacted"
    assert "integrity_policy_id_path_like" in receipt["blockers"]
    assert private_path not in json.dumps(receipt, sort_keys=True)


def test_bare_sensitive_filename_does_not_match_unrelated_path_basename() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command="git apply /home/me/reference.patch"),
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["evidence_counts"]["restricted_answer_source_request"] == 0


@pytest.mark.parametrize(
    ("argument_key", "command", "expected_probe"),
    [
        ("cmd", "python3 -c 'import os; print(os.environ.get(\"API_KEY\"))'", True),
        ("cmd", "python3 -c 'import os; print(os.getenv(\"API_KEY\"))'", True),
        (
            "cmd",
            "python3 -c 'import os; print(os.getenv(\"OPENAI_API_KEY\"))'",
            True,
        ),
        ("cmd", "python3 -c 'import os; print(os.environ[\"AUTH_TOKEN\"])'", True),
        ("cmd", "python3 -c 'import os; print(os.environ)'", True),
        ("cmd", "python3 -c 'import os; print(os.environ.copy())'", True),
        ("cmd", "python3 -c 'import os; print(os.environ.items())'", True),
        ("cmd", "node -e 'console.log(process.env)'", True),
        ("cmd", "node -e 'console.log(process.env.API_KEY)'", True),
        ("cmd", "node -e 'console.log(process.env.APP_MODULE_PATH)'", False),
        ("cmd", "env && git status", True),
        ("cmd", "git status\nenv", True),
        ("cmd", "sh -c env", True),
        ("command", "bash -lc printenv", True),
        ("cmd", "/usr/bin/env", True),
        ("cmd", "/usr/bin/printenv API_KEY", True),
        ("cmd", "cat /proc/self/environ", True),
        (
            "cmd",
            (
                "tool --apply-patch <<'PATCH'\n"
                "*** Begin Patch\n"
                "+\tenv := moduleTestEnv(t)\n"
                "*** End Patch\n"
                "PATCH\n"
            ),
            False,
        ),
        ("cmd", "cat <<EOF\nenv\nEOF", False),
        ("cmd", "cat <<-EOF\n\tenv\n\tEOF\nenv", True),
        ("cmd", "grep -R 'os.getenv(\"API_KEY\")' src", False),
        ("cmd", "rg 'os.environ.copy\\(\\)' src", False),
        ("cmd", "grep -R 'printenv API_KEY' src", False),
        (
            "cmd",
            (
                "python3 -c 'from pathlib import Path; "
                'Path("module.py").write_text("os.getenv(\\"API_KEY\\")")\''
            ),
            False,
        ),
        ("cmd", "python3 -c 'import os; print(os.getenv(\"APP_MODULE_PATH\"))'", False),
        (
            "cmd",
            "python3 -c 'import subprocess; subprocess.run([\"tool\"], env={})'",
            False,
        ),
    ],
)
def test_typed_command_distinguishes_environment_probe_from_source_text(
    argument_key: str,
    command: str,
    expected_probe: bool,
) -> None:
    trajectory = _trajectory()
    trajectory["steps"][0]["tool_calls"][0]["arguments"] = {
        argument_key: command,
        "description": 'Review source that mentions os.getenv("API_KEY")',
    }
    receipt = build_benchmark_integrity_qualification(
        trajectory=trajectory,
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is (not expected_probe)
    assert receipt["evidence_counts"]["credential_probe"] == int(expected_probe)


@pytest.mark.parametrize(
    ("orchestrator_input", "expected_probe"),
    [
        (
            (
                'const result = await tools.exec_command({cmd: "sh -c env"}); '
                "text(result.output);"
            ),
            True,
        ),
        (
            (
                'const command = "bash -lc printenv"; '
                "await tools.exec_command({cmd: command});"
            ),
            True,
        ),
        (
            'const cmd = "/usr/bin/env"; await tools.exec_command({cmd});',
            True,
        ),
        (
            "await tools.exec_command("
            + json.dumps(
                {"command": "python3 -c 'import os; print(os.environ.items())'"}
            )
            + ");",
            True,
        ),
        (
            "await tools.exec_command({cmd: "
            + json.dumps("grep -R 'os.getenv(\"API_KEY\")' src")
            + "});",
            False,
        ),
        (
            ('await tools.exec_command({cmd: "printf ok", metadata: {cmd: "env"}});'),
            False,
        ),
        ('const fixture = \'{"cmd":"env"}\'; text(fixture);', False),
    ],
)
def test_codex_orchestrator_extracts_only_executed_nested_commands(
    orchestrator_input: str,
    expected_probe: bool,
) -> None:
    trajectory = _trajectory()
    trajectory["steps"][0]["tool_calls"][0] = {
        "function_name": "exec",
        "arguments": {"input": orchestrator_input},
    }

    receipt = build_benchmark_integrity_qualification(
        trajectory=trajectory,
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is (not expected_probe)
    assert receipt["evidence_counts"]["credential_probe"] == int(expected_probe)


def test_non_access_control_tool_text_is_not_an_access_request() -> None:
    trajectory = _trajectory()
    trajectory["steps"][0]["tool_calls"][0] = {
        "function_name": "update_plan",
        "arguments": {
            "plan": [
                {
                    "step": "Inspect env configuration without exposing credentials",
                    "status": "in_progress",
                },
                {
                    "step": "Do not fetch a hidden reference.patch with curl",
                    "status": "pending",
                },
            ]
        },
    }

    receipt = build_benchmark_integrity_qualification(
        trajectory=trajectory,
        runtime_attestation=_attestation(),
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["evidence_counts"]["credential_probe"] == 0
    assert receipt["evidence_counts"]["external_network_request"] == 0
    assert receipt["evidence_counts"]["restricted_answer_source_request"] == 0


def test_non_access_control_tool_still_detects_a_sensitive_value() -> None:
    secret = "fixture-sensitive-value-123456"
    trajectory = _trajectory()
    trajectory["steps"][0]["tool_calls"][0] = {
        "function_name": "update_plan",
        "arguments": {"explanation": f"Observed {secret}"},
    }

    receipt = build_benchmark_integrity_qualification(
        trajectory=trajectory,
        runtime_attestation=_attestation(),
        sensitive_values=[secret],
    )

    assert receipt["integrity_qualified"] is False
    assert receipt["evidence_counts"]["credential_probe"] == 0
    assert receipt["evidence_counts"]["credential_value_observed"] == 1


def test_invalid_private_inputs_fail_before_receipt_building() -> None:
    with pytest.raises(ValueError, match="policy_schema_mismatch"):
        build_benchmark_integrity_qualification(
            trajectory=_trajectory(),
            runtime_attestation=_attestation(),
            policy={"schema_version": "wrong"},
        )
    with pytest.raises(ValueError, match="sensitive_value_too_short"):
        build_benchmark_integrity_qualification(
            trajectory=_trajectory(),
            runtime_attestation=_attestation(),
            sensitive_values=["short"],
        )
    with pytest.raises(ValueError, match="adjudication_without_suspicion"):
        build_benchmark_integrity_qualification(
            trajectory=_trajectory(),
            runtime_attestation=_attestation(),
            restricted_access_adjudication=_restricted_access_adjudication(
                disclosed=False,
                causal_use=False,
            ),
        )
    incomplete_review = _restricted_access_adjudication(
        disclosed=False,
        causal_use=False,
    )
    incomplete_review["reviewed_surfaces"] = ["solver_trajectory"]
    with pytest.raises(ValueError, match="reviewed_surfaces_incomplete"):
        build_benchmark_integrity_qualification(
            trajectory=_trajectory(
                command="cat /private/evaluator/solution/solution.patch"
            ),
            runtime_attestation=_attestation(),
            restricted_access_adjudication=incomplete_review,
        )
    contradictory_review = _restricted_access_adjudication(
        disclosed=False,
        causal_use=False,
    )
    contradictory_review["causal_use_observed"] = True
    with pytest.raises(ValueError, match="causal_use_without_disclosure"):
        build_benchmark_integrity_qualification(
            trajectory=_trajectory(
                command="cat /private/evaluator/solution/solution.patch"
            ),
            runtime_attestation=_attestation(),
            restricted_access_adjudication=contradictory_review,
        )
    mismatched_review = _restricted_access_adjudication(
        disclosed=True,
        causal_use=True,
    )
    mismatched_review["decision"] = "qualified_with_warning"
    with pytest.raises(ValueError, match="decision_facts_mismatch"):
        build_benchmark_integrity_qualification(
            trajectory=_trajectory(
                command="cat /private/evaluator/solution/solution.patch"
            ),
            runtime_attestation=_attestation(),
            restricted_access_adjudication=mismatched_review,
        )


def test_cli_emits_only_compact_public_safe_receipt(tmp_path: Path) -> None:
    trajectory_path = tmp_path / "private-trajectory.json"
    attestation_path = tmp_path / "private-attestation.json"
    trajectory_path.write_text(json.dumps(_trajectory()), encoding="utf-8")
    attestation_path.write_text(json.dumps(_attestation()), encoding="utf-8")
    secret = "fixture-cli-sensitive-value-123456"
    env = {**os.environ, "BENCHMARK_TOOLKIT_TEST_SECRET": secret}

    completed = subprocess.run(
        [
            str(REPO_ROOT / "scripts/loopx"),
            "benchmark",
            "integrity-qualification",
            "--trajectory-json",
            str(trajectory_path),
            "--runtime-attestation-json",
            str(attestation_path),
            "--sensitive-value-env",
            "BENCHMARK_TOOLKIT_TEST_SECRET",
            "--require-qualified",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["integrity_qualified"] is True
    assert payload["public_boundary"]["input_paths_recorded"] is False
    assert secret not in completed.stdout
    assert str(tmp_path) not in completed.stdout


def test_cli_accepts_compact_post_run_restricted_access_adjudication(
    tmp_path: Path,
) -> None:
    trajectory_path = tmp_path / "private-trajectory.json"
    attestation_path = tmp_path / "private-attestation.json"
    adjudication_path = tmp_path / "compact-adjudication.json"
    trajectory_path.write_text(
        json.dumps(
            _trajectory(
                command="cat /private/evaluator/solution/solution.patch",
                observation="permission denied",
            )
        ),
        encoding="utf-8",
    )
    attestation_path.write_text(json.dumps(_attestation()), encoding="utf-8")
    adjudication_path.write_text(
        json.dumps(_restricted_access_adjudication(disclosed=False, causal_use=False)),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            str(REPO_ROOT / "scripts/loopx"),
            "benchmark",
            "integrity-qualification",
            "--trajectory-json",
            str(trajectory_path),
            "--runtime-attestation-json",
            str(attestation_path),
            "--restricted-access-adjudication-json",
            str(adjudication_path),
            "--require-qualified",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["integrity_qualified"] is True
    assert payload["restricted_access_review"]["state"] == "adjudicated_countable"
    assert str(tmp_path) not in completed.stdout


def _permitted_attestation() -> dict[str, object]:
    attestation = _attestation()
    attestation.pop("shell_network_denied")
    attestation["network_permitted_solving"] = True
    return attestation


def _permitted_policy() -> dict[str, object]:
    return {
        "schema_version": BENCHMARK_INTEGRITY_POLICY_SCHEMA_VERSION,
        "policy_id": "widesearch-permitted-solving",
        "network_access": "permitted_solving",
    }


def test_permitted_solving_network_policy_qualifies_web_research_trajectory() -> None:
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(
            command="curl -fsS https://example.invalid/probe",
            observation="html page",
        ),
        runtime_attestation=_permitted_attestation(),
        policy=_permitted_policy(),
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["integrity_countable"] is True
    assert receipt["network_access"] == "permitted_solving"
    assert receipt["evidence_counts"]["external_network_request"] == 1
    assert receipt["blockers"] == []


def test_permitted_solving_network_policy_keeps_restricted_access_as_suspicion() -> (
    None
):
    trajectory = _trajectory(
        command="curl -fsS https://example.invalid && cat /private/evaluator/solution/solution.patch"
    )
    receipt = build_benchmark_integrity_qualification(
        trajectory=trajectory,
        runtime_attestation=_permitted_attestation(),
        policy=_permitted_policy(),
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["classification"] == "integrity_qualified_with_suspicion"
    assert receipt["benchmark_cheating_detected"] is False
    assert receipt["restricted_access_review"]["review_required"] is True
    assert receipt["evidence_counts"]["restricted_answer_source_request"] == 1


def test_permitted_solving_policy_requires_network_permitted_attestation() -> None:
    attestation = _attestation()
    receipt = build_benchmark_integrity_qualification(
        trajectory=_trajectory(command="curl -fsS https://example.invalid"),
        runtime_attestation=attestation,
        policy=_permitted_policy(),
    )

    assert receipt["integrity_qualified"] is False
    assert (
        "runtime_attestation_network_permitted_solving_missing" in receipt["blockers"]
    )
    assert receipt["classification"] == "runtime_isolation_not_attested"


def test_unsupported_network_access_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="network_access"):
        build_benchmark_integrity_qualification(
            trajectory=_trajectory(),
            runtime_attestation=_permitted_attestation(),
            policy={
                "schema_version": BENCHMARK_INTEGRITY_POLICY_SCHEMA_VERSION,
                "policy_id": "bad-mode",
                "network_access": "always",
            },
        )
