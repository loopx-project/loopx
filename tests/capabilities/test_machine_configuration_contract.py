from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from loopx.capabilities.machine_configuration.contract import (
    MachineConfigurationNamespace,
    MachineConfigurationRegistry,
    merge_machine_configuration_namespace,
    normalize_machine_configuration,
    project_machine_configuration,
    remove_machine_configuration_namespace,
)


def _normalize_example(value: Mapping[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(value) - {"schema_version", "enabled", "secret"})
    if unknown:
        raise ValueError("example contains unsupported fields: " + ", ".join(unknown))
    if not isinstance(value.get("enabled"), bool):
        raise TypeError("example.enabled must be a boolean")
    return dict(value)


def _normalize_required_input(value: Mapping[str, Any]) -> dict[str, Any]:
    if not value.get("path"):
        raise ValueError("path is required")
    return dict(value)


def _apply_example_public_update(
    current: Mapping[str, Any] | None,
    update: Mapping[str, Any],
) -> dict[str, Any]:
    unknown = sorted(set(update) - {"schema_version", "enabled"})
    if unknown:
        raise ValueError(
            "example public update contains unsupported fields: " + ", ".join(unknown)
        )
    return {**dict(current or {}), **dict(update)}


def _registry() -> MachineConfigurationRegistry:
    return MachineConfigurationRegistry().register(
        MachineConfigurationNamespace(
            namespace="example",
            schema_versions=frozenset({"example_v0"}),
            normalize=_normalize_example,
            project_public=lambda value: {
                key: item for key, item in value.items() if key != "secret"
            },
            apply_public_update=_apply_example_public_update,
            title="Example defaults",
            description="Example machine-level defaults.",
            default_configuration={
                "schema_version": "example_v0",
                "enabled": False,
                "secret": "hidden",
            },
        )
    )


def _config() -> dict[str, Any]:
    return {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {
            "example": {
                "schema_version": "example_v0",
                "enabled": True,
                "secret": "redacted",
            }
        },
    }


def test_registered_namespace_is_normalized_and_publicly_projected() -> None:
    assert normalize_machine_configuration(_config(), registry=_registry()) == _config()
    assert project_machine_configuration(_config(), registry=_registry()) == {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {"example": {"schema_version": "example_v0", "enabled": True}},
    }


def test_unknown_namespace_and_unknown_envelope_field_fail_closed() -> None:
    config = _config()
    config["namespaces"] = {"unknown": {"schema_version": "unknown_v0"}}
    with pytest.raises(ValueError, match="unsupported machine-configuration namespace"):
        normalize_machine_configuration(config, registry=_registry())

    config = _config()
    config["arbitrary"] = True
    with pytest.raises(ValueError, match="unsupported fields"):
        normalize_machine_configuration(config, registry=_registry())


def test_consumer_schema_version_and_fields_are_enforced() -> None:
    config = _config()
    config["namespaces"]["example"]["schema_version"] = "example_v1"
    with pytest.raises(ValueError, match="unsupported schema_version"):
        normalize_machine_configuration(config, registry=_registry())

    config = _config()
    config["namespaces"]["example"]["surprise"] = True
    with pytest.raises(ValueError, match="example contains unsupported fields"):
        normalize_machine_configuration(config, registry=_registry())


def test_removing_the_last_namespace_returns_document_absence() -> None:
    assert (
        remove_machine_configuration_namespace(
            _config(), namespace="example", registry=_registry()
        )
        is None
    )


def test_removing_an_unknown_namespace_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported machine-configuration namespace"):
        remove_machine_configuration_namespace(
            _config(), namespace="unknown", registry=_registry()
        )


def test_registry_catalog_is_public_and_supplies_an_editable_default() -> None:
    assert _registry().public_catalog() == {
        "schema_version": "machine_configuration_catalog_v0",
        "namespaces": [
            {
                "namespace": "example",
                "title": "Example defaults",
                "description": "Example machine-level defaults.",
                "schema_versions": ["example_v0"],
                "configuration_template": {
                    "schema_version": "example_v0",
                    "enabled": False,
                },
                "template_status": "ready",
            }
        ],
    }


def test_registry_catalog_keeps_namespaces_without_defaults_discoverable() -> None:
    registry = MachineConfigurationRegistry().register(
        MachineConfigurationNamespace(
            namespace="required_input",
            schema_versions=frozenset({"required_input_v0"}),
            normalize=_normalize_required_input,
            project_public=lambda value: dict(value),
            apply_public_update=lambda _current, update: dict(update),
        )
    )

    descriptor = registry.public_catalog()["namespaces"][0]
    assert descriptor["configuration_template"] == {
        "schema_version": "required_input_v0"
    }
    assert descriptor["template_status"] == "schema_only"


def test_registry_rejects_an_invalid_provider_template_before_effects() -> None:
    with pytest.raises(TypeError, match="example.enabled must be a boolean"):
        MachineConfigurationRegistry().register(
            MachineConfigurationNamespace(
                namespace="example",
                schema_versions=frozenset({"example_v0"}),
                normalize=_normalize_example,
                project_public=lambda value: dict(value),
                apply_public_update=lambda _current, update: dict(update),
                default_configuration={"schema_version": "example_v0"},
            )
        )


def test_namespace_merge_preserves_siblings_and_validates_the_patch() -> None:
    registry = _registry().register(
        MachineConfigurationNamespace(
            namespace="second",
            schema_versions=frozenset({"second_v0"}),
            normalize=lambda value: dict(value),
            project_public=lambda value: dict(value),
            apply_public_update=lambda _current, update: dict(update),
        )
    )
    current = _config()

    merged = merge_machine_configuration_namespace(
        current,
        namespace="second",
        namespace_configuration={"schema_version": "second_v0", "limit": 4},
        registry=registry,
    )

    assert merged["namespaces"]["example"] == current["namespaces"]["example"]
    assert merged["namespaces"]["second"] == {
        "schema_version": "second_v0",
        "limit": 4,
    }


def test_namespace_merge_delegates_public_updates_and_preserves_private_fields() -> None:
    merged = merge_machine_configuration_namespace(
        _config(),
        namespace="example",
        namespace_configuration={"schema_version": "example_v0", "enabled": False},
        registry=_registry(),
    )

    assert merged["namespaces"]["example"] == {
        "schema_version": "example_v0",
        "enabled": False,
        "secret": "redacted",
    }


def test_namespace_merge_rejects_private_fields_in_a_public_update() -> None:
    with pytest.raises(ValueError, match="public update contains unsupported fields"):
        merge_machine_configuration_namespace(
            _config(),
            namespace="example",
            namespace_configuration={
                "schema_version": "example_v0",
                "enabled": False,
                "secret": "replacement",
            },
            registry=_registry(),
        )


@pytest.mark.parametrize("source", ["file", "stdin"])
def test_machine_configuration_json_input_is_bounded(tmp_path, monkeypatch, source):
    import io
    from types import SimpleNamespace
    from loopx.capabilities.machine_configuration import cli as machine_config_cli

    monkeypatch.setattr(
        machine_config_cli,
        "MAX_MACHINE_CONFIGURATION_INPUT_BYTES",
        8,
        raising=False,
    )
    if source == "file":
        request_path = tmp_path / "oversized.json"
        request_path.write_bytes(b'{"ok":1} ')
        source_text = str(request_path)
    else:
        monkeypatch.setattr(
            machine_config_cli.sys,
            "stdin",
            SimpleNamespace(
                buffer=io.BytesIO(b'{"ok":1} '),
                read=lambda: '{"ok":1} ',
            ),
        )
        source_text = "-"

    with pytest.raises(ValueError, match="machine configuration input exceeds"):
        machine_config_cli._load_json_object(source_text)
