from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from inspect import signature
from pathlib import Path

import pytest

from loopx.extensions.lark.presentation.explore_results import (
    LarkExploreConfig,
    sync_issue_fix_explore_on_material_change,
    sync_explore_results_to_lark,
    sync_explore_visual_to_lark,
    sync_explore_visuals_to_lark,
)
from loopx.extensions.lark.presentation.explore_stage_document import (
    ensure_stage_whiteboards,
)
from loopx.extensions.lark.presentation.kanban import (
    LarkKanbanConfig,
    _public_safe_lark_values,
    _public_safe_text,
    build_record_upsert_command,
    lark_kanban_heartbeat,
    seed_lark_kanban_records,
    seed_lark_kanban_task,
    sync_loopx_projection_to_lark_kanban,
    sync_loopx_todos_to_lark_kanban,
)
from loopx.extensions.lark.presentation.sink_visibility import (
    SINK_VISIBILITY_OWNER_ONLY,
    SINK_VISIBILITY_SHARED,
)


PROVIDER_TOKENS = (
    pytest.param("gh" "p_" + "A" * 36, id="github"),
    pytest.param("AK" "IA" + "A" * 16, id="aws"),
    pytest.param("xox" "b-" + "a" * 24, id="slack"),
    pytest.param("AI" "za" + "a" * 24, id="google"),
    pytest.param("sk_" "live_" + "a" * 24, id="stripe"),
    pytest.param("np" "m_" + "a" * 24, id="npm"),
    pytest.param("py" "pi-" + "a" * 24, id="pypi"),
    pytest.param("ey" "J" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12, id="jwt"),
    pytest.param("Bear" "er " + "a" * 24, id="bearer"),
    pytest.param("AWS_SECRET_ACCESS_KEY=" + "a1B2" * 10, id="aws-secret-access-key"),
)


def _kanban_record_text(title: str, *, sink_visibility: str, tmp_path: Path) -> str:
    packet = sync_loopx_projection_to_lark_kanban(
        LarkKanbanConfig(
            base_token="base_public_fixture",
            table_id="tbl_public_fixture",
            cli_bin="lark-cli",
            identity="user",
        ),
        projection={
            "schema_version": "goal_channel_projection_v0",
            "goal_id": "goal_public_fixture",
            "source_id": "source-public-fixture",
            "agent_todos": [
                {
                    "todo_id": "todo_public_fixture",
                    "title": title,
                    "status": "open",
                    "task_class": "advancement_task",
                }
            ],
        },
        config_path=tmp_path / "lark-kanban.json",
        sink_visibility=sink_visibility,
    )
    return repr(packet["records"])


def _explore_record_text(title: str, *, sink_visibility: str, tmp_path: Path) -> str:
    packet = sync_explore_results_to_lark(
        LarkExploreConfig(
            base_token="base_public_fixture",
            table_ids={"nodes": "tblN", "edges": "tblE", "findings": "tblF"},
        ),
        projection={
            "schema_version": "loopx_explore_result_projection_v0",
            "goal_id": "goal_public_fixture",
            "nodes": [
                {
                    "node_id": "candidate",
                    "title": title,
                    "node_kind": "experiment",
                    "status": "resolved",
                    "parent_id": "",
                    "tags": [],
                    "summary": "fixture",
                }
            ],
            "edges": [],
            "findings": [],
        },
        config_path=tmp_path / "lark-explore.json",
        sink_visibility=sink_visibility,
    )
    return repr(packet["records"])


def _sync_todos_record_text(title: str, *, tmp_path: Path) -> str:
    project = tmp_path / "project"
    registry_path = project / ".loopx" / "registry.json"
    state_file = project / "ACTIVE_GOAL_STATE.md"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": "goal_public_fixture",
                        "repo": str(project),
                        "state_file": str(state_file),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    state_file.write_text(
        f"""# Active Goal State

## User Todo / Owner Review Reading Queue

## Agent Todo

- [ ] [P1] {title}
  <!-- loopx:todo todo_id=todo_secret status=open task_class=advancement_task -->
""",
        encoding="utf-8",
    )
    packet = sync_loopx_todos_to_lark_kanban(
        LarkKanbanConfig(
            base_token="base_public_fixture",
            table_id="tbl_public_fixture",
            cli_bin="lark-cli",
            identity="user",
        ),
        registry_path=registry_path,
        goal_id="goal_public_fixture",
    )
    return repr(packet["records"])


