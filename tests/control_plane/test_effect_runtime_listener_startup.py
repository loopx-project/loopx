from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from loopx.control_plane import effect_runtime


@pytest.mark.parametrize(
    ("node_code", "diagnostic_code", "launch_count"),
    [
        ("EPERM", "runtime_host_permission_denied", 1),
        ("EACCES", "runtime_host_permission_denied", 1),
        ("EMFILE", "io_process_descriptor_limit", 2),
        ("EADDRINUSE", "io_failure", 2),
    ],
)
def test_listener_failure_preserves_safe_diagnostic_and_retry_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    node_code: str,
    diagnostic_code: str,
    launch_count: int,
) -> None:
    for variable in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(variable, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    launches = tmp_path / "launches.txt"
    hook = tmp_path / "denied-listener.mjs"
    # Inject only the network failure; the real daemon and Python bridge run.
    hook.write_text(
        'import { appendFileSync } from "node:fs";\n'
        'import { Server } from "node:net";\n'
        'Server.prototype.listen = function () {\n'
        f'  appendFileSync({json.dumps(str(launches))}, "attempt\\n");\n'
        '  process.nextTick(() => this.emit("error", Object.assign(\n'
        '    new Error("private-listener-fixture"),\n'
        f'    {{ code: {json.dumps(node_code)}, syscall: "listen" }})));\n'
        '  return this;\n'
        '};\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("NODE_OPTIONS", f"--import={hook.as_uri()}")

    with pytest.raises(effect_runtime.EffectRuntimeStartupError) as raised:
        effect_runtime.effect_runtime_result("runtime.ping", {})

    assert raised.value.diagnostic_code == diagnostic_code
    assert isinstance(raised.value, effect_runtime.EffectRuntimeHostPermissionError) == (
        diagnostic_code == "runtime_host_permission_denied"
    )
    assert "private-listener-fixture" not in str(raised.value)
    assert str(tmp_path) not in str(raised.value)
    assert launches.read_text(encoding="utf-8").splitlines() == ["attempt"] * launch_count
    runtime_dir = effect_runtime._runtime_dir()
    assert not list(runtime_dir.glob("runtime-*.json"))
    assert not list(runtime_dir.glob("start-*.lock"))
