# Protocol Contracts

These versioned contracts define implementation-facing LoopX behavior. The
files remain flat in this migration to preserve established links; this index
groups them by responsibility so callers can find the right contract without
scanning a chronological list.

## Control Plane And State

- [`active_state_structured_projection_v0`](active-state-structured-projection-v0.md): Active state structured projection v0
- [`decision_scope_v0`](decision-scope-v0.md): Decision scope v0
- [`event_sourced_state_contract_v0`](event-sourced-state-contract-v0.md): Event-sourced state contract v0
- [`event_store_migration_bridge_v0`](event-store-migration-bridge-v0.md): Event store migration bridge v0
- [`file_lock_acquisition_v0`](file-lock-acquisition-v0.md): Bounded file-lock acquisition and operator recovery v0
- [`global_manager_command_v0`](global-manager-command-v0.md): Global manager command v0
- [Manager evidence and continuity v0](manager-evidence-and-continuity-v0.md): proposed coverage, scoped requests, and restart acceptance for the built-in manager
- [`goal_vision_replan_contract_v0`](goal-vision-replan-contract-v0.md): Goal vision replan contract v0
- [`local_state_write_correctness_v0`](local-state-write-correctness-v0.md): Local state write correctness v0
- [`loopx_goal_command_v0`](loopx-goal-command-v0.md): LoopX goal command v0
- [`loopx_turn_v0`](loopx-turn-v0.md): LoopX Turn v0
- [`quota_cli_hot_path_compaction_v0`](quota-cli-hot-path-compaction-v0.md): Quota CLI hot-path compaction v0
- [`quota_planning_horizon_v0`](quota-planning-horizon-v0.md): Bounded agent planning horizon v0
- [`rollback_packet_v0`](rollback-packet-v0.md): Rollback packet v0
- [`task_graph_projection_v0`](task-graph-projection-v0.md): Task graph projection v0
- [`typed_date_resume_trigger_v0`](typed-date-resume-trigger-v0.md): timezone-aware one-shot Todo resume and stable due receipt v0
- [`typed_date_resume_trigger_v0`](typed-date-resume-trigger-v0.zh-CN.md): timezone-aware one-shot Todo resume and stable due receipt v0 (中文)
- [`todo_detail_cold_path_v0`](todo-detail-cold-path-v0.md): Todo detail cold path v0
- [`turn_envelope_v0`](turn-envelope-v0.md): Turn envelope v0
- [`loop_turn_loop_disposition_v0`](turn-loop-controller-v0.md): Loop Turn Loop Disposition v0

## Agent And Multi-Agent Coordination

- [`agent_management_projection_v0`](agent-management-projection-v0.md): Agent management projection v0
- [`agent_material_frontier_v0`](agent-material-frontier-v0.md): Agent material frontier v0
- [`agent_scoped_evidence_ledger_v0`](agent-scoped-evidence-ledger-v0.md): Agent-scoped evidence ledger v0
- [`decision_context_architecture_v0`](decision-context-architecture-v0.md): Decision context architecture v0
- [`decision_context_architecture_v0`](decision-context-architecture-v0.zh-CN.md): Decision context architecture v0 (中文)
- [`long_horizon_agent_state_protocol_v0`](long-horizon-agent-state-protocol-v0.md): Long-horizon agent state protocol v0
- [`material_lifecycle_architecture_v0`](material-lifecycle-architecture-v0.md): Material lifecycle architecture v0
- [`material_lifecycle_architecture_v0`](material-lifecycle-architecture-v0.zh-CN.md): Material lifecycle architecture v0 (中文)
- [`multi_agent_three_layer_minimality_contract_v0`](multi-agent-three-layer-minimality-v0.md): Multi-agent three-layer minimality v0
- [`multi_agent_visible_launcher_v0`](multi-agent-visible-launcher-v0.md): Multi-agent visible launcher v0
- [`peer_agent_directory_v0`](peer-agent-directory-and-observation-v0.md): Peer agent directory, bounded observation and delivery v0
- [`peer_agent_runtime_v1`](peer-agent-runtime-v1.md): Peer agent runtime v1
- [`peer_supervisor_v0`](peer-supervisor-v0.md): Peer supervisor v0
- [`periodic_report_v0`](periodic-report-v0.md): Periodic report v0
- [`review_batch_v0`](review-batch-v0.md): Review batch v0
- [`reward_memory_architecture_v0`](../../../loopx/capabilities/reward_memory/README.md): Reward memory architecture v0
- [`reward_memory_architecture_v0`](../../../loopx/capabilities/reward_memory/README.zh-CN.md): Reward memory architecture v0 (中文)
- [`reward_memory_corpus_registry_v0`](reward-memory-corpus-registry-v0.md): Reward memory corpus registry v0
- [`memory_utility_projection_v0`](reward-memory-utility-projection-v0.md): Reward memory utility projection v0
- [`memory_utility_projection_v0`](reward-memory-utility-projection-v0.zh-CN.md): Reward memory utility projection v0 (中文)
- [`trajectory_hygiene_v0`](trajectory-hygiene-v0.md): Trajectory hygiene v0

