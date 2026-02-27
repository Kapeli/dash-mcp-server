# Replace Custom HTML Parser with html2text

**Date:** 2026-02-27

## Problem

The `load_documentation_page` tool used a hand-rolled `_HTMLToTextParser` (~80 lines) that:

- Added newlines for block-level tags (`<p>`, `<div>`, `<pre>`, etc.)
- Rendered `<a>` tags as `[text](href)` links
- Stripped `<script>` and `<style>` content

It did **not** handle:
- Heading levels (`<h1>`–`<h6>` → `#`–`######`)
- Unordered/ordered lists (`<ul>/<ol>/<li>` → `* item`)
- Fenced code blocks (`` ``` ``)
- Tables
- Blockquotes

The result was flat, hard-to-navigate plain text that made LLM consumption of documentation less effective.

## Solution

Replace `_HTMLToTextParser` and `html_to_text()` with the `html2text` library.

### Configuration

```python
h = html2text.HTML2Text()
h.ignore_links = False   # preserve hyperlinks as [text](href)
h.ignore_images = True   # omit image tags (not useful for LLMs)
h.body_width = 0         # no line wrapping (let LLM handle it)
h.unicode_snob = True    # prefer real Unicode over ASCII approximations
```

### Before vs After

**Input:**
```html
<h2>Parameters</h2>
<ul>
  <li>name: string</li>
  <li>value: number</li>
</ul>
<pre><code>foo(name="bar", value=42)</code></pre>
```

**Before (custom parser):**
```
Parameters
name: string
value: number
foo(name="bar", value=42)
```

**After (html2text):**
```markdown
## Parameters

  * name: string
  * value: number

    foo(name="bar", value=42)
```

## Trade-offs

| Concern | Notes |
|---------|-------|
| Added dependency | `html2text` is small (~1 file), mature, widely used |
| Behavior change | Output is now valid Markdown rather than plain text — better for LLM consumers |
| Link rendering | Unchanged: `[text](href)` format preserved |
| Images | Suppressed via `ignore_images = True` — not useful in documentation context |
