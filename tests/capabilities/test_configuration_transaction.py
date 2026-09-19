from __future__ import annotations

import pytest

from loopx.configuration_transaction import (
    CONFIGURATION_REVISION_MISSING,
    build_configuration_update_plan,
    configuration_payload_revision,
    require_expected_configuration_plan_revision,
)


def _plan(
    *,
    current: dict[str, object] | None,
    desired: dict[str, object] | None,
) -> dict[str, object]:
    return build_configuration_update_plan(
        schema_version="example_configuration_plan_v0",
        current_present=current is not None,
        desired_present=desired is not None,
        current_revision=(
            configuration_payload_revision(current)
            if current is not None
            else CONFIGURATION_REVISION_MISSING
        ),
        desired_revision=(
            configuration_payload_revision(desired)
            if desired is not None
            else CONFIGURATION_REVISION_MISSING
        ),
        target_identity={"configuration_ref": "configuration/example.json"},
        changed_units={"changed_capabilities": ["example"]},
        projected_configuration=desired,
        projection_field="configuration",
    )


@pytest.mark.parametrize(
    ("current", "desired", "action", "writes_required"),
    [
        (None, {"enabled": True}, "create", 1),
        ({"enabled": True}, {"enabled": False}, "update", 1),
        ({"enabled": True}, None, "delete", 1),
        ({"enabled": True}, {"enabled": True}, "unchanged", 0),
        (None, None, "unchanged", 0),
    ],
)
def test_update_plan_has_shared_action_and_revision_semantics(
    current: dict[str, object] | None,
    desired: dict[str, object] | None,
    action: str,
    writes_required: int,
) -> None:
    plan = _plan(current=current, desired=desired)

    assert plan["schema_version"] == "example_configuration_plan_v0"
    assert plan["action"] == action
    assert plan["writes_required"] == writes_required
    assert str(plan["plan_revision"]).startswith("sha256:")
    assert plan["configuration"] == desired


def test_update_plan_rejects_presence_revision_mismatch() -> None:
    with pytest.raises(ValueError, match="current presence"):
        build_configuration_update_plan(
            schema_version="example_configuration_plan_v0",
            current_present=True,
            desired_present=False,
            current_revision=CONFIGURATION_REVISION_MISSING,
            desired_revision=CONFIGURATION_REVISION_MISSING,
            target_identity={},
            changed_units={},
            projected_configuration=None,
            projection_field="configuration",
        )


def test_update_plan_can_require_a_host_write_when_configuration_is_unchanged() -> None:
    configuration = {"enabled": True, "max_children": 6}
    revision = configuration_payload_revision(configuration)

    plan = build_configuration_update_plan(
        schema_version="example_configuration_plan_v0",
        current_present=True,
        desired_present=True,
        current_revision=revision,
        desired_revision=revision,
        target_identity={"configuration_ref": "configuration/example.json"},
        changed_units={"changed_capabilities": []},
        projected_configuration=configuration,
        projection_field="configuration",
        additional_write_required=True,
    )

    assert plan["action"] == "update"
    assert plan["writes_required"] == 1
    assert plan["current_revision"] == plan["desired_revision"]


def test_apply_requires_exact_preview_revision() -> None:
    plan = _plan(current=None, desired={"enabled": True})
    require_expected_configuration_plan_revision(
        expected_plan_revision=str(plan["plan_revision"]),
        actual_plan_revision=str(plan["plan_revision"]),
        subject="example configuration",
    )

    with pytest.raises(ValueError, match="expected_plan_revision is required"):
        require_expected_configuration_plan_revision(
            expected_plan_revision=None,
            actual_plan_revision=str(plan["plan_revision"]),
            subject="example configuration",
        )
    with pytest.raises(ValueError, match="preview again"):
        require_expected_configuration_plan_revision(
            expected_plan_revision="sha256:" + "0" * 64,
            actual_plan_revision=str(plan["plan_revision"]),
            subject="example configuration",
        )