def _heartbeat_fixture() -> dict[str, object]:
    return {
        "data": {
            "fields": [
                "Status",
                "Claim",
                "Claimed By",
                "Worker Command",
                "Workdir",
                "Run History",
                "Evidence",
            ],
            "data": [["Todo", "Unclaimed", "", "echo fixture", ".", "", ""]],
            "record_id_list": ["rec_public_fixture"],
        }
    }


def _private_key_block() -> tuple[str, str]:
    body = "MIIE" + "syntheticPrivateKeyBody" * 4
    return (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        + body
        + "\n-----END RSA PRIVATE KEY-----",
        body,
    )


@pytest.mark.parametrize("sink", [pytest.param("kanban", id="kanban"), pytest.param("explore", id="explore")])
@pytest.mark.parametrize("credential", PROVIDER_TOKENS)
def test_shared_sink_redacts_canonical_provider_credentials(
    sink: str,
    credential: str,
    tmp_path: Path,
) -> None:
    title = f"Rotate leaked token {credential}"
    record_text = (
        _kanban_record_text(title, sink_visibility="shared", tmp_path=tmp_path)
        if sink == "kanban"
        else _explore_record_text(title, sink_visibility="shared", tmp_path=tmp_path)
    )

    assert credential not in record_text
    assert "[credential-redacted]" in record_text


def test_shared_sink_keeps_existing_redactions() -> None:
    text = " ".join(
        (
            "file://" + "/Us" "ers/operator/run.log",
            "https://" + "example.feishu.cn/base/private",
            "base" + "abcdef123456",
            "PRIVATE_" + "REF_FIXTURE",
        )
    )

    redacted = _public_safe_text(text)

    assert "[local-path-redacted]" in redacted
    assert "[private-link-redacted]" in redacted
    assert "[external-id-redacted]" in redacted
    assert "[private-ref-redacted]" in redacted


@pytest.mark.parametrize(
    "local_path",
    [
        pytest.param(r"C:\Users\operator\secret.txt", id="windows-backslash"),
        pytest.param("C:/Users/operator/secret.txt", id="windows-slash"),
        pytest.param(r"\\server\share\secret.txt", id="unc"),
    ],
)
def test_shared_sink_redacts_windows_and_unc_paths(local_path: str) -> None:
    redacted = _public_safe_text(f"Read {local_path} before continuing")

    assert local_path not in redacted
    assert "[local-path-redacted]" in redacted


@pytest.mark.parametrize(
    "credential",
    [
        pytest.param("GITHUB_TOKEN=" + "a1B2" * 10, id="github-env"),
        pytest.param("CLIENT_SECRET=" + "a1B2" * 10, id="client-secret-env"),
        pytest.param("AWS_SESSION_TOKEN=" + "a1B2" * 10, id="aws-session-env"),
        pytest.param("DB_PASSWORD=" + "a1B2" * 10, id="database-password-env"),
        pytest.param(
            "postgres://operator:" + "a1B2" * 10 + "@db.example/app",
            id="database-uri-userinfo",
        ),
    ],
)
def test_shared_sink_redacts_compound_credential_forms(credential: str) -> None:
    redacted = _public_safe_text(f"Rotate {credential} before retrying")

    assert credential not in redacted
    assert "[credential-redacted]" in redacted


def test_shared_sink_normalizes_terminal_controls_before_detection() -> None:
    credential = "gh" "p_" + "A" * 36
    obfuscated = "gh" "p_" + "\x1b[31m" + "A" * 36 + "\x1b[0m"

    redacted = _public_safe_text(f"Rotate {obfuscated}")

    assert credential not in redacted
    assert "\x1b" not in redacted
    assert "[credential-redacted]" in redacted


