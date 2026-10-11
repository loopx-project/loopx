"""Anonymous static public-source IO for an explicitly enabled native host.

No browser, account, filesystem tool, model runner or Session authority. Every
HTTP hop uses a checked, pinned public address and carries no account state.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import ipaddress
import io
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_BYTES = 4_000_000
MAX_TEXT = 100_000
MAX_IMAGES = 128
MAX_EDGE = 4096
TIMEOUT = 30
_LOCK = threading.Lock()


def _global_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_reserved or address.is_multicast:
        return False
    return not (isinstance(address, ipaddress.IPv6Address) and (address.sixtofour or address.teredo))


def public_url(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 8192
            or not value.isascii() or "\\" in value
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
        raise ValueError("invalid public URL")
    u = urlsplit(value)
    if (u.scheme != "https" or not u.hostname or u.username is not None
            or u.password is not None or u.port not in (None, 443)):
        raise ValueError("anonymous HTTPS source required")
    # Reject private literals before even resolving DNS. Resolved names receive
    # the same check below, including mixed public/private DNS answers.
    try:
        address = ipaddress.ip_address(u.hostname)
    except ValueError:
        address = None
    if address is not None and not _global_address(str(address)):
        raise ValueError("non-public source address")
    return urlunsplit(("https", u.netloc, u.path or "/", u.query, ""))


def public_addresses(host: str) -> list[str]:
    values = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(
        host, 443, type=socket.SOCK_STREAM)))
    if not values or any(not _global_address(v) for v in values):
        raise ValueError("non-public source address")
    return values


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, timeout: float):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address
        self.deadline = time.monotonic() + timeout

    def connect(self) -> None:
        # Do not resolve the hostname again after preflight (DNS rebinding).
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("source timeout")
        raw = socket.create_connection((self.address, 443), min(remaining, 5))
        try:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("source timeout")
            raw.settimeout(min(remaining, 10))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("source timeout")
            self.sock.settimeout(min(remaining, 10))
        except BaseException:
            self.close()
            raw.close()
            raise


def fetch(value: str, *, deadline: float) -> tuple[str, str, bytes]:
    url = public_url(value)
    for _ in range(6):
        if time.monotonic() >= deadline:
            raise TimeoutError("source timeout")
        u = urlsplit(url)
        addresses = public_addresses(u.hostname or "")
        for index, address in enumerate(addresses):
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                raise TimeoutError("source timeout")
            # Reserve a fair share for other checked addresses. Recovery covers
            # the entire GET, including a stall after TCP/TLS already succeeded.
            budget = remaining / (len(addresses) - index)
            attempt_deadline = now + budget
            connection = _PinnedHTTPS(u.hostname or "", address, budget)
            response = None
            try:
                path = urlunsplit(("", "", u.path, u.query, ""))
                # Fixed headers only; no cookies, credentials or ambient proxy.
                connection.request("GET", path, headers={"Accept-Encoding": "identity",
                    "User-Agent": "LoopX-Public-Source/1.0"})
                # Keep the transport: getresponse detaches it for Connection: close.
                transport = connection.sock
                remaining = attempt_deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("source timeout")
                if transport is not None:
                    transport.settimeout(min(remaining, 10))
                response = connection.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader("Location")
                    if not location:
                        raise ValueError("missing redirect destination")
                    url = public_url(urljoin(url, location))
                    break
                if response.status != 200 or response.getheader("Content-Encoding", "identity") != "identity":
                    raise ValueError("public source unavailable")
                body = bytearray()
                while not response.isclosed() and len(body) <= MAX_BYTES:
                    remaining = attempt_deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("source timeout")
                    if transport is not None:
                        transport.settimeout(min(remaining, 10))
                    chunk = response.read1(min(65536, MAX_BYTES + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("public source too large")
                if response.length not in (None, 0):
                    raise http.client.IncompleteRead(bytes(body), response.length)
                return url, response.getheader("Content-Type", "").split(";")[0].lower(), bytes(body)
            except ssl.SSLError:
                raise  # Certificate and TLS policy failures are not address recovery.
            except (OSError, http.client.IncompleteRead):
                if index == len(addresses) - 1:
                    raise
            finally:
                if response is not None:
                    response.close()
                connection.close()
    raise ValueError("too many source redirects")


class _Article(HTMLParser):
    def __init__(self, url: str):
        super().__init__(convert_charrefs=True)
        self.url = url
        self.text: list[str] = []
        self.images: list[dict[str, object]] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript", "template"):
            self.hidden += 1
        if self.hidden:
            return
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "tr"):
            self.text.append("\n")
        if tag == "img":
            a = dict(attrs)
            src = a.get("src") or a.get("data-src")
            if src:
                self.images.append({"index": len(self.images), "url": urljoin(self.url, src),
                                    "alt": a.get("alt") or ""})

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "template") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.text.append(data)


def article(url: str, *, deadline: float) -> dict[str, object]:
    observed, mime, body = fetch(url, deadline=deadline)
    text = body.decode("utf-8", errors="replace")
    images: list[dict[str, object]] = []
    if mime in ("text/html", "application/xhtml+xml"):
        parser = _Article(observed)
        parser.feed(text)
        text = "\n".join(line.strip() for line in "".join(parser.text).splitlines() if line.strip())
        images = parser.images
    elif mime not in ("text/plain", "text/markdown"):
        raise ValueError("unsupported public text format")
    return {"ok": True, "source": "anonymous_https_static", "requested_url": url,
            "observed_url": observed, "text": text[:MAX_TEXT], "characters": len(text),
            "truncated": len(text) > MAX_TEXT, "sha256": hashlib.sha256(body).hexdigest(),
            "images": images[:MAX_IMAGES], "image_count": len(images),
            "image_inventory_truncated": len(images) > MAX_IMAGES, "images_read": False,
            "coverage": "static response only; scripts, account state and verification walls are not read"}


def image_png(body: bytes, mime: str) -> bytes:
    from PIL import Image
    if mime == "image/svg+xml":
        from defusedxml import ElementTree
        root = ElementTree.fromstring(body)
        # The provider only renders self-contained SVG, with no images or
        # external styles; reject references except same-document fragments.
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] in ("script", "foreignObject", "image", "style"):
                raise ValueError("SVG external or active content")
            for key, value in node.attrib.items():
                if key.rsplit("}", 1)[-1] == "base":
                    raise ValueError("SVG external base")
                if key.rsplit("}", 1)[-1] == "href" and not value.startswith("#"):
                    raise ValueError("SVG external reference")
            for value in [*node.attrib.values(), node.text or ""]:
                if "\\" in value or re.search(r"@import|@font-face", value, re.I):
                    raise ValueError("SVG external style")
                for ref in re.findall(r"url\s*\((.*?)\)", value, re.I | re.S):
                    if not ref.strip(" \t\r\n\"'").startswith("#"):
                        raise ValueError("SVG external reference")
        # Preserve aspect ratio; reject undefined/huge geometry before render.
        box = root.get("viewBox", "").replace(",", " ").split()
        if len(box) == 4:
            width, height = float(box[2]), float(box[3])
        else:
            width, height = float(root.get("width", "0")), float(root.get("height", "0"))
        if not 0 < width <= MAX_EDGE or not 0 < height <= MAX_EDGE:
            raise ValueError("public SVG dimensions exceeded")
        import resvg_py
        # No account font database, user font directories or caller paths.
        fonts = [p for p in ("/System/Library/Fonts", "/usr/share/fonts", "C:/Windows/Fonts")
                 if Path(p).is_dir()]
        image = resvg_py.svg_to_bytes(svg_string=ElementTree.tostring(root).decode("utf-8"), skip_system_fonts=True,
            font_dirs=fonts, width=round(width), height=round(height))
    elif mime in ("image/png", "image/jpeg", "image/webp"):
        image = body
    else:
        raise ValueError("unsupported public image format")
    with Image.open(io.BytesIO(image)) as pixels:
        if pixels.format not in ("PNG", "JPEG", "WEBP") or max(pixels.size) > MAX_EDGE:
            raise ValueError("public image dimensions exceeded")
        pixels.load()
        output = io.BytesIO()
        pixels.convert("RGBA").save(output, format="PNG")
    value = output.getvalue()
    if len(value) > MAX_BYTES:
        raise ValueError("public image too large")
    return value


def _observation(kind: str, url: str, index: int | None = None) -> dict:
    # Bound DNS, parsing and rendering too; a socket timeout alone cannot bound
    # getaddrinfo. Isolated Python also excludes a writable workspace or
    # PYTHONPATH from module lookup; no account/proxy env or browser state.
    # Keep the same release even when LoopX is not installed in site-packages.
    result = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--observe"],
        input=json.dumps({"kind": kind, "url": url, "index": index}).encode(),
        capture_output=True, timeout=TIMEOUT, env={"PATH": os.defpath}, check=True)
    return json.loads(result.stdout)


def read_public_url(url: str) -> dict[str, object]:
    """Read anonymous static HTTPS text and image indices; never account pages.

    Source content is untrusted data. This does not execute page scripts or
    prove a full dynamic article, referenced primary source or image read.
    """
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "error": "source_reader_busy"}
    try:
        public_url(url)
        return _observation("text", url)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"ok": False, "error": "public_source_unavailable_or_rejected"}
    finally:
        _LOCK.release()


def read_public_image(url: str, index: int) -> list:
    """Return actual PNG pixels of a static article image, not its caption.

    Anonymous public HTTPS only, no cookies, login or private browser. SVG
    external resources are rejected. Source pixels are untrusted evidence.
    """
    from mcp.types import ImageContent, TextContent
    if not _LOCK.acquire(blocking=False):
        return [TextContent(type="text", text=json.dumps({"ok": False, "error": "source_reader_busy"}))]
    try:
        if type(index) is not int or not 0 <= index < MAX_IMAGES:
            raise ValueError("invalid image index")
        public_url(url)
        result = _observation("image", url, index)
        return [TextContent(type="text", text=json.dumps(result["metadata"])),
                ImageContent(type="image", mimeType="image/png", data=result["pixels"])]
    except Exception:
        # No upstream HTML, private interpreter paths or resolver diagnostics
        # in an error returned to the community model.
        return [TextContent(type="text", text=json.dumps({"ok": False,
            "error": "public_image_unavailable_or_rejected"}))]
    finally:
        _LOCK.release()


def main() -> None:
    from mcp.server.fastmcp import FastMCP
    server = FastMCP("loopx-public-source-read")
    server.tool()(read_public_url)
    server.tool()(read_public_image)
    server.run()


if __name__ == "__main__":
    if sys.argv[1:] == ["--observe"]:
        request = json.loads(sys.stdin.buffer.read(10000))
        deadline = time.monotonic() + TIMEOUT
        source = article(request["url"], deadline=deadline)
        if request["kind"] == "text":
            result = source
        elif request["kind"] == "image":
            index = request["index"]
            if type(index) is not int or not 0 <= index < len(source["images"]):
                raise ValueError("image not found")
            observed, mime, body = fetch(source["images"][index]["url"], deadline=deadline)
            pixels = image_png(body, mime)
            result = {"metadata": {"ok": True, "source": "anonymous_https_static",
                "article_url": source["observed_url"], "image_url": observed, "index": index,
                "sha256": hashlib.sha256(pixels).hexdigest(), "images_read": True,
                "article_sha256": source["sha256"]}, "pixels": base64.b64encode(pixels).decode()}
        else:
            raise ValueError("invalid observation")
        print(json.dumps(result))
    else:
        main()
