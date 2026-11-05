from __future__ import annotations

import json
import runpy
import re
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.control_plane.testing import cli_output_semantics
from tests.control_plane import test_cli_output_budget as probe
from tests.control_plane import cli_output_fixture


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

    def assert_crowded_turn_json_matrix(measurements):
        # This alias test deliberately samples one JSON surface, whereas the
        # production probe qualifies every surface/format and both scenarios.
        assert set(measurements) == {"crowded"}
        assert set(measurements["crowded"]) == {"loopx_turn_plan"}
        formats = measurements["crowded"]["loopx_turn_plan"]
        assert set(formats) == {"json"}
        assert formats["json"]["json_parseable"] is True
        assert formats["json"]["pretty_print_overhead_chars"] > 0

    monkeypatch.setattr(probe, "_surface_commands", turn_json_only)
    monkeypatch.setattr(probe, "_assert_scenario_matrix", assert_crowded_turn_json_matrix)
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
    # Full paths remain in the untouched JSON wire, including escaped Windows backslashes.
    assert json.dumps(str(root))[1:-1] in emitted[0]
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
    ceiling = probe.CLI_OUTPUT_BUDGET_BY_ID["loopx_turn_plan"].max_chars["crowded"]["json"]
    with pytest.raises(AssertionError, match=re.escape(f"baseline ceiling is {ceiling}")):
        crowded_turn_probe(probe, cli_output_semantics, tmp_path / "growth")


def _reader_exchange(monkeypatch, *, defect=None, padding=0, body_padding=0):
    expected = {"interaction_contract": {}, "scheduler_hint": {}, "selected_todo": None,
        "original": "保留原文与停止条件", "padding": "x" * body_padding}
    body = json.dumps(expected, ensure_ascii=False)
    split = body.index("原文")
    chunks = [body[:split], body[split:]]
    argv = [sys.executable, "-m", "loopx.entrypoint", "--format", "json", "quota", "context-read",
        "--capture-file", "synthetic-capture.json", "--capture-sha256", "1" * 64,
        "--offset", "0", "--page-bytes", "8192"]
    next_argv = [*argv]
    next_argv[next_argv.index("--offset") + 1] = str(len(chunks[0].encode()))
    pages = []
    for index, chunk in enumerate(chunks):
        page = {"ok": True, "sha256": "1" * 64, "offset": 0 if index == 0 else len(chunks[0].encode()),
            "content": chunk, "total_bytes": len(body.encode()), "eof": index == 1}
        if index == 0:
            page.update(next_offset=len(chunk.encode()), next_command=shlex.join(next_argv))
        pages.append(page)
    if defect == "gap":
        pages[1]["offset"] += 1
    elif defect == "digest":
        pages[1]["sha256"] = "2" * 64
    elif defect == "early_eof":
        for page in pages:
            page["total_bytes"] += 1
    elif defect == "route":
        pages[0]["next_command"] = pages[0]["next_command"].replace("synthetic-capture", "another-capture")
    elif defect == "no_progress":
        pages[0]["content"] = ""
    if defect == "guard":
        argv[argv.index("context-read")] = "should-run"
    outputs = [json.dumps(page, ensure_ascii=False) + "\n" for page in pages]
    calls = []

    def reader(command, **_):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=outputs[len(calls) - 1], stderr="")

    monkeypatch.setattr(cli_output_fixture.subprocess, "run", reader)
    navigation = json.dumps({"ok": True, "schema_version": "context_capture_navigation_v1",
        "recovery": {"command": shlex.join(argv)}}) + " " * padding
    return navigation, body, expected, outputs, calls


