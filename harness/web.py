"""Bounded HTTP text retrieval; no browser, JavaScript, or network sandbox."""
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

from .tools import Tool


_BYTE_LIMIT = 256 * 1024
_TEXT_LIMIT = 16000
_REDIRECTS = {301, 302, 303, 307, 308}


def _http_url(value: str) -> httpx.URL:
    if not isinstance(value, str):
        raise TypeError("url must be a string")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url must be an absolute HTTP or HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not supported")
    return httpx.URL(value)


class _HTMLText(HTMLParser):
    """Keep text and simple paragraph breaks, omitting non-content elements."""

    hidden = {"script", "style", "noscript", "template", "svg"}
    blocks = {"p", "div", "br", "hr", "li", "tr", "h1", "h2", "h3", "h4",
              "h5", "h6", "pre", "blockquote", "section", "article", "header", "footer"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title = []
        self.hidden_depth = 0
        self.in_head = False
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.hidden:
            self.hidden_depth += 1
        if tag == "head":
            self.in_head = True
        if tag == "title":
            self.in_title = True
        if tag in self.blocks and not self.hidden_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.hidden:
            self.hidden_depth = max(0, self.hidden_depth - 1)
        if tag == "head":
            self.in_head = False
        if tag == "title":
            self.in_title = False
        if tag in self.blocks and not self.hidden_depth:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.hidden_depth:
            return
        if self.in_title:
            self.title.append(data)
        elif not self.in_head:
            self.parts.append(data)

    def text(self):
        lines = (" ".join(line.split()) for line in "".join(self.parts).splitlines())
        return "\n".join(line for line in lines if line)


def web_tools(client: httpx.AsyncClient) -> dict[str, Tool]:
    """Use a dedicated client without credentials; caller owns its lifetime.

    Network access includes local addresses. Do not expose this tool to
    untrusted remote callers; it is intended for a local user-operated agent.
    """

    async def fetch_url(url: str) -> str:
        destination = _http_url(url)
        for redirects in range(6):
            # Inspect each redirect before making another request. Automatic
            # redirects could visit a URL containing credentials first.
            async with client.stream("GET", destination, follow_redirects=False,
                                     timeout=15, headers={"Accept-Encoding": "identity"}) as response:
                if response.status_code in _REDIRECTS:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("redirect response has no Location header")
                    if redirects == 5:
                        raise ValueError("too many redirects (maximum 5)")
                    # Validate credentials before joining, which can normalize
                    # away an empty userinfo component such as https://@host.
                    parsed = urlsplit(location)
                    if parsed.username is not None or parsed.password is not None:
                        raise ValueError("URL credentials are not supported")
                    destination = _http_url(str(destination.join(location)))
                    continue
                response.raise_for_status()
                mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if not (mime.startswith("text/") or mime in {
                    "application/json", "application/xml", "application/xhtml+xml"
                } or mime.endswith(("+json", "+xml"))):
                    raise ValueError(f"unsupported content type: {mime or '(missing)'}")
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    body.extend(chunk[:_BYTE_LIMIT + 1 - len(body)])
                    if len(body) > _BYTE_LIMIT:
                        break
                truncated = len(body) > _BYTE_LIMIT
                # HTTPX honors declared charsets and otherwise uses UTF-8.
                content = bytes(body[:_BYTE_LIMIT]).decode(response.encoding, errors="replace")
                title = ""
                if mime in {"text/html", "application/xhtml+xml"}:
                    parser = _HTMLText()
                    parser.feed(content)
                    parser.close()
                    title = " ".join("".join(parser.title).split())[:240]
                    content = parser.text()
                truncated = truncated or len(content) > _TEXT_LIMIT
                heading = f"URL: {response.url}\nStatus: {response.status_code}"
                if title:
                    heading += f"\nTitle: {title}"
                return heading + "\n\n" + content[:_TEXT_LIMIT] + ("\n[truncated]" if truncated else "")
        raise RuntimeError("redirect limit exceeded")  # The loop always returns or raises.

    return {"fetch_url": Tool(
        description="Fetch readable text from an HTTP(S) URL, without JavaScript; output limited to 16000 characters.",
        parameters={"type": "object", "properties": {"url": {"type": "string"}},
                    "required": ["url"], "additionalProperties": False},
        execute=fetch_url,
        timeout=20,
    )}
