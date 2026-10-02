"""Private CLI entry: unwind nested managed Hosts when its lease owner cancels.

The TS supervisor owns renewal and deadlines. Python only turns TERM into
normal stack unwinding so each managed-process transport closes its control
pipe and waits for its own Host cleanup before this CLI exits.
"""
import runpy
import signal


def _cancel(_signal, _frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _cancel)
    runpy.run_module("loopx.cli", run_name="__main__")
