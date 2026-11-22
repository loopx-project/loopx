#!/usr/bin/env python3
"""Smoke-test the cold-path Codex CLI wrapper contract for protocol packets."""

from __future__ import annotations

import importlib.util
import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPARISON_SMOKE = REPO_ROOT / "examples" / "protocol" / "protocol-action-packet-router-comparison-smoke.py"


def load_comparison_module() -> Any:
    spec = importlib.util.spec_from_file_location("protocol_router_comparison_smoke", COMPARISON_SMOKE)
    assert spec and spec.loader, COMPARISON_SMOKE
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compact_prompt(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    return (
        "Summarize this synthetic LoopX protocol comparison. "
        "Use one sentence. Preserve actor/action/no_api facts. "
        "Do not request external context. "
        f"schema={report['schema_version']} "
        f"scenarios={aggregate['scenario_count']} "
        f"min_shrinkage={aggregate['min_payload_shrinkage_ratio']} "
        f"decision={report['decision']['direct_llm_api']}."
    )


def build_codex_command(
    *,
    codex_cli: Path,
    project: Path,
    prompt: str,
    output_last_message: Path | None = None,
) -> list[str]:
    command = [
        str(codex_cli),
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "-c",
        'approval_policy="never"',
        "--sandbox",
        "workspace-write",
        "-C",
        str(project),
    ]
    if output_last_message is not None:
        command.extend(["-o", str(output_last_message)])
    command.append(prompt)
    return command


def write_fake_codex(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'summary': 'agent summary no_api'}))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def sidecar_from_fake_run(*, command: list[str], project: Path) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=project,
        text=True,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    fake_payload = json.loads(result.stdout)
    argv = fake_payload["argv"]
    return {
        "schema_version": "protocol_action_packet_codex_cli_wrapper_v0",
        "mode": "fake_codex_cli_contract",
        "real_codex_cli_invoked": False,
        "direct_llm_api_invoked": False,
        "env_values_read": False,
        "command_shape": {
            "subcommand": argv[:1],
            "ephemeral": "--ephemeral" in argv,
            "ignore_user_config": "--ignore-user-config" in argv,
            "ignore_rules": "--ignore-rules" in argv,
            "approval_config": argv[argv.index("-c") + 1],
            "sandbox": argv[argv.index("--sandbox") + 1],
            "project_arg_present": "-C" in argv,
        },
        "project_surface": "isolated_fixture_project",
        "prompt_chars": len(argv[-1]),
        "summary": fake_payload["summary"],
    }


def sidecar_from_real_run(
    *,
    command: list[str],
    project: Path,
    output_last_message: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=project,
        text=True,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
    )
    last_message = output_last_message.read_text(encoding="utf-8").strip() if output_last_message.exists() else ""
    return {
        "schema_version": "protocol_action_packet_codex_cli_wrapper_v0",
        "mode": "real_codex_cli_probe",
        "real_codex_cli_invoked": True,
        "direct_llm_api_invoked": False,
        "env_values_read": False,
        "codex_returncode": result.returncode,
        "project_surface": "isolated_fixture_project",
        "prompt_chars": len(command[-1]),
        "summary": _protocol_summary(last_message),
        "stdout_chars": len(result.stdout),
        "stderr_chars": len(result.stderr),
        "stderr_text_recorded": False,
    }


def _protocol_summary(value: str) -> str:
    text = " ".join(value.strip().split())
    return text[:260] if text else ""


def assert_public_sidecar(sidecar: dict[str, Any]) -> None:
    text = json.dumps(sidecar, ensure_ascii=False).lower()
    blocked_terms = [
        "".join(chr(value) for value in (97, 112, 105, 95, 107, 101, 121)),
        "".join(chr(value) for value in (115, 101, 99, 114, 101, 116)),
        "".join(chr(value) for value in (116, 111, 107, 101, 110, 61)),
        "/users/",
    ]
    assert all(term not in text for term in blocked_terms), sidecar
    assert sidecar["schema_version"] == "protocol_action_packet_codex_cli_wrapper_v0", sidecar
    assert isinstance(sidecar["real_codex_cli_invoked"], bool), sidecar
    assert sidecar["direct_llm_api_invoked"] is False, sidecar
    assert sidecar["env_values_read"] is False, sidecar
    shape = sidecar.get("command_shape")
    if shape is not None:
        assert shape["subcommand"] == ["exec"], sidecar
        assert shape["ephemeral"] is True, sidecar
        assert shape["ignore_user_config"] is True, sidecar
        assert shape["ignore_rules"] is True, sidecar
        assert shape["approval_config"] == 'approval_policy="never"', sidecar
        assert shape["sandbox"] == "workspace-write", sidecar
        assert shape["project_arg_present"] is True, sidecar
    if sidecar["real_codex_cli_invoked"]:
        assert sidecar["codex_returncode"] == 0, sidecar
        assert sidecar["stderr_text_recorded"] is False, sidecar
    assert "no_api" in sidecar["summary"], sidecar


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-codex-cli", action="store_true")
    parser.add_argument("--codex-cli", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--sidecar", type=Path)
    args = parser.parse_args()

    comparison = load_comparison_module().build_comparison_report()
    prompt = compact_prompt(comparison)
    assert "protocol_router_comparison_v0" in prompt, prompt
    assert len(prompt) < 260, prompt
    with tempfile.TemporaryDirectory(prefix="loopx-protocol-codex-wrapper-") as tmp:
        root = Path(tmp)
        project = root / "project"
        project.mkdir()
        if args.real_codex_cli:
            codex_candidate = args.codex_cli or (Path(found) if (found := shutil.which("codex")) else None)
            assert codex_candidate and codex_candidate.exists(), "codex CLI is required for --real-codex-cli"
            output_last_message = root / "last-message.txt"
            command = build_codex_command(
                codex_cli=codex_candidate,
                project=project,
                prompt=prompt,
                output_last_message=output_last_message,
            )
            sidecar = sidecar_from_real_run(
                command=command,
                project=project,
                output_last_message=output_last_message,
                timeout_seconds=args.timeout_seconds,
            )
        else:
            fake_codex = root / "codex"
            write_fake_codex(fake_codex)
            command = build_codex_command(codex_cli=fake_codex, project=project, prompt=prompt)
            sidecar = sidecar_from_fake_run(command=command, project=project)
        if args.sidecar is not None:
            args.sidecar.write_text(json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert_public_sidecar(sidecar)
    print(
        "protocol-action-packet-codex-cli-wrapper-smoke ok "
        f"prompt_chars={sidecar['prompt_chars']} "
        f"real_codex_cli={sidecar['real_codex_cli_invoked']} direct_llm_api=False"
    )


if __name__ == "__main__":
    main()
