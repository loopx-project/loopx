"""Fenced sibling-caller parity through the real CLI (Stage 2C E2E).

Every row runs one public command against a workspace whose durable legacy
writer fence is in one state, and compares the complete printed envelope, the
exit status, and the primary-record effect with the literal expectation in
tests/fixtures/control_plane/legacy_writer_fence_caller_parity_v0.json.
Nothing is mocked; the fence is engaged by the real TypeScript owner.
"""
from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from test_shadow_observable_e2e import Caller
from test_shadow_observable_native_e2e import native

pytestmark = pytest.mark.stage2c_e2e

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "control_plane" / "legacy_writer_fence_caller_parity_v0.json"
BUILDER = (
    "from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection as build; "
    "import json,sys; value=build(goal_id='observable', todos=json.loads(sys.argv[1]), "
    "leases=json.loads(sys.argv[2])); "
    "value['handoff_mode']='hard_lease'; print(json.dumps(value))"
)
PLACEHOLDERS = ("runtime_root", "todo_a", "todo_b", "todo_c", "todo_gate")


def _posix_runtime_paths(value: object) -> object:
    """Spell paths under the runtime root with the fixture's POSIX separators."""
    if isinstance(value, str) and value.startswith("{runtime_root}"):
        return value.replace("\\", "/")
    if isinstance(value, list):
        return [_posix_runtime_paths(item) for item in value]
    if isinstance(value, dict):
        return {key: _posix_runtime_paths(item) for key, item in value.items()}
    return value


class Workspace:
    """One seeded goal; rows are executed against it in fixture order."""

    def __init__(self, path: Path, mode: str, name: str) -> None:
        self.w = Caller(path, mode, name)
        self.ids: dict[str, str] = {}
        self.lease_version = 0
        self.lease_owner = "agent-a"
        self.lease_key = "parity-lease"
        self.control: dict[str, dict] | None = None

    def call(self, *args: str) -> dict:
        return self.w.call(*args)

    def exit(self) -> int:
        return int(self.w.rows[-1]["exit"])

    def outbox(self) -> set[str]:
        return {k for k in self.w.files() if k.startswith("runtime/authority-shadow/outbox/")}

    def fence_path(self) -> Path:
        from loopx.control_plane.coordination.legacy_writer_fence import (
            legacy_coordination_writer_fence_path,
        )

        return legacy_coordination_writer_fence_path(runtime_root=self.w.root, goal_id="observable")

    def normalize(self, value: object) -> object:
        text = json.dumps(value)
        text = text.replace(json.dumps(str(self.w.root))[1:-1], "{runtime_root}")
        for key in ("todo_gate", "todo_c", "todo_b", "todo_a"):
            if key in self.ids:
                text = text.replace(self.ids[key], "{" + key + "}")
        return _posix_runtime_paths(json.loads(text))

    def observe(self, args: tuple[str, ...]) -> dict:
        before, outbox_before = self.w.primary(), self.outbox()
        envelope = self.call(*args)
        after, outbox_after = self.w.primary(), self.outbox()
        return {
            "envelope": self.normalize(envelope),
            "exit": self.exit(),
            "effect": {
                "added": sorted(k for k in after if k not in before),
                "removed": sorted(k for k in before if k not in after),
                "changed": sorted(k for k in after if k in before and after[k] != before[k]),
            },
            "outbox_added": sorted(outbox_after - outbox_before),
        }


def seed_and_fence(ws: Workspace, todo_keys: tuple[str, ...]) -> None:
    records = [ws.w.read(ws.ids[key]) for key in todo_keys]
    lease_path = (
        ws.w.root / "goals" / "observable" / "task-leases" /
        f"{ws.ids['todo_b']}.json"
    )
    leases = [json.loads(lease_path.read_text(encoding="utf-8"))]
    projection = ws.w.invoke(
        [sys.executable, "-c", BUILDER, json.dumps(records), json.dumps(leases)],
        ["fixture-projection", json.dumps(records), "<lease-fixture>"],
    )
    assert native(ws.w, "seed", projection)["status"] == "applied"


