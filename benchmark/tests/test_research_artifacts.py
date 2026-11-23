"""Research retention must not silently alter a native candidate submission."""
from copy import deepcopy

import pytest

from benchmark.runtime.research_artifacts import (
    CORE_DUMP_EXCLUDE_PATTERN,
    RESEARCH_COMPONENT,
    RESEARCH_DIRECTORY,
    is_confirmed_core_dump_header,
    is_excluded_research_relpath,
    is_excluded_tar_member,
    is_runner_research_component,
    research_component_exclusions,
    selected_research_components,
    validate_research_scope,
    with_research_capture,
)


def test_whole_workspace_submission_and_explicit_files_allow_external_research():
    validate_research_scope("/task", ["."])
    validate_research_scope("/task", ["solver.py", "/opt/candidate"])


@pytest.mark.parametrize("workspace, paths", [
    ("/tmp", ["."]), ("/task", [RESEARCH_DIRECTORY]),
    (RESEARCH_DIRECTORY + "/task", ["."]), ("/task", ["/tmp"]),
    ("relative", ["."]), ("/task", ["../tmp"]),
])
def test_ambiguous_or_overlapping_submission_scope_is_not_rewritten(workspace, paths):
    before = deepcopy(paths)
    with pytest.raises(ValueError):
        validate_research_scope(workspace, paths)
    assert paths == before


def test_capture_adds_only_reserved_component_and_never_changes_original_request():
    request = {"destination": "/logs/checkpoints", "point_metadata": {"fixture": True},
               "components": [{"id": "workspace", "source_path": "/task"}]}
    before = deepcopy(request)
    captured = with_research_capture(request)
    assert request == before
    assert captured["components"] == [*before["components"],
        {"id": RESEARCH_COMPONENT, "source_path": RESEARCH_DIRECTORY}]
    assert with_research_capture(captured) == captured
    assert captured["point_metadata"] == before["point_metadata"]


@pytest.mark.parametrize("entry", [
    {"id": RESEARCH_COMPONENT, "source_path": "/tmp/unrelated"},
    {"id": "unrelated", "source_path": RESEARCH_DIRECTORY},
    {"id": "parent", "source_path": "/tmp"},
    {"id": "child", "source_path": RESEARCH_DIRECTORY + "/notes"},
])
def test_capture_conflicts_preserve_original_declaration(entry):
    request = {"components": [entry]}
    before = deepcopy(request)
    with pytest.raises(ValueError):
        with_research_capture(request)
    assert request == before


def test_only_exact_managed_component_is_selected_without_an_operator_override():
    entries = [{"id": "other-research", "source_path": "/tmp/other"}]
    metadata = {"worker_file_snapshot": {"components": entries}}
    assert selected_research_components(metadata, (), managed=True) == ()
    entries.append({"id": RESEARCH_COMPONENT, "source_path": RESEARCH_DIRECTORY})
    assert selected_research_components(metadata, (), managed=True) == (RESEARCH_COMPONENT,)
    assert selected_research_components(metadata, (), managed=False) == ()
    assert selected_research_components(metadata, ("other-research",), managed=True) == (
        "other-research", RESEARCH_COMPONENT)
    entries[-1]["source_path"] = "/tmp/foreign"
    with pytest.raises(ValueError, match="conflicting identity"):
        selected_research_components(metadata, (), managed=True)


