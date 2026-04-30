import asyncio

import httpx
import pytest

from dash_mcp_server import server
from dash_mcp_server.server import (
    extract_section,
    parse_fragment,
    validate_dash_load_url,
)


class DummyContext:
    def __init__(self):
        self.debug_messages = []
        self.info_messages = []
        self.warning_messages = []
        self.error_messages = []

    async def debug(self, message: str):
        self.debug_messages.append(message)

    async def info(self, message: str):
        self.info_messages.append(message)

    async def warning(self, message: str):
        self.warning_messages.append(message)

    async def error(self, message: str):
        self.error_messages.append(message)


def run(coro):
    return asyncio.run(coro)


def http_error(status_code: int, text: str = "error") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://127.0.0.1:1234/test")
    response = httpx.Response(status_code, text=text, request=request)
    return httpx.HTTPStatusError("HTTP error", request=request, response=response)


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
    def __init__(self, payload=None, text="", error=None):
        self.payload = payload
        self.text = text
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error
        return None

    def json(self):
        return self.payload if self.payload is not None else {}


class FakeAsyncClient:
    requests = []
    response = FakeResponse()
    exception = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, params=None):
        if self.exception:
            raise self.exception
        self.requests.append((url, params))
        return self.response


class TestToolContracts:
    def setup_method(self):
        FakeAsyncClient.requests = []
        FakeAsyncClient.response = FakeResponse()
        FakeAsyncClient.exception = None

    def test_mcp_tools_expose_annotations_and_output_schemas(self):
        tools = run(server.mcp.list_tools())
        by_name = {tool.name: tool for tool in tools}

        assert set(by_name) == {
            "ensure_dash_api_ready",
            "list_installed_docsets",
            "search_documentation",
            "enable_docset_fts",
            "load_documentation_page",
        }
        for tool in by_name.values():
            assert tool.annotations is not None
            assert tool.outputSchema is not None

        for name in {
            "list_installed_docsets",
            "search_documentation",
            "load_documentation_page",
        }:
            annotations = by_name[name].annotations
            assert annotations.readOnlyHint is True
            assert annotations.destructiveHint is False
            assert annotations.idempotentHint is True
            assert annotations.openWorldHint is False

        for name in {"ensure_dash_api_ready", "enable_docset_fts"}:
            annotations = by_name[name].annotations
            assert annotations.readOnlyHint is False
            assert annotations.destructiveHint is False
            assert annotations.idempotentHint is True
            assert annotations.openWorldHint is False

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

        result = run(server.list_installed_docsets(DummyContext()))

        assert result.error is not None
        assert result.returned_count == 0

    def test_working_api_base_url_returns_none_when_dash_is_not_running(
        self, monkeypatch
    ):
        async def not_running():
            return False

        monkeypatch.setattr(server, "check_dash_running", not_running)

        result = run(server.working_api_base_url(DummyContext()))

        assert result is None

    def test_working_api_base_url_returns_base_url_when_api_is_healthy(
        self, monkeypatch
    ):
        async def running():
            return True

        async def port(ctx):
            return 1234

        monkeypatch.setattr(server, "check_dash_running", running)
        monkeypatch.setattr(server, "get_dash_api_port", port)

        result = run(server.working_api_base_url(DummyContext()))

        assert result == "http://127.0.0.1:1234"

    def test_ensure_dash_api_ready_returns_structured_failure(self, monkeypatch):
        async def unavailable(ctx, launch, enable_api_server):
            assert launch is True
            assert enable_api_server is True
            return None

        monkeypatch.setattr(server, "ensure_dash_ready", unavailable)

        result = run(server.ensure_dash_api_ready(DummyContext()))

        assert result.success is False
        assert result.base_url is None
        assert result.error == server.DASH_API_UNAVAILABLE_ERROR

    def test_ensure_dash_ready_honors_setup_flags(self, monkeypatch):
        calls = []

        async def launch(ctx):
            calls.append("launch")
            return True

        async def working(ctx):
            calls.append("working")
            return None

        async def enable(ctx):
            calls.append("enable")
            return True

        monkeypatch.setattr(server, "launch_dash", launch)
        monkeypatch.setattr(server, "working_api_base_url", working)
        monkeypatch.setattr(server, "enable_dash_api_server", enable)

        result = run(server.ensure_dash_ready(DummyContext(), False, False))

        assert result is None
        assert calls == ["working"]

    def test_ensure_dash_ready_enables_api_when_requested(self, monkeypatch):
        calls = []

        async def launch(ctx):
            calls.append("launch")
            return True

        async def working(ctx):
            calls.append("working")
            if calls.count("working") == 2:
                return "http://127.0.0.1:1234"
            return None

        async def enable(ctx):
            calls.append("enable")
            return True

        monkeypatch.setattr(server, "launch_dash", launch)
        monkeypatch.setattr(server, "working_api_base_url", working)
        monkeypatch.setattr(server, "enable_dash_api_server", enable)

        result = run(server.ensure_dash_ready(DummyContext(), True, True))

        assert result == "http://127.0.0.1:1234"
        assert calls == ["launch", "working", "enable", "working"]

    def test_list_docsets_maps_dash_response_metadata(self, monkeypatch):
        async def base_url(ctx):
            return "http://127.0.0.1:1234"

        FakeAsyncClient.response = FakeResponse(
            {
                "docsets": [
                    {
                        "name": "Python",
                        "identifier": "python",
                        "platform": "python",
                        "full_text_search": "enabled",
                        "notice": "ready",
                    },
                    {
                        "name": "Ruby",
                        "identifier": "ruby",
                        "platform": "ruby",
                        "full_text_search": "disabled",
                    },
                ]
            }
        )
        monkeypatch.setattr(server, "working_api_base_url", base_url)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

        result = run(server.list_installed_docsets(DummyContext()))

        assert result.error is None
        assert result.truncated is False
        assert result.returned_count == 2
        assert result.total_count == 2
        assert [docset.identifier for docset in result.docsets] == ["python", "ruby"]
        assert result.docsets[0].notice == "ready"

    def test_list_docsets_reports_truncation(self, monkeypatch):
        async def base_url(ctx):
            return "http://127.0.0.1:1234"

        FakeAsyncClient.response = FakeResponse(
            {
                "docsets": [
                    {
                        "name": "Python",
                        "identifier": "python",
                        "platform": "python",
                        "full_text_search": "enabled",
                    }
                ]
            }
        )
        ctx = DummyContext()
        monkeypatch.setattr(server, "working_api_base_url", base_url)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)
        monkeypatch.setattr(server, "estimate_tokens", lambda obj: 30_000)

        result = run(server.list_installed_docsets(ctx))

        assert result.truncated is True
        assert result.returned_count == 0
        assert result.total_count == 1
        assert ctx.warning_messages

    def test_list_docsets_handles_not_found(self, monkeypatch):
        async def base_url(ctx):
            return "http://127.0.0.1:1234"

        FakeAsyncClient.response = FakeResponse(error=http_error(404, "not found"))
        monkeypatch.setattr(server, "working_api_base_url", base_url)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

        result = run(server.list_installed_docsets(DummyContext()))

        assert "No docsets found" in result.error

    def test_list_docsets_handles_generic_exception(self, monkeypatch):
        async def base_url(ctx):
            return "http://127.0.0.1:1234"

        FakeAsyncClient.exception = RuntimeError("boom")
        monkeypatch.setattr(server, "working_api_base_url", base_url)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

        result = run(server.list_installed_docsets(DummyContext()))

        assert result.error == "Failed to get installed docsets: boom"

    def test_search_accepts_docset_identifier_list(self, monkeypatch):
        async def base_url(ctx):
            return "http://127.0.0.1:1234"

        FakeAsyncClient.requests = []
        FakeAsyncClient.response = FakeResponse(
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
        monkeypatch.setattr(server, "working_api_base_url", base_url)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

        result = run(
            server.search_documentation(
                DummyContext(), "map", ["ruby", " python "], max_results=5
            )
        )

        assert result.error is None
        assert result.warning is None
        assert result.returned_count == 1
        assert result.total_count == 1
        assert FakeAsyncClient.requests[0][1]["docset_identifiers"] == "ruby,python"

    def test_search_warning_is_not_error_and_empty_results_are_filtered(
        self, monkeypatch
    ):
        async def base_url(ctx):
            return "http://127.0.0.1:1234"

        FakeAsyncClient.response = FakeResponse(
            {
                "message": "FTS is indexing",
                "results": [
                    {},
                    {
                        "name": "sort",
                        "type": "Method",
                        "load_url": "http://127.0.0.1:1234/doc.html",
                    },
                ],
            }
        )
        monkeypatch.setattr(server, "working_api_base_url", base_url)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

        result = run(server.search_documentation(DummyContext(), "sort", ["ruby"]))

        assert result.error is None
        assert result.warning == "FTS is indexing"
        assert result.returned_count == 1
        assert result.total_count == 1
        assert result.results[0].name == "sort"

    @pytest.mark.parametrize(
        "kwargs, expected_error",
        [
            (
                {"query": " ", "docset_identifiers": ["python"]},
                "Query cannot be empty",
            ),
            (
                {"query": "map", "docset_identifiers": [" ", ""]},
                "docset_identifiers cannot be empty",
            ),
            (
                {
                    "query": "map",
                    "docset_identifiers": ["python"],
                    "max_results": 0,
                },
                "max_results must be between 1 and 1000",
            ),
            (
                {
                    "query": "map",
                    "docset_identifiers": ["python"],
                    "max_results": 1001,
                },
                "max_results must be between 1 and 1000",
            ),
        ],
    )
    def test_search_validates_inputs(self, kwargs, expected_error):
        result = run(server.search_documentation(DummyContext(), **kwargs))

        assert expected_error in result.error

    def test_search_reports_truncation(self, monkeypatch):
        async def base_url(ctx):
            return "http://127.0.0.1:1234"

        FakeAsyncClient.response = FakeResponse(
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
        ctx = DummyContext()
        monkeypatch.setattr(server, "working_api_base_url", base_url)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)
        monkeypatch.setattr(server, "estimate_tokens", lambda obj: 30_000)

        result = run(server.search_documentation(ctx, "map", ["python"]))

        assert result.truncated is True
        assert result.returned_count == 0
        assert result.total_count == 1
        assert ctx.warning_messages

    @pytest.mark.parametrize(
        "status_code, text, expected_error",
        [
            (
                400,
                "Docset with identifier python not found",
                "Invalid docset identifier",
            ),
            (400, "No docsets found", "No valid docsets found"),
            (
                403,
                "API access blocked due to Dash trial expiration",
                "Your Dash trial has expired",
            ),
            (403, "blocked", "Forbidden: blocked"),
            (500, "server error", "HTTP error"),
        ],
    )
    def test_search_handles_http_errors(
        self, monkeypatch, status_code, text, expected_error
    ):
        async def base_url(ctx):
            return "http://127.0.0.1:1234"

        FakeAsyncClient.response = FakeResponse(error=http_error(status_code, text))
        monkeypatch.setattr(server, "working_api_base_url", base_url)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

        result = run(server.search_documentation(DummyContext(), "map", ["python"]))

        assert expected_error in result.error

    def test_search_handles_unavailable_api(self, monkeypatch):
        async def unavailable(ctx):
            return None

        monkeypatch.setattr(server, "working_api_base_url", unavailable)

        result = run(server.search_documentation(DummyContext(), "map", ["python"]))

        assert result.error == server.DASH_API_UNAVAILABLE_ERROR

    def test_enable_docset_fts_returns_structured_error(self):
        result = run(server.enable_docset_fts(DummyContext(), ""))

        assert result.success is False
        assert result.error == "Docset identifier cannot be empty"

    def test_enable_docset_fts_success(self, monkeypatch):
        async def base_url(ctx):
            return "http://127.0.0.1:1234"

        FakeAsyncClient.response = FakeResponse({"ok": True})
        monkeypatch.setattr(server, "working_api_base_url", base_url)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

        result = run(server.enable_docset_fts(DummyContext(), "python"))

        assert result.success is True
        assert result.identifier == "python"
        assert result.error is None
        assert FakeAsyncClient.requests[0][1] == {"identifier": "python"}

    @pytest.mark.parametrize(
        "status_code, text, expected_error",
        [
            (400, "bad identifier", "Bad request: bad identifier"),
            (404, "not found", "Docset not found: python"),
            (500, "server error", "HTTP error"),
        ],
    )
    def test_enable_docset_fts_handles_http_errors(
        self, monkeypatch, status_code, text, expected_error
    ):
        async def base_url(ctx):
            return "http://127.0.0.1:1234"

        FakeAsyncClient.response = FakeResponse(error=http_error(status_code, text))
        monkeypatch.setattr(server, "working_api_base_url", base_url)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

        result = run(server.enable_docset_fts(DummyContext(), "python"))

        assert result.success is False
        assert expected_error in result.error

    def test_enable_docset_fts_handles_unavailable_api(self, monkeypatch):
        async def unavailable(ctx):
            return None

        monkeypatch.setattr(server, "working_api_base_url", unavailable)

        result = run(server.enable_docset_fts(DummyContext(), "python"))

        assert result.success is False
        assert result.error == server.DASH_API_UNAVAILABLE_ERROR

    def test_enable_docset_fts_handles_generic_exception(self, monkeypatch):
        async def base_url(ctx):
            return "http://127.0.0.1:1234"

        FakeAsyncClient.exception = RuntimeError("boom")
        monkeypatch.setattr(server, "working_api_base_url", base_url)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

        result = run(server.enable_docset_fts(DummyContext(), "python"))

        assert result.success is False
        assert result.error == "Failed to enable FTS: boom"

    def test_setup_tool_returns_base_url_when_ready(self, monkeypatch):
        async def ready(ctx, launch, enable_api_server):
            assert launch is False
            assert enable_api_server is False
            return "http://127.0.0.1:1234"

        monkeypatch.setattr(server, "ensure_dash_ready", ready)

        result = run(
            server.ensure_dash_api_ready(
                DummyContext(), launch_dash_app=False, enable_api_server=False
            )
        )

        assert result.success is True
        assert result.base_url == "http://127.0.0.1:1234"

    def test_load_documentation_page_success(self, monkeypatch):
        async def port(ctx):
            return 1234

        FakeAsyncClient.response = FakeResponse(
            text="""
            <html><body>
              <nav>Noise</nav>
              <div id="target"><h2>Title</h2><p>Useful content.</p></div>
              <div id="other"><p>Other content.</p></div>
            </body></html>
            """
        )
        monkeypatch.setattr(server, "get_dash_api_port", port)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

        result = run(
            server.load_documentation_page(
                DummyContext(), "http://127.0.0.1:1234/doc.html#target"
            )
        )

        assert result.error is None
        assert "Title" in result.content
        assert "Useful content" in result.content
        assert "Other content" not in result.content

    def test_load_documentation_page_rejects_invalid_url_before_fetch(
        self, monkeypatch
    ):
        async def port(ctx):
            return 1234

        monkeypatch.setattr(server, "get_dash_api_port", port)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

        result = run(
            server.load_documentation_page(
                DummyContext(), "http://example.com:1234/doc.html"
            )
        )

        assert "local Dash API" in result.error
        assert FakeAsyncClient.requests == []

    def test_load_documentation_page_handles_unavailable_api(self, monkeypatch):
        async def no_port(ctx):
            return None

        monkeypatch.setattr(server, "get_dash_api_port", no_port)

        result = run(
            server.load_documentation_page(
                DummyContext(), "http://127.0.0.1:1234/doc.html"
            )
        )

        assert result.error == server.DASH_API_UNAVAILABLE_ERROR

    @pytest.mark.parametrize(
        "status_code, text, expected_error",
        [
            (
                403,
                "API access blocked due to Dash trial expiration",
                "Your Dash trial has expired",
            ),
            (403, "blocked", "Forbidden: blocked"),
            (404, "not found", "Documentation page not found"),
            (500, "server error", "HTTP error"),
        ],
    )
    def test_load_documentation_page_handles_http_errors(
        self, monkeypatch, status_code, text, expected_error
    ):
        async def port(ctx):
            return 1234

        FakeAsyncClient.response = FakeResponse(error=http_error(status_code, text))
        monkeypatch.setattr(server, "get_dash_api_port", port)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

        result = run(
            server.load_documentation_page(
                DummyContext(), "http://127.0.0.1:1234/doc.html"
            )
        )

        assert expected_error in result.error

    def test_load_documentation_page_handles_generic_exception(self, monkeypatch):
        async def port(ctx):
            return 1234

        FakeAsyncClient.exception = RuntimeError("boom")
        monkeypatch.setattr(server, "get_dash_api_port", port)
        monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

        result = run(
            server.load_documentation_page(
                DummyContext(), "http://127.0.0.1:1234/doc.html"
            )
        )

        assert result.error == "Failed to load documentation page: boom"
