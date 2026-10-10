#!/usr/bin/env python3
"""Smoke for the thin TraeX Turn host adapter.

Guards the reusable request/result translation without calling the model or
network: signed authority extraction from the bounded Turn envelope, native
structured-result file parsing, result shaping, process-group timeout cleanup,
and acceptance by the real LoopX host-result validator.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
for path in (str(REPO_ROOT), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import traex_turn_host_adapter as adapter  # noqa: E402
from loopx.control_plane.turn_driver.executor import (  # noqa: E402
    validate_loopx_turn_host_result,
)
from loopx.control_plane.quota.turn_envelope import (  # noqa: E402
    turn_envelope_action_signature_document,
)


TURN_KEY = (
    "sha256:" + "0" * 64
)


def _request(
    *,
    recommended_action: str = "Do the small thing.",
    primary_action: str = "Do the signed thing.",
) -> dict:
    request = {
        "schema_version": adapter.LOOPX_TURN_HOST_REQUEST_SCHEMA,
        "turn_key": TURN_KEY,
        "route": "primary_delivery",
        "session": {"goal_id": "g", "agent_id": "a"},
        "turn_envelope": {
            "schema_version": "loopx_turn_envelope_v0",
            "goal_id": "g",
            "agent_id": "a",
            "action": {
                "recommended_action": recommended_action,
                "primary_action": primary_action,
                "must_attempt": True,
            },
            "required_reads": [
                {
                    "kind": "command",
                    "command": "git status --short",
                    "reason": "establish the workspace baseline",
                }
            ],
            "boundary": {
                "write_scope": ["docs/**", "tests/**"],
                "workspace_guard": {
                    "schema_version": "workspace_guard_v0",
                    "status": "ready",
                    "action": "continue",
                },
            },
            "action_signature": {
                "schema_version": "loopx_action_signature_v0",
                "matches": True,
            },
        },
        "result_contract": {
            "schema_version": adapter.LOOPX_TURN_RESULT_SCHEMA,
            "completed_phases": list(adapter.COMPLETED_PHASES),
        },
    }
    signature = turn_envelope_action_signature_document(request["turn_envelope"])
    signature_hash = "sha256:" + sha256(
        json.dumps(
            signature,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    request["turn_envelope"]["action_signature"].update(
        {
            "source_hash": signature_hash,
            "envelope_hash": signature_hash,
        }
    )
    return request


def _plan(request: dict) -> dict:
    return {
        "transaction": {"turn_key": request["turn_key"]},
        "turn_envelope": request["turn_envelope"],
    }


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_prompt_preserves_structured_turn_authority() -> None:
    request = _request(recommended_action="legacy action must not appear")
    authority = adapter.extract_turn_authority(request)
    prompt = adapter.render_prompt(authority)
    _assert(
        authority["primary_action"] == "Do the signed thing.",
        "primary_action must be preserved",
    )
    _assert(
        authority["required_reads"] == request["turn_envelope"]["required_reads"],
        "required_reads must be preserved without prose reconstruction",
    )
    _assert(
        authority["write_scope"] == ["docs/**", "tests/**"],
        "the complete write scope must be preserved",
    )
    _assert(
        authority["workspace_guard"]
        == request["turn_envelope"]["boundary"]["workspace_guard"],
        "the complete workspace guard must be preserved",
    )
    _assert("legacy action must not appear" not in prompt, "legacy action must not leak")
    for expected in (
        '"primary_action":"Do the signed thing."',
        '"command":"git status --short"',
        '"write_scope":["docs/**","tests/**"]',
        '"workspace_guard":{"action":"continue"',
        "- recommended_action:",
        "- vision_unchanged_reason:",
    ):
        _assert(expected in prompt, f"prompt must contain structured authority: {expected}")


def test_structured_result_file_is_the_only_candidate_channel() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "result.json"
        candidate = {
            "result_kind": "validated_progress",
            "classification": "updated config",
            "recommended_action": "review",
            "next_action": "run the smoke",
            "vision_unchanged_reason": "the objective is unchanged",
            "summary": "changed one field",
        }
        path.write_text(json.dumps(candidate), encoding="utf-8")
        _assert(
            adapter.read_structured_result(path) == candidate,
            "the native structured result file must parse",
        )
        path.write_text("{not json}", encoding="utf-8")
        _assert(
            adapter.read_structured_result(path) is None,
            "invalid structured output must fail closed",
        )


def test_material_progress_result_validates() -> None:
    request = _request()
    candidate = {
        "result_kind": "validated_progress",
        "classification": "single surface change",
        "summary": "edited one file",
        "next_action": "review the diff",
    }
    result = adapter.build_result(request, candidate)
    verdict = validate_loopx_turn_host_result(_plan(request), result)
    _assert(verdict["ok"], "material result must validate: " + "; ".join(verdict["errors"]))


def test_wait_result_validates_without_material_fields() -> None:
    request = _request()
    result = adapter.build_result(
        request,
        {
            "result_kind": "wait",
            "classification": "throttled",
            "next_action": "retry after cadence",
        },
    )
    verdict = validate_loopx_turn_host_result(_plan(request), result)
    _assert(verdict["ok"], "wait result must validate: " + "; ".join(verdict["errors"]))
    _assert("delivery_batch_scale" not in result, "stop kinds carry no batch scale")


def test_missing_result_block_fails_closed_to_wait() -> None:
    request = _request()
    result = adapter.build_result(request, None, fallback_reason="no block found")
    _assert(result["result_kind"] == "wait", "missing block must not fabricate progress")
    verdict = validate_loopx_turn_host_result(_plan(request), result)
    _assert(verdict["ok"], "fail-closed wait must validate: " + "; ".join(verdict["errors"]))


def test_text_fields_are_bounded() -> None:
    request = _request()
    candidate = {
        "result_kind": "user_action_required",
        "classification": "x" * 500,
        "summary": "y" * 900,
        "next_action": "z" * 5000,
    }
    result = adapter.build_result(request, candidate)
    _assert(
        len(result["classification"]) <= 120,
        "classification must respect its limit",
    )
    _assert(len(result["summary"]) <= 400, "summary must respect its limit")
    _assert(len(result["next_action"]) <= 1_200, "next_action must respect its limit")


def test_subprocess_adapter_roundtrip_with_fake_host() -> None:
    """End-to-end: request -> native TraeX structured output -> Turn result."""

    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "fake-traex"
        argv_file = Path(tmp) / "argv.json"
        block = json.dumps(
            {
                "result_kind": "validated_progress",
                "classification": "did it",
                "recommended_action": "review",
                "summary": "one change",
                "next_action": "review",
                "vision_unchanged_reason": "the objective is unchanged",
            }
        )
        script = (
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            f"pathlib.Path({str(argv_file)!r}).write_text(json.dumps(sys.argv[1:]))\n"
            "output_path = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
            f"output_path.write_text({block!r}, encoding='utf-8')\n"
            "print('hook: pre-tool')\n"
            "print('INFO transport')\n"
            "print('unstructured stdout must not be the result channel')\n"
        )
        fake.write_text(script)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

        request = _request()
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "traex_turn_host_adapter.py"),
                "--traex-bin",
                str(fake),
            ],
            input=json.dumps(request),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        _assert(completed.returncode == 0, "adapter must exit 0: " + completed.stderr)
        result = json.loads(completed.stdout)
        verdict = validate_loopx_turn_host_result(_plan(request), result)
        _assert(
            verdict["ok"],
            "roundtrip result must validate: " + "; ".join(verdict["errors"]),
        )
        _assert(
            result["result_kind"] == "validated_progress",
            "native structured output must carry the material result",
        )
        argv = json.loads(argv_file.read_text())
        _assert("--output-schema" in argv, "adapter must request native output schema")
        _assert(
            "--output-last-message" in argv,
            "adapter must read the native structured result file",
        )
        _assert(
            "--permission-mode" not in argv,
            "default adapter invocation must use TraeX's headless policy",
        )
        sandbox_index = argv.index("--sandbox")
        _assert(
            argv[sandbox_index + 1] == "workspace-write",
            "default adapter invocation must permit governed workspace writes",
        )


def test_explicit_permission_mode_is_forwarded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "fake-traex"
        argv_file = Path(tmp) / "argv.json"
        block = json.dumps(
            {
                "result_kind": "wait",
                "classification": "permission probe",
                "summary": "No workspace change requested.",
                "recommended_action": "none",
                "next_action": "none",
                "vision_unchanged_reason": "the objective is unchanged",
            }
        )
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            f"pathlib.Path({str(argv_file)!r}).write_text(json.dumps(sys.argv[1:]))\n"
            "output_path = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
            f"output_path.write_text({block!r}, encoding='utf-8')\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "traex_turn_host_adapter.py"),
                "--traex-bin",
                str(fake),
                "--permission-mode",
                "bypass_permissions",
            ],
            input=json.dumps(_request()),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        _assert(completed.returncode == 0, "adapter must exit 0: " + completed.stderr)
        argv = json.loads(argv_file.read_text())
        permission_index = argv.index("--permission-mode")
        _assert(
            argv[permission_index + 1] == "bypass_permissions",
            "explicit permission mode must be forwarded unchanged",
        )


def test_timeout_terminates_traex_descendants() -> None:
    if os.name != "posix":
        return
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        marker = root / "late-descendant-write"
        child_code = (
            "from pathlib import Path; import time; "
            "time.sleep(1.2); "
            f"Path({str(marker)!r}).write_text('late', encoding='utf-8')"
        )
        fake = root / "fake-traex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
            "time.sleep(10)\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

        started = time.monotonic()
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "traex_turn_host_adapter.py"),
                "--traex-bin",
                str(fake),
                "--workspace",
                str(root),
                "--timeout-seconds",
                "1",
            ],
            input=json.dumps(_request()),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
        elapsed = time.monotonic() - started
        _assert(completed.returncode != 0, "timed out TraeX must fail the adapter")
        _assert(elapsed < 4, "adapter timeout must return promptly")
        time.sleep(1.5)
        _assert(not marker.exists(), "timed out TraeX descendants must not write later")


def main() -> int:
    tests = [
        test_prompt_preserves_structured_turn_authority,
        test_structured_result_file_is_the_only_candidate_channel,
        test_material_progress_result_validates,
        test_wait_result_validates_without_material_fields,
        test_missing_result_block_fails_closed_to_wait,
        test_text_fields_are_bounded,
        test_subprocess_adapter_roundtrip_with_fake_host,
        test_explicit_permission_mode_is_forwarded,
        test_timeout_terminates_traex_descendants,
    ]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
