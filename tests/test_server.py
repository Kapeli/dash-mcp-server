import asyncio
import json

from dash_mcp_server import server
from dash_mcp_server.server import (
    SearchResult,
    estimate_tokens,
    estimate_text_tokens,
    take_token_budget,
    extract_section,
    fit_within_token_limit,
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


class TestEstimateTokens:
    # A deterministic stand-in for a real search response: 30 records shaped exactly as the
    # Dash API returns them -- six short fields, long percent-encoded URLs, identifier-style
    # names. Its true token count was measured once with each tokenizer the estimator is
    # calibrated against, and those numbers are recorded here so a change to the
    # coefficients cannot silently drift away from reality.
    FIXTURE_MEASURED = {
        "GPT-4o": 3020,
        "GPT-4": 2957,
        "Qwen3": 3137,
        "DeepSeek-V3": 3106,
        "Kimi-K2": 2903,
        "GLM-4.5": 2957,
    }

    @staticmethod
    def _fixture():
        methods = [
            "merge", "groupby", "pivot_table", "drop_duplicates", "sort_values", "fillna",
            "to_parquet", "reset_index", "set_index", "apply", "astype", "query", "rename",
            "isna", "nunique", "memory_usage", "select_dtypes", "interpolate", "clip",
            "rank", "corr", "cov", "describe", "duplicated", "explode", "melt", "nlargest",
            "pct_change", "reindex", "sample",
        ]
        return [
            SearchResult(
                name=f"pandas.DataFrame.{m}",
                type="Method",
                platform="pandas",
                load_url=(
                    "http://127.0.0.1:12345/load?url=https%3A%2F%2Fpandas.pydata.org"
                    f"%2Fdocs%2Freference%2Fapi%2Fpandas.DataFrame.{m}.html"
                    f"%23pandas.DataFrame.{m}"
                ),
                docset="pandas",
                description=None,
                language=None,
                tags=None,
            )
            for m in methods
        ]

    def test_estimate_never_falls_below_a_measured_token_count(self):
        # The cap this feeds exists to protect the caller's context window, so the estimate
        # must not come in under the truth for any tokenizer it claims to cover.
        estimate = estimate_tokens(self._fixture())
        for model, measured in self.FIXTURE_MEASURED.items():
            assert estimate >= measured, f"under-estimates {model}: {estimate} < {measured}"

    def test_estimate_stays_close_to_the_measured_token_count(self):
        # Over-estimating truncates earlier than necessary, so the margin is bounded too.
        estimate = estimate_tokens(self._fixture())
        worst = max(estimate / measured for measured in self.FIXTURE_MEASURED.values())
        assert worst <= 1.30, f"over-estimates by {worst:.0%}, above the 30% bound"

    def test_estimate_beats_the_four_characters_per_token_heuristic(self):
        # The regression this replaces: 4 chars/token reported ~30% low on payloads made of
        # many small records, so the documented 25,000-token cap never came into effect.
        fixture = self._fixture()
        serialized = json.dumps([r.model_dump() for r in fixture])
        naive = len(serialized) // 4
        assert naive < min(self.FIXTURE_MEASURED.values())  # the old heuristic under-reports
        assert estimate_tokens(fixture) > naive

    def test_estimate_counts_json_structure_not_just_field_lengths(self):
        result = SearchResult(
            name="fastapi.Depends",
            type="Function",
            platform=None,
            load_url="https://fastapi.tiangolo.com/reference/dependencies/index.html#fastapi.Depends",
            docset="FastAPI",
            description=None,
            language=None,
            tags=None,
        )
        # Summing field lengths alone misses the quotes, braces, colons, commas and the
        # `null` written for every unset optional field. The estimate is taken over the
        # serialization, so it must exceed the sum of the values it contains.
        field_lengths = sum(
            len(v) for v in result.model_dump().values() if isinstance(v, str)
        )
        assert estimate_tokens(result) > field_lengths // 4

    def test_estimate_scales_with_a_list_of_records(self):
        results = [
            SearchResult(
                name=f"symbol_{i}",
                type="Function",
                platform=None,
                load_url=f"https://example.com/docs/index.html#symbol_{i}",
                docset="Example",
                description=None,
                language=None,
                tags=None,
            )
            for i in range(50)
        ]
        one = estimate_tokens(results[0])
        many = estimate_tokens(results)
        assert many >= 50 * one * 0.9

    def test_estimate_handles_plain_types(self):
        # An empty value still serializes to the two characters `""`, and the estimate is
        # per-character, so it lands at 2 rather than 1. The property that matters is that
        # it never comes in under the truth; a token or two on an empty payload is noise.
        assert estimate_tokens("") <= 2
        # Measured at 52 tokens by all six calibration tokenizers. The old assertion
        # here was `>= 100`, which is 402/4 -- it encoded the heuristic being replaced,
        # not the truth. A long letter run must still be estimated at or above its real
        # cost, which is what this now checks.
        assert estimate_tokens("a" * 400) >= 52
        assert estimate_tokens({"k": "v"}) >= 1
        assert estimate_tokens(None) == 1

    def test_estimate_survives_a_non_serializable_value(self):
        assert estimate_tokens({"when": object()}) >= 1


def search_results(count):
    return [
        SearchResult(
            name=f"example_symbol_{i}",
            type="Function",
            platform="python",
            load_url=f"http://127.0.0.1:1234/page{i}",
            docset="Python",
            description=f"Documentation for example_symbol_{i}.",
        )
        for i in range(count)
    ]


class TestFitWithinTokenLimit:
    def test_a_list_that_fits_is_returned_whole(self):
        rows = search_results(3)
        assert fit_within_token_limit(rows, 25000) == rows

    def test_an_empty_list_stays_empty(self):
        assert fit_within_token_limit([], 25000) == []

    def test_the_kept_list_is_measured_as_a_list_not_as_a_sum(self):
        """The commas and brackets between records belong to the budget too.

        A running per-item total misses them, which is how a response of a few
        hundred small records lands over the limit it was trimmed to.
        """
        rows = search_results(400)
        kept = fit_within_token_limit(rows, 25000)

        assert 0 < len(kept) < len(rows)
        assert estimate_tokens(kept) <= 25000
        assert sum(estimate_tokens(row) for row in rows[: len(kept)]) < estimate_tokens(
            kept
        )

    def test_one_more_record_would_not_have_fitted(self):
        rows = search_results(400)
        kept = fit_within_token_limit(rows, 25000)
        assert estimate_tokens(rows[: len(kept) + 1]) > 25000

    def test_a_limit_below_the_first_record_keeps_nothing(self):
        assert fit_within_token_limit(search_results(10), 1) == []

class TestEstimateTextTokens:
    def test_prose_estimate_exceeds_json_estimate_for_the_same_text(self):
        # Page text is mostly letters and spaces, which the JSON calibration
        # under-counts; the text profile must not inherit that under-count.
        text = "The quick brown fox jumps over the lazy dog. " * 200
        assert estimate_text_tokens(text) > 0
        assert estimate_text_tokens(text) >= len(text) // 8

    def test_estimate_grows_with_length(self):
        one = estimate_text_tokens("Documentation paragraph about sockets. ")
        many = estimate_text_tokens("Documentation paragraph about sockets. " * 50)
        assert many > one * 40

    def test_estimate_of_empty_text_is_zero(self):
        assert estimate_text_tokens("") == 0


class TestTakeTokenBudget:
    def test_text_within_budget_is_returned_whole(self):
        text = "line one\nline two\nline three\n"
        chunk, next_offset = take_token_budget(text, 0, 25000)
        assert chunk == text
        assert next_offset is None

    def test_text_over_budget_is_cut_and_reports_where_to_resume(self):
        text = ("word " * 20 + "\n") * 500
        chunk, next_offset = take_token_budget(text, 0, 200)
        assert 0 < len(chunk) < len(text)
        assert estimate_text_tokens(chunk) <= 200
        assert next_offset == len(chunk)

    def test_resuming_at_next_offset_reassembles_the_whole_text(self):
        text = ("word " * 20 + "\n") * 500
        parts = []
        offset = 0
        while offset is not None:
            chunk, offset = take_token_budget(text, offset, 200)
            assert chunk
            parts.append(chunk)
        assert "".join(parts) == text

    def test_cut_falls_on_a_line_boundary_when_there_is_one(self):
        text = ("word " * 20 + "\n") * 500
        chunk, _ = take_token_budget(text, 0, 200)
        assert chunk.endswith("\n")

    def test_cut_still_happens_when_a_single_line_exceeds_the_budget(self):
        text = "x" * 5000
        chunk, next_offset = take_token_budget(text, 0, 50)
        assert 0 < len(chunk) < len(text)
        assert next_offset == len(chunk)

    def test_offset_past_the_end_returns_nothing_more(self):
        text = "short page\n"
        chunk, next_offset = take_token_budget(text, len(text), 25000)
        assert chunk == ""
        assert next_offset is None

    def test_budget_too_small_for_any_content_still_makes_progress(self):
        text = "abcdefghij" * 100
        chunk, next_offset = take_token_budget(text, 0, 1)
        assert len(chunk) >= 1
        assert next_offset == len(chunk)


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
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self, text):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        return FakeResponse(self._text)