def build_seeded(path: Path, mode: str, name: str, *, gate: bool) -> Workspace:
    ws = Workspace(path, mode, name)
    assert ws.call("handoff-mode", "set", "--mode", "hard_lease")["ok"] is True
    ws.ids["todo_a"] = ws.w.add("Parity target A")
    ws.ids["todo_b"] = ws.w.add("Parity leased B")
    ws.ids["todo_c"] = ws.w.add("Parity fresh acquisition C")
    if gate:
        gate_add = ws.call("todo", "add", "--role", "user", "--task-class", "user_gate", "--global-gate",
                           "--text", "Approve the parity plan")
        assert gate_add["ok"] is True, gate_add
        ws.ids["todo_gate"] = gate_add["todo_id"]
    ws.control = {"cli-task_lease_acquire-absent": ws.observe((
        "task-lease", "acquire", "--todo-id", ws.ids["todo_b"], "--owner", "agent-a",
        "--idempotency-key", "parity-lease", "--ttl-seconds", "3600"))}
    lease = ws.control["cli-task_lease_acquire-absent"]["envelope"]
    assert lease.get("acquired") is True, lease
    ws.lease_version = int(lease["lease"]["version"])
    seed_and_fence(ws, ("todo_a", "todo_b", "todo_c", "todo_gate") if gate else ("todo_a", "todo_b", "todo_c"))
    return ws


def build_w3(path: Path) -> Workspace:
    ws = build_seeded(path, "absent", "fence-parity-invalid", gate=False)
    marker = json.loads(ws.fence_path().read_text(encoding="utf-8"))
    marker["state"] = "disengaged"
    ws.fence_path().write_text(json.dumps(marker), encoding="utf-8")
    return ws


def build_w4(path: Path) -> Workspace:
    ws = build_seeded(path, "absent", "fence-parity-unreadable", gate=False)
    ws.fence_path().unlink()
    ws.fence_path().mkdir()
    return ws


def build_w5(path: Path) -> Workspace:
    ws = Workspace(path, "absent", "fence-parity-no-store")
    assert ws.call("handoff-mode", "set", "--mode", "hard_lease")["ok"] is True
    ws.ids["todo_a"] = ws.w.add("Parity target A")
    ws.fence_path().parent.mkdir(parents=True, exist_ok=True)
    ws.fence_path().write_text(json.dumps({
        "schema_version": "loopx_legacy_coordination_writer_fence_v0", "state": "engaged", "goal_id": "observable",
        "fence_id": "caller-fixture", "source_version": "caller-fixture",
        "source_projection_sha256": "a" * 64, "expected_shadow_provider_revision": "file:1:aaaaaaaaaaaaaaaaaaaaaaaa",
    }), encoding="utf-8")
    return ws


def lease_args(ws: Workspace, verb: str) -> tuple[str, ...]:
    todo_b, version = ws.ids.get("todo_b", ""), str(ws.lease_version)
    if verb == "acquire":
        return ("task-lease", "acquire", "--todo-id", ws.ids.get("todo_c", ws.ids.get("todo_a", "")), "--owner", "agent-a",
                "--idempotency-key", "parity-acquire", "--ttl-seconds", "3600")
    common = ("task-lease", verb, "--todo-id", todo_b, "--owner", ws.lease_owner,
              "--idempotency-key", ws.lease_key, "--expected-version", version)
    if verb == "renew":
        return (*common, "--ttl-seconds", "7200")
    if verb == "transfer":
        return (*common, "--new-owner", "agent-b", "--new-idempotency-key", "parity-transfer")
    return common


