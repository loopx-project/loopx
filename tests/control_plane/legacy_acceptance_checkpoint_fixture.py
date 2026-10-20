"""Run the public CLI with the frozen dc9fdbb acceptance checkpoint codec.

The default regression needs no historical checkout. Only the old checkpoint
producer is pinned; canonical stores, receipt admission, writeback and spend
remain real. A supplied base checkout runs its entire original CLI instead,
allowing the separately recorded actual base-to-current qualification.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

BASE_REVISION = "dc9fdbb5186618ec2e797a9d7545e2fe524fee56"
_OLD_CHECKPOINT_CLI = '''import hashlib,json
from loopx.control_plane.goals import goal_frontier as owner
original=owner.acceptance_gaps_from_held_goal_binding
def old_codec(summary,items,*,agent_id):
    gaps=original(summary,items,agent_id=agent_id)
    contract=(summary or {}).get("goal_acceptance_contract") or {}
    for gap in gaps:
        state=gap["kind"].removeprefix("goal_acceptance_")
        identity=[gap["vision_todo_ids"][0],gap.get("generated_at"),contract.get("digest"),contract.get("revision"),state]
        gap["frontier_revision"]=hashlib.sha256(json.dumps(identity,ensure_ascii=True,separators=(",",":"),default=str).encode()).hexdigest()
        gap.pop("legacy_frontier_revision",None)
    return gaps
owner.acceptance_gaps_from_held_goal_binding=old_codec
from loopx.cli import main
raise SystemExit(main())'''


def legacy_acceptance_checkpoint_cli(project: Path, *, base_source: Path | None = None):
    source = base_source or Path(__file__).resolve().parents[2]
    if base_source is not None:
        observed = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        assert observed == BASE_REVISION
    command = ["-m", "loopx.cli"] if base_source is not None else ["-c", _OLD_CHECKPOINT_CLI]
    prefix = [sys.executable, *command,
        "--registry", str(project.parent / "registry.json"),
        "--runtime-root", str(project.parent / "runtime"), "--format", "json"]
    environment = {**os.environ, "PYTHONPATH": str(source), "LOOPX_USAGE_PING": "0"}

    def run(*arguments):
        result = subprocess.run([*prefix, *arguments], cwd=source, env=environment,
            capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
        assert result.stdout.strip(), result.stderr
        return result.returncode, json.loads(result.stdout)
    return run
