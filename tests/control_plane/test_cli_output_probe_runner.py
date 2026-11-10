from __future__ import annotations

import json
import copy
import os
import runpy
import re
import shlex
import shutil
import subprocess
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


def _quota_control_fixture():
    """Independent control values shared by the historical and complete codecs."""
    value = {
        "mode": "should-run", "goal_id": "fixture-goal", "decision": "run",
        "should_run": True, "effective_action": "normal_run",
        "agent_identity": {"agent_id": "worker-a"},
        "selected_todo": {"todo_id": "todo_fixture", "text": "Verify the decisive tail constraint.",
            "priority": "P0", "status": "open", "task_class": "advancement_task",
            "action_kind": "implement", "claimed_by": "worker-a"},
        "goal_boundary": {"rule": "stay_in_scope_or_stop", "write_scope": ["docs/**"],
            "stop_condition": "Stop before any unapproved external effect."},
        "interaction_contract": {
            "agent_channel": {"must_attempt": True, "delivery_allowed": True, "quiet_noop_allowed": False,
                "required_reads": [{"source": "goal_state", "ordering": "before_work", "command": "cat goal.md"},
                                   {"source": "fixture", "ordering": "before_work", "command": "cat tail.md"}]},
            "cli_channel": {"next_cli_actions": ["refresh original-turn", "spend original-turn"],
                "spend_allowed_now": False, "spend_after_validation": True, "spend_policy": "once after verified writeback"},
            "user_channel": {"notify": "DONT_NOTIFY", "action_required": False},
        },
        "work_lane_contract": {"must_attempt_work": True, "obligation": "advance_one_bounded_segment"},
        "execution_obligation": {"must_attempt_work": True, "notify_is_execution_gate": False},
        "execution_profile": {"minimum_scale": "multi_surface_or_implementation", "numeric_fixture": 1.0},
        "task_scope": "goal_all_read_claimed_run_global_read_v0", "replan_action_packet": None,
        "scheduler_hint": {"action": "none"},
    }
    return value


def test_quota_observation_preserves_current_controls_across_declared_carriers():
    observe = runpy.run_path(str(RUNNER))["_quota_control_observation"]
    source = _quota_control_fixture()
    complete = {**copy.deepcopy(source), "schema_version": "loopx_turn_envelope_v1"}
    legacy = {key: source[key] for key in ("mode", "goal_id", "decision", "should_run", "effective_action")}
    legacy.update(schema_version="loopx_turn_envelope_v0", agent_id="worker-a",
        action={**source["interaction_contract"]["agent_channel"], "selected_todo": {
            **{k: v for k, v in source["selected_todo"].items() if k != "text"},
            "text_ref": "action.recommended_action"}, "recommended_action": source["selected_todo"]["text"]},
        required_reads=source["interaction_contract"]["agent_channel"]["required_reads"],
        writeback=source["interaction_contract"]["cli_channel"],
        user=source["interaction_contract"]["user_channel"], boundary=source["goal_boundary"],
        scheduler=source["scheduler_hint"], contract_capsule={key: source[key] for key in (
            "work_lane_contract", "execution_obligation", "execution_profile", "task_scope")})
    old, current = observe(legacy), observe(complete)
    assert old["controls_sha256"] == current["controls_sha256"] == observe(source)["controls_sha256"]
    assert old["field_sha256"] == current["field_sha256"]
    assert set(current["field_sha256"]) == {
        "goal_id", "agent_id", "decision", "should_run", "effective_action", "selected_todo",
        "execution", "boundary", "required_reads", "next_cli_actions", "spend", "work_lane_contract",
        "execution_obligation", "execution_profile", "task_scope", "replan_action_packet",
        "notify", "user_action_required", "scheduler_action",
    }
    assert old["source_schema"] != current["source_schema"]
    legacy["action"]["selected_todo"]["text_ref"] = "unrelated.text"
    with pytest.raises(AssertionError):
        observe(legacy)
    assert observe({**complete, "schema_version": "unknown"}) is None


@pytest.mark.parametrize("path,replacement", [
    (("goal_id",), "wrong-goal"), (("agent_identity", "agent_id"), "other-worker"),
    (("decision",), "skip"), (("should_run",), False), (("effective_action",), "quota_skip"),
    (("selected_todo", "text"), "Omit the decisive tail constraint."),
    (("goal_boundary", "stop_condition"), "Ignore the original stop."),
    (("interaction_contract", "agent_channel", "delivery_allowed"), False),
    (("interaction_contract", "agent_channel", "required_reads"), []),
    (("interaction_contract", "cli_channel", "next_cli_actions"), ["spend different-turn"]),
    (("interaction_contract", "cli_channel", "spend_allowed_now"), True),
    (("work_lane_contract", "must_attempt_work"), False),
    (("execution_obligation", "notify_is_execution_gate"), True),
    (("execution_profile", "numeric_fixture"), 1),  # 1 and 1.0 are distinct source values.
    (("task_scope",), "wrong-scope"), (("replan_action_packet",), {"required": True}),
    (("interaction_contract", "user_channel", "notify"), "NOTIFY"),
    (("interaction_contract", "user_channel", "action_required"), True),
    (("scheduler_hint", "action"), "apply"),
])
def test_quota_observation_rejects_changed_control_values(path, replacement):
    observe = runpy.run_path(str(RUNNER))["_quota_control_observation"]
    original = _quota_control_fixture()
    changed = copy.deepcopy(original)
    parent = changed
    for field in path[:-1]:
        parent = parent[field]
    parent[path[-1]] = replacement
    assert observe(original)["controls_sha256"] != observe(changed)["controls_sha256"]
    assert observe(original)["field_sha256"] != observe(changed)["field_sha256"]


