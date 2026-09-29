"""Project-registry I/O stays behind the dual-format codec boundary."""

from __future__ import annotations

import copy
import json
import runpy
from pathlib import Path

import pytest

from loopx.semantics.inventory import SourceFile
from loopx.semantics.project_registry_io import (
    PROJECT_REGISTRY_IO_MANIFEST,
    build_project_registry_io_manifest,
    collect_project_registry_io,
    scan_python_registry_io,
    validate_project_registry_io_manifest,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _source(text: str, *, suffix: str = ".py") -> SourceFile:
    return SourceFile(path=f"loopx/example{suffix}", suffix=suffix, text=text)


def test_python_scan_separates_codec_calls_from_direct_json_io() -> None:
    observations = scan_python_registry_io(
        _source(
            "import json\n"
            "def update(registry_path, payload):\n"
            "    current = load_registry(registry_path)\n"
            "    with source_session_registry_transaction(registry_path, "
            "operation='test'):\n"
            "        pass\n"
            "    raw = json.loads(registry_path.read_text())\n"
            "    atomic_write_json(registry_path, payload)\n"
            "    return current, raw\n"
        )
    )

    assert [
        (row.kind, row.api)
        for row in observations
    ] == [
        ("codec_read", "load_registry"),
        ("codec_transaction", "source_session_registry_transaction"),
        ("direct_json_read", "json.loads"),
        ("direct_json_write", "atomic_write_json"),
    ]


def test_python_scan_tracks_registry_file_handles() -> None:
    observations = scan_python_registry_io(
        _source(
            "import json\n"
            "def read_registry(path):\n"
            "    with path.open(encoding='utf-8') as handle:\n"
            "        return json.load(handle)\n"
        )
    )

    assert [(row.kind, row.api) for row in observations] == [
        ("direct_json_read", "json.load")
    ]


def test_python_scan_does_not_treat_global_registry_io_as_project_io() -> None:
    observations = scan_python_registry_io(
        _source(
            "def update(global_path, payload):\n"
            "    current = read_json(global_path)\n"
            "    atomic_write_json(global_path, payload)\n"
            "    return current\n"
        )
    )

    assert observations == []


def test_typescript_scan_finds_direct_project_registry_io() -> None:
    observations = collect_project_registry_io(
        REPO_ROOT,
        [
            _source(
                "import { readFileSync, writeFileSync } from 'node:fs';\n"
                "export function update(registryPath: string): void {\n"
                "  const payload = JSON.parse(readFileSync(registryPath, 'utf8'));\n"
                "  writeFileSync(registryPath, JSON.stringify(payload));\n"
                "}\n",
                suffix=".ts",
            )
        ],
    )

    assert [(row.kind, row.api) for row in observations] == [
        ("direct_json_read", "readFileSync"),
        ("direct_json_write", "writeFileSync"),
    ]


def test_manifest_rejects_unclassified_and_stale_direct_sites() -> None:
    sources = [
        _source(
            "def update(registry_path, payload):\n"
            "    atomic_write_json(registry_path, payload)\n"
        )
    ]
    manifest = build_project_registry_io_manifest(REPO_ROOT, sources=sources)
    errors = validate_project_registry_io_manifest(
        REPO_ROOT,
        manifest,
        sources=sources,
    )
    assert errors == [
        "unclassified direct project registry I/O: "
        "loopx/example.py::<module>.update::"
        "direct_json_write:atomic_write_json#1"
    ]

    classified = copy.deepcopy(manifest)
    classified["sites"][0]["classification"] = "legacy_registry_source"
    assert (
        validate_project_registry_io_manifest(
            REPO_ROOT,
            classified,
            sources=sources,
        )
        == []
    )

    stale = copy.deepcopy(classified)
    stale["sites"].append(
        {
            "site": "loopx/removed.py::<module>::codec_read:load_registry#1",
            "line": 1,
            "column": 1,
            "kind": "codec_read",
            "api": "load_registry",
            "classification": "codec_api",
        }
    )
    errors = validate_project_registry_io_manifest(
        REPO_ROOT,
        stale,
        sources=sources,
    )
    assert errors[0] == (
        "stale project registry I/O sites: "
        "['loopx/removed.py::<module>::codec_read:load_registry#1']"
    )

    duplicate = copy.deepcopy(classified)
    duplicate["sites"].append(copy.deepcopy(duplicate["sites"][0]))
    errors = validate_project_registry_io_manifest(
        REPO_ROOT,
        duplicate,
        sources=sources,
    )
    assert errors[0] == (
        "duplicate project registry I/O sites: "
        "['loopx/example.py::<module>.update::"
        "direct_json_write:atomic_write_json#1']"
    )


def test_checked_in_project_registry_io_manifest_is_current() -> None:
    manifest = json.loads(
        (REPO_ROOT / PROJECT_REGISTRY_IO_MANIFEST).read_text(encoding="utf-8")
    )
    assert validate_project_registry_io_manifest(REPO_ROOT, manifest) == []


def test_premerge_semantic_smoke_rejects_moved_registry_site(tmp_path: Path) -> None:
    # Moving a call must fail the same smoke selected by canary premerge,
    # even when the site's identity and classification are unchanged.
    manifest = json.loads(
        (REPO_ROOT / PROJECT_REGISTRY_IO_MANIFEST).read_text(encoding="utf-8")
    )
    manifest["sites"][0]["line"] += 1
    stale_manifest = tmp_path / "stale-census.json"
    stale_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    smoke = runpy.run_path(str(REPO_ROOT / "examples/semantic-vocabulary-drift-smoke.py"))
    smoke["main"].__globals__["PROJECT_REGISTRY_IO_MANIFEST"] = stale_manifest
    with pytest.raises(smoke["Drift"], match="project registry I/O site metadata changed"):
        smoke["main"]()
