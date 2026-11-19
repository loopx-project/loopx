"""Native SForge judge using the same frozen source as EdgeBench workers."""
from __future__ import annotations

import sys

from benchmark.edgebench.source import bootstrap


def main():
    bootstrap(keep_log_arg=False)
    from sforge.cli import main as native_main

    sys.argv.insert(1, "serve")
    native_main()


if __name__ == "__main__":
    main()