def test_reader_preserves_unicode_body_route_and_all_output_cost(monkeypatch):
    navigation, body, expected, outputs, calls = _reader_exchange(monkeypatch)
    measurement = cli_output_fixture.measure_cli_context_for_oracle(navigation, output_format="json",
        measure=probe.measure_cli_output)
    assert measurement["resolved_payload"] == expected
    assert measurement["payload"] == json.loads(navigation)
    assert measurement["chars"] == len(navigation)
    assert measurement["context_body"]["chars"] == len(body)
    assert measurement["context_body"]["utf8_bytes"] == len(body.encode()) > len(body)
    cost = measurement["context_delivery"]
    assert cost["reader_calls"] == len(calls) == 2
    assert cost["navigation_utf8_bytes"] == len(navigation.encode())
    assert cost["reader_stdout_utf8_bytes"] == sum(len(output.encode()) for output in outputs)
    assert cost["total_stdout_utf8_bytes"] == len(navigation.encode()) + sum(len(output.encode()) for output in outputs)
    assert calls[0][:3] == [sys.executable, "-m", "loopx.entrypoint"]
    assert all(command[command.index("quota") + 1] == "context-read" for command in calls)


@pytest.mark.parametrize("defect", ["gap", "digest", "early_eof", "route", "no_progress", "guard"])
def test_reader_refuses_incomplete_or_changed_recovery(monkeypatch, defect):
    navigation, *_rest, calls = _reader_exchange(monkeypatch, defect=defect)
    with pytest.raises(AssertionError):
        cli_output_fixture.read_cli_context_for_oracle(navigation)
    if defect == "guard":
        assert calls == []


@pytest.mark.parametrize("part", ["body", "navigation"])
def test_progressive_budget_cannot_hide_growth_in_either_part(monkeypatch, part):
    spec = probe.CLI_OUTPUT_BUDGET_BY_ID["quota_should_run"]
    ceiling = spec.max_chars["small"]["json"]
    navigation, *_ = _reader_exchange(monkeypatch,
        padding=ceiling if part == "navigation" else 0,
        body_padding=ceiling if part == "body" else 0)
    measurement = cli_output_fixture.measure_cli_context_for_oracle(navigation,
        output_format="json", measure=probe.measure_cli_output)
    with pytest.raises(AssertionError):
        probe.assert_cli_output_baseline(spec, scenario="small", output_format="json",
            text=navigation, measurement=measurement)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_public_quota_reader_preserves_long_goal_and_is_read_only(tmp_path, provider):
    from loopx.control_plane.testing.canary_harness import import_fixture_authority
    from loopx.rollout_event_log import rollout_event_log_path

    project, runtime, registry, state = probe._write_fixture(tmp_path.resolve(), probe.SCENARIOS[0])
    stop = "Preserve every original constraint. 完整保留停止条件。\n" * 1000
    state.write_text(state.read_text() + "\n## Constraints\n\n" + stop)
    import_fixture_authority(registry, probe.GOAL_ID, provider=provider)
    command = probe._surface_commands(project=project, runtime=runtime, registry_path=registry,
        state_file=state, output_format="json")["quota_should_run"]
    code, navigation = probe._invoke_cli(command)
    assert code == 0
    assert json.loads(navigation)["schema_version"] == "context_capture_navigation_v1"
    events = rollout_event_log_path(runtime, probe.GOAL_ID)
    before = events.read_bytes()
    body, cost = cli_output_fixture.read_cli_context_for_oracle(navigation)
    payload = json.loads(body)
    assert payload["goal_id"] == probe.GOAL_ID
    assert payload["selected_todo"]["todo_id"] == "todo_fixture_000"
    assert payload["selected_todo"]["agent_id"] == probe.AGENT_IDS[0]
    sources = payload["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    goal = next(row for row in sources if row["source"] == "goal_state")
    assert goal["content"]["text"].endswith(stop)
    assert cost["reader_calls"] >= 2 and cost["body_utf8_bytes"] == len(body.encode())
    assert events.read_bytes() == before
    assert cli_output_fixture.read_cli_context_for_oracle(navigation)[0] == body
    assert events.read_bytes() == before
    reference = json.loads(navigation)["recovery"]["reference_file"]
    captured = Path(json.loads(Path(reference).read_text())["path"])
    captured.write_bytes(captured.read_bytes() + b" ")
    with pytest.raises(AssertionError):
        cli_output_fixture.read_cli_context_for_oracle(navigation)
    assert events.read_bytes() == before
