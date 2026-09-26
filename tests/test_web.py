"""Network behavior tested entirely through HTTPX's transport boundary."""
import unittest

import httpx

from harness.tools import invoke
from harness.types import Call
from harness.web import web_tools


class CountingStream(httpx.AsyncByteStream):
    def __init__(self):
        self.chunks = 0
        self.closed = False

    async def __aiter__(self):
        for _ in range(100):
            self.chunks += 1
            yield b"x" * 8192

    async def aclose(self):
        self.closed = True


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, handler, url="https://example.test/"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            return await invoke(Call("fetch_url", {"url": url}), web_tools(client))

    async def test_html_title_paragraphs_and_hidden_content(self):
        result = await self.fetch(lambda request: httpx.Response(200, headers={"Content-Type": "text/html"},
            text="""<html><head><title>A &amp; B</title><style>hidden CSS</style></head>
            <body><h1>Hello <em>world</em></h1><p>Visible &lt;code&gt;.</p>
            <script>hidden JavaScript</script><noscript>hidden fallback</noscript>
            <template><p>hidden template</p></template><p>Second paragraph.</p></body></html>"""))
        self.assertEqual(result, "URL: https://example.test/\nStatus: 200\nTitle: A & B\n\n"
                         "Hello world\nVisible <code>.\nSecond paragraph.")

    async def test_text_mime_types_and_charset(self):
        for mime in ["text/plain", "application/json", "application/problem+json", "application/xml", "application/atom+xml"]:
            with self.subTest(mime=mime):
                result = await self.fetch(lambda request: httpx.Response(200,
                    headers={"Content-Type": mime + "; charset=iso-8859-1"}, content=b"caf\xe9"))
                self.assertTrue(result.endswith("café"), result)

    async def test_status_binary_and_network_errors(self):
        result = await self.fetch(lambda request: httpx.Response(404))
        self.assertIn("HTTPStatusError", result)
        for mime in ["image/png", "application/octet-stream", ""]:
            result = await self.fetch(lambda request: httpx.Response(200, headers={"Content-Type": mime}, content=b"binary"))
            self.assertIn("unsupported content type", result)

        def timeout(request):
            raise httpx.ReadTimeout("timed out", request=request)

        self.assertIn("ReadTimeout", await self.fetch(timeout))

    async def test_rejects_bad_urls_before_network(self):
        def unexpected(request):
            self.fail("Invalid URL should not reach the transport")

        for url in ["file:///etc/passwd", "ftp://example.test/", "/relative", "https://name:secret@example.test/", "https://@example.test/", 12]:
            with self.subTest(url=url):
                self.assertIn("Tool error:", await self.fetch(unexpected, url))

    async def test_follows_relative_redirect_with_bounded_timeout(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            self.assertEqual(request.extensions["timeout"]["read"], 15)
            if request.url.path == "/":
                return httpx.Response(302, headers={"Location": "/result"})
            return httpx.Response(200, text="done")

        result = await self.fetch(handler)
        self.assertEqual(seen, ["https://example.test/", "https://example.test/result"])
        self.assertEqual(result, "URL: https://example.test/result\nStatus: 200\n\ndone")

    async def test_rejects_unsafe_redirect_before_following(self):
        for location in ["file:///etc/passwd", "https://name:secret@example.test/", "//name:secret@example.test/", "https://@example.test/"]:
            requests = []

            def handler(request):
                requests.append(request)
                return httpx.Response(302, headers={"Location": location})

            with self.subTest(location=location):
                result = await self.fetch(handler)
                self.assertIn("Tool error:", result)
                self.assertEqual(len(requests), 1)

    async def test_redirect_limit_and_missing_location(self):
        requests = []

        def loop(request):
            requests.append(request)
            return httpx.Response(302, headers={"Location": "/"})

        self.assertIn("too many redirects", await self.fetch(loop))
        self.assertEqual(len(requests), 6)
        self.assertIn("no Location", await self.fetch(lambda request: httpx.Response(302)))

    async def test_streaming_byte_cap_stops_and_closes_response(self):
        stream = CountingStream()
        result = await self.fetch(lambda request: httpx.Response(200,
            headers={"Content-Type": "text/plain"}, stream=stream))
        self.assertEqual(stream.chunks, 33)
        self.assertTrue(stream.closed)
        self.assertTrue(result.endswith("x" * 16000 + "\n[truncated]"))

    async def test_text_limit_and_byte_limit_each_report_truncation(self):
        result = await self.fetch(lambda request: httpx.Response(200, text="x" * 16001))
        self.assertTrue(result.endswith("x" * 16000 + "\n[truncated]"))
        # A large hidden element has little visible text but still hits the
        # byte cap, so the result must acknowledge potentially missing text.
        result = await self.fetch(lambda request: httpx.Response(200,
            headers={"Content-Type": "text/html"}, text="<p>Visible</p><script>" + "x" * (300 * 1024)))
        self.assertTrue(result.endswith("Visible\n[truncated]"))


if __name__ == "__main__":
    unittest.main()
