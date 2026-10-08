from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import quote
from urllib.request import urlopen

import pytest

from loopx.chat import VisibleResponseStreamFilter, parse_agent_response, redact_local_paths, redact_response_markdown
from loopx.chat_acp import ACPStdioAdapter
from loopx.chat_status_api import ChatStatusRequestMixin


@pytest.mark.parametrize("root", ["/custom-volume/private-state", "/custom-volume/private state", r"Q:\private state"])
def test_declared_roots_hide_the_entire_descendant_and_preserve_json(root):
    separator = "\\" if root.startswith("Q:") else "/"
    child = root + separator + "private" + separator + "gate.json"
    paths = ["/custom-project", root]
    assert redact_local_paths(f"Stopped at {child}.", protected_paths=paths) == "Stopped at [local-path]."
    assert json.loads(redact_local_paths(json.dumps({"path": child}), protected_paths=paths)) == {"path": "[local-path]"}
    assert parse_agent_response(f"Stopped at {child}.", protected_paths=paths)["message"] == "Stopped at [local-path]."


def test_project_label_requires_a_complete_path_boundary():
    root = "/custom-volume/project"
    assert redact_local_paths(f"{root}/a.txt and {root}.", protected_paths=[root]) == "[project] and [project]."
    unrelated = f"{root}-backup/a.txt relative{root}/a.txt https://example.org{root}/a.txt"
    assert redact_local_paths(unrelated, protected_paths=[root]) == unrelated


def test_percent_encoded_root_component_keeps_safe_paths_and_redacts_private_descendants():
    root = "/custom-volume/project space"
    safe_path = "/custom-volume/project%20space/notes/report.md"
    assert redact_local_paths(safe_path, protected_paths=[root]) == "[project]"
    expected_safe = "./notes/report.md"
    assert (
        parse_agent_response(safe_path, protected_paths=[root])["message"]
        == expected_safe
    )
    for split in range(len(safe_path) + 1):
        stream = VisibleResponseStreamFilter(protected_paths=[root])
        assert (
            stream.feed(safe_path[:split])
            + stream.feed(safe_path[split:])
            + stream.finish()
            == expected_safe
        )

    private_root = f"{root}/runtime private"
    private_path = "/custom-volume/project%20space/runtime%20private/gate.json"
    assert (
        parse_agent_response(private_path, protected_paths=[root, private_root])[
            "message"
        ]
        == "[local-path]"
    )
    for split in range(len(private_path) + 1):
        stream = VisibleResponseStreamFilter(protected_paths=[root, private_root])
        assert (
            stream.feed(private_path[:split])
            + stream.feed(private_path[split:])
            + stream.finish()
            == "[local-path]"
        )


