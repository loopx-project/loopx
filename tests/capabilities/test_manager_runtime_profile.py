from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from loopx.capabilities.machine_configuration.store import (
    configure_machine_configuration,
)
from loopx.capabilities.manager_runtime import (
    effective_manager_runtime_profile,
    load_effective_manager_runtime_profile,
    normalize_manager_runtime_profile,
)


def _configuration(profile: str) -> dict[str, object]:
    return {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {
            "manager_runtime": {
                "schema_version": "manager_runtime_profile_v0",
                "runtime_profile": profile,
            }
        },
    }


def _apply(runtime_root: Path, profile: str) -> None:
    registry = build_builtin_machine_configuration_registry()
    preview = configure_machine_configuration(
        runtime_root=runtime_root,
        configuration=_configuration(profile),
        registry=registry,
        execute=False,
    )
    configure_machine_configuration(
        runtime_root=runtime_root,
        configuration=_configuration(profile),
        registry=registry,
        execute=True,
        expected_plan_revision=str(preview["plan_revision"]),
    )


def test_manager_runtime_profile_is_explicit_and_fail_closed() -> None:
    assert normalize_manager_runtime_profile(
        {
            "schema_version": "manager_runtime_profile_v0",
            "runtime_profile": "trusted_owner",
        }
    )["runtime_profile"] == "trusted_owner"
    with pytest.raises(ValueError, match="restricted or trusted_owner"):
        normalize_manager_runtime_profile(
            {
                "schema_version": "manager_runtime_profile_v0",
                "runtime_profile": "unbounded",
            }
        )
    default = effective_manager_runtime_profile(None)
    assert default["runtime_profile"] == "restricted"
    assert default["sandbox"] == "read-only"
    assert default["standing_grant"] == "none"


def test_trusted_owner_profile_roundtrips_through_machine_configuration(
    tmp_path: Path,
) -> None:
    _apply(tmp_path, "trusted_owner")

    effective = load_effective_manager_runtime_profile(tmp_path)

    assert effective["status"] == "ready"
    assert effective["runtime_profile"] == "trusted_owner"
    assert effective["sandbox"] == "danger-full-access"
    assert effective["standing_grant"] == "machine_configuration"
    assert {"filesystem", "shell", "git", "web"} <= set(
        effective["tool_classes"]
    )
    assert str(effective["configuration_revision"]).startswith("sha256:")


def test_external_audience_does_not_inherit_owner_host_tools(tmp_path: Path) -> None:
    _apply(tmp_path, "trusted_owner")

    effective = load_effective_manager_runtime_profile(
        tmp_path,
        channel_id="manager.external.audience",
    )

    assert effective["status"] == "external_audience_restricted"
    assert effective["configured_runtime_profile"] == "trusted_owner"
    assert effective["runtime_profile"] == "restricted"
    assert effective["sandbox"] == "read-only"
    assert effective["standing_grant"] == "none"
    assert effective["source"] == "external_audience_boundary"


@pytest.mark.parametrize("profile", [None, "restricted", "trusted_owner"])
def test_owner_audience_proof_does_not_replace_the_machine_grant(tmp_path: Path, profile) -> None:
    if profile is not None:
        _apply(tmp_path, profile)
    effective = load_effective_manager_runtime_profile(tmp_path,
        channel_id="manager.external.native.fixture", owner_manager_audience=True)
    assert effective["runtime_profile"] == (profile or "restricted")
    assert effective["standing_grant"] == ("machine_configuration" if profile == "trusted_owner" else "none")


def test_unrelated_machine_configuration_does_not_change_manager_revision() -> None:
    before = effective_manager_runtime_profile(
        {
            "schema_version": "loopx_machine_configuration_v0",
            "namespaces": {},
        }
    )
    after = effective_manager_runtime_profile(
        {
            "schema_version": "loopx_machine_configuration_v0",
            "namespaces": {
                "unrelated_fixture": {
                    "schema_version": "unrelated_fixture_v0",
                    "enabled": True,
                }
            },
        }
    )

    assert before["configuration_revision"] == "absent"
    assert after["configuration_revision"] == "absent"


def test_invalid_sibling_does_not_rewrite_a_valid_manager_grant(
    tmp_path: Path,
) -> None:
    path = tmp_path / "machine" / "configuration.json"
    path.parent.mkdir(parents=True)
    configuration = _configuration("trusted_owner")
    namespaces = configuration["namespaces"]
    assert isinstance(namespaces, dict)
    namespaces["invalid_sibling"] = {
        "schema_version": "unknown_v0",
    }
    path.write_text(json.dumps(configuration), encoding="utf-8")

    effective = load_effective_manager_runtime_profile(tmp_path)

    assert effective["status"] == "ready"
    assert effective["runtime_profile"] == "trusted_owner"
    assert effective["sandbox"] == "danger-full-access"


def test_invalid_stored_profile_falls_back_visibly_to_restricted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "machine" / "configuration.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_configuration("unbounded")), encoding="utf-8")

    effective = load_effective_manager_runtime_profile(tmp_path)

    assert effective["status"] == "configuration_invalid"
    assert effective["runtime_profile"] == "restricted"
    assert effective["sandbox"] == "read-only"
    assert effective["source"] == "invalid_configuration_fallback"
    assert effective["repair"].startswith("Open machine capability settings")
