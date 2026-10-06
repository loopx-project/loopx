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
and Page for this process. Allow only the public-source origins needed for the
task. Do not reserve a Page
shared with another process or grant a private account/admin origin. Navigation
may use the existing browser's session; origin configuration does not prove that
every page on that origin is public. Follow the browser's installed skill for
TaskSpace/Page ownership and user consent.

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
LOOPX_EGO_READ_ORIGINS = "https://example.com,https://www.example.org"
```

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
ambiguous creation receipt fails closed until the operator inspects/restarts the
host. On normal MCP shutdown or SIGTERM, it finishes only its own created,
still-agent-owned space. SIGTERM cleanup can complete while the stdio server
still waits for its host to close stdin; callers should also close the pipe when
stopping the process. Configured numeric spaces are never finished by the
adapter. Shutdown failures may require operator cleanup; a killed process cannot
guarantee cleanup. No login/profile selection or browser-control tool is exposed.

The tool accepts an HTTPS URL, checks the configured origin before navigation,
and uses WHATWG URL normalization for the target before checking the exact
resulting URL before DOM extraction. Equivalent dot segments and query escaping
do not count as redirects. The fixed operator-owned script supplies the canonical
target; results retain both requested and observed URLs. Redirects and Page
races fail closed. Input cannot select code, executables, Page labels or browser
commands. Calls within one process reject concurrent reads. Execution times out
after 30 seconds; returned text is limited to 100,000 characters. Errors omit
raw browser diagnostics; use local provider logs for diagnosis.

Results include the actual URL, rendered text, digest, character count and
truncation flag. `image_count` and the first 128 image indices, alt labels and
natural dimensions are DOM metadata; `images_read` remains false for text reads.
`image_inventory_truncated` makes the bounded inventory explicit.

`read_public_image(url, index)` uses that same authorized Page to return one
actual PNG image-content block and its digest/dimensions. It scrolls the selected
image into view and waits up to 10 seconds for it to load. Capture is bounded to
4 MB and 4,096 pixels per edge; hidden, oversized, unloaded or missing images
fail visibly. Temporary screenshots use a private directory and are removed
after the response is assembled. No arbitrary URL download or new origin is
exposed to the caller. Images and text share the existing per-process read lock.

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
Session, a visible channel reply, a rejected out-of-scope URL, preserved Session
identity and unchanged workspace grants. Record failure/untested cases rather
than treating provider metadata or a host-side probe as native Bot acceptance.