def test_shared_sink_redacts_credentials_before_truncation() -> None:
    credential = "gh" "p_" + "A" * 36
    text = "x" * 69 + " " + credential

    redacted = _public_safe_text(text, limit=80)

    assert "gh" "p_" not in redacted


def test_shared_sink_redacts_nested_text_without_breaking_record_links() -> None:
    credential = "gh" "p_" + "A" * 36
    local_path = "file://" + "/Us" "ers/operator/run.log"

    values = _public_safe_lark_values(
        {
            "Evidence": [{"text": f"token {credential} in {local_path}"}],
            "From Node Link": [{"id": "rec_public_fixture"}],
        }
    )
    serialized = json.dumps(values)

    assert credential not in serialized
    assert local_path not in serialized
    assert "[credential-redacted]" in serialized
    assert "[local-path-redacted]" in serialized
    assert values["From Node Link"] == [{"id": "rec_public_fixture"}]


def test_shared_sink_redacts_private_content_in_nested_id_fields() -> None:
    local_path = "file://" + "/Us" "ers/operator/run.log"
    private_link = "https://" + "example.feishu.cn/base/private"
    private_ref = "PRIVATE_" + "REF_FIXTURE"

    values = _public_safe_lark_values(
        {
            "Evidence": [
                {"id": local_path},
                {"record_id": private_link},
                {"record_ids": [private_ref]},
            ]
        }
    )
    serialized = json.dumps(values)

    assert local_path not in serialized
    assert private_link not in serialized
    assert private_ref not in serialized
    assert "[local-path-redacted]" in serialized
    assert "[private-link-redacted]" in serialized
    assert "[private-ref-redacted]" in serialized


def test_shared_sink_redacts_private_content_in_nested_dict_keys() -> None:
    credential = "gh" "p_" + "A" * 36
    local_path = r"C:\Users\operator\secret.txt"

    values = _public_safe_lark_values(
        {
            "Evidence": [
                {f"token {credential}": "value"},
                {local_path: "value"},
            ]
        }
    )
    serialized = json.dumps(values)

    assert credential not in serialized
    assert local_path not in serialized
    assert "[credential-redacted]" in serialized
    assert "[local-path-redacted]" in serialized


@pytest.mark.parametrize("sink_visibility", ["shared", "owner-only"])
def test_stage_document_redacts_lane_labels_before_docs_update(
    sink_visibility: str,
    tmp_path: Path,
) -> None:
    credential = "gh" "p_" + "A" * 36
    local_path = r"C:\Users\operator\secret.txt"
    contents: list[str] = []
    created = False

    def runner(
        args: list[str], cwd: Path | None, _timeout: float | None
    ) -> dict[str, object]:
        nonlocal created
        if "docs" in args and "+update" in args:
            contents.append(args[args.index("--content") + 1])
            created = True
            return {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "document": {
                                "new_blocks": [
                                    {
                                        "block_id": "block_stage_02",
                                        "block_type": "whiteboard",
                                        "block_token": "wb_stage_02",
                                    }
                                ]
                            }
                        },
                    }
                ),
                "stderr": "",
            }
        assert "docs" in args and "+fetch" in args
        heading = (
            '<h2 id="heading_stage_02">Executive Evidence Stage 02</h2>'
            if created
            else ""
        )
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "ok": True,
                    "data": {
                        "document": {
                            "revision_id": 1,
                            "content": (
                                '<fragment mode="outline"><outline>'
                                + heading
                                + "</outline></fragment>"
                            ),
                        }
                    },
                }
            ),
            "stderr": "",
        }

    config_path = tmp_path / "lark-explore.json"
    _, _, _, _, error = ensure_stage_whiteboards(
        LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE"),
        role="executive",
        role_sink={
            "docx_token": "doc_public_fixture",
            "stage_whiteboards": [
                {"stage_index": 1, "whiteboard_token": "wb_stage_01"}
            ],
        },
        stage_views=[
            {
                "stage_index": 1,
                "lanes": ["capability"],
                "primary_node_count": 1,
                "context_node_count": 0,
                "cross_lane_edge_count": 0,
            },
            {
                "stage_index": 2,
                "lanes": [credential, local_path, "R&D"],
                "primary_node_count": 1,
                "context_node_count": 0,
                "cross_lane_edge_count": 0,
            },
        ],
        config_path=config_path,
        sink_visibility=sink_visibility,
        execute=True,
        runner=runner,
        read_local_config=lambda _path: {
            "visual_sinks": {"executive": {}},
        },
        write_local_config=lambda _path, _payload: None,
    )

    assert error is None
    assert len(contents) == 1
    content = contents[0]
    assert credential not in content
    assert "ghp" not in content.casefold()
    assert "R&amp;D" in content
    if sink_visibility == "shared":
        assert local_path not in content
        assert "[local-path-redacted]" in content
    else:
        assert local_path.casefold() in content.casefold()


