"""Select one balanced shard of complete TypeScript test files."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.ci.module_shard import assign_modules

TEST_ROOT = Path("tests/control_plane_ts")
# Measured single-file seconds for the files that dominate the suite. Weights
# only balance shards: every file is selected by exactly one shard, and a stale
# or missing weight (default 1) can unbalance but never skip a file.
WEIGHTS = {
    "nokv_jsonl_transport.test.ts": 409,
    "nokv_authority_store.test.ts": 361,
    "sqlite_authority_store.test.ts": 342,
    "authority_store.test.ts": 250,
    "leased_continuation.test.ts": 87,
    "todo_continuation.test.ts": 56,
    "sqlite_capacity.test.ts": 35,
    "canonical_task_lease_renew.test.ts": 34,
    "local_authority_provider.test.ts": 29,
    "local_authority_runtime.test.ts": 27,
    "reviewed_promotion.test.ts": 22,
    "canonical_snapshot_page.test.ts": 21,
    "sqlite_authority_bounded_profile.test.ts": 18,
    "authority_archive.test.ts": 13,
    "shadow_management.test.ts": 12,
    "coordination_runtime_shadow.test.ts": 12,
}


def select(files: list[str], shards: int, shard: int) -> list[str]:
    if not 1 <= shard <= shards:
        raise ValueError("invalid shard coordinates")
    assigned = assign_modules({name: WEIGHTS.get(name, 1) for name in files}, shards)
    return sorted(name for name in files if assigned[name] == shard)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--shard", type=int, required=True)
    args = parser.parse_args(argv)
    files = sorted(path.name for path in TEST_ROOT.glob("*.test.ts"))
    if not files:
        parser.error(f"no TypeScript tests under {TEST_ROOT}")
    for name in select(files, args.shards, args.shard):
        print((TEST_ROOT / name).as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
