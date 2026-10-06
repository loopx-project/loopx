from __future__ import annotations

import os
import sys
from pathlib import Path


def write_python_host_fixture(path: Path, source: str) -> Path:
    """Launch the same fixture through the real Host supervisor on each OS."""
    if os.name != "nt":
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)
        return path

    # Windows cannot execute a POSIX shebang. Use a native console launcher,
    # keeping argv, pipes, exit codes and supervision on the real process path.
    from distlib.scripts import ScriptMaker

    script = path.with_suffix(".py")
    script.write_text(source, encoding="utf-8")
    maker = ScriptMaker(str(path.parent), str(path.parent))
    maker.executable = sys.executable
    maker.force = True
    maker.variants = {""}
    maker.make(script.name)
    return path.with_suffix(".exe")


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
