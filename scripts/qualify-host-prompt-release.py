#!/usr/bin/env python3
"""Explicit release opt-in; never called by default PR CI or canaries."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loopx.control_plane.testing.doubao_model_behavior_actor import (  # noqa: E402
    ARK_API_KEY_ENV,
    DEEPSEEK_API_KEY_ENV,
    DEEPSEEK_BASE_URL_ENV,
    DEEPSEEK_FLASH_MODEL,
    DOUBAO_SEED_EVOLVING_MODEL,
    _direct_ark_transport,
    _direct_deepseek_transport,
    resolve_deepseek_chat_completions_endpoint,
)
from loopx.control_plane.testing.host_prompt_behavior import run_probe  # noqa: E402
from loopx.control_plane.testing.model_tool_behavior import (  # noqa: E402
    DeepSeekExecToolClient,
    DoubaoExecToolClient,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-live", action="store_true")
    parser.add_argument("--provider", choices=("ark", "deepseek"), default="ark")
    parser.add_argument("--model", default=None)
    parser.add_argument("--repeats", type=int, choices=range(1, 6), default=2)
    args = parser.parse_args(argv)
    if not args.release_live:
        print(json.dumps({"status": "skipped", "reason": "release_opt_in_required", "provider_call_count": 0}))
        return 0
    if args.provider == "deepseek":
        key = os.environ.get(DEEPSEEK_API_KEY_ENV, "")
        if not key.strip():
            print(json.dumps({"status": "skipped", "reason": "provider_credential_unavailable", "provider_call_count": 0}))
            return 0
    else:
        key = os.environ.get(ARK_API_KEY_ENV, "")
        if not key.strip():
            print(json.dumps({"status": "skipped", "reason": "provider_credential_unavailable", "provider_call_count": 0}))
            return 0
    try:
        if args.provider == "deepseek":
            endpoint = resolve_deepseek_chat_completions_endpoint(os.environ.get(DEEPSEEK_BASE_URL_ENV))
            client: DoubaoExecToolClient = DeepSeekExecToolClient(
                api_key=key,
                model=args.model or DEEPSEEK_FLASH_MODEL,
                timeout_seconds=90,
                transport=_direct_deepseek_transport,
                endpoint=endpoint,
            )
        else:
            client = DoubaoExecToolClient(
                api_key=key,
                model=args.model or DOUBAO_SEED_EVOLVING_MODEL,
                timeout_seconds=90,
                transport=_direct_ark_transport,
            )
        report = run_probe(client, repeats=args.repeats)
    except Exception:
        # Never publish a transport exception, raw response or credential.
        print(json.dumps({"status": "failed", "reason": "probe_execution_failed"}))
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report["qualification_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