def test_legacy_markdown_control_basis_requires_actual_matching_printed_values():
    runner = runpy.run_path(str(RUNNER))
    source = _quota_control_fixture()
    observation = runner["_quota_control_observation"](source)
    body = "# LoopX Quota Should Run\n\n" + "\n".join([
        "- goal_id: `fixture-goal`", "- agent_identity: agent_id=worker-a agent_model=peer_v1",
        "- decision: `run`", "- should_run: `True`", "- effective_action: `normal_run`",
    ])
    fields = runner["_quota_markdown_field_observations"](body)
    assert len(fields) == 5 and all(observation["field_sha256"][k] == v for k, v in fields.items())
    assert not runner["_quota_markdown_field_observations"](body + "\n- goal_id: `duplicate`")
    assert not runner["_quota_markdown_field_observations"](body + "\n- agent_id: `worker-a`")
    assert not runner["_quota_markdown_field_observations"](body.replace("- decision: `run`", ""))
    base = {"row_id": "surface/quota_should_run/small/json", "format": "json",
        "continuation_instructions": {}, "quota_controls": observation}
    rendered = {"row_id": "surface/quota_should_run/small/markdown", "format": "markdown",
        "quota_markdown_fields": fields}
    assert runner["_link_turn_warning_basis"]([base, rendered])[1]["quota_controls"] == observation
    rendered = {**rendered, "quota_markdown_fields": runner["_quota_markdown_field_observations"](
        body.replace("fixture-goal", "wrong-goal"))}
    with pytest.raises(AssertionError, match="Markdown quota controls differ"):
        runner["_link_turn_warning_basis"]([base, rendered])


def test_runner_imports_frozen_support_with_an_older_checkout(tmp_path: Path) -> None:
    workload = tmp_path / "workload"
    workload.mkdir()
    runner = workload / RUNNER.name
    shutil.copyfile(RUNNER, runner)
    shutil.copyfile(cli_output_fixture.__file__, workload / "cli_output_fixture.py")

    historical = tmp_path / "historical"
    legacy_tests = historical / "tests" / "control_plane"
    legacy_tests.mkdir(parents=True)
    for package in (legacy_tests, legacy_tests.parent):
        (package / "__init__.py").touch()
    (legacy_tests / "cli_output_fixture.py").write_text(
        "# Older checkout with no current observation helper.\n", encoding="utf-8"
    )
    probe_source = workload / "probe.py"
    probe_source.write_text(
        "from pathlib import Path\n"
        "from tests.control_plane.cli_output_fixture import measure_cli_context_for_oracle\n"
        "assert Path(measure_cli_context_for_oracle.__code__.co_filename) == "
        "Path(__file__).with_name('cli_output_fixture.py')\n"
        "print('frozen fixture support loaded')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable, str(runner),
            "--test-source", str(probe_source),
            "--semantics-source", str(workload / "unused_semantics.py"),
            "--fixture-root", str(workload / "unused_fixture"),
            "--receipt", str(workload / "unused_receipt.json"),
        ],
        cwd=workload,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(historical), str(RUNNER.parents[2]))),
            "LOOPX_USAGE_PING": "0",
        },
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "frozen fixture support loaded"


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
        "$.work_contract.replan_action_packet.writeback_contract.vision_authoring"
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


@pytest.mark.parametrize("output_format", ["json", "markdown"])
def test_reader_preserves_unicode_body_route_and_all_output_cost(monkeypatch, output_format):
    navigation, body, expected, outputs, calls = _reader_exchange(monkeypatch)
    if output_format == "markdown":
        navigation = "# LoopX Turn Envelope\n\n```json\n" + navigation + "\n```"
    measurement = cli_output_fixture.measure_cli_context_for_oracle(navigation, output_format=output_format,
        measure=probe.measure_cli_output)
    assert measurement["resolved_payload"] == expected
    if output_format == "json":
        assert measurement["payload"] == json.loads(navigation)
    else:
        assert measurement["payload"] is None
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
@pytest.mark.parametrize("output_format", ["json", "markdown"])
def test_reader_refuses_incomplete_or_changed_recovery(monkeypatch, defect, output_format):
    navigation, *_rest, calls = _reader_exchange(monkeypatch, defect=defect)
    if output_format == "markdown":
        navigation = "# LoopX Turn Envelope\n\n```json\n" + navigation + "\n```"
    with pytest.raises(AssertionError):
        cli_output_fixture.read_cli_context_for_oracle(navigation, output_format=output_format)
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
        state_file=state, output_format="json", context_delivery="on")["quota_should_run"]
    command += ["--codex-app", "--turn-instance-id", "public-reader",
        "--codex-context-root", str(tmp_path),
        "--decision-output-root", str(tmp_path)]
    code, navigation = probe._invoke_cli(command)
    assert code == 0, navigation
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


