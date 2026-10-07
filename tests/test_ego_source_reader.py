"""Source adapter boundaries; actual browser/host acceptance is separate."""
import hashlib
import json
import subprocess

import pytest

from loopx.extensions import ego_source_reader as reader

URL = "https://example.com/article?q=1"


@pytest.fixture(autouse=True)
def isolated_reader_space(monkeypatch):
    monkeypatch.setattr(reader, "_OWNED_SPACE", reader._OwnedSpace())


@pytest.fixture
def configured(monkeypatch, tmp_path):
    executable = tmp_path / "ego-browser"
    executable.write_text("installed executable fixture")
    monkeypatch.setenv("LOOPX_EGO_READ_BIN", str(executable))
    monkeypatch.setenv("LOOPX_EGO_READ_TASK_SPACE", "7")
    monkeypatch.setenv("LOOPX_EGO_READ_PAGE", "p2")
    monkeypatch.setenv("LOOPX_EGO_READ_ORIGINS", "https://example.com")
    return executable


def extraction(**changes):
    return {"url": URL, "title": "Article", "text": "Source evidence 原文",
            "truncated": False, "image_count": 2, **changes}


def response(value, *, stderr=False):
    if isinstance(value, dict) and "url" in value:
        value = {"requested_url": URL, "canonical_url": URL, **value}
    output = reader.MARKER + json.dumps(value)
    return subprocess.CompletedProcess([], 0, "" if stderr else output,
                                       output if stderr else "")


@pytest.mark.parametrize("stderr", [False, True])
def test_read_returns_evidence_without_visual_or_write_claim(configured, monkeypatch, stderr):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return response(extraction(), stderr=stderr)

    monkeypatch.setattr(reader.subprocess, "run", run)
    result = reader.read_public_url(URL)
    assert result["ok"] and result["text"] == "Source evidence 原文"
    assert result["sha256"] == hashlib.sha256(result["text"].encode()).hexdigest()
    assert result["images_read"] is False and result["image_count"] == 2
    args, kwargs = calls[0]
    assert args[:3] == [str(configured), "nodejs", "-e"]
    assert kwargs["stdin"] == subprocess.DEVNULL and 0 < kwargs["timeout"] <= 30
    assert "shell" not in kwargs
    assert "does not prove article" in result["limitations"]


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_real_child_preserves_utf8_evidence_on_a_gbk_host(
    configured, monkeypatch, stream,
):
    import sys
    # The fixture child emits real UTF-8 bytes; it does not return a predecoded
    # mock. Python's default text codec models the Windows host locale.
    value = {"requested_url": URL, "canonical_url": URL, **extraction()}
    payload = (reader.MARKER + json.dumps(value, ensure_ascii=False)).encode("utf-8")
    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "gbk")

    def emit(_args, **kwargs):
        return real_run(
            [sys.executable, "-c", f"import sys; sys.{stream}.buffer.write({payload!r})"],
            **kwargs,
        )

    monkeypatch.setattr(reader.subprocess, "run", emit)
    result = reader.read_public_url(URL)
    assert result["ok"] is True
    assert result["text"] == "Source evidence 原文"
    assert result["sha256"] == hashlib.sha256("Source evidence 原文".encode("utf-8")).hexdigest()


@pytest.mark.parametrize("url,error", [
    ("http://example.com/article", "source_url_invalid"),
    ("https://user:secret@example.com/article", "source_url_invalid"),
    ("https://@example.com/article", "source_url_invalid"),
    ("https://example.com:8443/article", "source_url_invalid"),
    ("https://example.com.evil.test/article", "source_origin_not_authorized"),
    ("https://other.test/article", "source_origin_not_authorized"),
    ("https://example.com\n.evil.test/article", "source_url_invalid"),
    ("https://example.com\\@evil.test/article", "source_url_invalid"),
    ("https://[fe80::1%25en0]/article", "source_url_invalid"),
    ("javascript:alert(1)", "source_url_invalid"),
])
def test_rejects_url_before_browser_access(configured, monkeypatch, url, error):
    monkeypatch.setattr(reader.subprocess, "run", lambda *a, **k: pytest.fail("browser accessed"))
    assert reader.read_public_url(url) == {"ok": False, "error": error}


