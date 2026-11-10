"""Independent quota, credential isolation and ambiguous-write boundaries."""
from __future__ import annotations

import base64
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

        def status(self, *, adopt_current=True):
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


@pytest.mark.parametrize("selection,late_change", [
    ("selected", None), ("stale", None), ("unselected", None),
    ("selected", "login"), ("selected", "recovery"), ("selected", "inventory"),
])
@pytest.mark.parametrize("execute", [False, True])
def test_real_native_preparation_preserves_service_recovery_and_selection(
        tmp_path, monkeypatch, selection, late_change, execute):
    """A quota preview cannot repair desktop state, even when repair is possible."""
    package = Path(__file__).resolve().parents[1] / "packages/loopx-codex-provider-routing/src"
    monkeypatch.syspath_prepend(str(package))
    from loopx_codex_provider_routing import native_host

    home = tmp_path.resolve()
    monkeypatch.setenv("CODEX_HOME", str(home / "codex"))
    monkeypatch.setenv("CODEX_ELECTRON_USER_DATA_PATH", str(home / "frontend"))
    which = native_host.shutil.which
    monkeypatch.setattr(native_host.shutil, "which", lambda name: "/usr/bin/true" if name == "codex" else which(name))
    discover = native_host.discover
    host_type = native_host.NativeHost
    monkeypatch.setattr(native_host, "discover", lambda: discover(user_home=home))
    monkeypatch.setattr(native_host, "NativeHost",
        lambda adapter, **kwargs: host_type(adapter, user_home=home, **kwargs))
    monkeypatch.setattr(native_host.sys, "platform", "darwin")
    monkeypatch.setattr(native_host.native_app, "app_bundle", lambda *_: home / "Fixture.app")
    monkeypatch.setattr(native_host.native_app, "executable", lambda _: Path("/usr/bin/true"))
    monkeypatch.setattr(native_host.native_app, "running", lambda *_: None)
    resumed = []
    monkeypatch.setattr(host_type, "resume_chat_agents", lambda _: resumed.append(True))
    spec = native_host.native_app.route(home, "gpt")
    accounts = native_host.native_account_store
    store = accounts.Accounts(home, spec, None, None, None)
    for slot in ("source", "large"):
        claims = base64.urlsafe_b64encode(json.dumps({"sub": "fixture-" + slot}).encode()).decode().rstrip("=")
        raw = json.dumps({"tokens": {"id_token": "fixture." + claims + ".signature",
            "access_token": "fixture-access", "refresh_token": "fixture-refresh",
            "account_id": "fixture-" + slot}}).encode()
        accounts.atomic(store.slot(slot), raw)
    accounts.atomic(store.auth, accounts.read_private(store.slot("large")))
    if selection != "unselected":
        slot = "large" if selection == "selected" else "source"
        accounts.atomic(store.active, accounts.encoded(dict(slot=slot, binding=store.binding,
            identity=accounts.identity(accounts.read_private(store.slot(slot))))))
    accounts.atomic(store.root / "chat-recovery.json", b'{"fixture":true}')
    destination = home / "isolated"

    def snapshot():
        return {str(p.relative_to(home)): (p.stat().st_mode, p.read_bytes() if p.is_file() else None)
                for p in home.rglob("*") if not p.is_relative_to(destination)}

    expected_snapshot = [snapshot()]
    source_raw = accounts.read_private(store.slot("source"))
    observations = []

    def usage(raw):
        observations.append(raw)
        if late_change and len(observations) == 2:
            if late_change == "login":
                # A non-cooperating desktop restore leaves the managed
                # selection revision unchanged, but invalidates ownership.
                accounts.atomic(store.auth, source_raw)
            elif late_change == "recovery":
                accounts.atomic(store.pending, b'{"fixture":true}')
            else:
                path = store.slot("source")
                path.unlink()
                path.parent.rmdir()
            expected_snapshot[0] = snapshot()
        return {"rateLimitsByLimitId": {"codex": {"primary": {
            "usedPercent": 100 if raw == source_raw else 10,
            "resetsAt": 5000}, "credits": {"hasCredits": False}}}}

    monkeypatch.setattr(native_host, "read_usage", usage)
    args = dict(source_login=store.slot("source"), destination=destination,
        slots=["source", "large"], operation_id="preview", run_id="fixture-run",
        deadline_epoch=3000, clock=lambda: 1000, execute=execute)
    if selection == "selected" and late_change is None:
        result = prepare(**args)
        assert result["decision"]["target"] == "large"
        assert result["prepared"] is execute
        assert destination.exists() is execute
        if execute:
            assert accounts.read_private(destination / "auth.json") == accounts.read_private(store.auth)
    else:
        with pytest.raises(ValueError, match="ownership is unavailable"):
            prepare(**args)
        assert not destination.exists()
    assert not resumed
    assert snapshot() == expected_snapshot[0]
    # Ordinary account-manager status retains its existing adoption behavior.
    if selection != "selected":
        assert store.status()["active_slot"] == "large"
        assert store.state()["slot"] == "large"
