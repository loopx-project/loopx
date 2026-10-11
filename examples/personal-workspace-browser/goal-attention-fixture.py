"""Exercise the real status producer against disposable synthetic Goal state."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from loopx.status import collect_status


def main() -> None:
    with TemporaryDirectory(prefix="loopx-attention-ui-") as directory:
        root = Path(directory)
        state = root / "GOAL.md"
        state.write_text(
            "---\nstatus: waiting\n---\n# Synthetic decision scope\n\n## User Todo\n\n"
            + "\n".join(
                f"- [ ] Review synthetic decision {index}.\n"
                f"  <!-- loopx:todo todo_id=decision-{index} status=open task_class=user_gate "
                f"updated_at=2026-10-{index + 1:02d}T00:00:00Z -->"
                for index in range(21)
            )
            + "\n\n## Agent Todo\n\n- [ ] Continue after the owner decision.\n",
            encoding="utf-8",
        )
        registry = root / "registry.json"
        registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(root / "runtime"),
            "goals": [{"id": "progress-projection", "repo": str(root), "state_file": state.name,
                "domain": "general", "status": "active", "authority_sources": [],
                "adapter": {"kind": "test_v0", "status": "connected-read-only"}}]}), encoding="utf-8")
        payload = collect_status(registry_path=registry, runtime_root_override=str(root / "runtime"),
            scan_roots=[], limit=5, include_public_boundary_scan=False)
        item = next(item for item in payload["attention_queue"]["items"] if item["goal_id"] == "progress-projection")
        assert item["user_todos"]["open_count"] == 21
        assert len(item["user_todos"]["items"]) == 12
        assert item["project_asset"]["user_todos"]["open"] == 21
        assert len(item["project_asset"]["user_todos"]["items"]) == 3
        print(json.dumps(item))


if __name__ == "__main__":
    main()
