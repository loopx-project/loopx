"""Origin binding, controlled cancellation and durable return through real owners."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from test_issue_fix_maintainer_correction_admission import (
    Harness, GOAL_ID, AGENT_ID, PR_URL, ROOT, _event, _metadata, _correction_v1,
)
from loopx.domain_packs.issue_fix import default_issue_fix_domain_state_ledger_path
from loopx.control_plane.collaboration.inbox import acknowledge
from loopx.control_plane.collaboration.peers import request as peer_request
from loopx.control_plane.collaboration.request_control_source import execute_request_control
from loopx.capabilities.issue_fix.correction_settlement import _settle_maintainer_correction_entries


ORIGIN_GOAL_ID = "steward-goal"
ORIGIN_AGENT_ID = "steward-reviewer"


class BoundHarness(Harness):
    run = None  # assigned below after the shared CLI adapter is defined


class OriginHarness(Harness):
    def __init__(self, root, provider):
        super().__init__(root, provider)
        self.origin_registry = root / "origin" / "registry.json"
        self.runtime = root / "origin-runtime"
        self._counter = 0
        args = [sys.executable, "-m", "loopx.cli", "--registry", str(self.origin_registry),
            "--runtime-root", str(self.runtime), "--format", "json", "project", "register",
            "--project-id", "correction-project", "--project-kind", "work",
            "--knowledge-root", str(root / "origin"), "--goal-id", ORIGIN_GOAL_ID,
            "--objective", "Apply bounded corrections", "--non-goal", "No remote changes",
            "--acceptance", "Independent validation passes", "--next-effect", "Inspect request",
            "--stop-condition", "Stop on cancellation", "--goal-instance-profile", "source_session_v1",
            "--operation-id", "create-goal", "--registered-agent", ORIGIN_AGENT_ID,
            "--registered-agent", "requester", "--canonical-storage", provider]
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        self.ref = json.loads(result.stdout)["goal_ref"]
        brief = {"schema_version": "collaboration_brief_v0", "purpose": "Validate correction",
            "context": "Preserve compatibility", "constraints": ["No remote changes"], "inputs": [],
            "acceptance": ["Tests pass"], "return_requirement": "Return validation result"}
        self.request_id = peer_request(self.runtime, self.origin_registry, ORIGIN_GOAL_ID, "requester",
            ORIGIN_AGENT_ID, "correction-request", brief, caller_goal_ref=self.ref)["request_id"]
        acknowledge(self.runtime, ORIGIN_GOAL_ID, ORIGIN_AGENT_ID, self.request_id, "adopt",
            "Validate bounded correction", registry=self.origin_registry, caller_goal_ref=self.ref)

    def control(self, operation, parameters=None):
        return execute_request_control(registry_path=self.origin_registry, runtime_root=self.runtime,
            goal_ref=self.ref, receiver_agent_id=ORIGIN_AGENT_ID, request_id=self.request_id,
            actor_agent_id="requester", operation=operation, parameters=parameters,
            execute=operation != "inspect")["view"]

    def cancel(self):
        view = self.control("inspect")
        return self.control("cancel", {"operation_id": "cancel-request", "expected_revision": view["revision"],
            "expected_digest": view["revision_digest"], "reason": "Stop corrections"})

    def run(self, *, metadata, correction, origin=None, env=None, check=True, origin_registry=None, origin_runtime=None, extra_args=()):
        args = [sys.executable, "-m", "loopx.cli", "--registry", str(self.registry),
            "--format", "json", "issue-fix", "pr-lifecycle", "--url", PR_URL,
            "--metadata-json", str(self._write_json("metadata", metadata)), "--goal-id", GOAL_ID,
            "--project", str(self.project), "--claimed-by", AGENT_ID, "--execute-transition"]
        if origin:
            args += ["--origin-request-id", origin, "--origin-registry", str(origin_registry or self.origin_registry),
                "--origin-runtime-root", str(origin_runtime or self.runtime)]
        if correction is not None:
            args += ["--maintainer-correction-json", str(self._write_json("correction", correction))]
        args += list(extra_args)
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, **(env or {})})
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    def replies(self):
        return list((self.runtime / ".local/manager-context/replies" / self.request_id).glob("conclusion*.json"))


BoundHarness.run = OriginHarness.run
BoundHarness.replies = OriginHarness.replies


@pytest.fixture(params=["file", "sqlite"])
def legacy(tmp_path, request):
    h = OriginHarness(tmp_path, request.param)
    return h


@pytest.fixture(params=["file", "sqlite"])
def h(tmp_path, request):
    return OriginHarness(tmp_path, request.param)


def observation(events, head="a" * 40):
    return {**_metadata(events), "headRefOid": head}


def settle(h, packet):
    return _settle_maintainer_correction_entries(payload=packet,
        ledger_path=default_issue_fix_domain_state_ledger_path(project=h.project, goal_id=GOAL_ID),
        registry_path=h.registry, goal_id=GOAL_ID, claimed_by=AGENT_ID, project=h.project)


def test_return_head_prefix_collision(h):
    event = _event("309")
    h.run_json(metadata=observation([event]), correction=_correction_v1(event), origin=h.request_id)
    h.mark_done(h.entries()[0]["todo_id"])
    for suffix in "123":
        h.run_json(metadata=observation([event], "b" * 12 + suffix * 28), correction=None)
    returns = h.entries()[0]["returns"]
    assert len(h.replies()) == len(returns) == 3
    assert len({r["update_id"] for r in returns}) == 3
    replies = [json.loads(path.read_text()) for path in h.replies()]
    assert len({r.get("update_id") for r in replies}) == 3


def test_stored_goal_ref_mismatch(h):
    event = _event("310")
    h.run_json(metadata=observation([event]), correction=None, origin=h.request_id)
    row = h.ledger_row()
    row["origin_request"]["goal_ref"]["goal_instance_id"] = "other-instance"
    ledger = default_issue_fix_domain_state_ledger_path(project=h.project, goal_id=GOAL_ID)
    ledger.write_text(json.dumps(row) + "\n")
    packet = h.run_json(metadata=observation([event]), correction=_correction_v1(event))
    assert packet["maintainer_correction_admission"]["reason"] == "origin_request_unreadable"
    assert not h.correction_todos()
    assert not h.replies()


@pytest.mark.parametrize("owner,name", [
    ("loopx.capabilities.issue_fix.correction_settlement", "_resolve_origin_request_identity"),
    ("loopx.control_plane.collaboration.request_control_source", "execute_request_control"),
])
def test_programming_error_propagates(h, monkeypatch, owner, name):
    event = _event("311")
    packet = h.run_json(metadata=observation([event]), correction=None, origin=h.request_id)

    def broken(*args, **kwargs):
        raise TypeError("programming error")

    monkeypatch.setattr(f"{owner}.{name}", broken)
    with pytest.raises(TypeError, match="programming error"):
        settle(h, packet)


def test_report_receives_goal_ref(h, monkeypatch):
    from loopx.capabilities.manager_context import roundtrip

    event = _event("312")
    packet = h.run_json(metadata=observation([event]), correction=_correction_v1(event), origin=h.request_id)
    h.mark_done(h.entries()[0]["todo_id"])
    packet["observation"]["head_commit_ref"] = "b" * 40
    received = []
    original = roundtrip.report

    def report(*args, **kwargs):
        if kwargs.get("registry") is not None:
            received.append(kwargs["caller_goal_ref"])
        return original(*args, **kwargs)

    monkeypatch.setattr(roundtrip, "report", report)
    settle(h, packet)
    assert received == [h.ref]
    assert len(h.replies()) == 1


def test_binding_conflict_and_unreadable(legacy):
    h = legacy
    event = _event("301")
    h.run(metadata=observation([event]), correction=None, origin=h.request_id)
    binding = h.ledger_row()["origin_request"]
    assert binding["goal_id"] == ORIGIN_GOAL_ID
    assert binding["agent_id"] == ORIGIN_AGENT_ID
    assert binding["goal_ref"] == h.ref
    h.run(metadata=observation([event]), correction=None)
    assert h.ledger_row()["origin_request"] == binding
    before = h.ledger_row()
    result = h.run(metadata=observation([event]), correction=None, origin="f" * 64, check=False)
    assert result.returncode == 1 and "origin_request_conflict" in result.stdout
    assert h.ledger_row() == before
    import shutil
    other_registry = h.root / "other-registry.json"
    shutil.copyfile(h.origin_registry, other_registry)
    other_runtime = h.root / "other-runtime"
    other_runtime.mkdir()
    for overrides in ({"origin_registry": other_registry}, {"origin_runtime": other_runtime}):
        result = h.run(metadata=observation([event]), correction=None, origin=h.request_id,
            check=False, **overrides)
        assert result.returncode == 1 and "origin_request_conflict" in result.stdout
        assert h.ledger_row() == before


def test_cancelled_before_admission(h):
    h.cancel()
    event = _event("302")
    result = h.run_json(metadata=observation([event]), correction=_correction_v1(event), origin=h.request_id)
    assert result["maintainer_correction_admission"]["reason"] == "origin_request_cancelled"
    assert h.entries()[0]["disposition"] == "context_only"
    assert h.correction_todos() == []
    assert h.replies() == []


def test_return_once_per_head(h):
    event = _event("303")
    h.run_json(metadata=observation([event]), correction=_correction_v1(event), origin=h.request_id)
    entry = h.entries()[0]
    assert entry["accepted_head_sha"] == "a" * 40
    h.mark_done(entry["todo_id"])
    for _ in range(3):
        packet = h.run_json(metadata=observation([event], "b" * 40), correction=None)
    assert len(h.replies()) == len(h.entries()[0]["returns"]) == 1
    assert packet["return_delivery"][0]["status"] == "queued"
    assert str(h.origin_registry) not in json.dumps(packet)
    assert str(h.runtime) not in json.dumps(packet)
    reply = json.loads(h.replies()[0].read_text())
    assert PR_URL in reply["text"] and "bbbbbbbbbbbb" in reply["text"]
    assert str(h.origin_registry) not in reply["text"]
    assert str(h.runtime) not in reply["text"]
    h.run_json(metadata=observation([event], "c" * 40), correction=None)
    assert len(h.replies()) == len(h.entries()[0]["returns"]) == 2


def test_cancel_after_existing_todo(h):
    event = _event("304")
    h.run_json(metadata=observation([event]), correction=_correction_v1(event), origin=h.request_id)
    todo = h.entries()[0]["todo_id"]
    h.mark_done(todo)
    h.cancel()
    h.run_json(metadata=observation([event], "b" * 40), correction=_correction_v1(event))
    assert [t["todo_id"] for t in h.correction_todos()] == [todo]
    assert not h.replies()


def test_unbound_legacy_return_is_absent(legacy):
    h = legacy
    event = _event("305")
    h.run_json(metadata=observation([event]), correction=_correction_v1(event))
    h.mark_done(h.entries()[0]["todo_id"])
    h.run_json(metadata=observation([event], "b" * 40), correction=None)
    assert not h.replies()


def test_two_entry_crash_recovery(legacy):
    h = legacy
    first, second = _event("306"), _event("307")
    h.run_json(metadata=observation([first, second]), correction=_correction_v1(first))
    failed = h.run(metadata=observation([first, second]), correction=_correction_v1(second),
        env={"LOOPX_ISSUE_FIX_FAULT_BEFORE_CORRECTION_TODO": "1"}, check=False)
    assert failed.returncode == 1
    assert sum(bool(e["todo_id"]) for e in h.entries()) == 1
    for _ in range(2):
        h.run_json(metadata=observation([first, second]), correction=None)
    assert len(h.correction_todos()) == 2
    assert len({e["todo_id"] for e in h.entries()}) == 2


@pytest.mark.parametrize("damage", ["delete", "rename", "tamper"])
def test_unreadable_request_fails_closed(h, damage):
    event = _event("308")
    h.run_json(metadata=observation([event]), correction=None, origin=h.request_id)
    path = next((h.runtime / ".local/manager-context/entries").glob(f"*/{h.request_id}.json"))
    if damage == "delete":
        path.unlink()
    elif damage == "rename":
        path.rename(path.with_suffix(".missing"))
    else:
        ledger = default_issue_fix_domain_state_ledger_path(project=h.project, goal_id=GOAL_ID)
        row = h.ledger_row()
        row["origin_request"]["goal_id"] = "tampered-goal"
        ledger.write_text(json.dumps(row) + "\n")
    packet = h.run_json(metadata=observation([event]), correction=_correction_v1(event))
    assert packet["maintainer_correction_admission"]["reason"] == "origin_request_unreadable"
    assert h.entries()[0]["disposition"] == "context_only"
    assert not h.correction_todos()
    assert not h.replies()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("location", ["nowhere", "project"])
def test_missing_origin_request_writes_nothing(h, existing, location):
    if existing:
        h.run_json(metadata=observation([]), correction=None)
    request_id = "a" * 64
    if location == "project":
        path = h.root / "runtime/.local/manager-context/entries/project" / f"{request_id}.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"goal_id": GOAL_ID, "agent_id": AGENT_ID}))
    ledger = default_issue_fix_domain_state_ledger_path(project=h.project, goal_id=GOAL_ID)
    before = ledger.read_bytes() if ledger.exists() else None
    result = h.run(metadata=observation([]), correction=None, origin=request_id, check=False)
    assert result.returncode == 1
    assert json.loads(result.stdout)["ok"] is False
    assert json.loads(result.stdout)["error"] == "origin_request_not_found"
    assert (ledger.read_bytes() if ledger.exists() else None) == before


@pytest.mark.parametrize("flags", [
    ["--origin-request-id", "a" * 64],
    ["--origin-registry", "unused"],
    ["--origin-runtime-root", "unused"],
    ["--origin-request-id", "a" * 64, "--origin-registry", "unused"],
    ["--origin-request-id", "a" * 64, "--origin-runtime-root", "unused"],
])
def test_missing_origin_companions_write_nothing(tmp_path, flags):
    harness = BoundHarness(tmp_path, "file")
    result = harness.run(metadata=observation([]), correction=None, check=False, extra_args=flags)
    assert result.returncode == 1
    assert json.loads(result.stdout)["ok"] is False
    assert not default_issue_fix_domain_state_ledger_path(project=harness.project, goal_id=GOAL_ID).exists()


@pytest.mark.parametrize("invalid", ["id", "registry_missing", "runtime_missing", "profile", "decode"])
def test_invalid_origin_write_nothing(h, invalid):
    overrides = {}
    origin = h.request_id
    if invalid == "id":
        origin = "invalid"
    elif invalid == "registry_missing":
        overrides["origin_registry"] = h.root / "missing"
    elif invalid == "runtime_missing":
        overrides["origin_runtime"] = h.root / "missing"
    elif invalid == "profile":
        overrides["origin_registry"] = h.registry
    else:
        broken = h.root / "broken.json"
        broken.write_text("invalid json")
        overrides["origin_registry"] = broken
    result = h.run(metadata=observation([]), correction=None, origin=origin, check=False, **overrides)
    assert result.returncode == 1
    assert json.loads(result.stdout)["ok"] is False
    assert str(h.runtime) not in result.stdout
    assert not default_issue_fix_domain_state_ledger_path(project=h.project, goal_id=GOAL_ID).exists()


@pytest.mark.parametrize("damage,error", [
    ("duplicate", "origin_request_ambiguous"),
    ("json", "origin_request_not_found"),
    ("recipient", "origin_request_unsupported_target"),
])
def test_invalid_request_entry_writes_nothing(h, damage, error):
    path = next((h.runtime / ".local/manager-context/entries").glob(f"*/{h.request_id}.json"))
    if damage == "duplicate":
        duplicate = path.parent.parent / "duplicate" / path.name
        duplicate.parent.mkdir()
        duplicate.write_bytes(path.read_bytes())
    elif damage == "json":
        path.write_text("invalid json")
    else:
        path.write_text(json.dumps({"recipient": {"agent_id": "receiver"}}))
    result = h.run(metadata=observation([]), correction=None, origin=h.request_id, check=False)
    assert result.returncode == 1
    assert json.loads(result.stdout)["ok"] is False
    assert json.loads(result.stdout)["error"] == error
    assert not default_issue_fix_domain_state_ledger_path(project=h.project, goal_id=GOAL_ID).exists()


@pytest.mark.parametrize("change", ["checks", "revision"])
def test_return_ledger_failure_replays_the_immutable_publication(h, monkeypatch, change):
    from loopx.capabilities.issue_fix import correction_settlement

    event = _event("313")
    packet = h.run_json(metadata=observation([event]), correction=_correction_v1(event), origin=h.request_id)
    h.mark_done(h.entries()[0]["todo_id"])
    packet["observation"]["head_commit_ref"] = "b" * 40
    original = correction_settlement.upsert_issue_fix_pr_lifecycle_ledger_jsonl

    def fail_return_write(path, payload):
        if any(entry.get("returns") for entry in payload["maintainer_corrections"]):
            raise OSError("return ledger interrupted")
        return original(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(correction_settlement, "upsert_issue_fix_pr_lifecycle_ledger_jsonl", fail_return_write)
        with pytest.raises(OSError, match="return ledger interrupted"):
            settle(h, packet)
    assert len(h.replies()) == 1
    pending = h.entries()[0]
    assert not pending.get("returns")
    publication = pending["pending_return"]
    assert publication["head_sha"] == "b" * 40
    assert "checks PASSING" in publication["text"]
    retry_observation = observation([event], "b" * 40)
    if change == "checks":
        retry_observation["statusCheckRollup"] = [{"name": "focused", "conclusion": "FAILURE"}]
    else:
        view = h.control("inspect")
        h.control("revise", {
            "operation_id": "revision-after-publication",
            "expected_revision": view["revision"],
            "expected_digest": view["revision_digest"],
            "brief": {**view["brief"], "purpose": "Validate the changed correction"},
            "reason": "The request changed after publication",
        })
    h.run_json(metadata=retry_observation, correction=None)
    assert len(h.replies()) == len(h.entries()[0]["returns"]) == 1
    assert json.loads(h.replies()[0].read_text())["text"] == publication["text"]
    if change == "checks":
        h.run_json(metadata=observation([event], "c" * 40), correction=None)
        assert len(h.replies()) == len(h.entries()[0]["returns"]) == 2