def test_ipv6_source_and_origin_use_browser_canonical_host(configured, monkeypatch, tmp_path):
    url = "https://[2606:4700:4700:0000:0000:0000:0000:1111]/article"
    origin = "https://[2606:4700:4700::1111]"
    monkeypatch.setenv("LOOPX_EGO_READ_ORIGINS", "https://[2606:4700:4700:0000:0000:0000:0000:1111]")

    config = reader.ReaderConfig.from_environment()
    canonical, actual_origin = reader._url(url)

    assert canonical == origin + "/article"
    assert actual_origin == origin
    assert config.origins == frozenset({origin})

    result, observation = run_generated_script(
        config, canonical, False, tmp_path / "unused.png",
    )
    assert result["ok"] is True
    assert result["url"] == canonical
    assert observation["domReads"] > 0


@pytest.mark.parametrize("origin_host", ["::ffff:8.8.8.8", "::ffff:808:808"])
@pytest.mark.parametrize("request_host", ["::ffff:8.8.8.8", "::ffff:808:808"])
def test_ipv4_mapped_ipv6_round_trips_through_browser_url(
    configured, monkeypatch, tmp_path, origin_host, request_host,
):
    monkeypatch.setenv("LOOPX_EGO_READ_ORIGINS", f"https://[{origin_host}]")
    config = reader.ReaderConfig.from_environment()
    url = f"https://[{request_host}]/article"

    canonical, origin = reader._url(url)
    result, observation = run_generated_script(
        config, url, False, tmp_path / "unused.png",
    )

    expected = "https://[::ffff:808:808]/article"
    assert result["ok"] is True
    assert result["url"] == expected
    assert observation["domReads"] > 0
    assert canonical == expected
    assert origin == "https://[::ffff:808:808]"
    assert config.origins == frozenset({"https://[::ffff:808:808]"})


