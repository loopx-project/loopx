"""X discovery semantics and inherited reader boundaries, without live accounts."""
import json
import os
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

from loopx.extensions import ego_source_reader as reader
from loopx.extensions import ego_x_search as search


_BROWSER_HARNESS = r"""
const input=INPUT;
let href='',pageAccess=0,domReads=0,waits=0;
const articles=input.posts.map(post=>({
 getClientRects(){return post.hidden?[]:[1];},
 querySelector(selector){
  domReads++;
  if(selector==='time')return post.url?{
   closest(){return {href:post.url};},getAttribute(){return '2026-01-02T03:04:05Z';}
  }:null;
  if(selector==='[data-testid="tweetText"]')return {innerText:post.text||''};
  return post.media?{}:null;
 }
}));
global.document={title:'Search / X',querySelectorAll(selector){
 domReads++;
 const empty=input.empty?[{getClientRects(){return [1];}}]:[];
 if(selector==='[data-testid="emptyState"]')return empty;
 return selector.includes('emptyState')?[...articles,...empty]:articles;
}};
global.location={get href(){return href;}};
const p={async goto(url){href=input.redirect||url;},
 async waitForFunction(fn,arg){
  // The shared navigation has its own extensively tested content predicate.
  if(++waits>1&&!fn(arg))throw Error('page.waitForFunction timed out');
 },async evaluate(fn,arg){return fn(arg);}};
async function listTaskSpaces(){return [{id:7,ownership:input.ownership}];}
async function taskSpace(){pageAccess++;return {page(){return p;}};}
(async()=>{await eval('(async()=>{'+input.script+'})()');})()
 .catch(e=>{console.error(e.message);process.exitCode=1;})
 .finally(()=>console.log('OBS:'+JSON.stringify({pageAccess,domReads})));
"""


@pytest.fixture
def configured(monkeypatch, tmp_path):
    executable = tmp_path / "ego-browser"
    executable.write_text("installed fixture")
    monkeypatch.setenv("LOOPX_EGO_READ_BIN", str(executable))
    monkeypatch.setenv("LOOPX_EGO_READ_TASK_SPACE", "7")
    monkeypatch.setenv("LOOPX_EGO_READ_PAGE", "p2")
    monkeypatch.setenv("LOOPX_EGO_READ_ORIGINS", "https://x.com")


