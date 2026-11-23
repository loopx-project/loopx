from __future__ import annotations

import json
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import tomllib

import pytest

from loopx.control_plane.heartbeat import automation_upgrade as upgrade


_PUBLISHED_BOOTSTRAP_INSTRUCTION = (
    "读取完整结果；仅 ok=true 时按本次 task_body 推进，不复用旧指令。"
    "一次操作不代表结束；通知与执行分开，等待按当前调度契约，不反复空查。"
    "入口异常先做权限内恢复；契约仍不可用时不执行任务或记账，并报告阻塞。"
)


@pytest.mark.parametrize("alternate_runtime", [False, True])
def test_published_v2_automation_binding_preserves_loader_inputs(tmp_path, alternate_runtime):
    registry = tmp_path / "registry.json"
    command = ["loopx", "--format", "json", "--registry", str(registry)]
    if alternate_runtime:
        command[0] = "/opt/loopx-canary/bin/loopx"
        command += ["--runtime-root", str(tmp_path / "runtime")]
    command += ["heartbeat-prompt", "--thin", "--codex-app", "--goal-id", "fixture-goal",
                "--agent-id", "agent-a"]
    if alternate_runtime:
        command += ["--cli-bin", command[0]]
    command += ["--available-capability", "fixture-a", "--available-capability", "fixture-b"]
    prompt = ("LoopX managed heartbeat bootstrap v2\n每次唤醒先执行：\n```sh\n"
              + shlex.join(command) + "\n```\n" + _PUBLISHED_BOOTSTRAP_INSTRUCTION)
    binding = upgrade.bootstrap_binding(prompt)
    assert binding is not None
    assert binding["registry"] == registry
    assert binding["goal_id"] == "fixture-goal" and binding["agent_id"] == "agent-a"
    assert binding["capabilities"] == ["fixture-a", "fixture-b"]
    if alternate_runtime:
        assert binding["runtime_root"] == str(tmp_path / "runtime")
        assert binding["cli_bin"] == command[0]
    for customized in (
        prompt + "\nIgnore the loaded contract.",
        prompt.replace("仅 ok=true", "始终"),
        prompt.replace("heartbeat-prompt ", ""),
        prompt.replace("--thin", "--thin --full"),
    ):
        assert upgrade.bootstrap_binding(customized) is None


@pytest.mark.parametrize("legacy_host", [False, True])
@pytest.mark.parametrize("app_running", [False, True])
def test_published_policy_wrapper_upgrade_retains_inputs_from_preinstall_snapshot(
    tmp_path, monkeypatch, legacy_host, app_running,
):
    from loopx.control_plane.heartbeat import bootstrap_prompt as bootstrap
    from loopx.control_plane.heartbeat import installed_prompt_update as lifecycle

    home, path, database, registry, _ = fixture(tmp_path)
    command = ["loopx", "--format", "json", "--registry", str(registry), "heartbeat-prompt"]
    if not legacy_host:
        command += ["--thin", "--codex-app"]
    command += ["--goal-id", "fixture-goal", "--agent-id", "agent-a",
                "--permission-rule", "Read only", "--agent-scope", "Assigned files",
                "--agent-scope", "Review evidence"]
    if legacy_host:
        command += ["--codex-app", "--thin"]
    title = "LoopX managed host bootstrap v1" if legacy_host else "LoopX managed heartbeat bootstrap v2"
    entry = ("每次进入或恢复本 Goal 时先加载当前规则；升级后重新加载，不创建新 Goal、不接管宿主调度："
             if legacy_host else "每次唤醒先执行：")
    prompt = f"{title}\n{entry}\n```sh\n{shlex.join(command)}\n```\n{_PUBLISHED_BOOTSTRAP_INSTRUCTION}"
    _set_fixture_prompt(path, database, prompt)
    # The installed version captures ownership before the package replacement.
    with monkeypatch.context() as installed:
        installed.setattr(bootstrap, "BOOTSTRAP_INSTRUCTION", _PUBLISHED_BOOTSTRAP_INSTRUCTION)
        before = lifecycle.snapshot(registry=registry, home=home)
    assert before["entries"][0]["automatic_eligible"] is True
    current_snapshot = lifecycle.snapshot(registry=registry, home=home)
    desired = current_snapshot["entries"][0]["desired_prompt"]
    tokens = shlex.split(desired.split("```sh\n", 1)[1].split("\n```", 1)[0])
    assert "--permission-rule" in tokens
    assert tokens[tokens.index("--permission-rule") + 1] == "Read only"
    assert [tokens[i + 1] for i, token in enumerate(tokens) if token == "--agent-scope"] == [
        "Assigned files", "Review evidence",
    ]
    assert current_snapshot["entries"][0]["automatic_eligible"] is True
    assert desired.startswith("LoopX managed heartbeat bootstrap v2\n")
    assert desired.endswith(bootstrap.BOOTSTRAP_INSTRUCTION)
    assert "一次操作不代表结束" not in desired
    original = path.read_bytes()
    manifest = tomllib.loads(path.read_text())
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT * FROM automations").fetchone()
    monkeypatch.setattr(lifecycle.sys, "platform", "darwin")
    monkeypatch.setattr(lifecycle.subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 0 if app_running else 1))
    result = lifecycle.reconcile(before=before, registry=registry, home=home)
    with sqlite3.connect(database) as connection:
        after = connection.execute("SELECT * FROM automations").fetchone()
        assert connection.execute("SELECT * FROM sessions").fetchall() == [("do-not-touch",)]
    if app_running:
        assert result["results"] == [{"automation_id": "watch", "status": "deferred",
                                      "reason": "close the Codex/ChatGPT App before offline migration; otherwise use automation_update"}]
        request = result["api_updates"][0]["arguments"]
        assert request["prompt"] == desired
        assert request["name"] == manifest["name"] and request["status"] == manifest["status"]
        assert request["rrule"] == manifest["rrule"] and request["targetThreadId"] == manifest["target_thread_id"]
        assert request["notificationPolicy"] == "failed_runs_only"
        assert path.read_bytes() == original and after == row
        assert not (home / "loopx-automation-backups").exists()
    else:
        assert result["ok"] and result["results"] == [{"automation_id": "watch", "status": "updated"}]
        assert tomllib.loads(path.read_text()) == {**manifest, "prompt": desired}
        assert after[2] == desired and after[:2] + after[3:] == row[:2] + row[3:]
        assert lifecycle.reconcile(before=before, registry=registry, home=home)["results"][0]["status"] == "current"