@pytest.mark.parametrize("profile", ["official", "native-goal", "heartbeat-resume", "heartbeat-explore"])
def test_native_entry_installs_identical_research_scope_and_declares_checkpoint_coverage(
        tmp_path, monkeypatch, profile):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    import json
    from types import SimpleNamespace
    from benchmark.edgebench import run
    monkeypatch.setenv("LOOPX_SRC_DIR", str(tmp_path))
    monkeypatch.setenv("LOOPX_EXPECTED_COMMIT", "fixture")
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-login")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / "fixture.json").write_text("{}")
    monkeypatch.setattr(run, "source_pins", lambda *a: ("fixture", "fixture"))
    monkeypatch.setattr(run, "load_benchmark", lambda *a: None)
    monkeypatch.setattr(run, "make_task_spec", lambda *a: SimpleNamespace(
        cwd="/task", work_image_key="work", judge_image_key="judge", internet=False,
        game_mode=False, submit_paths=["."], work=SimpleNamespace(agent_query="Original acceptance."),
        judge=SimpleNamespace(selection="score_first", score_direction="maximize")))
    def backend(**kwargs):
        assert kwargs["research_sidecar"] is True
        return SimpleNamespace(observe_local_images=lambda **keys: {},
                               registration_options=lambda provider: {})
    monkeypatch.setattr(run, "RecordingDockerBackend", backend)
    capture = profile.startswith("heartbeat-")
    request = {"destination": "/logs/agent/checkpoints", "components": [
        {"id": "workspace", "source_path": "/task"}], "point_metadata": {}}
    source = tmp_path / "capture.json"
    source.write_text(json.dumps(request))
    def boundary(**kwargs):
        worker = kwargs["agent"]
        assert worker.research_sidecar is True
        assert RESEARCH_DIRECTORY in worker.workspace_instructions
        assert worker.feedback_prompt.startswith(worker.workspace_instructions)
        assert worker.task_objective == "Original acceptance."
        if capture:
            assert json.loads(worker.checkpoint_request) == with_research_capture(request)
        else:
            assert worker.checkpoint_request is None
        raise RuntimeError("synthetic launch boundary")
    monkeypatch.setattr(run, "run_agent", boundary)
    argv = ["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
        "--run-id", "fixture", "--worker", profile, "--model", "fixture", "--effort", "xhigh",
        "--judge-url", "http://127.0.0.1:9999", "--feedback", "native"]
    if capture:
        argv += ["--checkpoint-request", str(source)]
    with pytest.raises(RuntimeError, match="synthetic launch boundary"):
        run.main(argv)
    assert json.loads(source.read_bytes()) == request


def _elf_bytes(e_type: int, *, little_endian: bool = True) -> bytes:
    buf = bytearray(64)
    buf[0:4] = b"\x7fELF"
    buf[4] = 2
    buf[5] = 1 if little_endian else 2
    buf[16:18] = e_type.to_bytes(2, "little" if little_endian else "big")
    return bytes(buf)


def _macho_bytes(filetype: int, *, little_endian: bool = True) -> bytes:
    buf = bytearray(32)
    if little_endian:
        buf[0:4] = b"\xcf\xfa\xed\xfe"
        buf[12:16] = filetype.to_bytes(4, "little")
    else:
        buf[0:4] = b"\xfe\xed\xfa\xcf"
        buf[12:16] = filetype.to_bytes(4, "big")
    return bytes(buf)


def test_core_dump_filter_preserves_normal_core_dirs_and_plain_files():
    import io
    import tarfile

    assert research_component_exclusions([{"id": RESEARCH_COMPONENT, "source_path": RESEARCH_DIRECTORY}]) == {
        RESEARCH_COMPONENT: ["*.raw.gz", CORE_DUMP_EXCLUDE_PATTERN, "target/", ".cargo-target/"],
    }
    assert is_confirmed_core_dump_header(_elf_bytes(4, little_endian=True))
    assert is_confirmed_core_dump_header(_elf_bytes(4, little_endian=False))
    assert is_confirmed_core_dump_header(_macho_bytes(4, little_endian=True))
    assert is_confirmed_core_dump_header(_macho_bytes(4, little_endian=False))
    assert not is_confirmed_core_dump_header(_elf_bytes(2))
    assert not is_confirmed_core_dump_header(_macho_bytes(2))
    assert not is_confirmed_core_dump_header(b"core-dump text")
    assert not is_excluded_research_relpath(("core", "analysis.md"), is_dir=False, read_header=lambda: b"# notes")
    assert not is_excluded_research_relpath(("topic", "core"), is_dir=False, read_header=lambda: b"text")
    assert is_excluded_research_relpath(("core",), is_dir=False, read_header=lambda: _elf_bytes(4))
    assert is_excluded_research_relpath(("sub", "core.77"), is_dir=False, read_header=lambda: _macho_bytes(4))

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        entries = (
            ("loopx-benchmark-research/core/analysis.md", b"# analysis\n"),
            ("loopx-benchmark-research/topic/core", b"ordinary topic notes\n"),
            ("loopx-benchmark-research/bin/core", _elf_bytes(2)),
            ("loopx-benchmark-research/core", _elf_bytes(4)),
            ("loopx-benchmark-research/sub/core.123", _macho_bytes(4)),
            ("loopx-benchmark-research/dump.raw.gz", b"gz"),
            ("loopx-benchmark-research/target/release/app", b"elf"),
        )
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r") as archive:
        kept = [
            m.name for m in archive.getmembers()
            if not is_excluded_tar_member(archive, m, tuple(m.name.split("/")[1:]))
        ]
    assert kept == [
        "loopx-benchmark-research/core/analysis.md",
        "loopx-benchmark-research/topic/core",
        "loopx-benchmark-research/bin/core",
    ]


