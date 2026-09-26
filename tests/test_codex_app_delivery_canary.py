from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loopx.control_plane.runtime.codex_app_delivery import check_codex_app_delivery
from scripts.codex_app_apply_rrule import main

NOW = 1_800_000_000_000
PROMPT = "Advance the synthetic goal.\n先运行 quota guard。\n"


@pytest.fixture
def delivery(tmp_path: Path):
    manifest = tmp_path / "automations" / "synthetic" / "automation.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        'version = 1\nid = "synthetic"\nkind = "heartbeat"\nstatus = "ACTIVE"\n'
        'target_thread_id = "synthetic-thread"\n'
        f"prompt = {json.dumps(PROMPT, ensure_ascii=False)}\n",
        encoding="utf-8",
    )
    observation = {
        "schema_version": "codex_app_prompt_delivery_observation_v1",
        "automation_id": "synthetic",
        "goal_id": "synthetic-goal",
        "agent_id": "synthetic-agent",
        "thread_id": "synthetic-thread",
        "turn_id": "scheduled-turn-1",
        "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        "turn_started_at_ms": NOW - 2000,
        "agent_activity_observed": True,
        "observed_at_ms": NOW,
    }
    observation_path = tmp_path / "observation.json"
    observation_path.write_text(json.dumps(observation), encoding="utf-8")
    kwargs = {
        "manifest_path": manifest,
        "observation_path": observation_path,
        "automation_id": "synthetic",
        "goal_id": "synthetic-goal",
        "agent_id": "synthetic-agent",
        "thread_id": "synthetic-thread",
        "turn_id": "scheduled-turn-1",
        "now_ms": NOW,
    }
    return kwargs, observation


def test_installation_is_not_delivery_evidence(delivery):
    kwargs, _ = delivery
    kwargs["observation_path"].unlink()
    result = check_codex_app_delivery(**kwargs)
    assert result["status"] == "host_prompt_delivery_unverified"
    assert result["reason_code"] == "host_observation_missing"
    assert not result["ok"]
    kwargs["observation_path"] = None
    assert check_codex_app_delivery(**kwargs) == result


def test_exact_observed_turn_replays_without_writes_or_authority(delivery):
    kwargs, _ = delivery
    paths = [kwargs["manifest_path"], kwargs["observation_path"]]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    first = check_codex_app_delivery(**kwargs)
    assert first["ok"]
    assert first["status"] == "host_observation_matched"
    assert first["grants_execution_authority"] is False
    assert check_codex_app_delivery(**kwargs) == first
    assert before == [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]


@pytest.mark.parametrize(
    "field", ["automation_id", "goal_id", "agent_id", "thread_id", "turn_id"]
)
def test_other_identity_never_satisfies_selected_turn(delivery, field):
    kwargs, observation = delivery
    observation[field] = "another-identity"
    kwargs["observation_path"].write_text(json.dumps(observation))
    result = check_codex_app_delivery(**kwargs)
    assert not result["ok"]
    assert result["reason_code"] == "host_observation_identity_mismatch"


@pytest.mark.parametrize(
    "field, value, reason",
    [
        ("prompt_sha256", "0" * 64, "host_prompt_digest_mismatch"),
        ("schema_version", "future-v99", "host_observation_schema_mismatch"),
        ("prompt_sha256", None, "prompt_delivery_not_observed"),
        ("turn_started_at_ms", None, "agent_start_not_observed"),
        ("agent_activity_observed", False, "agent_start_not_observed"),
        ("agent_activity_observed", 1, "host_observation_invalid"),
        ("turn_started_at_ms", True, "host_observation_invalid"),
        ("observed_at_ms", "1800000000000", "host_observation_invalid"),
        ("observed_at_ms", 2**53, "host_observation_invalid"),
        ("observed_at_ms", NOW + 1, "host_observation_time_mismatch"),
        ("turn_started_at_ms", NOW + 1, "host_observation_time_mismatch"),
        ("turn_started_at_ms", NOW - 900001, "host_observation_stale"),
    ],
)
def test_reject_missing_changed_or_stale_host_facts(delivery, field, value, reason):
    kwargs, observation = delivery
    observation[field] = value
    kwargs["observation_path"].write_text(json.dumps(observation))
    result = check_codex_app_delivery(**kwargs)
    assert not result["ok"]
    assert result["reason_code"] == reason


def test_fresh_readback_does_not_extend_old_delivery(delivery):
    kwargs, observation = delivery
    observation["turn_started_at_ms"] = NOW - 900000
    kwargs["observation_path"].write_text(json.dumps(observation))
    assert check_codex_app_delivery(**kwargs)["ok"]
    kwargs["now_ms"] += 1
    assert check_codex_app_delivery(**kwargs)["reason_code"] == "host_observation_stale"


def test_prompt_identity_is_exact_and_invalidated_after_edit(delivery):
    kwargs, _ = delivery
    manifest = kwargs["manifest_path"]
    manifest.write_text(manifest.read_text().replace("quota guard", "quota  guard"))
    assert (
        check_codex_app_delivery(**kwargs)["reason_code"]
        == "host_prompt_digest_mismatch"
    )


@pytest.mark.parametrize(
    "contents",
    [
        "{}",
        "[]",
        "null",
        "{",
        '{"schema_version": "v0", "schema_version": "v1"}',
        '{"value": NaN}',
        "[" * 1200,
        "x" * 4097,
    ],
)
def test_malformed_input_never_echoes_or_verifies(delivery, contents):
    kwargs, _ = delivery
    kwargs["observation_path"].write_text(contents)
    result = check_codex_app_delivery(**kwargs)
    assert result["reason_code"] == "host_observation_invalid"
    assert not result["ok"]