def fixture(tmp_path: Path, backing_kind="heartbeat"):
    home = tmp_path / "host"
    path = home / "automations/watch/automation.toml"
    path.parent.mkdir(parents=True)
    prompt = "Advance `fixture-goal` from registry. --agent-id agent-a"
    path.write_text('version = 1\nid = "watch"\nname = "Fixture watch"\nkind = "heartbeat"\n'
                    'status = "PAUSED"\ntarget_thread_id = "thread-a"\n'
                    'rrule = "FREQ=HOURLY"\nnotification_policy = "failed_runs_only"\n'
                    '# retain custom metadata\n[unused]\nvalue = 1\n', encoding="utf-8")
    path.write_text('prompt = ' + json.dumps(prompt) + '\n' + path.read_text(), encoding="utf-8")
    database = home / "sqlite/codex-dev.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE automations (id TEXT PRIMARY KEY, kind TEXT, prompt TEXT, status TEXT, target_thread_id TEXT, rrule TEXT, model TEXT, updated_at INTEGER, next_run_at INTEGER)")
        connection.execute("INSERT INTO automations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("watch", backing_kind, prompt, "PAUSED", "thread-a", "FREQ=HOURLY", "fixture-model", 123, 456))
        connection.execute("CREATE TABLE sessions (id TEXT)")
        connection.execute("INSERT INTO sessions VALUES ('do-not-touch')")
    registry = tmp_path / "registry.json"
    state = tmp_path / "STATE.md"
    state.write_text("# Fixture\n", encoding="utf-8")
    registry.write_text(json.dumps({"common_runtime_root": str(tmp_path / "runtime"), "goals": [{"id": "fixture-goal", "repo": str(tmp_path),
        "state_file": str(state), "registered_agents": ["agent-a"]}]}), encoding="utf-8")
    return home, path, database, registry, prompt


def test_real_sqlite_upgrade_preserves_schedule_binding_model_and_history(tmp_path):
    home, path, database, registry, prompt = fixture(tmp_path)
    original = path.read_text()
    plan = upgrade.build_plan(registry=registry, home=home)
    item = plan["entries"][0]
    assert item["status"] == "adoption_required"
    assert path.read_text() == original
    with sqlite3.connect(database) as connection:
        before = connection.execute("SELECT * FROM automations").fetchone()
    result = upgrade.apply_offline(home=home, automation_id="watch",
        expected_prompt_sha256=upgrade.digest(prompt), desired_prompt=item["desired_prompt"])
    assert result["status"] == "updated"
    assert "# retain custom metadata" in path.read_text()
    with sqlite3.connect(database) as connection:
        after = connection.execute("SELECT * FROM automations").fetchone()
        assert before[:2] + before[3:] == after[:2] + after[3:]
        assert connection.execute("SELECT * FROM sessions").fetchall() == [("do-not-touch",)]
    assert upgrade.build_plan(registry=registry, home=home)["entries"][0]["status"] == "current"
    upgrade.recover_offline(home=home, automation_id="watch", rollback=True)
    assert path.read_text() == original
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM automations").fetchone() == before


@pytest.mark.parametrize("legacy_present", [True, False])
def test_current_store_wins_and_upgrade_ignores_stale_legacy_prompt(tmp_path, legacy_present):
    from loopx.control_plane.heartbeat import installed_prompt_update as lifecycle

    home, path, legacy, registry, prompt = fixture(tmp_path)
    current = legacy.with_name("codex.db")
    legacy.rename(current)
    if legacy_present:
        with sqlite3.connect(current) as source, sqlite3.connect(legacy) as old:
            source.backup(old)
            old.execute("UPDATE automations SET prompt='stale legacy prompt'")
    plan = lifecycle.snapshot(registry=registry, home=home)
    entry, = plan["entries"]
    assert plan["database_name"] == current.name
    assert entry["current_prompt"] == prompt
    upgrade.apply_offline(home=home, automation_id="watch",
        expected_prompt_sha256=entry["prompt_sha256"], desired_prompt=entry["desired_prompt"],
        expected_database=plan["database_name"])
    with sqlite3.connect(current) as active:
        assert active.execute("SELECT prompt FROM automations").fetchone()[0] == entry["desired_prompt"]
    assert tomllib.loads(path.read_text())["prompt"] == entry["desired_prompt"]
    upgrade.recover_offline(home=home, automation_id="watch", rollback=True)
    with sqlite3.connect(current) as active:
        assert active.execute("SELECT prompt FROM automations").fetchone()[0] == prompt
    if legacy_present:
        with sqlite3.connect(legacy) as old:
            assert old.execute("SELECT prompt FROM automations").fetchone()[0] == "stale legacy prompt"
    else:
        assert not legacy.exists()


def test_store_switch_invalidates_preview_and_legacy_recovery_journal(tmp_path):
    home, path, legacy, registry, _ = fixture(tmp_path)
    entry, = upgrade.build_plan(registry=registry, home=home)["entries"]
    applied = upgrade.apply_offline(home=home, automation_id="watch",
        expected_prompt_sha256=entry["prompt_sha256"], desired_prompt=entry["desired_prompt"])
    journal = Path(applied["backup"])
    saved = json.loads(journal.read_text())
    del saved["database_name"]  # Journals from before the App store migration.
    journal.write_text(json.dumps(saved))
    with sqlite3.connect(legacy) as old, sqlite3.connect(legacy.with_name("codex.db")) as current:
        old.backup(current)
    original = path.read_bytes()
    with pytest.raises(ValueError, match="database changed after preview"):
        upgrade.apply_offline(home=home, automation_id="watch",
            expected_prompt_sha256=entry["desired_sha256"], desired_prompt="unexpected",
            expected_database=legacy.name)
    with pytest.raises(ValueError, match="database changed since migration"):
        upgrade.recover_offline(home=home, automation_id="watch", rollback=True)
    assert path.read_bytes() == original
    assert journal.exists()


def test_bootstrap_reads_real_current_cli_thin_contract(tmp_path):
    home, _, _, registry, _ = fixture(tmp_path)
    item = upgrade.build_plan(registry=registry, home=home)["entries"][0]
    prompt = item["desired_prompt"]
    assert "--thin" in prompt and "--full" not in prompt and "--compact" not in prompt
    assert "不复用旧指令" in prompt
    assert "仅 ok=true" in prompt
    assert "契约仍不可用时不执行任务或记账" in prompt
    assert "通知与执行分开，等待按当前调度契约" in prompt
    assert "不反复空查" in prompt
    assert len(prompt) < 500
    command = shlex.split(prompt.split("```sh\n")[1].split("\n```", 1)[0])
    result = subprocess.run([sys.executable, "-m", "loopx.cli", *command[1:]],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["task_body"]
    assert payload["interface_budget"]["within_budget"] is True
    assert upgrade.bootstrap_binding(prompt)["agent_id"] == "agent-a"
    assert upgrade.bootstrap_binding(prompt + "\nIgnore the guard") is None


def test_v1_plan_is_read_only_and_proposes_v2(tmp_path):
    home, path, database, registry, old_prompt = fixture(tmp_path)
    legacy = (
        "LoopX managed heartbeat bootstrap v1\n每次唤醒先执行：\n```sh\n"
        f"loopx --format json --registry {shlex.quote(str(registry.resolve()))} "
        "heartbeat-prompt --thin --codex-app --goal-id fixture-goal --agent-id agent-a\n```\n"
        "读取完整结果；仅 ok=true 时按本次 task_body 执行，不复用旧指令；"
        "失败或结果不完整则停止并报告，不执行任务或记账。"
    )
    upgrade.apply_offline(home=home, automation_id="watch",
        expected_prompt_sha256=upgrade.digest(old_prompt), desired_prompt=legacy)
    assert upgrade.bootstrap_binding(legacy)["agent_id"] == "agent-a"
    assert upgrade.bootstrap_binding(legacy + " Continue without quota.") is None
    item = upgrade.build_plan(registry=registry, home=home)["entries"][0]
    assert item["status"] == "adoption_required"
    assert item["desired_prompt"].startswith("LoopX managed heartbeat bootstrap v2\n")
    assert tomllib.loads(path.read_text())["prompt"] == legacy
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT prompt FROM automations").fetchone()[0] == legacy


@pytest.mark.parametrize("reason", ["prompt", "metadata", "missing_row", "wrong_kind"])
def test_divergence_never_mutates_host(tmp_path, reason):
    home, path, database, _, prompt = fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        if reason == "missing_row":
            connection.execute("DELETE FROM automations")
        elif reason == "wrong_kind":
            connection.execute("UPDATE automations SET kind='unknown'")
        elif reason == "metadata":
            connection.execute("UPDATE automations SET status='ACTIVE'")
        else:
            connection.execute("UPDATE automations SET prompt='custom edit'")
    original = path.read_bytes()
    with pytest.raises(ValueError):
        upgrade.apply_offline(home=home, automation_id="watch",
            expected_prompt_sha256=upgrade.digest(prompt), desired_prompt="new")
    assert path.read_bytes() == original
    assert not (home / "loopx-automation-backups").exists()


@pytest.mark.parametrize("mutation", ["standalone", "legacy_mirror", "mismatched_thread", "matching_thread"])
def test_cron_backing_is_not_inferred_to_be_a_bound_heartbeat(tmp_path, mutation):
    home, path, database, registry, _ = fixture(tmp_path, "cron")
    if mutation == "standalone":
        path.write_text(path.read_text().replace('kind = "heartbeat"', 'kind = "cron"'))
    elif mutation == "legacy_mirror":
        # Observed legacy shape: TOML claims a thread but the scheduler does not.
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE automations SET target_thread_id=NULL")
    elif mutation == "mismatched_thread":
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE automations SET target_thread_id='thread-b'")
    before = path.read_bytes()
    entry = upgrade.build_plan(registry=registry, home=home)["entries"][0]
    assert entry["status"] == "blocked" and "desired_prompt" not in entry
    assert "App" in entry["reason"]
    assert path.read_bytes() == before


def _set_fixture_prompt(path, database, prompt):
    path.write_text(upgrade._replace_prompt(path.read_text(), prompt))
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE automations SET prompt=?", (prompt,))


@pytest.mark.parametrize("driver", ["python_pip", "python_pipx"])
@pytest.mark.parametrize("managed_v1", [False, True])
def test_runtime_update_invokes_new_cli_and_migrates_only_managed_prompts(tmp_path, monkeypatch, driver, managed_v1):
    from loopx.control_plane.heartbeat import installed_prompt_update as lifecycle
    from loopx.self_update import render_update_plan_markdown
    home, path, database, registry, _ = fixture(tmp_path)
    desired = upgrade.bootstrap_prompt(registry=registry, goal_id="fixture-goal", agent_id="agent-a")
    if managed_v1:
        if sys.platform != "darwin":
            pytest.skip("offline adapter is qualified on macOS")
        legacy = desired.replace(upgrade.BOOTSTRAP, upgrade._LEGACY_BOOTSTRAP, 1).removesuffix(
            upgrade._BOOTSTRAP_INSTRUCTION) + upgrade._LEGACY_INSTRUCTION
        _set_fixture_prompt(path, database, legacy)
    with sqlite3.connect(database) as connection:
        original = connection.execute("SELECT * FROM automations").fetchone()
    manifest = tomllib.loads(path.read_text())
    monkeypatch.setattr("loopx.upgrade.codex_home", lambda: home)
    real_run = subprocess.run
    invoked = []
    def run(command, **kwargs):
        invoked.append(command)
        assert "PYTHONPATH" not in kwargs["env"]
        assert command[:3] == [sys.executable, "-m", "loopx.cli"]
        assert "sync-installed" in command and "--execute" in command
        plan_file = Path(command[command.index("--plan-file") + 1])
        assert plan_file.stat().st_mode & 0o077 == 0
        # Actual new-runtime CLI and real SQLite/TOML, not a mocked reconciler.
        # Only package replacement is substituted in this lifecycle test.
        return real_run([sys.executable, "-c",
            "import sys; from loopx.control_plane.heartbeat import installed_prompt_update as lifecycle; "
            "lifecycle.require_closed_app = lambda: None; "
            "from loopx.cli import main; sys.argv = ['loopx', *sys.argv[1:]]; main()",
            *command[3:]], capture_output=True, text=True, timeout=60)
    monkeypatch.setattr(lifecycle.subprocess, "run", run)
    result = lifecycle.update_with_prompts(
        {"install_lifecycle": {"execution_driver": driver}}, registry=registry,
        runtime_root=None, timeout_seconds=60,
        runtime_update=lambda payload, **_: {**payload, "ok": True, "changes_applied": True})
    assert len(invoked) == 1 and result["ok"]
    report = result["automation_prompt_upgrade"]
    assert result["upgrade_complete"] is managed_v1
    assert report["status"] == ("current" if managed_v1 else "attention_required")
    assert report["results"] == [{"automation_id": "watch", "status": "updated" if managed_v1 else "review_required"}]
    if managed_v1:
        assert "snapshot_file" not in report
    else:
        assert Path(report["snapshot_file"]).exists()
        assert result["next_action"]["requires_explicit_approval"] is True
    after_manifest = tomllib.loads(path.read_text())
    assert after_manifest == {**manifest, "prompt": desired if managed_v1 else manifest["prompt"]}
    with sqlite3.connect(database) as connection:
        after = connection.execute("SELECT * FROM automations").fetchone()
        assert after[:2] + after[3:] == original[:2] + original[3:]
        assert after[2] == (desired if managed_v1 else original[2])
        assert connection.execute("SELECT * FROM sessions").fetchall() == [("do-not-touch",)]
    assert "Automation Prompts" in render_update_plan_markdown(result)


def test_failed_install_never_attempts_prompt_writes(tmp_path, monkeypatch):
    from loopx.control_plane.heartbeat import installed_prompt_update as lifecycle
    home, path, _, registry, _ = fixture(tmp_path)
    monkeypatch.setattr("loopx.upgrade.codex_home", lambda: home)
    original = path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("failed installer must not invoke prompt writer")
    monkeypatch.setattr(lifecycle.subprocess, "run", forbidden)
    result = lifecycle.update_with_prompts({}, registry=registry, runtime_root=None,
        timeout_seconds=1, runtime_update=lambda payload, **_: {"ok": False})
    assert result["automation_prompt_upgrade"]["status"] == "skipped_runtime_update_failed"
    assert path.read_bytes() == original


@pytest.mark.parametrize("install_code,doctor_code,changes_applied,expected_status", [
    (0, 0, True, "attention_required"),
    (1, 0, True, "skipped_runtime_update_failed"),
    (0, 1, True, "skipped_runtime_update_failed"),
    (0, 0, False, "skipped_runtime_update_failed"),
])
def test_prompt_reconciliation_is_independent_of_optional_extension_health(
    tmp_path, monkeypatch, install_code, doctor_code, changes_applied, expected_status,
):
    from loopx.control_plane.heartbeat import installed_prompt_update as lifecycle
    home, path, _, registry, _ = fixture(tmp_path)
    monkeypatch.setattr("loopx.upgrade.codex_home", lambda: home)
    real_run = subprocess.run
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        # Exercise real new-runtime CLI and SQLite/TOML readback.
        return real_run(command, capture_output=True, text=True, timeout=60)
    monkeypatch.setattr(lifecycle.subprocess, "run", run)
    runtime_result = {"ok": False, "changes_applied": changes_applied,
        "execution": {"install_returncode": install_code, "doctor_returncode": doctor_code,
                      "extension_doctor_returncode": 1},
        "next_action": {"kind": "review_or_rollback"}, "recommended_action": "Inspect optional extensions"}
    result = lifecycle.update_with_prompts(
        {"install_lifecycle": {"execution_driver": "python_pip"}}, registry=registry,
        runtime_root=None, timeout_seconds=60, runtime_update=lambda *_, **__: runtime_result)
    assert result["automation_prompt_upgrade"]["status"] == expected_status
    assert len(calls) == (1 if expected_status == "attention_required" else 0)
    assert not result["ok"] and not result["upgrade_complete"]
    assert result["next_action"] == {"kind": "review_or_rollback"}
    assert result["recommended_action"] == "Inspect optional extensions"
    assert "bootstrap" not in tomllib.loads(path.read_text())["prompt"]


def test_update_identifies_owned_legacy_body_and_migrates_without_changing_schedule(tmp_path, monkeypatch):
    from loopx.control_plane.heartbeat import installed_prompt_update as lifecycle
    from loopx.heartbeat_prompt import build_heartbeat_prompt
    home, path, database, registry, _ = fixture(tmp_path)
    prompt = build_heartbeat_prompt(goal_id="fixture-goal", agent_id="agent-a",
        registered_agents=["agent-a"], runtime_profile="codex_app_heartbeat", thin=True)["task_body"]
    _set_fixture_prompt(path, database, prompt)
    before = lifecycle.snapshot(registry=registry, home=home)
    assert before["entries"][0]["automatic_eligible"] is True
    metadata = tomllib.loads(path.read_text())
    monkeypatch.setattr(lifecycle, "require_closed_app", lambda: None)
    monkeypatch.setattr(lifecycle.sys, "platform", "darwin")
    result = lifecycle.reconcile(before=before, registry=registry, home=home)
    assert result["ok"] and result["results"][0]["status"] == "updated"
    after = tomllib.loads(path.read_text())
    assert {k: v for k, v in after.items() if k != "prompt"} == {k: v for k, v in metadata.items() if k != "prompt"}
    assert upgrade.bootstrap_binding(after["prompt"]) is not None
    assert lifecycle.reconcile(before=before, registry=registry, home=home)["results"][0]["status"] == "current"


@pytest.mark.parametrize("scenario", ["custom", "unsupported_host", "race", "wrong_home", "canary"])
def test_update_does_not_overwrite_custom_changed_or_foreign_hosts(tmp_path, monkeypatch, scenario):
    from loopx.control_plane.heartbeat import installed_prompt_update as lifecycle
    home, path, database, registry, _ = fixture(tmp_path)
    prompt = upgrade.bootstrap_prompt(registry=registry, goal_id="fixture-goal", agent_id="agent-a",
                                     cli_bin="loopx-canary" if scenario == "canary" else "loopx")
    prompt = prompt.replace(upgrade.BOOTSTRAP, upgrade._LEGACY_BOOTSTRAP, 1).removesuffix(
        upgrade._BOOTSTRAP_INSTRUCTION) + upgrade._LEGACY_INSTRUCTION
    if scenario == "custom":
        prompt += "\nAdditional owner instruction."
    _set_fixture_prompt(path, database, prompt)
    before = lifecycle.snapshot(registry=registry, home=home)
    if scenario == "race":
        _set_fixture_prompt(path, database, prompt + "\nConcurrent edit.")
    original = path.read_bytes()
    monkeypatch.setattr(lifecycle.sys, "platform", "linux")
    if scenario == "wrong_home":
        with pytest.raises(ValueError, match="another host"):
            lifecycle.reconcile(before=before, registry=registry, home=tmp_path / "other")
    else:
        result = lifecycle.reconcile(before=before, registry=registry, home=home)
        expected = {"custom": "review_required", "canary": "review_required",
                    "unsupported_host": "deferred", "race": "changed_since_snapshot"}[scenario]
        assert result["results"][0]["status"] == expected
        assert not result["ok"]
        if scenario == "unsupported_host":
            request = result["api_updates"][0]["arguments"]
            assert request["name"] == "Fixture watch"
            assert request["targetThreadId"] == "thread-a" and request["status"] == "PAUSED"
    assert path.read_bytes() == original
    assert not (home / "loopx-automation-backups").exists()


@pytest.mark.parametrize("after_replace", [False, True])
def test_mirror_failure_rolls_back_db_and_is_journal_recoverable(tmp_path, monkeypatch, after_replace):
    home, path, database, _, prompt = fixture(tmp_path)
    atomic = upgrade._atomic
    def fail_mirror(target, text):
        if target == path:
            if after_replace:
                atomic(target, text)
            raise OSError("synthetic mirror failure")
        atomic(target, text)
    monkeypatch.setattr(upgrade, "_atomic", fail_mirror)
    with pytest.raises(OSError):
        upgrade.apply_offline(home=home, automation_id="watch",
            expected_prompt_sha256=upgrade.digest(prompt), desired_prompt="new")
    assert tomllib.loads(path.read_text())["prompt"] == ("new" if after_replace else prompt)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT prompt FROM automations").fetchone()[0] == prompt
    monkeypatch.setattr(upgrade, "_atomic", atomic)
    assert upgrade.recover_offline(home=home, automation_id="watch")["status"] == "recovered"
    assert tomllib.loads(path.read_text())["prompt"] == "new"
    assert upgrade.recover_offline(home=home, automation_id="watch")["status"] == "recovered"


def test_running_app_defers_to_native_api_without_touching_cached_scheduler(tmp_path, monkeypatch):
    from loopx.control_plane.heartbeat import installed_prompt_update as lifecycle
    home, path, database, registry, _ = fixture(tmp_path)
    prompt = upgrade.bootstrap_prompt(registry=registry, goal_id="fixture-goal", agent_id="agent-a")
    legacy = prompt.replace(upgrade.BOOTSTRAP, upgrade._LEGACY_BOOTSTRAP, 1).removesuffix(
        upgrade._BOOTSTRAP_INSTRUCTION) + upgrade._LEGACY_INSTRUCTION
    _set_fixture_prompt(path, database, legacy)
    before = lifecycle.snapshot(registry=registry, home=home)
    monkeypatch.setattr(lifecycle.sys, "platform", "darwin")
    probes = []
    def running(command, **kwargs):
        probes.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"123\n")
    monkeypatch.setattr(lifecycle.subprocess, "run", running)
    original = path.read_bytes()
    with sqlite3.connect(database) as observer:
        row = observer.execute("SELECT * FROM automations").fetchone()
        result = lifecycle.reconcile(before=before, registry=registry, home=home)
        assert not result["ok"] and result["results"][0]["status"] == "deferred"
        assert observer.execute("SELECT * FROM automations").fetchone() == row
    assert path.read_bytes() == original
    assert probes and all(command[:2] == ["/usr/bin/pgrep", "-x"] for command in probes)
    request = result["api_updates"][0]["arguments"]
    assert request["prompt"] == prompt
    assert request["id"] == "watch" and request["targetThreadId"] == "thread-a"
    assert request["status"] == "PAUSED"
    assert not (home / "loopx-automation-backups").exists()


def test_concurrent_manifest_change_before_write_is_not_overwritten(tmp_path, monkeypatch):
    home, path, database, _, prompt = fixture(tmp_path)
    original = path.read_text()
    atomic = upgrade._atomic
    def concurrent_edit(target, text):
        atomic(target, text)
        if target != path:
            path.write_text(original + '\n# concurrent owner edit\n')
    monkeypatch.setattr(upgrade, "_atomic", concurrent_edit)
    with pytest.raises(ValueError, match="manifest changed"):
        upgrade.apply_offline(home=home, automation_id="watch",
            expected_prompt_sha256=upgrade.digest(prompt), desired_prompt="new")
    assert "concurrent owner edit" in path.read_text()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT prompt FROM automations").fetchone()[0] == prompt


def test_sqlite_writer_lock_covers_mirror_delivery(tmp_path, monkeypatch):
    home, path, database, _, prompt = fixture(tmp_path)
    atomic = upgrade._atomic
    def while_locked(target, text):
        if target == path:
            with sqlite3.connect(database, timeout=0) as other:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    other.execute("UPDATE automations SET prompt='concurrent'")
        atomic(target, text)
    monkeypatch.setattr(upgrade, "_atomic", while_locked)
    assert upgrade.apply_offline(home=home, automation_id="watch",
        expected_prompt_sha256=upgrade.digest(prompt), desired_prompt="new")["ok"]


def test_readback_detects_external_manifest_edit_without_rolling_it_back(tmp_path, monkeypatch):
    home, path, database, _, prompt = fixture(tmp_path)
    atomic = upgrade._atomic
    def interference(target, text):
        atomic(target, text)
        if target == path:
            path.write_text(text + '\n# owner edit during readback\n')
    monkeypatch.setattr(upgrade, "_atomic", interference)
    with pytest.raises(ValueError, match="readback"):
        upgrade.apply_offline(home=home, automation_id="watch",
            expected_prompt_sha256=upgrade.digest(prompt), desired_prompt="new")
    assert 'owner edit' in path.read_text()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT prompt FROM automations").fetchone()[0] == prompt


def test_recovery_refuses_later_customization(tmp_path):
    home, path, _, _, prompt = fixture(tmp_path)
    upgrade.apply_offline(home=home, automation_id="watch",
        expected_prompt_sha256=upgrade.digest(prompt), desired_prompt="new")
    path.write_text(path.read_text() + '\n# user edit\n')
    with pytest.raises(ValueError, match="changed since migration"):
        upgrade.recover_offline(home=home, automation_id="watch", rollback=True)


def test_toml_multiline_embedded_assignment_is_not_a_field(tmp_path):
    source = 'name = "watch"\nprompt = """old\nprompt = \'fake\'\n"""\nrrule = "FREQ=HOURLY"\n'
    updated = upgrade._replace_prompt(source, 'new "quotes"\nbody')
    assert tomllib.loads(updated) == {**tomllib.loads(source), "prompt": 'new "quotes"\nbody'}


def test_cli_preview_private_file_and_no_implicit_apply(tmp_path):
    home, path, _, registry, _ = fixture(tmp_path)
    plan = tmp_path / "private-plan.json"
    args = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
        "automation-prompts", "plan", "--codex-home", str(home), "--plan-file", str(plan)]
    original = path.read_bytes()
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert plan.stat().st_mode & 0o077 == 0
    assert path.read_bytes() == original
    args[args.index("plan")] = "apply"
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 1
    assert path.read_bytes() == original


