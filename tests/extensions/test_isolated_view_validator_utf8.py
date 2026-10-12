"""The isolated presentation view validator must transport UTF-8 bytes.

`load_presentation_view_validator` runs a non-core, extension-declared
`view_validator` inside the extension's own runtime interpreter, launched as
`python -I -c <helper>`. `-I` isolates that interpreter from `PYTHONPATH` and
from `PYTHONIOENCODING`/`PYTHONUTF8`, so its `sys.stdin`/`sys.stdout` fall back
to the host locale codec - `cp936` on a zh-CN Windows host - while the transport
owner writes the request as UTF-8 bytes (`ensure_ascii=False` then
`.encode("utf-8")`) and decodes the response as UTF-8. A view that contains
non-ASCII text (a Chinese headline, for example) then decodes into mojibake and
lone surrogates, and those surrogate escapes survive the JSON round trip: the
returned view no longer matches what the provider published, and persisting it
fails, because the envelope hash and the projection file are serialized with
`ensure_ascii=False` and written as UTF-8.

`tests/extensions/test_extension_provider_stdio_utf8.py` guards the provider
entrypoints, which pin their own stdio. The generated helper cannot be edited
by the extension, so it must not depend on the text streams at all.
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any

from loopx.extensions.presentation import (
    _ISOLATED_VIEW_VALIDATOR,
    load_presentation_view_validator,
)
from loopx.extensions.readiness import ResolvedRuntimeEntrypoint

LOCALE_CODEC = "gbk"
VIEW = {
    "headline": "中文标题，带标点。",
    "detail": "研究结论：证据不足。",
}
# `builtins.str` is a minimal echo validator with no extension dependency; the
# string form keeps the non-ASCII text in the response as well as the request.
ECHO_REFERENCE = "builtins:str"


def _transport_request(view: Any) -> bytes:
    """The exact request bytes `invoke_runtime_validator` writes to the child."""

    return json.dumps(
        {"operation": "validate", "reference": ECHO_REFERENCE, "view": view},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _run_helper_behind_locale_streams(request: bytes) -> dict[str, Any]:
    """Run the generated helper with the text streams a cp936 child would get.

    The transport hands the child UTF-8 bytes and reads UTF-8 bytes back, so
    only the helper's own text layer links locale to payload. Substituting a
    locale-coded `sys.stdin`/`sys.stdout` pair reproduces the affected host on
    any test host.
    """

    stdin = io.TextIOWrapper(
        io.BytesIO(request), encoding=LOCALE_CODEC, errors="surrogateescape"
    )
    stdout_buffer = io.BytesIO()
    stdout = io.TextIOWrapper(stdout_buffer, encoding=LOCALE_CODEC, errors="strict")
    previous_stdin, previous_stdout = sys.stdin, sys.stdout
    try:
        sys.stdin, sys.stdout = stdin, stdout
        exec(
            compile(_ISOLATED_VIEW_VALIDATOR, "<isolated-view-validator>", "exec"),
            {"__name__": "__main__"},
        )
        stdout.flush()
    finally:
        sys.stdin, sys.stdout = previous_stdin, previous_stdout
    return json.loads(stdout_buffer.getvalue().decode("utf-8"))


def test_generated_helper_transports_utf8_bytes_behind_locale_streams() -> None:
    """The helper's JSON transport must not depend on the locale text streams."""

    response = _run_helper_behind_locale_streams(_transport_request(VIEW))

    assert response == {"ok": True, "view": str(VIEW)}


def test_isolated_validator_round_trips_non_ascii_view_in_runtime() -> None:
    """A real isolated child behind the production transport keeps the view."""

    entrypoint = ResolvedRuntimeEntrypoint(
        argv_prefix=(sys.executable,),
        identity="test-runtime",
        python_executable=sys.executable,
    )
    validator = load_presentation_view_validator(
        {"view_validator": ECHO_REFERENCE},
        runtime_entrypoint=entrypoint,
    )

    assert validator(VIEW) == str(VIEW)