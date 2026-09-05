import asyncio

import httpx

from dash_mcp_server import server
from dash_mcp_server.server import (
    dash_error_message,
    extract_section,
    parse_fragment,
)


class TestParseFragment:
    def test_dash_ref_fragment(self):
        url = "http://127.0.0.1:1234/Dash/abc/Enumerable.html#//dash_ref_method%2Di%2Dsort%5Fby/Method/sort_by/0"
        assert parse_fragment(url) == "method-i-sort_by"

    def test_plain_fragment(self):
        url = "http://127.0.0.1:1234/page.html#some-anchor"
        assert parse_fragment(url) == "some-anchor"

    def test_no_fragment(self):
        url = "http://127.0.0.1:1234/page.html"
        assert parse_fragment(url) is None

    def test_empty_fragment(self):
        url = "http://127.0.0.1:1234/page.html#"
        assert parse_fragment(url) is None


class TestExtractSection:
    FULL_PAGE = """
    <html><body>
      <nav><a href="/">Home</a><a href="/docs">Docs</a></nav>
      <aside class="sidebar"><ul><li>Item</li></ul></aside>
      <div id="method-i-sort_by">
        <h2>sort_by</h2>
        <p>Sorts by the block return value.</p>
      </div>
      <div id="method-i-map">
        <h2>map</h2>
        <p>Maps elements.</p>
      </div>
    </body></html>
    """

    def test_extracts_anchor_section(self):
        result = extract_section(self.FULL_PAGE, "method-i-sort_by")
        assert "sort_by" in result
        assert "Sorts by the block return value" in result
        assert "Maps elements" not in result

    def test_strips_nav_when_no_anchor(self):
        result = extract_section(self.FULL_PAGE, None)
        assert "<nav>" not in result
        assert "Home" not in result
        assert "sort_by" in result
        assert "Maps elements" in result

    def test_strips_sidebar_when_no_anchor(self):
        result = extract_section(self.FULL_PAGE, None)
        assert "sidebar" not in result

    def test_falls_back_to_nav_strip_when_anchor_not_found(self):
        result = extract_section(self.FULL_PAGE, "nonexistent-anchor")
        assert "<nav>" not in result
        assert "sort_by" in result

    def test_walks_up_from_thin_anchor_element(self):
        html = """
        <html><body>
          <div id="method-wrapper">
            <a id="method-i-foo"></a>
            <h2>foo</h2>
            <p>Foo description.</p>
          </div>
        </body></html>
        """
        result = extract_section(html, "method-i-foo")
        assert "Foo description" in result

    def test_walks_up_falls_back_when_no_block_parent(self):
        html = """
        <html><body>
          <nav><a href="/">Home</a></nav>
          <a id="orphan-anchor"></a>
          <p>Content with no block wrapper.</p>
        </body></html>
        """
        result = extract_section(html, "orphan-anchor")
        # No suitable block parent, so falls back to nav-stripping
        assert "<nav>" not in result
        assert "Content with no block wrapper" in result


class TestDashErrorMessage:
    def test_reads_the_reason_out_of_a_dash_error_page(self):
        html = (
            '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            "<title>HTTP Error 400</title></head><body>"
            "<h1>HTTP Error 400: Full-text search is not supported for this docset</h1>"
            "<h3></h3></body></html>"
        )
        assert dash_error_message(html) == "Full-text search is not supported for this docset"

    def test_falls_back_to_the_raw_text_when_it_is_not_a_dash_error_page(self):
        assert dash_error_message("something else entirely") == "something else entirely"

    def test_handles_an_empty_body(self):
        assert dash_error_message("") == ""


class FakeContext:
    """Minimal stand-in for the MCP Context: records what the tool reported."""

    def __init__(self):
        self.messages = []

    async def debug(self, message):
        self.messages.append(("debug", message))

    async def info(self, message):
        self.messages.append(("info", message))

    async def warning(self, message):
        self.messages.append(("warning", message))

    async def error(self, message):
        self.messages.append(("error", message))


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        return self._response


class TestEnableDocsetFts:
    def _enable(self, monkeypatch, response, identifier="python"):
        async def fake_base_url(ctx):
            return "http://127.0.0.1:1234"

        monkeypatch.setattr(server, "working_api_base_url", fake_base_url)
        monkeypatch.setattr(server.httpx, "Client", lambda *a, **kw: FakeClient(response))
        ctx = FakeContext()
        return asyncio.run(server.enable_docset_fts(ctx, identifier)), ctx

    def test_a_fresh_enable_reports_success_and_keeps_dash_message(self, monkeypatch):
        response = FakeResponse({"message": "Full-text search indexing has started"})
        result, _ = self._enable(monkeypatch, response)
        assert result.enabled is True
        assert result.identifier == "python"
        assert "indexing has started" in result.message
        assert result.error is None

    def test_already_enabled_is_distinguishable_from_a_fresh_enable(self, monkeypatch):
        response = FakeResponse(
            {"message": "Full-text search is already enabled for this docset"}
        )
        result, _ = self._enable(monkeypatch, response)
        assert result.enabled is True
        assert "already enabled" in result.message

    def test_unsupported_docset_says_so_instead_of_just_false(self, monkeypatch):
        response = FakeResponse(
            status_code=400,
            text="<html><body><h1>HTTP Error 400: Full-text search is not supported "
            "for this docset</h1></body></html>",
        )
        result, _ = self._enable(monkeypatch, response)
        assert result.enabled is False
        assert result.error == "Full-text search is not supported for this docset"

    def test_unknown_docset_is_distinguishable_from_an_unsupported_one(self, monkeypatch):
        response = FakeResponse(
            status_code=404,
            text="<html><body><h1>HTTP Error 404: Docset not found</h1></body></html>",
        )
        result, _ = self._enable(monkeypatch, response)
        assert result.enabled is False
        assert "not found" in result.error.lower()
        assert "not supported" not in result.error.lower()

    def test_an_empty_identifier_is_rejected_before_any_request(self, monkeypatch):
        result, ctx = self._enable(monkeypatch, FakeResponse({}), identifier="  ")
        assert result.enabled is False
        assert "identifier" in result.error
