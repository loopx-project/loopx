"""Explicit source routes must not select an ambiguous machine default."""
import json
from pathlib import Path

import pytest

from loopx import paths
from loopx.control_plane.runtime.runtime_projection_route import (
    resolve_runtime_projection_route,
    runtime_projection_candidate_roots,
)


@pytest.fixture
def conflicting_defaults(tmp_path, monkeypatch):
    current, legacy = tmp_path / "current", tmp_path / "legacy"
    for root in (current, legacy):
        root.mkdir()
        (root / "machine-state.json").write_text("{}")
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", current)
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", legacy)
    monkeypatch.delenv("LOOPX_RUNTIME_ROOT", raising=False)
    assert paths.default_runtime_route()["status"] == "conflict"
    return current, legacy


def write_mirror(root, source):
    (root / "registry.global.json").write_text(json.dumps({"goals": [{
        "id": "bounded-goal", "source_registry": str(source),
    }]}))


def test_discovery_keeps_explicit_source_without_choosing_default(conflicting_defaults, tmp_path):
    current, legacy = conflicting_defaults
    source = tmp_path / "source"
    before = [sorted(root.iterdir()) for root in (current, legacy)]
    assert runtime_projection_candidate_roots(source_runtime_root=source) == [current, legacy, source]
    with pytest.raises(ValueError, match="Both default LoopX runtime roots"):
        paths.select_default_runtime_root()
    route = resolve_runtime_projection_route(registry_path=source / "registry.json",
        goal_id="bounded-goal", source_runtime_root=source)
    assert route["status"] == "single_runtime"
    assert Path(route["target_runtime_root"]) == source
    assert [sorted(root.iterdir()) for root in (current, legacy)] == before


@pytest.mark.parametrize("selected", [0, 1])
def test_one_declared_mirror_resolves_without_touching_the_other(conflicting_defaults, tmp_path, selected):
    roots = conflicting_defaults
    source = tmp_path / "source" / "registry.json"
    write_mirror(roots[selected], source)
    before = (roots[selected] / "registry.global.json").read_bytes()
    route = resolve_runtime_projection_route(registry_path=source, goal_id="bounded-goal",
        source_runtime_root=source.parent)
    assert route["status"] == "resolved"
    assert Path(route["target_runtime_root"]) == roots[selected]
    assert (roots[selected] / "registry.global.json").read_bytes() == before


def test_two_declared_mirrors_remain_ambiguous(conflicting_defaults, tmp_path):
    source = tmp_path / "source" / "registry.json"
    for root in conflicting_defaults:
        write_mirror(root, source)
    before = [(root / "registry.global.json").read_bytes() for root in conflicting_defaults]
    route = resolve_runtime_projection_route(registry_path=source, goal_id="bounded-goal",
        source_runtime_root=source.parent)
    assert route["status"] == "ambiguous"
    assert route["target_runtime_root"] is None
    assert route["projection_required"] is True
    assert [(root / "registry.global.json").read_bytes() for root in conflicting_defaults] == before


def test_explicit_candidates_do_not_inspect_default_stores(conflicting_defaults, tmp_path, monkeypatch):
    def forbidden():
        pytest.fail("explicit candidates must not inspect machine defaults")

    monkeypatch.setattr(paths, "default_runtime_route", forbidden)
    source = tmp_path / "source"
    assert runtime_projection_candidate_roots(source_runtime_root=source,
        candidate_roots=[source]) == [source]


def test_invalid_default_does_not_override_explicit_source(conflicting_defaults):
    current, legacy = conflicting_defaults
    (current / "registry.global.json").symlink_to(legacy / "missing.json")
    with pytest.raises(ValueError, match="not a regular file"):
        paths.select_default_runtime_root()
    assert runtime_projection_candidate_roots(source_runtime_root=legacy) == [current, legacy]
