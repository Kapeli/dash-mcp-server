# BeautifulSoup Section Extraction for load_documentation_page

**Date:** 2026-02-27

## Problem

`load_documentation_page` returns the entire documentation page as Markdown. For large
pages (e.g. Ruby's `Enumerable` module), this produces ~39k tokens — mostly sidebar
navigation and a class index — when the caller only wants a single method's documentation.

## Solution

Use BeautifulSoup to surgically extract only the relevant section of the HTML *before*
passing it to `html_to_text()`. The fragment in the `load_url` (e.g.
`#//dash_ref_method-i-sort_by/Method/sort_by/0`) identifies exactly which section is
wanted.

## Data Flow

1. Parse `load_url` with `urllib.parse.urlparse` to extract the fragment *before* making
   the HTTP request (httpx strips the fragment from the wire request automatically)
2. Fetch the full HTML from the Dash API (unchanged)
3. Parse HTML with BeautifulSoup (`html.parser`, stdlib — no extra dependency)
4. **If fragment present:** decode anchor ID, find element, extract section
5. **If no fragment:** strip nav/sidebar elements, return cleaned body
6. Pass cleaned HTML string to `html_to_text()` as before

## Fragment Decoding

Dash HTTP API fragments use the format `//dash_ref_{html-id}/Type/Name/Index`.

```python
from urllib.parse import urlparse, unquote

fragment = unquote(urlparse(load_url).fragment)

if fragment.startswith("//dash_ref_"):
    anchor_id = fragment[len("//dash_ref_"):].split("/")[0]
elif fragment:
    anchor_id = fragment  # plain #anchor fallback for non-Dash docsets
else:
    anchor_id = None
```

The `//dash_ref_` format was determined empirically from observed API responses. Plain
fragment fallback ensures compatibility with non-Dash-formatted URLs.

## BeautifulSoup Extraction Logic

### With anchor_id

```python
element = soup.find(id=anchor_id)
```

- If the element is a substantial block (`<div>`, `<section>`) with real content, extract
  it directly via `str(element)`
- If it's a thin element (e.g. `<a>` anchor tag), walk up to the nearest block-level
  parent with meaningful content
- If the anchor is not found, fall back to full body-minus-nav

### Without anchor_id

Remove noise elements before converting:

```python
for tag in soup.find_all(["nav", "aside", "header", "footer"]):
    tag.decompose()
for tag in soup.find_all(class_=re.compile(r"sidebar|navigation|toc|menu", re.I)):
    tag.decompose()
```

Return `str(soup.body)` (or full document if no `<body>`).

## New Internal Helpers

Not exposed as MCP tools:

- `parse_fragment(load_url: str) -> Optional[str]` — extracts anchor ID from URL
- `extract_section(html: str, anchor_id: Optional[str]) -> str` — BeautifulSoup surgery,
  returns cleaned HTML string

## API Changes

- `load_documentation_page(ctx, load_url)` — **signature unchanged**
- `DocumentationPage` model — **unchanged**
- New dependency: `beautifulsoup4` (uses stdlib `html.parser`, no `lxml` needed)

## Trade-offs

| Concern | Notes |
|---------|-------|
| New dependency | `beautifulsoup4` is small and widely used |
| Docset compatibility | Falls back gracefully if anchor not found |
| Non-Dash fragments | Plain `#anchor` fallback handles other docsets |
| Navigation removal | Tag/class heuristics cover common patterns; may miss unusual docsets |