@pytest.mark.parametrize("key,value", [
    ("LOOPX_EGO_READ_PAGE", "p1);process.exit()"),
    ("LOOPX_EGO_READ_TASK_SPACE", "0"),
    ("LOOPX_EGO_READ_ORIGINS", "https://example.com/private"),
    ("LOOPX_EGO_READ_ORIGINS", "https://example.com/#fragment"),
    ("LOOPX_EGO_READ_BIN", "relative-command"),
])
def test_bad_operator_configuration_fails_closed(configured, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    monkeypatch.setattr(reader.subprocess, "run", lambda *a, **k: pytest.fail("browser accessed"))
    assert reader.read_public_url(URL)["error"] == "source_reader_not_configured"


def auto_config(monkeypatch):
    monkeypatch.setenv("LOOPX_EGO_READ_TASK_SPACE", "auto")
    monkeypatch.setenv("LOOPX_EGO_READ_PAGE", "p1")


def space_response(space, *, stderr=False):
    output = reader.SPACE_MARKER + json.dumps({"id": space})
    return subprocess.CompletedProcess([], 0, "" if stderr else output, output if stderr else "")


@pytest.mark.parametrize("stderr", [False, True])
def test_auto_space_is_lazy_and_reused_for_text_and_image(configured, monkeypatch, tmp_path, stderr):
    auto_config(monkeypatch)
    calls = []
    def run(_executable, script, **_kwargs):
        calls.append(script)
        if 'taskSpace("LoopX public-source reader ' in script:
            return space_response(19, stderr=stderr)
        if "screenshot(" in script:
            path = tmp_path / "image.png"
            path.write_bytes(png())
            return response({"url": URL, "index": 0, "alt": "figure"})
        return response(extraction())
    monkeypatch.setattr(reader, "_run", run)
    assert reader.read_public_url("https://other.test/private")["error"] == "source_origin_not_authorized"
    assert not calls
    assert reader.read_public_url(URL)["ok"]
    assert reader.read_public_url(URL)["ok"]
    assert reader._read(URL, 0, str(tmp_path / "image.png"))["ok"]
    assert len(calls) == 4
    assert all("taskSpace(19)" in s for s in calls[1:])


def test_separate_reader_process_state_owns_separate_spaces(configured, monkeypatch):
    auto_config(monkeypatch)
    config = reader.ReaderConfig.from_environment()
    # Model the real factory's name-based reuse, not predetermined distinct ids.
    spaces = {}
    def run(_executable, script, **_kwargs):
        name = json.loads(script.split("taskSpace(", 1)[1].split(");", 1)[0])
        return space_response(spaces.setdefault(name, 19 + len(spaces)))
    monkeypatch.setattr(reader, "_run", run)
    first, second = reader._OwnedSpace(), reader._OwnedSpace()
    assert first.resolve(config).task_space == 19
    assert second.resolve(config).task_space == 20
    assert first.resolve(config).task_space == 19
    assert first.name != second.name and len(spaces) == 2
    first.forget_closed()
    first.resolve(config)
    assert len(spaces) == 2  # The owner identity remains stable during recovery.


@pytest.mark.parametrize("failure", ["user_control", "timeout", "ambiguous_creation"])
def test_errors_do_not_create_replacement_spaces(configured, monkeypatch, failure):
    auto_config(monkeypatch)
    calls = []
    def run(executable, script, **_kwargs):
        calls.append(script)
        if 'taskSpace("LoopX public-source reader ' in script:
            if failure == "ambiguous_creation":
                raise subprocess.TimeoutExpired(executable, 30)
            return space_response(19)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(executable, 30)
        return subprocess.CompletedProcess([], 1, "", "user took control")
    monkeypatch.setattr(reader, "_run", run)
    assert not reader.read_public_url(URL)["ok"]
    assert not reader.read_public_url(URL)["ok"]
    assert sum('taskSpace("LoopX public-source reader ' in s for s in calls) == 1


@pytest.mark.parametrize("stderr", [False, True])
def test_only_confirmed_missing_owned_space_is_replaced_once(configured, monkeypatch, stderr):
    auto_config(monkeypatch)
    calls = []
    def run(_executable, script, **_kwargs):
        calls.append(script)
        if 'taskSpace("LoopX public-source reader ' in script:
            return space_response(19 if len(calls) == 1 else 20)
        if "taskSpace(19)" in script:
            output = reader.SPACE_MARKER + '{"closed":true}'
            return subprocess.CompletedProcess([], 1, "" if stderr else output, output if stderr else "")
        return response(extraction())
    monkeypatch.setattr(reader, "_run", run)
    assert reader.read_public_url(URL)["ok"]
    assert reader._OWNED_SPACE.space == 20 and len(calls) == 4


@pytest.mark.parametrize("auto", [True, False])
def test_shutdown_only_finishes_its_created_space_once(configured, monkeypatch, auto):
    if auto:
        auto_config(monkeypatch)
    calls = []
    def run(_executable, script, **_kwargs):
        calls.append(script)
        return space_response(19)
    monkeypatch.setattr(reader, "_run", run)
    reader._OWNED_SPACE.resolve(reader.ReaderConfig.from_environment())
    reader._OWNED_SPACE.close()
    reader._OWNED_SPACE.close()
    if auto:
        assert len(calls) == 2
        assert "taskSpace(19)" in calls[-1] and "ownership==='agent'" in calls[-1]
        assert "finish({keep:[]})" in calls[-1]
    else:
        assert not calls


def test_creation_and_read_share_one_timeout_budget(configured, monkeypatch):
    auto_config(monkeypatch)
    clock = iter([0, 5, 29])
    monkeypatch.setattr(reader.time, "monotonic", lambda: next(clock))
    timeouts = []
    def run(_args, **kwargs):
        timeouts.append(kwargs["timeout"])
        return space_response(19) if len(timeouts) == 1 else response(extraction())
    monkeypatch.setattr(reader.subprocess, "run", run)
    assert reader.read_public_url(URL)["ok"]
    assert timeouts == [25, 1]


def test_stdio_hosts_create_isolated_spaces_and_clean_up_on_eof(configured, monkeypatch, tmp_path):
    import asyncio
    import os
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    auto_config(monkeypatch)
    log = tmp_path / "calls.jsonl"
    configured.write_text("#!" + sys.executable + "\n" +
        "import json,os,sys\nfrom pathlib import Path\n" +
        "script=sys.argv[-1]\n" +
        f"with Path({str(log)!r}).open('a') as f: f.write(json.dumps([os.getppid(),script])+'\\n')\n" +
        "if 'taskSpace(\"LoopX public-source reader ' in script:\n" +
        " print('LOOPX_READER_SPACE:'+json.dumps({'id':os.getppid()}),file=sys.stderr)\n" +
        "elif 'finish({keep:[]})' not in script:\n" +
        " print('LOOPX_PUBLIC_SOURCE:'+" + repr(response(extraction()).stdout.split(":", 1)[1]) + ",file=sys.stderr)\n")
    configured.chmod(0o700)
    async def host():
        params = StdioServerParameters(command=sys.executable,
            args=["-m", "loopx.extensions.ego_source_reader"], env=dict(os.environ))
        async with stdio_client(params) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing) as session:
                await session.initialize()
                for _ in range(2):
                    result = await session.call_tool("read_public_url", {"url": URL})
                    assert json.loads(result.content[0].text)["ok"]
    async def journey():
        await asyncio.gather(host(), host())
    asyncio.run(journey())
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    hosts = {pid for pid, _ in calls}
    assert len(hosts) == 2
    for pid in hosts:
        scripts = [script for owner, script in calls if owner == pid]
        assert len(scripts) == 4
        assert sum('taskSpace("LoopX public-source reader ' in s for s in scripts) == 1
        assert all(f"taskSpace({pid})" in s for s in scripts[1:])
        assert scripts[-1].endswith("if(t.ownership==='agent')await t.finish({keep:[]});")