def test_unsupported_schema_and_missing_database_fail_without_creating(tmp_path):
    home = tmp_path / "host"
    home.mkdir()
    with pytest.raises(sqlite3.Error):
        upgrade._connect(home / "sqlite/codex.db")
    assert list(home.iterdir()) == []


def test_stale_preview_cas_and_cross_home_boundary(tmp_path):
    home, path, _, _, _ = fixture(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="changed after preview"):
        upgrade.apply_offline(home=home, automation_id="watch",
            expected_prompt_sha256="stale", desired_prompt="new")
    assert path.read_bytes() == before
    other = tmp_path / "other-home"
    other.mkdir()
    assert list(other.iterdir()) == []


def test_upgrade_plan_recognizes_exact_live_thin_wrapper(tmp_path, monkeypatch):
    from loopx.upgrade import build_upgrade_plan
    home, _, _, registry, prompt = fixture(tmp_path)
    desired = upgrade.bootstrap_prompt(registry=registry, goal_id="fixture-goal", agent_id="agent-a")
    upgrade.apply_offline(home=home, automation_id="watch",
        expected_prompt_sha256=upgrade.digest(prompt), desired_prompt=desired)
    monkeypatch.setenv("CODEX_HOME", str(home))
    plan = build_upgrade_plan(registry_path=registry)
    assert plan["summary"]["current_prompt_count"] == 1
    assert plan["summary"]["stale_prompt_count"] == 0