def page_html(paragraphs):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f"<html><body><article>{body}</article></body></html>"


class TestLoadDocumentationPageSize:
    def _run(self, monkeypatch, html, **kwargs):
        monkeypatch.setattr(
            server.httpx, "Client", lambda *a, **kw: FakeClient(html)
        )
        ctx = FakeContext()
        page = asyncio.run(
            server.load_documentation_page(ctx, "http://127.0.0.1:1234/page", **kwargs)
        )
        return page, ctx

    def test_small_page_comes_back_whole(self, monkeypatch):
        page, _ = self._run(monkeypatch, page_html(["A short documentation page."]))
        assert "short documentation page" in page.content
        assert page.truncated is False
        assert page.next_offset is None
        assert page.total_characters == len(page.content)

    def test_large_page_is_truncated_and_says_where_to_resume(self, monkeypatch):
        paragraphs = [f"Paragraph {i} about sockets and buffers." for i in range(4000)]
        page, ctx = self._run(monkeypatch, page_html(paragraphs), max_tokens=25000)
        assert page.truncated is True
        assert page.next_offset == len(page.content)
        assert page.total_characters > len(page.content)
        assert estimate_text_tokens(page.content) <= 25000
        assert any(kind == "warning" for kind, _ in ctx.messages)

    def test_reading_a_page_in_pieces_returns_all_of_it(self, monkeypatch):
        paragraphs = [f"Paragraph {i} about sockets and buffers." for i in range(4000)]
        html = page_html(paragraphs)
        collected = ""
        offset = 0
        while True:
            page, _ = self._run(monkeypatch, html, offset=offset, max_tokens=5000)
            collected += page.content
            if page.next_offset is None:
                break
            offset = page.next_offset
        assert len(collected) == page.total_characters
        assert "Paragraph 3999" in collected

    def test_respects_a_smaller_budget(self, monkeypatch):
        paragraphs = [f"Paragraph {i} about sockets and buffers." for i in range(4000)]
        page, _ = self._run(monkeypatch, page_html(paragraphs), max_tokens=500)
        assert estimate_text_tokens(page.content) <= 500
        assert page.truncated is True

    def test_rejects_a_negative_offset(self, monkeypatch):
        page, ctx = self._run(monkeypatch, page_html(["Text."]), offset=-1)
        assert page.content == ""
        assert "offset" in page.error
        assert any(kind == "error" for kind, _ in ctx.messages)

    def test_a_negative_budget_is_refused(self, monkeypatch):
        page, _ = self._run(monkeypatch, page_html(["Text."]), max_tokens=-1)
        assert page.content == ""
        assert "max_tokens" in page.error

    def test_zero_means_no_limit(self, monkeypatch):
        paragraphs = [f"Paragraph {i} about sockets and buffers." for i in range(4000)]
        page, _ = self._run(monkeypatch, page_html(paragraphs), max_tokens=0)
        assert page.truncated is False
        assert page.next_offset is None
        assert len(page.content) == page.total_characters

    def test_without_a_budget_the_environment_decides(self, monkeypatch):
        paragraphs = [f"Paragraph {i} about sockets and buffers." for i in range(4000)]
        monkeypatch.setenv("DASH_RETRIEVAL_TOKEN_LIMIT", "5000")
        page, _ = self._run(monkeypatch, page_html(paragraphs))
        assert page.truncated is True
        assert estimate_text_tokens(page.content) <= 5000

    def test_with_no_environment_variable_the_whole_page_is_returned(
        self, monkeypatch
    ):
        paragraphs = [f"Paragraph {i} about sockets and buffers." for i in range(4000)]
        monkeypatch.delenv("DASH_RETRIEVAL_TOKEN_LIMIT", raising=False)
        page, _ = self._run(monkeypatch, page_html(paragraphs))
        assert page.truncated is False
        assert len(page.content) == page.total_characters

    def test_an_explicit_budget_overrides_the_environment(self, monkeypatch):
        paragraphs = [f"Paragraph {i} about sockets and buffers." for i in range(4000)]
        monkeypatch.setenv("DASH_RETRIEVAL_TOKEN_LIMIT", "500")
        page, _ = self._run(monkeypatch, page_html(paragraphs), max_tokens=5000)
        assert estimate_text_tokens(page.content) > 500