def test_complete_plan_receipt_checks_original_goal_bytes(tmp_path):
    receipt_row = runpy.run_path(str(RUNNER))["_receipt_row"]
    with probe._stable_budget_fixture_root(tmp_path) as root:
        project, runtime, registry, state = probe._write_fixture(root, probe.SCENARIOS[0])
        from loopx.control_plane.testing.canary_harness import import_fixture_authority
        import_fixture_authority(registry, probe.GOAL_ID)
        command = probe._mode_variant_commands(project=project, runtime=runtime,
            registry_path=registry, state_file=state, output_format="json")["loopx_turn_plan_transaction_detail"]
        code, text = probe._invoke_cli(command)
        assert code == 0, text
        kwargs = dict(semantics=cli_output_semantics,
            row_id="variant/loopx_turn_plan_transaction_detail/small/json",
            surface_id="loopx_turn_plan", scenario="small", output_format="json",
            qualification_policy="explicit_opt_in_cold_path", semantic_json_keys=("work_contract",),
            markdown_anchor="", text=text)
        measure = probe.measure_cli_output(text, output_format="json")
        observation = receipt_row(**kwargs, measurement=measure)
        assert observation["turn_plan_controls"]["plan_version"] == "loopx_turn_plan_v1"
        source = measure["payload"]["work_contract"]
        goal = next(row for row in source["interaction_contract"]["agent_channel"]["work_context"]["sources"]
                    if row["source"] == "goal_state")
        goal["content"]["text"] += "\nChanged acceptance or stop condition\n"
        with pytest.raises(AssertionError):
            receipt_row(**kwargs, measurement=measure)


@pytest.mark.parametrize("output_format", ["json", "markdown"])
def test_signed_todo_row_projection_recovers_its_original_decision(tmp_path, output_format):
    from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision
    from loopx.control_plane.testing.canary_harness import import_fixture_authority
    from loopx.rollout_event_log import rollout_event_log_path

    project, runtime, registry, state = probe._write_fixture(tmp_path, probe.SCENARIOS[2])
    # Partial overlap between lanes permits row references even when there is
    # no outcome history to defer. Make that projection worth its reader cost.
    state.write_text(state.read_text().replace("without reading archival detail.",
        "preserving the original constraint. " * 36))
    import_fixture_authority(registry, probe.GOAL_ID)
    command = probe._surface_commands(project=project, runtime=runtime, registry_path=registry,
        state_file=state, output_format=output_format, context_delivery="auto")["quota_should_run"]
    code, navigation = probe._invoke_cli([
        *command, "--todo-id", "todo_fixture_000", "--turn-instance-id", "fixture-signed-rows",
    ])
    assert code == 0, navigation
    events = rollout_event_log_path(runtime, probe.GOAL_ID)
    before = events.read_bytes()
    measurement = cli_output_fixture.measure_cli_context_for_oracle(navigation,
        output_format=output_format, measure=probe.measure_cli_output)
    required = measurement["resolved_payload"]
    view = json.dumps(required, ensure_ascii=False)
    spec = probe.CLI_OUTPUT_BUDGET_BY_ID["quota_should_run"]
    from loopx.control_plane.testing.cli_output_budget import assert_cli_output_semantics
    assert_cli_output_semantics("quota_should_run", output_format=output_format,
        text=navigation, measurement=measurement, semantic_json_keys=spec.semantic_json_keys,
        markdown_anchor=spec.markdown_anchor, output_contract_version=spec.output_contract_version)
    row = runpy.run_path(str(RUNNER))["_receipt_row"](semantics=cli_output_semantics,
        row_id=f"surface/quota_should_run/multi_agent/{output_format}",
        surface_id="quota_should_run", scenario="multi_agent", output_format=output_format,
        qualification_policy=spec.qualification_policy, semantic_json_keys=spec.semantic_json_keys,
        markdown_anchor=spec.markdown_anchor, output_contract_version=spec.output_contract_version,
        measurement=measurement, text=navigation)
    assert len(row["quota_controls"]["field_sha256"]) == 19
    assert "context_reference_instruction" in required
    route = shlex.split(required["context_details"]["command_template"])
    assert route[-2] == "--select"
    source, _, _ = cli_output_fixture.read_pinned_cli_body(shlex.join(route[:-2]))
    original = json.loads(source)
    assert len(view.encode()) < len(source.encode())
    assert original["contract_binding"] == required["contract_binding"]
    assert_complete_turn_decision(original)
    assert events.read_bytes() == before
