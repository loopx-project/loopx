# Anonymous public evidence for isolated Chat

An isolated project Chat can read static public articles and actual image pixels
without inheriting personal MCP servers, browser cookies or account history.
The optional bundled `loopx.extensions.public_source_reader` provider performs
anonymous HTTPS GETs only. It owns external IO, not Session, grant, material,
delegation or publication authority. It is a fallback for static sources;
[Ego's rendered reader](ego-source-reader.md) remains useful on an explicitly
authorized personal host when a page needs browser rendering.

Install the optional dependency in the **existing Chat host's** environment:

```sh
python -m pip install 'loopx[public-source-reader]'
```

On that trusted host, set `LOOPX_CHAT_PUBLIC_SOURCE_READ=on` and restart the idle
service through its existing lifecycle. `off` is the default and rollback.
Only native `workspace_only` Chat processes consume this setting; ordinary
personal Chat keeps its existing MCP configuration. Existing Sessions resume
in their original native store. Workspace grants, shell network restrictions,
model authentication, audience and history do not change.

The host first disables all effective personal/project MCP entries, then admits
one provider with a fixed interpreter/module, minimal environment and no caller
credentials. Isolated Python excludes the writable workspace and `PYTHONPATH`
from provider module lookup. The reserved name `loopx_public_source_read` must not appear in a
lower native configuration layer: a collision fails before model dispatch rather
than merging its command, environment or headers. Project files and model input
cannot enable this provider. Missing dependencies are an installation failure,
not authority to enable a personal reader or widen the shell sandbox.

`read_public_url(url)` returns static text, image indices, observed URL, source
digest and explicit truncation/coverage. `read_public_image(url, index)` reads
that article again and returns one PNG image-content block with its source
digest. A changed article may have changed image indices; verify the returned
digest and URL. Text/alt labels do not prove pixels were read, and static HTML
does not prove a dynamic article or a verification wall was fully read. The
Agent must report missing coverage and inspect rendered labels and arrows.

The provider sends no cookies, account headers, proxy settings or caller-defined
headers. Every destination and redirect must resolve entirely to globally
routable addresses; the checked address is pinned to the TLS connection while
the original hostname is used for certificate validation. File URLs, credentials
in URLs, nonstandard ports, local/private/link-local addresses and mixed DNS
answers are rejected. The whole observation, including DNS and rendering, runs
in a minimal-environment worker with a 30-second deadline. Responses are bounded
to 4 MB, text to 100,000 characters, image inventories to 128 and image edges to
4,096 pixels. Errors return no private interpreter paths or resolver output.

PNG/JPEG/WebP and self-contained SVG images are supported. SVG geometry and
aspect ratio are preserved; active elements, external references and stylesheet
blocks are rejected. The bundled [resvg renderer](https://resvg-py.readthedocs.io/en/latest/api.html)
uses only standard OS font directories, skipping the account's font database;
no additional native Cairo installation is required. This provider never reads
a local source file supplied by the caller or navigates an authenticated browser.

Qualification must separately demonstrate native Session tool/image consumption,
an original-topic answer with actual provider readback, outside/private-resource
rejection, unchanged grants/history and source completeness. A successful host
MCP probe or unit suite alone does not qualify the community Bot's golden pack.
Keep admission on hold while mandatory cases remain failed, blocked or untested.
