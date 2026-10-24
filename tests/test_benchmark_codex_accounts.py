"""Independent quota, credential isolation and ambiguous-write boundaries."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark.runtime.codex_accounts import main, prepare


@pytest.fixture
def owner(tmp_path, monkeypatch):
    package = Path(__file__).resolve().parents[1] / "packages/loopx-codex-provider-routing/src"
    monkeypatch.syspath_prepend(str(package))
    from loopx_codex_provider_routing import native_host

    paths = {}
    for slot in ["source", "small", "large"]:
        paths[slot] = tmp_path / (slot + ".json")
        paths[slot].write_text(json.dumps({"synthetic_identity": slot}))
        paths[slot].chmod(0o600)
    calls = []
    # The smaller remaining window limits a candidate; use the production TS
    # selector, never a test-side reimplementation of its ordering algorithm.
    windows = {"source": (100, 100), "small": (1, 95), "large": (30, 40)}

    def usage(raw):
        slot = json.loads(raw)["synthetic_identity"]
        calls.append(slot)
        first, second = windows[slot]
        return {"rateLimitsByLimitId": {"codex": {
            "primary": {"usedPercent": first, "resetsAt": 4000},
            "secondary": {"usedPercent": second, "resetsAt": 5000},
            "credits": {"hasCredits": False}}}}

    monkeypatch.setattr(native_host, "read_usage", usage)

    class Native:
        accounts = SimpleNamespace(read_private=lambda p: p.read_bytes(),
            identity=lambda raw: json.loads(raw)["synthetic_identity"])
        manager = SimpleNamespace(auth=paths["large"], slot=paths.__getitem__)
        revision = "original"

        def status(self):
            return dict(slots=list(paths), active_slot="large", current_login_matches=True,
                        recovery_required=False, selection_revision=self.revision)

    native = Native()
    args = dict(source_login=paths["source"], destination=tmp_path / "isolated",
                slots=list(paths), operation_id="recovery-1", run_id="source-run",
                deadline_epoch=3000, native=native, clock=lambda: 1000)
    return SimpleNamespace(args=args, native=native, paths=paths, calls=calls,
                           windows=windows, host=native_host)


def test_preview_reuses_native_selection_without_changing_any_login(owner):
    result = prepare(**owner.args)
    assert result["decision"]["target"] == "large"
    assert result["prepared"] is False
    assert not owner.args["destination"].exists()
    assert owner.native.status()["active_slot"] == "large"
    assert owner.calls == ["source", "small", "large"]


def test_prepare_is_private_exclusive_and_replay_does_not_repeat_effect(owner, monkeypatch):
    original = {slot: p.read_bytes() for slot, p in owner.paths.items()}
    result = prepare(**owner.args, execute=True)
    destination = owner.args["destination"]
    assert result["prepared"] is True
    assert (destination / "auth.json").read_bytes() == original["large"]
    assert destination.stat().st_mode & 0o077 == 0
    assert all(p.stat().st_mode & 0o077 == 0 for p in destination.iterdir())
    assert {slot: p.read_bytes() for slot, p in owner.paths.items()} == original
    monkeypatch.setattr(owner.host, "read_usage", lambda _: pytest.fail("replayed quota query"))
    repeated = prepare(**owner.args, execute=True)
    assert repeated["idempotent"] is True
    assert repeated["fresh_allowance_required_before_launch"] is True
    with pytest.raises(ValueError, match="destination changed"):
        prepare(**{**owner.args, "operation_id": "another-operation"}, execute=True)


@pytest.mark.parametrize("failure", ["quota_unknown", "auth_required", "still_available", "credits"])
def test_no_handoff_for_unproven_exhaustion_or_credit_funded_source(owner, monkeypatch, failure):
    original = owner.host.read_usage

    def usage(raw):
        if json.loads(raw)["synthetic_identity"] != "source":
            return original(raw)
        if failure == "quota_unknown":
            return {"rateLimitsByLimitId": {"other_product": {"allowed": False}}}
        if failure == "auth_required":
            raise owner.host.HostError("auth_required")
        value = original(raw)
        bucket = value["rateLimitsByLimitId"]["codex"]
        if failure == "still_available":
            bucket["primary"]["usedPercent"] = bucket["secondary"]["usedPercent"] = 20
        else:
            bucket["credits"]["hasCredits"] = True
        return value

    monkeypatch.setattr(owner.host, "read_usage", usage)
    result = prepare(**owner.args, execute=True)
    assert result["decision"]["action"] != "switch"
    assert not owner.args["destination"].exists()


def test_changed_native_selection_cannot_publish_credentials(owner, monkeypatch):
    original = owner.host.read_usage

    def changed(raw):
        result = original(raw)
        owner.native.revision = "manual-selection-wins"
        return result

    monkeypatch.setattr(owner.host, "read_usage", changed)
    with pytest.raises(ValueError, match="selection changed"):
        prepare(**owner.args, execute=True)
    assert not owner.args["destination"].exists()


def test_expired_deadline_cannot_prepare_or_replay(owner):
    with pytest.raises(ValueError, match="unexpired original deadline"):
        prepare(**{**owner.args, "deadline_epoch": 999}, execute=True)
    assert not owner.calls
    assert not owner.args["destination"].exists()


def test_partial_write_and_symlink_are_inspection_gates_not_overwrites(owner):
    destination = owner.args["destination"]
    destination.mkdir(mode=0o700)
    marker = destination / "auth.json"
    marker.write_text("retained uncertain write")
    marker.chmod(0o600)
    with pytest.raises(FileNotFoundError):
        prepare(**owner.args, execute=True)
    assert marker.read_text() == "retained uncertain write"
    link = destination.parent / "linked-destination"
    link.symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError):
        prepare(**{**owner.args, "destination": link}, execute=True)
    assert marker.read_text() == "retained uncertain write"


def test_cli_failure_does_not_expose_private_paths_or_provider_details(owner, capsys):
    result = main(["--source-login", "/private/missing-login", "--destination",
        str(owner.args["destination"]), "--slots", "source", "large", "--operation-id", "recover",
        "--run-id", "run", "--deadline-epoch", "1", "--execute"])
    assert result == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ok": False, "code": "continuation_login_unavailable"}
    assert "/private" not in output.out and not output.err