def test_private_extra_fields_fail_closed_without_echo(delivery):
    kwargs, observation = delivery
    observation["raw_prompt"] = "synthetic-private-sentinel"
    kwargs["observation_path"].write_text(json.dumps(observation))
    result = check_codex_app_delivery(**kwargs)
    assert not result["ok"]
    assert "synthetic-private-sentinel" not in json.dumps(result)
    assert str(kwargs["manifest_path"]) not in json.dumps(result)
    assert PROMPT not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(
    "old, new, reason",
    [
        ('id = "synthetic"', 'id = "other"', "manifest_automation_mismatch"),
        ('kind = "heartbeat"', 'kind = "cron"', "unsupported_automation_kind"),
        ('status = "ACTIVE"', 'status = "PAUSED"', "automation_not_active"),
        (
            'target_thread_id = "synthetic-thread"',
            'target_thread_id = "other"',
            "manifest_thread_mismatch",
        ),
    ],
)
def test_manifest_binding_is_required(delivery, old, new, reason):
    kwargs, _ = delivery
    path = kwargs["manifest_path"]
    path.write_text(path.read_text().replace(old, new))
    assert check_codex_app_delivery(**kwargs)["reason_code"] == reason


def _argv(kwargs):
    return [
        "--check-delivery",
        "--automations-root",
        str(kwargs["manifest_path"].parents[1]),
        "--automation-id",
        "synthetic",
        "--goal-id",
        "synthetic-goal",
        "--agent-id",
        "synthetic-agent",
        "--scheduled-thread-id",
        "synthetic-thread",
        "--scheduled-turn-id",
        "scheduled-turn-1",
        "--delivery-observation",
        str(kwargs["observation_path"]),
    ]


def test_diagnostic_bypasses_all_quota_and_host_mutations(
    delivery, monkeypatch, capsys
):
    kwargs, _ = delivery

    def forbidden(*args, **kwargs):
        pytest.fail("read-only delivery check reached the mutation/query path")

    for name in (
        "_load_scheduler_hint",
        "_sqlite_row_exists",
        "_update_toml",
        "_update_sqlite",
        "_run_ack",
    ):
        monkeypatch.setattr(f"scripts.codex_app_apply_rrule.{name}", forbidden)
    monkeypatch.setattr("scripts.codex_app_apply_rrule._now_ms", lambda: NOW)
    assert main(_argv(kwargs)) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "host_observation_matched"
    kwargs["observation_path"].unlink()
    assert main(_argv(kwargs)) == 1
    assert (
        json.loads(capsys.readouterr().out)["status"]
        == "host_prompt_delivery_unverified"
    )


@pytest.mark.parametrize("observed", [False, True])
def test_real_cli_reports_delivery_without_creating_host_state(
    delivery, tmp_path, observed
):
    kwargs, observation = delivery
    if observed:
        now = time.time_ns() // 1_000_000
        observation.update(
            turn_started_at_ms=now - 2000,
            agent_activity_observed=True,
            observed_at_ms=now,
        )
        kwargs["observation_path"].write_text(json.dumps(observation))
    else:
        kwargs["observation_path"].unlink()
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.codex_app_apply_rrule",
            *_argv(kwargs),
            "--db-path",
            str(tmp_path / "absent-host.db"),
            "--loopx",
            "must-not-launch",
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == (0 if observed else 1)
    result = json.loads(completed.stdout)
    assert result["ok"] is observed
    assert completed.stderr == ""
    assert before == sorted(str(p) for p in tmp_path.rglob("*"))


@pytest.mark.parametrize(
    "changes, reason",
    [
        ({"automation_id": "../other"}, "invalid_expected_identity"),
        ({"turn_id": ""}, "invalid_expected_identity"),
        ({"max_age_seconds": 0}, "invalid_observation_window"),
        ({"max_age_seconds": 86401}, "invalid_observation_window"),
        ({"now_ms": True}, "invalid_observation_window"),
    ],
)
def test_invalid_expected_context_fails_before_reading(delivery, changes, reason):
    kwargs, _ = delivery
    kwargs.update(changes)
    kwargs["manifest_path"].unlink()
    assert check_codex_app_delivery(**kwargs)["reason_code"] == reason


@pytest.mark.parametrize(
    "argv",
    [
        ["--check-delivery"],
        ["--delivery-observation", "fixture.json"],
        ["--scheduled-turn-id", "scheduled-turn-1"],
    ],
)
def test_incomplete_or_mixed_modes_are_rejected(argv):
    with pytest.raises(SystemExit) as raised:
        main(argv)
    assert raised.value.code == 2


def test_host_read_is_explicit_and_never_falls_back_to_supplied_facts(
    delivery, monkeypatch
):
    from loopx.control_plane.runtime import codex_app_delivery_observer as observer

    kwargs, _ = delivery
    calls = []

    def unavailable(**args):
        calls.append(args)
        raise observer.HostObservationError("host_observer_unavailable")

    monkeypatch.setattr(observer, "observe_codex_app_delivery", unavailable)
    assert check_codex_app_delivery(**kwargs)["ok"]
    assert calls == []
    kwargs["observe_host"] = True
    assert (
        check_codex_app_delivery(**kwargs)["reason_code"]
        == "conflicting_observation_sources"
    )
    assert calls == []
    kwargs["observation_path"] = None
    assert (
        check_codex_app_delivery(**kwargs)["reason_code"] == "host_observer_unavailable"
    )
    assert len(calls) == 1
