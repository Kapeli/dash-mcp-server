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


class TestExtractSectionHeadings:
    def test_heading_section_stops_at_same_rank_heading(self):
        html = """
        <html><body>
          <h2 id="fastapi.Depends">fastapi.Depends</h2>
          <p>First paragraph about Depends.</p>
          <table><tr><td>Signature table</td></tr></table>
          <pre><code>def Depends()</code></pre>
          <h2 id="fastapi.Security">fastapi.Security</h2>
          <p>Security section content.</p>
        </body></html>
        """
        result = extract_section(html, "fastapi.Depends")
        assert "<h2" in result and "fastapi.Depends" in result
        assert "First paragraph about Depends" in result
        assert "Signature table" in result
        assert "def Depends()" in result
        assert "fastapi.Security" not in result
        assert "Security section content" not in result

    def test_heading_section_stops_at_higher_rank_heading(self):
        html = """
        <html><body>
          <h2 id="section">Section</h2>
          <p>Section intro.</p>
          <h3 id="subsection">Subsection</h3>
          <p>Subsection body that belongs to the section.</p>
          <h1 id="top-level">Next top-level part</h1>
          <p>Top-level content.</p>
        </body></html>
        """
        result = extract_section(html, "section")
        assert "Section intro" in result
        # Lower-rank headings (h3) and their content are included
        assert "Subsection" in result
        assert "Subsection body that belongs to the section" in result
        # Higher-rank heading terminates the section
        assert "Next top-level part" not in result
        assert "Top-level content" not in result

    def test_heading_section_runs_to_end_of_container(self):
        html = """
        <html><body>
          <div class="main">
            <h2 id="last-heading">Last heading</h2>
            <p>Only paragraph.</p>
            <div><p>Nested trailing block.</p></div>
          </div>
          <div class="other">
            <h2 id="other-heading">Other heading</h2>
            <p>Other content.</p>
          </div>
        </body></html>
        """
        result = extract_section(html, "last-heading")
        assert "<h2" in result and "Last heading" in result
        assert "Only paragraph" in result
        assert "Nested trailing block" in result
        # Section ends at the parent container boundary
        assert "Other heading" not in result
        assert "Other content" not in result
