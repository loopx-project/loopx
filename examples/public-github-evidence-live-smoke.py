#!/usr/bin/env python3
"""Opt-in public GitHub exact-plan journey through the shipped source CLI.

Only anonymous public GETs and a disposable synthetic research ledger are used.
The explicit synthetic parent decision is separate from provider execution.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def qualify(source: str, root: Path) -> dict:
    def run(*args, json_output=True):
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", "loopx.entrypoint",
            "--runtime-root", str(root / "runtime"), "--registry", str(root / "registry.json"),
            *args, "--format", "json" if json_output else "markdown"], capture_output=True,
            text=True, encoding="utf-8", timeout=90, check=True)
        return json.loads(result.stdout) if json_output else result.stdout

    def save(name, value):
        path = root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    objective = "Inspect the public pinned README for the LoopX literal"
    plan = run("external-evidence", "plan", "--objective", objective,
        "--user-activity", "Choose a public source", "--decision", "Whether the source contains LoopX",
        "--evidence-kind", "literal_match", "--public-github", "--source", source, "--search-term", "LoopX")
    assert plan["status"] == "ready"
    plan_path = save("plan.json", plan)
    execution = run("external-evidence", "execute", "--plan-json", plan_path, "--execute")
    receipt = execution["receipt"]
    assert receipt["status"] == "succeeded" and len(receipt["sources"]) == 1
    assert "observed at lines" in receipt["sources"][0]["finding"]
    receipt_path = save("receipt.json", execution)
    before = run("external-evidence", "readback", "--plan-json", plan_path, "--receipt-json", receipt_path)
    assert before["parent_admission"] is None and before["retirement"] is None
    admitted = run("external-evidence", "admit", "--plan-json", plan_path, "--receipt-json", receipt_path,
        "--decision", "admit", "--reason", "Synthetic parent checked the pinned file and literal-match finding",
        "--admit-source", source)
    admission_path = save("admission.json", admitted)
    project = root / "research"
    run("deepresearch", "start", "--project", str(project), "--question", objective)
    args = ("external-evidence", "readback", "--plan-json", plan_path, "--receipt-json", receipt_path,
        "--admission-json", admission_path, "--project", str(project))
    assert run(*args)["retirement"]["status"] == "retained"
    result = run(*args, "--execute")
    assert result["downstream_source_refs"] == [source]
    assert result["retirement"]["status"] == "retire_ready"
    assert run(*args, "--execute") == result
    markdown = run(*args, json_output=False)
    assert source in markdown and "retire_ready" in markdown and "Evidence completeness is unverified" in markdown
    return {"ok": True, "provider": "method:public-github", "sources_observed": 1,
        "explicit_parent_admission": True, "actual_ledger_readback": True,
        "retained_before_projection": True, "idempotent_projection": True,
        "raw_content_persisted": False, "source_ref": source}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-public-provider", action="store_true")
    parser.add_argument("--source")
    args = parser.parse_args()
    if not args.execute_public_provider:
        # The public fleet must exercise default-off refusal without fetching a
        # source. The owning CLI rejects before opening even the absent plan.
        with tempfile.TemporaryDirectory(prefix="lxe-off-") as folder:
            result = subprocess.run([sys.executable, "-m", "loopx.entrypoint",
                "external-evidence", "execute", "--plan-json",
                str(Path(folder) / "absent.json"), "--format", "json"],
                capture_output=True, text=True, timeout=30)
            payload = json.loads(result.stdout)
            assert result.returncode == 1, payload
            assert "--execute is required" in payload["error"], payload
        print(json.dumps({"ok": True, "default_off_refusal_verified": True,
            "provider_executed": False, "live_journey_qualified": False}))
        return
    if not args.source:
        parser.error("--source is required with --execute-public-provider")
    with tempfile.TemporaryDirectory(prefix="lxe-") as folder:
        print(json.dumps(qualify(args.source, Path(folder)), sort_keys=True))


if __name__ == "__main__":
    main()
