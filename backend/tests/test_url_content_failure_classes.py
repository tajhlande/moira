"""Unit tests for url_content's failure classification.

Every failed ToolResult must carry a stable class prefix in its error
string ("blocked:", "timeout:", "not_found:", "too_large:",
"unsupported:", "network:", "parse:", "invalid:") — the research loop's
blocked-host memory and the retrieval harness's per-class failure
counts parse that prefix (split on the first colon).

These tests exercise the REAL ``_fetch`` path by injecting an
httpx.MockTransport into the client factory the tool constructs (no
network). The sibling file test_url_content.py is excluded from the
standard suite because it ends with real-network integration tests,
which is why classification coverage lives here instead.
"""

import httpx
import pytest

from moira.tools.builtin import url_content as url_content_module
from moira.tools.builtin.url_content import UrlContentTool

SAMPLE_HTML = """<html><head><title>T</title></head>
<body><h1>Heading</h1><p>Body paragraph.</p></body></html>"""


def _install_transport(monkeypatch, handler):
    """Make the tool's real ``_fetch`` use a MockTransport with ``handler``.

    The tool constructs ``httpx.AsyncClient`` internally; patching the
    module-level reference to a subclass that force-injects the mock
    transport keeps _fetch's own logic (status mapping, content-type
    guard, size guard) under test instead of being mocked away. The
    patch is global to the httpx module for the test's duration only.
    """

    class _MockTransportClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    monkeypatch.setattr(url_content_module.httpx, "AsyncClient", _MockTransportClient)


@pytest.fixture
def tool():
    defn = UrlContentTool.make_definition()
    return UrlContentTool(defn)


class TestUrlContentFailureClasses:
    """Failures are classifiable by error-string prefix."""

    @pytest.mark.asyncio
    async def test_missing_url_is_invalid(self, tool):
        r = await tool.execute({})
        assert not r.success
        assert r.error.startswith("invalid:")
        assert "url" in r.error

    @pytest.mark.asyncio
    async def test_403_is_blocked(self, tool, monkeypatch):
        _install_transport(monkeypatch, lambda request: httpx.Response(403, text="Forbidden"))
        r = await tool.execute({"url": "https://bls.gov/x"})
        assert not r.success
        assert r.error.startswith("blocked:")
        # The URL stays in the error for forensics.
        assert "https://bls.gov/x" in r.error

    @pytest.mark.asyncio
    async def test_429_is_blocked(self, tool, monkeypatch):
        _install_transport(
            monkeypatch, lambda request: httpx.Response(429, text="Too Many Requests")
        )
        r = await tool.execute({"url": "https://example.com/rate-limited"})
        assert not r.success
        assert r.error.startswith("blocked:")

    @pytest.mark.asyncio
    async def test_404_is_not_found(self, tool, monkeypatch):
        _install_transport(monkeypatch, lambda request: httpx.Response(404, text="Gone"))
        r = await tool.execute({"url": "https://example.com/missing"})
        assert not r.success
        assert r.error.startswith("not_found:")

    @pytest.mark.asyncio
    async def test_500_is_network(self, tool, monkeypatch):
        _install_transport(monkeypatch, lambda request: httpx.Response(500, text="Server Error"))
        r = await tool.execute({"url": "https://example.com/broken"})
        assert not r.success
        assert r.error.startswith("network:")

    @pytest.mark.asyncio
    async def test_timeout_is_classified(self, tool, monkeypatch):
        def handler(request):
            raise httpx.ReadTimeout("read timed out")

        _install_transport(monkeypatch, handler)
        r = await tool.execute({"url": "https://slow.example.com/page"})
        assert not r.success
        assert r.error.startswith("timeout:")

    @pytest.mark.asyncio
    async def test_pdf_content_type_is_unsupported(self, tool, monkeypatch):
        _install_transport(
            monkeypatch,
            lambda request: httpx.Response(
                200,
                content=b"%PDF-1.4 not html",
                headers={"content-type": "application/pdf"},
            ),
        )
        r = await tool.execute({"url": "https://example.com/paper.pdf"})
        assert not r.success
        assert r.error.startswith("unsupported:")
        assert "application/pdf" in r.error

    @pytest.mark.asyncio
    async def test_oversized_response_is_too_large(self, tool, monkeypatch):
        big = b"x" * (url_content_module._MAX_RESPONSE_SIZE + 1)
        _install_transport(
            monkeypatch,
            lambda request: httpx.Response(
                200, content=big, headers={"content-type": "text/html"}
            ),
        )
        r = await tool.execute({"url": "https://example.com/huge"})
        assert not r.success
        assert r.error.startswith("too_large:")

    @pytest.mark.asyncio
    async def test_parse_failure_is_classified(self, tool, monkeypatch):
        _install_transport(monkeypatch, lambda request: httpx.Response(200, text=SAMPLE_HTML))

        def boom(html, **kwargs):
            raise RuntimeError("trafilatura exploded")

        tool._extract = boom
        r = await tool.execute({"url": "https://example.com/page"})
        assert not r.success
        assert r.error.startswith("parse:")
        assert "https://example.com/page" in r.error

    @pytest.mark.asyncio
    async def test_success_path_unaffected(self, tool, monkeypatch):
        """A plain HTML page still extracts successfully through the
        classified _fetch — classification must not break fetching."""
        _install_transport(monkeypatch, lambda request: httpx.Response(200, text=SAMPLE_HTML))
        r = await tool.execute({"url": "https://example.com/ok"})
        assert r.success
        assert "Body paragraph" in r.output
