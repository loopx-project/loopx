"""Source selection is cohort-wide, explicit, and independent of installed SForge."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import re
import subprocess
import sys

import pytest

from benchmark.edgebench import source


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    repo = tmp_path / "remote"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "integration")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    # Synthetic provider: the serve CLI identifies the imported source, without Docker.
    (repo / "sforge").mkdir()
    (repo / "sforge/__init__.py").write_text("MARKER = 'shared-source'\n")
    (repo / "sforge/cli.py").write_text(
        "def main():\n"
        " import json, sys, sforge\n"
        " from benchmark.edgebench.source import active_source_receipt\n"
        " print(json.dumps({'marker':sforge.MARKER,'source':active_source_receipt(),'args':sys.argv[1:]}))\n")
    (repo / ".gitignore").write_text("__pycache__/\n")
    git(repo, "add", ".")
    git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "synthetic source")
    defaults = tmp_path / "defaults.json"
    defaults.write_text(json.dumps({"repository": str(repo), "ref": "refs/heads/integration"}))
    monkeypatch.setattr(source, "DEFAULT_FILE", defaults)
    return repo


def test_default_freezes_cohort_while_new_cohort_sees_branch_advance(repository, tmp_path):
    first = git(repository, "rev-parse", "HEAD")
    checkout, receipt = source.prepare_source(tmp_path / "old")
    assert receipt["commit"] == first
    assert (checkout / "sforge/__init__.py").read_text() == "MARKER = 'shared-source'\n"
    (repository / "new.txt").write_text("second revision")
    git(repository, "add", "new.txt")
    git(repository, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "advance")
    second = git(repository, "rev-parse", "HEAD")
    assert source.prepare_source(tmp_path / "old")[1] == receipt
    assert source.prepare_source(tmp_path / "new")[1]["commit"] == second != first


def test_frozen_cohort_reuses_source_without_network(repository, tmp_path, monkeypatch):
    expected = source.prepare_source(tmp_path / "logs")[1]
    original = source._git

    def offline(checkout, *args):
        assert args[0] not in {"fetch", "clone", "ls-remote"}
        return original(checkout, *args)

    monkeypatch.setattr(source, "_git", offline)
    assert source.prepare_source(tmp_path / "logs")[1] == expected


def test_parallel_worker_and_judge_share_one_source(repository, tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: source.prepare_source(tmp_path / "logs")[1], range(4)))
    assert all(result == results[0] for result in results)


def test_explicit_pin_overrides_default_but_cannot_change_existing_cohort(repository, tmp_path):
    revision = git(repository, "rev-parse", "HEAD")
    log = tmp_path / "logs"
    _, receipt = source.prepare_source(log, repository=str(repository), revision=revision)
    assert receipt["ref"] == receipt["commit"] == revision
    with pytest.raises(ValueError, match="conflicts"):
        source.prepare_source(log, revision="f" * 40)
    with pytest.raises(ValueError, match="conflicts"):
        source.prepare_source(log, repository="https://example.invalid/other.git", ref=revision)
    assert source.prepare_source(log)[1] == receipt


@pytest.mark.parametrize("change", ["tracked", "untracked", "head", "receipt", "repository", "receipt_type"])
def test_source_drift_rejected(repository, tmp_path, change):
    checkout, _ = source.prepare_source(tmp_path / "logs")
    if change == "tracked":
        (checkout / "sforge/__init__.py").write_text("MARKER='changed'\n")
    elif change == "untracked":
        (checkout / "sforge/injected.py").write_text("injected=True\n")
    elif change == "head":
        git(checkout, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-qm", "changed head")
    else:
        path = checkout.parent / "receipt.json"
        receipt = json.loads(path.read_text())
        if change == "receipt_type":
            receipt = []
        elif change == "repository":
            receipt["repository"] = "https://example.invalid/different.git"
        else:
            receipt["commit"] = "not-a-sha"
        path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        source.prepare_source(tmp_path / "logs")


def test_legacy_runs_cannot_be_silently_relabelled(repository, tmp_path):
    (tmp_path / "logs/runs/existing").mkdir(parents=True)
    with pytest.raises(ValueError, match="Existing runs"):
        source.prepare_source(tmp_path / "logs")
    assert not (tmp_path / "logs" / source.STATE_DIR).exists()


def test_failed_fetch_leaves_no_source_receipt_and_can_retry(repository, tmp_path):
    with pytest.raises(RuntimeError, match="fetch failed"):
        source.prepare_source(tmp_path / "logs", ref="refs/heads/missing")
    assert not (tmp_path / "logs" / source.STATE_DIR).exists()
    assert source.prepare_source(tmp_path / "logs")[1]["commit"] == git(repository, "rev-parse", "HEAD")


def test_real_serve_entry_loads_selected_provider_over_ambient_pythonpath(repository, tmp_path):
    shadow = tmp_path / "ambient/sforge"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("raise AssertionError('wrong installed provider')\n")
    env = {**os.environ, "PYTHONPATH": str(shadow.parent)}
    env.pop(source.RECEIPT_ENV, None)
    cmd = [sys.executable, "-m", "benchmark.edgebench.serve", "--log-dir", str(tmp_path / "logs"),
           "--sforge-repository", str(repository), "--sforge-ref", "refs/heads/integration", "--port", "8088"]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)
    observed = json.loads(result.stdout)
    assert observed["marker"] == "shared-source"
    assert observed["source"]["commit"] == git(repository, "rev-parse", "HEAD")
    assert observed["args"] == ["serve", "--port", "8088"]


def test_imported_wrong_provider_rejected(repository, tmp_path, monkeypatch):
    checkout, _ = source.prepare_source(tmp_path / "logs")
    monkeypatch.setenv(source.RECEIPT_ENV, str(checkout.parent / "receipt.json"))
    from types import SimpleNamespace
    monkeypatch.setattr(source.importlib.util, "find_spec", lambda _: SimpleNamespace(origin="unrelated.py"))
    with pytest.raises(ValueError, match="Imported SForge differs"):
        source.active_source_receipt()


def test_annotated_tag_resolves_to_commit(repository, tmp_path):
    git(repository, "tag", "-a", "release", "-m", "synthetic release")
    _, receipt = source.prepare_source(tmp_path / "logs", ref="refs/tags/release")
    assert receipt["commit"] == git(repository, "rev-parse", "HEAD")


@pytest.mark.parametrize("selection", [
    {"ref": "--upload-pack=unexpected"}, {"ref": ""}, {"revision": "short"},
    {"ref": "main", "revision": "f" * 40}, {"repository": "https://example.invalid/repo"},
])
def test_invalid_selection_rejected_before_creating_cohort(tmp_path, selection):
    with pytest.raises(ValueError):
        source.prepare_source(tmp_path / "logs", **selection)
    assert not (tmp_path / "logs").exists()


def test_transport_timeout_omits_command_and_credentials(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(["git", "https://user:secret@example.invalid/repo"], 180)

    monkeypatch.setattr(source.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="^EdgeBench git fetch timed out$"):
        source._git(tmp_path, "fetch", "origin", "main")


def test_prepare_help_does_not_need_provider_or_create_source(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "benchmark.edgebench.source", "--help", "--log-dir", str(tmp_path / "logs")],
        capture_output=True, text=True, check=True,
    )
    assert "--sforge-revision" in result.stdout
    assert "null" not in result.stdout
    assert not (tmp_path / "logs").exists()


def install_test_patch(repository, other_changes=None, *, compact=True):
    """A real full-index Git patch against the fixture provider, without a remote service."""
    target = repository / "sforge/__init__.py"
    target.write_text("MARKER = 'patched-source'\n")
    for path, contents in (other_changes or {}).items():
        (repository / path).write_text(contents)
    patch = subprocess.check_output([
        "git", "-C", str(repository), "-c",
        f"diff.suppressBlankEmpty={'true' if compact else 'false'}",
        "diff", "--binary", "--full-index", f"--unified={1 if compact else 3}",
        "--no-color", "--no-ext-diff", "--no-textconv", "HEAD", "--",
    ])
    git(repository, "checkout", "--", "sforge/__init__.py", *(other_changes or {}))
    asset = source.DEFAULT_FILE.parent / "compatibility/fixture-v1.patch"
    asset.parent.mkdir(exist_ok=True)
    asset.write_bytes(patch)
    defaults = json.loads(source.DEFAULT_FILE.read_text())
    defaults["compatibility_patch"] = {
        "id": "fixture-v1", "relative_path": "compatibility/fixture-v1.patch",
        "sha256": hashlib.sha256(patch).hexdigest(),
    }
    source.DEFAULT_FILE.write_text(json.dumps(defaults))
    return asset


def test_bundled_patch_passes_required_public_checks(tmp_path):
    from loopx.contract import scan_public_boundary

    defaults = json.loads(source.DEFAULT_FILE.read_text())
    # The production bundle is qualified with an immutable upstream revision.
    # A moving branch can invalidate its full-index preimages between cohorts.
    assert re.fullmatch(r"[0-9a-f]{40}", defaults["ref"])
    _, patch = source._bundled_patch(defaults["compatibility_patch"])
    asset = tmp_path / "compatibility.patch"
    asset.write_bytes(patch)
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "--", asset.name)
    assert git(tmp_path, "diff", "--cached", "--check") == ""
    assert scan_public_boundary([asset], registry={})["ok"]


def test_pinned_default_and_patch_advance_only_as_a_reviewed_pair(repository, tmp_path):
    install_test_patch(repository)
    defaults = json.loads(source.DEFAULT_FILE.read_text())
    defaults["ref"] = git(repository, "rev-parse", "HEAD")
    source.DEFAULT_FILE.write_text(json.dumps(defaults))
    old_checkout, original = source.prepare_source(tmp_path / "old")
    old_receipt = (old_checkout.parent / "receipt.json").read_bytes()

    # Even a touched-file change on the remote branch cannot break a new cohort
    # or silently opt it into an unqualified provider revision.
    (repository / "sforge/__init__.py").write_text("MARKER = 'new-upstream'\n")
    git(repository, "add", "sforge/__init__.py")
    git(repository, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "advance provider")
    advanced = git(repository, "rev-parse", "HEAD")
    new_checkout, same = source.prepare_source(tmp_path / "new")
    assert same == original
    assert (new_checkout / "sforge/__init__.py").read_text() == "MARKER = 'patched-source'\n"

    # Updating just the pin still fails closed; no partially prepared source
    # can be consumed by a concurrent worker or judge.
    defaults["ref"] = advanced
    source.DEFAULT_FILE.write_text(json.dumps(defaults))
    with pytest.raises(ValueError, match="preimage differs"):
        source.prepare_source(tmp_path / "unpaired")
    assert not (tmp_path / "unpaired" / source.STATE_DIR).exists()

    install_test_patch(repository)
    _, refreshed = source.prepare_source(tmp_path / "refreshed")
    assert refreshed["upstream_commit"] == advanced != original["upstream_commit"]
    assert refreshed["compatibility_patch"]["sha256"] != original["compatibility_patch"]["sha256"]
    assert source.prepare_source(tmp_path / "old")[1] == original
    assert (old_checkout.parent / "receipt.json").read_bytes() == old_receipt


def test_default_patch_creates_reproducible_clean_source(repository, tmp_path, monkeypatch):
    asset = install_test_patch(repository)
    upstream = git(repository, "rev-parse", "HEAD")
    first, receipt = source.prepare_source(tmp_path / "one")
    # The dependency commit must not borrow the operator's identity or clock.
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Unrelated Operator")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "operator@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2000-01-01T00:00:00Z")
    second, again = source.prepare_source(tmp_path / "two")
    assert receipt == again
    assert receipt["schema_version"] == "edgebench_source_v2"
    assert receipt["upstream_commit"] == upstream != receipt["commit"]
    assert receipt["compatibility_patch"] == {
        "id": "fixture-v1", "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
    }
    for checkout in (first, second):
        assert git(checkout, "status", "--porcelain", "--untracked-files=normal") == ""
        assert git(checkout, "rev-parse", "HEAD") == receipt["commit"]
        assert git(checkout, "rev-parse", "HEAD^") == upstream
        assert git(checkout, "show", "-s", "--format=%an <%ae>") == (
            "LoopX Compatibility <loopx-compatibility@example.invalid>")
        assert git(checkout, "show", "-s", "--format=%ct") == git(repository, "show", "-s", "--format=%ct")
        assert (checkout / "sforge/__init__.py").read_text() == "MARKER = 'patched-source'\n"
        assert (checkout.parent / "compatibility.patch").read_bytes() == asset.read_bytes()


def test_patch_accepts_only_untouched_preimages_as_default_branch_advances(repository, tmp_path):
    install_test_patch(repository)
    original = source.prepare_source(tmp_path / "old")[1]
    (repository / "unrelated.txt").write_text("unrelated branch change\n")
    git(repository, "add", "unrelated.txt")
    git(repository, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "unrelated change")
    checkout, advanced = source.prepare_source(tmp_path / "new")
    assert advanced["upstream_commit"] == git(repository, "rev-parse", "HEAD")
    assert advanced["commit"] != original["commit"]
    assert advanced["compatibility_patch"] == original["compatibility_patch"]
    assert (checkout / "unrelated.txt").read_text() == "unrelated branch change\n"
    assert source.prepare_source(tmp_path / "old")[1] == original

    # A context patch could still apply; changing any touched blob must fail before apply.
    with (repository / "sforge/__init__.py").open("a") as target:
        target.write("OTHER = True\n")
    git(repository, "add", "sforge/__init__.py")
    git(repository, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "changed preimage")
    with pytest.raises(ValueError, match="preimage differs"):
        source.prepare_source(tmp_path / "incompatible")
    assert not (tmp_path / "incompatible" / source.STATE_DIR).exists()
    assert not list((tmp_path / "incompatible").glob(".edgebench-source-*"))


@pytest.mark.parametrize("compact", [False, True])
def test_canonical_patch_verification_ignores_operator_diff_presentation(repository, tmp_path, monkeypatch, compact):
    lines = [f"# line {index}\n" for index in range(40)]
    lines[1] = "\n"
    (repository / "sforge/long.py").write_text("".join(lines))
    git(repository, "add", "sforge/long.py")
    git(repository, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "separate patch hunks")
    lines[2], lines[30] = "# first change\n", "# distant change\n"
    install_test_patch(repository, {"sforge/long.py": "".join(lines)}, compact=compact)
    expected = source.prepare_source(tmp_path / "one")[1]
    order = tmp_path / "operator-diff-order"
    order.write_text("sforge/long.py\nsforge/__init__.py\n")
    settings = {"diff.interHunkContext": "40", "diff.suppressBlankEmpty": "true",
                "diff.orderFile": str(order)}
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(settings)))
    for index, (key, value) in enumerate(settings.items()):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{index}", value)
    assert source.prepare_source(tmp_path / "one")[1] == expected
    assert source.prepare_source(tmp_path / "two")[1] == expected


def test_represented_patch_preserves_tree_and_existing_offline_cohort(repository, tmp_path, monkeypatch):
    original = "# first\n\n# before\n# old\n# after\n\n# last\n"
    target = repository / "sforge/representation.py"
    target.write_text(original)
    git(repository, "add", "sforge/representation.py")
    git(repository, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "context fixture")
    changes = {"sforge/representation.py": original.replace("# old\n", "# new\n")}
    asset = install_test_patch(repository, changes, compact=False)
    legacy_patch = asset.read_bytes()
    legacy_checkout, legacy = source.prepare_source(tmp_path / "legacy")
    legacy_receipt = (legacy_checkout.parent / "receipt.json").read_bytes()

    install_test_patch(repository, changes)
    assert asset.read_bytes() != legacy_patch
    assert b"\n \n" in legacy_patch and b"\n \n" not in asset.read_bytes()
    checkout, current = source.prepare_source(tmp_path / "current")
    assert current["upstream_commit"] == legacy["upstream_commit"]
    assert current["commit"] != legacy["commit"]
    assert current["compatibility_patch"]["sha256"] != legacy["compatibility_patch"]["sha256"]
    assert git(checkout, "rev-parse", "HEAD^{tree}") == git(legacy_checkout, "rev-parse", "HEAD^{tree}")

    original_git = source._git_bytes

    def offline(checkout, *args, **kwargs):
        assert args[0] not in {"fetch", "clone", "ls-remote"}
        return original_git(checkout, *args, **kwargs)

    monkeypatch.setattr(source, "_git_bytes", offline)
    asset.unlink()
    source.DEFAULT_FILE.unlink()
    for log, expected in (("legacy", legacy), ("current", current)):
        assert source.prepare_source(tmp_path / log)[1] == expected
    assert (legacy_checkout.parent / "receipt.json").read_bytes() == legacy_receipt
    assert (legacy_checkout.parent / "compatibility.patch").read_bytes() == legacy_patch


@pytest.mark.parametrize("change", ["digest", "format", "noncanonical", "path", "symlink"])
def test_invalid_default_patch_leaves_no_published_source(repository, tmp_path, change):
    asset = install_test_patch(repository)
    defaults = json.loads(source.DEFAULT_FILE.read_text())
    if change == "digest":
        asset.write_bytes(asset.read_bytes() + b"\n")
    elif change in {"format", "noncanonical"}:
        raw = b"not a Git patch\n" if change == "format" else asset.read_bytes().replace(
            b"@@ -1 +1 @@", b"@@ -1,1 +1,1 @@")
        asset.write_bytes(raw)
        defaults["compatibility_patch"]["sha256"] = hashlib.sha256(raw).hexdigest()
    elif change == "path":
        defaults["compatibility_patch"]["relative_path"] = "../outside.patch"
    else:
        alias = asset.with_name("alias.patch")
        alias.symlink_to(asset)
        defaults["compatibility_patch"]["relative_path"] = "compatibility/alias.patch"
    source.DEFAULT_FILE.write_text(json.dumps(defaults))
    with pytest.raises(ValueError):
        source.prepare_source(tmp_path / "logs")
    assert not (tmp_path / "logs" / source.STATE_DIR).exists()
    assert not list((tmp_path / "logs").glob(".edgebench-source-*"))


@pytest.mark.parametrize("change", ["patch", "digest", "id", "extra_commit", "extra_change", "schema", "extra_field"])
def test_patched_receipt_and_exact_commit_are_verified(repository, tmp_path, change):
    install_test_patch(repository)
    checkout, receipt = source.prepare_source(tmp_path / "logs")
    if change == "patch":
        path = checkout.parent / "compatibility.patch"
        path.write_bytes(path.read_bytes() + b"\n")
    elif change == "digest":
        receipt["compatibility_patch"]["sha256"] = "0" * 64
    elif change == "id":
        receipt["compatibility_patch"]["id"] = "unrecorded-patch"
    elif change in {"extra_commit", "extra_change"}:
        if change == "extra_change":
            (checkout / "unexpected.txt").write_text("outside the frozen patch\n")
            git(checkout, "add", "unexpected.txt")
        git(checkout, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-qm", "extra commit")
        receipt["commit"] = git(checkout, "rev-parse", "HEAD")
    elif change == "schema":
        receipt["schema_version"] = "edgebench_source_v1"
    else:
        receipt["extra"] = "unversioned metadata"
    (checkout.parent / "receipt.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        source.prepare_source(tmp_path / "logs")


def test_legacy_receipt_is_not_upgraded_when_default_gains_patch(repository, tmp_path):
    checkout, legacy = source.prepare_source(tmp_path / "legacy")
    original = (checkout.parent / "receipt.json").read_bytes()
    install_test_patch(repository)
    assert source.prepare_source(tmp_path / "legacy")[1] == legacy
    assert legacy["schema_version"] == "edgebench_source_v1"
    assert (checkout.parent / "receipt.json").read_bytes() == original
    assert not (checkout.parent / "compatibility.patch").exists()
    assert source.prepare_source(tmp_path / "new")[1]["schema_version"] == "edgebench_source_v2"


def test_patched_cohort_is_offline_and_independent_of_current_bundle(repository, tmp_path, monkeypatch):
    asset = install_test_patch(repository)
    checkout, expected = source.prepare_source(tmp_path / "logs")
    original = source._git_bytes

    def offline(checkout, *args, **kwargs):
        assert args[0] not in {"fetch", "clone", "ls-remote"}
        return original(checkout, *args, **kwargs)

    monkeypatch.setattr(source, "_git_bytes", offline)
    asset.unlink()
    source.DEFAULT_FILE.unlink()
    assert source.prepare_source(tmp_path / "logs")[1] == expected
    assert source.prepare_source(tmp_path / "logs", revision=expected["commit"])[1] == expected
    with pytest.raises(ValueError, match="conflicts"):
        source.prepare_source(tmp_path / "logs", revision=expected["upstream_commit"])
    assert git(checkout, "status", "--porcelain") == ""


@pytest.mark.parametrize("choice", ["ref", "revision", "repository"])
def test_explicit_selection_does_not_apply_default_patch(repository, tmp_path, choice):
    asset = install_test_patch(repository)
    # An explicit choice must not even depend on the bundled patch being usable.
    asset.unlink()
    upstream = git(repository, "rev-parse", "HEAD")
    selection = {"ref": "refs/heads/integration"} if choice != "revision" else {"revision": upstream}
    if choice == "repository":
        alternate = tmp_path / "alternate"
        subprocess.run(["git", "clone", "-q", str(repository), str(alternate)], check=True)
        (alternate / "sforge/__init__.py").write_text("MARKER = 'custom-source'\n")
        git(alternate, "add", "sforge/__init__.py")
        git(alternate, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "core.hooksPath=/dev/null", "commit", "-qm", "custom source")
        selection["repository"] = str(alternate)
        upstream = git(alternate, "rev-parse", "HEAD")
    checkout, receipt = source.prepare_source(tmp_path / "logs", **selection)
    assert receipt["schema_version"] == "edgebench_source_v1"
    assert receipt["commit"] == upstream
    assert not (checkout.parent / "compatibility.patch").exists()
    expected = "custom-source" if choice == "repository" else "shared-source"
    assert (checkout / "sforge/__init__.py").read_text() == f"MARKER = '{expected}'\n"


def test_real_bootstrap_imports_prepared_patched_source(repository, tmp_path):
    install_test_patch(repository)
    checkout, receipt = source.prepare_source(tmp_path / "logs")
    env = dict(os.environ)
    env.pop(source.RECEIPT_ENV, None)
    result = subprocess.run([
        sys.executable, "-m", "benchmark.edgebench.serve", "--log-dir", str(tmp_path / "logs"),
        "--port", "8088",
    ], env=env, capture_output=True, text=True, check=True)
    observed = json.loads(result.stdout)
    assert observed["marker"] == "patched-source"
    assert observed["source"] == receipt
    assert observed["source"]["commit"] == git(checkout, "rev-parse", "HEAD")
