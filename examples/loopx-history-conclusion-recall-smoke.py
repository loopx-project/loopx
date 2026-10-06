#!/usr/bin/env python3
"""Focused smoke for the atomic LoopX history-conclusion exporter.

Public-safe and offline: it does not require a live OpenViking backend, network,
credentials, or raw transcripts. It pins the durable end-to-end boundary
behaviors, including negative cases for the review findings:

1. Export drops any conclusion field carrying a local path or secret-like token.
2. Runs with no substantive conclusion beyond classification are skipped.
3. render/export reject an unsafe goal_id (path traversal) and never echo an
   unsafe generated_at into the document.
4. Export replaces only its manifest-owned prior generation, preserves foreign
   files, publishes unique documents, and leaves the prior corpus intact if
   staging fails.
5. The extension-owned console entrypoint reaches the exporter without adding
   a speculative OpenViking ingest or recall lifecycle.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loopx.extensions.openviking_semantic_preference import history_export  # noqa: E402
from loopx.extensions.openviking_semantic_preference import provider  # noqa: E402
from loopx.extensions.openviking_semantic_preference.history_export import (  # noqa: E402
    HISTORY_EXPORT_SCHEMA_VERSION,
    conclusion_fields,
    export_goal_conclusions,
    main as export_main,
    render_conclusion_markdown,
)


def _expect_value_error(fn, message: str) -> None:
    try:
        fn()
    except ValueError:
        return
    raise AssertionError(message)


def _export_runs(
    tmp_path: Path,
    monkeypatch,
    *,
    goal_id: str,
    runs: list[dict],
) -> dict:
    monkeypatch.setattr(history_export, "collect_history", lambda **_: {"runs": runs})
    return export_goal_conclusions(
        goal_id=goal_id,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path,
        out_dir=tmp_path / "out",
    )


def _corpus_snapshot(result: dict) -> tuple[Path, Path, bytes, dict[str, bytes]]:
    receipt = result["local_receipt"]
    goal_out = Path(receipt["out_dir"])
    manifest_path = Path(receipt["manifest_path"])
    documents = {
        name: (goal_out / name).read_bytes() for name in receipt["owned_files"]
    }
    return goal_out, manifest_path, manifest_path.read_bytes(), documents


def _assert_corpus_unchanged(
    goal_out: Path,
    manifest_path: Path,
    manifest: bytes,
    documents: dict[str, bytes],
) -> None:
    assert manifest_path.read_bytes() == manifest
    assert {name: (goal_out / name).read_bytes() for name in documents} == documents
    assert sorted(path.name for path in goal_out.glob("*.md")) == sorted(documents)


def _case_export_drops_local_paths_and_secrets() -> None:
    # Build the secret at runtime so this source file contains no literal
    # credential for the repo boundary scanner, while the sanitizer still sees a
    # real secret-shaped value at test time.
    fake_secret = "token" + "=" + "ghp_" + "a" * 36
    run = {
        "classification": "example_conclusion",
        "recommended_action": "freeze the cross-market alert contract before evidence",
        "state": {
            "progress": [
                "Wrote artifact under /Users/someone/private/secret-report.md",
                f"{fake_secret} committed by mistake",
                "Validated the residual model on untouched folds",
            ]
        },
    }
    texts = [text for _, text in conclusion_fields(run)]
    assert any("residual model" in t for t in texts)
    assert not any("/Users/" in t for t in texts), "local path leaked"
    assert not any("ghp_" in t for t in texts), "secret token leaked"


def _case_noise_run_is_skipped() -> None:
    assert (
        render_conclusion_markdown("goal-x", {"classification": "quota_slot_spent"})
        is None
    )
    md = render_conclusion_markdown(
        "goal-x",
        {"classification": "did_something", "recommended_action": "do the next thing"},
    )
    assert md is not None and "do the next thing" in md


def _case_render_rejects_unsafe_goal_id() -> None:
    substantive = {"classification": "c", "recommended_action": "do it"}
    for bad in ("../escaped", "a/b", "..", "."):
        _expect_value_error(
            lambda: render_conclusion_markdown(bad, substantive),
            f"unsafe goal_id must raise: {bad!r}",
        )


def _case_render_rejects_non_public_goal_id_surfaces() -> None:
    substantive = {"classification": "c", "recommended_action": "do it"}
    fake_secret = "ak_" + "a" * 16
    fake_github_token = "ghp_" + "a" * 36
    fake_aws_key = "AK" + "IA" + "A" * 16
    fake_slack_token = "xox" + "b-" + "a" * 24
    for bad in (
        " leading-space",
        "line\nbreak",
        "control\x1fbyte",
        "markdown`break",
        "markdown|row",
        "markdown[link]",
        fake_secret,
        fake_github_token,
        fake_aws_key,
        fake_slack_token,
    ):
        _expect_value_error(
            lambda bad=bad: render_conclusion_markdown(bad, substantive),
            f"non-public goal_id must raise before rendering: {bad!r}",
        )


def _case_unsafe_generated_at_is_not_echoed() -> None:
    md = render_conclusion_markdown(
        "goal-x",
        {
            "classification": "c",
            "recommended_action": "do it",
            "generated_at": "/Users/attacker/`ls`",
        },
    )
    assert md is not None
    assert "/Users/" not in md, "unsafe generated_at leaked into document"
    assert "generated_at: ``" in md, "unsafe timestamp should be dropped to empty"


def _case_export_rejects_unsafe_goal_id(tmp_path: Path) -> None:
    _expect_value_error(
        lambda: export_goal_conclusions(
            goal_id="../escaped",
            registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path,
            out_dir=tmp_path / "out",
        ),
        "export must reject path-traversal goal_id before touching the filesystem",
    )


def _case_export_rejects_non_public_goal_id_before_history_read(
    tmp_path: Path, monkeypatch
) -> None:
    history_reads = 0

    def _must_not_read_history(**kwargs):
        del kwargs
        nonlocal history_reads
        history_reads += 1
        raise AssertionError("unsafe goal_id reached history collection")

    monkeypatch.setattr(history_export, "collect_history", _must_not_read_history)
    _expect_value_error(
        lambda: export_goal_conclusions(
            goal_id="goal\n# injected",
            registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path,
            out_dir=tmp_path / "out",
        ),
        "non-public goal_id must raise before history collection",
    )
    assert history_reads == 0
    assert not (tmp_path / "out").exists()


def _case_export_rejects_symlinked_goal_root(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    out.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    goal_root = out / "goal-through-link"
    try:
        goal_root.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        # Some Windows runners do not grant symlink creation to the test user.
        return

    monkeypatch.setattr(history_export, "collect_history", lambda **_: {"runs": []})
    try:
        export_goal_conclusions(
            goal_id="goal-through-link",
            registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path,
            out_dir=out,
        )
    except RuntimeError as exc:
        assert "goal output directory must not be a symlink" in str(exc)
    else:
        raise AssertionError("export must reject a symlinked goal output directory")

    assert goal_root.is_symlink(), "export must not replace the caller's symlink"
    assert not (outside / "history-conclusions").exists(), (
        "export must not create files through the symlink"
    )


def _case_export_preserves_foreign_files_when_retiring_owned_generation(
    tmp_path: Path, monkeypatch
) -> None:
    out = tmp_path / "out"
    goal = "retire-me"
    runs = [
        {
            "classification": "prior",
            "generated_at": "2026-08-04T01:02:03Z",
            "recommended_action": "retain only until the next generation",
        }
    ]
    first = _export_runs(tmp_path, monkeypatch, goal_id=goal, runs=runs)
    goal_out = out / goal
    owned_files = set(first["local_receipt"]["owned_files"])
    assert len(owned_files) == 1
    foreign = goal_out / "foreign.md"
    foreign.write_text("# maintained by another publisher", encoding="utf-8")

    # later history is empty
    result = _export_runs(tmp_path, monkeypatch, goal_id=goal, runs=[])
    assert foreign.exists(), "exporter deleted a foreign markdown file"
    corpus_out = Path(result["local_receipt"]["out_dir"])
    assert all(not (corpus_out / name).exists() for name in owned_files)
    assert list(goal_out.glob("*.md")) == [foreign]
    assert list(corpus_out.glob("*.md")) == []
    proj = result["public_projection"]
    assert proj["retired_prior_generation"] == len(owned_files)
    assert proj["written"] == 0
    # public projection must not expose a local filesystem path
    assert "out_dir" not in proj
    assert "/" not in "".join(
        str(v)
        for v in proj.values()
        if isinstance(v, str) and v != proj["target_scope_hint"]
    )
    # local path is only in the local receipt
    assert result["local_receipt"]["out_dir"].endswith(
        f"out/{goal}/history-conclusions"
    )


def _case_export_refuses_foreign_file_inside_managed_corpus(
    tmp_path: Path, monkeypatch
) -> None:
    first = _export_runs(
        tmp_path,
        monkeypatch,
        goal_id="foreign-inside",
        runs=[
            {
                "classification": "prior",
                "recommended_action": "preserve the published conclusion",
            }
        ],
    )
    corpus_out, manifest_path, prior_manifest, prior_documents = _corpus_snapshot(first)
    foreign = corpus_out / "foreign.md"
    foreign.write_text("# maintained by another publisher", encoding="utf-8")

    try:
        _export_runs(
            tmp_path,
            monkeypatch,
            goal_id="foreign-inside",
            runs=[
                {
                    "classification": "replacement",
                    "recommended_action": "must not delete foreign content",
                }
            ],
        )
    except RuntimeError as exc:
        assert "unowned" in str(exc)
    else:
        raise AssertionError("unowned corpus file must block directory replacement")

    assert foreign.read_text(encoding="utf-8").startswith("# maintained")
    assert manifest_path.read_bytes() == prior_manifest
    assert {
        name: (corpus_out / name).read_bytes() for name in prior_documents
    } == prior_documents


def _case_export_refuses_modified_manifest_owned_document(
    tmp_path: Path, monkeypatch
) -> None:
    first = _export_runs(
        tmp_path,
        monkeypatch,
        goal_id="modified-owned",
        runs=[
            {
                "classification": "prior",
                "recommended_action": "preserve the published conclusion",
            }
        ],
    )
    corpus_out, manifest_path, prior_manifest, prior_documents = _corpus_snapshot(first)
    owned_name = next(iter(prior_documents))
    modified = b"# modified outside the exporter\n"
    (corpus_out / owned_name).write_bytes(modified)

    try:
        _export_runs(
            tmp_path,
            monkeypatch,
            goal_id="modified-owned",
            runs=[
                {
                    "classification": "replacement",
                    "recommended_action": "must not overwrite modified content",
                }
            ],
        )
    except RuntimeError as exc:
        assert "digest" in str(exc)
    else:
        raise AssertionError("modified manifest-owned file must block replacement")

    assert manifest_path.read_bytes() == prior_manifest
    assert (corpus_out / owned_name).read_bytes() == modified


def _case_export_uses_unique_names_and_reports_exact_document_count(
    tmp_path: Path, monkeypatch
) -> None:
    runs = [
        {
            "classification": "same",
            "generated_at": "2026-08-04T01:02:03Z",
            "recommended_action": "first conclusion",
        },
        {
            "classification": "same",
            "generated_at": "2026-08-04T01:02:03Z",
            "recommended_action": "second conclusion",
        },
    ]
    result = _export_runs(tmp_path, monkeypatch, goal_id="collision", runs=runs)
    documents = list(Path(result["local_receipt"]["out_dir"]).glob("*.md"))
    assert len(documents) == 2, "same timestamp/classification overwrote a document"
    assert len({path.name for path in documents}) == 2
    assert result["public_projection"]["written"] == len(documents)
    assert sorted(result["local_receipt"]["owned_files"]) == sorted(
        path.name for path in documents
    )


def _case_export_write_failure_preserves_prior_owned_corpus(
    tmp_path: Path, monkeypatch
) -> None:
    out = tmp_path / "out"
    goal = "atomic"
    prior_runs = [
        {
            "classification": "prior",
            "generated_at": "2026-08-04T01:02:03Z",
            "recommended_action": "published prior conclusion",
        }
    ]
    first = _export_runs(
        tmp_path,
        monkeypatch,
        goal_id=goal,
        runs=prior_runs,
    )
    goal_out, manifest_path, prior_manifest, prior_documents = _corpus_snapshot(first)

    new_runs = [
        {
            "classification": "new",
            "generated_at": "2026-08-04T04:05:06Z",
            "recommended_action": f"new conclusion {index}",
        }
        for index in range(2)
    ]
    monkeypatch.setattr(
        history_export, "collect_history", lambda **_: {"runs": new_runs}
    )
    original_write_text = Path.write_text
    staged_document_writes = 0

    def _fail_second_staged_document(path: Path, data: str, **kwargs) -> int:
        nonlocal staged_document_writes
        if path.suffix == ".md" and path.parent != goal_out:
            staged_document_writes += 1
            if staged_document_writes == 2:
                raise OSError("simulated staged write failure")
        return original_write_text(path, data, **kwargs)

    monkeypatch.setattr(Path, "write_text", _fail_second_staged_document)
    try:
        export_goal_conclusions(
            goal_id=goal,
            registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path,
            out_dir=out,
        )
    except OSError as exc:
        assert "simulated staged write failure" in str(exc)
    else:
        raise AssertionError("staged write failure must propagate")

    _assert_corpus_unchanged(goal_out, manifest_path, prior_manifest, prior_documents)


def _case_export_publish_failure_rolls_back_prior_owned_corpus(
    tmp_path: Path, monkeypatch
) -> None:
    out = tmp_path / "out"
    goal = "rollback"
    first = _export_runs(
        tmp_path,
        monkeypatch,
        goal_id=goal,
        runs=[
            {
                "classification": "prior",
                "generated_at": "2026-08-04T01:02:03Z",
                "recommended_action": "published prior conclusion",
            }
        ],
    )
    goal_out, manifest_path, prior_manifest, prior_documents = _corpus_snapshot(first)

    monkeypatch.setattr(
        history_export,
        "collect_history",
        lambda **_: {
            "runs": [
                {
                    "classification": "new",
                    "generated_at": "2026-08-04T04:05:06Z",
                    "recommended_action": "replacement conclusion",
                }
            ]
        },
    )
    original_replace = history_export.os.replace
    failed = False

    def _fail_manifest_publish(source: Path, destination: Path) -> None:
        nonlocal failed
        if (
            not failed
            and Path(source).name == "publish"
            and Path(destination) == goal_out
        ):
            failed = True
            raise OSError("simulated corpus publish failure")
        original_replace(source, destination)

    monkeypatch.setattr(history_export.os, "replace", _fail_manifest_publish)
    try:
        export_goal_conclusions(
            goal_id=goal,
            registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path,
            out_dir=out,
        )
    except OSError as exc:
        assert "simulated corpus publish failure" in str(exc)
    else:
        raise AssertionError("publication failure must propagate after rollback")

    assert failed
    _assert_corpus_unchanged(goal_out, manifest_path, prior_manifest, prior_documents)


def _case_export_recovers_backup_only_interrupted_publication(
    tmp_path: Path, monkeypatch
) -> None:
    goal = "recover-backup"
    first = _export_runs(
        tmp_path,
        monkeypatch,
        goal_id=goal,
        runs=[
            {
                "classification": "prior",
                "generated_at": "2026-08-04T01:02:03Z",
                "recommended_action": "published prior conclusion",
            }
        ],
    )
    corpus_out = Path(first["local_receipt"]["out_dir"])
    goal_root = corpus_out.parent
    foreign = goal_root / "foreign.md"
    foreign.write_text("# maintained by another publisher", encoding="utf-8")
    backup = corpus_out.parent / f".{corpus_out.name}.loopx-backup"
    history_export.os.replace(corpus_out, backup)
    assert backup.is_dir()
    assert not corpus_out.exists()

    result = _export_runs(
        tmp_path,
        monkeypatch,
        goal_id=goal,
        runs=[
            {
                "classification": "replacement",
                "generated_at": "2026-08-04T04:05:06Z",
                "recommended_action": "publish after backup recovery",
            }
        ],
    )

    assert Path(result["local_receipt"]["manifest_path"]).is_file()
    assert foreign.read_text(encoding="utf-8").startswith("# maintained")
    assert not backup.exists()
    assert result["public_projection"]["retired_prior_generation"] == 1


def _case_export_cleans_backup_when_live_is_valid(
    tmp_path: Path, monkeypatch
) -> None:
    goal = "recover-live-backup"
    result = _export_runs(
        tmp_path,
        monkeypatch,
        goal_id=goal,
        runs=[
            {
                "classification": "prior",
                "generated_at": "2026-08-04T01:02:03Z",
                "recommended_action": "published prior conclusion",
            }
        ],
    )
    corpus_out = Path(result["local_receipt"]["out_dir"])
    backup = corpus_out.parent / f".{corpus_out.name}.loopx-backup"
    history_export.shutil.copytree(corpus_out, str(backup))

    result2 = _export_runs(
        tmp_path,
        monkeypatch,
        goal_id=goal,
        runs=[
            {
                "classification": "replacement",
                "generated_at": "2026-08-04T04:05:06Z",
                "recommended_action": "publish after live+backup recovery",
            }
        ],
    )
    assert Path(result2["local_receipt"]["manifest_path"]).is_file()
    assert not backup.exists()


def _case_export_defers_until_verified_backup_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    goal = "recover-live-cleanup-retry"
    result = _export_runs(
        tmp_path,
        monkeypatch,
        goal_id=goal,
        runs=[
            {
                "classification": "prior",
                "generated_at": "2026-08-04T01:02:03Z",
                "recommended_action": "published prior conclusion",
            }
        ],
    )
    corpus_out, manifest_path, prior_manifest, prior_documents = _corpus_snapshot(
        result
    )
    backup = corpus_out.parent / f".{corpus_out.name}.loopx-backup"
    history_export.shutil.copytree(corpus_out, backup)
    original_rmtree = history_export.shutil.rmtree

    def _leave_backup(path, *args, **kwargs) -> None:
        if Path(path) == backup:
            return
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(history_export.shutil, "rmtree", _leave_backup)
    try:
        _export_runs(
            tmp_path,
            monkeypatch,
            goal_id=goal,
            runs=[
                {
                    "classification": "replacement",
                    "generated_at": "2026-08-04T04:05:06Z",
                    "recommended_action": "must wait for backup cleanup",
                }
            ],
        )
    except RuntimeError as exc:
        assert "backup cleanup is incomplete" in str(exc)
    else:
        raise AssertionError("incomplete backup cleanup must defer publication")

    _assert_corpus_unchanged(
        corpus_out, manifest_path, prior_manifest, prior_documents
    )
    assert backup.is_dir()

    monkeypatch.setattr(history_export.shutil, "rmtree", original_rmtree)
    result2 = _export_runs(
        tmp_path,
        monkeypatch,
        goal_id=goal,
        runs=[
            {
                "classification": "replacement",
                "generated_at": "2026-08-04T04:05:06Z",
                "recommended_action": "retry after cleanup succeeds",
            }
        ],
    )
    assert Path(result2["local_receipt"]["manifest_path"]).is_file()
    assert not backup.exists()


def _case_export_reports_post_publish_backup_cleanup_failure(
    tmp_path: Path, monkeypatch
) -> None:
    goal = "post-publish-cleanup-retry"
    _export_runs(
        tmp_path,
        monkeypatch,
        goal_id=goal,
        runs=[
            {
                "classification": "prior",
                "generated_at": "2026-08-04T01:02:03Z",
                "recommended_action": "published prior conclusion",
            }
        ],
    )
    corpus_out = tmp_path / "out" / goal / "history-conclusions"
    backup = corpus_out.parent / f".{corpus_out.name}.loopx-backup"
    original_rmtree = history_export.shutil.rmtree

    def _leave_backup(path, *args, **kwargs) -> None:
        if Path(path) == backup:
            return
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(history_export.shutil, "rmtree", _leave_backup)
    try:
        _export_runs(
            tmp_path,
            monkeypatch,
            goal_id=goal,
            runs=[
                {
                    "classification": "replacement",
                    "generated_at": "2026-08-04T04:05:06Z",
                    "recommended_action": "must report cleanup failure",
                }
            ],
        )
    except RuntimeError as exc:
        assert "backup cleanup is incomplete" in str(exc)
    else:
        raise AssertionError("post-publish cleanup failure must be reported")

    assert backup.is_dir()
    assert "must report cleanup failure" in next(corpus_out.glob("*.md")).read_text(
        encoding="utf-8"
    )


def _case_export_refuses_unowned_backup_when_live_is_valid(
    tmp_path: Path, monkeypatch
) -> None:
    goal = "recover-live-unowned-backup"
    result = _export_runs(
        tmp_path,
        monkeypatch,
        goal_id=goal,
        runs=[
            {
                "classification": "prior",
                "generated_at": "2026-08-04T01:02:03Z",
                "recommended_action": "published prior conclusion",
            }
        ],
    )
    corpus_out = Path(result["local_receipt"]["out_dir"])
    backup = corpus_out.parent / f".{corpus_out.name}.loopx-backup"
    foreign = backup / "foreign.md"
    backup.mkdir()
    foreign.write_text("# maintained by another publisher", encoding="utf-8")

    try:
        _export_runs(
            tmp_path,
            monkeypatch,
            goal_id=goal,
            runs=[
                {
                    "classification": "replacement",
                    "generated_at": "2026-08-04T04:05:06Z",
                    "recommended_action": "must preserve the foreign backup",
                }
            ],
        )
    except RuntimeError as exc:
        assert "unverified backup" in str(exc)
    else:
        raise AssertionError("unowned backup must block recovery")

    assert foreign.read_text(encoding="utf-8").startswith("# maintained")
    assert (corpus_out / ".loopx-history-conclusion-export.json").is_file()


def _case_export_recovers_valid_backup_and_preserves_unverified_live(
    tmp_path: Path, monkeypatch
) -> None:
    goal = "recover-invalid-live"
    result = _export_runs(
        tmp_path,
        monkeypatch,
        goal_id=goal,
        runs=[
            {
                "classification": "prior",
                "generated_at": "2026-08-04T01:02:03Z",
                "recommended_action": "published prior conclusion",
            }
        ],
    )
    corpus_out = Path(result["local_receipt"]["out_dir"])
    backup = corpus_out.parent / f".{corpus_out.name}.loopx-backup"
    unverified_live = corpus_out.parent / f".{corpus_out.name}.loopx-unverified-live"
    history_export.shutil.copytree(corpus_out, backup)
    foreign = corpus_out / "foreign.md"
    foreign.write_text("# maintained by another publisher", encoding="utf-8")

    result2 = _export_runs(
        tmp_path,
        monkeypatch,
        goal_id=goal,
        runs=[
            {
                "classification": "replacement",
                "generated_at": "2026-08-04T04:05:06Z",
                "recommended_action": "recover from verified backup",
            }
        ],
    )

    assert Path(result2["local_receipt"]["manifest_path"]).is_file()
    assert not backup.exists()
    assert foreign.exists() is False
    assert (unverified_live / "foreign.md").read_text(
        encoding="utf-8"
    ).startswith("# maintained")


def _case_export_fails_closed_when_neither_live_nor_backup_is_valid(
    tmp_path: Path, monkeypatch
) -> None:
    goal = "recover-neither-valid"
    result = _export_runs(
        tmp_path,
        monkeypatch,
        goal_id=goal,
        runs=[
            {
                "classification": "prior",
                "generated_at": "2026-08-04T01:02:03Z",
                "recommended_action": "published prior conclusion",
            }
        ],
    )
    corpus_out = Path(result["local_receipt"]["out_dir"])
    backup = corpus_out.parent / f".{corpus_out.name}.loopx-backup"
    manifest = corpus_out / ".loopx-history-conclusion-export.json"
    manifest.unlink()
    backup.mkdir()
    try:
        _export_runs(
            tmp_path,
            monkeypatch,
            goal_id=goal,
            runs=[
                {
                    "classification": "replacement",
                    "generated_at": "2026-08-04T04:05:06Z",
                    "recommended_action": "fails closed",
                }
            ],
        )
        raise AssertionError("should have raised")
    except RuntimeError as exc:
        assert "unverified backup" in str(exc)


def _case_active_provider_rejects_exit_zero_unsuccessful_openviking_envelope(
    monkeypatch,
) -> None:
    class _Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "ok": False,
                "error": {"code": "index_unavailable"},
                "result": {"resources": []},
            }
        )

    monkeypatch.setattr(provider.subprocess, "run", lambda *a, **k: _Completed())
    try:
        provider._run_ov(
            "ov",
            ["find", "-o", "json", "storage"],
            cli_config=None,
            timeout_seconds=25,
        )
    except RuntimeError as exc:
        assert "unsuccessful response" in str(exc)
    else:
        raise AssertionError("OpenViking ok=false envelope must fail")


def _case_extension_owned_entrypoint_reaches_atomic_export(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        history_export,
        "collect_history",
        lambda **kwargs: {
            "runs": [
                {
                    "classification": "reachable",
                    "recommended_action": f"export {kwargs['goal_id']}",
                }
            ]
        },
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert (
            export_main(
                [
                    "--goal-id",
                    "reachable-goal",
                    "--registry-path",
                    str(tmp_path / "registry.json"),
                    "--runtime-root",
                    str(tmp_path),
                    "--out-dir",
                    str(tmp_path / "out"),
                ]
            )
            == 0
        )
    exported = json.loads(output.getvalue())
    assert exported["public_projection"]["written"] == 1
    assert not hasattr(history_export, "recall_history_conclusions")
    assert not hasattr(history_export, "refresh_exported_conclusions")


def _case_schema_versions_are_stable() -> None:
    assert HISTORY_EXPORT_SCHEMA_VERSION == "loopx_history_conclusion_export_v0"


class _MonkeyPatch:
    _MISSING = object()

    def __init__(self) -> None:
        self._undo: list[tuple[object, str, object]] = []

    def setattr(
        self,
        target: object,
        name: str,
        value: object,
        *,
        raising: bool = True,
    ) -> None:
        old = getattr(target, name, self._MISSING)
        if old is self._MISSING and raising:
            raise AttributeError(name)
        self._undo.append((target, name, old))
        setattr(target, name, value)

    def undo(self) -> None:
        for target, name, old in reversed(self._undo):
            if old is self._MISSING:
                delattr(target, name)
            else:
                setattr(target, name, old)


def _discover_cases() -> list[tuple[str, object]]:
    unexpected_pytest_cases = sorted(
        name
        for name, value in globals().items()
        if name.startswith("test_")
        and name != "test_history_conclusion_export_contract"
        and inspect.isfunction(value)
    )
    if unexpected_pytest_cases:
        raise AssertionError(
            "history export contract cases must use the shared _case_ prefix: "
            f"{unexpected_pytest_cases}"
        )
    cases = [
        (name, value)
        for name, value in globals().items()
        if name.startswith("_case_") and inspect.isfunction(value)
    ]
    return sorted(cases, key=lambda item: item[1].__code__.co_firstlineno)


def _run_cases() -> list[tuple[str, str]]:
    failures: list[tuple[str, str]] = []
    for name, case in _discover_cases():
        monkeypatch = _MonkeyPatch()
        with tempfile.TemporaryDirectory(prefix="loopx-history-export-smoke-") as tmp:
            available = {
                "tmp_path": Path(tmp),
                "monkeypatch": monkeypatch,
            }
            parameters = inspect.signature(case).parameters
            unknown = sorted(set(parameters) - set(available))
            if unknown:
                failures.append(
                    (name, f"unsupported smoke fixtures: {', '.join(unknown)}")
                )
                continue
            try:
                case(**{key: available[key] for key in parameters})
            except Exception:
                failures.append((name, traceback.format_exc()))
            finally:
                monkeypatch.undo()
    return failures


def test_history_conclusion_export_contract() -> None:
    assert not _run_cases()


def main() -> int:
    failures = _run_cases()
    if failures:
        for name, failure in failures:
            print(f"FAIL: {name}\n{failure}", file=sys.stderr)
        return 1
    print(f"ok: {len(_discover_cases())} history-conclusion export cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