def test_dot_segment_encoded_alias_of_private_root_is_fully_redacted(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    private_target = project / "runtime private"
    private_target.mkdir()
    private_alias = project / "runtime%20private"
    private_alias.symlink_to(private_target, target_is_directory=True)
    secret_file = private_target / "gate.json"
    secret_file.write_text("{}", encoding="utf-8")

    aliased_path = f"{project}/./runtime%20private/gate.json"
    assert Path(aliased_path).resolve() == secret_file.resolve()
    assert redact_local_paths(
        aliased_path,
        protected_paths=[str(project), str(private_alias)],
        project_relative=True,
    ) == "[local-path]"


def test_canonical_private_path_shapes_and_public_urls():
    assert redact_local_paths("/mnt/private/a.txt") == "[local-path]"
    public = "https://example.org/tmp/file.json ./relative/file.json docs/file.json"
    assert redact_local_paths(public) == public


@pytest.mark.parametrize("destination", [
    "/custom-volume/project/report.md#result", "/custom-volume/project/a(b).md:12",
    '</custom-volume/project/a file.md> "title"',
    "file:///custom-volume/project/report.md", "%2Fcustom-volume%2Fproject%2Freport.md",
    r"Q:\private state\report.md",
])
def test_response_local_links_keep_labels_without_broken_or_private_destinations(destination):
    text = f"- [报告 [结果]]({destination})；[公开来源](https://example.org/a(b))."
    result = parse_agent_response(text, protected_paths=["/custom-volume/project", r"Q:\private state"])["message"]
    assert result == "- 报告 [结果]；[公开来源](https://example.org/a(b))."


def test_response_link_repair_preserves_code_and_does_not_rewrite_status_json():
    link = "[label](/custom-volume/project/report.md)"
    code = f"`{link}`\n```md\n{link}\n```\n"
    assert redact_response_markdown(code, protected_paths=["/custom-volume/project"]) == code.replace(
        "/custom-volume/project/report.md", "./report.md")
    assert json.loads(redact_local_paths(json.dumps({"message": link}), protected_paths=["/custom-volume/project"])) == {
        "message": "[label]([project])"}
    raw = '<loopx-review-json>' + json.dumps({"message": link}) + '</loopx-review-json>'
    assert parse_agent_response(raw, protected_paths=["/custom-volume/project"])["message"] == "label"


@pytest.mark.parametrize("root,child", [
    ("/custom-volume/project", "/notes/report.md:12"),
    ("/custom-volume/project space", "/notes/report.md"),
    (r"Q:\project", r"\notes\report.md"),
])
def test_response_and_split_stream_retain_the_project_filename(root, child):
    text = f"Read back `{root}{child}`.\n"
    expected = "Read back `./" + child.lstrip("/\\").replace("\\", "/") + "`.\n"
    assert redact_response_markdown(text, protected_paths=[root]) == expected
    assert parse_agent_response(text, protected_paths=[root])["message"] == expected.strip()
    for split in range(len(text) + 1):
        stream = VisibleResponseStreamFilter(protected_paths=[root])
        assert stream.feed(text[:split]) + stream.feed(text[split:]) + stream.finish() == expected


def test_answer_path_presentation_does_not_relax_nested_private_roots_or_structured_fields():
    root = "/custom-volume/project"
    secret_root = root + "/runtime"
    paths = [root, secret_root]
    text = f"`{root}/notes/report.md`; `{secret_root}/private/gate.json`; `/home/other/private.txt`."
    assert parse_agent_response(text, protected_paths=paths)["message"] == (
        "`./notes/report.md`; `[local-path]`; `[local-path]`."
    )
    raw = '<loopx-review-json>' + json.dumps({
        "message": text,
        "proposals": [{"kind": "todo", "text": f"Read {root}/notes/report.md", "rationale": f"See {secret_root}/private/gate.json"}],
    }) + '</loopx-review-json>'
    parsed = parse_agent_response(raw, protected_paths=paths)
    assert parsed["message"].startswith("`./notes/report.md`")
    assert parsed["proposals"][0]["text"] == "Read [project]"
    assert parsed["proposals"][0]["rationale"] == "See [local-path]"


@pytest.mark.parametrize("private_path", [
    "/custom-volume/project/./runtime/private/gate.json",
    "/custom-volume/project//runtime/private/gate.json",
    "/custom-volume/project/runtime/./private/gate.json",
    "/custom-volume/project/%2e/runtime/private/gate.json",
    "/custom-volume/project/runtime%2fprivate/gate.json",
    "/custom-volume%2Fproject%2Fruntime%2Fprivate%2Fgate.json",
    "%2Fcustom-volume%2fproject%2fruntime%2fprivate%2fgate.json",
    r"Q:\project\.\runtime\private\gate.json",
    r"Q:\project\\runtime\private\gate.json",
    r"Q:\project\.\RUNTIME\private\gate.json",
    r"Q:%5Cproject%5CRUNTIME%5Cprivate%5Cgate.json",
])
def test_answer_and_every_stream_split_hide_equivalent_nested_private_paths(private_path):
    paths = [r"Q:\project", r"Q:\project\runtime"] if private_path.startswith("Q:") else [
        "/custom-volume/project", "/custom-volume/project/runtime",
    ]
    text = f"Read back `{private_path}`.\n"
    expected = "Read back `[local-path]`.\n"
    assert redact_response_markdown(text, protected_paths=paths) == expected
    assert parse_agent_response(text, protected_paths=paths)["message"] == expected.strip()
    for split in range(len(text) + 1):
        stream = VisibleResponseStreamFilter(protected_paths=paths)
        assert stream.feed(text[:split]) + stream.feed(text[split:]) + stream.finish() == expected


@pytest.mark.parametrize("root,private_root,separator,path_separator", [
    ("/custom-volume/project", "/custom-volume/project/runtime", "%2F", "%2F"),
    ("/custom-volume/project", "/custom-volume/project/runtime", "%2f", "/"),
    (r"Q:\project", r"Q:\project\runtime", "%5c", "%5C"),
])
def test_answer_and_every_stream_split_hide_private_paths_with_encoded_root_separator(
    root, private_root, separator, path_separator
):
    private_path = root + separator + "runtime" + path_separator + "private/gate.json"
    text = f"Read back `{private_path}`.\n"
    expected = "Read back `[local-path]`.\n"
    paths = [root, private_root]
    assert redact_local_paths(private_path, protected_paths=paths) == "[local-path]"
    assert redact_local_paths(private_path, protected_paths=paths, project_relative=True) == "[local-path]"
    assert parse_agent_response(text, protected_paths=paths)["message"] == expected.strip()
    for split in range(len(text) + 1):
        stream = VisibleResponseStreamFilter(protected_paths=paths)
        assert stream.feed(text[:split]) + stream.feed(text[split:]) + stream.finish() == expected


def test_project_answer_retains_safe_filename_after_encoded_root_separator():
    root = "/custom-volume/project"
    text = f"Read {root}%2fnotes%2Freport.md."
    assert redact_local_paths(text, protected_paths=[root], project_relative=True) == "Read ./notes/report.md."
    assert parse_agent_response(text, protected_paths=[root])["message"] == "Read ./notes/report.md."


def test_project_answer_retains_safe_filename_after_encoded_root_components():
    root = "/custom-volume/project"
    text = f"Read {root.replace('/', '%2F')}%2Fnotes%2Freport.md."
    expected = "Read ./notes/report.md."
    assert redact_local_paths(text, protected_paths=[root], project_relative=True) == expected
    assert parse_agent_response(text, protected_paths=[root])["message"] == expected
    for split in range(len(text) + 1):
        stream = VisibleResponseStreamFilter(protected_paths=[root])
        assert stream.feed(text[:split]) + stream.feed(text[split:]) + stream.finish() == expected


def _run_acp_answer(tmp_path, *, project, agent_work_dir, text):
    provider = tmp_path / "synthetic_acp.py"
    provider.write_text('''import json, sys
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {}}
    elif method == "session/new":
        result = {"sessionId": "fixture"}
    elif method == "session/prompt":
        for chunk in sys.argv[1]:
            print(json.dumps({"jsonrpc": "2.0", "method": "session/update",
                "params": {"sessionId": "fixture", "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": chunk}}}}), flush=True)
        result = {"stopReason": "end_turn"}
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
''')
    events = []
    adapter = ACPStdioAdapter.start(
        command=(sys.executable, str(provider), text), work_dir=project,
        agent_work_dir=agent_work_dir,
        startup_timeout_sec=5, idle_timeout_sec=5, hard_timeout_sec=10,
    )
    try:
        response = adapter.start_turn("Report fixture locations", lambda kind, payload: events.append((kind, payload)))
    finally:
        adapter.close_session()

    return response, events


def test_acp_stdio_final_and_stream_hide_private_aliases_and_keep_public_filename(tmp_path):
    project = tmp_path / "project"
    private = project / "runtime private"
    private.mkdir(parents=True)
    secret = private / "synthetic-secret.md"
    secret.write_text("synthetic fixture")
    private_alias = project / "runtime%20private"
    private_alias.symlink_to(private, target_is_directory=True)
    aliases = [
        str(private_alias / secret.name),
        f"{project}/./runtime%20private/{secret.name}",
        f"{project}//runtime%20private/{secret.name}",
    ]
    assert all(Path(alias).resolve() == secret.resolve() for alias in aliases)
    text = "\n".join([*aliases, str(project / "notes/report.md")]) + "\n"
    expected = "[local-path]\n" * len(aliases) + "./notes/report.md\n"
    response, events = _run_acp_answer(
        tmp_path, project=project, agent_work_dir=private_alias, text=text
    )

    assert response["message"] == expected.strip()
    assert "".join(payload["text"] for kind, payload in events if kind == "answer.delta") == expected
    assert [payload["response"]["message"] for kind, payload in events if kind == "answer.final"] == [expected.strip()]


def test_acp_stdio_stream_hides_a_long_percent_encoded_private_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    private = project.joinpath(*(["private segment"] * 12))
    private.mkdir(parents=True)
    encoded_path = quote(f"{private}/secret/gate.json", safe="")
    response, events = _run_acp_answer(
        tmp_path,
        project=project,
        agent_work_dir=private,
        text=encoded_path + "\n",
    )

    assert response["message"] == "[local-path]"
    assert "".join(
        payload["text"] for kind, payload in events if kind == "answer.delta"
    ) == "[local-path]\n"


@pytest.mark.parametrize("suffix", ["/../other/private.txt", "/notes/../../private.txt", "/%2e%2e/private.txt"])
def test_answer_paths_do_not_present_traversal_as_project_files(suffix):
    text = "/custom-volume/project" + suffix
    assert parse_agent_response(text, protected_paths=["/custom-volume/project"])["message"] == "[project]"


def test_stream_holds_a_long_project_filename_until_its_boundary():
    root = "/custom-volume/" + "r" * 190
    relative = "notes/" + "s" * 190 + "/report.md"
    path = root + "/" + relative
    stream = VisibleResponseStreamFilter(protected_paths=[root])
    assert stream.feed(path[:170]) == ""
    assert stream.feed(path[170:300]) == ""
    assert stream.feed(path[300:] + "\n") + stream.finish() == "./" + relative + "\n"


@pytest.mark.parametrize("root", [
    "/custom-volume/private-state", "/custom-volume/private state",
    "/custom-volume/" + "r" * 190,
])
def test_stream_does_not_split_a_long_custom_path_before_redacting(root):
    stream = VisibleResponseStreamFilter(protected_paths=["/custom-project", root])
    path = root + "/" + "s" * 190 + "/gate.json"
    chunks = [stream.feed(path[:170]), stream.feed(path[170:] + "\n"), stream.finish()]
    assert "".join(chunks) == "[local-path]\n"


def test_stream_holds_a_long_fully_encoded_private_root_until_redacting():
    root = "/custom-volume/" + "private segment " * 18
    path = quote(root + "/secret/gate.json", safe="")
    stream = VisibleResponseStreamFilter(
        protected_paths=["/custom-project", root]
    )
    streamed = "".join(stream.feed(character) for character in path)
    assert streamed == ""
    assert stream.feed("\n") + stream.finish() == "[local-path]\n"


def test_status_http_hides_the_selected_custom_runtime_root(monkeypatch):
    root = Path("/custom-volume/private-state")
    monkeypatch.setattr("loopx.chat_status_api.collect_status", lambda **kwargs: {
        "ok": True, "runtime_root": str(root), "diagnostic": str(root / "private/gate.json"),
    })
    monkeypatch.setattr("loopx.chat_status_api.attach_host_thread_activity", lambda *args, **kwargs: None)

    class Handler(ChatStatusRequestMixin, BaseHTTPRequestHandler):
        def do_GET(self):
            self._status()

        def _send_json(self, payload, *, status=200):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def _send_error(self, message, **kwargs):
            self._send_json({"error": message}, status=kwargs.get("status", 500))

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.selected_goal_id = None
    server.registry_path = Path("/custom-project/registry.json")
    server.scan_roots = [Path("/custom-project")]
    server.runtime_root = root
    server.runtime_root_override = root
    server.limit = 80
    server.goal_subagent_configuration_enabled = False
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/status.json", timeout=5) as response:
            payload = json.load(response)
            assert payload["ok"] is True
            assert payload["runtime_root"] == "[local-path]"
            assert payload["diagnostic"] == "[local-path]"
            assert str(root) not in json.dumps(payload)
            dashboard_api = payload["local_dashboard_api"]
            assert dashboard_api["source"] == "chat"
            assert dashboard_api["status_url"] == "/status.json"
            assert dashboard_api["presentation_surfaces_url"] == "/extension-presentation-surfaces"
            assert dashboard_api["presentation_detail_url"] == "/extension-projection"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
