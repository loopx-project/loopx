#!/usr/bin/env python3
"""Validate docs assets integrity, existence, and deduplication."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    guide_path = repo_root / "docs" / "guides" / "personal-workspace-user-guide.md"
    assets_dir = repo_root / "docs" / "assets" / "personal-workspace"

    assert guide_path.is_file(), f"guide doc not found at {guide_path}"
    assert assets_dir.is_dir(), f"assets directory not found at {assets_dir}"

    guide_text = guide_path.read_text(encoding="utf-8")

    # Find all referenced assets in markdown
    md_image_refs = re.findall(r"!\[.*?\]\(\.\./assets/personal-workspace/([a-zA-Z0-9_\-\.]+)\)", guide_text)
    html_src_refs = re.findall(r'src="\.\./assets/personal-workspace/([a-zA-Z0-9_\-\.]+)"', guide_text)
    all_refs = sorted(set(md_image_refs + html_src_refs))

    assert len(all_refs) >= 6, f"expected at least 6 asset references in guide, found {len(all_refs)}: {all_refs}"

    # Verify each asset exists, is non-empty, and check SHA-256 uniqueness
    hashes: dict[str, str] = {}
    for ref in all_refs:
        target_file = assets_dir / ref
        assert target_file.is_file(), f"referenced asset does not exist: {target_file}"
        data = target_file.read_bytes()
        assert len(data) > 1024, f"asset {ref} is suspiciously small ({len(data)} bytes)"

        if ref.endswith(".png"):
            file_hash = hashlib.sha256(data).hexdigest()
            assert file_hash not in hashes, (
                f"Duplicate asset detected: {ref} has identical SHA-256 hash to {hashes[file_hash]} ({file_hash})"
            )
            hashes[file_hash] = ref

    # Public boundary scan on guide text
    forbidden_tokens = ["Users/chou", "localhost:", "Cookie:", "session-private"]
    for token in forbidden_tokens:
        assert token not in guide_text, f"guide text contains private/local token: {token}"

    print(f"docs-asset-integrity-smoke: ok ({len(all_refs)} assets verified, all PNG hashes distinct)")


if __name__ == "__main__":
    main()
