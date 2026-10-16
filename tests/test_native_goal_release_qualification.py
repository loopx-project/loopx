"""Runner policy tests never start a real model, including under ordinary CI."""

import importlib.util
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_goal", REPO / "scripts/qualify-native-goal-release.py",
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_native_spawn_preserves_isolated_profile_and_secret_free_shell(monkeypatch, tmp_path):
    from loopx.capabilities.benchmark_toolkit.native_codex_goal import StdioNativeGoalTransport

    for suffix, value in {"API_KEY": "synthetic-selected-key", "MODEL": "fixture-model",
                          "BASE_URL": "https://example.com/v1"}.items():
        monkeypatch.setenv("LOOPX_CODEX_QUALIFICATION_" + suffix, value)
    monkeypatch.setenv("UNRELATED_AUTH_TOKEN", "synthetic-forbidden-key")
    monkeypatch.setenv("SSH_AUTH_SOCK", "synthetic-forbidden-socket")
    launcher = tmp_path / "bin/loopx"
    monkeypatch.setattr(runner, "setup", lambda _: (tmp_path, tmp_path / "runtime", launcher))
    prompt_loads = []
    def current_cli(_launcher, *arguments):
        if arguments[0] == "heartbeat-prompt":
            assert "--bootstrap" in arguments
            prompt_loads.append(arguments)
        return {"task_body": "Synthetic task"}
    monkeypatch.setattr(runner, "cli", current_cli)

    class InspectedSpawn(Exception):
        pass

    def inspect(command, **kwargs):
        assert len(prompt_loads) == 1
        env = kwargs["env"]
        assert env["LOOPX_USAGE_PING"] == "0"
        assert "UNRELATED_AUTH_TOKEN" not in env and "SSH_AUTH_SOCK" not in env
        settings = tomllib.loads((Path(env["CODEX_HOME"]) / "config.toml").read_text())
        policy = settings["shell_environment_policy"]
        assert policy["inherit"] == "none"
        assert policy["set"]["LOOPX_USAGE_PING"] == "0"
        assert "LOOPX_CODEX_QUALIFICATION_API_KEY" not in policy["set"]
        child = subprocess.run([sys.executable, "-c", "import os,json; print(json.dumps(dict(os.environ)))"],
                               env=policy["set"], capture_output=True, text=True, check=True)
        assert "synthetic-selected-key" not in child.stdout
        assert "synthetic-forbidden" not in child.stdout
        raise InspectedSpawn

    monkeypatch.setattr(StdioNativeGoalTransport, "spawn", inspect)
    with pytest.raises(InspectedSpawn):
        runner.qualify(tmp_path, "synthetic-codex", 10)


def test_default_does_not_even_probe_model_environment(monkeypatch, capsys):
    def forbidden(*args):
        raise AssertionError("default must not touch a model host")
    monkeypatch.setattr(runner, "prerequisite_failure", forbidden)
    monkeypatch.setattr(runner, "qualify", forbidden)
    assert runner.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "skipped", "reason": "release_opt_in_required", "model_executed": False}


