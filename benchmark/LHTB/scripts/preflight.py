#!/usr/bin/env python3
"""Fail-closed preflight for the LHTB LoopX generic_cli heartbeat arm."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import tempfile
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from benchmark.runtime.codex import Execution
from loopx.control_plane.effect_runtime import (
    MINIMUM_NODE_VERSION,
    MINIMUM_NODE_VERSION_TEXT,
)


EXPECTED_SEPARATE = {"langchain-version-migration", "nbody-accel-iterative"}
EXPECTED_AGENT = "benchmark.runtime.harbor:BenchmarkCodex"


def command(argv: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        result = subprocess.run(
            argv,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return result.returncode, (result.stdout or "").strip()


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--lhtb-root", type=Path, required=True)
    parser.add_argument("--loopx-src", type=Path, required=True)
    parser.add_argument("--codex-bin", type=Path, required=True)
    parser.add_argument("--portable-python", type=Path, required=True)
    parser.add_argument("--node-dir", type=Path, required=True)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-task-count", type=int, required=True)
    args = parser.parse_args()

    failures: list[str] = []

    def check(label: str, passed: bool, detail: str) -> None:
        print(f"[{'OK' if passed else 'FAIL'}] {label}: {detail}")
        if not passed:
            failures.append(label)

    lhtb_root = args.lhtb_root.resolve()
    tasks_root = lhtb_root / "upstream" / "tasks"
    task_dirs = sorted(path for path in tasks_root.iterdir() if path.is_dir())
    check("LHTB task count", len(task_dirs) == 46, f"{len(task_dirs)}/46")

    separate: set[str] = set()
    offline = 0
    for task in task_dirs:
        manifest = tomllib.loads((task / "task.toml").read_text(encoding="utf-8"))
        if manifest.get("environment", {}).get("allow_internet") is False:
            offline += 1
        verifier = manifest.get("verifier", {})
        if (
            verifier.get("environment_mode") == "separate"
            or verifier.get("environment") is not None
        ):
            separate.add(task.name)
    check(
        "LHTB internet policy",
        offline == 22,
        f"offline={offline}, online={46 - offline}",
    )
    check(
        "LHTB verifier policy",
        separate == EXPECTED_SEPARATE,
        f"shared={46 - len(separate)}, separate={sorted(separate)}",
    )

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    selected = config["datasets"][0]["task_names"]
    check(
        "selected task coverage",
        len(selected) == args.expected_task_count
        and set(selected) <= {p.name for p in task_dirs},
        f"{len(selected)}/{args.expected_task_count}",
    )
    agent = config["agents"][0]
    kwargs = agent.get("kwargs", {})
    check(
        "Harbor adapter",
        agent.get("import_path") == EXPECTED_AGENT,
        str(agent.get("import_path")),
    )
    check("model selected", bool(agent.get("model_name")), str(agent.get("model_name")))
    check(
        "reasoning selected",
        bool(kwargs.get("reasoning_effort")),
        str(kwargs.get("reasoning_effort")),
    )
    execution = Execution(
        mode=kwargs.get("execution_mode", "heartbeat"),
        task_entry=kwargs.get("task_entry"),
        context=kwargs.get("iteration_context", "fresh"),
        validation_command=kwargs.get("validation_command", []),
    )
    check(
        "native Goal setting",
        kwargs.get("goals") == str(execution.native_goal).lower(),
        str(kwargs.get("goals")),
    )
    check(
        "web search disabled",
        kwargs.get("web_search") == "disabled",
        str(kwargs.get("web_search")),
    )

    actual_commit = command(["git", "-C", str(args.loopx_src), "rev-parse", "HEAD"])[1]
    check(
        "LoopX commit",
        actual_commit == args.expected_commit,
        actual_commit or "unavailable",
    )
    check(
        "LoopX external scheduler",
        (args.loopx_src / "scripts" / "external_scheduler_worker.py").is_file(),
        str(args.loopx_src / "scripts" / "external_scheduler_worker.py"),
    )
    scheduler_path = args.loopx_src / "scripts" / "external_scheduler_worker.py"
    try:
        scheduler_worker = load_module(
            scheduler_path, "lhtb_external_scheduler_worker_preflight"
        )
        stop_actions = (
            "stop_until_explicit_resume",
            "return_to_owner_until_material_change",
        )
        stop_decisions = [
            scheduler_worker.parse_tick(
                {
                    "should_run": False,
                    "effective_action": action,
                    "scheduler_hint": {
                        "action": action,
                        "cadence_class": "terminal",
                        "reason": "preflight",
                        "unchanged_poll": {"local_scheduler": "stop"},
                    },
                }
            )
            for action in stop_actions
        ]
        terminal_compatible = all(
            decision.terminal is True
            and decision.after_limit == "stop_tick_loop"
            and decision.unchanged_limit is None
            for decision in stop_decisions
        )
        terminal_detail = ", ".join(
            f"{decision.action}:terminal={decision.terminal}"
            for decision in stop_decisions
        )
    except Exception as exc:
        terminal_compatible = False
        terminal_detail = str(exc)
    check(
        "terminal scheduler compatibility",
        terminal_compatible,
        terminal_detail,
    )
    shared_codex_adapter = args.loopx_src / "benchmark" / "runtime" / "codex_offline.py"
    check(
        "shared offline Codex adapter",
        shared_codex_adapter.is_file(),
        str(shared_codex_adapter),
    )
    rc, help_text = command(
        [str(args.loopx_src / "scripts" / "loopx"), "configure-goal", "--help"]
    )
    replan_receipt: dict[str, int] = {}
    try:
        from benchmark.runtime.harbor import BenchmarkCodex

        with tempfile.TemporaryDirectory(prefix="benchmark-preflight-") as directory:
            candidate = BenchmarkCodex(
                logs_dir=Path(directory), model_name=agent["model_name"], **kwargs
            )
            check(
                "shared runtime configuration",
                True,
                f"{candidate.execution.mode}/{candidate.execution.context}",
            )
            if candidate.execution.uses_loopx:
                field, option, count = candidate._replan_configuration()
                check(
                    "resolved replan cadence CLI",
                    rc == 0 and f"{option} {{1,2,3,4,5}}" in help_text,
                    f"{field}={count}",
                )
                replan_receipt = candidate._replan_receipt()
    except (ImportError, TypeError, ValueError) as exc:
        check("shared runtime configuration", False, str(exc))

    harbor = lhtb_root / ".venv" / "bin" / "harbor"
    check("Harbor", harbor.is_file() and os.access(harbor, os.X_OK), str(harbor))
    check(
        "Codex binary",
        args.codex_bin.is_file() and os.access(args.codex_bin, os.X_OK),
        str(args.codex_bin),
    )
    check(
        "Codex code-mode sidecar",
        (args.codex_bin.parent / "codex-code-mode-host").is_file(),
        str(args.codex_bin.parent / "codex-code-mode-host"),
    )
    check(
        "portable Python",
        (args.portable_python / "bin" / "python3").is_file(),
        str(args.portable_python),
    )
    rc, node_version = command([str(args.node_dir / "bin" / "node"), "--version"])
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", node_version)
    check(
        "Node runtime",
        rc == 0
        and match is not None
        and tuple(map(int, match.groups())) >= MINIMUM_NODE_VERSION,
        f"{node_version}; requires >= {MINIMUM_NODE_VERSION_TEXT}",
    )

    docker_source = (
        lhtb_root / "upstream/harbor/src/harbor/environments/docker/docker.py"
    )
    patched = (
        docker_source.is_file()
        and "LHTB_MODELONLY_NET" in docker_source.read_text(encoding="utf-8")
    )
    check("model-only Harbor patch", patched, str(docker_source))
    rc, output = command(["docker", "info"])
    check("Docker daemon", rc == 0, output.splitlines()[0] if output else "unavailable")
    rc, output = command(
        ["docker", "network", "inspect", args.network, "--format", "{{.Internal}}"]
    )
    check(
        "internal model network",
        rc == 0 and output == "true",
        f"{args.network}: {output or 'missing'}",
    )

    parsed = urlsplit(args.gateway)
    health = f"{parsed.scheme}://{parsed.netloc}/health"
    rc, output = command(
        ["curl", "-sS", "--noproxy", "*", "-m", "5", health], timeout=10
    )
    check(
        "model gateway",
        rc == 0 and bool(output),
        f"{health}: {(output or 'unreachable')[:120]}",
    )

    receipt = {
        "ok": not failures,
        "config": str(args.config.resolve()),
        "task_count": len(selected),
        "loopx_commit": actual_commit,
        "runtime_profile": "generic_cli",
        "execution_mode": execution.mode,
        "iteration_context": execution.context,
        "home_scope": "trial",
        "model": agent.get("model_name"),
        "reasoning_effort": kwargs.get("reasoning_effort"),
        **replan_receipt,
        "verifier_policy": {"shared": 44, "separate": sorted(EXPECTED_SEPARATE)},
        "failures": failures,
    }
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
