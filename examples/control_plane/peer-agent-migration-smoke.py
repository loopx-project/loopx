#!/usr/bin/env python3
"""Exercise the one-time hierarchy-to-peer automation/runtime migration."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.configure_goal import configure_goal  # noqa: E402
from loopx.control_plane.agents.identity import (  # noqa: E402
    build_identity_aware_prompt_upgrade,
    build_quota_agent_identity,
)
from loopx.control_plane.agents.legacy_migration import (  # noqa: E402
    PEER_AGENT_RUNTIME_MIGRATION,
)
from loopx.heartbeat_prompt import build_heartbeat_prompt  # noqa: E402
from loopx.upgrade import peer_runtime_upgrade_migration  # noqa: E402


GOAL_ID = "peer-agent-migration-fixture"
AGENTS = ["codex-alpha", "codex-beta"]


def write_legacy_registry(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "goals": [
                    {
                        "id": GOAL_ID,
                        "coordination": {
                            "registered_agents": AGENTS,
                            "agent_model": "peer_v1",
                            "side_agent_handoff_agent": AGENTS[0],
                            "agent_profiles": {
                                AGENTS[0]: {
                                    "schema_version": "agent_profile_v0",
                                    "role": "primary-agent",
                                    "scope_summary": "runtime and release work",
                                    "review_policy": {
                                        "can_self_merge": True,
                                        "reviews_side_agent_work": True,
                                    },
                                },
                                AGENTS[1]: {
                                    "schema_version": "agent_profile_v0",
                                    "role": "side-agent",
                                    "primary_agent": AGENTS[0],
                                    "scope_summary": "docs and validation work",
                                    "review_policy": {
                                        "can_self_merge": "small_validated_only",
                                        "handoff_agent": AGENTS[0],
                                    },
                                },
                            },
                            "write_scope": ["loopx/**"],
                        },
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-peer-agent-migration-") as tmp:
        registry_path = Path(tmp) / "registry.json"
        write_legacy_registry(registry_path)
        original = registry_path.read_text(encoding="utf-8")
        legacy_goal = json.loads(original)["goals"][0]
        identity = build_quota_agent_identity(legacy_goal, agent_id=AGENTS[0])

        first = build_identity_aware_prompt_upgrade(
            legacy_goal,
            goal_id=GOAL_ID,
            agent_identity=identity,
        )
        repeated_projection = build_identity_aware_prompt_upgrade(
            legacy_goal,
            goal_id=GOAL_ID,
            agent_identity=identity,
        )
        assert first["migration_id"] == repeated_projection["migration_id"], first
        assert first["delivery_semantics"] == "stable_idempotent_until_ack", first
        assert first["host_update_idempotency_key"] == first["migration_id"], first
        assert first["migration_id"] in first["completion_command"], first
        migration_id = first["migration_id"]

        host_migration = peer_runtime_upgrade_migration(
            legacy_goal,
            goal_id=GOAL_ID,
            installed={
                "thin:codex-alpha": {
                    "status": "stale",
                    "installed": True,
                    "requires_update": True,
                    "automation_id": "alpha-heartbeat",
                    "agent_id": AGENTS[0],
                },
                "thin:codex-beta": {
                    "status": "unknown",
                    "installed": False,
                    "requires_update": True,
                    "automation_id": None,
                    "agent_id": AGENTS[1],
                },
                "thin:codex-current": {
                    "status": "current",
                    "installed": True,
                    "requires_update": False,
                    "automation_id": "current-heartbeat",
                    "agent_id": "codex-current",
                },
            },
            generated_prompts={
                "thin:codex-alpha": {"command": "loopx heartbeat-prompt --thin"},
                "thin:codex-beta": {"command": "loopx heartbeat-prompt --thin"},
                "thin:codex-current": {"command": "loopx heartbeat-prompt --thin"},
            },
        )
        assert host_migration["host_update_required_once"] is True, host_migration
        assert [
            item["automation_id"] for item in host_migration["host_updates"]
        ] == ["alpha-heartbeat"], host_migration

        preview = configure_goal(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            agent_model="peer_v1",
        )
        assert preview["dry_run"] is True, preview
        assert preview["after"]["legacy_hierarchy_present"] is True, preview
        assert preview["heartbeat_prompt_migration"]["migration_id"] == migration_id
        assert registry_path.read_text(encoding="utf-8") == original

        try:
            configure_goal(
                registry_path=registry_path,
                goal_id=GOAL_ID,
                agent_model="peer_v1",
                execute=True,
            )
        except ValueError as exc:
            assert migration_id in str(exc), exc
            assert "host automation migration" in str(exc), exc
        else:
            raise AssertionError("registry hard cut must require the host-update ack")

        applied = configure_goal(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            automation_prompt_migration_ack=migration_id,
            execute=True,
        )
        assert applied["written"] is True, applied
        assert applied["automation_prompt_migration"]["status"] == "completed", applied
        backup_path = Path(applied["backup_path"])
        assert backup_path.exists(), applied
        assert backup_path.read_text(encoding="utf-8") == original
        goal = json.loads(registry_path.read_text(encoding="utf-8"))["goals"][0]
        coordination = goal["coordination"]
        assert coordination["agent_model"] == "peer_v1", coordination
        assert coordination["registered_agents"] == AGENTS, coordination
        assert coordination["write_scope"] == ["loopx/**"], coordination
        assert "primary_agent" not in coordination, coordination
        assert "side_agent_handoff_agent" not in coordination, coordination
        assert set(coordination["agent_profiles"]) == set(AGENTS), coordination
        for profile in coordination["agent_profiles"].values():
            assert profile["schema_version"] == "agent_profile_v1", profile
            assert "role" not in profile and "primary_agent" not in profile, profile
            assert "worktree_policy" not in profile and "review_policy" not in profile, profile
        completed = coordination["completed_migrations"][PEER_AGENT_RUNTIME_MIGRATION]
        assert completed["migration_id"] == migration_id, completed
        assert completed["status"] == "completed", completed

        repeated_ack = configure_goal(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            automation_prompt_migration_ack=migration_id,
            execute=True,
        )
        assert repeated_ack["changed"] is False, repeated_ack
        assert repeated_ack["backup_path"] is None, repeated_ack
        assert repeated_ack["automation_prompt_migration"]["status"] == (
            "already_completed"
        ), repeated_ack

        migrated_identity = build_quota_agent_identity(goal, agent_id=AGENTS[0])
        assert (
            build_identity_aware_prompt_upgrade(
                goal,
                goal_id=GOAL_ID,
                agent_identity=migrated_identity,
            )
            is None
        )

        # A stale v0.1 writer may reintroduce legacy fields after cutover. The
        # completed migration remains final authority: runtime ignores those
        # fields and neither quota nor upgrade planning wakes the user again.
        coordination["primary_agent"] = AGENTS[0]
        coordination["side_agent_handoff_agent"] = AGENTS[1]
        assert (
            build_identity_aware_prompt_upgrade(
                goal,
                goal_id=GOAL_ID,
                agent_identity=migrated_identity,
            )
            is None
        )
        completed_upgrade = peer_runtime_upgrade_migration(
            goal,
            goal_id=GOAL_ID,
            installed={},
            generated_prompts={},
        )
        assert completed_upgrade == {
            "schema_version": "peer_runtime_automation_migration_v1",
            "required": False,
            "status": "completed",
            "migration_id": migration_id,
        }, completed_upgrade
        reintroduced_registry = json.loads(registry_path.read_text(encoding="utf-8"))
        reintroduced_registry["goals"][0] = goal
        registry_path.write_text(
            json.dumps(reintroduced_registry, indent=2) + "\n",
            encoding="utf-8",
        )
        repeated_after_legacy_write = configure_goal(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            automation_prompt_migration_ack=migration_id,
            execute=True,
        )
        assert repeated_after_legacy_write["changed"] is False, repeated_after_legacy_write
        assert repeated_after_legacy_write["backup_path"] is None, repeated_after_legacy_write

        custom_role_registry = Path(tmp) / "custom-role-registry.json"
        custom_role_registry.write_text(
            json.dumps(
                {
                    "goals": [
                        {
                            "id": GOAL_ID,
                            "coordination": {
                                "registered_agents": AGENTS,
                                "agent_model": "peer_v1",
                                "agent_profiles": {
                                    AGENTS[0]: {
                                        "role": "researcher",
                                        "scope_summary": "research synthesis",
                                    }
                                },
                            },
                        }
                    ]
                }
            )
            + "\n",
            encoding="utf-8",
        )
        custom_goal = json.loads(custom_role_registry.read_text(encoding="utf-8"))[
            "goals"
        ][0]
        custom_identity = build_quota_agent_identity(custom_goal, agent_id=AGENTS[0])
        custom_upgrade = build_identity_aware_prompt_upgrade(
            custom_goal,
            goal_id=GOAL_ID,
            agent_identity=custom_identity,
        )
        assert custom_upgrade["registry_migration_required"] is True, custom_upgrade
        custom_applied = configure_goal(
            registry_path=custom_role_registry,
            goal_id=GOAL_ID,
            automation_prompt_migration_ack=custom_upgrade["migration_id"],
            execute=True,
        )
        assert custom_applied["written"] is True, custom_applied
        custom_migrated_goal = json.loads(
            custom_role_registry.read_text(encoding="utf-8")
        )["goals"][0]
        custom_profile = custom_migrated_goal["coordination"]["agent_profiles"][AGENTS[0]]
        assert custom_profile["profile_role"] == "researcher", custom_profile
        assert custom_profile["scope_summary"] == "research synthesis", custom_profile
        assert "role" not in custom_profile, custom_profile

        fresh_registry = Path(tmp) / "fresh-registry.json"
        fresh_registry.write_text(
            json.dumps({"goals": [{"id": GOAL_ID}]}) + "\n",
            encoding="utf-8",
        )
        fresh = configure_goal(
            registry_path=fresh_registry,
            goal_id=GOAL_ID,
            registered_agents=AGENTS,
            execute=True,
        )
        assert fresh["after"]["agent_model"] == "peer_v1", fresh
        assert fresh["automation_prompt_migration"]["status"] == "not_required", fresh

        heartbeat = build_heartbeat_prompt(
            goal_id=GOAL_ID,
            thin=True,
            agent_id=AGENTS[0],
            agent_scopes=["peer task claims and leases"],
            registered_agents=AGENTS,
        )
        assert heartbeat["agent_model"] == "peer_v1", heartbeat
        assert heartbeat["agent_role"] == "peer-agent", heartbeat
        assert "primary_agent" not in heartbeat, heartbeat
        assert "side_agent_handoff_agent" not in heartbeat, heartbeat
        assert "single primary" not in heartbeat["task_body"].lower(), heartbeat
        assert "side-agent" not in heartbeat["task_body"].lower(), heartbeat
        assert "equal peer" in heartbeat["task_body"].lower(), heartbeat
        assert "Follow user authority and repository rules" in heartbeat["task_body"], heartbeat
        assert "Gate only the affected path" in heartbeat["task_body"], heartbeat

    print("peer-agent-migration-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
