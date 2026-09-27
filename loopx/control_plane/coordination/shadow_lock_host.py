"""Kernel-lock effect for the native drainer; EOF releases every held lock.

The TS caller owns marker locks and all drain decisions. Keep OS-specific flock /
Windows locking in the existing Python adapter and use the caller's interpreter.
"""
from contextlib import ExitStack
import json
from pathlib import Path
import sys

from ...file_lock import try_exclusive_file_lock


def main() -> None:
    for line in sys.stdin:
        paths = json.loads(line)
        if not isinstance(paths, list) or not paths or any(
            not isinstance(path, str) or not Path(path).is_absolute() for path in paths
        ):
            raise ValueError("absolute lock targets required")
        with ExitStack() as stack:
            for path in paths:
                held = stack.enter_context(try_exclusive_file_lock(
                    Path(path), operation="local_authority_shadow_drain"
                ))
                if held is None:
                    print("busy", flush=True)
                    break
            else:
                print("held", flush=True)
                # EOF (including parent death) always releases the kernel locks.
                if sys.stdin.readline() != "release\n":
                    return
                stack.close()
                print("released", flush=True)


if __name__ == "__main__":
    main()