@pytest.mark.parametrize("value", [
    extraction(url="https://other.test/private"),
    extraction(url="https://example.com/another-article"),
    extraction(text=""), extraction(text="x" * (reader.MAX_TEXT_CHARS + 1)),
    extraction(text=None), extraction(truncated="false"),
    extraction(image_count=True), extraction(title=None), [], {},
])
def test_malformed_or_raced_result_does_not_escape(configured, monkeypatch, value):
    monkeypatch.setattr(reader.subprocess, "run", lambda *a, **k: response(value))
    assert reader.read_public_url(URL) == {"ok": False, "error": "browser_read_result_invalid"}


def test_redirect_fence_returns_no_text(configured, monkeypatch):
    monkeypatch.setattr(reader.subprocess, "run", lambda *a, **k: response({"error": "source_url_changed"}))
    assert reader.read_public_url(URL) == {"ok": False, "error": "source_url_changed"}


def test_browser_failures_are_sanitized(configured, monkeypatch):
    monkeypatch.setattr(reader.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess([], 4, "", "private bootstrap output"))
    assert reader.read_public_url(URL) == {"ok": False, "error": "browser_read_failed", "exit_code": 4}

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("private command", 30)

    monkeypatch.setattr(reader.subprocess, "run", timeout)
    assert reader.read_public_url(URL) == {"ok": False, "error": "browser_read_timeout"}


def test_truncation_and_verification_page_remain_observable(configured, monkeypatch):
    # A provider success is not semantic evidence that an article was readable.
    monkeypatch.setattr(reader.subprocess, "run", lambda *a, **k:
                        response(extraction(text="Complete verification to continue", truncated=True)))
    result = reader.read_public_url(URL)
    assert result["ok"] and result["truncated"]
    assert "verification wall" in result["limitations"]


def test_concurrent_call_does_not_navigate_reserved_page(configured, monkeypatch):
    monkeypatch.setattr(reader.subprocess, "run", lambda *a, **k: pytest.fail("browser accessed"))
    with reader._READ_LOCK:
        assert reader.read_public_url(URL)["error"] == "source_reader_busy"


def test_missing_config_does_not_invoke_browser(configured, monkeypatch):
    monkeypatch.delenv("LOOPX_EGO_READ_BIN")
    monkeypatch.setattr(reader.subprocess, "run", lambda *a, **k: pytest.fail("browser accessed"))
    assert reader.read_public_url(URL)["error"] == "source_reader_not_configured"


def test_duplicate_or_non_json_result_is_rejected():
    assert not reader._result(reader.MARKER + "not json", "", URL)["ok"]
    output = reader.MARKER + json.dumps(extraction())
    assert not reader._result(output, output, URL)["ok"]


def test_url_is_json_data_and_normalized_before_navigation(configured):
    config = reader.ReaderConfig.from_environment()
    url = 'https://example.com/?q=";process.exit();//'
    script = reader._script(config, url)
    assert "requestedUrl=" + json.dumps(url) in script
    assert "await p.goto(target.href)" in script
    assert script.index("new URL(requestedUrl)") < script.index("await p.goto(")


def png(width=2, height=2):
    import struct
    import zlib
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x30\x80\xA0" * width) * height)) + chunk(b"IEND", b""))


