"""Packaged UI fixture with the real Chat inspection route and a synthetic TLS peer."""

import argparse
import json
import os
from pathlib import Path
import sys

from examples.control_plane.quota_plan_fixtures import SCOPED_AGENT_ID, write_cli_fixture
from loopx.chat_server import default_chat_assets_dir
from loopx.presentation.chat_bundle import validate_bundle
from tests.control_plane.remote_authority_inspection_fixture import (
    CREDENTIAL, bind_remote_authority, remote_authority_service,
)
from tests.test_chat_remote_authority_inspection import inspection_api


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--assets", type=Path)
    args = parser.parse_args()
    assets = args.assets or default_chat_assets_dir()
    validate_bundle(assets, source_root=None if args.assets else Path(__file__).resolve().parents[2])
    registry, runtime, _ = write_cli_fixture(args.root, scoped_agents=True)
    document = json.loads(registry.read_text())
    for goal in document["goals"]:
        if goal["id"] == "full-speed":
            goal["id"] = "product-release"
    registry.write_text(json.dumps(document))
    os.environ["LOOPX_AUTHORITY_CREDENTIAL"] = CREDENTIAL
    with remote_authority_service(args.root, SCOPED_AGENT_ID) as (config, control), inspection_api(
        registry, runtime, assets=assets,
    ) as origin:
        bind_remote_authority(registry, "product-release", config)
        before = {p: p.read_bytes() for p in args.root.rglob("*") if p.is_file()}
        print(json.dumps({"origin": origin}), flush=True)
        for line in sys.stdin:
            command = json.loads(line)
            if "mode" in command:
                control["mode"] = command["mode"]
            print(json.dumps({"requests": len(control["requests"]), "unchanged": before == {
                p: p.read_bytes() for p in args.root.rglob("*") if p.is_file()
            }}), flush=True)


if __name__ == "__main__":
    main()