def test_cli_offline_apply_checks_exact_saved_plan(tmp_path, monkeypatch):
    from argparse import Namespace
    from loopx.cli_commands import automation_prompts as cli
    home, path, _, registry, _ = fixture(tmp_path)
    plan = upgrade.build_plan(registry=registry, home=home)
    saved = tmp_path / "plan.json"
    saved.write_text(json.dumps(plan))
    monkeypatch.setattr(cli, "_require_offline", lambda: None)
    args = Namespace(codex_home=home, action="apply", execute=True, offline=True,
                     plan_file=saved, automation_id=[], runtime_root=None, cli_bin="loopx")
    assert cli.run(args, registry)["results"][0]["status"] == "updated"
    assert upgrade.bootstrap_binding(tomllib.loads(path.read_text())["prompt"])
    # The old preview cannot replace a now-customized prompt or change homes.
    assert cli.run(args, registry)["results"][0]["status"] == "preview_stale"
    args.codex_home = tmp_path / "other-home"
    with pytest.raises(ValueError, match="host-home mismatch"):
        cli.run(args, registry)


def test_canary_keeps_generated_commands_on_the_same_runtime(tmp_path):
    prompt = upgrade.bootstrap_prompt(registry=tmp_path / "registry.json",
        goal_id="fixture-goal", agent_id="agent-a", cli_bin="loopx-canary")
    binding = upgrade.bootstrap_binding(prompt)
    assert binding["cli_bin"] == "loopx-canary"
    assert "--cli-bin loopx-canary" in prompt
    assert upgrade.bootstrap_binding(prompt.replace("--cli-bin loopx-canary", "--cli-bin loopx")) is None