def browser(monkeypatch, posts, *, empty=False, ownership="agent", redirect=None):
    """Run the actual extraction in Node against independent source fixtures.

    Shared URL/readiness mechanics are covered by test_ego_source_reader; this
    harness executes the X-specific readiness predicate and DOM extraction.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for generated browser extraction")
    observations = []
    real_run = subprocess.run

    def run(_executable, script, **_kwargs):
        inputs = json.dumps({"posts": posts, "empty": empty, "ownership": ownership,
                            "redirect": redirect, "script": script})
        harness = _BROWSER_HARNESS.replace("INPUT", inputs, 1)
        result = real_run([node, "-e", harness], capture_output=True, text=True, timeout=10)
        observations.append(json.loads(next(line[4:] for line in result.stdout.splitlines()
                                            if line.startswith("OBS:"))))
        return result

    monkeypatch.setattr(reader, "_run", run)
    return observations


@pytest.mark.parametrize("query,max_posts,latest", [
    ("", 10, False), ("  ", 10, False), ("q\nprivate", 10, False),
    ("x" * 513, 10, False), (None, 10, False), ("topic", 0, False),
    ("topic", 21, False), ("topic", True, False), ("topic", 1, "latest"),
])
def test_invalid_input_never_touches_browser(monkeypatch, query, max_posts, latest):
    monkeypatch.setattr(reader, "_run", lambda *a, **k: pytest.fail("browser accessed"))
    assert search.search_x(query, max_posts, latest) == {
        "ok": False, "error": "source_search_input_invalid"}


@pytest.mark.parametrize("latest", [False, True])
def test_unicode_query_is_data_and_search_mode_is_explicit(configured, monkeypatch, latest):
    browser(monkeypatch, [{"url": "https://x.com/maker/status/123", "text": "原始内容"}])
    query = '建筑 "geometry nodes" & f=user `$(no)`'
    result = search.search_x(query, latest=latest)
    assert result["ok"] and result["query"] == query
    parsed = parse_qs(urlsplit(result["url"]).query)
    assert parsed["q"] == [query]
    assert parsed.get("f") == (["live"] if latest else None)
    assert result["posts"][0]["excerpt"] == "原始内容"


def test_loaded_results_keep_own_post_identity_and_bound_excerpt(configured, monkeypatch):
    browser(monkeypatch, [
        {"url": "https://twitter.com/creator/status/123?ref=search", "text": "x" * 4010, "media": True},
        {"url": "https://x.com/creator/status/123", "text": "duplicate"},
        {"url": "https://x.com/other/status/456", "text": "second"},
        {"url": "https://x.com/creator/status/123/analytics", "text": "not a post"},
        {"url": "https://x.com.evil.test/creator/status/123", "text": "foreign"},
        {"url": "https://x.com/hidden/status/789", "text": "hidden", "hidden": True},
    ])
    result = search.search_x("topic", max_posts=1)
    assert result["ok"] and result["post_count"] == 1 and result["more_loaded_posts"]
    assert result["posts"] == [{
        "url": "https://x.com/creator/status/123", "author_handle": "creator",
        "published_at": "2026-01-02T03:04:05Z", "excerpt": "x" * 4000,
        "excerpt_truncated": True, "media_present": True,
    }]
    assert "not exhaustive" in result["limitations"]
    assert "public-visibility" in result["limitations"]


@pytest.mark.parametrize("empty", [False, True])
def test_empty_platform_state_is_distinct_from_loading_or_login(configured, monkeypatch, empty):
    browser(monkeypatch, [], empty=empty)
    result = search.search_x("topic")
    if empty:
        assert result["ok"] and result["posts"] == [] and result["empty_state_observed"]
    else:
        assert result == {"ok": False, "error": "source_content_not_ready"}


def test_unrecognized_article_does_not_become_no_results(configured, monkeypatch):
    browser(monkeypatch, [{"text": "visible article without post provenance"}])
    assert search.search_x("topic") == {"ok": False, "error": "source_content_not_ready"}


def test_origin_denial_happens_before_browser_access(configured, monkeypatch):
    monkeypatch.setenv("LOOPX_EGO_READ_ORIGINS", "https://example.com")
    monkeypatch.setattr(reader, "_run", lambda *a, **k: pytest.fail("browser accessed"))
    assert search.search_x("topic") == {"ok": False, "error": "source_origin_not_authorized"}


def test_user_control_never_resolves_or_navigates_page(configured, monkeypatch):
    seen = browser(monkeypatch, [], ownership="user")
    assert search.search_x("topic") == {"ok": False, "error": "source_reader_not_agent_owned"}
    assert seen == [{"pageAccess": 0, "domReads": 0}]


def test_redirect_never_extracts_destination_posts(configured, monkeypatch):
    seen = browser(monkeypatch, [{"url": "https://x.com/maker/status/123"}],
                   redirect="https://other.test/private")
    assert search.search_x("topic") == {"ok": False, "error": "source_url_changed"}
    assert seen[0]["domReads"] == 0


@pytest.mark.parametrize("posts", [
    [{"url": "javascript:alert(1)"}],
    [{"url": "https://x.com/maker/status/123", "author_handle": "different"}],
])
def test_malformed_provider_payload_is_not_a_valid_search(monkeypatch, posts):
    monkeypatch.setattr(reader, "_read", lambda *a, **k: {"ok": True, "text": json.dumps({
        "posts": posts, "empty_state_observed": False, "more_loaded_posts": False})})
    assert search.search_x("topic") == {"ok": False, "error": "browser_read_result_invalid"}


@pytest.mark.skipif(os.name == "nt", reason="Integration fixture emulates a POSIX Ego executable")
@pytest.mark.parametrize("text,excerpt,truncated", [
    ("x" * 3998 + "🙂", "x" * 3998 + "🙂", False),
    ("x" * 3998 + "🙂tail", "x" * 3998 + "🙂", True),
    ("x" * 3999 + "🙂tail", "x" * 3999, True),
    ("x" * 4000 + "🙂tail", "x" * 4000, True),
    ("🙂" * 2001, "🙂" * 2000, True),
], ids=["fits", "pair-before-limit", "pair-crosses-limit", "pair-after-limit", "all-emoji"])
def test_stdio_tools_share_auto_space_and_shutdown_owner(tmp_path, text, excerpt, truncated):
    """Execute extraction and real MCP serialization; retain one lifecycle owner."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for generated browser extraction")
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    events = tmp_path / "events.jsonl"
    executable = tmp_path / "ego-browser"
    executable.write_text(f"#!{sys.executable}\n" + r'''
import json, re, subprocess, sys
from pathlib import Path
events=Path(EVENTS)
script=sys.argv[-1]
kind='create' if 'taskSpace("LoopX public-source reader ' in script else 'close' if 'finish({keep:[]})' in script else 'read'
with events.open('a') as f: f.write(json.dumps({'kind':kind})+'\n')
if kind=='create':
    print('LOOPX_READER_SPACE:'+json.dumps({'id':19}))
elif kind=='read':
    url=json.loads(re.search(r'const requestedUrl=("(?:[^"\\]|\\.)*");',script)[1])
    search='const posts=[]' in script
    if search:
        inputs=json.dumps({'posts':[{'url':'https://x.com/maker/status/123','text':POST_TEXT}],
          'empty':False,'ownership':'agent','redirect':None,'script':script})
        harness=BROWSER_HARNESS.replace('INPUT', inputs, 1)
        result=subprocess.run([NODE, '-e', harness], capture_output=True, text=True,
          encoding='utf-8', timeout=10)
        print(result.stdout, end='')
        print(result.stderr, end='', file=sys.stderr)
        sys.exit(result.returncode)
    print('LOOPX_PUBLIC_SOURCE:'+json.dumps({'url':url,'canonical_url':url,'requested_url':url,
      'title':'Source','text':'source','truncated':False,'image_count':0,'images':[]}))
'''.replace("EVENTS", repr(str(events)))
       .replace("POST_TEXT", repr(text))
       .replace("BROWSER_HARNESS", repr(_BROWSER_HARNESS))
       .replace("NODE", repr(node)))
    executable.chmod(0o700)

    async def exercise():
        params = StdioServerParameters(command=sys.executable,
            args=["-m", "loopx.extensions.ego_source_reader"], env={
                **os.environ, "LOOPX_USAGE_PING": "0", "LOOPX_EGO_READ_BIN": str(executable),
                "LOOPX_EGO_READ_TASK_SPACE": "auto", "LOOPX_EGO_READ_PAGE": "p1",
                "LOOPX_EGO_READ_ORIGINS": "https://x.com"})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name: t for t in (await session.list_tools()).tools}
                assert tools["search_x"].annotations.readOnlyHint
                for name, args in [("search_x", {"query": "topic", "max_posts": 1}),
                                   ("read_public_url", {"url": "https://x.com/maker/status/123"})]:
                    result = await session.call_tool(name, args)
                    assert not result.isError, result.content
                    payload = json.loads(result.content[0].text)
                    assert payload["ok"]
                    if name == "search_x":
                        assert payload["post_count"] == 1
                        post = payload["posts"][0]
                        assert post["url"] == "https://x.com/maker/status/123"
                        assert post["excerpt"] == excerpt
                        assert post["excerpt_truncated"] is truncated
                        assert len(post["excerpt"].encode("utf-16-le")) <= 8000
                    else:
                        assert payload["text"] == "source"

    asyncio.run(exercise())
    kinds = [json.loads(line)["kind"] for line in events.read_text().splitlines()]
    assert kinds == ["create", "read", "read", "close"]
