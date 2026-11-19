"""Fixture preparation uses real authority; model/source identities are test doubles."""

from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from loopx.control_plane.testing.replan_semantic_action_behavior import (
    _build_fixture as build_replan_fixture,
)
from loopx.control_plane.testing.terminal_settlement_tool_behavior import (
    _build_fixture as build_terminal_fixture,
)


@pytest.mark.parametrize("use_alias", [False, True], ids=["physical", "symlink"])
def test_live_runner_prepares_canonical_fixtures_through_physical_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, use_alias: bool,
) -> None:
    physical = tmp_path / "physical"
    physical.mkdir()
    temporary = physical
    if use_alias:
        temporary = tmp_path / "alias"
        try:
            temporary.symlink_to(physical, target_is_directory=True)
        except OSError:
            pytest.skip("creating a directory symlink is unavailable")

    source = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "live_portfolio_fixture", source / "scripts/qualify-doubao-model-behavior-live.py",
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner, "collect_release_source_identity", lambda _: {"git_dirty": False})
    monkeypatch.setattr(sys, "argv", [str(spec.origin), "--qualification-id", "offline-path-fixture"])

    @contextmanager
    def temporary_directory(**_):
        yield str(temporary)

    monkeypatch.setattr(runner, "TemporaryDirectory", temporary_directory)
    monkeypatch.setattr(runner, "build_actual_default_model_behavior_scenario_inputs", lambda _: ({}, {}))
    prepared = []

    def factory(prepare=None):
        def qualify(*, fixture_root, qualification_id, **_):
            # Exercise the existing registry witness and canonical File creation,
            # rather than supplying the postcondition that rejected the alias.
            fixture = prepare(fixture_root)
            prepared.append(fixture.project_root)
            return {"qualification_passed": True}

        return lambda **_: SimpleNamespace(qualify=qualify)

    for name in (
        "DoubaoModelBehaviorActor", "DoubaoOnboardingModelBehaviorActor",
        "DoubaoSelectedTodoToolBehaviorActor", "DoubaoScopedGateSuccessorToolBehaviorActor",
        "DoubaoCapabilityMonitorRepairToolBehaviorActor",
    ):
        monkeypatch.setattr(getattr(runner, name), "from_environment", factory())
    monkeypatch.setattr(runner.DoubaoTerminalSettlementToolBehaviorActor,
                        "from_environment", factory(build_terminal_fixture))
    monkeypatch.setattr(runner.DoubaoReplanSemanticActionBehaviorActor,
                        "from_environment", factory(build_replan_fixture))

    def portfolio(*_, terminal_settlement_actor, replan_semantic_action_actor, **__):
        terminal_settlement_actor("terminal")
        replan_semantic_action_actor("replan")
        return {"qualification_passed": True}

    monkeypatch.setattr(runner, "run_actual_default_model_behavior_portfolio", portfolio)
    assert runner.main() == 0
    assert len(prepared) == 2
    assert all(path == path.resolve() and path.is_relative_to(physical.resolve()) for path in prepared)
    assert json.loads(capsys.readouterr().out)["provider_call_count"] == 0
