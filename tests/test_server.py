import asyncio

import pytest

from dash_mcp_server import server
from dash_mcp_server.server import (
    extract_section,
    parse_fragment,
    validate_dash_load_url,
)


class DummyContext:
    async def debug(self, message: str):
        pass

    async def info(self, message: str):
        pass

    async def warning(self, message: str):
        pass

    async def error(self, message: str):
        pass


class TestParseFragment:
    def test_dash_ref_fragment(self):
        url = (
            "http://127.0.0.1:1234/Dash/abc/Enumerable.html"
            "#//dash_ref_method%2Di%2Dsort%5Fby/Method/sort_by/0"
        )
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


class TestValidateDashLoadUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:1234/Dash/doc.html",
            "http://localhost:1234/Dash/doc.html#anchor",
        ],
    )
    def test_accepts_local_url_on_expected_port(self, url):
        assert validate_dash_load_url(url, 1234) is None

    @pytest.mark.parametrize(
        "url, expected_error",
        [
            ("https://127.0.0.1:1234/Dash/doc.html", "must use http"),
            ("http://example.com:1234/Dash/doc.html", "local Dash API"),
            ("http://127.0.0.1:4321/Dash/doc.html", "active Dash API port"),
            ("http://user:pass@127.0.0.1:1234/Dash/doc.html", "credentials"),
            ("http://[::1]:1234/Dash/doc.html", "local Dash API"),
            ("http://127.0.0.1:not-a-port/Dash/doc.html", "invalid port"),
        ],
    )
    def test_rejects_non_dash_urls(self, url, expected_error):
        assert expected_error in validate_dash_load_url(url, 1234)


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self.payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeAsyncClient:
    requests = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, params=None):
        self.requests.append((url, params))
        return FakeResponse(
            {
                "results": [
                    {
                        "name": "map",
                        "type": "Method",
                        "load_url": "http://127.0.0.1:1234/doc.html",
                    }
                ]
            }
        )


class TestToolContracts:
    def test_list_docsets_does_not_launch_or_enable_dash(self, monkeypatch):
        async def unavailable(ctx):
            return None

        async def fail_launch(ctx):
            raise AssertionError("read-only tool must not launch Dash")

        async def fail_enable(ctx):
            raise AssertionError("read-only tool must not enable Dash API")

        monkeypatch.setattr(server, "working_api_base_url", unavailable)
        monkeypatch.setattr(server, "launch_dash", fail_launch)
        monkeypatch.setattr(server, "enable_dash_api_server", fail_enable)

        result = asyncio.run(server.list_installed_docsets(DummyContext()))

        assert result.error is not None
        assert result.returned_count == 0

    def test_search_accepts_docset_identifier_list(self, monkeypatch):
        async def base_url(ctx):
            return "http://127.0.0.1:1234"

        FakeAsyncClient.requests = []
        monkeypatch.setattr(server, "working_api_base_url", base_url)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

        result = asyncio.run(
            server.search_documentation(
                DummyContext(), "map", ["ruby", " python "], max_results=5
            )
        )

        assert result.error is None
        assert result.warning is None
        assert result.returned_count == 1
        assert result.total_count == 1
        assert FakeAsyncClient.requests[0][1]["docset_identifiers"] == "ruby,python"

    def test_enable_docset_fts_returns_structured_error(self):
        result = asyncio.run(server.enable_docset_fts(DummyContext(), ""))

        assert result.success is False
        assert result.error == "Docset identifier cannot be empty"

    def test_setup_tool_returns_base_url_when_ready(self, monkeypatch):
        async def ready(ctx, launch, enable_api_server):
            assert launch is False
            assert enable_api_server is False
            return "http://127.0.0.1:1234"

        monkeypatch.setattr(server, "ensure_dash_ready", ready)

        result = asyncio.run(
            server.ensure_dash_api_ready(
                DummyContext(), launch_dash_app=False, enable_api_server=False
            )
        )

        assert result.success is True
        assert result.base_url == "http://127.0.0.1:1234"
