from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from loopx.capabilities.machine_configuration.contract import (
    MACHINE_CONFIGURATION_SCHEMA,
)
from loopx.capabilities.machine_configuration.store import (
    configure_machine_configuration,
    machine_configuration_store_path,
)
from loopx.capabilities.steward_executor import (
    STEWARD_EXECUTOR_NAMESPACE,
    STEWARD_EXECUTOR_SCHEMA,
    effective_steward_executor_defaults,
    load_effective_steward_executor_defaults,
    normalize_manager_executor_allocation,
    normalize_steward_executor_machine_defaults,
)


def _steward(
    *,
    endpoint: str = "dsh",
    model: str | None = "deepseek-v4-flash",
    effort: str | None = "high",
    selection_policy: str = "preferred",
    eligible_endpoints: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": STEWARD_EXECUTOR_SCHEMA,
        "selection_policy": selection_policy,
        "executor_endpoint": endpoint,
        "eligible_endpoints": eligible_endpoints or [],
        "executor_model": model,
        "executor_reasoning_effort": effort,
    }


def _document(namespaces: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": MACHINE_CONFIGURATION_SCHEMA,
        "namespaces": namespaces,
    }


def _apply(runtime_root: Path, namespaces: dict[str, object]) -> None:
    registry = build_builtin_machine_configuration_registry()
    configuration = _document(namespaces)
    preview = configure_machine_configuration(
        runtime_root=runtime_root,
        configuration=configuration,
        registry=registry,
        execute=False,
    )
    configure_machine_configuration(
        runtime_root=runtime_root,
        configuration=configuration,
        registry=registry,
        execute=True,
        expected_plan_revision=str(preview["plan_revision"]),
    )


def test_the_steward_executor_namespace_is_registered_and_typed() -> None:
    registry = build_builtin_machine_configuration_registry()

    assert STEWARD_EXECUTOR_NAMESPACE in registry.namespace_ids
    catalog = {
        namespace["namespace"]: namespace
        for namespace in registry.public_catalog()["namespaces"]
    }
    descriptor = catalog[STEWARD_EXECUTOR_NAMESPACE]
    assert descriptor["template_status"] == "ready"
    # The template is the shipped decision, not a discovered value: one
    # endpoint, and the two fields the machine leaves to the lower layers.
    assert descriptor["configuration_template"] == _steward(
        endpoint="codex", model=None, effort=None
    )
    assert descriptor["documentation"]["path"] == (
        "docs/architecture/rfcs/harness-selection-dsh-pi-v0.md"
    )


def test_the_steward_executor_namespace_fails_closed_on_untyped_values() -> None:
    normalized = normalize_steward_executor_machine_defaults(_steward())
    assert normalized == _steward()

    # A blank model or effort means "this machine decides nothing about that
    # field", so it normalizes to absence rather than to an empty selection.
    assert normalize_steward_executor_machine_defaults(
        _steward(model="  ", effort="")
    ) == _steward(model=None, effort=None)

    with pytest.raises(ValueError, match="unsupported fields: provider"):
        normalize_steward_executor_machine_defaults(
            {**_steward(), "provider": "deepseek-official"}
        )
    with pytest.raises(ValueError, match="executor_endpoint must be one of"):
        normalize_steward_executor_machine_defaults(_steward(endpoint="dhs"))
    with pytest.raises(ValueError, match="executor_endpoint must be one of"):
        normalize_steward_executor_machine_defaults(_steward(endpoint=""))
    with pytest.raises(ValueError, match="reasoning_effort must be one of"):
        normalize_steward_executor_machine_defaults(_steward(effort="maximum"))
    with pytest.raises(ValueError, match="must use"):
        normalize_steward_executor_machine_defaults(
            {**_steward(), "schema_version": "steward_executor_v1"}
        )


def test_flexible_selection_requires_a_closed_authorized_pool() -> None:
    flexible = normalize_steward_executor_machine_defaults(
        _steward(
            endpoint="dsh",
            selection_policy="flexible",
            eligible_endpoints=["dsh", "codex"],
        )
    )
    assert flexible["eligible_endpoints"] == ["dsh", "codex"]

    with pytest.raises(ValueError, match="must not be empty"):
        normalize_steward_executor_machine_defaults(
            _steward(endpoint="dsh", selection_policy="flexible")
        )
    with pytest.raises(ValueError, match="must belong"):
        normalize_steward_executor_machine_defaults(
            _steward(
                endpoint="dsh",
                selection_policy="flexible",
                eligible_endpoints=["codex"],
            )
        )
    with pytest.raises(ValueError, match="only valid for flexible"):
        normalize_steward_executor_machine_defaults(
            _steward(eligible_endpoints=["dsh"])
        )


