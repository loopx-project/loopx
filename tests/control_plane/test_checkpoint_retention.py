"""Real local artifact owner, retention CLI and filesystem recovery negatives."""
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from loopx.cli import main
from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.hosts import checkpoint_retention as transport

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Local unlink transport is qualified on POSIX")


@pytest.fixture
def bank(tmp_path):
    root = (tmp_path / "bank").resolve()
    root.mkdir(mode=0o700)
    source = tmp_path / "code"
    source.mkdir()
    (source / "negative.txt").write_text("retained counterexample")
    source.chmod(0o500)
    components = [{"id": "research", "source_path": str(source)}]
    probe = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components})
    for n in range(8):
        point = root / f"p{n}"
        effect_runtime_result("runtime.checkpoint_artifacts", {"action": "capture", "components": components,
            "destination": str(point), "point_metadata": {}, "expected_source_sha256": probe["source_sha256"]})
        os.utime(point / "checkpoint.json", (1000 + n * 100, 1000 + n * 100))
    source.chmod(0o700)
    return root


@pytest.fixture
def policy():
    return dict(strategy="uniform", keep_latest=2, keep_first=True, minimum_age_seconds=0,
                pins=[], protect_since=None, history_slots=1)


def test_preview_is_read_only_and_execute_preserves_pins_latest_and_unsealed(bank, policy):
    (bank / "unsealed").mkdir()
    (bank / "unsealed/work-in-progress").write_text("held writer export")
    (bank / "p2.json").write_text('{"ok":false,"error_kind":"TimeoutError"}')
    pin = json.loads((bank / "p2/checkpoint.json").read_bytes())["sha256"]
    policy["pins"] = [dict(id="p2", sha256=pin)]
    plan = transport.rotate_bank(bank, policy)
    assert plan["dry_run"] and not (bank / ".checkpoint-retention").exists()
    assert {p["id"] for p in plan["retained"]} == {"p0", "p2", "p5", "p6", "p7"}
    result = transport.rotate_bank(bank, policy, execute=True, expected_plan_sha256=plan["plan_sha256"])
    assert {p["id"] for p in result["deleted"]} == {"p1", "p3", "p4"}
    assert (bank / "p2.json").read_text().startswith('{"ok":false')
    assert (bank / "unsealed/work-in-progress").read_text() == "held writer export"
    assert not list((bank / ".checkpoint-retention/trash").iterdir())
    for p in result["retained"]:
        observed = effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "verify", "root": str(bank / p["id"]), "expected_sha256": p["sha256"]})
        assert observed["integrity_verified"]
    assert transport.rotate_bank(bank, policy, execute=True)["deleted"] == []


def test_stale_preview_and_changed_pin_cannot_delete(bank, policy):
    preview = transport.rotate_bank(bank, policy)
    os.utime(bank / "p2/checkpoint.json", (9000, 9000))
    with pytest.raises(ValueError, match="plan changed"):
        transport.rotate_bank(bank, policy, execute=True, expected_plan_sha256=preview["plan_sha256"])
    assert len(list(bank.glob("*/checkpoint.json"))) == 8
    policy["pins"] = [dict(id="missing", sha256="a" * 64)]
    with pytest.raises(RuntimeError):
        transport.rotate_bank(bank, policy, execute=True)
    assert len(list(bank.glob("*/checkpoint.json"))) == 8


def test_interrupted_unlink_recovers_original_quarantine_without_recapturing(bank, policy, monkeypatch):
    purge = transport._purge
    monkeypatch.setattr(transport, "_purge", lambda path: (_ for _ in ()).throw(OSError("injected unlink failure")))
    with pytest.raises(OSError, match="injected"):
        transport.rotate_bank(bank, policy, execute=True)
    assert list((bank / ".checkpoint-retention/trash").iterdir())
    monkeypatch.setattr(transport, "_purge", purge)
    result = transport.rotate_bank(bank, policy, execute=True)
    assert len(result["recovered"]) == 4 and result["deleted"] == []
    receipts = [json.loads(p.read_bytes()) for p in (bank / ".checkpoint-retention/receipts").glob("*.json")]
    assert {p["status"] for p in receipts} == {"deleted"}
    assert (bank / "p7/checkpoint.json").exists()


