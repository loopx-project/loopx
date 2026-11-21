from __future__ import annotations

import copy

import pytest

from loopx.capabilities.content_ops.surface import (
    build_content_ops_surface_fixture,
    project_content_ops_surface,
    validate_content_ops_surface,
)


def _surface_with_angle_source_ids(source_item_ids: object) -> dict[str, object]:
    surface = copy.deepcopy(build_content_ops_surface_fixture())
    angles = surface["angle_candidates"]
    assert isinstance(angles, list)
    first_angle = angles[0]
    assert isinstance(first_angle, dict)
    if source_item_ids is ...:
        first_angle.pop("source_item_ids", None)
    else:
        first_angle["source_item_ids"] = source_item_ids
    return surface


def test_validate_surface_accepts_valid_source_item_ids_list() -> None:
    surface = _surface_with_angle_source_ids(["source_demo_public_feed_001"])

    validation = validate_content_ops_surface(surface)
    projection = project_content_ops_surface(surface)

    assert validation["ok"] is True
    assert validation["errors"] == []
    assert projection["validation"] == validation


def test_validate_surface_rejects_unknown_source_item_ids_in_list() -> None:
    surface = _surface_with_angle_source_ids(["missing-source"])

    validation = validate_content_ops_surface(surface)
    projection = project_content_ops_surface(surface)

    assert validation["ok"] is False
    assert validation["errors"] == [
        "angle angle_source_aware_loop references unknown source missing-source"
    ]
    assert projection["validation"] == validation


@pytest.mark.parametrize(
    "malformed_source_ids",
    [
        "missing-source",
        "",
        {"missing-source": True},
        {"source_demo_public_feed_001": True},
        {},
        b"missing-source",
        bytearray(b"missing-source"),
        123,
        True,
    ],
)
def test_validate_surface_rejects_malformed_source_item_ids_containers(
    malformed_source_ids: object,
) -> None:
    surface = _surface_with_angle_source_ids(malformed_source_ids)

    validation = validate_content_ops_surface(surface)
    projection = project_content_ops_surface(surface)

    assert validation["ok"] is False
    assert validation["errors"] == [
        "angle angle_source_aware_loop has invalid source_item_ids"
    ]
    assert projection["validation"] == validation


@pytest.mark.parametrize("empty_or_omitted", [[], None, ...])
def test_validate_surface_preserves_empty_or_missing_source_item_ids(
    empty_or_omitted: object,
) -> None:
    surface = _surface_with_angle_source_ids(empty_or_omitted)

    validation = validate_content_ops_surface(surface)

    assert validation["ok"] is True
    assert validation["errors"] == []
