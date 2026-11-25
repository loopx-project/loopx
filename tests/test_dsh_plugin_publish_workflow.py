from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/dsh-plugin-publish.yml"
README_PATH = REPO_ROOT / "packages/dsh-loopx-plugin/README.md"


def test_dsh_plugin_publish_workflow_allows_tag_or_main_dispatch_with_relative_artifact_path() -> None:
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)

    assert "workflow_dispatch" in parsed[True]
    release_input = parsed[True]["workflow_dispatch"]["inputs"]["release_tag"]
    assert release_input["required"] is True
    assert release_input["type"] == "string"

    job = parsed["jobs"]["publish"]
    condition = job["if"]
    assert "github.repository == 'loopx-project/loopx'" in condition
    assert "github.ref_type == 'tag' && github.ref_name == inputs.release_tag" in condition
    assert "github.ref == 'refs/heads/main' && startsWith(inputs.release_tag, 'dsh-loopx-plugin-v')" in condition

    steps = job["steps"]
    checkout = next(step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "refs/tags/${{ inputs.release_tag }}"
    assert checkout["with"]["fetch-depth"] == 0
    setup = next(step for step in steps if str(step.get("uses", "")).startswith("actions/setup-node@"))
    assert setup["with"]["registry-url"] == "https://registry.npmjs.org"
    assert setup["with"]["package-manager-cache"] is False

    identity_run = next(
        step["run"]
        for step in steps
        if step.get("name") == "Require a merged, published release with matching package identity"
    )
    assert "git merge-base --is-ancestor HEAD origin/main" in identity_run
    assert "DSH_ARTIFACT=./output/${manifest.name}-${manifest.version}.tgz" in identity_run
    assert re.search(r"DSH_ARTIFACT=output/", raw) is None

    download_step = next(
        step for step in steps if step.get("name") == "Download the immutable GitHub package"
    )
    assert "DSH_NPM_PACKAGE_MISSING=" in download_step["run"]
    assert "DSH_NPM_STAGING_PLACEHOLDER=" in download_step["run"]

    publish_step = next(
        step for step in steps if step.get("name") == "Publish the same bytes with npm trusted publishing"
    )
    assert 'npm publish "$DSH_ARTIFACT" --access public --tag latest --ignore-scripts --provenance' in publish_step["run"]
    assert "Allow npm publish checked" in publish_step["run"]
    assert "blank Environment name" in publish_step["run"]

    verify_step = next(
        step for step in steps if step.get("name") == "Read back npm bytes, latest, and marketplace repository discovery"
    )
    assert "git show origin/main:packages/dsh-loopx-plugin/scripts/verify-distribution.mjs > scripts/verify-distribution.mjs" in verify_step["run"]
    assert 'node scripts/verify-distribution.mjs --tarball "$DSH_ARTIFACT"' in verify_step["run"]

    readme = README_PATH.read_text(encoding="utf-8")
    normalized_readme = " ".join(readme.split())
    assert "on the existing published plugin tag or on `main`" in normalized_readme
    assert "refs/tags/<release_tag>" in normalized_readme
    assert "Allow npm publish" in normalized_readme


@pytest.mark.skipif(os.name != "posix" or shutil.which("bash") is None, reason="The hosted step uses Bash on a POSIX runner")
def test_dsh_publish_reports_the_authentication_stage_without_disclosing_trace(tmp_path, monkeypatch) -> None:
    steps = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))["jobs"]["publish"]["steps"]
    publish = next(step["run"] for step in steps if step.get("name") == "Publish the same bytes with npm trusted publishing")
    fake_npm = tmp_path / "npm"
    fake_npm.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'npm verbose oidc Failed token exchange request with body message: synthetic-private-trace'\nexit 17\n",
        encoding="utf-8",
    )
    fake_npm.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setenv("DSH_ARTIFACT", "./output/immutable.tgz")
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    missing = subprocess.run(["bash", "-e", "-c", publish], check=False, capture_output=True, text=True)
    assert missing.returncode == 1
    assert "GitHub OIDC unavailable" in missing.stdout

    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://example.invalid/oidc")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "synthetic-token")
    rejected = subprocess.run(["bash", "-e", "-c", publish], check=False, capture_output=True, text=True)
    assert rejected.returncode == 17
    assert "npm rejected its token exchange" in rejected.stdout
    assert "synthetic-private-trace" not in rejected.stdout + rejected.stderr
