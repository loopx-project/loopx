# Rendered public-source reading with Ego

The optional `loopx.extensions.ego_source_reader` stdio MCP adapter gives an
existing Codex host narrow `read_public_url` and `read_public_image` tools when its model shell cannot
reach Ego's local bootstrap. It reuses an installed Ego browser with a reserved
Page. It does not replace the Chat Session owner or create a browser,
model thread, background service, material catalog or permission authority.

Operator setup is explicit. Choose `auto` to lazily create one TaskSpace and its
initial `p1` per MCP process. Ego's named factory can reuse an existing space,
so the adapter generates a unique host nonce once and keeps that name throughout
its lifecycle and confirmed-missing recovery. This avoids expired fixed ids and
keeps concurrent hosts on separate Pages. Alternatively, reserve an existing numeric TaskSpace
and Page for this process. Once explicitly enabled, the reader defaults to all
HTTPS origins; `LOOPX_EGO_READ_ORIGINS = "*"` selects that mode explicitly.
Existing comma-separated origin lists retain their restricted behavior. Use an
explicit list for a community or otherwise restricted host. Do not reserve a Page
shared with another process. Navigation
may use the existing browser's session; origin configuration does not prove that
every page on that origin is public. Follow the browser's installed skill for
TaskSpace/Page ownership and user consent.

An origin list is insufficient for a public community host that must not reuse
personal cookies. Its isolated native Chat can explicitly enable the separate
[anonymous static public reader](public-source-reader.md), retaining its shell,
history and private-MCP boundary. Dynamic signed-in pages remain outside that
provider's coverage; do not silently substitute the personal Ego reader.

For example, append a uniquely named server to the **actual execution host's**
MCP configuration, preserving its existing entries and authentication:

```toml
[mcp_servers.loopx_ego_source_read]
command = "/absolute/path/to/loopx-environment/bin/python"
args = ["-m", "loopx.extensions.ego_source_reader"]
startup_timeout_sec = 30
tool_timeout_sec = 40

[mcp_servers.loopx_ego_source_read.env]
LOOPX_EGO_READ_BIN = "/absolute/path/to/installed/ego-browser"
LOOPX_EGO_READ_TASK_SPACE = "auto"
LOOPX_EGO_READ_PAGE = "p1"
LOOPX_EGO_READ_ORIGINS = "*"
```

All-origin mode removes per-site configuration for source links, including
links discovered while reading another source. It changes this provider's origin
policy only: URLs still require HTTPS with no credentials and only the default
port, and navigation cannot read a redirected or raced Page. It does not grant
login, browser control, filesystem writes, publishing or private-account access.
The tools retain their existing `read_public_url` and `read_public_image` names;
the host must still distinguish public sources from signed-in private pages.
The adapter remains optional and disabled until configured on the execution host.

To restrict or roll back that origin scope, set
`LOOPX_EGO_READ_ORIGINS = "https://example.com,https://www.example.org"` and reload
the idle host. An empty value, `all`, subdomain wildcards or a list mixing `*`
with origins is invalid and does not expand the scope.

Ordinary workspace Chat guides the Agent to inspect actual pixels when a
requested answer depends on a figure, screenshot or chart. Captions, OCR and SVG
source alone do not complete that visual read. If a reader fails, the Agent should
discover a permitted alternative and check its rendering for missing labels,
orientation and arrows before describing it. This is Agent guidance, not a
machine-enforced completeness check or authority to install tools, access a
private browser, expand a workspace grant or take control from the user. The
optional reader still requires explicit host setup; ordinary Chat does not enable
it automatically, and workspace-only hosts do not inherit personal MCP servers.

Use a supported LoopX installation containing this module. Restart an idle host
through its existing service path and resume the original Session. Do not change
its sandbox, approval policy, workspace grants or authentication to make the
tool work. The Python environment needs the optional `loopx[ego-source-reader]`
extra (`mcp==1.28.1`); the base LoopX CLI has no Python runtime dependencies.
Keep this environment outside PATH when another installation owns `loopx`.
Disable by removing only this MCP entry and restarting that idle host. Keep a
private configuration backup and the installed/source revision for rollback.

In `auto` mode, URL/configuration/origin validation runs before creation.
The process reuses its space for text and image calls, and replaces it once only
when Ego explicitly reports `task space not found`. Other browser errors,
verification walls and user-control stops do not create replacements. An
ambiguous creation receipt never triggers another creation attempt.

