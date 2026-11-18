#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "loopx" / "cli.py"
MODULE = ROOT / "loopx" / "cli_commands" / "worker_bridge.py"
INIT = ROOT / "loopx" / "cli_commands" / "__init__.py"
sys.path.insert(0, str(ROOT))

from loopx.worker_bridge import build_worker_bridge_install_contract  # noqa: E402


def run_cli(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", *args],
        input=stdin,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_success(result: subprocess.CompletedProcess[str]) -> str:
    if result.returncode != 0:
        raise AssertionError(
            f"expected success, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )
    return result.stdout


def main() -> None:
    cli_source = CLI.read_text(encoding="utf-8")
    module_source = MODULE.read_text(encoding="utf-8")
    init_source = INIT.read_text(encoding="utf-8")

    forbidden_cli_markers = [
        "worker_bridge_parser = sub.add_parser",
        "worker_bridge_sub = worker_bridge_parser.add_subparsers",
        "build_worker_bridge_install_contract(",
        "observe_active_user_intervention_feed(",
        'if args.command == "worker-bridge":',
    ]
    for marker in forbidden_cli_markers:
        require(marker not in cli_source, f"worker-bridge marker leaked into cli.py: {marker}")

    for marker in (
        "register_worker_bridge_commands",
        "handle_worker_bridge_command",
        "WORKER_BRIDGE_COMMANDS",
        "append_worker_bridge_counter_trace_row(",
    ):
        require(marker in module_source, f"worker-bridge command module missing {marker}")
    require("register_worker_bridge_commands" in init_source, "__init__ did not export worker bridge registration")
    require("handle_worker_bridge_command" in init_source, "__init__ did not export worker bridge handler")

    help_text = require_success(run_cli("worker-bridge", "active-user-observe", "--help"))
    for option in ("--feed-jsonl", "--observation-json", "--counter-trace-json"):
        require(option in help_text, f"active-user-observe help omitted {option}")

    contract_payload = json.loads(
        require_success(run_cli("worker-bridge", "contract", "--format", "json"))
    )
    require(contract_payload.get("ok") is True, "worker bridge contract should be ok")
    require(
        contract_payload.get("schema_version")
        == "loopx_worker_bridge_install_contract_v0",
        "contract schema changed",
    )
    require(
        contract_payload["bridge_surface"] == "loopx_worker_bridge_source_mount_v0",
        "bridge surface changed",
    )
    require(contract_payload["install_mode"] == "source_mount_read_only_pythonpath", "install mode changed")
    require(
        contract_payload["runtime_policy"] == "ensure_python3_before_worker_cli_bridge",
        "runtime policy changed",
    )
    require("apt-get install -y python3" in contract_payload["runtime_preflight_command"], "Python preflight missing")
    require("importlib.import_module" in contract_payload["runtime_preflight_command"], "module preflight missing")
    require(
        contract_payload["mounts"] == [
            {
                "read_only": True, "source": "<loopx-project-root>",
                "target": "<loopx-project-root>", "type": "bind",
            },
            {
                "read_only": True, "source": "<loopx-runtime-root>",
                "target": "<loopx-runtime-root>", "type": "bind",
            },
        ],
        "default mounts must be read-only",
    )
    require(contract_payload["boundary"]["no_upload"] is True, "contract must not upload")
    require(
        contract_payload["boundary"]["credential_values_recorded"] is False,
        "contract must not record credentials",
    )
    agent_kwargs = contract_payload["agent_kwargs"]
    require(
        agent_kwargs["loopx_command_prefix"] == "PYTHONPATH='<loopx-project-root>' python3 -m loopx.cli",
        "worker CLI prefix changed",
    )
    require(agent_kwargs["loopx_registry_arg"] == "<loopx-runtime-root>/registry.global.json", "registry path changed")
    require(contract_payload["trace"]["raw_trace_public"] is False, "raw trace must stay private")

    writable_contract = build_worker_bridge_install_contract(active_user_host_dir="<active-user-host-dir>")
    require(
        writable_contract["mounts"][-1]
        == {
            "read_only": False, "source": "<active-user-host-dir>",
            "target": "/loopx-active-user", "type": "bind",
        },
        "active-user feed mount changed",
    )
    require(
        writable_contract["active_user_external_update_mount"]["raw_host_path_recorded"] is False,
        "host path must not be recorded",
    )
    require(
        writable_contract["agent_kwargs"]["loopx_active_user_feed_jsonl"]
        == "/loopx-active-user/loopx-active-user-interventions.jsonl",
        "active-user feed path changed",
    )
    channel_boundary = writable_contract["active_user_intervention_channel_contract"]["claim_boundary"]
    require(channel_boundary["direct_codex_chat_injection"] is False, "direct chat injection must be disabled")
    require(channel_boundary["worker_pull_required"] is True, "worker pull must be required")

    with tempfile.TemporaryDirectory(prefix="loopx-worker-bridge-cli-") as tmp:
        root = Path(tmp)
        before = require_success(
            run_cli(
                "worker-bridge",
                "active-user-intervention",
                "--seq",
                "0",
                "--message",
                "before worker start",
                "--before-worker-start",
                "--jsonl",
            )
        ).strip()
        after = require_success(
            run_cli(
                "worker-bridge",
                "active-user-intervention",
                "--seq",
                "2",
                "--message",
                "after worker start",
                "--jsonl",
            )
        ).strip()
        feed_path = root / "feed.jsonl"
        observation_path = root / "observation.json"
        counter_trace_path = root / "counter-trace.jsonl"
        feed_path.write_text(before + "\n" + after + "\n", encoding="utf-8")

        observe_payload = json.loads(
            require_success(
                run_cli(
                    "worker-bridge",
                    "active-user-observe",
                    "--feed-jsonl",
                    str(feed_path),
                    "--worker-start-seq",
                    "1",
                    "--observation-json",
                    str(observation_path),
                    "--counter-trace-json",
                    str(counter_trace_path),
                    "--format",
                    "json",
                )
            )
        )
        require(observe_payload.get("ok") is True, "active-user-observe should succeed")
        require(observe_payload.get("observation_written") is True, "observation writeback missing")
        require(observe_payload.get("counter_trace_written") is True, "counter trace writeback missing")

    print("cli-worker-bridge-command-modularization-smoke: ok")


if __name__ == "__main__":
    main()
