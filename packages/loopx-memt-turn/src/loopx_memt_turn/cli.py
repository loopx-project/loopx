from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from .config import AdapterError, decode_document, load_config
from .runtime import dependency_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in MemT Nano host; no core defaults change.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--doctor", action="store_true", help="Local checks only; no inference or server requests.")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.doctor:
            checks = dependency_status()
            assets = (Path(config.tokenizer) / "config.json").is_file()
            ready = all(checks.values()) and assets
            report = {"ok": ready or not config.enabled, "enabled": config.enabled,
                      "client_dependencies": checks, "local_assets_present": assets,
                      "network_checked": False, "model_inference_verified": False}
        else:
            from .host import run

            if config.enabled and Path.cwd().resolve() != Path(config.workspace).resolve():
                raise AdapterError("workspace_binding_mismatch")
            request = decode_document(sys.stdin.buffer.read(256_001))
            report = asyncio.run(run(request, config))
    except Exception as error:
        # The CLI is a privacy boundary: even SDK/MCP exception groups become explicit failures without payloads.
        reason = str(error) if isinstance(error, AdapterError) else type(error).__name__
        report = {"ok": False, "error": reason}
    json.dump(report, sys.stdout, ensure_ascii=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 1 if report.get("ok") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