def test_caller_chosen_benchmark_research_id_preserves_declared_files_end_to_end(tmp_path):
    from loopx.control_plane.effect_runtime import effect_runtime_result

    caller_dir = tmp_path / "caller-research"
    caller_dir.mkdir()
    (caller_dir / "experiment.raw.gz").write_bytes(b"raw-gz-payload")
    (caller_dir / "notes.md").write_text("# caller notes\n", encoding="utf-8")

    components = [{"id": RESEARCH_COMPONENT, "source_path": str(caller_dir)}]
    assert not is_runner_research_component(components[0])
    exclusions = research_component_exclusions(components)
    assert exclusions == {}

    probe = effect_runtime_result(
        "runtime.checkpoint_artifacts",
        {"action": "probe", "components": components, **({"component_exclusions": exclusions} if exclusions else {})},
    )
    unit = tmp_path / "captured-unit"
    capture = effect_runtime_result(
        "runtime.checkpoint_artifacts",
        {
            "action": "capture",
            "components": components,
            "destination": str(unit),
            "expected_source_sha256": probe["source_sha256"],
            "point_metadata": {"caller": True},
            **({"component_exclusions": exclusions} if exclusions else {}),
        },
    )
    effect_runtime_result(
        "runtime.checkpoint_artifacts",
        {"action": "verify", "root": str(unit), "expected_sha256": capture["sha256"]},
    )
    restored = tmp_path / "materialized"
    effect_runtime_result(
        "runtime.checkpoint_artifacts",
        {
            "action": "materialize",
            "root": str(unit),
            "expected_sha256": capture["sha256"],
            "destination": str(restored),
        },
    )
    comp_root = restored / "components" / RESEARCH_COMPONENT
    assert (comp_root / "experiment.raw.gz").read_bytes() == b"raw-gz-payload"
    assert (comp_root / "notes.md").read_text(encoding="utf-8") == "# caller notes\n"


def test_archive_exclude_research_skips_ephemeral_dumps(tmp_path):
    import base64
    import io
    import json
    import tarfile
    from types import SimpleNamespace
    from benchmark.edgebench import checkpoint_worker

    elf_core = b"\x7fELF\x02\x01" + b"\x00" * 10 + (4).to_bytes(2, "little") + b"\x00" * 46
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        for d in ("loopx-benchmark-research", "loopx-benchmark-research/topic", "loopx-benchmark-research/sub"):
            dinfo = tarfile.TarInfo(d)
            dinfo.type = tarfile.DIRTYPE
            dinfo.mode = 0o700
            archive.addfile(dinfo)
        for rel, body in (
            ("loopx-benchmark-research/candidate.py", b"print('ok')\n"),
            ("loopx-benchmark-research/topic/core", b"ordinary topic evidence\n"),
            ("loopx-benchmark-research/dump.raw.gz", b"gz"),
            ("loopx-benchmark-research/core", elf_core),
            ("loopx-benchmark-research/target/release/bin", b"bin"),
            ("loopx-benchmark-research/.cargo-target/x.rlib", b"rlib"),
        ):
            info = tarfile.TarInfo(rel)
            info.size = len(body)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(body))
        for link_rel in ("loopx-benchmark-research/alias_note", "loopx-benchmark-research/sub/core.2"):
            linfo = tarfile.TarInfo(link_rel)
            linfo.type = tarfile.LNKTYPE
            linfo.linkname = "loopx-benchmark-research/core"
            linfo.mode = 0o644
            archive.addfile(linfo)

    stat_hdr = base64.b64encode(json.dumps({"linkTarget": ""}).encode("utf-8")).decode("ascii")
    response = SimpleNamespace(
        headers={"x-docker-container-path-stat": stat_hdr},
        iter_content=lambda chunk_size, decode_unicode=False: iter([payload.getvalue()]),
        close=lambda: None,
    )
    container = SimpleNamespace(
        id="c1",
        client=SimpleNamespace(
            api=SimpleNamespace(
                _url=lambda tpl, cid: tpl.format(cid),
                _get=lambda url, **kwargs: response,
                _raise_for_status=lambda resp: None,
            )
        ),
    )
    extracted = checkpoint_worker._archive(
        container, "/tmp/loopx-benchmark-research", tmp_path / "dest", exclude_research=True,
    )
    assert (extracted / "candidate.py").read_bytes() == b"print('ok')\n"
    assert (extracted / "topic/core").read_bytes() == b"ordinary topic evidence\n"
    assert (extracted / "alias_note").read_bytes() == elf_core
    assert not (extracted / "dump.raw.gz").exists()
    assert not (extracted / "core").exists()
    assert not (extracted / "sub/core.2").exists()
    assert not (extracted / "target").exists()
    assert not (extracted / ".cargo-target").exists()


