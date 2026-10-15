#!/usr/bin/env python3
"""Exercise the public release artifact identity and checksum contract."""

from __future__ import annotations

import ast
import gzip
import hashlib
import io
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
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


def workflow_run(workflow: str, step: str) -> str:
    """Execute the shipped block, rather than a second implementation of it."""
    section = workflow.split(f"      - name: {step}\n", 1)[1]
    lines = section.split("        run: |\n", 1)[1].splitlines()
    body = []
    for line in lines:
        if line and not line.startswith("          "):
            break
        body.append(line)
    return textwrap.dedent("\n".join(body)) + "\n"


def fixture_recovery_contract(workflow: str, identity: release_artifacts.ReleaseIdentity) -> None:
    paths = (
        "examples/personal-workspace-browser/fixture.mjs",
        "examples/personal-workspace-browser/typed-actions.mjs",
        "examples/personal-workspace-browser/goal-work-map.mjs",
        "examples/personal-workspace-browser/goal-workspace-m2.mjs",
    )
    recover = workflow_run(workflow, "Resolve browser fixture correction without rebuilding product bytes")
    restore = workflow_run(workflow, "Restore browser fixtures and bind verified package checksums")
    source = recover.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    allowed = next(node.value for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "allowed" for target in node.targets))
    assert ast.literal_eval(allowed) == set(paths)
    assert workflow.index("Build wheel and source distribution") < workflow.index("Resolve browser fixture correction")
    assert workflow.index("Restore browser fixtures") < workflow.index("Attest release packages")
    with tempfile.TemporaryDirectory(prefix="loopx-release-recovery-") as temporary:
        root = Path(temporary)

        def git(*args: str) -> str:
            return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

        def commit(message: str) -> str:
            git("add", "--", *paths, "product.txt")
            git("commit", "--quiet", "-m", message)
            return git("rev-parse", "HEAD")

        git("init", "--quiet")
        git("config", "user.name", "Release fixture smoke")
        git("config", "user.email", "fixture@example.invalid")
        for path in paths:
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text("tagged fixture\n")
        (root / "product.txt").write_text("immutable product\n")
        product = commit("product")
        for path in paths:
            (root / path).write_text("corrected fixture\n")
        (root / "product.txt").write_text("unrelated product change\n")
        correction = commit("merged fixture correction and unrelated product change")
        git("update-ref", "refs/remotes/origin/main", correction)
        git("checkout", "--quiet", "--detach", product)
        packages = root / "dist/packages"
        packages.mkdir(parents=True)
        (packages / f"loopx-{identity.version}-py3-none-any.whl").write_bytes(b"immutable wheel\n")
        (packages / f"loopx-{identity.version}.tar.gz").write_bytes(b"immutable sdist\n")
        (root / "dist/SHA256SUMS").write_text(release_artifacts.render_manifest(tuple(sorted(packages.iterdir()))))
        (root / "scripts").mkdir()
        shutil.copyfile(SCRIPT, root / "scripts/release_artifacts.py")
        shutil.copyfile(ROOT / "pyproject.toml", root / "pyproject.toml")
        (root / "loopx").mkdir()
        shutil.copyfile(ROOT / "loopx/__init__.py", root / "loopx/__init__.py")
        artifacts = [*packages.iterdir(), root / "dist/SHA256SUMS"]
        before = {path.name: digest(path) for path in artifacts}
        env = {**os.environ, "RELEASE_TAG": identity.tag, "GITHUB_SHA": correction,
               "GITHUB_WORKFLOW_REF": "fixture/release-artifacts.yml@reviewed"}

        def execute(block: str, fixture: str = "") -> subprocess.CompletedProcess[str]:
            return subprocess.run(["bash", "-euc", block], cwd=root,
                                  env={**env, "BROWSER_FIXTURE_COMMIT": fixture},
                                  capture_output=True, text=True)

        for selected in ("", correction):
            result = execute(recover, selected)
            assert result.returncode == 0, result
            record = json.loads((root / "dist/RELEASE-SOURCES.json").read_text())
            assert record["product_commit"] == product
            assert record["browser_fixture_commit"] == (selected or product)
            assert record["browser_fixture_paths"] == (sorted(paths) if selected else [])
            assert (root / "product.txt").read_text() == "immutable product\n"
            assert all((root / path).read_text() == ("corrected fixture\n" if selected else "tagged fixture\n") for path in paths)
            result = execute(restore)
            assert result.returncode == 0, result
            assert git("diff", "--name-only") == ""
            assert {path.name: digest(path) for path in artifacts} == before
            assert json.loads((root / "dist/RELEASE-SOURCES.json").read_text())["sha256"] == before

        # Invalid candidates must fail before copying even an earlier valid path.
        candidates = [(correction[:12], "full commit SHA"), ("f" * 40, "")]
        (root / paths[-1]).write_text("unmerged correction\n")
        unmerged = commit("unmerged fixture")
        candidates.append((unmerged, ""))
        git("checkout", "--quiet", "--detach", product)
        (root / "product.txt").write_text("only unrelated change\n")
        unrelated = commit("unrelated merged commit")
        git("update-ref", "refs/remotes/origin/main", unrelated)
        candidates.append((unrelated, "no correction"))
        for kind in ("symlink", "deleted"):
            git("checkout", "--quiet", "--detach", product)
            (root / paths[0]).write_text("must not be partially copied\n")
            (root / paths[-1]).unlink()
            if kind == "symlink":
                (root / paths[-1]).symlink_to("fixture.mjs")
            invalid = commit(kind)
            git("update-ref", "refs/remotes/origin/main", invalid)
            candidates.append((invalid, "regular source file"))
        # Keep each rejected candidate merged except the explicit unmerged case.
        git("merge", "--quiet", "-s", "ours", "--allow-unrelated-histories", unrelated, "-m", "merged candidates")
        git("merge", "--quiet", "-s", "ours", candidates[-2][0], "-m", "merged symlink candidate")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        git("checkout", "--quiet", "--detach", product)
        for candidate, error in candidates:
            result = execute(recover, candidate)
            assert result.returncode != 0, candidate
            assert error in result.stderr, result
            assert git("diff", "--name-only") == "", candidate
            assert {path.name: digest(path) for path in artifacts} == before


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
    fixture_recovery_contract(workflow, identity)
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
