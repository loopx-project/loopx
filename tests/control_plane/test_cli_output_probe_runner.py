from __future__ import annotations

import runpy
from pathlib import Path

import pytest

from loopx.control_plane.testing import cli_output_semantics
from tests.control_plane import test_cli_output_budget as probe


RUNNER = (
    Path(__file__).resolve().parents[2]
    / "examples/control_plane/cli-output-probe-runner.py"
)


@pytest.fixture
def crowded_turn_probe(monkeypatch: pytest.MonkeyPatch):
    commands = probe._surface_commands
    monkeypatch.setattr(probe, "SCENARIOS", (probe.SCENARIOS[1],))

    def turn_json_only(**kwargs):
        if kwargs["output_format"] != "json":
            return {}
        return {"loopx_turn_plan": commands(**kwargs)["loopx_turn_plan"]}

    monkeypatch.setattr(probe, "_surface_commands", turn_json_only)
    return runpy.run_path(str(RUNNER))["_default_rows"]


@pytest.mark.parametrize("layout", ["short", "long-runner-layout" * 8])
def test_runner_uses_the_pytest_scenario_alias_without_rewriting_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crowded_turn_probe,
    layout: str,
) -> None:
    fixture_roots: list[Path] = []
    write_fixture = probe._write_fixture
    invoke_cli = probe._invoke_cli
    emitted: list[str] = []

    def capture_fixture(root, scenario):
        fixture_roots.append(root)
        assert (scenario.todo_count, scenario.agent_count, scenario.run_count) == (
            36,
            1,
            12,
        )
        return write_fixture(root, scenario)

    def capture_stdout(command):
        rc, text = invoke_cli(command)
        emitted.append(text)
        return rc, text

    monkeypatch.setattr(probe, "_write_fixture", capture_fixture)
    monkeypatch.setattr(probe, "_invoke_cli", capture_stdout)
    rows = crowded_turn_probe(probe, cli_output_semantics, tmp_path / layout)

    assert len(rows) == len(emitted) == len(fixture_roots) == 1
    root = fixture_roots[0]
    assert root.parent == Path("/tmp")
    assert root.name.startswith("loopx-cli-budget-")
    assert len(root.name.removeprefix("loopx-cli-budget-")) == 12
    assert not root.exists()  # The shared context cleans up its alias.
    assert str(root) in emitted[0]  # Full CLI command paths are still emitted.
    assert rows[0]["row_id"] == "surface/loopx_turn_plan/crowded/json"
    assert rows[0]["chars"] == len(emitted[0])
    assert (
        "$.turn_envelope.replan_action_packet.writeback_contract.vision_authoring"
        in (rows[0]["json_shape_paths"])
    )


def test_runner_still_rejects_actual_stdout_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crowded_turn_probe,
) -> None:
    invoke_cli = probe._invoke_cli

    def oversized_stdout(command):
        rc, text = invoke_cli(command)
        return rc, text + " " * 15_000

    monkeypatch.setattr(probe, "_invoke_cli", oversized_stdout)
    with pytest.raises(AssertionError, match="baseline ceiling is 14500"):
        crowded_turn_probe(probe, cli_output_semantics, tmp_path / "growth")
