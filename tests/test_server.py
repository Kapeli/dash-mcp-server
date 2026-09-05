import asyncio

from dash_mcp_server import server
from dash_mcp_server.server import (
    extract_section,
    filter_docsets,
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


DOCSETS = [
    {"name": "Python 3.14.6", "identifier": "aaaa1111", "platform": "python",
     "full_text_search": "enabled"},
    {"name": "IPython 7.14.0", "identifier": "bbbb2222", "platform": "ipython",
     "full_text_search": "enabled"},
    {"name": "Pythonista 3.1", "identifier": "cccc3333", "platform": "pythonista",
     "full_text_search": "not supported"},
    {"name": "PostgreSQL 18.4", "identifier": "dddd4444", "platform": "psql",
     "full_text_search": "enabled"},
    {"name": "SQLAlchemy 2.0.51", "identifier": "eeee5555", "platform": "sqlalchemy",
     "full_text_search": "enabled"},
    {"name": "Racket 9.2", "identifier": "ffff6666", "platform": "racket",
     "full_text_search": "enabled"},
    {"name": "AppCode Global: 1.1.1", "identifier": "gggg7777", "platform": "cheatsheet",
     "full_text_search": "not supported"},
    {"name": "Crontab Global: 1.1.1", "identifier": "hhhh8888", "platform": "cheatsheet",
     "full_text_search": "not supported"},
]


def names(rows):
    return [row["name"] for row in rows]


class TestFilterDocsets:
    def test_no_filter_returns_everything_and_suggests_nothing(self):
        matches, suggestions = filter_docsets(DOCSETS, "")
        assert len(matches) == len(DOCSETS)
        assert suggestions == []

    def test_substring_matches_every_docset_containing_the_term(self):
        matches, suggestions = filter_docsets(DOCSETS, "python")
        # Ranked, not in Dash's order: an exact name first, then names starting with the
        # term, then names merely containing it.
        assert names(matches) == ["Python 3.14.6", "Pythonista 3.1", "IPython 7.14.0"]
        assert suggestions == []

    def test_an_exact_name_match_is_ranked_first(self):
        matches, _ = filter_docsets(DOCSETS, "Python 3.14.6")
        assert matches[0]["name"] == "Python 3.14.6"

    def test_matching_is_case_insensitive_but_keeps_the_original_casing(self):
        matches, _ = filter_docsets(DOCSETS, "postgresql")
        assert names(matches) == ["PostgreSQL 18.4"]

    def test_a_platform_only_term_still_finds_the_docset(self):
        # 'psql' appears nowhere in the display name.
        matches, _ = filter_docsets(DOCSETS, "psql")
        assert names(matches) == ["PostgreSQL 18.4"]

    def test_an_identifier_is_matched_too(self):
        matches, _ = filter_docsets(DOCSETS, "dddd4444")
        assert names(matches) == ["PostgreSQL 18.4"]

    def test_a_shared_platform_returns_all_of_its_docsets(self):
        matches, _ = filter_docsets(DOCSETS, "cheatsheet")
        assert len(matches) == 2

    def test_a_term_with_no_match_suggests_near_misses_instead(self):
        matches, suggestions = filter_docsets(DOCSETS, "react")
        assert matches == []
        assert names(suggestions) == ["Racket 9.2"]

    def test_a_misspelling_is_recovered_as_a_suggestion(self):
        matches, suggestions = filter_docsets(DOCSETS, "sqlachemy")
        assert matches == []
        assert "SQLAlchemy 2.0.51" in names(suggestions)

    def test_a_term_that_resembles_nothing_suggests_nothing(self):
        matches, suggestions = filter_docsets(DOCSETS, "zzzzqqqqwwww")
        assert matches == []
        assert suggestions == []

    def test_suggestions_never_accompany_real_matches(self):
        for term in ("python", "psql", "cheatsheet", ""):
            matches, suggestions = filter_docsets(DOCSETS, term)
            assert matches, term
            assert suggestions == [], term


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


class TestListInstalledDocsetsFilter:
    def _list(self, monkeypatch, **kwargs):
        async def fake_base_url(ctx):
            return "http://127.0.0.1:1234"

        monkeypatch.setattr(server, "working_api_base_url", fake_base_url)
        monkeypatch.setattr(
            server.httpx, "Client", lambda *a, **kw: FakeClient({"docsets": DOCSETS})
        )
        ctx = FakeContext()
        return asyncio.run(server.list_installed_docsets(ctx, **kwargs)), ctx

    def test_without_a_filter_it_behaves_exactly_as_before(self, monkeypatch):
        results, _ = self._list(monkeypatch)
        assert len(results.docsets) == len(DOCSETS)
        assert results.suggestions == []
        assert results.error is None

    def test_a_filter_returns_only_the_matches(self, monkeypatch):
        results, _ = self._list(monkeypatch, filter="python")
        assert [d.name for d in results.docsets] == [
            "Python 3.14.6",
            "Pythonista 3.1",
            "IPython 7.14.0",
        ]
        assert results.suggestions == []

    def test_an_unmatched_filter_returns_suggestions_and_warns(self, monkeypatch):
        results, ctx = self._list(monkeypatch, filter="react")
        assert results.docsets == []
        assert [d.name for d in results.suggestions] == ["Racket 9.2"]
        assert results.error is None
        assert any(kind == "warning" for kind, _ in ctx.messages)

    def test_a_suggestion_carries_the_identifier_so_no_second_call_is_needed(
        self, monkeypatch
    ):
        results, _ = self._list(monkeypatch, filter="sqlachemy")
        assert results.suggestions[0].identifier == "eeee5555"