def row_args(ws: Workspace, caller: str) -> tuple[str, ...]:
    a = ws.ids.get("todo_a", "")
    b = ws.ids.get("todo_b", "")
    gate = ws.ids.get("todo_gate", "")
    version = str(ws.lease_version)
    table: dict[str, tuple[str, ...]] = {
        "task_lease_acquire": lease_args(ws, "acquire"),
        "task_lease_renew": lease_args(ws, "renew"),
        "task_lease_transfer": lease_args(ws, "transfer"),
        "task_lease_release": lease_args(ws, "release"),
        "todo_complete": ("todo", "complete", "--todo-id", a, "--agent-id", "agent-a",
                          "--evidence", "validation://parity", "--no-follow-up"),
        "todo_update_status": ("todo", "update", "--todo-id", a, "--agent-id", "agent-a", "--status", "deferred"),
        "todo_supersede": ("todo", "supersede", "--todo-id", a, "--agent-id", "agent-a",
                           "--text", "Parity successor", "--evidence", "validation://parity"),
        "todo_archive_completed_execute": ("todo", "archive-completed", "--execute"),
        "todo_archive_completed_preview": ("todo", "archive-completed"),
        "handoff_mode_set": ("handoff-mode", "set", "--mode", "soft_claim"),
        "todo_complete_dry_run_gate": ("todo", "complete", "--todo-id", gate, "--agent-id", "agent-a",
                                       "--decision-outcome", "approve", "--evidence", "validation://parity",
                                       "--no-follow-up", "--dry-run"),
        "todo_supersede_dry_run": ("todo", "supersede", "--todo-id", a, "--agent-id", "agent-a",
                                   "--text", "Parity successor", "--evidence", "validation://parity", "--dry-run"),
        "todo_complete_dry_run_leased": ("todo", "complete", "--todo-id", b, "--agent-id", ws.lease_owner,
                                         "--task-lease-idempotency-key", ws.lease_key,
                                         "--task-lease-expected-version", version,
                                         "--evidence", "validation://parity", "--no-follow-up", "--dry-run"),
    }
    return table[caller]


WORKSPACES = {
    "w1": lambda path: build_seeded(path, "absent", "fence-parity-engaged", gate=True),
    "w2": lambda path: build_seeded(path, "enabled", "fence-parity-engaged-capture", gate=False),
    "w3": build_w3,
    "w4": build_w4,
    "w5": build_w5,
}


