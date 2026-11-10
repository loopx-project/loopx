"""Read-only inventory of local Docker images observed by EdgeBench preflight."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from itertools import chain
import json
import os
from pathlib import Path
import re


def _digest(value, pattern):
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else None


def _size(value):
    return value if type(value) is int and value >= 0 else None


def inventory(log_dirs, *, task=None):
    """Reduce selected run roots without reading submissions or contacting Docker."""
    receipts = set()
    unresolved_receipts = set()
    for log_dir in log_dirs:
        root = log_dir.resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"Expected an EdgeBench log directory: {root}")
        # Preserve literal matching, adding links omitted by older pathlib.
        literal_paths = root.glob("runs/*/*/runtime-receipt.json")
        linked_paths = (
            path for path in root.glob("runs/*/*/*")
            if path.name == "runtime-receipt.json" and os.path.islink(path)
        )
        for path in chain(literal_paths, linked_paths):
            case_id = path.parent.name
            if task is not None and case_id != task:
                continue
            try:
                receipt = path.resolve()
            except (OSError, RuntimeError):
                unresolved_receipts.add(path)
                continue
            if not receipt.is_relative_to(root):
                continue
            parts = receipt.relative_to(root).parts
            if len(parts) == 4 and parts[0] == "runs" and parts[2:] == (case_id, path.name):
                receipts.add(receipt)

    cases = {}
    unreadable = len(unresolved_receipts)
    for path in receipts:
        try:
            receipt = json.loads(path.read_bytes())
        except (OSError, ValueError):
            unreadable += 1
            continue
        if not isinstance(receipt, dict) or receipt.get("task") != path.parent.name:
            unreadable += 1
            continue
        case_id = receipt["task"]
        task_sha = _digest(receipt.get("task_sha256"), r"[0-9a-f]{64}")
        images, counts = cases.setdefault((case_id, task_sha), (defaultdict(Counter), Counter()))
        observations = receipt.get("local_docker_images")
        if not isinstance(observations, dict):
            counts["without_preflight"] += 1
            continue
        counts["with_preflight"] += 1
        for role in ("work", "judge"):
            entry = observations.get(role)
            entry = entry if isinstance(entry, dict) else {}
            image_id = _digest(entry.get("image_id"), r"sha256:[0-9a-f]{64}")
            images[role, image_id][_size(entry.get("logical_size_bytes"))] += 1

    rows = []
    for case_id, task_sha in sorted(cases, key=lambda key: (key[0], key[1] or "")):
        images, counts = cases[case_id, task_sha]
        rows.append({
            "task": case_id,
            "task_sha256": task_sha,
            "receipts_with_preflight": counts["with_preflight"],
            "receipts_without_preflight": counts["without_preflight"],
            "images": [
                {
                    "role": role,
                    "image_id": image_id,
                    "logical_sizes_bytes": sorted(size for size in sizes if size is not None),
                    "observations": sum(sizes.values()),
                    "observations_without_size": sizes[None],
                }
                for (role, image_id), sizes in sorted(
                    images.items(), key=lambda entry: (entry[0][0], entry[0][1] or "")
                )
            ],
        })
    return {"cases": rows, "unreadable_receipts": unreadable}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", type=Path, action="append", required=True,
                        help="Local run root. Repeat for additional cohorts.")
    parser.add_argument("--task", help="Only read receipts for this task ID.")
    args = parser.parse_args(argv)
    try:
        result = inventory(args.log_dir, task=args.task)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