If a creation receipt is lost, the next tool call first looks up the process's
exact unique space name. One Agent-created, currently Agent-owned match restores
that original space automatically. Zero matches, duplicate names, unknown
ownership or a user-controlled match preserve the uncertainty without creating,
claiming or taking over another space. This bounded lookup shares the existing
30-second call budget and returns no unrelated space metadata.

Before navigation, the reader checks live ownership rather than trusting an old
TaskSpace handle. `source_reader_not_agent_owned` preserves the Page without
navigating or capturing it. Once Ego returns Agent control, the next call uses
the same space automatically; no extra chat confirmation is needed. This tool
does not force control, poll in the background or grant approval for protected
actions. Browser failures and rendering timeouts alone are not requests for
human handoff. A source's actual login or verification requirement remains a
separate boundary; follow the installed Ego skill and continue independent work.

On normal MCP shutdown or SIGTERM, it finishes only its own created,
still-agent-owned space. SIGTERM cleanup can complete while the stdio server
still waits for its host to close stdin; callers should also close the pipe when
stopping the process. Configured numeric spaces are never finished by the
adapter. Shutdown failures may require operator cleanup; a killed process cannot
guarantee cleanup. No login/profile selection or browser-control tool is exposed.

The tool accepts an HTTPS URL, checks the configured origin policy before navigation,
and uses WHATWG URL normalization for the target before checking the exact
resulting URL before DOM extraction. Equivalent dot segments and query escaping
do not count as redirects. The fixed operator-owned script supplies the canonical
target; results retain both requested and observed URLs. Redirects and Page
races fail closed. Input cannot select code, executables, Page labels or browser
commands. Calls within one process reject concurrent reads. Execution times out
after 30 seconds; returned text is limited to 100,000 characters. Errors omit
raw browser diagnostics; use local provider logs for diagnosis.

Both text and image reads wait up to 10 seconds within that same execution
budget for readable semantic content after navigation. Articles inside the
main region take precedence, with the main region or body as fallback. Ancillary
landmarks such as sidebars and navigation cannot select, satisfy or block the content
region. Visible loading/busy indicators in the content and a busy ancestor
delay extraction; unrelated sidebar spinners do not. There
is no minimum text length. Image reads also accept a visible, loaded image in
that region without requiring a text caption. A bounded readiness timeout returns
`source_content_not_ready`, preserving the Page for a later retry. Redirects
remain fenced before DOM access; browser control or ownership failures are not
treated as readiness timeouts. This is a rendering check, not proof of a
complete article or a solved verification wall.

Results include the actual URL, rendered text, digest, character count and
truncation flag. `image_count` and the first 128 image indices, alt labels and
natural dimensions are DOM metadata; `images_read` remains false for text reads.
`image_inventory_truncated` makes the bounded inventory explicit.

`read_public_image(url, index)` uses that same authorized Page to return one
actual PNG image-content block and its digest/dimensions. It scrolls the selected
image into view and waits up to 10 seconds for it to load. Capture is bounded to
4 MB and 4,096 pixels per edge; hidden, oversized, unloaded or missing images
fail visibly. Temporary screenshots use a private directory and are removed
after the response is assembled. No arbitrary URL download is exposed to the
caller. Images use the same origin policy as text, and both share the existing
per-process read lock.

The image result is a **rendered region**, possibly occluded by page overlays,
not the original image file. URL, image source and geometry are checked before
and after capture; those checks are not atomic with screenshot capture. Keep
the Page reserved for this process, and follow Ego ownership/user-control stops.
An unstable Page returns no image. One image result does not prove that other
images, the article or an embedded social post's primary source were read.
Only a host that actually consumes MCP image content qualifies visual reading.
Success means text extraction succeeded, **not** that an article is complete,
a verification wall was solved, referenced sources were read, or image content
was understood. Page text and image pixels remain untrusted data, never instructions. A source
read does not authorize material intake, note edits, publishing or delegation.

Tests cover transport and scope boundaries with a simulated CLI and execute
the generated scripts in Node to check canonical URLs and rejection before DOM
access. These fixtures do not operate the user's browser. Release
qualification must separately verify a tool call by the original native Bot
Session, a visible channel reply, a rejected invalid URL (and an out-of-scope URL
when using an explicit list), preserved Session
identity and unchanged workspace grants. Record failure/untested cases rather
than treating provider metadata or a host-side probe as native Bot acceptance.