def test_new_missing_pin_holds_prior_quarantine_recovery(bank, policy, monkeypatch):
    purge = transport._purge
    monkeypatch.setattr(transport, "_purge", lambda path: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(OSError):
        transport.rotate_bank(bank, policy, execute=True)
    pending = next((bank / ".checkpoint-retention/receipts").glob("*.json"))
    ref = json.loads(pending.read_bytes())["point"]
    policy["pins"] = [dict(id=ref["id"], sha256=ref["sha256"])]
    monkeypatch.setattr(transport, "_purge", purge)
    with pytest.raises(RuntimeError):
        transport.rotate_bank(bank, policy, execute=True)
    assert (bank / ".checkpoint-retention/trash" / (ref["id"] + "-" + ref["sha256"])).exists()


def test_symlink_bank_point_or_manifest_never_follows_outside(bank, policy, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "valuable"
    sentinel.write_text("keep")
    (bank / "foreign").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError):
        transport.rotate_bank(bank, policy, execute=True)
    assert sentinel.read_text() == "keep" and (bank / "p0").exists()
    (bank / "foreign").unlink()
    manifest = bank / "p1/checkpoint.json"
    manifest.unlink()
    manifest.symlink_to(sentinel)
    with pytest.raises(RuntimeError):
        transport.rotate_bank(bank, policy, execute=True)
    assert sentinel.read_text() == "keep"


def test_unknown_point_members_hold_whole_bank_without_erasing_new_data(bank, policy):
    member = bank / "p2/new-unsealed-evidence"
    member.write_text("not declared in the immutable capture")
    with pytest.raises(RuntimeError):
        transport.rotate_bank(bank, policy, execute=True)
    assert member.read_text() == "not declared in the immutable capture"
    assert len(list(bank.glob("*/checkpoint.json"))) == 8


def test_corrupt_newest_cannot_evict_the_older_recovery_points(bank, policy):
    (bank / "p7/components/research/negative.txt").write_text("changed after publication")
    with pytest.raises(RuntimeError):
        transport.rotate_bank(bank, policy, execute=True)
    assert len(list(bank.glob("*/checkpoint.json"))) == 8


def test_managed_reader_serializes_cleanup_and_off_state_creates_nothing(bank, policy):
    with transport.checkpoint_bank_use(bank / "p7"):
        assert not (bank / ".checkpoint-retention").exists()
    transport._state(bank)
    entered, release = [threading.Event(), threading.Event()], threading.Event()
    def reader(index):
        with transport.checkpoint_bank_use(bank / "p7/components/research"):
            entered[index].set()
            assert release.wait(10)
    workers = [threading.Thread(target=reader, args=(index,)) for index in range(2)]
    for worker in workers:
        worker.start()
    assert all(event.wait(5) for event in entered), "same-point readers must not serialize each other"
    try:
        with pytest.raises(TimeoutError):
            transport.rotate_bank(bank, policy, execute=True)
        assert len(list(bank.glob("*/checkpoint.json"))) == 8
    finally:
        release.set()
        for worker in workers:
            worker.join(10)
    assert not any(worker.is_alive() for worker in workers)
    assert transport.rotate_bank(bank, policy, execute=True)["deleted"]


def test_real_cli_preview_execute_and_newest_restore(bank, policy, tmp_path, capsys):
    request = tmp_path / "retention.json"
    request.write_text(json.dumps(policy))
    args = ["--format", "json", "research-checkpoint", "rotate", "--root", str(bank), "--request-file", str(request)]
    assert main(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["dry_run"] and not (bank / ".checkpoint-retention").exists()
    assert main([*args, "--execute", "--expected-plan-sha256", plan["plan_sha256"]]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert len(applied["deleted"]) == 4
    newest = next(p for p in applied["retained"] if p["id"] == "p7")
    assert main(["--format", "json", "research-checkpoint", "verify", "--root", str(bank / "p7"),
                 "--expected-sha256", newest["sha256"]]) == 0
    assert json.loads(capsys.readouterr().out)["integrity_verified"]
    result = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "materialize", "root": str(bank / "p7"),
        "expected_sha256": newest["sha256"], "destination": str(tmp_path / "inert")})
    assert result["materialized"] and not result["activation_performed"]
    assert (tmp_path / "inert/components/research/negative.txt").read_text() == "retained counterexample"
    assert main([*args, "--watch"]) == 1
    assert "requires rotate" in json.loads(capsys.readouterr().out)["error"]


def test_cli_default_is_latest_only_and_managed_bank_policy_adds_explicit_pins(bank, capsys):
    args = ["--format", "json", "research-checkpoint", "rotate", "--root", str(bank)]
    assert main(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert [p["id"] for p in plan["retained"]] == ["p7"]
    assert not (bank / ".checkpoint-retention").exists()
    state = transport._state(bank)
    pin = json.loads((bank / "p2/checkpoint.json").read_bytes())["sha256"]
    selected = {**plan["policy"], "pins": [dict(id="p2", sha256=pin)]}
    (state / "policy.json").write_text(json.dumps(selected))
    assert main(args) == 0
    pinned = json.loads(capsys.readouterr().out)
    assert {p["id"] for p in pinned["retained"]} == {"p2", "p7"}
    assert {p["id"] for p in transport.rotate_bank(bank)["retained"]} == {"p2", "p7"}
    assert main([*args, "--execute", "--expected-plan-sha256", pinned["plan_sha256"]]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert len(applied["deleted"]) == 6 and (bank / "p2/checkpoint.json").exists()
    (state / "policy.json").write_text("{}")
    assert main([*args, "--execute"]) == 1
    assert not json.loads(capsys.readouterr().out)["ok"]
    assert (bank / "p2/checkpoint.json").exists()


def test_cli_watcher_rotates_new_publication_and_silences_unchanged_generation(bank, policy, tmp_path, capsys, monkeypatch):
    from loopx.cli_commands import research_checkpoint as cli
    policy.update(keep_first=False, keep_latest=1, history_slots=0)
    request = tmp_path / "policy.json"
    request.write_text(json.dumps(policy))
    sleeps = []
    def wait(_):
        sleeps.append(True)
        if len(sleeps) == 1:
            components = [dict(id="research", source_path=str(bank / "p7/components/research"))]
            probe = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components})
            effect_runtime_result("runtime.checkpoint_artifacts", {"action": "capture", "components": components,
                "destination": str(bank / "p8"), "point_metadata": {}, "expected_source_sha256": probe["source_sha256"]})
        elif len(sleeps) == 3:
            raise KeyboardInterrupt
    import time
    monkeypatch.setattr(cli, "time", SimpleNamespace(time=time.time, sleep=wait))
    with pytest.raises(KeyboardInterrupt):
        main(["--format", "json", "research-checkpoint", "rotate", "--root", str(bank),
              "--request-file", str(request), "--execute", "--watch", "--interval-seconds", "10"])
    packets = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(packets) == 2
    assert [p["id"] for p in packets[1]["deleted"]] == ["p7"]
    assert (bank / "p8/checkpoint.json").exists()


def test_cli_watcher_revisits_grace_expiry_without_a_new_capture(bank, policy, tmp_path, capsys, monkeypatch):
    from loopx.cli_commands import research_checkpoint as cli
    clock, sleeps = [1800.0], []
    policy.update(history_slots=0, minimum_age_seconds=500)
    request = tmp_path / "policy.json"
    request.write_text(json.dumps(policy))
    def wait(_):
        sleeps.append(True)
        if len(sleeps) > 1:
            raise KeyboardInterrupt
        clock[0] += 600
    monkeypatch.setattr(cli, "time", SimpleNamespace(time=lambda: clock[0], sleep=wait))
    monkeypatch.setattr(transport, "time", SimpleNamespace(time=lambda: clock[0]))
    with pytest.raises(KeyboardInterrupt):
        main(["--format", "json", "research-checkpoint", "rotate", "--root", str(bank),
              "--request-file", str(request), "--execute", "--watch", "--interval-seconds", "10"])
    packets = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(packets) == 2
    assert {p["id"] for p in packets[1]["deleted"]} == {"p4", "p5"}
    assert [p.name for p in sorted(bank.glob("p*"))] == ["p0", "p6", "p7"]