def test_image_pixels_and_provenance_are_content_not_json_metadata(configured, monkeypatch):
    from pathlib import Path
    from mcp.types import ImageContent, TextContent
    def run(args, **kwargs):
        import re
        path = json.loads(re.search(r"path:(\"[^\"]+\")", args[-1])[1])
        Path(path).write_bytes(png())
        return response({"url": URL, "index": 8, "alt": "figure"})
    monkeypatch.setattr(reader.subprocess, "run", run)
    content = reader.read_public_image(URL, 8)
    assert isinstance(content[0], TextContent) and isinstance(content[1], ImageContent)
    metadata = json.loads(content[0].text)
    assert metadata["ok"] and metadata["index"] == 8 and metadata["width"] == 2
    assert "image_data" not in metadata and "not atomically" in metadata["limitations"]
    import base64
    assert base64.b64decode(content[1].data) == png()


@pytest.mark.parametrize("index", [True, -1, 128, 1.5, "0"])
def test_image_invalid_index_never_touches_browser(configured, monkeypatch, index):
    monkeypatch.setattr(reader.subprocess, "run", lambda *a, **k: pytest.fail("browser accessed"))
    assert json.loads(reader.read_public_image(URL, index)[0].text)["error"] == "source_image_index_invalid"


@pytest.mark.parametrize("url,error", [("https://other.test/", "source_origin_not_authorized"),
                                       ("http://example.com/", "source_url_invalid")])
def test_image_uses_existing_source_authority(configured, monkeypatch, url, error):
    monkeypatch.setattr(reader.subprocess, "run", lambda *a, **k: pytest.fail("browser accessed"))
    assert json.loads(reader.read_public_image(url, 0)[0].text)["error"] == error


@pytest.mark.parametrize("value", [
    {"url": "https://other.test/private", "index": 8, "alt": "private"},
    {"url": URL, "index": 9, "alt": "changed"},
    {"url": URL, "index": True, "alt": "changed"},
    {"url": URL, "index": 8, "alt": "x" * 513}, [],
])
def test_image_malformed_or_raced_provenance_returns_no_pixels(tmp_path, value):
    path = tmp_path / "image.png"
    path.write_bytes(png())
    result = reader._image_result(response(value).stdout, "", URL, 8, str(path))
    assert result == {"ok": False, "error": "browser_image_result_invalid"}


@pytest.mark.parametrize("data", [b"not PNG", png(1, 2), png(4097, 2), b"x" * 4_000_001])
def test_image_binary_and_size_limits_preserve_failure(tmp_path, data):
    path = tmp_path / "image.png"
    path.write_bytes(data)
    r = reader._image_result(response({"url": URL, "index": 8, "alt": ""}).stdout, "", URL, 8, str(path))
    assert r == {"ok": False, "error": "browser_image_result_invalid"}


@pytest.mark.parametrize("error", ["source_url_changed", "source_image_unavailable", "source_image_bounds_unsupported"])
def test_image_browser_boundary_rejects_before_file_access(tmp_path, error):
    assert reader._image_result(response({"error": error}).stdout, "", URL, 8, str(tmp_path / "absent")) == {"ok": False, "error": error}


def test_image_and_text_share_the_reserved_page_lock(configured, monkeypatch):
    monkeypatch.setattr(reader.subprocess, "run", lambda *a, **k: pytest.fail("browser accessed"))
    with reader._READ_LOCK:
        assert json.loads(reader.read_public_image(URL, 8)[0].text)["error"] == "source_reader_busy"


@pytest.mark.parametrize("inventory", [[{"index": 0, "alt": "", "natural_width": True, "natural_height": 2}],
                                      [{"index": 1, "alt": "", "natural_width": 2, "natural_height": 2}],
                                      "images"])
def test_image_inventory_is_validated(inventory):
    assert not reader._result(response(extraction(images=inventory)).stdout, "", URL)["ok"]


def test_image_mcp_stdio_returns_native_image_content(configured, monkeypatch):
    import asyncio
    import base64
    import os
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import ImageContent
    configured.write_text("#!" + sys.executable + "\n" +
        "import sys,re,json,base64\nfrom pathlib import Path\n" +
        "path=json.loads(re.search(r'path:(\"[^\"]+\")',sys.argv[-1])[1])\n" +
        "Path(path).write_bytes(base64.b64decode(" + repr(base64.b64encode(png()).decode()) + "))\n" +
        "print('LOOPX_PUBLIC_SOURCE:'+" + repr(json.dumps({"url": URL, "requested_url": URL,
        "canonical_url": URL, "index": 8, "alt": "figure"})) + ")\n")
    configured.chmod(0o700)
    async def journey():
        async with stdio_client(StdioServerParameters(command=sys.executable,
            args=["-m", "loopx.extensions.ego_source_reader"], env=dict(os.environ))) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing) as session:
                await session.initialize()
                tools = await session.list_tools()
                image_tool = next(t for t in tools.tools if t.name == "read_public_image")
                assert image_tool.annotations.readOnlyHint is True
                assert set(image_tool.inputSchema["properties"]) == {"url", "index"}
                result = await session.call_tool("read_public_image", {"url": URL, "index": 8})
                assert not result.isError
                assert any(isinstance(c, ImageContent) and base64.b64decode(c.data) == png() for c in result.content)
    asyncio.run(journey())


