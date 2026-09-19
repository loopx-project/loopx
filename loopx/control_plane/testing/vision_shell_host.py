"""OS-isolated shell for the required-vision actor; CLI effects stay supervised.

The shell owns normal command syntax, not a qualification-specific parser.
Only the existing LoopX executor can write the fixture's authority stores.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any


_CLI_CLIENT = """import json, os, socket, sys
with socket.socket(socket.AF_UNIX) as connection:
    connection.connect(os.environ['LOOPX_FIXTURE_SOCKET'])
    connection.sendall(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}).encode())
    connection.shutdown(socket.SHUT_WR)
    with connection.makefile('rb') as stream:
        result = json.load(stream)
sys.stdout.write(result['output'])
sys.exit(result['exit_code'])
"""


def shell_isolation_available() -> bool:
    return (sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file()) or bool(shutil.which("bwrap"))


class VisionShellHost:
    """One fixture, a normal shell, and an argv-only real-CLI bridge."""

    def __init__(self, project: Path, invoke: Callable[[list[str], Path, str], str], *, turn_instance_id: str) -> None:
        if not shell_isolation_available():
            raise RuntimeError("Native qualification needs sandbox-exec (macOS) or bubblewrap (Linux); unrestricted execution is not a fallback")
        self.project = project.resolve()
        self.fixture_root = self.project.parent
        self.invoke = invoke
        self.turn_instance_id = turn_instance_id
        # Keep the socket short enough for AF_UNIX, and outside writable actor scope.
        self.control = tempfile.TemporaryDirectory(prefix="lx-shell-", dir="/tmp")
        self.control_path = Path(self.control.name).resolve()
        self.socket_path = self.control_path / "cli.sock"
        self.scratch = self.project / ".scratch"
        self.originals = [path.resolve() for path in self.project.iterdir()]
        self.scratch.mkdir()
        client = self.control_path / "loopx"
        client.write_text(f"#!{sys.executable}\n" + _CLI_CLIENT, encoding="utf-8")
        client.chmod(0o700)
        self._output: Any = None
        self.cli_calls: list[dict[str, Any]] = []
        self._invocation_lock = threading.Lock()
        self._active = False
        host = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                try:
                    request = json.load(self.rfile)
                    argv, cwd = request["argv"], Path(request["cwd"]).resolve()
                    if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv) or not cwd.is_relative_to(host.project):
                        raise ValueError("CLI request must use literal argv from the isolated project")
                    with host._invocation_lock:
                        if not host._active:
                            raise ValueError("The shell command has ended; no later CLI effect is admitted")
                        output = host.invoke(argv, cwd, host.output())
                    result = {"output": output, "exit_code": 0}
                except (ValueError, RuntimeError, OSError) as exc:
                    detail = getattr(exc, "output", str(exc))
                    if getattr(exc, "detail", ""):
                        detail += ": " + exc.detail
                    result = {"output": detail, "exit_code": getattr(exc, "returncode", getattr(exc, "exit_code", 1))}
                host.cli_calls.append({"exit_code": result["exit_code"]})
                self.wfile.write(json.dumps(result).encode())

        self.server = socketserver.UnixStreamServer(str(self.socket_path), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _sandbox(self) -> list[str]:
        readable = [Path(path).resolve() for path in ("/System", "/Library/Apple", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/opt/homebrew/Cellar", sys.base_prefix, sys.prefix) if Path(path).exists()]
        readable += [self.fixture_root, self.control_path]
        if sys.platform == "darwin":
            clauses = ["(version 1)", "(allow default)", "(deny file-read-data)", "(deny file-write*)", "(deny network*)", "(deny signal)", "(deny process-info*)", "(allow process-info* (target self))"]
            clauses += [f"(allow file-read-data (subpath {json.dumps(str(path))}))" for path in readable]
            clauses += ["(allow file-read-data (literal \"/\") (subpath \"/dev\"))", "(allow file-write-data (literal \"/dev/null\"))"]
            clauses += [f"(allow file-write* (subpath {json.dumps(str(self.project))}))"]
            clauses += [f"(deny file-write* (subpath {json.dumps(str(path))}))" for path in self.originals]
            clauses += [f"(allow network* (remote unix-socket (literal {json.dumps(str(self.socket_path))})))"]
            return ["/usr/bin/sandbox-exec", "-p", "\n".join(clauses)]
        argv = [str(shutil.which("bwrap")), "--unshare-all", "--die-with-parent", "--new-session", "--proc", "/proc", "--dev", "/dev"]
        for path in dict.fromkeys(readable):
            argv += ["--ro-bind", str(path), str(path)]
        # usr-merged Linux needs the original loader and shell aliases too.
        for name in ("/bin", "/sbin", "/lib", "/lib64"):
            if Path(name).is_symlink():
                argv += ["--symlink", os.readlink(name), name]
        argv += ["--bind", str(self.project), str(self.project)]
        for path in self.originals:
            argv += ["--ro-bind", str(path), str(path)]
        return argv

    def output(self) -> str:
        # pread does not move the shared offset used by the shell's stdout.
        if self._output is None:
            return ""
        return os.pread(self._output.fileno(), 128_000, 0).decode("utf-8", errors="replace")

    def execute(self, command: str) -> tuple[str, int]:
        self.cli_calls = []
        env = {"PATH": os.pathsep.join((str(self.control_path), str(Path(sys.executable).parent), "/opt/homebrew/bin", "/usr/bin", "/bin")),
               "HOME": str(self.fixture_root / "home"), "TMPDIR": str(self.scratch), "LC_ALL": "en_US.UTF-8", "SHELL": "/bin/sh",
               "PYTHONDONTWRITEBYTECODE": "1", "LOOPX_FIXTURE_SOCKET": str(self.socket_path), "LOOPX_TURN": self.turn_instance_id}
        with tempfile.TemporaryFile() as output:
            self._output = output
            with self._invocation_lock:
                self._active = True
            process = subprocess.Popen([*self._sandbox(), "/bin/sh", "-c", command], cwd=self.project, env=env,
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                code = 124
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                with self._invocation_lock:
                    self._active = False
            result = self.output()
            self._output = None
        return result, code

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.control.cleanup()
