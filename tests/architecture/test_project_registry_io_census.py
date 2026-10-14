"""Project-registry I/O stays behind the dual-format codec boundary."""

from __future__ import annotations

import copy
import json
import runpy
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.semantics.inventory import SourceFile
from loopx.semantics.project_registry_io import (
    DIRECT_IO_CLASSIFICATIONS_PATH,
    build_project_registry_io_manifest,
    collect_project_registry_io,
    load_project_registry_io_classifications,
    scan_python_registry_io,
    validate_project_registry_io_manifest,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DIRECT_SITE = (
    "loopx/example.py::<module>.update::direct_json_write:atomic_write_json#1"
)
DIRECT_SOURCE = (
    "def update(registry_path, payload):\n"
    "    atomic_write_json(registry_path, payload)\n"
)


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    policy = root / DIRECT_IO_CLASSIFICATIONS_PATH
    policy.parent.mkdir(parents=True)
    policy.write_text("{}\n", encoding="utf-8")
    (root / "loopx/example.py").write_text(DIRECT_SOURCE, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "add", "loopx/example.py"], cwd=root, check=True)
    return root


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
    manifest = build_project_registry_io_manifest(REPO_ROOT, sources=sources, classifications={})
    errors = validate_project_registry_io_manifest(
        REPO_ROOT,
        manifest,
        sources=sources,
        classifications={},
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
            classifications={DIRECT_SITE: "legacy_registry_source"},
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
        classifications={DIRECT_SITE: "legacy_registry_source"},
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
        classifications={DIRECT_SITE: "legacy_registry_source"},
    )
    assert errors[0] == (
        "duplicate project registry I/O sites: "
        "['loopx/example.py::<module>.update::"
        "direct_json_write:atomic_write_json#1']"
    )


def test_current_tree_is_valid_without_a_checked_in_census() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--", "loopx/semantics/project_registry_io_manifest_v1.json"],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    assert tracked.stdout == ""
    assert validate_project_registry_io_manifest(REPO_ROOT) == []


def test_moving_a_registered_call_needs_no_policy_change(source_repo: Path) -> None:
    policy = source_repo / DIRECT_IO_CLASSIFICATIONS_PATH
    policy.write_text(json.dumps({DIRECT_SITE: "legacy_registry_source"}), encoding="utf-8")
    before = build_project_registry_io_manifest(source_repo)
    (source_repo / "loopx/example.py").write_text("\n\n" + DIRECT_SOURCE, encoding="utf-8")
    after = build_project_registry_io_manifest(source_repo)
    assert before["sites"][0]["line"] == 2
    assert after["sites"][0]["line"] == 4
    assert after["sites"][0]["classification"] == "legacy_registry_source"
    assert validate_project_registry_io_manifest(source_repo) == []
    assert validate_project_registry_io_manifest(source_repo, before) == [
        f"project registry I/O site metadata changed: {DIRECT_SITE}"
    ]


def test_classifications_cannot_come_from_an_export() -> None:
    sources = [_source(DIRECT_SOURCE)]
    manifest = build_project_registry_io_manifest(
        REPO_ROOT, sources=sources, classifications={DIRECT_SITE: "legacy_registry_source"},
    )
    assert validate_project_registry_io_manifest(
        REPO_ROOT, manifest, sources=sources, classifications={},
    ) == [f"project registry I/O site metadata changed: {DIRECT_SITE}"]


def test_legacy_snapshot_does_not_affect_current_check(source_repo: Path) -> None:
    policy = source_repo / DIRECT_IO_CLASSIFICATIONS_PATH
    policy.write_text(json.dumps({DIRECT_SITE: "legacy_registry_source"}), encoding="utf-8")
    legacy = source_repo / "loopx/semantics/project_registry_io_manifest_v1.json"
    legacy.write_text("obsolete report, not JSON", encoding="utf-8")
    assert validate_project_registry_io_manifest(source_repo) == []


def test_stale_and_invalid_classifications_are_rejected() -> None:
    assert validate_project_registry_io_manifest(
        REPO_ROOT, sources=[], classifications={DIRECT_SITE: "legacy_registry_source"},
    ) == [f"stale project registry I/O classifications: ['{DIRECT_SITE}']"]
    errors = validate_project_registry_io_manifest(
        REPO_ROOT, sources=[_source(DIRECT_SOURCE)], classifications={DIRECT_SITE: "codec_api"},
    )
    assert errors == [
        f"invalid project registry I/O classification: {DIRECT_SITE}: codec_api",
        f"unclassified direct project registry I/O: {DIRECT_SITE}",
    ]


@pytest.mark.parametrize("payload", [[], {"site": 42}])
def test_classification_file_requires_a_string_map(source_repo: Path, payload: object) -> None:
    (source_repo / DIRECT_IO_CLASSIFICATIONS_PATH).write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="classifications must map sites to strings"):
        load_project_registry_io_classifications(source_repo)


def test_premerge_semantic_smoke_rejects_unclassified_registry_site(source_repo: Path) -> None:
    smoke = runpy.run_path(str(REPO_ROOT / "examples/semantic-vocabulary-drift-smoke.py"))
    smoke["main"].__globals__["validate_project_registry_io_manifest"] = (
        lambda _: validate_project_registry_io_manifest(source_repo)
    )
    with pytest.raises(smoke["Drift"], match="unclassified direct project registry I/O"):
        smoke["main"]()


def test_generator_stdout_and_explicit_export_check(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = source_repo / DIRECT_IO_CLASSIFICATIONS_PATH
    policy.write_text(json.dumps({DIRECT_SITE: "legacy_registry_source"}), encoding="utf-8")
    script = REPO_ROOT / "scripts/generate_project_registry_io_manifest.py"
    main = runpy.run_path(str(script))["main"]
    main.__globals__["ROOT"] = source_repo
    monkeypatch.setattr(sys, "argv", [str(script)])
    before = sorted(source_repo.rglob("*"))
    assert main() == 0
    stdout = capsys.readouterr().out
    assert json.loads(stdout)["sites"][0]["classification"] == "legacy_registry_source"
    assert sorted(source_repo.rglob("*")) == before

    # The export is outside the source root and has no authority over policy.
    output = tmp_path / "report/census.json"
    monkeypatch.setattr(sys, "argv", [str(script), "--output", str(output)])
    assert main() == 0
    assert output.read_text(encoding="utf-8") == stdout
    monkeypatch.setattr(sys, "argv", [str(script), "--output", str(output), "--check"])
    assert main() == 0
    (source_repo / "loopx/example.py").write_text("\n" + DIRECT_SOURCE, encoding="utf-8")
    assert main() == 1
    assert output.read_text(encoding="utf-8") == stdout
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", [str(script), "--check"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_generator_does_not_reuse_report_classifications(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "census.json"
    output.write_text(json.dumps({"sites": [{
        "site": DIRECT_SITE, "classification": "legacy_registry_source",
    }]}), encoding="utf-8")
    script = REPO_ROOT / "scripts/generate_project_registry_io_manifest.py"
    main = runpy.run_path(str(script))["main"]
    main.__globals__["ROOT"] = source_repo
    monkeypatch.setattr(sys, "argv", [str(script), "--output", str(output)])
    assert main() == 1
    assert json.loads(output.read_text(encoding="utf-8"))["sites"][0]["classification"] == "unclassified"
    assert "unclassified direct project registry I/O" in capsys.readouterr().err
