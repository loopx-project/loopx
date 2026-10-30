"""Recover a worker's captured decision without replaying optional material."""

import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

from benchmark.runtime.context_delivery import preflight_context_instruction


def test_projection_failure_keeps_original_capture_selectively_readable(tmp_path, monkeypatch):
    capture = tmp_path / "captured decision.json"
    source = {
        "ok": True,
        "goal_id": "synthetic-recovery",
        "agent_id": "synthetic-worker",
        "agent_identity": {"agent_id": "synthetic-worker"},
        "heartbeat_receipt": {"turn_instance_id": "synthetic-turn"},
        "work": {"criteria": ["Preserve the original decision identity"]},
        "optional_evidence": "supporting material " * 12000,
    }
    raw = json.dumps(source).encode()
    capture.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    # Keep every invoked CLI, including its returned command routes, on this
    # checkout and a disposable runtime rather than the operator's Goal.
    cli = tmp_path / "isolated reader"
    repository = Path(__file__).resolve().parents[2]
    isolated_args = [
        "--registry", str(tmp_path / "registry.json"),
        "--runtime-root", str(tmp_path / "runtime"),
    ]
    cli.write_text(
        f"#!{sys.executable}\n"
        f"import sys\nsys.path.insert(0, {str(repository)!r})\n"
        "from pathlib import Path\nimport loopx\n"
        f"assert Path(loopx.__file__).resolve().parents[1] == Path({str(repository)!r})\n"
        "from loopx.cli import main\n"
        f"sys.argv[1:1] = {isolated_args!r}\n"
        "raise SystemExit(main())\n"
    )
    cli.chmod(0o700)
    unusable_context = tmp_path / "unavailable-context"
    unusable_context.write_text("Not a native context directory")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "on")
    monkeypatch.setenv("LOOPX_EXECUTION_MODE", "heartbeat")
    instruction = preflight_context_instruction(
        {
            "LOOPX_CLI": str(cli),
            "LOOPX_GOAL_ID": source["goal_id"],
            "LOOPX_AGENT_ID": source["agent_id"],
            "LOOPX_CODEX_CONTEXT_DIR": str(unusable_context),
        },
        capture,
        digest,
        "synthetic-turn",
    )
    commands = [
        shlex.split(command)
        for command in re.findall(r"```sh\n(.*?)\n```", instruction, re.S)
    ]
    assert len(commands) == 2

    def invoke(argv):
        return subprocess.run(
            argv, text=True, capture_output=True, timeout=30,
            cwd=Path(__file__).resolve().parents[2],
        )

    # A refused presentation must not force a full replay or become new
    # authority. Its original hash-bound reader remains the recovery surface.
    refused = invoke(commands[0])
    assert refused.returncode != 0
    refused_payload = json.loads(refused.stdout)
    assert refused_payload["ok"] is False
    assert refused_payload["error_code"] == "quota_decision_transport_failed"
    assert "--list" in shlex.split(refused_payload["recovery"]["command"])
    assert "Choose the sections needed for the current decision" in refused_payload["instruction"]
    assert "do not drain unrelated optional material" in refused_payload["instruction"]
    assert "Follow next_command through eof" not in refused_payload["instruction"]

    recovery = shlex.split(refused_payload["recovery"]["command"])
    discovery = invoke(recovery)
    assert discovery.returncode == 0, discovery.stderr
    index = json.loads(discovery.stdout)
    assert index["ok"] and index["sha256"] == digest and index["eof"]
    assert json.loads(index["content"])["schema_version"] == "context_capture_index_v1"
    assert len(discovery.stdout.encode()) < len(raw) // 5

    reader = recovery[:recovery.index("context-read") + 1]
    branch = invoke([
        *reader, "--capture-file", str(capture), "--capture-sha256", digest,
        "--list", "--select", "/work",
    ])
    assert branch.returncode == 0, branch.stderr
    assert "/work/criteria" in branch.stdout

    # Read the relevant criteria directly; no traversal of the large optional
    # evidence field is needed before the decision that depends on them.
    selected = invoke([
        *reader, "--capture-file", str(capture), "--capture-sha256", digest,
        "--select", "/work/criteria",
    ])
    assert selected.returncode == 0, selected.stderr
    page = json.loads(selected.stdout)
    assert page["sha256"] == digest and page["eof"]
    values = json.loads(page["content"])["selections"]
    assert values == [{"pointer": "/work/criteria", "value": source["work"]["criteria"]}]
    assert capture.read_bytes() == raw

    forged = invoke([
        *reader, "--capture-file", str(capture), "--capture-sha256", "0" * 64,
        "--select", "/work/criteria",
    ])
    assert forged.returncode != 0
    assert "Neither presentation nor recovery renews" in instruction
    assert "Follow every next_command." not in instruction
