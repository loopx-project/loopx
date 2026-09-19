#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
repo_root_text = str(REPO_ROOT)
if sys.path[0] != repo_root_text:
    sys.path.insert(0, repo_root_text)

from loopx.control_plane.testing.release_commit_qualification import (  # noqa: E402
    collect_release_source_identity,
)
from loopx.control_plane.testing.replan_semantic_action_behavior import (  # noqa: E402
    DoubaoReplanSemanticActionBehaviorActor,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one production-prompt Doubao tool loop against a hermetic "
            "LoopX goal and verify that the model chooses a typed semantic "
            "delta from the host-projected replan action packet."
        )
    )
    parser.add_argument("--qualification-id", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--required-vision", action="store_true", help="Qualify the missing-vision journey through bound settlement and readback.")
    return parser


def main() -> int:
    args = _parser().parse_args()
    source = collect_release_source_identity(args.repo_root)
    if source["git_dirty"]:
        raise RuntimeError(
            "live Doubao qualification requires a clean candidate checkout"
        )
    actor = DoubaoReplanSemanticActionBehaviorActor.from_environment(
        timeout_seconds=args.timeout_seconds
    )
    with TemporaryDirectory(prefix="loopx-doubao-replan-semantic-") as temp_dir:
        result = actor.qualify(
            qualification_id=args.qualification_id,
            fixture_root=Path(temp_dir),
            required_vision=args.required_vision,
        )
    result["source"] = source
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["qualification_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
