#!/usr/bin/env python3
"""Exercise the public release artifact identity and checksum contract."""

from __future__ import annotations

import ast
import copy
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
        "examples/personal-workspace-browser/lark-cli-missing.mjs",
        "examples/personal-workspace-browser/pr-review-agent-order.mjs",
        "apps/presentation/dashboard/smoke/workspace-locale-browser-smoke.mjs",
    )
    recover = workflow_run(workflow, "Resolve browser fixture correction without rebuilding product bytes")
    restore = workflow_run(workflow, "Restore browser fixtures and bind verified package checksums")
    adopt = workflow_run(workflow, "Verify candidate provenance and adopt only selected fixture corrections").split("python -m venv", 1)[0]
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
            source_digest = digest(root / "dist/RELEASE-SOURCES.json")
            result = subprocess.run(["bash", "-euc", adopt], cwd=root, capture_output=True, text=True,
                                    env={**env, "RELEASE_SOURCES_SHA256": source_digest,
                                         "RELEASE_PRODUCT_COMMIT": product})
            assert result.returncode == 0, result
            assert all((root / path).read_text() == ("corrected fixture\n" if selected else "tagged fixture\n") for path in paths)
            assert (root / "product.txt").read_text() == "immutable product\n"
            assert execute(restore).returncode == 0
            for overrides in ({"RELEASE_SOURCES_SHA256": "0" * 64}, {"RELEASE_PRODUCT_COMMIT": correction}):
                result = subprocess.run(["bash", "-euc", adopt], cwd=root, capture_output=True, text=True,
                                        env={**env, "RELEASE_SOURCES_SHA256": source_digest,
                                             "RELEASE_PRODUCT_COMMIT": product, **overrides})
                assert result.returncode != 0, overrides
                assert git("diff", "--name-only") == ""
            assert {path.name: digest(path) for path in artifacts} == before

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


