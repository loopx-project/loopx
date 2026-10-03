from __future__ import annotations

from typing import Any

from .four_arm_contract import (
    BENCHMARK_FOUR_ARM_ATTESTATIONS,
    BENCHMARK_FOUR_ARM_RUNNER_OBLIGATIONS,
)

BENCHMARK_TOOLKIT_CATALOG_ENTRY: dict[str, Any] = {
    "id": "benchmark-toolkit",
    "origin": "builtin",
    "visibility": "public",
    "provider_id": "loopx-core",
    "documentation": {
        "source_root": "loopx/capabilities/benchmark_toolkit",
        "site_root": "capabilities/benchmark-toolkit",
        "canonical": "README.md",
    },
    "title": "Benchmark experiment toolkit",
    "status": "active-preview",
    "real_world_anchor": (
        "provider-neutral permission, integrity, and post-run analysis boundaries "
        "for benchmark experiments"
    ),
    "user_value": (
        "Keep solving agents answer-denied while giving post-run analysts enough "
        "evidence and structure to explain benchmark outcomes."
    ),
    "entry_command": "loopx benchmark --help",
    "workflow_skill": {
        "name": "loopx-benchmark",
        "delivery": "packaged_host_skill",
        "activation": "task_triggered",
        "goal_switch_required": False,
        "project_copy_required": False,
        "install_command": "loopx workflow-skills --install",
        "readback_command": "loopx doctor",
        "authority_boundary": (
            "Skill discovery does not grant runner, shell, network, credential, "
            "private-evidence, or Goal mutation authority; todo requirements, "
            "provider bindings, and host permissions remain authoritative."
        ),
    },
    "commands": [
        {
            "command": (
                "loopx benchmark four-arm-contract "
                "--spec-json <four-arm-spec.json> "
                "--require-qualified --format json"
            ),
            "purpose": (
                "Freeze a Goal/LoopX by plain/domain-hint factorial design and "
                "prove task-goal prompt parity within each matched pair."
            ),
            "write_boundary": (
                "read-only local spec; default receipt contains prompt hashes but "
                "not prompt text, qualifies design only, and grants no runner or "
                "startup authority"
            ),
        },
        {
            "command": (
                "loopx benchmark source-revision-fence "
                "--source-checkout <clean-source> "
                "--expected-revision <pin> "
                "--observed-reference-revision <observed-head> "
                "--require-admitted --format json"
            ),
            "purpose": (
                "Fail closed before a new run when the installed source no "
                "longer matches the caller-observed reference head."
            ),
            "write_boundary": (
                "local Git readback only; caller owns reference observation, "
                "fetch, installation, and launch"
            ),
        },
        {
            "command": (
                "loopx benchmark continuation-decision "
                "--progress-json <public-progress.json> "
                "--expected-first-prompt-sha256 <sha256> "
                "--observed-first-prompt-sha256 <sha256> "
                "--expected-total-unit-count <n> "
                "--previous-completed-unit-count <n> "
                "--completed-segment-count <n> --max-agent-segments <n> "
                "--elapsed-ms <ms> --total-budget-ms <ms> --format json"
            ),
            "purpose": (
                "Choose a bounded next agent segment from public progress while "
                "preserving first-prompt parity and the total time budget."
            ),
            "write_boundary": (
                "read-only decision; caller owns progress observation, process "
                "lifecycle, continuation prompt construction, and evidence capture"
            ),
        },
        {
            "command": (
                "loopx benchmark experiment-board-show --goal-id <goal-id> "
                "[--four-arm-contract-json <compact-contract.json>] --format json"
            ),
            "purpose": (
                "Read baseline, treatment, explore, countability, effort, and "
                "insight status before selecting or launching another arm; an "
                "explicit four-arm contract also projects conditional effects."
            ),
            "write_boundary": "read-only project-local public-safe domain state",
        },
        {
            "command": (
                "loopx benchmark runtime-observation --admission-active "
                "--job-receipt-state resolved --runner-owner-state alive "
                "--require-healthy --format json"
            ),
            "purpose": (
                "Classify exact-job runtime evidence without treating an active "
                "admission-ledger row as proof of process liveness."
            ),
            "write_boundary": (
                "read-only compact provider facts; caller owns discovery, startup "
                "grace, terminal writes, reconciliation, and slot release"
            ),
        },
        {
            "command": (
                "loopx benchmark runtime-continuity "
                "--launch-runtime-digest <sha256> "
                "--closeout-runtime-digest <sha256> "
                "--launch-generation-digest <sha256> "
                "--closeout-generation-digest <sha256> "
                "--event-window-state <state> --require-qualified --format json"
            ),
            "purpose": (
                "Fail closed unless terminal closeout retains the launch runtime "
                "artifact, attempt generation, and qualified event window."
            ),
            "write_boundary": (
                "read-only compact provider facts; emits no digest, run identity, "
                "event payload, or path, and performs no closeout write"
            ),
        },
        {
            "command": (
                "loopx benchmark experiment-board-upsert --goal-id <goal-id> "
                "--row-json <compact-row.json> --execute --format json"
            ),
            "purpose": (
                "Idempotently record one planned, running, or terminal compact run "
                "row, including optional provider-neutral orchestrator runtime "
                "revision provenance; omit --execute for a no-write preview."
            ),
            "write_boundary": (
                "explicit project-local domain-state write; rejects raw paths, "
                "logs, trajectories, hidden evaluation, credentials, and unknown fields"
            ),
        },
        {
            "command": (
                "loopx benchmark experiment-board-reconcile --goal-id <goal-id> "
                "--source-ledger <provider-board.jsonl> --execute --format json"
            ),
            "purpose": (
                "Monotonically fold one or more provider-owned public-safe board "
                "shards into the canonical project experiment board; omit --execute "
                "for a no-write preview."
            ),
            "write_boundary": (
                "explicit project-local canonical board write; source paths are "
                "never recorded and conflicting terminal states fail closed"
            ),
        },
        {
            "command": (
                "loopx benchmark concurrency-configure --goal-id <goal-id> "
                "--max-active-cases <n> --target-active-cases <n> "
                "--max-baseline-cases <n> --max-test-cases <n> "
                "--reserved-test-cases <n> "
                "[--require-resource-headroom-receipt] --execute --format json"
            ),
            "purpose": (
                "Configure one combined capacity envelope across baseline, control, "
                "treatment, and explore runs."
            ),
            "write_boundary": (
                "explicit project-local admission state; grants no runner or model "
                "launch authority"
            ),
        },
        {
            "command": (
                "loopx benchmark concurrency-admit --goal-id <goal-id> "
                "--run-id <run-id> --case-id <case-id> --arm-role <role> "
                "[--resource-headroom-json <receipt.json>] "
                "--execute --format json"
            ),
            "purpose": (
                "Atomically reserve a baseline or test-group slot immediately "
                "before an independently authorized runner launch."
            ),
            "write_boundary": (
                "compact project-local run identities only; resource receipts and "
                "raw metrics are not persisted, and launch, liveness, and termination "
                "remain runner-owned"
            ),
        },
        {
            "command": (
                "loopx benchmark concurrency-tune --goal-id <goal-id> "
                "--feedback-json <feedback.json> "
                "--resource-headroom-json <receipt.json> "
                "[--execute] --format json"
            ),
            "purpose": (
                "Adapt desired occupancy inside the operator-owned hard ceiling "
                "from compact runner health and resource headroom."
            ),
            "write_boundary": (
                "preview by default; execute changes only target occupancy, never "
                "hard or role caps, active runs, raw metrics, or launch authority"
            ),
        },
        {
            "command": (
                "loopx benchmark integrity-qualification "
                "--trajectory-json <private.json> "
                "--runtime-attestation-json <attestation.json> "
                "[--restricted-access-adjudication-json <compact.json>] "
                "--require-qualified --format json"
            ),
            "purpose": (
                "Reduce private ATIF tool evidence and runner-owned isolation "
                "facts to a compact integrity qualification receipt; scanner "
                "suspicion remains eligible unless post-run causal review confirms "
                "restricted disclosure and use."
            ),
            "write_boundary": (
                "read-only private local inputs; emits hashes, counts, and "
                "reason codes only"
            ),
        },
        {
            "command": (
                "loopx benchmark traex-evidence "
                "--source-jsonl <private-stdout.jsonl> "
                "--route-source-jsonl <private-session.jsonl> "
                "--atif-output <private-trajectory.json> "
                "--route-receipt-output <public-route.json> "
                "--requested-model <model> --execute --format json"
            ),
            "purpose": (
                "Convert TraeX stdout or archived tool events into private ATIF "
                "and independently reduce archived runtime route evidence."
            ),
            "write_boundary": (
                "writes caller-selected local evidence files only; the command "
                "receipt and route receipt omit prompts, tool content, and paths"
            ),
        },
        {
            "command": (
                "loopx benchmark treatment-continuation-receipt "
                "--observation-json <compact-post-run-observation.json> "
                "--format json"
            ),
            "purpose": (
                "Separate qualified treatment startup from post-start semantic "
                "control persistence and terminal settlement."
            ),
            "write_boundary": (
                "read-only compact post-run mechanism facts; changes no score, "
                "countability, integrity, or treatment-fidelity decision"
            ),
        },
        {
            "command": "loopx benchmark classify-artifacts <paths...> --format json",
            "purpose": "Classify benchmark artifacts before reading or publishing them.",
            "write_boundary": "path classification only; no file reads or writes",
        },
        {
            "command": (
                "loopx benchmark verify-verifier-reward <reward.json> "
                "--require-valid --format json"
            ),
            "purpose": (
                "Validate a verifier reward.json against the numeric-only "
                "verifier reward contract (pier VerifierResult compatible)."
            ),
            "write_boundary": (
                "read-only local reward file; emits stable counts, labels, and "
                "reason codes only, never raw reward values"
            ),
        },
        {
            "command": (
                "loopx benchmark study-validate "
                "--manifest-json <study-manifest.json> --format json"
            ),
            "purpose": (
                "Validate provider-neutral case, arm, factor, native-metric, and "
                "source-revision intent before producing upload records."
            ),
            "write_boundary": (
                "read-only local manifest validation; grants no runner, scorer, "
                "network, upload, or publication authority"
            ),
        },
        {
            "command": (
                "loopx benchmark upload-envelope "
                "--payload-json <public-safe-record.json> "
                "--record-kind <allowlisted-kind> --producer-id <adapter-id> "
                "--producer-version <version> --benchmark-id <benchmark-id> "
                "--study-id <study-id> --idempotency-key <stable-key> "
                "--observed-at <timestamp> --source-revision <revision> "
                "--format json"
            ),
            "purpose": (
                "Normalize one allowlisted record and bind it to stable producer, "
                "study, idempotency, source, and payload-digest identity."
            ),
            "write_boundary": (
                "read-only envelope construction; accepts no credentials and "
                "performs no local or external write"
            ),
        },
        {
            "command": (
                "loopx benchmark upload-local "
                "--envelope-json <public-safe-envelope.json> "
                "--store <simulation.jsonl> [--execute] --format json"
            ),
            "purpose": (
                "Preview or locally simulate digest-bound upload and readback "
                "before implementing an independently authorized provider."
            ),
            "write_boundary": (
                "preview by default; execute writes only the caller-selected local "
                "JSONL simulation store and performs no network or external write"
            ),
        },
        {
            "command": (
                "loopx benchmark study-dashboard "
                "--manifest-json <study-manifest.json> "
                "--store <simulation.jsonl> "
                "[--four-arm-contract-json <compact-contract.json>] --format json"
            ),
            "purpose": (
                "Derive campaign, arm, case, run, matched-pair, and optional "
                "factorial projections with explicit denominators."
            ),
            "write_boundary": (
                "read-only local reducer; experiment-board and factorial contracts "
                "remain the scoring and comparison authorities"
            ),
        },
        {
            "command": (
                "loopx benchmark upload-readback --store <simulation.jsonl> "
                "--record-id <record-id> --format json"
            ),
            "purpose": (
                "Verify the exact record id, payload digest, provider revision, "
                "and supersession state in the local simulation."
            ),
            "write_boundary": (
                "read-only local receipt; returns no store path and performs no "
                "network or external write"
            ),
        },
        {
            "command": "loopx capability show benchmark-toolkit --format json",
            "purpose": (
                "Read the post-run analyst hint and benchmark_case_insight_v0 "
                "artifact template."
            ),
            "write_boundary": (
                "read-only catalog; private analysis evidence remains outside "
                "the solving agent and public repository"
            ),
        },
    ],
    "agent_usage": {
        "benchmark_start_hint": (
            "At benchmark start, read the experiment board and concurrency envelope "
            "before selecting or launching a case; update the same stable board row "
            "and capacity reservation at every material run transition. "
            "Providers that keep separate board shards reconcile them "
            "into the canonical board before agent readback."
        ),
        "required_sequence": [
            "read_experiment_board_before_launch_or_case_selection",
            "qualify_four_arm_contract_when_domain_guidance_is_a_factor",
            "configure_or_read_concurrency_envelope_before_launch",
            "reconcile_admission_ledger_with_real_runner_liveness",
            "backfill_when_concurrency_status_reports_underfilled",
            "qualify_source_revision_before_each_new_run_admission",
            "qualify_resource_headroom_when_the_envelope_requires_a_receipt",
            "tune_target_occupancy_from_fresh_runner_feedback_when_opted_in",
            "atomically_admit_case_slot_before_runner_launch",
            "upsert_preregistered_or_running_row_when_a_run_starts",
            "classify_exact_runtime_observation_during_active_monitor_cycles",
            "require_runtime_continuity_before_terminal_closeout_write",
            "qualify_each_agent_segment_receipt_before_using_its_result",
            "adjudicate_restricted_access_suspicion_after_solver_and_score_terminal",
            "upsert_terminal_score_countability_effort_and_insight_status",
            "release_case_slot_after_terminal_or_runner_invalid_transition",
            "read_matched_comparisons_before_selecting_the_next_arm",
        ],
        "board_commands": {
            "read": (
                "loopx benchmark experiment-board-show --goal-id <goal-id> "
                "[--four-arm-contract-json <compact-contract.json>] --format json"
            ),
            "preview": (
                "loopx benchmark experiment-board-upsert --goal-id <goal-id> "
                "--row-json <compact-row.json> --format json"
            ),
            "write": (
                "loopx benchmark experiment-board-upsert --goal-id <goal-id> "
                "--row-json <compact-row.json> --execute --format json"
            ),
            "reconcile": (
                "loopx benchmark experiment-board-reconcile --goal-id <goal-id> "
                "--source-ledger <provider-board.jsonl> --execute --format json"
            ),
        },
        "concurrency_commands": {
            "status": (
                "loopx benchmark concurrency-status --goal-id <goal-id> --format json"
            ),
            "configure": (
                "loopx benchmark concurrency-configure --goal-id <goal-id> "
                "--max-active-cases <n> --target-active-cases <n> "
                "--max-baseline-cases <n> --max-test-cases <n> "
                "--reserved-test-cases <n> "
                "[--require-resource-headroom-receipt] --execute --format json"
            ),
            "admit": (
                "loopx benchmark concurrency-admit --goal-id <goal-id> "
                "--run-id <run-id> --case-id <case-id> --arm-role <role> "
                "[--resource-headroom-json <receipt.json>] "
                "--execute --format json"
            ),
            "release": (
                "loopx benchmark concurrency-release --goal-id <goal-id> "
                "--run-id <run-id> --execute --format json"
            ),
            "tune": (
                "loopx benchmark concurrency-tune --goal-id <goal-id> "
                "--feedback-json <feedback.json> "
                "--resource-headroom-json <receipt.json> "
                "[--execute] --format json"
            ),
        },
        "concurrency_boundary": (
            "The envelope owns capacity admission only. A benchmark runner remains "
            "responsible for resource observation, launch, liveness, termination, "
            "and score transitions. When configured, admission requires a fresh "
            "public-safe resource-headroom receipt but never persists raw metrics."
        ),
        "selection_rule": (
            "Keep diagnostic-only explore rows separate and make paired claims "
            "only from matched_pair_countable comparisons."
        ),
        "study_projection_workflow": {
            "sequence": [
                "validate_provider_neutral_study_manifest",
                "wrap_one_allowlisted_public_safe_record_per_envelope",
                "upload_exact_terminal_run_before_its_redacted_case_insight",
                "preview_local_upload_without_writing",
                "execute_local_simulation_when_explicitly_requested",
                "verify_record_id_digest_and_provider_revision_by_readback",
                "derive_read_only_dashboard_packet",
            ],
            "case_insight_upload": {
                "schema_version": "benchmark_case_insight_projection_v0",
                "precondition": (
                    "one active exact-run board record is terminal, has complete "
                    "post-run analysis status, and matches case/run/outcome identity"
                ),
                "authority_boundary": (
                    "the projection contains no score, countability, integrity, or "
                    "treatment-fidelity fields and cannot mutate run authority"
                ),
                "evidence_boundary": (
                    "only bounded redacted analysis and public-safe evidence handles "
                    "or digests; raw trajectory and evaluator material stay private"
                ),
            },
            "external_provider_boundary": (
                "The local simulation proves contract and readback behavior only. "
                "Authentication, remote transport, retention, and publication need "
                "a separately activated provider and explicit authority."
            ),
        },
    },
    "four_arm_study": {
        "benchmark_start_hint": (
            "When a benchmark-specific solver hint may affect outcomes, preregister "
            "goal_plain, loopx_plain, goal_<hint-id>, and loopx_<hint-id>; keep "
            "guided startup out of the task prompt and require equal prompt hashes "
            "within each Goal/LoopX pair."
        ),
        "factors": {
            "loopx": [False, True],
            "domain_hint": [False, True],
        },
        "attestations": list(BENCHMARK_FOUR_ARM_ATTESTATIONS),
        "runner_obligations": list(BENCHMARK_FOUR_ARM_RUNNER_OBLIGATIONS),
        "historical_boundary": (
            "An arm that mixed startup and domain guidance remains diagnostic-only "
            "and cannot be relabeled into the factorial study."
        ),
    },
    "concurrency_occupancy": {
        "benchmark_start_hint": (
            "When starting a benchmark, add one continuous_monitor todo that "
            "reconciles the admission ledger with exact runner liveness and "
            "backfills any safe target-occupancy gap."
        ),
        "monitor_todo_template": {
            "task_class": "continuous_monitor",
            "action_kind": "benchmark_concurrency_occupancy_monitor",
            "trigger": "launch_terminal_invalid_or_periodic_liveness_transition",
            "text": (
                "Classify exact-job receipt and runner-owner facts through the typed "
                "runtime observation on each launch, terminal or runner-invalid "
                "transition and a bounded cadence. Apply its terminal or runner-invalid "
                "transition before releasing that reservation; then read "
                "concurrency-status and, when launch authority already exists, admit "
                "and start up to missing_cases without exceeding caps."
            ),
        },
        "authority_boundary": (
            "The monitor may launch replacements only under separately granted "
            "runner authority; the envelope grants no model, verifier, upload, or "
            "submission authority."
        ),
    },
    "campaign_monitor_handoff": {
        "lane_boundary": (
            "A continuous_monitor observes and records campaign transitions; "
            "repository delivery, runner repair, experiment redesign, and other "
            "bounded work belong in an independent advancement_task."
        ),
        "material_transition_command": (
            "loopx quota monitor-poll --goal-id <goal-id> "
            "--todo-id <monitor-todo-id> --agent-id <registered-agent> "
            "--result-hash <public-safe-hash> --material-change "
            '--next-agent-todo "<bounded public-safe work>" '
            "--next-action-kind <action-kind> "
            "--next-task-repository <git-repository> "
            "--next-required-capability <capability> --execute --format json"
        ),
        "material_transition_result": {
            "monitor_status": "open",
            "successor_task_class": "advancement_task",
            "successor_relationship": "independent_runnable_work",
            "quota_spend": False,
        },
        "external_wait_command": (
            "loopx todo update --goal-id <goal-id> "
            "--todo-id <waiting-advancement-todo-id> "
            "--agent-id <registered-agent> --status open "
            "--resume-when monitor_changed:<monitor-todo-id> "
            "--successor-todo-id <independent-runnable-successor-id> "
            '--reason "<public-safe external-wait rationale>" --format json'
        ),
        "external_wait_result": (
            "Keep the waiting advancement Todo visible but non-runnable until the "
            "monitor generation advances. Do not mark it blocked, and do not use "
            "the monitor itself as executable delivery work."
        ),
        "unchanged_policy": (
            "An unchanged poll records monitor evidence only; it creates no "
            "successor and spends no delivery quota."
        ),
    },
    "post_run_case_analysis": {
        "benchmark_start_hint": (
            "When starting a benchmark, add one continuous_monitor todo that "
            "refreshes aggregate score coverage and runs the post-run analyst brief "
            "on every material scored-case transition, with bounded periodic reviews "
            "while the campaign is active."
        ),
        "monitor_todo_template": {
            "task_class": "continuous_monitor",
            "action_kind": "benchmark_case_insight_monitor",
            "trigger": "material_scored_case_transition",
            "active_campaign_review": "bounded_periodic",
            "delivery_contract": {
                "catalog_role": "guidance_template",
                "creation_owner": "benchmark_startup_provider",
                "scheduler_owner": "registered_monitor_runtime",
            },
            "text": (
                "On each material scored-case transition and bounded active-campaign "
                "review, validate exact-job runtime authority and advance at least one "
                "bounded solver-trajectory slice even when no case became terminal; "
                "refresh the public-safe aggregate score and coverage summary, report "
                "material changes to the user, and after solver termination read the "
                "complete private evaluation evidence and write one "
                "benchmark_case_insight_v0."
            ),
        },
        "aggregate_reporting": {
            "score_source": "experiment_board_public_safe_projection",
            "insight_boundary": (
                "report only public-safe conclusions from post-run insight; never "
                "copy raw private evidence into the user update"
            ),
            "report_on": [
                "new_countable_terminal",
                "countability_or_pairing_change",
                "aggregate_score_or_direction_change",
                "new_reusable_case_insight",
                "systematic_runner_or_treatment_fidelity_issue",
            ],
            "report_fields": [
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
            ],
            "effort_stratification": {
                "default_reference_arm": "baseline",
                "default_reference_field": "effort.duration_ms",
                "candidate_duration_affects_bucket": False,
                "interpretation": "descriptive_sensitivity_only",
                "report_per_stratum": [
                    "matched_pair_count",
                    "binary_outcome_by_arm_when_available",
                    "aggregate_primary_metric_by_arm",
                    "feature_metric_by_arm_when_available",
                    "preservation_guardrail_by_arm_when_available",
                    "improved_flat_regressed_pair_counts",
                ],
                "boundary_policy": (
                    "Preregister benchmark-appropriate fixed boundaries before "
                    "reading candidate durations. Use the same baseline-side case "
                    "bucket for every arm in a matched comparison; never define "
                    "difficulty from treatment duration."
                ),
            },
            "unchanged_policy": (
                "Do not send a repetitive user update when no score, coverage, "
                "direction, insight, or material runner state changed."
            ),
        },
        "treatment_continuation_receipt": {
            "command": (
                "loopx benchmark treatment-continuation-receipt "
                "--observation-json <compact-post-run-observation.json> "
                "--format json"
            ),
            "classifications": [
                "sustained",
                "startup_only",
                "unknown",
                "not_applicable",
            ],
            "analysis_boundary": (
                "The receipt describes mechanism persistence only. It does not "
                "change score countability, integrity qualification, treatment "
                "fidelity, or the experiment-board matched-pair decision."
            ),
            "absence_rule": (
                "Classify startup_only only after a complete authorized post-run "
                "review observes qualified startup and zero post-start semantic "
                "control transitions; otherwise absence remains unknown."
            ),
            "persistence_rule": (
                "Only task-facing Todo transitions and technical replans that "
                "change the solving course before the result is fixed establish "
                "sustained control. Terminal-only Todo settlement, replan "
                "bookkeeping, and closeout remain recorded as closeout events but "
                "do not establish persistence."
            ),
            "observation_template": {
                "schema_version": (
                    "benchmark_treatment_continuation_observation_v0"
                ),
                "treatment_applicable": "<true-or-false>",
                "startup_state": (
                    "<qualified-not_qualified-unknown-or-not_applicable>"
                ),
                "observation_complete": "<true-or-false>",
                "post_start_control_events": {
                    "todo_transition_count": "<non-negative-integer>",
                    "technical_replan_count": "<non-negative-integer>",
                    "control_closeout_count": "<non-negative-integer>",
                },
                "terminal_control_state": (
                    "<settled-unsettled-unknown-or-not_applicable>"
                ),
                "precommit_validation_state": (
                    "<observed-not_observed-unknown-or-not_applicable>"
                ),
            },
            "score_semantics": "unchanged",
        },
        "active_progress_readback": {
            "workspace_basis": [
                "recorded_start_revision_to_current_head",
                "current_worktree_status",
            ],
            "runtime_basis": [
                "active_admission_ledger",
                "exact_job_runtime_receipt",
                "exact_runner_owner_liveness_after_startup_grace",
                "terminal_result_presence",
                "goal_state_and_event_freshness",
                "typed_runner_error_category",
            ],
            "runtime_contract": (
                "Admission-ledger occupancy is not liveness. A healthy active run "
                "requires a resolved exact-job receipt and a live exact runner owner; "
                "terminal or lost-owner observations require provider reconciliation "
                "before slot release. These are provider obligations; the reducer "
                "classifies facts but performs no monitor, write, or release effect."
            ),
            "trajectory_basis": (
                "solver_trajectory_phase_without_hidden_evaluator_feedback"
            ),
            "classification_owner": "benchmark_monitor_provider",
            "stalled_when": {
                "all": [
                    "no_committed_progress",
                    "no_uncommitted_progress",
                ],
                "any": [
                    "trajectory_stale",
                    "typed_fatal_runner_error",
                ],
            },
            "non_signals": [
                "clean_worktree_alone",
                "raw_log_error_count_alone",
            ],
        },
        "active_case_observation": {
            "status": "provisional_only",
            "purpose": (
                "Preserve traceable runtime evidence while a case or campaign is "
                "still active without presenting it as an official scored insight."
            ),
            "artifact_template": {
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
            },
            "score_boundary": (
                "A provisional observation must not invent an official score or "
                "treat request success, progress, or runtime status as case quality."
            ),
            "promotion_rule": (
                "After terminal scoring, re-read the complete authorized evidence "
                "and write a separate benchmark_case_insight_v0; do not relabel the "
                "provisional artifact as final."
            ),
            "privacy_boundary": (
                "Keep raw evidence references in private benchmark storage. The "
                "public experiment board may expose only a compact classification "
                "or private artifact handle, never trace IDs, spans, URLs, paths, "
                "or provider-specific session identifiers."
            ),
        },
        "hint": (
            "After the solver has stopped and scoring is complete, read the task, "
            "real trajectory, final patch or workspace, hidden tests, grader or "
            "verifier, and full failure and score details; explain the decisive "
            "evidence, why the outcome happened, whether it was expected, and what "
            "LoopX should test or change next."
        ),
        "restricted_access_adjudication": {
            "hint": (
                "When the integrity scan marks restricted access as suspected, "
                "inspect the real solver trajectory, tool results, and final "
                "workspace after terminal scoring. Keep the run countable unless "
                "restricted material was actually disclosed and causally entered "
                "a solving or validation decision."
            ),
            "artifact_template": {
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
            },
            "decision_rule": (
                "Only disclosed=true plus causal_use=true may be "
                "confirmed_cheating; all other reviewed suspicion remains "
                "qualified_with_warning."
            ),
        },
        "role_boundary": {
            "solver": (
                "must not access hidden tests, grader or verifier sources, expected "
                "answers, or official feedback during the solving phase"
            ),
            "active_campaign_monitor": (
                "may inspect the solver-owned trajectory and exact-job runtime while "
                "the solver is active, but must not read hidden evaluator evidence or "
                "send findings back into the solving arm"
            ),
            "post_run_analyst": (
                "may read the full private case evidence only after the solver is "
                "terminal and scoring is complete"
            ),
        },
        "artifact_template": {
            "schema_version": "benchmark_case_insight_v0",
            "case": {
                "benchmark_id": "<public-id>",
                "case_id": "<public-id>",
                "arm": "<baseline-or-treatment>",
            },
            "outcome": {
                "status": "<completed-or-runner-invalid>",
                "score": "<official-score-or-null>",
                "countable": "<true-or-false>",
            },
            "evidence_reviewed": [
                "task",
                "real_trajectory",
                "final_patch_or_workspace",
                "hidden_tests",
                "grader_or_verifier",
                "failure_and_score_details",
            ],
            "evidence_refs": [
                {
                    "kind": "<trace-log-artifact-report-or-other>",
                    "trace_id": "<opaque-id-or-null>",
                    "span_id": "<opaque-id-or-null>",
                    "env": "<environment-token-or-null>",
                    "artifact_ref": "<private-pointer-or-null>",
                }
            ],
            "insight": {
                "approach_summary": "<what-the-solver-tried>",
                "decisive_evidence": ["<specific-observation>"],
                "why_this_outcome": "<causal-explanation>",
                "expectedness": "<expected-surprising-mixed-or-unknown>",
                "baseline_treatment_difference": "<difference-or-not-yet-compared>",
                "loopx_implication": "<reusable-product-or-experiment-insight>",
                "next_probe": "<smallest-discriminating-next-step>",
            },
            "confidence": "<high-medium-or-low>",
            "reuse_boundary": (
                "<diagnostic-only-heldout-generalization-or-declared-feedback>"
            ),
        },
    },
    "implemented_protocols": [
        {
            "schema_version": "benchmark_study_manifest_v0",
            "module": "loopx.capabilities.benchmark_toolkit.study_projection",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_upload_envelope_v0",
            "module": "loopx.capabilities.benchmark_toolkit.study_projection",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_upload_readback_receipt_v0",
            "module": "loopx.capabilities.benchmark_toolkit.study_projection",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_case_insight_projection_v0",
            "module": "loopx.capabilities.benchmark_toolkit.study_projection",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_study_dashboard_v0",
            "module": "loopx.capabilities.benchmark_toolkit.study_projection",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_treatment_continuation_receipt_v0",
            "module": "loopx.capabilities.benchmark_toolkit.treatment_continuation",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_public_progress_v0",
            "purpose": (
                "Carry aggregate completed and total unit counts without unit ids, "
                "paths, task text, or verifier output."
            ),
        },
        {
            "schema_version": "benchmark_continuation_decision_v0",
            "purpose": (
                "Choose a bounded continue or stop disposition without invoking "
                "the host, writing state, or owning benchmark lifecycle."
            ),
        },
        {
            "schema_version": "benchmark_four_arm_contract_v0",
            "module": "loopx.capabilities.benchmark_toolkit.four_arm_contract",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_concurrency_envelope_v0",
            "module": "loopx.capabilities.benchmark_toolkit.concurrency_envelope",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_adaptive_concurrency_policy_v0",
            "module": "loopx.capabilities.benchmark_toolkit.adaptive_concurrency",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_concurrency_feedback_v0",
            "module": "loopx.capabilities.benchmark_toolkit.adaptive_concurrency",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_source_revision_fence_v0",
            "module": "loopx.capabilities.benchmark_toolkit.source_revision_fence",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_experiment_board_row_v0",
            "module": "loopx.capabilities.benchmark_toolkit.experiment_board",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_experiment_board_v0",
            "module": "loopx.capabilities.benchmark_toolkit.experiment_board",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_integrity_qualification_v0",
            "module": "loopx.capabilities.benchmark_toolkit.integrity",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_restricted_access_adjudication_v0",
            "module": "loopx.capabilities.benchmark_toolkit.integrity",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_runtime_integrity_attestation_v0",
            "module": "loopx.capabilities.benchmark_toolkit.integrity",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_runtime_observation_v0",
            "module": "loopx.capabilities.benchmark_toolkit.runtime_observation",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_exact_container_binding_v0",
            "module": "loopx.capabilities.benchmark_toolkit.container_binding",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "run_permission_policy_v0",
            "module": "loopx.capabilities.benchmark_toolkit.run_permissions",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_case_observation_v0",
            "module": "loopx.capabilities.benchmark_toolkit.catalog_entry",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
        {
            "schema_version": "benchmark_case_insight_v0",
            "module": "loopx.capabilities.benchmark_toolkit.catalog_entry",
            "doc": "loopx/capabilities/benchmark_toolkit/README.md",
        },
    ],
    "smokes": [
        (
            "python -m pytest tests/capabilities/test_benchmark_toolkit.py "
            "tests/capabilities/test_benchmark_concurrency_envelope.py "
            "tests/capabilities/test_benchmark_experiment_board.py "
            "tests/capabilities/test_benchmark_source_revision_fence.py -q"
        )
    ],
    "docs": ["loopx/capabilities/benchmark_toolkit/README.md"],
    "boundaries": [
        "The four-arm contract controls prompt parity and factor identity only; providers retain launch, guided-startup, task, model, verifier, and score authority.",
        "The toolkit never grants runner, Docker, model, upload, submission, or publication authority; each effect remains separately gated.",
        "Source revision qualification is read-only and caller-observed: it does not fetch, install, update a checkout, or rewrite an already admitted run.",
        "Raw trajectories are private local inputs to integrity qualification and are never copied into receipts, ledgers, docs, or PR artifacts.",
        "A clean trajectory scan is not isolation proof: runner-owned permission and verifier-order attestations are mandatory and fail closed when absent.",
        "Automated restricted-access matches are countable suspicion only; confirmed cheating requires post-run agent evidence of both disclosure and causal use.",
        "Concurrent Docker runs must bind runtime evidence to one exact job-owned container; image-only discovery is not sufficient.",
        "Admission-ledger occupancy never proves runner liveness; active health requires a resolved exact-job receipt and a live exact runner owner after startup grace.",
        "Integrity qualification establishes countability eligibility only; an independent official result and matched experiment contract are still required.",
        "Post-run analyst access never widens the solving agent's evidence boundary or grants feedback reuse in another scored run.",
        "Concurrency reservations are not runtime liveness: runners must reconcile exact processes before release or backfill.",
        "The experiment board is a compact projection, not a score authority: benchmark-family runners and scoring adapters remain outside the active capability.",
    ],
    "next_real_step": (
        "Use the board and concurrency envelope before launch, classify exact runtime "
        "during active monitor cycles, and after each scored case advance one bounded "
        "trajectory review while keeping solver integrity separate from private analysis."
    ),
}
