# BeautifulSoup Section Extraction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `load_documentation_page` return only the relevant section when a URL fragment is present, and strip navigation when it isn't, dramatically reducing token usage.

**Architecture:** Add two pure helper functions (`parse_fragment`, `extract_section`) that sit between the HTTP fetch and `html_to_text()`. BeautifulSoup parses the raw HTML, extracts the relevant DOM subtree (or strips nav elements), and the resulting HTML string is passed to the existing `html_to_text()` unchanged.

**Tech Stack:** `beautifulsoup4` (new), `html.parser` (stdlib), `pytest` (new dev dep), existing `html2text`

---

### Task 1: Add dependencies and test infrastructure

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/__init__.py`
- Create: `tests/test_server.py`

**Step 1: Add beautifulsoup4 and pytest**

```bash
uv add beautifulsoup4
uv add --dev pytest
```

Expected output: both packages installed, `uv.lock` updated.

**Step 2: Verify pytest works**

```bash
uv run pytest --collect-only
```

Expected: `no tests ran` (no errors).

**Step 3: Create empty test file**

Create `tests/__init__.py` (empty file).

Create `tests/test_server.py`:

```python
# tests/test_server.py
from dash_mcp_server.server import parse_fragment, extract_section
```

**Step 4: Run to confirm import fails**

```bash
uv run pytest tests/test_server.py -v
```

Expected: `ImportError: cannot import name 'parse_fragment'`

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/
git commit -m "Add beautifulsoup4 and pytest dependencies"
```

---

### Task 2: Implement and test `parse_fragment`

**Files:**
- Modify: `src/dash_mcp_server/server.py` (add function after imports)
- Modify: `tests/test_server.py`

**Step 1: Write failing tests**

Replace `tests/test_server.py` with:

```python
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
```

**Step 2: Run to verify tests fail**

```bash
uv run pytest tests/test_server.py::TestParseFragment -v
```

Expected: `ImportError: cannot import name 'parse_fragment'`

**Step 3: Implement `parse_fragment` in server.py**

Add after the existing imports (after `from pydantic import BaseModel, Field`):

```python
from urllib.parse import urlparse, unquote
```

Add this function after the `html_to_text` function (around line 232):

```python
def parse_fragment(load_url: str) -> Optional[str]:
    """Extract the HTML anchor ID from a Dash load_url fragment.

    Handles Dash-specific format: //dash_ref_{html-id}/Type/Name/Index
    Falls back to plain #anchor for non-Dash docsets.
    """
    fragment = unquote(urlparse(load_url).fragment)
    if not fragment:
        return None
    if fragment.startswith("//dash_ref_"):
        return fragment[len("//dash_ref_"):].split("/")[0]
    return fragment
```

**Step 4: Run to verify tests pass**

```bash
uv run pytest tests/test_server.py::TestParseFragment -v
```

Expected: 4 tests passing.

**Step 5: Commit**

```bash
git add src/dash_mcp_server/server.py tests/test_server.py
git commit -m "Add parse_fragment helper with tests"
```

---

### Task 3: Implement and test `extract_section`

**Files:**
- Modify: `src/dash_mcp_server/server.py`
- Modify: `tests/test_server.py`

**Step 1: Write failing tests**

Append to `tests/test_server.py`:

```python
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
```

**Step 2: Run to verify tests fail**

```bash
uv run pytest tests/test_server.py::TestExtractSection -v
```

Expected: `ImportError: cannot import name 'extract_section'`

**Step 3: Add import to server.py**

Add `re` to the imports at the top of `server.py`:

```python
import re
```

Add `BeautifulSoup` import after the existing imports:

```python
from bs4 import BeautifulSoup
```

**Step 4: Implement `extract_section` in server.py**

Add this function directly after `parse_fragment`:

```python
def extract_section(html: str, anchor_id: Optional[str]) -> str:
    """Extract a specific section from HTML by anchor ID, or strip navigation.

    With anchor_id: finds the element with that id and returns it. If the element
    is a thin anchor tag, walks up to the nearest block-level parent.
    Falls back to nav-stripping if the anchor is not found.

    Without anchor_id: removes nav/sidebar elements and returns the body.
    """
    soup = BeautifulSoup(html, "html.parser")

    if anchor_id:
        element = soup.find(id=anchor_id)
        if element:
            # Walk up from thin elements (e.g. <a id="..."> used as anchor)
            if element.name in ("a", "span"):
                for parent in element.parents:
                    if parent.name in ("div", "section", "article", "li"):
                        element = parent
                        break
            return str(element)
        # Anchor not found — fall through to nav stripping

    # Strip navigation and sidebar noise
    for tag in soup.find_all(["nav", "aside", "header", "footer"]):
        tag.decompose()
    for tag in soup.find_all(class_=re.compile(r"sidebar|navigation|toc|menu", re.I)):
        tag.decompose()

    body = soup.body
    return str(body) if body else str(soup)
```

**Step 5: Run to verify tests pass**

```bash
uv run pytest tests/test_server.py::TestExtractSection -v
```

Expected: 5 tests passing.

**Step 6: Commit**

```bash
git add src/dash_mcp_server/server.py tests/test_server.py
git commit -m "Add extract_section helper with tests"
```

---

### Task 4: Wire helpers into `load_documentation_page`

**Files:**
- Modify: `src/dash_mcp_server/server.py` (update `load_documentation_page`)

**Step 1: Find the current conversion line**

In `load_documentation_page` (around line 538), find:

```python
content = html_to_text(response.text)
```

**Step 2: Replace with the two-step extraction**

```python
anchor_id = parse_fragment(load_url)
cleaned_html = extract_section(response.text, anchor_id)
content = html_to_text(cleaned_html)
```

**Step 3: Verify the server still starts**

```bash
uv run dash-mcp-server &
sleep 2
kill %1
```

Expected: starts without errors.

**Step 4: Commit**

```bash
git add src/dash_mcp_server/server.py
git commit -m "Wire BeautifulSoup section extraction into load_documentation_page"
```

---

### Task 5: Manual verification

**Step 1: Reload MCP server**

Restart Claude Code to pick up the updated server.

**Step 2: Call with a fragment URL**

Call `load_documentation_page` with a URL containing a `#//dash_ref_...` fragment (e.g. the `sort_by` URL from earlier testing).

Expected: response contains only the `sort_by` method documentation, no class index or sidebar navigation. Token count should be well under 5k.

**Step 3: Call with a no-fragment URL**

Call `load_documentation_page` with a plain page URL (no fragment).

Expected: response contains method documentation but no `<nav>` content or sidebar links.