@pytest.mark.parametrize("sink", [pytest.param("kanban", id="kanban"), pytest.param("explore", id="explore")])
@pytest.mark.parametrize("sink_visibility", ["owner-only", "shared"])
def test_private_key_body_is_removed_from_record_sinks(
    sink: str,
    sink_visibility: str,
    tmp_path: Path,
) -> None:
    private_key, body = _private_key_block()
    record_text = (
        _kanban_record_text(
            f"Rotate key {private_key}",
            sink_visibility=sink_visibility,
            tmp_path=tmp_path,
        )
        if sink == "kanban"
        else _explore_record_text(
            f"Rotate key {private_key}",
            sink_visibility=sink_visibility,
            tmp_path=tmp_path,
        )
    )

    assert body not in record_text
    assert "[credential-redacted]" in record_text


@pytest.mark.parametrize("sink", [pytest.param("kanban", id="kanban"), pytest.param("explore", id="explore")])
def test_owner_only_sink_still_redacts_credentials(sink: str, tmp_path: Path) -> None:
    credential = "gh" "p_" + "A" * 36
    local_path = "file://" + "/Us" "ers/operator/run.log"
    title = f"Owner-only {credential} at {local_path}"
    record_text = (
        _kanban_record_text(title, sink_visibility="owner-only", tmp_path=tmp_path)
        if sink == "kanban"
        else _explore_record_text(title, sink_visibility="owner-only", tmp_path=tmp_path)
    )

    assert credential not in record_text
    assert "[credential-redacted]" in record_text
    assert local_path in record_text


def test_sync_loopx_todos_redacts_credentials_and_private_paths(tmp_path: Path) -> None:
    credential = "gh" "p_" + "A" * 36
    local_path = "file://" + "/Us" "ers/operator/run.log"

    record_text = _sync_todos_record_text(
        f"Rotate {credential} from {local_path}",
        tmp_path=tmp_path,
    )

    assert credential not in record_text
    assert local_path not in record_text
    assert "[credential-redacted]" in record_text
    assert "[local-path-redacted]" in record_text


def test_heartbeat_writeback_redacts_worker_output(tmp_path: Path) -> None:
    credential = "gh" "p_" + "A" * 36

    def runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, object]:
        assert args == ["echo", "fixture"]
        return {
            "returncode": 1,
            "stdout": f"worker token {credential}",
            "stderr": f"AWS_SECRET_ACCESS_KEY={credential}",
            "timed_out": False,
        }

    packet = lark_kanban_heartbeat(
        LarkKanbanConfig(
            base_token="base_public_fixture",
            table_id="tbl_public_fixture",
            cli_bin="lark-cli",
            identity="user",
        ),
        fixture=_heartbeat_fixture(),
        worker_command="echo fixture",
        execute_worker=True,
        allowed_command_prefixes=["echo"],
        runner=runner,
    )

    assert credential not in repr(packet["writeback"])
    assert credential not in repr(packet["worker"])
    assert credential not in repr(packet["commands"])
    assert "[credential-redacted]" in packet["writeback"]["Evidence"]
    assert "[credential-redacted]" in packet["writeback"]["Last Error"]


