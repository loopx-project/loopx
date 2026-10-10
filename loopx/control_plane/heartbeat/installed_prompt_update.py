"""Update-time discovery and conservative adoption of installed host prompts.

Only a byte-exact generated prompt may opt into unattended replacement. Names,
Goal ids and prose heuristics can suggest review, never authorize replacement.
The App remains the preferred writer while running.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from .automation_upgrade import SCHEMA, _atomic, apply_offline, bootstrap_binding, build_plan
from .bootstrap_prompt import host_bootstrap_binding


def require_closed_app(timeout: float = 5.0) -> None:
    if sys.platform != "darwin":
        raise ValueError("offline adapter is qualified only on macOS; use the App automation API")
    for name in ("Codex", "ChatGPT"):
        try:
            observed = subprocess.run(
                ["/usr/bin/pgrep", "-x", name],
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise ValueError(f"failed to check running application status for {name}: {exc}") from exc
        if observed.returncode != 1:
            raise ValueError("close the Codex/ChatGPT App before offline migration; otherwise use automation_update")


def _owned(entry: dict, registry: Path, runtime_root: str | None, cli_bin: str) -> bool:
    prompt = entry.get("current_prompt", "")
    binding = bootstrap_binding(prompt) or host_bootstrap_binding(prompt)
    if binding is not None:
        # Never retarget a canary binary, home, registry or runtime implicitly.
        return (binding["registry"].resolve() == registry.resolve()
                and binding.get("cli_bin", "loopx") == cli_bin
                and binding.get("runtime_root") == runtime_root)
    # Before replacing the installed version, reproduce its uncustomized output.
    # Older/custom bodies which cannot be reproduced remain review-only.
    from loopx.agent_registry import agent_profile_from_registry, registered_agent_ids_from_registry
    from loopx.heartbeat_prompt import build_heartbeat_prompt

    for mode in ("thin", "brief", "compact", "full"):
        try:
            generated = build_heartbeat_prompt(
                goal_id=entry["goal_id"], agent_id=entry["agent_id"],
                registered_agents=registered_agent_ids_from_registry(registry, entry["goal_id"]),
                agent_profile=agent_profile_from_registry(registry, entry["goal_id"], entry["agent_id"]),
                runtime_profile="codex_app_heartbeat", runtime_root=runtime_root,
                cli_bin=cli_bin, **{mode: True},
            )
        except ValueError:
            continue
        if generated.get("ok") and generated.get("task_body") == prompt:
            return True
    return False


def snapshot(*, registry: Path, home: Path, runtime_root: str | None = None,
             cli_bin: str = "loopx") -> dict:
    if not (home / "sqlite/codex-dev.db").is_file():
        return {"ok": True, "status": "not_installed", "entries": []}
    plan = build_plan(registry=registry, home=home, runtime_root=runtime_root, cli_bin=cli_bin)
    for entry in plan["entries"]:
        entry["automatic_eligible"] = (entry["status"] in {"current", "adoption_required"}
            and _owned(entry, registry, runtime_root, cli_bin))
    return plan


def reconcile(*, before: dict, registry: Path, home: Path,
              runtime_root: str | None = None, cli_bin: str = "loopx") -> dict:
    """Re-read after installation; report per-task outcomes without raw prompts."""
    if before.get("status") == "not_installed":
        return {"ok": True, "status": "not_installed", "results": []}
    if before.get("codex_home") != str(home.resolve()):
        raise ValueError("update snapshot belongs to another host home")
    if before.get("schema_version") != SCHEMA:
        raise ValueError("unsupported update snapshot schema")
    current = {entry["automation_id"]: entry for entry in
               build_plan(registry=registry, home=home, runtime_root=runtime_root, cli_bin=cli_bin)["entries"]}
    results = []
    api_updates = []
    for old in before["entries"]:
        identifier = old["automation_id"]
        now = current.get(identifier)
        result = {"automation_id": identifier, "status": "review_required"}
        if now is None:
            result["status"] = "missing"
        elif now["status"] in {"current", "unmanaged", "blocked"}:
            result["status"] = now["status"]
            if now.get("reason"):
                result["reason"] = now["reason"]
        elif any(old.get(key) != now.get(key) for key in
                 ("source_sha256", "prompt_sha256", "target_thread_id", "goal_id", "agent_id")):
            result["status"] = "changed_since_snapshot"
        elif old.get("automatic_eligible") is True:
            try:
                # The desktop host caches automation rows and can overwrite both
                # mirrors after a direct SQLite update. CAS only fences disk
                # writers; it does not invalidate the host's live scheduler.
                require_closed_app()
                applied = apply_offline(home=home, automation_id=identifier,
                    expected_prompt_sha256=old["prompt_sha256"], desired_prompt=now["desired_prompt"],
                    expected_source_sha256=old["source_sha256"])
                result["status"] = applied["status"]
            except (OSError, ValueError, sqlite3.Error) as error:
                result.update(status="deferred", reason=str(error))
                # The CLI cannot call an in-App tool itself. Give its host a
                # complete prompt-only request, plus a precondition to re-view.
                import tomllib
                try:
                    manifest = tomllib.loads((home / "automations" / identifier / "automation.toml").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    manifest = {}
                required = {"name", "status", "rrule", "target_thread_id"}
                if required <= manifest.keys() and manifest.get("prompt") == now["current_prompt"]:
                    api_updates.append({"tool": "automation_update",
                        "expected_prompt_sha256": now["prompt_sha256"],
                        "precondition": "View the same automation; verify this prompt hash and all preserved fields before update; read back afterward.",
                        "arguments": {"mode": "update", "id": identifier, "kind": "heartbeat",
                            "name": manifest["name"], "status": manifest["status"],
                            "rrule": manifest["rrule"], "targetThreadId": manifest["target_thread_id"],
                            "notificationPolicy": manifest.get("notification_policy"),
                            "prompt": now["desired_prompt"]}})
        results.append(result)
    from .prompt_upgrade_hook import record_deferred_upgrades
    record_deferred_upgrades(registry=registry, home=home, runtime_root=runtime_root,
        cli_bin=cli_bin, entries=current, results=results)
    pending = any(result["status"] not in {"current", "updated", "unmanaged", "missing"} for result in results)
    return {"ok": not pending, "status": "attention_required" if pending else "current", "results": results,
            "api_updates": api_updates,
            "next_action": "Use automation-prompts plan and the App automation API for pending entries; never rewrite scheduling or thread bindings." if pending else None}


def save_snapshot(path: Path, payload: dict) -> None:
    _atomic(path, json.dumps(payload, ensure_ascii=False))


def update_with_prompts(payload: dict, *, registry: Path, runtime_root: str | None,
                        timeout_seconds: int, runtime_update) -> dict:
    """Capture old-template evidence, then run reconciliation in the NEW runtime.

    Prompt problems are separate from binary installation success. Never report
    the application fully updated merely because its executable was replaced.
    """
    from loopx.upgrade import codex_home

    home = codex_home().expanduser().resolve()
    try:
        before = snapshot(registry=registry, home=home, runtime_root=runtime_root)
    except (ValueError, OSError, sqlite3.Error) as error:
        before = {"ok": False, "status": "discovery_failed", "reason": str(error)}
    updated = runtime_update(payload, timeout_seconds=timeout_seconds)
    # Optional extension qualification is not binary installation readiness.
    # Do not strand managed prompts after a successful install + core doctor,
    # but preserve the failed aggregate result and its original repair action.
    runtime_ready = bool(updated.get("ok"))
    execution = updated.get("execution", {})
    installed = runtime_ready or (updated.get("changes_applied") is True
        and execution.get("install_returncode") == 0
        and execution.get("doctor_returncode") == 0)
    updated["upgrade_complete"] = False
    if not installed:
        updated["automation_prompt_upgrade"] = {"status": "skipped_runtime_update_failed"}
        return updated
    if not before.get("ok") or not before.get("entries"):
        updated["automation_prompt_upgrade"] = {
            key: value for key, value in before.items() if key in {"ok", "status", "reason"}}
        updated["upgrade_complete"] = runtime_ready and before.get("ok") is True
        if not before.get("ok") and runtime_ready:
            updated["recommended_action"] = "Runtime updated; automation discovery failed. Review automation-prompts plan through the App before adopting prompts."
        return updated
    directory = Path(tempfile.mkdtemp(prefix="loopx-prompt-update-"))
    plan_file = directory / "before.json"
    save_snapshot(plan_file, before)
    driver = payload.get("install_lifecycle", {}).get("execution_driver")
    if driver is None and isinstance(payload.get("source"), dict):
        driver = "archive_snapshot"
    command = ([sys.executable, "-m", "loopx.cli"] if driver in {"python_pip", "python_pipx"}
               else [str(Path.home() / ".local/bin/loopx")] if driver == "archive_snapshot"
               else ["loopx"])
    command += ["--format", "json", "--registry", str(registry.resolve())]
    if runtime_root:
        command += ["--runtime-root", runtime_root]
    command += ["automation-prompts", "sync-installed", "--codex-home", str(home),
                "--plan-file", str(plan_file), "--execute"]
    try:
        # Do not accidentally import a checkout through the parent's PYTHONPATH.
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                                timeout=timeout_seconds, cwd=directory)
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or "results" not in report:
            raise ValueError("new runtime returned no prompt migration result")
    except (ValueError, OSError, subprocess.TimeoutExpired):
        report = {"ok": False, "status": "reconciliation_failed"}
    if not report.get("ok"):
        report["snapshot_file"] = str(plan_file)
        if runtime_ready:
            updated["recommended_action"] = "Runtime updated; review pending automation prompts using the App API, or retry sync-installed with the saved snapshot. Custom prompts and alternate loader bindings require explicit review."
            updated["next_action"] = {"kind": "apply_host_prompt_updates", "mutating": True,
                "requires_explicit_approval": any(row.get("status") == "review_required"
                    for row in report.get("results", [])),
                "reason": "Only byte-exact managed prompts have automatic adoption authority; custom entries require separate review.",
                "api_updates": report.get("api_updates", [])}
    else:
        plan_file.unlink()
        directory.rmdir()
    updated["automation_prompt_upgrade"] = report
    updated["upgrade_complete"] = runtime_ready and report.get("ok") is True
    return updated
