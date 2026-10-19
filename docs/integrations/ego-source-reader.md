# Rendered public-source reading with Ego

## Host skill continuation and upgrades

LoopX's existing workflow-skill refresh now carries a small continuation policy
for an **already installed** Ego browser skill. An authorized task can reuse its
known idle TaskSpace after an ordinary finish or handoff without another chat
confirmation. If the Agent’s recorded successful `finish({ keep: [] })` closed
that space, and the current inventory confirms its id is absent, later browser
work within the same authorization can create one replacement and reuse its
initial Page. A missing space without that closure evidence, a refused claim or
a stuck/failed space cannot use this exception. Keep one active space, record
its identity and finish once per space lifetime.

Active user intervention, explicit pauses, reserved control,
unknown task identity and browser-owned permission/login prompts retain their
existing stops. This is Agent guidance; it neither changes Ego's native control
checks nor gives the public-source reader a claim/takeover tool.

Normal LoopX Python-package upgrades already run `workflow-skills --install`;
the POSIX and Windows installers use the same Ego delivery adapter. The adapter
reads Ego's installed skill through its stable local source link, copies its API
references and appends the scoped policy in a local host copy. It never edits
the browser bundle, installs Ego, launches a browser, changes an MCP entry or
expands any operation/account permission. No optional reader is enabled.
App-private runtime preparation that explicitly skips host integrations retains
that isolation; it is not a host-skill upgrade. Refresh the host through the
normal LoopX install/update path above.

Inspect without writing, refresh after an independently authorized Ego upgrade,
and read back the result with the existing commands:

```bash
loopx workflow-skills --format json
loopx workflow-skills --install --format json
loopx workflow-skills --format json
```

The `ego_browser` result distinguishes current, refreshed, absent, preserved
customization and unavailable-source/recovery cases. The Markdown output also
includes its reason. A missing Ego skill does not install one. Unmanaged copies
and locally modified managed copies are preserved with an actionable message.
An explicit nondefault `--skills-dir` never changes a shared host entry outside
that scope. At the default personal root, the existing shared Ego entry and its
LoopX-managed personal-profile alias are reused rather than duplicated.
If an Ego reinstall resets the shared entry to its pristine upstream link while
the managed personal copy remains, refresh reconnects that known link to the
managed copy. Custom shared aliases remain untouched; Windows rollback includes
both affected entries.

`loopx workflow-skills --uninstall` removes unchanged LoopX workflow skills and
restores an unchanged managed Ego copy to Ego's **current** upstream link.
Locally modified copies are preserved; an unresolved restore is reported rather
than silently removing their policy. Publication rechecks the original entry's
identity and contents after staging and parking it; a concurrent local edit or
shared-alias change keeps refresh unready and preserves the original. An
interrupted publish restores the old entry. If restoring it also fails, its
original is retained at the reported backup path; restore that entry before retrying. Ego's own updater can reset its
host entry, so rerun the refresh after that independently authorized upgrade;
LoopX does not intercept or promise to modify the browser's updater.

## Optional public-source reader

The optional `loopx.extensions.ego_source_reader` stdio MCP adapter gives an
existing Codex host narrow `read_public_url`, `read_public_image`, and `search_x` tools when its model shell cannot
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

### X search and original-post discovery

`search_x(query, max_posts=10, latest=false)` uses the same configured reader,
reserved Page, origin scope, live-control checks, deadline and cleanup. It opens
X's rendered search UI and returns at most 20 currently loaded post references,
author handles, timestamps, bounded excerpts and media-presence indicators.
It does not scroll indefinitely or use a second browser controller. `latest=true`
selects the platform's Latest tab; the default uses its Top search.

For creator demonstrations, a useful starting query is
`"geometry nodes" filter:videos`; refine by topic, language or `from:<creator>`
when the first results reveal an origin. The provider encodes query text as URL
data. It never executes query text as shell or browser code. An explicit origin
list must include `https://x.com`; this operation does not expand that list.

Wait for real post cards or the platform's explicit empty state. Loading shells,
unrecognized cards, login/verification walls and timeouts are not empty-result
proof. Results identify each card through its own timestamp link, exclude account
navigation and analytics links, and deduplicate post URLs. Private account chrome
is not returned. Visible content still does not prove public visibility: review
that boundary before publishing or admitting public evidence.

Open the returned original URL with the existing reader or authorized Ego CLI,
then inspect relevant media and trace technical claims to primary documentation
or code. An excerpt, thumbnail or `media_present` flag is not full-post reading
or video observation. Search ranking is neither exhaustive coverage nor a
reliability score. Record sources through the existing
[external-evidence workflow](../../loopx/capabilities/external_research/README.md#egox-discovery--egox-发现)
when using LoopX research; the tool never admits evidence itself.

The operation is available after upgrading this optional adapter and reloading
its idle MCP host through the existing host lifecycle. Existing installations
without that update can use the installed Ego CLI under its skill. Do not restart
an active chat or change its permissions solely to expose the new tool. Disable
by removing the existing adapter entry; no new persistent setting is added.

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
human handoff. If control changes after inventory, Ego's native boundary still
stops the operation. Its hard-stop output sink can discard console sentinels;
the adapter preserves only the stable user-control/inactive error code in a
private temporary file and returns `source_reader_not_agent_owned`. It discards
partial output, makes no replacement or takeover attempt, and removes that
file after the call. Ordinary failures remain ordinary failures. This error
transport does not grant control or prove a live human handoff was tested.
A source's actual login or verification requirement remains a
separate boundary; follow the installed Ego skill and continue independent work.

On normal MCP shutdown or SIGTERM, it finishes only its own created,
still-agent-owned space, checking live control rather than the handle's creation
snapshot before resolving it. Missing spaces, unreadable state and user control
leave the browser untouched. SIGTERM cleanup can complete while the stdio server
still waits for its host to close stdin; callers should also close the pipe when
stopping the process. Configured numeric spaces are never finished by the
adapter. Shutdown failures may require operator cleanup; a killed process cannot
guarantee cleanup. No login/profile selection or browser-control tool is exposed.

The tool accepts an HTTPS URL, checks the configured origin policy before navigation,
and uses WHATWG URL normalization for the target before checking the exact
resulting URL before DOM extraction. Equivalent dot segments and query escaping
do not count as redirects. The fixed operator-owned script supplies the canonical
target; successful results retain both requested and observed URLs. Redirects and Page
races still fail closed before source text, title, image metadata or pixels are read.
For a same-origin mismatch, `source_url_changed` now includes `observed_url`,
validated against the original request and canonical target. Cross-origin
destinations are omitted, even in all-origin mode. Legacy error receipts without
location metadata remain supported. No redirected content or automatic second
read is returned. Check whether the observed location is the intended source
before making a separate authorized read; a login or verification page is not
the requested article. This diagnosis does not override an access denial or
authorize a different browser, login, takeover or wider host permissions.

`source_url_changed` proves that this call reached the browser's URL check;
it is not a disconnected-bootstrap error. A separate model-shell bootstrap
failure does not invalidate a working configured MCP reader. Keep those paths
distinct, and do not infer that opening the app or enabling Full Access is
required. Original host and site permission stops still apply.

Input cannot select code, executables, Page labels or browser
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