def test_legacy_preference_configuration_remains_readable() -> None:
    legacy = {
        "schema_version": "steward_executor_machine_defaults_v0",
        "executor_endpoint": "dsh",
        "executor_model": None,
        "executor_reasoning_effort": None,
    }
    assert normalize_steward_executor_machine_defaults(legacy) == legacy
    effective = effective_steward_executor_defaults(_document({"steward_executor": legacy}))
    assert effective["selection_policy"] == "preferred"
    assert effective["eligible_endpoints"] == []


def test_persisted_allocation_uses_the_same_closed_typed_contract() -> None:
    allocation = {
        "schema_version": "manager_executor_allocation_v0",
        "selection_policy": "preferred",
        "allocation_reason": "product_default",
        "executor_endpoint": "codex",
        "executor_endpoint_source": "product_default",
        "executor_endpoint_default_reason": "steward_channel_default",
        "configured_endpoint": None,
        "eligible_endpoints": [],
        "configuration_revision": "",
        "available": None,
        "model": "gpt-6-astra",
        "model_source": "vendor_default",
        "reasoning_effort": "high",
    }
    assert normalize_manager_executor_allocation(allocation) == allocation
    captured = {
        **allocation,
        "configured_model": None,
        "configured_reasoning_effort": "high",
    }
    assert normalize_manager_executor_allocation(captured) == captured
    with pytest.raises(ValueError, match="both configured"):
        normalize_manager_executor_allocation({**allocation, "configured_model": None})
    with pytest.raises(ValueError, match="configured_model"):
        normalize_manager_executor_allocation({**captured, "configured_model": ""})
    with pytest.raises(ValueError, match="configured_reasoning_effort"):
        normalize_manager_executor_allocation({**captured, "configured_reasoning_effort": "maximum"})

    with pytest.raises(ValueError, match="allocation_reason"):
        normalize_manager_executor_allocation(
            {**allocation, "allocation_reason": "model_decided_somehow"}
        )


def test_an_unconfigured_machine_decides_nothing() -> None:
    """Absence resolves to the lower layers, never to a guessed endpoint."""

    assert effective_steward_executor_defaults(None) == {
        "schema_version": "steward_executor_effective_defaults_v0",
        "status": "absent",
        "source": "capability_default",
        "configuration_revision": "absent",
        "executor_endpoint": None,
        "executor_model": None,
        "executor_reasoning_effort": None,
        "selection_policy": "preferred",
        "eligible_endpoints": [],
    }
    # A document that configures a sibling capability says nothing about the
    # steward, and neither does an unreadable store.
    assert (
        effective_steward_executor_defaults(
            _document(
                {
                    "manager_runtime": {
                        "schema_version": "manager_runtime_profile_v0",
                        "runtime_profile": "restricted",
                    }
                }
            )
        )["status"]
        == "absent"
    )


def test_the_machine_selection_round_trips_through_the_live_store(
    tmp_path: Path,
) -> None:
    _apply(
        tmp_path,
        {
            STEWARD_EXECUTOR_NAMESPACE: _steward(),
            "manager_runtime": {
                "schema_version": "manager_runtime_profile_v0",
                "runtime_profile": "trusted_owner",
            },
        },
    )

    effective = load_effective_steward_executor_defaults(tmp_path)

    assert effective["status"] == "ready"
    assert effective["source"] == "machine_configuration"
    assert effective["executor_endpoint"] == "dsh"
    assert effective["executor_model"] == "deepseek-v4-flash"
    assert effective["executor_reasoning_effort"] == "high"
    assert str(effective["configuration_revision"]).startswith("sha256:")


def test_a_malformed_steward_value_falls_back_with_a_typed_reason(
    tmp_path: Path,
) -> None:
    """A person's channel keeps answering; the invalid choice is named."""

    path = machine_configuration_store_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_document({STEWARD_EXECUTOR_NAMESPACE: _steward(endpoint="dsh")}))
        .replace('"dsh"', '"dhs"'),
        encoding="utf-8",
    )

    effective = load_effective_steward_executor_defaults(tmp_path)

    assert effective["status"] == "configuration_invalid"
    assert effective["source"] == "invalid_configuration_fallback"
    assert effective["executor_endpoint"] is None
    assert effective["repair"] == (
        "Open machine capability settings and repair Steward executor."
    )


def test_a_malformed_sibling_does_not_rewrite_a_valid_steward_selection(
    tmp_path: Path,
) -> None:
    """Each namespace owns its runtime effect, including its failure mode."""

    path = machine_configuration_store_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            _document(
                {
                    STEWARD_EXECUTOR_NAMESPACE: _steward(),
                    "manager_runtime": {
                        "schema_version": "manager_runtime_profile_v0",
                        "runtime_profile": "unbounded",
                    },
                }
            )
        ),
        encoding="utf-8",
    )

    effective = load_effective_steward_executor_defaults(tmp_path)

    assert effective["status"] == "ready"
    assert effective["executor_endpoint"] == "dsh"