def test_heartbeat_redacts_private_key_body_from_worker_outputs(tmp_path: Path) -> None:
    private_key, body = _private_key_block()

    def runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, object]:
        assert args == ["echo", "fixture"]
        return {
            "returncode": 1,
            "stdout": f"worker key {private_key}",
            "stderr": f"failed with {private_key}",
            "timed_out": False,
        }

    packet = lark_kanban_heartbeat(
        LarkKanbanConfig(
            base_token="base_public_fixture",
            table_id="tbl_public_fixture",
            cli_bin="lark-cli",
            identity="user",
        ),
        fixture=_heartbeat_fixture(),
        worker_command="echo fixture",
        execute_worker=True,
        allowed_command_prefixes=["echo"],
        runner=runner,
    )

    assert body not in repr(packet["writeback"])
    assert body not in repr(packet["worker"])
    assert body not in repr(packet["commands"])
    assert "[credential-redacted]" in packet["writeback"]["Evidence"]
    assert "[credential-redacted]" in packet["writeback"]["Last Error"]


def test_shared_visual_sink_redacts_the_rendered_source(tmp_path: Path) -> None:
    credential = "gh" "p_" + "A" * 36
    local_path = "file://" + "/Us" "ers/operator/run.log"
    private_link = "https://" + "example.feishu.cn/base/private"
    published_source: list[str] = []
    published_marker = ""

    def runner(args: list[str], cwd: Path | None, _timeout: float | None) -> dict[str, object]:
        nonlocal published_marker
        if "+update" in args:
            source_arg = args[args.index("--source") + 1]
            assert cwd is not None
            source = (cwd / source_arg.removeprefix("@")).read_text(encoding="utf-8")
            published_source.append(source)
            marker = re.search(r"LoopX delivery [0-9a-f]{20}", source)
            assert marker is not None
            published_marker = marker.group(0)
            return {"returncode": 0, "stdout": json.dumps({"ok": True}), "stderr": ""}
        assert "+query" in args
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "ok": True,
                    "data": {"nodes": [{"text": {"text": published_marker}}]},
                }
            ),
            "stderr": "",
        }

    result = sync_explore_visual_to_lark(
        LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE"),
        projection={
            "goal_id": "goal_visual_fixture",
            "nodes": [],
            "edges": [],
            "findings": [],
        },
        visual_sink={
            "whiteboard_token": "wb_visual_fixture",
            "view_role": "canonical",
            "renderer": "mermaid",
        },
        config_path=tmp_path / "lark-explore.json",
        semantic_digest="synthetic-visual-digest",
        display_projection={
            "view_role": "canonical",
            "mermaid": (
                'flowchart TD\n    node_fixture["Credential '
                f"{credential} at {local_path} via {private_link}"
                '"]\n'
            ),
        },
        sink_visibility="shared",
        execute=True,
        runner=runner,
    )

    assert result["ok"] is True
    assert result["sink_visibility"] == "shared"
    assert len(published_source) == 1
    assert credential not in published_source[0]
    assert local_path not in published_source[0]
    assert private_link not in published_source[0]
    assert "[credential-redacted]" in published_source[0]
    assert "[local-path-redacted]" in published_source[0]
    assert "[private-link-redacted]" in published_source[0]


