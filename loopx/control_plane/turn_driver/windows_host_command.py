"""Windows executable selection and creation IO for the TS Host supervisor.

Run in the requested Host cwd/environment. The private report describes OS
process creation, not successful script execution, acceptance or authority.
No timeout, retry or descendant supervision decision belongs in this relay.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def resolve_executable(name: str) -> str:
    if not name:
        raise FileNotFoundError("empty Host command")
    # CreateProcess's extensionless compatibility is .exe, independent of
    # PATHEXT. Explicit extensions are exact, including .cmd/.bat/.com.
    filename = name if os.name != "nt" or os.path.splitext(name)[1] else name + ".exe"
    candidates = ([filename] if os.path.dirname(filename)
                  else [os.path.join(directory, filename) for directory in [os.curdir, *os.get_exec_path()]])
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    raise FileNotFoundError(name)


def main() -> int:
    report, command = Path(sys.argv[1]), sys.argv[2:]
    try:
        executable = resolve_executable(command[0])
        # lpApplicationName binds the exact chosen file. A second implicit
        # CreateProcess search must not substitute x.exe.exe after removal.
        arguments: str | list[str] = [executable, *command[1:]]
        if os.name == "nt" and os.path.splitext(executable)[1].lower() in {".cmd", ".bat"}:
            # CreateProcess starts batch files through cmd. Its /c parser needs
            # the enclosing quotes as well as list2cmdline's per-argument quotes
            # when the batch pathname contains spaces.
            arguments = '"' + subprocess.list2cmdline(arguments) + '"'
        process = subprocess.Popen(arguments, executable=executable)
    except OSError:
        report.write_text(json.dumps(False), encoding="utf-8")
        return 1
    try:
        report.write_text(json.dumps(True), encoding="utf-8")
        return process.wait()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