def load_rows() -> list[dict]:
    if not FIXTURE.exists():
        # Keep the module importable for the fixture recorder; the test itself fails loudly.
        return [{"id": "fixture-missing", "surface": "cli", "workspace": None}]
    return [row for row in json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"] if row["surface"] == "cli"]


@pytest.fixture(scope="module")
def workspaces(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Callable[[str], Workspace]]:
    built: dict[str, Workspace] = {}

    def get(key: str) -> Workspace:
        # Single-row mutation probes need only this row's workspace. Keep reuse
        # and ordered mutations within a module, never across worker processes.
        if key not in built:
            built[key] = WORKSPACES[key](tmp_path_factory.mktemp(key))
        return built[key]

    yield get
    for ws in built.values():
        shutil.rmtree(ws.w.path, ignore_errors=True)


def observe_row(ws: Workspace, row: dict) -> dict:
    if ws.control and row["id"] in ws.control:
        return ws.control[row["id"]]
    return ws.observe(row_args(ws, row["caller"]))


def assert_canonical_lease_transition(ws: Workspace, caller: str, before: dict, observed: dict) -> None:
    """Prove the caller's transition and carry only independently checked proof."""
    assert "lease_path" not in observed["envelope"], observed
    canonical_after = native(ws.w, "read", {})
    old_head, new_head = before["head"], canonical_after["head"]
    assert int(new_head["cursor"]) == int(old_head["cursor"]) + 1
    assert new_head["head"]["todos"] == old_head["head"]["todos"]
    target = ws.ids["todo_b"]
    old_leases, new_leases = old_head["head"]["leases"], new_head["head"]["leases"]
    assert [lease for lease in new_leases if lease["todo_id"] != target] == [
        lease for lease in old_leases if lease["todo_id"] != target
    ]
    old = next(lease for lease in old_leases if lease["todo_id"] == target)
    current = next(lease for lease in new_leases if lease["todo_id"] == target)
    assert observed["envelope"]["lease"] == ws.normalize(current)
    released, transferred = caller == "task_lease_release", caller == "task_lease_transfer"
    expected = {
        "version": old["version"] + (0 if released else 1),
        "lease_epoch": old["lease_epoch"] + (1 if transferred else 0),
        "owner": "agent-b" if transferred else old["owner"],
        "idempotency_key": "parity-transfer" if transferred else old["idempotency_key"],
        "write_scopes": old["write_scopes"],
        "status": "released" if released else "active",
    }
    assert {key: current[key] for key in expected} == expected
    if released:
        assert current["expires_at"] == old["expires_at"]
        assert current["released_at"] == current["updated_at"]
    else:
        # Renew invalidates the old version; transfer also retires the sender's
        # owner/key. Preserve those distinct completion-proof rejections.
        stale = ws.call(*row_args(ws, "todo_complete_dry_run_leased"))
        code = "lease_cas_mismatch" if transferred else "version_mismatch"
        assert stale["ok"] is False and stale["error_code"] == code, stale
        assert native(ws.w, "read", {}) == canonical_after
    ws.lease_version = expected["version"]
    ws.lease_owner, ws.lease_key = expected["owner"], expected["idempotency_key"]
    # The receiver's current proof previews successfully, while a released
    # proof cannot authorize completion. Neither preview may mutate authority.
    preview = ws.call(*row_args(ws, "todo_complete_dry_run_leased"))
    if released:
        assert preview["ok"] is False and preview["error_code"] == "handoff_mode_requires_lease", preview
    else:
        assert preview["ok"] is True and preview["completed"] is True, preview
    assert native(ws.w, "read", {}) == canonical_after


def assert_canonical_acquisition(ws: Workspace, before: dict, observed: dict) -> None:
    """New execution C must not change the existing B proof or keyless A tests."""
    after = native(ws.w, "read", {})
    old, current = before["head"], after["head"]
    assert int(current["cursor"]) == int(old["cursor"]) + 1
    assert current["head"]["todos"] == old["head"]["todos"]
    target = ws.ids["todo_c"]
    assert [r for r in current["head"]["leases"] if r["todo_id"] != target] == old["head"]["leases"]
    lease = next(r for r in current["head"]["leases"] if r["todo_id"] == target)
    assert lease["owner"] == "agent-a" and lease["idempotency_key"] == "parity-acquire"
    assert lease["version"] == lease["lease_epoch"] == 1 and lease["status"] == "active"
    assert observed["envelope"]["lease"] == ws.normalize(lease)
    assert "lease_path" not in observed["envelope"]
    replay = ws.call(*lease_args(ws, "acquire"))
    assert replay["ok"] and replay["idempotent"] and not replay["acquired"]
    assert replay["lease"] == lease
    assert native(ws.w, "read", {}) == after


@pytest.mark.parametrize("row", load_rows(), ids=[row["id"] for row in load_rows()])
def test_fence_caller_parity(workspaces: Callable[[str], Workspace], row: dict) -> None:
    if row["id"] == "fixture-missing":
        pytest.fail(f"parity fixture is missing: {FIXTURE}")
    ws = workspaces(row["workspace"])
    canonical_before = native(ws.w, "read", {}) if row["caller"] in {
        "task_lease_renew", "task_lease_transfer", "task_lease_release",
    } or (row["caller"] == "task_lease_acquire" and row["fence_state"] == "engaged") else None
    observed = observe_row(ws, row)
    assert observed["exit"] == row["exit"], observed
    if row.get("match") == "subset":
        assert {key: observed["envelope"].get(key) for key in row["expect"]} == row["expect"], observed
    else:
        assert observed["envelope"] == row["expect"], observed
    if row["caller"] == "handoff_mode_set":
        # This command now crosses the canonical boundary. Its dynamic revision,
        # operation ID and lease expiry are not a literal legacy-writer envelope.
        assert observed["envelope"].get("claimed_todos") or observed["envelope"].get("active_leases"), observed
        assert observed["envelope"].get("provider_revision"), observed
    if canonical_before is not None:
        # Public canonical callers change the provider; legacy wire rows stay fenced.
        if row["caller"] == "task_lease_acquire":
            assert_canonical_acquisition(ws, canonical_before, observed)
        else:
            assert_canonical_lease_transition(ws, row["caller"], canonical_before, observed)
    assert observed["effect"] == row["effect"], observed
    assert observed["outbox_added"] == row.get("outbox_added", []), observed


def test_baseline_annotations_are_not_stale() -> None:
    for row in load_rows():
        for revision, delta in (row.get("baseline") or {}).items():
            if revision == "note":
                continue
            assert delta != row["expect"], row["id"]
