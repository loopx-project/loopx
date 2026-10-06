from __future__ import annotations

import pytest

from loopx.capabilities.configuration_ui import (
    build_capability_configuration_catalog,
    capability_configuration_editor,
    resolve_capability_configuration,
)
from loopx.capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from loopx.configuration_catalog import (
    build_configuration_capability_descriptors,
    build_goal_configuration_catalog,
)


def test_machine_catalog_only_uses_dashboard_supported_editor_kinds() -> None:
    """A Goal-only descriptor must not make the whole machine page unreadable."""

    catalog = build_capability_configuration_catalog(
        machine_namespaces=build_builtin_machine_configuration_registry().public_catalog()[
            "namespaces"
        ],
        goal_features=build_configuration_capability_descriptors(),
    )
    supported = {
        "boolean",
        "number",
        "select",
        "string_list",
        "text",
        "periodic_report_schedule",
    }
    fields = {
        (capability["capability_id"], field["key"]): field
        for capability in catalog["capabilities"]
        for field in capability["configuration_editor"]["fields"]
    }
    assert fields[("progress_review", "drift_threshold")]["input_kind"] == "number"
    unsupported = {
        key: field["input_kind"]
        for key, field in fields.items()
        if field["input_kind"] not in supported
    }
    assert unsupported == {}


def test_periodic_report_editor_is_shared_across_machine_and_goal_scopes() -> None:
    editor = capability_configuration_editor("periodic_report")

    assert editor["schema_version"] == "capability_configuration_editor_v0"
    assert editor["editable"] is True
    assert editor["supported_scopes"] == ["machine", "goal"]
    assert [field["key"] for field in editor["fields"]] == [
        "enabled",
        "profile_preset",
        "route_ref",
        "timezone",
        "schedule",
    ]


def test_pull_request_review_editor_supports_machine_and_goal_ci_policy() -> None:
    editor = capability_configuration_editor("pull_request_review")

    assert editor["schema_version"] == "capability_configuration_editor_v0"
    assert editor["editable"] is True
    assert editor["supported_scopes"] == ["machine", "goal"]
    assert editor["writable_scopes"] == ["machine", "goal"]
    assert editor["fields"][0]["key"] == "wait_for_ci"
    assert editor["fields"][0]["input_kind"] == "boolean"
    assert [field["key"] for field in editor["fields"]] == [
        "wait_for_ci", "owner_logins", "review_order",
    ]
    owner = editor["fields"][1]
    assert owner["input_kind"] == "string_list"
    assert "does not infer membership or grant review/merge authority" in owner["description"]
    assert editor["fields"][2:] == [
        {
            "key": "review_order",
            "label": "Review direction",
            "description": (
                "Forward ranks other authors first, oldest first within tiers. "
                "Reverse inverts the whole actionable queue before the batch limit."
            ),
            "input_kind": "select",
            "required": True,
            "options": ["forward", "reverse"],
        }
    ]


def test_reward_memory_editor_writes_binding_without_returning_private_path() -> None:
    editor = capability_configuration_editor("reward_memory")

    assert editor["editable"] is True
    assert editor["writable_scopes"] == ["goal"]
    assert [field["key"] for field in editor["fields"]] == [
        "config_path",
        "enabled_agents",
    ]
    catalog = build_capability_configuration_catalog(
        goal_features=[
            {
                "feature_id": "reward_memory",
                "display_name": "Reward Memory",
                "current": {
                    "enabled": True,
                    "config_pointer_registered": True,
                    "binding_revision": "sha256:opaque",
                    "enabled_agents": ["researcher"],
                },
            }
        ]
    )
    current = catalog["capabilities"][0]["current"]
    assert current["config_pointer_registered"] is True
    assert "config_path" not in current