def test_text_read_exposes_image_indices_without_claiming_visual_read(configured, monkeypatch):
    item = {"index": 0, "alt": "diagram", "natural_width": 1080, "natural_height": 550}
    monkeypatch.setattr(reader.subprocess, "run", lambda *a, **k: response(extraction(images=[item])))
    result = reader.read_public_url(URL)
    assert result["images"] == [item] and result["image_inventory_truncated"] is True
    assert result["images_read"] is False


def run_generated_script(config, url, image, path, *, redirect=None, readiness=None,
                         text="Source evidence"):
    """Execute the production script in Node, without the user's browser/Page."""
    import shutil
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node required for the WHATWG URL boundary check")
    script = (reader._image_script(config, url, 0, str(path)) if image
              else reader._script(config, url))
    harness = """
const vm=require('node:vm'),fs=require('node:fs');
let href='',domReads=0,captures=0,loaded=!readiness||readiness.startsWith('sidebar_busy')||readiness==='empty_sibling_article';
const image={alt:'figure',currentSrc:'https://example.com/image.png',
 get complete(){return !readiness?.startsWith('image_')||loaded;},
 naturalWidth:2,naturalHeight:2,scrollIntoView(){},
 closest(){return null;},
 getClientRects(){return [1];},
 getBoundingClientRect(){return {left:0,top:0,width:2,height:2};}};
const spinner={getClientRects(){return [1];}};
const root={get innerText(){return readiness?.startsWith('image_')?'':loaded?text:'Navigation Loading';},
 getClientRects(){return [1];},
 getAttribute(){return (readiness==='busy'&&!loaded)||readiness==='main_busy_with_readable_article'?'true':null;},
 closest(selector){return selector==='[aria-busy="true"]'&&this.getAttribute('aria-busy')==='true'?this:null;},
 querySelectorAll(selector){if(selector==='article')return articles;
 if(selector==='img')return readiness?.startsWith('navigation_')&&!loaded?[]:[image];
 return readiness?.startsWith('navigation_')?[]:readiness==='sidebar_busy_inside_main'?[sidebar]:loaded?[]:[spinner];}};
spinner.closest=()=>null;
const article={innerText:text,getAttribute(){return null;},closest(){return null;},getClientRects(){return [1];},
 querySelectorAll(selector){return selector==='img'?[image]:[];}};
const emptyArticle={innerText:'',getAttribute(){return null;},closest(){return null;},querySelectorAll(){return [];}};
const sidebar={innerText:'Loading sidebar',getAttribute(){return 'true';},
 closest(selector){return selector==='[aria-busy="true"]'?this:{};},
 querySelectorAll(){return [spinner];},getClientRects(){return [1];}};
const sidebarFixture=readiness?.startsWith('sidebar_busy');
let articles=readiness==='article_with_sidebar'?[article]:[];
if(sidebarFixture)articles=readiness==='sidebar_busy_inside_main'?[sidebar]:[];
if(readiness==='sidebar_busy_with_primary_article')articles=[sidebar,article];
if(readiness==='main_busy_with_readable_article'){
 articles=[article];article.closest=selector=>selector==='[aria-busy="true"]'?root:null;
}
if(readiness==='article_busy_with_main_chrome'){
 articles=[sidebar];sidebar.closest=selector=>selector==='[aria-busy="true"]'?sidebar:null;
}
const doc=new Proxy({title:'Article',body:root,
 createRange(){return {selectNodeContents(){},getClientRects(){return [1];}};},
 createTreeWalker(node){
  const chromeOnly=readiness?.startsWith('navigation_')&&!loaded;
  const parent=chromeOnly?{closest(){return {};},getClientRects(){return [1];}}:node;
  let visited=false;return {nextNode(){if(visited)return null;visited=true;
   return {textContent:node.innerText,parentElement:parent};}};},
 querySelectorAll(selector){return selector==='main,[role="main"]'?[root]:[...articles,...(sidebarFixture?[sidebar]:[])];},
 querySelector(selector){if(readiness==='article_with_sidebar')return selector==='article'?article:root;
 if(readiness==='empty_sibling_article')return selector==='article'?emptyArticle:root;
 if(sidebarFixture||readiness==='article_busy_with_main_chrome')return selector==='article'?sidebar:root;
 if(readiness==='main_busy_with_readable_article')return selector==='article'?article:root;
 return readiness&&selector!=='article'?root:null;},images:[image]},
 {get(target,key){domReads++;return target[key];}});
const page={async goto(url){href=new URL(redirect||url).href;},
 async evaluate(fn,arg){return vm.runInNewContext('('+fn.toString()+')(arg)',
 {URL,location:{href},document:doc,arg,scrollX:0,scrollY:0,NodeFilter:{SHOW_TEXT:4},
 getComputedStyle(){return {visibility:'visible'};}});},
 async waitForFunction(fn,arg){
  if(typeof arg==='string'&&readiness){
   const initial=await this.evaluate(fn,arg);
   if(readiness==='article_with_sidebar'||sidebarFixture||readiness==='empty_sibling_article'){if(!initial)throw Error('readable content delayed by sidebar');loaded=true;}
   else{
    if(initial)throw Error('loading content accepted');
    if(readiness==='timeout'||readiness==='image_timeout'||readiness==='navigation_timeout'||readiness==='main_busy_with_readable_article'||readiness==='article_busy_with_main_chrome')throw Error('page.waitForFunction timed out after 10000ms; private diagnostic');
    if(readiness==='user_control')throw Error('User took control');
    loaded=true;
   }
  }
  if(!await this.evaluate(fn,arg))throw Error('not loaded');},
 async screenshot(options){captures++;fs.writeFileSync(options.path,Buffer.from(pixels,'base64'));}};
async function taskSpace(){return {page(){return page;}};}
(async()=>{await eval('(async()=>{'+source+'})()');
 console.log('SCRIPT_OBSERVATION:'+JSON.stringify({domReads,captures}));})()
 .catch(error=>{console.error(error);process.exitCode=1;});
"""
    import base64
    inputs = ("const source=" + json.dumps(script) + ";const redirect=" + json.dumps(redirect)
              + ";const readiness=" + json.dumps(readiness) + ";const text=" + json.dumps(text)
              + ";const pixels=" + json.dumps(base64.b64encode(png()).decode()) + ";")
    result = subprocess.run([node, "-e", inputs + harness], capture_output=True,
                            text=True, timeout=10, check=True)
    observed = next(line.split(":", 1)[1] for line in result.stdout.splitlines()
                    if line.startswith("SCRIPT_OBSERVATION:"))
    decoded = (reader._image_result(result.stdout, result.stderr, url, 0, str(path)) if image
               else reader._result(result.stdout, result.stderr, url))
    return decoded, json.loads(observed)


