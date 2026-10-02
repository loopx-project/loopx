#!/usr/bin/env python3
"""Guard the public showcase animation path against private/live sources."""

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SHOWCASE_INDEX = REPO_ROOT / "docs" / "showcases" / "README.md"
CATALOG = REPO_ROOT / "docs" / "showcases" / "showcase-catalog.json"
STORYBOARD = REPO_ROOT / "docs" / "showcases" / "showcase-animation-storyboard.json"

FORBIDDEN_SOURCE_PROMOTIONS = (
    "status.frontstage-share.json",
    "status.local.json",
    "registry.global.json",
    ".loopx/registry.json",
    ".codex/goals/",
    ".loopx/goals/",
    "ACTIVE_GOAL_STATE.md",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def assert_required_content() -> None:
    showcase_index = read(SHOWCASE_INDEX)
    assert "showcase-animation-storyboard.json" in showcase_index, SHOWCASE_INDEX
    assert "showcase-catalog.json` as the only case data source" in showcase_index, SHOWCASE_INDEX
    assert "showcase-animation-prototype-smoke.py" in showcase_index, SHOWCASE_INDEX


def assert_no_live_source_promotion() -> None:
    showcase_index = read(SHOWCASE_INDEX)
    storyboard = read(STORYBOARD)
    for marker in FORBIDDEN_SOURCE_PROMOTIONS:
        assert marker not in showcase_index, (
            f"{SHOWCASE_INDEX}: live/private source marker {marker!r}"
        )
        assert marker not in storyboard, f"{STORYBOARD}: live/private source marker {marker!r}"


def assert_catalog_is_frontstage_ready() -> None:
    catalog = json.loads(read(CATALOG))
    assert catalog.get("schema_version") in {
        "loopx_showcase_catalog_v0",
        "loopx_showcase_catalog_v1",
    }, catalog
    cases = catalog.get("cases")
    assert isinstance(cases, list) and len(cases) >= 4, catalog
    for case in cases:
        assert case.get("id"), case
        assert case.get("headline"), case
        assert case.get("evidence_boundary"), case
        frontend = case.get("frontend_card")
        appendix = case.get("appendix_surface")
        assert isinstance(frontend, dict) or isinstance(appendix, dict), case
        if isinstance(frontend, dict):
            beats = frontend.get("story_beats")
            assert isinstance(beats, list) and len(beats) >= 3, case
        else:
            assert appendix.get("public_surface") == "appendix_only", case


def assert_storyboard_uses_public_catalog() -> None:
    catalog = json.loads(read(CATALOG))
    catalog_ids = {case["id"] for case in catalog["cases"]}

    storyboard = json.loads(read(STORYBOARD))
    assert storyboard.get("schema_version") == "loopx_showcase_animation_storyboard_v0", storyboard
    assert storyboard.get("source_catalog") == "docs/showcases/showcase-catalog.json", storyboard
    assert storyboard.get("duration_seconds_target") == {"min": 20, "max": 30}, storyboard

    boundary = storyboard.get("public_boundary")
    assert isinstance(boundary, dict), storyboard
    for key in (
        "live_registry_state",
        "local_status_exports",
        "user_specific_active_state",
        "private_docs_or_chats",
        "raw_benchmark_traces",
        "internal_project_names",
    ):
        assert boundary.get(key) is False, (key, boundary)

    scenes = storyboard.get("scenes")
    assert isinstance(scenes, list) and len(scenes) >= 5, storyboard
    referenced_ids: set[str] = set()
    for scene in scenes:
        assert scene.get("id"), scene
        assert scene.get("copy"), scene
        assert scene.get("visual"), scene
        source_case_ids = scene.get("source_case_ids")
        assert isinstance(source_case_ids, list), scene
        for case_id in source_case_ids:
            assert case_id in catalog_ids, (case_id, scene)
            referenced_ids.add(case_id)

    assert referenced_ids, referenced_ids
    assert referenced_ids <= catalog_ids, (referenced_ids, catalog_ids)


def main() -> int:
    assert_required_content()
    assert_no_live_source_promotion()
    assert_catalog_is_frontstage_ready()
    assert_storyboard_uses_public_catalog()
    print("showcase-animation-source-boundary-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