def test_steward_executor_editor_is_machine_only_and_typed() -> None:
    """The steward's executor is a machine setting no Goal can override."""

    editor = capability_configuration_editor("steward_executor")

    assert editor["editable"] is True
    assert editor["supported_scopes"] == ["machine"]
    assert editor["writable_scopes"] == ["machine"]
    fields = {field["key"]: field for field in editor["fields"]}
    # The form offers exactly the choices the owning namespace accepts, so a
    # submission cannot name an executor the channel has no contract for.
    assert fields["executor_endpoint"]["input_kind"] == "select"
    assert fields["executor_endpoint"]["options"] == ["codex", "dsh"]
    assert fields["executor_endpoint"]["required"] is True
    assert fields["selection_policy"]["options"] == [
        "preferred",
        "pinned",
        "flexible",
    ]
    assert fields["eligible_endpoints"]["input_kind"] == "string_list"
    assert fields["executor_model"]["input_kind"] == "text"
    assert fields["executor_model"]["nullable"] is True
    assert fields["executor_reasoning_effort"]["input_kind"] == "select"
    assert fields["executor_reasoning_effort"]["nullable"] is True
    assert "high" in fields["executor_reasoning_effort"]["options"]
    with pytest.raises(ValueError, match="does not support Goal configuration"):
        resolve_capability_configuration(
            "steward_executor",
            goal_override={
                "schema_version": "steward_executor_machine_defaults_v0",
                "executor_endpoint": "dsh",
            },
        )
    catalog = build_capability_configuration_catalog(
        machine_namespaces=[
            {
                "namespace": "steward_executor",
                "title": "Steward executor",
                "current": {
                    "schema_version": "steward_executor_machine_defaults_v0",
                    "executor_endpoint": "dsh",
                    "executor_model": "deepseek-v4-flash",
                    "executor_reasoning_effort": "high",
                },
                "configuration_template": {
                    "schema_version": "steward_executor_machine_defaults_v1",
                    "selection_policy": "preferred",
                    "executor_endpoint": "codex",
                    "eligible_endpoints": [],
                    "executor_model": None,
                    "executor_reasoning_effort": None,
                },
            }
        ]
    )
    capability = catalog["capabilities"][0]
    assert capability["available_scopes"] == ["machine"]
    # The configured machine value is the effective value, and it is reported as
    # an inherited machine default rather than as a capability default.
    assert capability["effective_configuration"]["source"] == "machine_default"
    assert capability["effective_configuration"]["inherited"] is True
    assert capability["effective_configuration"]["configuration"] == (
        capability["machine_current"]
    )


def test_catalog_merges_machine_and_goal_descriptors_without_losing_scope() -> None:
    catalog = build_capability_configuration_catalog(
        machine_namespaces=[
            {
                "namespace": "periodic_report",
                "title": "Periodic reports",
                "description": "Machine default.",
            },
            {
                "namespace": "search_defaults",
                "title": "Search defaults",
            },
        ],
        goal_features=[
            {
                "feature_id": "periodic_report",
                "display_name": "Periodic reports",
                "availability": "supported_opt_in",
                "default": {"enabled": False},
                "current": {"enabled": True},
            },
            {
                "feature_id": "explore_graph",
                "display_name": "Explore Graph",
                "current": {"enabled": False},
            },
        ],
    )

    assert catalog["schema_version"] == "capability_configuration_catalog_v0"
    entries = {item["capability_id"]: item for item in catalog["capabilities"]}
    assert entries["periodic_report"]["available_scopes"] == ["machine", "goal"]
    assert (
        entries["periodic_report"]["effective_value_policy"]
        == "goal_override_over_live_machine_default"
    )
    assert entries["explore_graph"]["available_scopes"] == ["goal"]
    assert entries["search_defaults"]["available_scopes"] == ["machine"]
    assert entries["search_defaults"]["configuration_editor"]["editable"] is False