@pytest.mark.parametrize("image", [False, True])
@pytest.mark.parametrize("readiness", ["loading", "busy", "article_with_sidebar"])
def test_rendered_content_waits_without_accepting_navigation_shell(
    configured, tmp_path, image, readiness,
):
    result, observation = run_generated_script(
        reader.ReaderConfig.from_environment(), URL, image, tmp_path / "image.png",
        readiness=readiness,
    )
    assert result["ok"]
    if not image:
        assert result["text"] == "Source evidence"
    assert observation["captures"] == int(image)


@pytest.mark.parametrize("image", [False, True])
def test_readiness_timeout_returns_no_shell_or_image(configured, tmp_path, image):
    result, observation = run_generated_script(
        reader.ReaderConfig.from_environment(), URL, image, tmp_path / "image.png",
        readiness="timeout",
    )
    assert result == {"ok": False, "error": "source_content_not_ready"}
    assert observation["captures"] == 0


@pytest.mark.parametrize("image", [False, True])
def test_readiness_does_not_swallow_user_control(configured, tmp_path, image):
    with pytest.raises(subprocess.CalledProcessError):
        run_generated_script(
            reader.ReaderConfig.from_environment(), URL, image, tmp_path / "image.png",
            readiness="user_control",
        )


def test_short_readable_content_has_no_length_threshold(configured, tmp_path):
    result, _ = run_generated_script(
        reader.ReaderConfig.from_environment(), URL, False, tmp_path / "image.png", text="Hi",
    )
    assert result["ok"] and result["text"] == "Hi"


