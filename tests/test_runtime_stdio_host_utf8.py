"""The shipped stdio hosts LoopX starts must pin their own streams to UTF-8.

`sys.stdin` and `sys.stdout` default to the host locale codec - `cp936` on a
zh-CN Windows host - while every LoopX caller writes and reads these transports
as UTF-8. On such a host each entry below decodes its caller's request with
`gbk`, and the outcome depends on whether the request's bytes pair up: a
multi-byte sequence with no partner raises `UnicodeDecodeError`, and one that
pairs decodes *without any error* into different text. `gbk` is a two-byte
codec, so a lone lead byte consumes the byte after it - a path separator or a
JSON escape introducer simply disappears.

That silent half is why this is not only a crash. `shadow_lock_host` answers
`held` while locking a differently named directory, `research_snapshot_host`
reads a different runtime root and still returns a complete snapshot for it, and
`scan_cli --diff` - the documented fail-closed public-boundary gate - reports
`PASS: public-safe` for a diff that does contain a private absolute path.

#6470. `test_cli_stdio_utf8.py`, `test_claude_goal_mode_stdio_utf8.py`,
`test_dsh_goal_mode_stdio_utf8.py` and
`tests/extensions/test_extension_provider_stdio_utf8.py` guard the other
entries of the same family.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The codec an unpinned host falls back to on a zh-CN Windows console.
LOCALE_CODEC = "gbk"

# A name whose UTF-8 bytes are odd in number, so the last byte is a lead byte
# with no partner and `gbk` consumes the byte after it. What that byte was
# decides whether the request is rejected or silently rewritten.
ODD_BYTES_NAME = "\u9ec4\u6c5f\u5357"
# A name whose UTF-8 bytes pair up completely. `gbk` decodes it into different
# text without reporting anything, so only the addresses it reaches show it.
EVEN_BYTES_NAME = "\u6d4b\u8bd5"


# The NoKV helper is launched as a file, not as a module: the TS transport uses
# `[python, <this file>]` and the live qualification probe uses
# `[python, "-I", <this file>]`, so it must stay runnable without package context.
NOKV_HELPER = (
    REPO_ROOT / "loopx" / "control_plane" / "coordination" / "nokv_jsonl_helper.py"
)


def _run_argv(
    argv: list[str], stdin: bytes = b"", *, cwd: Path = REPO_ROOT
) -> subprocess.CompletedProcess[bytes]:
    environment = {**os.environ, "PYTHONIOENCODING": LOCALE_CODEC, "PYTHONUTF8": "0"}
    return subprocess.run(
        argv,
        cwd=cwd,
        env=environment,
        input=stdin,
        capture_output=True,
        timeout=180,
    )


def _run(
    module: str, *argv: str, stdin: bytes = b""
) -> subprocess.CompletedProcess[bytes]:
    return _run_argv([sys.executable, "-m", module, *argv], stdin)


def _request_bytes(payload: dict | list) -> bytes:
    """Raw UTF-8 request bytes that keep the non-ASCII path literal, not escaped."""

    raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    assert any(ord(character) > 127 for character in raw.decode("utf-8"))
    return raw


@pytest.fixture(scope="module")
def sdk_only_python(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A standard-library interpreter that resolves the NoKV SDK and not LoopX.

    This is the interpreter shape `examples/nokv-authority-store/README.md`
    documents for the live qualification probe: one that resolves the qualified
    NoKV SDK, with no requirement that LoopX be installed into it. `venv` with
    `--without-pip` builds it from the standard library alone, so nothing here
    installs, downloads, or reaches the network.
    """

    root = tmp_path_factory.mktemp("sdk-only") / uuid.uuid4().hex
    created = subprocess.run(
        [sys.executable, "-I", "-m", "venv", "--without-pip", str(root)],
        capture_output=True,
        timeout=300,
    )
    python = (
        root
        / ("Scripts" if os.name == "nt" else "bin")
        / ("python.exe" if os.name == "nt" else "python")
    )
    assert created.returncode == 0, created.stderr.decode("utf-8", "replace")
    assert python.is_file()
    return str(python)


