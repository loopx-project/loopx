from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import __version__


def pin_utf8_stdio() -> None:
    """Pin a LoopX-owned process's own stdio to UTF-8.

    Without this the process inherits the host locale codec - `cp936` on a
    zh-CN Windows host. Non-ASCII output then raises `UnicodeEncodeError`, and
    UTF-8 stdin is decoded with the locale codec. LoopX already pins UTF-8 for
    every text-mode file read/write and subprocess call (see
    `tests/test_loopx_text_io_utf8.py` and `tests/test_runtime_subprocess_utf8.py`);
    the streams of the processes LoopX starts - the shipped CLI and the bundled
    extension entrypoints that answer its JSON-over-stdio transport - are the
    remaining locale-dependent surface. A real console and a stream that is
    already UTF-8 are left untouched.

    Input and output keep separate error handling. `stdin` decodes strictly so
    a malformed request is rejected exactly like the `--metadata-json <file>`
    route instead of being silently repaired into valid JSON; `stdout`/`stderr`
    use `replace` so an unencodable character cannot abort an already-computed
    result.
    """

    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        if str(getattr(stream, "encoding", "") or "").lower().replace("_", "-") == "utf-8":
            continue
        errors = "strict" if stream is sys.stdin else "replace"
        try:
            reconfigure(encoding="utf-8", errors=errors)
        except (OSError, ValueError):
            continue


def _bound_option_present(argv: list[str], option: str) -> bool:
    for index, value in enumerate(argv):
        if value.startswith(f"{option}=") and value != f"{option}=":
            return True
        if value == option and index + 1 < len(argv):
            return bool(str(argv[index + 1]).strip())
    return False


def _native_scheduler_followup_argv(raw_argv: list[str]) -> list[str] | None:
    """Select only generated, receipt-bound scheduler follow-up commands."""

    # The selected Python dispatcher owns the existing outer-controller guard.
    # Do not process-replace it before it can reject a model-visible write.
    if os.environ.get("LOOPX_KUNLUNCODE_OUTER_CONTROLLER") == "1":
        return None

    value_options = {"--format", "--registry", "--runtime-root"}
    positionals: list[str] = []
    skip_value = False
    for value in raw_argv:
        if skip_value:
            skip_value = False
            continue
        option = value.split("=", 1)[0]
        if option in value_options:
            skip_value = "=" not in value
            continue
        if value.startswith("-"):
            continue
        positionals.append(value)
        if len(positionals) == 2:
            break
    if positionals not in (
        ["quota", "scheduler-ack-current"],
        ["quota", "scheduler-fail-current"],
    ):
        return None
    if not _bound_option_present(raw_argv, "--scheduler-host-facts-chunk"):
        return None
    if not _bound_option_present(raw_argv, "--turn-instance-id"):
        return None
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("native scheduler follow-up requires Node.js 22.22.3 or newer")
    entry = (
        Path(__file__).resolve().parent
        / "control_plane"
        / "scheduler"
        / "heartbeat_followup_cli.ts"
    )
    return [
        node,
        "--no-warnings",
        "--experimental-strip-types",
        str(entry),
        *raw_argv,
    ]


def main(argv: list[str] | None = None) -> int:
	"""Keep the version path tiny, then load the selected CLI runtime."""

	raw_argv = sys.argv[1:] if argv is None else list(argv)
	if argv is None:
		pin_utf8_stdio()
	if raw_argv == ["--version"]:
		print(f"loopx {__version__}")
		return 0
	if argv is None:
		try:
			native_argv = _native_scheduler_followup_argv(raw_argv)
		except RuntimeError as exc:
			print(f"loopx runtime error: {exc}", file=sys.stderr)
			return 2
		if native_argv is not None:
			os.execv(native_argv[0], native_argv)

	from .cli_runtime import main as runtime_main

	return runtime_main(argv)


if __name__ == "__main__":
	raise SystemExit(main())