@pytest.mark.parametrize("readiness,ok", [(None, True), ("image_loading", True), ("image_timeout", False)])
def test_image_only_content_is_readable_after_pixels_load(configured, tmp_path, readiness, ok):
    result, observation = run_generated_script(
        reader.ReaderConfig.from_environment(), URL, True, tmp_path / "image.png",
        readiness=readiness, text="",
    )
    assert result["ok"] is ok
    assert observation["captures"] == int(ok)
    if not ok:
        assert result["error"] == "source_content_not_ready"


@pytest.mark.parametrize("image", [False, True])
@pytest.mark.parametrize("readiness", [
    "sidebar_busy_before_main", "sidebar_busy_inside_main", "sidebar_busy_with_primary_article",
    "empty_sibling_article",
])
def test_busy_sidebar_article_does_not_block_primary_content(configured, tmp_path, image, readiness):
    result, observation = run_generated_script(
        reader.ReaderConfig.from_environment(), URL, image, tmp_path / "image.png",
        readiness=readiness, text="" if image else "Primary content",
    )
    assert result["ok"]
    assert observation["captures"] == int(image)


@pytest.mark.parametrize("image", [False, True])
@pytest.mark.parametrize("readiness,ok", [("navigation_loading", True), ("navigation_timeout", False)])
def test_navigation_text_does_not_make_empty_content_ready(configured, tmp_path, image, readiness, ok):
    result, observation = run_generated_script(
        reader.ReaderConfig.from_environment(), URL, image, tmp_path / "image.png",
        readiness=readiness,
    )
    assert result["ok"] is ok
    assert observation["captures"] == int(image and ok)
    if ok and not image:
        assert result["text"] == "Source evidence"
    if not ok:
        assert result["error"] == "source_content_not_ready"


@pytest.mark.parametrize("image", [False, True])
@pytest.mark.parametrize("readiness", ["main_busy_with_readable_article", "article_busy_with_main_chrome"])
def test_primary_busy_state_cannot_be_bypassed_by_readable_chrome(configured, tmp_path, image, readiness):
    result, observation = run_generated_script(
        reader.ReaderConfig.from_environment(), URL, image, tmp_path / "image.png",
        readiness=readiness,
    )
    assert result == {"ok": False, "error": "source_content_not_ready"}
    assert observation["captures"] == 0


@pytest.mark.parametrize("image", [False, True])
@pytest.mark.parametrize("url,canonical", [
    ("https://example.com/a/../", "https://example.com/"),
    ('https://example.com/?q="x"', "https://example.com/?q=%22x%22"),
    ("https://example.com/a/%2e%2e/article", "https://example.com/article"),
])
def test_browser_equivalent_urls_keep_exact_resource_fence(configured, tmp_path, image, url, canonical):
    result, observation = run_generated_script(reader.ReaderConfig.from_environment(), url,
                                               image, tmp_path / "image.png")
    assert result["ok"], result
    assert result["url"] == canonical and result["requested_url"] == url
    assert observation["domReads"] > 0
    assert observation["captures"] == int(image)


@pytest.mark.parametrize("image", [False, True])
@pytest.mark.parametrize("redirect", ["https://example.com/different", "https://other.test/private"])
def test_generated_scripts_reject_real_redirect_before_dom(configured, tmp_path, image, redirect):
    result, observation = run_generated_script(reader.ReaderConfig.from_environment(), URL,
                                               image, tmp_path / "image.png", redirect=redirect)
    assert result == {"ok": False, "error": "source_url_changed"}
    assert observation == {"domReads": 0, "captures": 0}


@pytest.mark.parametrize("field,value", [("requested_url", "https://example.com/different"),
                                        ("canonical_url", "https://example.com/different"),
                                        ("requested_url", None), ("canonical_url", None)])
def test_result_url_provenance_is_required_and_bound_to_request(tmp_path, field, value):
    path = tmp_path / "image.png"
    path.write_bytes(png())
    text = response(extraction(**{field: value})).stdout
    image = response({"url": URL, "index": 8, "alt": "", field: value}).stdout
    assert not reader._result(text, "", URL)["ok"]
    assert not reader._image_result(image, "", URL, 8, str(path))["ok"]