class TestTokenLimitFromEnvironment:
    def test_absent_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.delenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, raising=False)
        assert server.token_limit(server.RESPONSE_TOKEN_LIMIT_VARIABLE, 25000) == 25000

    def test_blank_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, "   ")
        assert server.token_limit(server.RESPONSE_TOKEN_LIMIT_VARIABLE, 25000) == 25000

    def test_unparseable_falls_back_rather_than_failing(self, monkeypatch):
        monkeypatch.setenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, "lots")
        assert server.token_limit(server.RESPONSE_TOKEN_LIMIT_VARIABLE, 25000) == 25000

    def test_zero_is_honoured_as_no_limit(self, monkeypatch):
        monkeypatch.setenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, "0")
        assert server.token_limit(server.RESPONSE_TOKEN_LIMIT_VARIABLE, 25000) == 0

    def test_a_negative_value_reads_as_no_limit(self, monkeypatch):
        monkeypatch.setenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, "-5")
        assert server.token_limit(server.RESPONSE_TOKEN_LIMIT_VARIABLE, 25000) == 0

    def test_a_set_value_wins(self, monkeypatch):
        monkeypatch.setenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, "1200")
        assert server.token_limit(server.RESPONSE_TOKEN_LIMIT_VARIABLE, 25000) == 1200


class FakeJsonResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeJsonClient:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        return FakeJsonResponse(self._payload)


def many_docsets(count):
    return {
        "docsets": [
            {
                "name": f"Example Docset {i} 1.0.0",
                "identifier": f"docset{i:04d}",
                "platform": f"example{i}",
                "full_text_search": "enabled",
                "notice": None,
            }
            for i in range(count)
        ]
    }


def many_results(count):
    return {
        "results": [
            {
                "name": f"example_symbol_{i}",
                "type": "Function",
                "platform": "python",
                "load_url": f"http://127.0.0.1:1234/page{i}",
                "docset": "Python",
                "description": f"Documentation for example_symbol_{i}.",
            }
            for i in range(count)
        ]
    }


class ResponseLimitHarness:
    """Runs a tool against a canned Dash payload, with the API reachable."""

    def _call(self, monkeypatch, payload, tool):
        async def fake_base_url(ctx):
            return "http://127.0.0.1:1234"

        monkeypatch.setattr(server, "working_api_base_url", fake_base_url)
        monkeypatch.setattr(
            server.httpx, "Client", lambda *a, **kw: FakeJsonClient(payload)
        )
        ctx = FakeContext()
        return asyncio.run(tool(ctx)), ctx


class TestDocsetListingRespectsTheResponseLimit(ResponseLimitHarness):
    def _run(self, monkeypatch, count):
        return self._call(
            monkeypatch, many_docsets(count), server.list_installed_docsets
        )

    def test_a_low_limit_truncates_and_names_the_variable(self, monkeypatch):
        monkeypatch.setenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, "300")
        result, ctx = self._run(monkeypatch, 200)
        assert 0 < len(result.docsets) < 200
        assert any(
            kind == "warning" and server.RESPONSE_TOKEN_LIMIT_VARIABLE in message
            for kind, message in ctx.messages
        )

    def test_zero_returns_every_docset(self, monkeypatch):
        monkeypatch.setenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, "0")
        result, _ = self._run(monkeypatch, 2000)
        assert len(result.docsets) == 2000

    def test_without_the_variable_the_default_applies(self, monkeypatch):
        monkeypatch.delenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, raising=False)
        result, ctx = self._run(monkeypatch, 4000)
        assert 0 < len(result.docsets) < 4000
        assert estimate_tokens(result.docsets) <= server.DEFAULT_RESPONSE_TOKEN_LIMIT
        assert any(
            kind == "warning" and str(server.DEFAULT_RESPONSE_TOKEN_LIMIT) in message
            for kind, message in ctx.messages
        )


