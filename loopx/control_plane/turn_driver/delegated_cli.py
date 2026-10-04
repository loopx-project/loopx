"""Private CLI entry: unwind nested managed Hosts when its lease owner cancels.

The TS supervisor owns renewal and deadlines. Python only turns TERM into
normal stack unwinding so each managed-process transport closes its control
pipe and waits for its own Host cleanup before this CLI exits.
"""
import os
import runpy
import signal
import sys

from .host_process_transport import HOST_PROCESS_PARENT_ENV, HOST_PROCESS_RECORD_ENV


def _cancel(_signal, _frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    # This private launcher alone carries the nested record across the outer
    # leased supervisor. The actual Host transport consumes it before launch.
    if len(sys.argv) >= 3 and sys.argv[1] == "--host-process-record":
        os.environ[HOST_PROCESS_RECORD_ENV] = sys.argv[2]
        os.environ[HOST_PROCESS_PARENT_ENV] = "leased"
        del sys.argv[1:3]
    signal.signal(signal.SIGTERM, _cancel)
    runpy.run_module("loopx.cli", run_name="__main__")
