from __future__ import annotations


COUNTER_PROCESS_SOURCE = """
import os
import signal
import sys
import time
from pathlib import Path

marker = Path(sys.argv[1])
pid_path = Path(sys.argv[2])
interval_seconds = float(sys.argv[3])
pause_path = Path(sys.argv[4]) if len(sys.argv) == 5 else None

signal.signal(signal.SIGTERM, signal.SIG_IGN)
pid_path.write_text(str(os.getpid()), encoding="utf-8")
staged = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
counter = 0
while True:
    staged.write_text(str(counter), encoding="utf-8")
    if pause_path is not None:
        pause_path.touch()
        signal.pause()
    os.replace(staged, marker)
    counter += 1
    time.sleep(interval_seconds)
"""