def test_archive_exclude_research_hardlink_matches_local_probe_witness(tmp_path):
    import base64
    import io
    import json
    import os
    import tarfile
    from types import SimpleNamespace
    from benchmark.edgebench import checkpoint_worker
    from benchmark.runtime.research_artifacts import RESEARCH_EXCLUDE_PATTERNS
    from loopx.control_plane.effect_runtime import effect_runtime_result

    elf_core = b"\x7fELF\x02\x01" + b"\x00" * 10 + (4).to_bytes(2, "little") + b"\x00" * 46
    local_root = tmp_path / "local" / "loopx-benchmark-research"
    (local_root / "sub").mkdir(parents=True)
    local_root.chmod(0o700)
    (local_root / "sub").chmod(0o700)
    (local_root / "candidate.py").write_text("score = 1\n", encoding="utf-8")
    (local_root / "core").write_bytes(elf_core)
    os.link(local_root / "core", local_root / "alias_note")
    os.link(local_root / "core", local_root / "sub" / "core.2")

    exclusions = {RESEARCH_COMPONENT: list(RESEARCH_EXCLUDE_PATTERNS)}
    prepared = effect_runtime_result(
        "runtime.checkpoint_artifacts",
        {
            "action": "probe",
            "components": [{"id": RESEARCH_COMPONENT, "source_path": str(local_root)}],
            "component_exclusions": exclusions,
        },
    )

    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        archive.add(local_root, arcname="loopx-benchmark-research")
    stat_hdr = base64.b64encode(json.dumps({"linkTarget": ""}).encode("utf-8")).decode("ascii")
    response = SimpleNamespace(
        headers={"x-docker-container-path-stat": stat_hdr},
        iter_content=lambda chunk_size, decode_unicode=False: iter([payload.getvalue()]),
        close=lambda: None,
    )
    container = SimpleNamespace(
        id="c1",
        client=SimpleNamespace(
            api=SimpleNamespace(
                _url=lambda tpl, cid: tpl.format(cid),
                _get=lambda url, **kwargs: response,
                _raise_for_status=lambda resp: None,
            )
        ),
    )
    extracted = checkpoint_worker._archive(
        container, "/tmp/loopx-benchmark-research", tmp_path / "dest", exclude_research=True,
    )
    observed = effect_runtime_result(
        "runtime.checkpoint_artifacts",
        {
            "action": "probe",
            "components": [{"id": RESEARCH_COMPONENT, "source_path": str(extracted)}],
            "component_exclusions": exclusions,
        },
    )
    assert observed["source_sha256"] == prepared["source_sha256"]
    assert observed["source_witness"] == prepared["source_witness"]
    assert [row["path"] for row in observed["source_witness"][RESEARCH_COMPONENT]] == [
        "",
        "alias_note",
        "candidate.py",
        "sub",
    ]
