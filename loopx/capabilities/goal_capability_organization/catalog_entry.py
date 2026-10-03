"""Package-owned discovery metadata, not runtime admission."""

GOAL_CAPABILITY_ORGANIZATION_CATALOG_ENTRY = {
    "id": "goal-capability-organization", "origin": "builtin", "visibility": "public",
    "provider_id": "loopx-core", "title": "Goal-scoped bounded capability improvement",
    "status": "active-preview", "default_enabled": False,
    "documentation": {"source_root": "loopx/capabilities/goal_capability_organization",
                      "site_root": "capabilities/goal-capability-organization", "canonical": "README.md"},
    "real_world_anchor": "A material Goal gap whose relevant capabilities need bounded inspection before a reversible trial.",
    "user_value": "Use existing capabilities directly; keep discovery and trial intent bounded, with original-owner configuration and explicit effect/rollback references.",
    "entry_command": "loopx configure-goal --goal-id <goal> --capability-improvement-mode bounded",
    "commands": [{"command": "loopx agent-context --goal-id <goal> --agent-id <agent> --phase before_plan --capability-gap-ref <gap-ref>",
                  "purpose": "Project bounded improvement advice without enabling a capability or starting work.", "write_boundary": "read-only"}],
    "implemented_protocols": [{"schema_version": "goal_capability_improvement_plan_v0",
                               "module": "loopx.control_plane.capabilities.goal_capability_organization",
                               "doc": "loopx/capabilities/goal_capability_organization/README.md"}],
    "smokes": ["python -m pytest tests/capabilities/test_capability_improvement.py -q"],
    "next_real_step": "Qualify packaged App and Lark guidance/configuration through the shared projection, then measure a real trial against its baseline. Backend advice alone is partial delivery.",
}
