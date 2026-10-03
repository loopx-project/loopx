"""Interface-budget fixtures must never select an operator's default route."""

from pathlib import Path
import runpy

import pytest

from loopx import paths


def test_budget_smoke_isolates_and_restores_ambient_routes(
    monkeypatch, capsys, tmp_path
):
    smoke = (
        Path(__file__).resolve().parents[2]
        / "examples/control_plane/hot-path-interface-budget-smoke.py"
    )
    current, legacy = tmp_path / "current", tmp_path / "legacy"
    for root in (current, legacy):
        root.mkdir()
        (root / paths.GLOBAL_REGISTRY_FILENAME).write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", current)
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", legacy)
    with pytest.raises(ValueError, match="Both default LoopX registries exist"):
        paths.select_default_runtime_root()

    assert runpy.run_path(str(smoke))["main"]() == 0
    assert "hot-path-interface-budget-smoke ok" in capsys.readouterr().out
    assert paths.DEFAULT_RUNTIME_ROOT == current
    assert paths.LEGACY_RUNTIME_ROOT == legacy
    for root in (current, legacy):
        assert (root / paths.GLOBAL_REGISTRY_FILENAME).read_text(
            encoding="utf-8"
        ) == "{}\n"
    with pytest.raises(ValueError, match="Both default LoopX registries exist"):
        paths.select_default_runtime_root()
