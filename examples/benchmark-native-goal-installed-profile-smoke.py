#!/usr/bin/env python3
"""Prove a formal LoopX install and optional real app-server skill readback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.capabilities.benchmark_toolkit.native_codex_goal import (  # noqa: E402
    NativeGoalConfig,
    compact_native_goal_receipt,
    probe_native_goal_process,
)
from loopx.capabilities.benchmark_toolkit.native_codex_profile import (  # noqa: E402
    NativeCodexProfile,
    compact_native_codex_goal_prompt_receipt,
    compact_native_codex_profile_receipt,
    inspect_native_codex_profile,
    install_native_codex_profile,
    native_codex_app_server_shell_policy_args,
    native_codex_profile_environment,
    render_native_codex_goal_prompt,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument(
        "--require-app-server",
        action="store_true",
        help="Fail instead of recording unavailable when Codex is not on PATH.",
    )
    parser.add_argument(
        "--allow-dirty-source",
        action="store_true",
        help="Allow an uncommitted checkout for local pre-commit validation.",
    )
    return parser


def _run_profile_cli(
    profile: NativeCodexProfile,
    project: Path,
    *args: str,
) -> dict[str, object]:
    completed = subprocess.run(
        [str(profile.cli_bin), *args],
        cwd=project,
        env=native_codex_profile_environment(profile),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode:
        digest = hashlib.sha256(completed.stderr.encode("utf-8")).hexdigest()[:16]
        raise SystemExit(f"profile CLI setup failed: stderr_sha256={digest}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit("profile CLI setup returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise SystemExit("profile CLI setup did not return ok")
    return payload


def _stop_profile_runtime(profile: NativeCodexProfile) -> None:
    # Stop only this disposable installed profile's runtime before removing
    # files that its idle writer can still create.
    payload = _run_profile_cli(
        profile,
        profile.root,
        "--format",
        "json",
        "doctor",
        "--installation-only",
        "--restart-runtime",
    )
    restart = payload.get("effect_runtime_restart")
    if not isinstance(restart, dict) or restart.get("status") not in {
        "stopped",
        "not_running",
    }:
        raise SystemExit("profile runtime shutdown did not complete")


def _profile_authority_contains_value(profile: NativeCodexProfile, value: str) -> bool:
    marker = value.encode("utf-8")
    for root in (profile.home, profile.codex_home):
        for path in root.rglob("*"):
            if (
                not path.is_file()
                or path.is_symlink()
                or path.stat().st_size > 2_000_000
            ):
                continue
            if marker in path.read_bytes():
                return True
    return False


def main() -> int:
    args = _parser().parse_args()
    with (
        tempfile.TemporaryDirectory(prefix="loopx-native-goal-profile-smoke-") as raw,
        ExitStack() as cleanup,
    ):
        profile = install_native_codex_profile(
            REPO_ROOT,
            Path(raw) / "profile",
            require_clean_source=not args.allow_dirty_source,
        )
        cleanup.callback(_stop_profile_runtime, profile)
        profile_receipt = compact_native_codex_profile_receipt(profile)
        provider_key = "ARK_OPENAI_API_KEY"
        gateway_sentinel_key = "LOOPX_MODEL_PROVIDER_SENTINEL"
        unrelated_key = "LOOPX_BENCHMARK_UNRELATED_SENTINEL"
        provider_value = (
            "fixture-" + hashlib.sha256(str(profile.root).encode("utf-8")).hexdigest()
        )
        sentinel_env = {
            **os.environ,
            provider_key: provider_value,
            unrelated_key: "unrelated-sentinel",
        }
        default_profile_env = native_codex_profile_environment(
            profile, base_env=sentinel_env
        )
        app_server_env = {
            **default_profile_env,
            gateway_sentinel_key: "runner-owned-gateway-no-upstream-secret",
        }
        shell_policy_args = native_codex_app_server_shell_policy_args(
            excluded_env_keys=(gateway_sentinel_key,)
        )
        provider_environment_receipt = {
            "upstream_provider_value_excluded": provider_key not in app_server_env,
            "runner_gateway_sentinel_admitted": app_server_env.get(gateway_sentinel_key)
            == "runner-owned-gateway-no-upstream-secret",
            "unrelated_value_excluded": unrelated_key not in app_server_env,
            "profile_home_provider_value_excluded": (
                not _profile_authority_contains_value(profile, provider_value)
            ),
            "agent_shell_gateway_sentinel_excluded": (
                f'shell_environment_policy.exclude=["{gateway_sentinel_key}"]'
                in shell_policy_args
            ),
            "raw_values_recorded": False,
        }
        if not all(
            value is True
            for key, value in provider_environment_receipt.items()
            if key != "raw_values_recorded"
        ):
            raise SystemExit("profile provider environment boundary failed")
        project = Path(raw) / "project"
        project.mkdir()
        registry = project / ".loopx" / "registry.json"
        runtime_root = project / ".loopx" / "runtime"
        runtime_registry = runtime_root / "registry.global.json"
        goal_id = "native-goal-profile-smoke"
        agent_id = "native-goal-smoke-agent"
        _run_profile_cli(
            profile,
            project,
            "--format",
            "json",
            "--runtime-root",
            str(runtime_root),
            "connect",
            "--goal-id",
            goal_id,
            "--objective",
            "Validate one formal native Goal treatment without model work.",
            "--state-file",
            ".loopx/state.md",
            "--adapter-kind",
            "read_only_project_map_v0",
            "--adapter-status",
            "connected-read-only",
            "--no-global-sync",
        )
        _run_profile_cli(
            profile,
            project,
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            "configure-goal",
            "--goal-id",
            goal_id,
            "--registered-agent",
            agent_id,
            "--execute",
        )
        prompt = render_native_codex_goal_prompt(
            profile,
            project_root=project,
            goal_id=goal_id,
            agent_id=agent_id,
            registry_path=registry,
            runtime_root=runtime_root,
            runtime_registry_path=runtime_registry,
        )
        prompt_receipt = compact_native_codex_goal_prompt_receipt(prompt)
        codex_bin = shutil.which(args.codex_bin)
        if codex_bin is None:
            if args.require_app_server:
                raise SystemExit("Codex app-server executable is unavailable")
            app_server_receipt: dict[str, object] = {
                "status": "not_available",
                "model_turn_started": False,
            }
        else:
            turn = probe_native_goal_process(
                NativeGoalConfig(
                    cwd=str(project),
                    objective=prompt.task_body,
                    task_instruction="No model turn may be started.",
                    required_skill_ids=profile.required_skill_ids,
                ),
                codex_bin=codex_bin,
                process_command=[
                    codex_bin,
                    "app-server",
                    "--listen",
                    "stdio://",
                    "--enable",
                    "goals",
                    "-c",
                    'model_provider="loopx_runner_gateway"',
                    "-c",
                    'model_providers.loopx_runner_gateway.name="runner-owned-gateway"',
                    "-c",
                    'model_providers.loopx_runner_gateway.base_url="http://127.0.0.1:9"',
                    "-c",
                    (
                        "model_providers.loopx_runner_gateway.env_key="
                        '"LOOPX_MODEL_PROVIDER_SENTINEL"'
                    ),
                    "-c",
                    'model_providers.loopx_runner_gateway.wire_api="responses"',
                    *shell_policy_args,
                ],
                process_env=app_server_env,
                process_cwd=str(project),
                response_timeout_sec=30,
            )
            compact = compact_native_goal_receipt(turn)
            app_server_receipt = {
                "status": "verified",
                "methods": compact["methods"],
                "goal_status": compact["goal_status"],
                "model_turn_started": compact["turn_id_present"],
                "required_skills_discovered": compact["required_skills_discovered"],
                "required_skill_ids": compact["required_skill_ids"],
                "skill_catalog_count": compact["skill_catalog_count"],
                "skill_error_count": compact["skill_error_count"],
            }
            if app_server_receipt["model_turn_started"] is not False:
                raise SystemExit("profile smoke unexpectedly started a model turn")
            if app_server_receipt["required_skills_discovered"] is not True:
                raise SystemExit("profile smoke did not discover required skills")
        post_run_profile = inspect_native_codex_profile(
            profile.root,
            source_root=REPO_ROOT,
            require_clean_source=not args.allow_dirty_source,
        )
        if post_run_profile.skills_digest != profile.skills_digest:
            raise SystemExit("profile skill digest changed during the smoke")

    print(
        json.dumps(
            {
                "ok": True,
                "profile": profile_receipt,
                "goal_prompt": prompt_receipt,
                "app_server": app_server_receipt,
                "provider_environment": provider_environment_receipt,
                "public_boundary": {
                    "local_paths_recorded": False,
                    "credentials_recorded": False,
                    "task_content_recorded": False,
                    "raw_goal_prompt_recorded": False,
                    "model_turn_started": False,
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
