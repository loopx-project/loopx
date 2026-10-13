"""Locked canonical graph transport for native completion. EOF releases locks.

The existing Python Explore codec owns graph IO; TypeScript remains the owner
of eligibility, lineage and completion. No caller commands or task effects run.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from ...file_lock import exclusive_file_lock
from .research_frontier import build_research_composition_frontier
from .result_log import build_explore_result_projection, explore_result_log_path, load_explore_result_events_strict


def main() -> None:
    request = json.loads(sys.stdin.readline())
    root, goal = Path(request["runtime_root"]), request["goal_id"]
    if not root.is_absolute():
        raise ValueError("absolute source runtime required")
    path = explore_result_log_path(root, goal)
    with exclusive_file_lock(path, operation="research-native-completion"):
        events = load_explore_result_events_strict(path, goal_id=goal)
        projection = build_explore_result_projection(events, goal_id=goal)
        frontier = build_research_composition_frontier(
            projection, candidate_sources=[
                {"node_id": event["result_id"], "research_observation": event["research_observation"]}
                for event in events if event.get("research_observation")
            ], harness=request["harness"], todos=request["todos"], agent_id=request["agent_id"],
        )
        print(json.dumps({"schema_version": "research_graph_snapshot_v0", "frontier": frontier},
                         ensure_ascii=False, allow_nan=False), flush=True)
        # Keep the exact graph locked until the native transaction returns.
        # Parent death closes this pipe and releases the OS lock automatically.
        if sys.stdin.readline() != "release\n":
            return


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print(json.dumps({"schema_version": "research_graph_snapshot_v0", "error": type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
