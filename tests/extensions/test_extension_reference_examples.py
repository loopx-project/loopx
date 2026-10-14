"""Full example manifests in the extensions reference must load.

A reader copies the reference example as a starting point. If it fails
`load_extension_manifest`, the first `loopx extension install` is rejected and
the documentation has taught the wrong contract. Fragments that illustrate one
table (no `schema_version`) are not manifests and are skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from loopx.extensions.manifest import load_extension_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCE = REPO_ROOT / "docs" / "reference" / "extensions.md"
TOML_BLOCK = re.compile(r"```toml\n(.*?)```", re.S)


def _full_manifest_blocks() -> list[str]:
    text = REFERENCE.read_text(encoding="utf-8")
    return [block for block in TOML_BLOCK.findall(text) if "schema_version" in block]


def test_reference_has_a_full_example_manifest() -> None:
    assert _full_manifest_blocks(), "extensions reference lost its full example manifest"


@pytest.mark.parametrize("block", _full_manifest_blocks())
def test_reference_example_manifest_loads(tmp_path: Path, block: str) -> None:
    path = tmp_path / "extension.toml"
    path.write_text(block, encoding="utf-8")

    manifest = load_extension_manifest(path)

    provider = manifest["provider"]
    assert provider["id"]
    for adapter in manifest.get("hook_adapters") or []:
        assert set(adapter["required_permissions"]) <= set(provider["permissions"])
