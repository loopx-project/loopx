"""One reviewed migration; subsequent runtime upgrades need no host-store write."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from loopx.control_plane.heartbeat.automation_upgrade import (
    SCHEMA, _atomic, apply_offline, build_plan, recover_offline,
)
from loopx.upgrade import codex_home
from loopx.control_plane.heartbeat.installed_prompt_update import require_closed_app as _require_offline


def register_automation_prompts(subparsers, add_format) -> None:
    parser = subparsers.add_parser("automation-prompts", help="Preview and migrate existing Codex heartbeats to live LoopX rules.")
    add_format(parser)
    parser.add_argument("action", choices=("plan", "apply", "recover", "rollback", "sync-installed"))
    parser.add_argument("--codex-home", type=Path, help="One explicit host home; never discovers or migrates other homes.")
    parser.add_argument("--plan-file", type=Path, help="Private reviewed plan file; plan saves it, apply reads it.")
    parser.add_argument("--automation-id", action="append", default=[])
    parser.add_argument("--cli-bin", default="loopx", help="Stable installed executable; choose a canary binary for a single-task trial.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Acknowledge the Codex App is closed; use its automation API while running.")


def run(args: argparse.Namespace, registry: Path) -> dict:
    home = (args.codex_home or codex_home()).expanduser().resolve()
    if args.action == "sync-installed":
        from loopx.control_plane.heartbeat.installed_prompt_update import reconcile, snapshot
        if not args.execute:
            return snapshot(registry=registry, home=home, runtime_root=args.runtime_root, cli_bin=args.cli_bin)
        if not args.plan_file:
            raise ValueError("sync-installed --execute requires the private pre-update --plan-file")
        before = json.loads(args.plan_file.read_text(encoding="utf-8"))
        if args.automation_id:
            before["entries"] = [entry for entry in before.get("entries", [])
                                 if entry["automation_id"] in args.automation_id]
        return reconcile(before=before, registry=registry,
                         home=home, runtime_root=args.runtime_root, cli_bin=args.cli_bin)
    if args.action == "plan":
        if args.execute:
            raise ValueError("plan cannot execute")
        payload = build_plan(registry=registry, home=home, runtime_root=args.runtime_root, cli_bin=args.cli_bin)
        if args.automation_id:
            payload["entries"] = [entry for entry in payload["entries"] if entry["automation_id"] in args.automation_id]
        if args.plan_file:
            _atomic(args.plan_file.expanduser(), json.dumps(payload, ensure_ascii=False, indent=2))
        return payload
    if not args.execute or not args.offline:
        raise ValueError("use the App API, or close the App and explicitly pass --offline --execute")
    _require_offline()
    if args.action in ("recover", "rollback"):
        if not args.automation_id:
            raise ValueError("recovery requires explicit --automation-id")
        return {"ok": True, "results": [recover_offline(home=home, automation_id=identifier,
                rollback=args.action == "rollback") for identifier in args.automation_id]}
    if not args.plan_file:
        raise ValueError("apply requires a reviewed --plan-file")
    plan = json.loads(args.plan_file.expanduser().read_text(encoding="utf-8"))
    if plan.get("schema_version") != SCHEMA or plan.get("codex_home") != str(home):
        raise ValueError("plan schema or host-home mismatch")
    # Regenerate using current registry/profile inputs: a saved plan is not a
    # license to install stale rules or execute arbitrary prompt text.
    current = {entry["automation_id"]: entry for entry in build_plan(
        registry=registry, home=home, runtime_root=args.runtime_root, cli_bin=args.cli_bin)["entries"]}
    results = []
    for entry in plan["entries"]:
        identifier = entry["automation_id"]
        if args.automation_id and identifier not in args.automation_id:
            continue
        if entry["status"] != "adoption_required":
            continue
        now = current.get(identifier)
        if not now or any(now.get(key) != entry.get(key) for key in
                          ("prompt_sha256", "source_sha256", "desired_prompt", "target_thread_id")):
            results.append({"automation_id": identifier, "ok": False, "status": "preview_stale"})
            continue
        try:
            results.append(apply_offline(home=home, automation_id=identifier,
                expected_prompt_sha256=entry["prompt_sha256"], desired_prompt=entry["desired_prompt"],
                expected_source_sha256=entry["source_sha256"],
                expected_database=plan.get("database_name", "codex-dev.db")))
        except (ValueError, OSError, sqlite3.Error) as error:
            results.append({"automation_id": identifier, "ok": False, "status": "failed", "reason": str(error)})
    return {"ok": all(item["ok"] for item in results), "results": results,
            "scope": "per-automation commit; other automations and sessions unchanged"}


def render(payload: dict) -> str:
    return "# Automation prompt upgrade\n\n```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"