## Runtime And Host Integration

- [`ark_managed_agent_goal_continuity_qualification_v0`](ark-managed-agent-goal-continuity-qualification-v0.md): Ark Managed Agent goal continuity qualification v0
- [`ark_managed_agent_issue_fix_qualification_v0`](ark-managed-agent-issue-fix-qualification-v0.md): Ark Managed Agent issue-fix qualification v0
- [`codex_app_host_command_registry_v0`](codex-app-host-command-registry-v0.md): Codex App host command registry v0
- [`decision_context_advisory_provider_v0`](decision-context-advisory-provider-v0.md): extension-backed, read-only advisory recall for Decision Context
- [`computer_use_runtime_v0`](computer-use-runtime-v0.md): Computer-use runtime v0
- [`host_integration_plugin_plan_v0`](host-integration-plugin-plan-v0.md): Host integration plugin plan v0
- [`host_integration_surface_v0`](host-integration-surface-v0.md): Host integration surface v0
- [`host_mode_plan_v0`](host-mode-plan-v0.md): Host mode plan v0
- [`local_agent_launch_plan_v1`](local-agent-launch-plan-v1.md): Local agent launch plan v1
- [`openviking_session_memory_adapter_v0`](openviking-session-memory-adapter-v0.md): OpenViking session memory adapter v0
- [`protocol_action_packet_codex_cli_wrapper_v0`](protocol-action-packet-codex-cli-wrapper-v0.md): Protocol action packet Codex CLI wrapper v0
- [`protocol_action_packet_decision_v0`](protocol-action-packet-decision-v0.md): Protocol action packet decision v0
- [`protocol_action_packet_router_comparison_v0`](protocol-action-packet-router-comparison-v0.md): Protocol action packet router comparison v0
- [`session_runtime_controlled_writeback_v0`](session-runtime-controlled-writeback-v0.md): Session runtime controlled writeback v0
- [`session_runtime_loopx_projection_v0`](session-runtime-loopx-projection-v0.md): Session runtime to LoopX projection v0

## Domain Capabilities

- [`auto_research_lane_contract_v1`](auto-research-lane-contract-v1.md): Auto-research lane contract v1
- [`auto_research_role_profile_v0`](auto-research-role-profile-v0.md): Auto-research role profile v0
- [`auto_research_role_state_machine_v0`](auto-research-role-state-machine-v0.md): Auto-research role state machine v0
- [`decentralized_auto_research_state_v0`](decentralized-auto-research-state-v0.md): Decentralized auto-research state v0
- [`content_ops_surface_v0`](content-ops-surface-v0.md): Content operations surface v0
- [`cs_notes_explore_capability_map_v0`](cs-notes-explore-capability-map-v0.md): CS notes Explore capability map v0
- [`issue_fix_acceptance_loop_v0`](issue-fix-acceptance-loop-v0.md): Issue-fix acceptance loop v0
- [`value_connector_plan_v0`](value-connector-plan-v0.md): Value connector plan v0
- [`x_public_channel_ops_v0`](x-public-channel-ops-v0.md): X public channel operations v0
- [`content_ops_item_v0`](content-ops-item-lifecycle-v0.md): provider-neutral content item lifecycle v0
- [`content_ops_queue_projection_v0`](content-ops-queue-v0.md): read-only managed content queue projection v0
- [`content_ops_layout_plan_v0`](content-ops-layout-v0.md): typed content layout plan, template library, and acceptance check v0

## Quality, Review, And Release

- [`model_behavior_qualification_v0`](model-behavior-qualification-v0.md): Model behavior qualification v0
- [`pr_review_command_v0`](../../../loopx/capabilities/pr_review_queue/README.md): PR review command v0
