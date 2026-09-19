#!/usr/bin/env python3
"""Exercise the public release artifact identity and checksum contract."""

from __future__ import annotations

import importlib.util
import gzip
import hashlib
import io
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_artifacts.py"
SPEC = importlib.util.spec_from_file_location("release_artifacts", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release_artifacts = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_artifacts
SPEC.loader.exec_module(release_artifacts)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(ROOT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def write_sdist(path: Path, *, mtime: int) -> None:
    with path.open("wb") as raw_stream:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_stream, mtime=mtime) as gzip_stream:
            with tarfile.open(fileobj=gzip_stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
                payload = b"fixture package metadata\n"
                member = tarfile.TarInfo("loopx-fixture/PKG-INFO")
                member.size = len(payload)
                member.mtime = mtime
                archive.addfile(member, io.BytesIO(payload))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    identity = release_artifacts.load_identity(ROOT)
    assert identity.name == "loopx", identity
    assert run("expected-tag").stdout.strip() == identity.tag
    assert run("validate-tag", identity.tag).returncode == 0

    project_metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert project_metadata["urls"] == {
        "Homepage": "https://loopx-project.github.io/loopx/",
        "Documentation": "https://loopx-project.github.io/loopx/docs/",
        "Repository": "https://github.com/loopx-project/loopx",
        "Issues": "https://github.com/loopx-project/loopx/issues",
        "Changelog": "https://github.com/loopx-project/loopx/releases",
    }

    invalid_tag = run("validate-tag", "v999.0.0")
    assert invalid_tag.returncode == 2, invalid_tag
    assert "does not match package tag" in invalid_tag.stderr, invalid_tag.stderr

    with tempfile.TemporaryDirectory(prefix="loopx-release-artifacts-") as temporary:
        root = Path(temporary)
        dist_dir = root / "packages"
        dist_dir.mkdir()
        wheel = dist_dir / f"loopx-{identity.version}-py3-none-any.whl"
        sdist = dist_dir / f"loopx-{identity.version}.tar.gz"
        wheel.write_bytes(b"wheel fixture\n")
        write_sdist(sdist, mtime=1_700_000_001)
        checksums = root / "SHA256SUMS"

        normalized = run(
            "normalize-sdist",
            "--dist-dir",
            str(dist_dir),
            "--source-date-epoch",
            "1700000000",
        )
        assert normalized.returncode == 0, normalized
        normalized_digest = digest(sdist)
        write_sdist(sdist, mtime=1_700_000_099)
        normalized_again = run(
            "normalize-sdist",
            "--dist-dir",
            str(dist_dir),
            "--source-date-epoch",
            "1700000000",
        )
        assert normalized_again.returncode == 0, normalized_again
        assert digest(sdist) == normalized_digest

        written = run(
            "write-checksums",
            "--dist-dir",
            str(dist_dir),
            "--output",
            str(checksums),
        )
        assert written.returncode == 0, written
        manifest = checksums.read_text(encoding="ascii")
        lines = manifest.splitlines()
        assert len(lines) == 2, lines
        assert lines == sorted(lines, key=lambda line: line.split("  ", 1)[1]), lines
        assert all(len(line.split("  ", 1)[0]) == 64 for line in lines), lines

        verified = run(
            "verify-checksums",
            "--dist-dir",
            str(dist_dir),
            "--checksum-file",
            str(checksums),
        )
        assert verified.returncode == 0, verified

        wheel.write_bytes(b"tampered wheel\n")
        tampered = run(
            "verify-checksums",
            "--dist-dir",
            str(dist_dir),
            "--checksum-file",
            str(checksums),
        )
        assert tampered.returncode == 2, tampered
        assert "does not match release distributions" in tampered.stderr, tampered.stderr

        extra = dist_dir / "unexpected.txt"
        extra.write_text("not a release asset\n", encoding="utf-8")
        rejected = run(
            "write-checksums",
            "--dist-dir",
            str(dist_dir),
            "--output",
            str(checksums),
        )
        assert rejected.returncode == 2, rejected
        assert "unexpected files" in rejected.stderr, rejected.stderr

    workflow = (ROOT / ".github" / "workflows" / "release-artifacts.yml").read_text(
        encoding="utf-8"
    )
    required_contract = (
        "release:\n    types: [published]",
        "python scripts/release_artifacts.py validate-tag",
        "python scripts/release_artifacts.py normalize-sdist",
        "python scripts/release_artifacts.py write-checksums",
        "python scripts/release_artifacts.py verify-checksums",
        "uses: actions/attest@v4",
        "gh release upload",
        "vars.PYPI_PUBLISH_ENABLED == 'true'",
        "environment:\n      name: pypi",
        "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
        "Verify the published package from PyPI",
        "--index-url https://pypi.org/simple",
        '"loopx==${version}"',
        'test "$(PYTHONPATH="${verify_target}" python -m loopx.cli --version)" =',
        "workflow-skills --install --skills-dir",
        "workflow-skills --uninstall --skills-dir",
        'payload["source"]["kind"] == "python_distribution"',
        "Verify wheel through the DSH managed-runtime install shape",
        '--target "${target}"',
        'loopx-wheel-target-inspect.json',
        'verify_target="${RUNNER_TEMP}/loopx-pypi-target-${version}"',
        'loopx-pypi-workflow-skills.json',
        '"schema_version": "quota_settlement_plan_v1"',
        '"identity": journal_identity.as_dict()',
    )
    for text in required_contract:
        assert text in workflow, text
    assert "password:" not in workflow
    assert "--clobber" not in workflow

    official_repository = "github.repository == 'loopx-project/loopx'"
    owner_gated_workflows = (
        "release-artifacts.yml",
        "desktop-release-artifacts.yml",
        "desktop-updater.yml",
        "full-public-smokes.yml",
        "update-notes.yml",
    )
    for workflow_name in owner_gated_workflows:
        owner_gated_workflow = (
            ROOT / ".github" / "workflows" / workflow_name
        ).read_text(encoding="utf-8")
        assert official_repository in owner_gated_workflow, workflow_name
        assert "github.repository == 'huangruiteng/loopx'" not in owner_gated_workflow, (
            workflow_name
        )

    print("release-artifacts-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