def browser_qualification_contract(workflow: str) -> None:
    gate = workflow_run(workflow, "Require exact catalog coverage and common package provenance")
    catalog_probe = """import {scenarioCatalog} from './examples/personal-workspace-browser-smoke.mjs';
console.log(JSON.stringify(scenarioCatalog.map(scenario => scenario.id)));
"""
    # Importing the real catalog must not start a server, require Playwright or
    # apply the caller's scenario filter. The gate needs the complete catalog.
    catalog_command = ["node", "--input-type=module", "-e", catalog_probe]
    catalog_env = {**os.environ, "LOOPX_PERSONAL_WORKSPACE_SCENARIO": "unknown",
                   "LOOPX_PERSONAL_WORKSPACE_SHARD": "invalid", "LOOPX_USAGE_PING": "0"}
    catalog = json.loads(subprocess.check_output(
        catalog_command, cwd=ROOT, env=catalog_env, text=True, timeout=10,
    ))
    assert catalog and len(catalog) == len(set(catalog))
    # A catalog consumer can have a virtual entry name, as with Node stdin.
    virtual_entry_catalog = json.loads(subprocess.check_output(
        [*catalog_command, "-"], cwd=ROOT, env=catalog_env, text=True, timeout=10,
    ))
    assert virtual_entry_catalog == catalog
    with tempfile.TemporaryDirectory(prefix="loopx-release-browser-gate-") as temporary:
        root = Path(temporary)
        examples = root / "examples"
        examples.mkdir()
        entry = examples / "personal-workspace-browser-smoke.mjs"
        for child in (ROOT / "examples").iterdir():
            if child.name != entry.name:
                (examples / child.name).symlink_to(child, target_is_directory=child.is_dir())
        entry_source = (ROOT / "examples" / entry.name).read_text()
        entry.write_text(entry_source)
        linked_entry = root / "linked entry.mjs"
        linked_entry.symlink_to(entry)
        # Direct entry paths must execute validation, including through symlinks.
        # The specific error distinguishes a reached main() from a parse/import failure.
        for runner in (entry, linked_entry):
            result = subprocess.run(
                ["node", str(runner)], cwd=root, capture_output=True, text=True, timeout=10,
                env={**catalog_env, "LOOPX_PERSONAL_WORKSPACE_PACKAGED": "0", "LOOPX_DASHBOARD_COVERAGE": "0",
                     "LOOPX_RELEASE_SOURCES_FILE": "", "LOOPX_USAGE_PING": "0"},
            )
            assert result.returncode != 0, runner
            assert "Unknown personal workspace scenario unknown" in result.stderr, result.stderr
        subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "--quiet", "--allow-empty", "-m", "fixture"], cwd=root, check=True)
        product = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        packages = root / "dist/packages"
        runtime = root / "dist/runtime"
        packages.mkdir(parents=True)
        runtime.mkdir()
        (packages / "loopx-fixture.whl").write_bytes(b"immutable wheel\n")
        (root / "dist/SHA256SUMS").write_bytes(b"fixture checksums\n")
        (runtime / "qualification.json").write_bytes(b'{"ok":true}\n')
        (runtime / "runtime.tar.gz").write_bytes(b"qualified snapshot\n")
        source = root / "dist/RELEASE-SOURCES.json"
        source.write_text(json.dumps({
            "product_commit": product,
            "sha256": {path.name: digest(path) for path in [*packages.iterdir(), root / "dist/SHA256SUMS"]},
            "runtime_sha256": {path.name: digest(path) for path in runtime.iterdir()},
        }))
        env = {**os.environ, "RELEASE_SOURCES_SHA256": digest(source), "RELEASE_CANDIDATE_ARTIFACT_ID": "fixture-candidate"}
        partition_root = root / "browser-qualification"
        baseline = {}
        for index in (1, 2, 3):
            selected = catalog[index - 1::3]
            baseline[index] = {
                "qualification.json": {"shard": f"{index}/3", "release_sources_sha256": digest(source),
                                       "candidate_artifact_id": "fixture-candidate",
                                       "companions": ["team"] if index == 1 else ["craft"] if index == 2 else []},
                "acceptance-results.json": {"shard": f"{index}/3", "release_sources_sha256": digest(source),
                                            "catalog_count": len(catalog), "selected": selected,
                                            "scenarios": {name: {"status": "PASS"} for name in selected}},
            }

        def write(receipts: dict) -> None:
            shutil.rmtree(partition_root, ignore_errors=True)
            for index, documents in receipts.items():
                directory = partition_root / f"release-browser-{index}"
                directory.mkdir(parents=True)
                for name, document in documents.items():
                    (directory / name).write_text(json.dumps(document))

        def execute() -> subprocess.CompletedProcess[str]:
            return subprocess.run(["bash", "-euc", gate], cwd=root, env=env,
                                  capture_output=True, text=True, timeout=10)

        write(baseline)
        assert execute().returncode == 0
        # An immutable tagged runner has the same catalog and an unconditional
        # invocation. The temporary import adapter must preserve it without effects.
        entry.write_text(entry_source.split("// The release gate imports", 1)[0]
                         .replace("export { scenarioCatalog };\n", "") + "\nawait main();\n")
        assert execute().returncode == 0
        assert not (examples / (entry.name + ".release-catalog.mjs")).exists()
        entry.write_text(entry_source)
        negatives = []
        missing = copy.deepcopy(baseline)
        del missing[3]
        negatives.append(missing)
        extra = copy.deepcopy(baseline)
        extra[4] = copy.deepcopy(extra[1])
        negatives.append(extra)
        for field, value in (("shard", "2/3"), ("release_sources_sha256", "0" * 64),
                             ("candidate_artifact_id", "previous-candidate"), ("companions", [])):
            changed = copy.deepcopy(baseline)
            changed[1]["qualification.json"][field] = value
            negatives.append(changed)
        for field, value in (("shard", "2/3"), ("release_sources_sha256", "0" * 64), ("catalog_count", len(catalog) - 1)):
            changed = copy.deepcopy(baseline)
            changed[1]["acceptance-results.json"][field] = value
            negatives.append(changed)
        for kind in ("missing", "unknown", "duplicate", "wrong-partition", "failed", "missing-result", "extra-result"):
            changed = copy.deepcopy(baseline)
            results = changed[1]["acceptance-results.json"]
            name = results["selected"][0]
            if kind == "missing":
                results["selected"].pop()
            elif kind == "unknown":
                results["selected"][0] = "unknown"
            elif kind == "duplicate":
                results["selected"][0] = results["selected"][1]
            elif kind == "wrong-partition":
                results["selected"][0] = baseline[2]["acceptance-results.json"]["selected"][0]
            elif kind == "failed":
                results["scenarios"][name]["status"] = "FAIL"
            elif kind == "missing-result":
                del results["scenarios"][name]
            else:
                results["scenarios"]["unknown"] = {"status": "PASS"}
            negatives.append(changed)
        for receipts in negatives:
            write(receipts)
            assert execute().returncode != 0, receipts
        write(baseline)
        binding = workflow_run(workflow, "Bind the successful browser partition to the unchanged candidate")
        binding_python = binding.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        output = root / "output/playwright/personal-workspace"
        output.mkdir(parents=True)
        legacy_results = copy.deepcopy(baseline[1]["acceptance-results.json"])
        del legacy_results["release_sources_sha256"]
        acceptance = output / "acceptance-results.json"
        acceptance.write_text(json.dumps(legacy_results))
        result = subprocess.run([sys.executable, "-c", binding_python], cwd=root, capture_output=True, text=True,
                                env={**env, "LOOPX_PERSONAL_WORKSPACE_SHARD": "1/3", "GITHUB_OUTPUT": str(root / "step-output")})
        assert result.returncode == 0, result
        bound = json.loads(acceptance.read_text())
        assert bound.pop("release_sources_sha256") == digest(source)
        assert bound == legacy_results
        assert json.loads((output / "qualification.json").read_text()) == baseline[1]["qualification.json"]
        for path in (source, packages / "loopx-fixture.whl", root / "dist/SHA256SUMS", runtime / "runtime.tar.gz", runtime / "qualification.json"):
            original = path.read_bytes()
            path.write_bytes(original + b"tampered\n")
            assert execute().returncode != 0, path
            path.write_bytes(original)
        assert execute().returncode == 0
        prerequisite_gate = workflow_run(workflow, "Require every qualification job to succeed")
        for prepare, browser in (("success", "success"), ("failure", "success"), ("success", "failure"), ("success", "skipped")):
            result = subprocess.run(["bash", "-euc", prerequisite_gate],
                                    env={**os.environ, "PREPARE_RESULT": prepare, "BROWSER_RESULT": browser})
            assert (result.returncode == 0) == (prepare == browser == "success")


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
    browser_qualification_contract(workflow)
    required_contract = (
        "release:\n    types: [published]",
        "python scripts/release_artifacts.py validate-tag",
        "python scripts/release_artifacts.py normalize-sdist",
        "python scripts/release_artifacts.py write-checksums",
        "python scripts/release_artifacts.py verify-checksums",
        "uses: actions/attest@v4",
        "scripts/release_artifacts.py upload-immutable",
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