def test_missing_release_environment_is_explicit_skip(monkeypatch, capsys):
    monkeypatch.setattr(runner, "prerequisite_failure", lambda _: "codex_auth_unavailable")
    assert runner.main(["--release-live"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "skipped"


def test_attempted_release_failure_is_not_converted_to_skip(monkeypatch, capsys):
    monkeypatch.setattr(runner, "prerequisite_failure", lambda _: None)
    def failing(*args):
        raise RuntimeError("sensitive diagnostic sentinel")
    monkeypatch.setattr(runner, "qualify", failing)
    assert runner.main(["--release-live"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "failed", "error_kind": "RuntimeError"}


def test_release_timeout_must_be_positive():
    with pytest.raises(SystemExit):
        runner.main(["--timeout-seconds", "0"])


def test_isolated_codex_profile_never_imports_operator_config_or_shell_secrets(monkeypatch, tmp_path):
    for suffix, value in {"API_KEY": "synthetic-key", "MODEL": "fixture-model",
                          "BASE_URL": "https://example.com/v1"}.items():
        monkeypatch.setenv("LOOPX_CODEX_QUALIFICATION_" + suffix, value)
    forbidden = ("ARK_API_KEY", "GH_TOKEN", "CUSTOM_AUTH", "SSH_AUTH_SOCK", "NODE_OPTIONS", "BASH_ENV")
    for key in forbidden:
        monkeypatch.setenv(key, "synthetic-unrelated-value")
    env = runner.configure_codex(tmp_path, tmp_path / "bin/loopx")
    assert all(key not in env for key in forbidden)
    config = tomllib.loads((tmp_path / "codex/config.toml").read_text())
    assert "synthetic-key" not in (tmp_path / "codex/config.toml").read_text()
    assert config["model"] == "fixture-model"
    policy = config["shell_environment_policy"]
    assert policy["inherit"] == "none"
    result = subprocess.run([sys.executable, "-c", "import os,json; print(json.dumps(dict(os.environ)))"],
                            env=policy["set"], capture_output=True, text=True, check=True)
    actual = json.loads(result.stdout)
    assert "LOOPX_CODEX_QUALIFICATION_API_KEY" not in actual
    assert all(key not in actual for key in forbidden)
    assert actual["HOME"] == str(tmp_path / "home")
    assert env["CODEX_HOME"] == str(tmp_path / "codex")
    assert os.environ["GH_TOKEN"] == "synthetic-unrelated-value"


def test_synthetic_fixture_real_cli_projects_identity_reentry(tmp_path):
    project, _, launcher = runner.setup(tmp_path)
    assert (project / "TASK.md").read_bytes() == (runner.FIXTURE / "TASK.md").read_bytes()
    quota = runner.cli(launcher, "quota", "should-run", "--runtime-profile", "codex_cli",
                       "--goal-id", runner.GOAL, "--agent-id", runner.AGENT)
    assert quota["should_run"] is True
    assert quota["selected_todo"]["todo_id"] == "todo_reducer"
    actions = quota["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert len(actions) == 1 and "--turn-instance-id" in actions[0]
    assert "spend-slot" not in actions[0]

    # The Markdown file is only the display projection after migration. A
    # direct edit cannot fabricate canonical completion or Goal terminality.
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace("- [ ]", "- [x]").replace(
        "status=open", "status=done no_followup=true",
    ))
    unchanged = runner.cli(launcher, "quota", "should-run", "--runtime-profile", "codex_cli",
                           "--goal-id", runner.GOAL, "--agent-id", runner.AGENT)
    assert unchanged["should_run"] is True
    assert unchanged["selected_todo"]["todo_id"] == "todo_reducer"
    assert unchanged["interaction_contract"]["mode"] != "terminal_no_followup"

    state.write_text(state.read_text().replace("## User Todo\n\n", ""))
    incomplete = runner.cli(launcher, "quota", "should-run", "--runtime-profile", "codex_cli",
                            "--goal-id", runner.GOAL, "--agent-id", runner.AGENT)
    assert incomplete["should_run"] is True
    assert incomplete["selected_todo"]["todo_id"] == "todo_reducer"
    assert incomplete["interaction_contract"]["mode"] != "terminal_no_followup"


@pytest.mark.parametrize("mutation", ["unbound", "duplicate"])
def test_settlement_oracle_rejects_missing_identity_and_duplicate_spend(tmp_path, mutation):
    rows = [{"classification": "quota_slot_spent", "todo_id": todo,
             "settlement_identity": {"effect_id": todo}} for todo in sorted(runner.TODOS)]
    if mutation == "unbound":
        rows.append({"classification": "quota_slot_spent"})
    else:
        rows.append(rows[0].copy())
    index = tmp_path / "goals" / runner.GOAL / "runs/index.jsonl"
    index.parent.mkdir(parents=True)
    index.write_text("\n".join(json.dumps(row) for row in rows))
    todos = [{"todo_id": todo, "status": "done"} for todo in runner.TODOS]
    with pytest.raises(AssertionError):
        runner.verify_settlement(tmp_path, todos)


@pytest.mark.parametrize("missing", ["writeback", "spend", "receipt"])
def test_settlement_oracle_requires_durable_receipts_not_only_index_rows(tmp_path, monkeypatch, missing):
    from loopx.control_plane.quota import settlement
    from loopx.control_plane.effect_program import SettlementFailure, SettlementFailureKind, SettlementResult

    receipt = None if missing == "receipt" else SimpleNamespace(
        # Real failed result objects are truthy, unlike None. The oracle must
        # inspect the typed failure, not accept object existence as evidence.
        settlement=SettlementResult(value=None, failure=SettlementFailure(
            kind=SettlementFailureKind.RECEIPT_MISSING, reason=missing, step_kind=None,
        )),
    )
    monkeypatch.setattr(settlement, "read_heartbeat_settlement", lambda *_, **__: receipt)
    rows = [{"classification": "quota_slot_spent", "todo_id": todo, "turn_instance_id": todo,
             "settlement_identity": {"effect_id": todo}} for todo in sorted(runner.TODOS)]
    index = tmp_path / "goals" / runner.GOAL / "runs/index.jsonl"
    index.parent.mkdir(parents=True)
    index.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(AssertionError):
        runner.verify_settlement(tmp_path, [{"todo_id": t, "status": "done"} for t in runner.TODOS])


def test_settlement_oracle_rejects_duplicate_open_successor(tmp_path):
    todos = [{"todo_id": t, "status": "done"} for t in runner.TODOS]
    todos.append({"todo_id": "todo_duplicate", "status": "open"})
    with pytest.raises(AssertionError):
        runner.verify_settlement(tmp_path, todos)