def test_shared_stage_svg_redacts_display_values_before_encoding(tmp_path: Path) -> None:
    credential = "gh" "p_" + "A" * 36
    private_link = "https://" + "example.feishu.cn/base/private"
    published_sources: list[str] = []
    published_marker = ""

    def runner(args: list[str], cwd: Path | None, _timeout: float | None) -> dict[str, object]:
        nonlocal published_marker
        if "+update" in args:
            source_arg = args[args.index("--source") + 1]
            assert cwd is not None
            source = (cwd / source_arg.removeprefix("@")).read_text(encoding="utf-8")
            published_sources.append(source)
            marker = re.search(r"LoopX delivery [0-9a-f]{20}", source)
            assert marker is not None
            published_marker = marker.group(0)
            return {"returncode": 0, "stdout": json.dumps({"ok": True}), "stderr": ""}
        assert "+query" in args
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "ok": True,
                    "data": {"nodes": [{"text": {"text": published_marker}}]},
                }
            ),
            "stderr": "",
        }

    projection = {
        "goal_id": "goal_public_fixture",
        "nodes": [
            {
                "node_id": "stage_node_one",
                "title": "Confirm the failing stage",
                "node_kind": "decision",
                "status": "exploring",
                "summary": (
                    "Measured 12ms improvement from the shared board. "
                    f"See {private_link}"
                ),
                "parent_id": "",
                "tags": ["lane-fix_pr"],
            },
            {
                "node_id": "stage_node_two",
                "title": "Apply the fix",
                "node_kind": "decision",
                "status": "resolved",
                "summary": f"Patch verified with {credential}.",
                "parent_id": "stage_node_one",
                "tags": ["lane-fix_pr"],
            },
        ],
        "edges": [],
        "findings": [],
    }

    result = sync_explore_visuals_to_lark(
        LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE"),
        projection=projection,
        visual_sinks={
            "canonical": {
                "board_style": "semantic_lane_columns",
                "stage_capacity": 14,
                "stage_whiteboards": [
                    {"stage_index": 1, "whiteboard_token": "wb_public_stage_fixture"}
                ],
            }
        },
        config_path=tmp_path / "lark-explore.json",
        sink_visibility=SINK_VISIBILITY_SHARED,
        execute=True,
        runner=runner,
    )

    assert result["ok"] is True
    assert len(published_sources) == 1
    source = published_sources[0]
    parsed = ET.fromstring(source)
    assert parsed.tag.endswith("svg")
    assert credential not in source
    assert private_link not in source
    assert "[credential-redacted]" in source
    assert "[private-link-redacted]" in source


def test_record_upsert_command_redacts_nested_credentials() -> None:
    credential = "gh" "p_" + "A" * 36

    command = build_record_upsert_command(
        LarkKanbanConfig(
            base_token="base_public_fixture",
            table_id="tbl_public_fixture",
            cli_bin="lark-cli",
            identity="user",
        ),
        record_id=None,
        values={"Evidence": [{"text": f"token {credential}"}]},
    )

    assert credential not in " ".join(command)
    assert "[credential-redacted]" in " ".join(command)


def test_seed_writers_default_to_shared_and_redact_nested_values() -> None:
    credential = "gh" "p_" + "A" * 36
    local_path = r"C:\Users\operator\private"
    config = LarkKanbanConfig(
        base_token="base_public_fixture",
        table_id="tbl_public_fixture",
        cli_bin="lark-cli",
        identity="user",
    )
    task = {
        "Task": f"Rotate {credential}",
        "Workdir": local_path,
        "Worker Command": f"echo {credential}",
    }

    for sink in (seed_lark_kanban_task, seed_lark_kanban_records):
        assert (
            signature(sink).parameters["sink_visibility"].default
            == SINK_VISIBILITY_SHARED
        )

    task_packet = seed_lark_kanban_task(config, task=task)
    records_packet = seed_lark_kanban_records(config, records=[task])
    for packet in (task_packet, records_packet):
        serialized = json.dumps(packet)
        assert credential not in serialized
        assert local_path not in serialized
        assert "[credential-redacted]" in serialized
        assert "[local-path-redacted]" in serialized

    owner_packet = seed_lark_kanban_task(
        config,
        task=task,
        sink_visibility=SINK_VISIBILITY_OWNER_ONLY,
    )
    assert owner_packet["task"]["Workdir"] == local_path
    assert credential not in json.dumps(owner_packet)


def test_operator_and_automatic_sink_paths_default_to_shared() -> None:
    for sink in (
        sync_loopx_todos_to_lark_kanban,
        lark_kanban_heartbeat,
        sync_issue_fix_explore_on_material_change,
    ):
        assert (
            signature(sink).parameters["sink_visibility"].default
            == SINK_VISIBILITY_SHARED
        )


def test_explicit_visual_sink_paths_stay_owner_only_by_default() -> None:
    for sink in (sync_explore_visual_to_lark, sync_explore_visuals_to_lark):
        assert (
            signature(sink).parameters["sink_visibility"].default
            == SINK_VISIBILITY_OWNER_ONLY
        )
