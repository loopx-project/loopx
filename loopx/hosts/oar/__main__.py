"""Install or inspect OAR, or execute a signed Turn with the bundled Pi runtime."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys

from ...control_plane.turn_driver.host_failure import BuiltInHostError
from ...entrypoint import pin_utf8_stdio
from . import installation
from .turn import OarTurnHostConfig, run_oar_host


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        pin_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install", help="Install the packaged OAR lock into the private runtime cache.")
    commands.add_parser("status", help="Inspect the optional runtime without installing dependencies.")
    turn = commands.add_parser(
        "turn", help="Run one signed generic-cli request using Pi's native tools.",
        description=(
            "Read one signed LoopX Turn request from stdin. Pi uses its configured "
            "models and native tools with the operating-system permissions of this "
            "process; this explicit host selection does not provide an OS sandbox."
        ),
    )
    turn.add_argument("--runtime", choices=("pi",), required=True)
    turn.add_argument("--model", help="Native provider/model selector; defaults to Pi's configured model.")
    turn.add_argument("--effort", help="Native thinking level supported by the selected Pi model.")
    turn.add_argument("--cwd", type=Path, default=Path.cwd())
    turn.add_argument("--timeout-seconds", type=float, default=300.0)
    args = parser.parse_args(argv)
    if args.command in {"install", "status"}:
        try:
            result = installation.install() if args.command == "install" else installation.inspect_installation()
        except (OSError, RuntimeError, ValueError):
            print("OAR installation failed; check Node.js 24+, npm and the runtime cache permissions.", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
        return 0 if result["ready"] else 1
    try:
        request = json.load(sys.stdin)
    except (ValueError, UnicodeError):
        print("OAR Turn host requires one JSON request on stdin.", file=sys.stderr)
        return 2
    try:
        result = run_oar_host(request, config=OarTurnHostConfig(
            runtime=args.runtime, cwd=args.cwd, model=args.model,
            effort=args.effort, timeout_seconds=args.timeout_seconds,
        ))
    except BuiltInHostError as exc:
        print(
            f"OAR Pi host failed: {exc.reason} ({exc.failure_kind}). "
            "Inspect `python -m loopx.hosts.oar status` and the selected Pi provider configuration.",
            file=sys.stderr,
        )
        return 2 if exc.failure_kind == "contract_rejected" else 1
    except (OSError, RuntimeError, TypeError, ValueError):
        print("OAR Turn request or result was rejected; inspect the signed request and native configuration.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