@pytest.mark.parametrize(
    ("machine_namespaces", "goal_features", "message"),
    [
        ([{}], [], "requires a namespace"),
        ([], [{}], "requires a feature_id"),
        (
            [{"namespace": "same"}, {"namespace": "same"}],
            [],
            "duplicate machine capability",
        ),
        (
            [],
            [{"feature_id": "same"}, {"feature_id": "same"}],
            "duplicate Goal capability",
        ),
    ],
)
def test_catalog_fails_closed_on_incomplete_or_duplicate_descriptors(
    machine_namespaces: list[dict[str, object]],
    goal_features: list[dict[str, object]],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_capability_configuration_catalog(
            machine_namespaces=machine_namespaces,
            goal_features=goal_features,
        )


def test_goal_configuration_uses_the_shared_capability_catalog() -> None:
    catalog = build_goal_configuration_catalog(
        goal_id="goal-example",
        settings={},
        feature_summary={},
        default_multi_subagent_max_children=3,
        explore_harness_profiles=("generic",),
    )

    shared = catalog["capability_catalog"]
    assert shared["schema_version"] == "capability_configuration_catalog_v0"
    assert {item["capability_id"] for item in shared["capabilities"]} == {
        item["feature_id"] for item in catalog["features"]
    }
    scopes = {
        item["capability_id"]: item["available_scopes"]
        for item in shared["capabilities"]
    }
    assert scopes["todo_replan_cadence"] == ["goal"]
    assert scopes["change_quality_qualification"] == ["goal"]
    assert all(
        item["available_scopes"] == ["goal"] for item in shared["capabilities"]
    )
    multi_subagent = next(
        item
        for item in shared["capabilities"]
        if item["capability_id"] == "multi_subagent"
    )
    assert multi_subagent["context_contribution"] == {
        "supported_phases": ["before_plan", "before_delegate", "after_delegate_result"],
        "target": "coordinator", "activation": "with_capability", "receipt_required": True,
    }
    assert "context_contribution" not in multi_subagent["current"]
    assert multi_subagent["default"] == {
        "enabled": False,
        "max_children": 3,
        "allowed_domains": [],
    }


def test_goal_override_wins_atomically_without_rebinding_machine_fields() -> None:
    machine_default = {
        "enabled": True,
        "profile_preset": "weekly-progress",
        "route_ref": "loopx-manager-group",
        "timezone": "Asia/Shanghai",
    }
    existing_goal_override = {
        "enabled": True,
        "profile_preset": "ark-4.0-weekly",
        "route_ref": "existing-goal-binding",
        "timezone": "Asia/Shanghai",
    }

    resolved = resolve_capability_configuration(
        "periodic_report",
        goal_override=existing_goal_override,
        machine_default=machine_default,
    )

    assert resolved["schema_version"] == "capability_configuration_resolution_v0"
    assert resolved["source"] == "goal_override"
    assert resolved["configuration"] == existing_goal_override
    assert resolved["configuration"]["route_ref"] == "existing-goal-binding"
    assert resolved["inherited"] is False
    assert resolved["goal_override_present"] is True
    assert resolved["machine_default_present"] is True
    assert resolved["effective_revision"].startswith("sha256:")


def test_unconfigured_goal_inherits_live_machine_default_without_mutation() -> None:
    machine_default = {
        "enabled": True,
        "profile_preset": "weekly-progress",
        "route_ref": "loopx-manager-group",
        "timezone": "Asia/Shanghai",
    }

    resolved = resolve_capability_configuration(
        "periodic_report",
        machine_default=machine_default,
        capability_default={"enabled": False},
    )

    assert resolved["source"] == "machine_default"
    assert resolved["configuration"] == machine_default
    assert resolved["inherited"] is True
    assert resolved["goal_override_present"] is False


def test_resolution_rejects_values_for_unsupported_scopes() -> None:
    with pytest.raises(ValueError, match="does not support machine configuration"):
        resolve_capability_configuration(
            "explore_graph",
            machine_default={"enabled": True},
        )

    with pytest.raises(TypeError, match="goal_override must be an object or null"):
        resolve_capability_configuration(  # type: ignore[arg-type]
            "periodic_report",
            goal_override=True,
        )


@pytest.mark.parametrize(
    ("capability_id", "scopes"),
    [
        ("manager_runtime", ["machine"]),
        ("steward_executor", ["machine"]),
        ("periodic_report", ["machine", "goal"]),
        ("change_quality_qualification", ["machine", "goal"]),
        ("pull_request_review", ["machine", "goal"]),
        ("todo_replan_cadence", ["machine", "goal"]),
        ("multi_subagent", ["goal"]),
        ("peer_task_coordination", ["goal"]),
        ("explore_harness", ["goal"]),
        ("explore_harness", ["goal"]),
        ("progress_review", ["goal"]),
        ("reward_memory", ["goal"]),
        ("lark_kanban_heartbeat_sync", ["goal"]),
        ("lark_event_inbox", ["goal"]),
        ("coordination_runtime_shadow", ["goal"]),
        ("local_authority_shadow", ["goal"]),
    ],
)
def test_capability_scope_matches_product_ownership(capability_id, scopes) -> None:
    """Host authority, reusable defaults and Goal bindings stay distinct."""

    catalog = build_capability_configuration_catalog(
        machine_namespaces=build_builtin_machine_configuration_registry().public_catalog()[
            "namespaces"
        ],
        goal_features=build_configuration_capability_descriptors(),
    )
    descriptor = next(c for c in catalog["capabilities"] if c["capability_id"] == capability_id)
    assert descriptor["available_scopes"] == scopes
    assert descriptor["configuration_editor"]["supported_scopes"] == scopes
    assert set(descriptor["configuration_editor"]["writable_scopes"]) <= set(scopes)
    for scope, keyword in (("machine", "machine_default"), ("goal", "goal_override")):
        if scope not in scopes:
            with pytest.raises(ValueError, match="does not support"):
                resolve_capability_configuration(capability_id, **{keyword: {}})
