"""Complete checkpoint witnesses cross the existing private Effect transport."""
import hashlib
import json
import time

import pytest

from loopx.cli import main
from loopx.control_plane.effect_runtime import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    EffectRuntimeRemoteError,
    EffectRuntimeResponseAmbiguous,
    effect_runtime_result,
)


# This is a complete-transport functional check, not a storage latency SLO.
# A complete inventory performs durable per-file copy/flush work; keep its
# finite RPC allowance separate from artifact deadlines
# or the benchmark's separate live-capture/sealing clocks.
LARGE_ARTIFACT_RPC_SECONDS = 240


def test_large_member_transport_with_explicit_rpc_budget_preserves_inventory(tmp_path, capsys):
    source = tmp_path / "research"
    source.mkdir(mode=0o700)
    content = b"retained negative evidence\n"
    expected = [{"path": "", "kind": "directory", "mode": 0o700}]
    for index in range(8192):
        name = f"{index:05}-" + "e" * 150 + ".txt"
        path = source / name
        path.write_bytes(content)
        path.chmod(0o600)
        expected.append({"path": name, "kind": "file", "mode": 0o600,
                         "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    witness = {"research": expected}
    # The expected inventory is independent of the production walker. A count
    # alone would miss dropped, reordered or substituted scientific members.
    assert len(json.dumps(witness, separators=(",", ":")).encode()) > MAX_RESPONSE_BYTES
    components = [{"id": "research", "source_path": str(source)}]
    request = {"action": "probe", "components": components}
    with pytest.raises(EffectRuntimeResponseAmbiguous):
        effect_runtime_result("runtime.checkpoint_artifacts", request, timeout=LARGE_ARTIFACT_RPC_SECONDS)
    observed = effect_runtime_result("runtime.checkpoint_artifacts", request,
                                     timeout=LARGE_ARTIFACT_RPC_SECONDS, large_local_snapshot=True)
    assert observed["source_witness"] == witness
    unit = tmp_path / "point"
    captured = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "capture", "components": components, "destination": str(unit),
        "point_metadata": {}, "expected_source_sha256": observed["source_sha256"],
    }, timeout=LARGE_ARTIFACT_RPC_SECONDS, retry_safe=False, large_local_snapshot=True)
    manifest = json.loads((unit / "checkpoint.json").read_bytes())
    assert manifest["source_witness"] == witness and manifest["artifact_witness"] == witness
    assert captured["source_stability_verified"] and not captured["execution_authority_granted"]
    assert main(["--format", "json", "research-checkpoint", "verify", "--root", str(unit),
                 "--expected-sha256", captured["sha256"]]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["integrity_verified"] and not verified["research_point_qualified"]
    destination = tmp_path / "receiver"
    restored = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "materialize", "root": str(unit), "expected_sha256": captured["sha256"],
        "destination": str(destination),
    }, timeout=LARGE_ARTIFACT_RPC_SECONDS, retry_safe=False, large_local_snapshot=True)
    assert restored["materialized"] and not restored["activation_performed"]
    assert (destination / "checkpoint.json").read_bytes() == (unit / "checkpoint.json").read_bytes()
    assert sorted(p.name for p in (destination / "components/research").iterdir()) == [
        row["path"] for row in expected[1:]]
    member = destination / "components/research" / expected[-1]["path"]
    assert member.read_bytes() == content
    member.unlink()
    with pytest.raises(EffectRuntimeRemoteError):
        effect_runtime_result("runtime.checkpoint_artifacts", {"action": "verify", "root": str(destination),
            "expected_sha256": captured["sha256"]}, timeout=LARGE_ARTIFACT_RPC_SECONDS, large_local_snapshot=True)


def test_large_checkpoint_metadata_uses_private_request_and_response(tmp_path):
    source = tmp_path / "candidate"
    source.write_bytes(b"candidate")
    components = [{"id": "code", "source_path": str(source)}]
    observed = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "probe", "components": components}, large_local_snapshot=True)
    request = {"action": "capture", "components": components, "destination": str(tmp_path / "point"),
        "point_metadata": {"retained_basis": "x" * (MAX_REQUEST_BYTES + 1)},
        "expected_source_sha256": observed["source_sha256"]}
    result = effect_runtime_result("runtime.checkpoint_artifacts", request,
        timeout=30, retry_safe=False, large_local_snapshot=True)
    basis = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "read_basis", "root": request["destination"], "expected_sha256": result["sha256"]},
        timeout=30, large_local_snapshot=True)
    assert basis["point_metadata"] == request["point_metadata"]
    assert result["source_stability_verified"] and not result["execution_authority_granted"]


def test_large_rpc_allowance_cannot_extend_artifact_capture_deadline(tmp_path):
    source = tmp_path / "candidate"
    source.write_bytes(b"retained evidence")
    components = [{"id": "code", "source_path": str(source)}]
    observed = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "probe", "components": components}, large_local_snapshot=True)
    # The same valid request succeeds with a future artifact deadline. Do not
    # infer the rejection cause from internal error prose hidden by the bridge.
    live = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "capture", "components": components,
        "destination": str(tmp_path / "live"), "point_metadata": {},
        "expected_source_sha256": observed["source_sha256"],
        "deadline_epoch_ms": (time.time() + LARGE_ARTIFACT_RPC_SECONDS) * 1000,
    }, timeout=LARGE_ARTIFACT_RPC_SECONDS, retry_safe=False, large_local_snapshot=True)
    assert live["source_stability_verified"]
    unit = tmp_path / "expired"
    with pytest.raises(EffectRuntimeRemoteError):
        effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "capture", "components": components,
            "destination": str(unit), "point_metadata": {},
            "expected_source_sha256": observed["source_sha256"], "deadline_epoch_ms": 0,
        }, timeout=LARGE_ARTIFACT_RPC_SECONDS, retry_safe=False, large_local_snapshot=True)
    assert not unit.exists()
