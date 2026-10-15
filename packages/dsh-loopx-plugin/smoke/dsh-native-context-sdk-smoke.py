#!/usr/bin/env python3
"""Load the optional native context module in the real Python SDK, without a Turn.

Run with an interpreter containing deepseek-harness. This checks the runtime's
actual tool/schema registration, which a structural service fake cannot check.
No model request, Core binding, approval or account operation is performed.
"""

import argparse
import json
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    module = args.module.resolve(strict=True)
    from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

    with tempfile.TemporaryDirectory(prefix="dsh-native-context-smoke-") as directory:
        root = Path(directory)
        # Keep SDK stdout ownership intact. The wrapper forbids model requests
        # even if SDK initialization behavior changes in a later release.
        wrapper = root / "smoke.mjs"
        wrapper.write_text(
            "import {apply as register} from " + json.dumps(module.as_uri()) + ";\n"
            "export const name='native-context-startup-smoke';\n"
            "export const inject=['agents','tools','sessionProjections'];\n"
            "export function apply(ctx) { register(ctx);\n"
            "ctx.on('agent/request', () => { throw new Error('model requests forbidden in startup smoke') });\n"
            "}\n",
            encoding="utf-8",
        )
        patch = root / "smoke.patch.yml"
        patch.write_text(
            "- insert:\n    - id: native-context-startup-smoke\n      name: "
            + json.dumps(wrapper.as_uri()) + "\n",
            encoding="utf-8",
        )
        config = DeepSeekHarnessConfig(
            provider=args.provider,
            model=args.model,
            cwd=str(root),
            dsh_home=str(root / "sdk-home"),
            patches=(str(patch),),
            # Never expose a caller's genuine provider credential to this smoke.
            api_key="startup-smoke-placeholder",
            base_url="http://127.0.0.1:9",
            initialize_timeout_seconds=30,
            request_timeout_seconds=5,
        )
        with DeepSeekHarness(config):
            print(json.dumps({"initialized": True, "model_request_count": 0}))


if __name__ == "__main__":
    main()