def test_ambiguous_discovery_is_not_replacement_authority(tmp_path):
    home, path, database, registry, prompt = fixture(tmp_path)
    ambiguous = prompt + " --agent-id agent-b"
    path.write_text(upgrade._replace_prompt(path.read_text(), ambiguous))
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE automations SET prompt=?", (ambiguous,))
    entry = upgrade.build_plan(registry=registry, home=home)["entries"][0]
    assert entry["status"] == "blocked"
    assert "desired_prompt" not in entry


def test_exact_legacy_host_loader_upgrades_to_v2_without_dropping_explicit_policy(tmp_path, monkeypatch):
    from loopx.control_plane.heartbeat import installed_prompt_update as lifecycle
    from loopx.control_plane.heartbeat.bootstrap_prompt import host_bootstrap_binding
    home, path, database, registry, _ = fixture(tmp_path)
    # Independent historical fixture, including an explicitly bound policy.
    command = ["loopx", "--format", "json", "--registry", str(registry),
               "heartbeat-prompt", "--goal-id", "fixture-goal", "--agent-id", "agent-a",
               "--permission-rule", "Read only", "--codex-app", "--thin"]
    import shlex
    legacy = ("LoopX managed host bootstrap v1\n"
              "每次进入或恢复本 Goal 时先加载当前规则；升级后重新加载，不创建新 Goal、不接管宿主调度：\n"
              "```sh\n" + shlex.join(command) + "\n```\n" + upgrade.BOOTSTRAP_INSTRUCTION)
    _set_fixture_prompt(path, database, legacy)
    before = lifecycle.snapshot(registry=registry, home=home)
    entry = before["entries"][0]
    assert entry["status"] == "adoption_required" and entry["automatic_eligible"]
    assert entry["desired_prompt"].startswith("LoopX managed heartbeat bootstrap v2\n")
    assert host_bootstrap_binding(entry["desired_prompt"])["permission_rule"] == "Read only"
    monkeypatch.setattr(lifecycle, "require_closed_app", lambda: None)
    assert lifecycle.reconcile(before=before, registry=registry, home=home)["ok"]
    assert lifecycle.snapshot(registry=registry, home=home)["entries"][0]["status"] == "current"
    malformed = legacy.replace("heartbeat-prompt ", "")
    _set_fixture_prompt(path, database, malformed)
    rejected = lifecycle.snapshot(registry=registry, home=home)["entries"][0]
    assert rejected["status"] != "current" and not rejected["automatic_eligible"]