@pytest.mark.parametrize("name", [ODD_BYTES_NAME, EVEN_BYTES_NAME])
def test_shadow_lock_host_locks_the_requested_directory(
    tmp_path: Path, name: str
) -> None:
    """The lock must land on the path the caller named instead of a re-encoded one."""

    root = tmp_path / f"Users-{name}"
    (root / "runtime").mkdir(parents=True)
    lock = root / "runtime" / "authority.lock"

    result = _run(
        "loopx.control_plane.coordination.shadow_lock_host",
        stdin=_request_bytes([str(lock)]) + b"release\n",
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.split() == [b"held", b"released"]
    # A locale decode consumes the separator inside the path, so the lock would
    # land on a differently named sibling and leave the requested path empty.
    assert sorted(path.name for path in tmp_path.iterdir()) == [root.name]


@pytest.mark.parametrize("name", [ODD_BYTES_NAME, EVEN_BYTES_NAME])
def test_research_snapshot_host_reads_the_requested_runtime_root(
    tmp_path: Path, name: str
) -> None:
    """The snapshot must be computed against the runtime root the caller named."""

    root = tmp_path / f"Users-{name}"
    root.mkdir()

    result = _run(
        "loopx.capabilities.explore.research_snapshot_host",
        stdin=_request_bytes(
            {
                "runtime_root": str(root),
                "goal_id": "g1",
                "harness": {},
                "agent_id": "a1",
                "todos": [],
            }
        ),
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert json.loads(result.stdout.decode("utf-8"))["schema_version"] == (
        "research_graph_snapshot_v0"
    )
    # A locale decode reads a different (here: previously nonexistent) runtime
    # root, takes its graph lock there, and still returns a complete snapshot.
    assert (root / "goals").is_dir()
    assert sorted(path.name for path in tmp_path.iterdir()) == [root.name]


@pytest.mark.parametrize("prefix", [(), ("-I",)])
def test_nokv_jsonl_helper_reads_the_utf8_open_handshake(
    tmp_path: Path, prefix: tuple[str, ...]
) -> None:
    """A locale decode rewrites the handshake, so the caller's request id is lost.

    Both of the helper's real launch modes are exercised: the TS transport's
    `[python, <file>]` and the qualification probe's `[python, "-I", <file>]`.
    """

    workbench = tmp_path / f"Users-{ODD_BYTES_NAME}" / "LoopX"
    result = _run_argv(
        [sys.executable, *prefix, str(NOKV_HELPER)],
        _request_bytes(
            {
                "request_id": "r1",
                "operation": "open",
                "config": {
                    "root_id": "a" * 32,
                    "workbench_root": str(workbench),
                    "routing": {
                        "kind": "static",
                        "endpoint": "127.0.0.1:1",
                        "logical_shard_id": "0",
                        "object_namespace_id": "0",
                        "placement_generation": 1,
                        "owner_epoch": 1,
                    },
                    "object_store": {"kind": "memory"},
                },
            }
        ),
    )

    assert result.stdout, result.stderr.decode("utf-8", "replace")
    # Consuming the escape introducer leaves invalid JSON, so an unpinned helper
    # answers `invalid_json` with a null request id instead of reading the open.
    assert json.loads(result.stdout.decode("utf-8"))["request_id"] == "r1"


@pytest.mark.parametrize("prefix", [(), ("-I",)])
def test_nokv_jsonl_helper_starts_without_loopx_on_the_interpreter_path(
    sdk_only_python: str, tmp_path: Path, prefix: tuple[str, ...]
) -> None:
    """The helper must reach its handshake where LoopX itself is not importable.

    `examples/nokv-authority-store/README.md` gives the live qualification probe
    only the absolute path to a Python executable that resolves the qualified
    NoKV SDK, and the probe then fixes `-I` in front of this helper. That
    interpreter is not asked to install LoopX, and `-I` drops this file's
    directory from `sys.path`, so the helper has to bootstrap its own checkout
    instead of requiring an installed package. Both real launch modes are
    exercised: the TS transport's `[python, <file>]` and the probe's
    `[python, "-I", <file>]`.
    """

    # Ground truth, not an assumption about the fixture: without the helper's
    # bootstrap there is no route from this interpreter to LoopX at all.
    probe = subprocess.run(
        [sdk_only_python, *prefix, "-c", "import loopx"],
        cwd=tmp_path,
        capture_output=True,
        timeout=180,
    )
    assert probe.returncode != 0, probe.stdout.decode("utf-8", "replace")
    assert b"ModuleNotFoundError" in probe.stderr, probe.stderr.decode(
        "utf-8", "replace"
    )

    result = subprocess.run(
        [sdk_only_python, *prefix, str(NOKV_HELPER)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONIOENCODING": LOCALE_CODEC, "PYTHONUTF8": "0"},
        input=_request_bytes(
            {
                "request_id": "r1",
                "operation": "open",
                "config": {
                    "routing": {},
                    "workbench_root": str(tmp_path / EVEN_BYTES_NAME),
                },
            }
        ),
        capture_output=True,
        timeout=180,
    )

    # An import that fails before `main()` writes nothing at all, and the caller
    # sees a disconnected helper instead of a typed refusal.
    assert result.stdout, result.stderr.decode("utf-8", "replace")
    refusal = json.loads(result.stdout.decode("utf-8"))
    assert refusal["status"] == "failed"
    assert refusal["request_id"] == "r1"
    assert refusal["reason_code"] == "invalid_config"


def test_scan_cli_diff_keeps_a_private_path_behind_a_non_ascii_name() -> None:
    """The boundary gate must not report clean for a diff it silently re-encoded."""

    diff = f"diff --git a/x b/x\n+++ b/x\n+ {ODD_BYTES_NAME}\\Users\\alice\\x\n".encode(
        "utf-8"
    )

    result = _run(
        "loopx.capabilities.public_safe_outbound.scan_cli",
        "--diff",
        "--format",
        "text",
        stdin=diff,
    )

    assert result.returncode == 1, result.stderr.decode("utf-8", "replace")
    assert b"private_abs_path" in result.stdout
    assert b"FAIL: hits found" in result.stdout


def test_scan_cli_scan_root_writes_a_utf8_receipt(tmp_path: Path) -> None:
    """A non-ASCII hit location must reach the caller as UTF-8, not locale bytes."""

    scanned = tmp_path / f"{EVEN_BYTES_NAME}.md"
    scanned.write_text("see /home/alice/secret\n", encoding="utf-8")

    result = _run(
        "loopx.capabilities.public_safe_outbound.scan_cli",
        "--scan-root",
        str(tmp_path),
        "--format",
        "json",
    )

    assert result.returncode == 1, result.stderr.decode("utf-8", "replace")
    receipt = json.loads(result.stdout.decode("utf-8"))
    assert [hit["location"] for hit in receipt["hits"]] == [scanned.name]


@pytest.mark.parametrize("name", [ODD_BYTES_NAME, EVEN_BYTES_NAME])
def test_delegation_preview_worker_reads_the_binding_request(
    tmp_path: Path, name: str
) -> None:
    """The worker must accept the workspace its caller named, not a re-encoded one.

    `DelegationPreviewTransport` starts this worker with `[python, -P, -m]`
    and writes each framed request as UTF-8, while the worker - unlike the four
    hosts above - read its stdin through the host locale codec. A locale decode
    makes the request's `--project` resolve to another path, so the worker's own
    bound-work check rejects the request before the CLI ever runs.
    """

    workspace = tmp_path / f"Users-{name}" / "LoopX"
    workspace.mkdir(parents=True)
    registry = tmp_path / "registry.json"
    registry.write_text("{}", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    result = _run_argv(
        [
            sys.executable,
            "-P",
            "-m",
            "loopx.control_plane.collaboration.delegation_preview_worker",
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime),
            "--goal-id",
            "goal",
            "--agent-id",
            "agent",
            "--todo-id",
            "todo",
        ],
        _request_bytes(
            {
                "id": 1,
                "argv": [
                    "turn",
                    "run-once",
                    "--goal-id",
                    "goal",
                    "--agent-id",
                    "agent",
                    "--todo-id",
                    "todo",
                    "--project",
                    str(workspace),
                ],
            }
        ),
        cwd=workspace,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    # An unpinned worker decodes the frame into different text and exits with
    # the retarget refusal instead of answering the caller's request.
    preview = json.loads(result.stdout.decode("utf-8"))
    assert preview["kind"] == "preview" and preview["id"] == 1
    assert preview["value"]["mode"] == "run_once"
