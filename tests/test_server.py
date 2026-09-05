import json

from dash_mcp_server.server import (
    SearchResult,
    estimate_tokens,
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
