import asyncio

from dash_mcp_server import server
from dash_mcp_server.server import parse_fragment, extract_section


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
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        return FakeResponse(self._payload)


def a_result(name):
    return {
        "name": name,
        "type": "Function",
        "load_url": f"http://127.0.0.1:1234/{name}",
        "docset": "Python 3",
    }


class TestSearchDiagnostics:
    def _search(self, monkeypatch, payload, **kwargs):
        async def fake_base_url(ctx):
            return "http://127.0.0.1:1234"

        monkeypatch.setattr(server, "working_api_base_url", fake_base_url)
        monkeypatch.setattr(server.httpx, "Client", lambda *a, **kw: FakeClient(payload))
        ctx = FakeContext()
        kwargs.setdefault("query", "buffer")
        kwargs.setdefault("docset_identifiers", "python")
        kwargs.setdefault("search_snippets", False)
        results = asyncio.run(server.search_documentation(ctx, **kwargs))
        return results, ctx

    def test_a_partial_search_reports_a_warning_not_an_error(self, monkeypatch):
        payload = {
            "message": "Some docsets were busy indexing and were not searched. Results may be incomplete.",
            "results": [a_result("open"), a_result("read")],
        }
        results, ctx = self._search(monkeypatch, payload)

        assert len(results.results) == 2
        assert results.error is None
        assert any("busy indexing" in w for w in results.warning)
        assert any(kind == "warning" for kind, _ in ctx.messages)

    def test_a_clean_search_reports_neither(self, monkeypatch):
        results, _ = self._search(monkeypatch, {"results": [a_result("open")]})
        assert results.error is None
        assert results.warning is None

    def test_every_validation_problem_is_reported_at_once(self, monkeypatch):
        results, _ = self._search(
            monkeypatch, {"results": []}, query="  ", docset_identifiers="  ",
            max_results=5000,
        )
        assert results.results == []
        assert len(results.error) == 3
        assert any("Query" in e for e in results.error)
        assert any("docset_identifiers" in e for e in results.error)
        assert any("max_results" in e for e in results.error)

    def test_a_single_validation_problem_yields_a_single_error(self, monkeypatch):
        results, _ = self._search(monkeypatch, {"results": []}, max_results=0)
        assert len(results.error) == 1
        assert "max_results" in results.error[0]

    def test_an_empty_result_is_not_an_error(self, monkeypatch):
        # The search ran and found nothing; that is an outcome, not a failure.
        results, _ = self._search(monkeypatch, {"results": []}, query="two words")
        assert results.error is None
        assert results.results == []
        assert any("Nothing found" in w for w in results.warning)

    def test_an_empty_result_keeps_the_reason_dash_gave_for_it(self, monkeypatch):
        # The indexing notice explains the empty result; advice to shorten the query does
        # not, and must not replace it.
        payload = {
            "message": "Some docsets were busy indexing and were not searched. Results may be incomplete.",
            "results": [],
        }
        results, _ = self._search(monkeypatch, payload, query="two words")
        assert results.error is None
        assert any("busy indexing" in w for w in results.warning)

    def test_truncation_is_visible_in_the_response_not_only_in_the_log(self, monkeypatch):
        payload = {"results": [a_result(f"symbol_{i}" * 40) for i in range(400)]}
        results, _ = self._search(monkeypatch, payload)
        assert 0 < len(results.results) < 400
        assert any("truncated" in w.lower() or "of 400" in w for w in results.warning)

    def test_both_kinds_of_warning_survive_together(self, monkeypatch):
        payload = {
            "message": "Some docsets were busy indexing and were not searched. Results may be incomplete.",
            "results": [a_result(f"symbol_{i}" * 40) for i in range(400)],
        }
        results, _ = self._search(monkeypatch, payload)
        assert len(results.warning) == 2
        assert any("busy indexing" in w for w in results.warning)
        assert any("400" in w for w in results.warning)

    def test_a_real_failure_reports_an_error_and_no_results(self, monkeypatch):
        async def fake_base_url(ctx):
            return None

        monkeypatch.setattr(server, "working_api_base_url", fake_base_url)
        ctx = FakeContext()
        results = asyncio.run(
            server.search_documentation(ctx, "buffer", "python", search_snippets=False)
        )
        assert results.results == []
        assert len(results.error) == 1
        assert "Dash API Server" in results.error[0]