class TestSearchRespectsTheResponseLimit(ResponseLimitHarness):
    def _run(self, monkeypatch, count):
        def tool(ctx):
            return server.search_documentation(
                ctx, query="example", docset_identifiers="docset0000"
            )

        return self._call(monkeypatch, many_results(count), tool)

    def test_a_low_limit_truncates_and_names_the_variable(self, monkeypatch):
        monkeypatch.setenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, "300")
        result, ctx = self._run(monkeypatch, 200)
        assert 0 < len(result.results) < 200
        assert any(
            kind == "warning" and server.RESPONSE_TOKEN_LIMIT_VARIABLE in message
            for kind, message in ctx.messages
        )

    def test_zero_returns_every_result(self, monkeypatch):
        monkeypatch.setenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, "0")
        result, _ = self._run(monkeypatch, 2000)
        assert len(result.results) == 2000

    def test_without_the_variable_the_default_applies(self, monkeypatch):
        monkeypatch.delenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, raising=False)
        result, ctx = self._run(monkeypatch, 4000)
        assert 0 < len(result.results) < 4000
        assert estimate_tokens(result.results) <= server.DEFAULT_RESPONSE_TOKEN_LIMIT
        assert any(
            kind == "warning" and str(server.DEFAULT_RESPONSE_TOKEN_LIMIT) in message
            for kind, message in ctx.messages
        )

    def test_the_retrieval_variable_does_not_affect_search(self, monkeypatch):
        monkeypatch.setenv(server.RETRIEVAL_TOKEN_LIMIT_VARIABLE, "10")
        monkeypatch.delenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, raising=False)
        result, _ = self._run(monkeypatch, 100)
        assert len(result.results) == 100


class TestToolDescriptionsFollowTheConfiguration:
    """A description naming a number the operator did not choose misleads the model."""

    def test_the_default_limit_is_stated_with_its_variable(self, monkeypatch):
        monkeypatch.delenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, raising=False)
        note = server.response_limit_note()
        assert "25,000" in note
        assert server.RESPONSE_TOKEN_LIMIT_VARIABLE in note

    def test_a_configured_limit_replaces_the_default(self, monkeypatch):
        monkeypatch.setenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, "4000")
        note = server.response_limit_note()
        assert "4,000" in note
        assert "25,000" not in note

    def test_no_limit_means_no_truncation_sentence(self, monkeypatch):
        monkeypatch.setenv(server.RESPONSE_TOKEN_LIMIT_VARIABLE, "0")
        assert server.response_limit_note() == ""

    def test_the_page_loader_says_so_when_nothing_is_capped(self, monkeypatch):
        monkeypatch.delenv(server.RETRIEVAL_TOKEN_LIMIT_VARIABLE, raising=False)
        note = server.retrieval_limit_note()
        assert "whole page" in note
        assert "truncated at" not in note

    def test_the_page_loader_states_a_configured_limit(self, monkeypatch):
        monkeypatch.setenv(server.RETRIEVAL_TOKEN_LIMIT_VARIABLE, "8000")
        note = server.retrieval_limit_note()
        assert "8,000" in note
        assert server.RETRIEVAL_TOKEN_LIMIT_VARIABLE in note

    def test_the_registered_descriptions_carry_the_note(self):
        """What the model actually receives, read back off the server's own registry.

        Registration happens at import, so this pins the wiring rather than the value:
        the note must reach the description the client is handed, not just exist.
        """
        registered = {
            tool.name: tool.description
            for tool in asyncio.run(server.mcp.list_tools())
        }

        for name in ("list_installed_docsets", "search_documentation"):
            assert server.RESPONSE_TOKEN_LIMIT_VARIABLE in registered[name]
            assert "25,000 tokens" in registered[name]

        page = registered["load_documentation_page"]
        assert "whole page is returned" in page
        assert "25,000" not in page
