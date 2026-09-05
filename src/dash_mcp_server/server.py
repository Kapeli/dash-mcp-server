from typing import Optional
import html2text
import httpx

import os
import re
import subprocess
import json
from pathlib import Path
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Context
from pydantic import BaseModel, Field
from urllib.parse import urlparse, unquote

DEFAULT_RESPONSE_TOKEN_LIMIT = 25000
DEFAULT_RETRIEVAL_TOKEN_LIMIT = 0

RESPONSE_TOKEN_LIMIT_VARIABLE = "DASH_RESPONSE_TOKEN_LIMIT"
RETRIEVAL_TOKEN_LIMIT_VARIABLE = "DASH_RETRIEVAL_TOKEN_LIMIT"


def token_limit(variable: str, default: int) -> int:
    """Read a token budget from the environment, falling back to `default`.

    Zero means no limit, and so does a negative value, which is the only sane reading of
    a budget that cannot be met. Absent, blank or unparseable leaves the default in place
    rather than failing the server at startup: an operator typo should not take the tool
    down, and the default is the behaviour that was there before the variable existed.

    Read per call rather than once, so a limit can be changed without restarting.
    """
    raw = os.environ.get(variable)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return max(value, 0)


mcp = FastMCP("Dash Documentation API")


async def check_api_health(ctx: Context, port: int) -> bool:
    """Check if the Dash API server is responding at the given port."""
    base_url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{base_url}/health")
            response.raise_for_status()
        await ctx.debug(f"Successfully connected to Dash API at {base_url}")
        return True
    except Exception as e:
        await ctx.debug(f"Health check failed for {base_url}: {e}")
        return False


async def working_api_base_url(ctx: Context) -> Optional[str]:
    dash_running = await ensure_dash_running(ctx)
    if not dash_running:
        return None

    port = await get_dash_api_port(ctx)
    if port is None:
        # Try to automatically enable the Dash API Server
        await ctx.info(
            "The Dash API Server is not enabled. Attempting to enable it automatically..."
        )
        try:
            subprocess.run(
                [
                    "defaults",
                    "write",
                    "com.kapeli.dashdoc",
                    "DHAPIServerEnabled",
                    "YES",
                ],
                check=True,
                timeout=10,
            )
            subprocess.run(
                [
                    "defaults",
                    "write",
                    "com.kapeli.dash-setapp",
                    "DHAPIServerEnabled",
                    "YES",
                ],
                check=True,
                timeout=10,
            )
            # Wait a moment for Dash to pick up the change
            import time

            time.sleep(2)

            # Try to get the port again
            port = await get_dash_api_port(ctx)
            if port is None:
                await ctx.error(
                    "Failed to enable Dash API Server automatically. Please enable it manually in Dash Settings > Integration"
                )
                return None
            else:
                await ctx.info("Successfully enabled Dash API Server")
        except Exception as e:
            await ctx.error(
                "Failed to enable Dash API Server automatically. Please enable it manually in Dash Settings > Integration"
            )
            return None

    return f"http://127.0.0.1:{port}"


async def get_dash_api_port(ctx: Context) -> Optional[int]:
    """Get the Dash API port from the status.json file and verify the API server is responding."""
    status_file = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Dash"
        / ".dash_api_server"
        / "status.json"
    )

    try:
        with open(status_file, "r") as f:
            status_data = json.load(f)
            port = status_data.get("port")
            if port is None:
                return None

        # Check if the API server is actually responding
        if await check_api_health(ctx, port):
            return port
        else:
            return None

    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def check_dash_running() -> bool:
    """Check if Dash app is running by looking for the process."""
    try:
        # Use pgrep to check for Dash process
        result = subprocess.run(["pgrep", "-f", "Dash"], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


async def ensure_dash_running(ctx: Context) -> bool:
    """Ensure Dash is running, launching it if necessary."""
    if not check_dash_running():
        await ctx.info("Dash is not running. Launching Dash...")
        try:
            # Launch Dash using the bundle identifier
            result = subprocess.run(
                ["open", "-g", "-j", "-b", "com.kapeli.dashdoc"], timeout=10
            )
            if result.returncode != 0:
                # Try Setapp bundle identifier
                subprocess.run(
                    ["open", "-g", "-j", "-b", "com.kapeli.dash-setapp"],
                    check=True,
                    timeout=10,
                )
            # Wait a moment for Dash to start
            import time

            time.sleep(4)

            # Check again if Dash is now running
            if not check_dash_running():
                await ctx.error("Failed to launch Dash application")
                return False
            else:
                await ctx.info("Dash launched successfully")
                return True
        except subprocess.CalledProcessError:
            await ctx.error("Failed to launch Dash application")
            return False
        except Exception as e:
            await ctx.error(f"Error launching Dash: {e}")
            return False
    else:
        return True


class DocsetResult(BaseModel):
    """Information about a docset."""

    name: str = Field(description="Display name of the docset")
    identifier: str = Field(description="Unique identifier")
    platform: str = Field(description="Platform/type of the docset")
    full_text_search: str = Field(
        description="Full-text search status: 'not supported', 'disabled', 'indexing', or 'enabled'"
    )
    notice: Optional[str] = Field(
        description="Optional notice about the docset status", default=None
    )


class DocsetResults(BaseModel):
    """Result from listing docsets."""

    docsets: list[DocsetResult] = Field(
        description="List of installed docsets", default_factory=list
    )
    error: Optional[str] = Field(
        description="Error message if there was an issue", default=None
    )


class SearchResult(BaseModel):
    """A search result from documentation."""

    name: str = Field(description="Name of the documentation entry")
    type: str = Field(description="Type of result (Function, Class, etc.)")
    platform: Optional[str] = Field(description="Platform of the result", default=None)
    load_url: str = Field(description="URL to load the documentation")
    docset: Optional[str] = Field(description="Name of the docset", default=None)
    description: Optional[str] = Field(
        description="Additional description", default=None
    )
    language: Optional[str] = Field(
        description="Programming language (snippet results only)", default=None
    )
    tags: Optional[str] = Field(description="Tags (snippet results only)", default=None)


class SearchResults(BaseModel):
    """Result from searching documentation."""

    results: list[SearchResult] = Field(
        description="List of search results", default_factory=list
    )
    error: Optional[str] = Field(
        description="Error message if there was an issue", default=None
    )


class DocumentationPage(BaseModel):
    """Documentation page content."""

    content: str = Field(description="The documentation page content")
    load_url: str = Field(description="The URL that was loaded")
    error: Optional[str] = Field(
        description="Error message if there was an issue", default=None
    )
    truncated: bool = Field(
        description="True if the page was cut short to stay within the token budget",
        default=False,
    )
    next_offset: Optional[int] = Field(
        description="Character offset to pass back as `offset` to continue reading this "
        "page, or null when the page is complete",
        default=None,
    )
    total_characters: Optional[int] = Field(
        description="Length in characters of the whole page, so a caller can tell how much "
        "is left",
        default=None,
    )


def html_to_text(html: str) -> str:
    """Convert HTML to Markdown using html2text."""
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    h.unicode_snob = True
    return h.handle(html)


def parse_fragment(load_url: str) -> Optional[str]:
    """Extract the HTML anchor ID from a Dash load_url fragment.

    Handles Dash-specific format: //dash_ref_{html-id}/Type/Name/Index
    Falls back to plain #anchor for non-Dash docsets.
    """
    fragment = unquote(urlparse(load_url).fragment)
    if not fragment:
        return None
    if fragment.startswith("//dash_ref_"):
        anchor = fragment[len("//dash_ref_"):].split("/")[0]
        return anchor if anchor else None
    return fragment


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
            # Return if we found a substantial element (not still a thin anchor)
            if element.name not in ("a", "span"):
                return str(element)
        # Anchor not found, or thin element with no suitable parent — fall through

    # Strip navigation and sidebar noise
    for tag in soup.find_all(["nav", "aside", "header", "footer"]):
        tag.decompose()


    body = soup.body
    return str(body) if body else str(soup)


def _as_jsonable(obj):
    """Convert Pydantic models to plain dicts, including ones nested inside containers.

    `json.dumps` cannot serialize a model, and its `default=` fallback would stringify one
    into its repr — a different length from the JSON that actually gets sent.
    """
    if hasattr(obj, "model_dump"):  # Pydantic model
        return _as_jsonable(obj.model_dump())
    if isinstance(obj, dict):
        return {k: _as_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_as_jsonable(item) for item in obj]
    return obj


# Token estimation is calibrated against real tokenizers rather than assumed. The two
# coefficients below were fitted on 55 real responses from this server (one docsets listing
# and 54 searches over 18 queries against 198 installed docsets, 12k-93k characters each),
# then validated on a held-out third against six tokenizers: GPT-4o (o200k), GPT-4
# (cl100k), Qwen3, DeepSeek-V3, Kimi-K2 and GLM-4.5.
#
# Letters and symbols are counted separately because they cost very different amounts. A
# response of this kind is roughly 63% letters, 25% punctuation and URL syntax, 7% digits
# and only 5% spaces; JSON punctuation costs about one token per character, while letters
# pack around eight per token in identifier-dense text. A single chars-per-token divisor
# has to average across that ratio, and because the ratio swings with the shape of the
# payload no single value fits: 4 characters per token reports 12-43% low across the
# held-out set, so the documented cap never bites. Counting the two classes separately
# tracks the real count to within 0-24%.
#
# The coefficients include a 1.14 safety factor, so the estimate never falls below the true
# count for any of the six. That asymmetry is deliberate: this figure guards a truncation
# cap, where over-estimating returns slightly less content than it could, but
# under-estimating overruns the caller's context window.
#
# Scope, stated plainly: the fit is calibrated on this server's search and docset responses.
# A small-vocabulary client (a 32k-vocab model such as Mistral-7B) tokenizes more densely
# and would be under-estimated by up to 11%. Prose-heavy text — a converted documentation
# page, which no caller of this function currently produces — has a different letter/space
# mix, and these coefficients land anywhere between 25% under and 29% over on it (measured
# on 80 real pages loaded through this server). Text of that shape needs its own
# calibration; it must not borrow this one.
_TOKENS_PER_LETTER = 0.1268
_TOKENS_PER_SYMBOL = 0.9863

_LETTER_RUN_RE = re.compile(r"[A-Za-z]+")


def estimate_tokens(obj) -> int:
    """Estimate how many tokens `obj` will occupy once serialized as JSON.

    The estimate is taken over the object's JSON serialization, which is what is actually
    sent. Summing each field's length separately misses everything JSON adds around them --
    quotes, colons, commas, braces, and `null` for every unset optional field -- and rounds
    each field down independently, so it reports well under the real size on responses made
    of many small records.

    Letters and symbols are weighted separately; see the calibration note above.
    """
    try:
        serialized = json.dumps(_as_jsonable(obj), default=str)
    except (TypeError, ValueError):
        serialized = str(obj)

    letters = sum(len(run) for run in _LETTER_RUN_RE.findall(serialized))
    spaces = serialized.count(" ")
    # Everything that is neither a letter nor a space: punctuation, digits, JSON syntax.
    symbols = len(serialized) - letters - spaces

    estimate = letters * _TOKENS_PER_LETTER + symbols * _TOKENS_PER_SYMBOL
    return max(1, round(estimate))


def response_limit_note() -> str:
    """The truncation sentence for a tool description, or nothing when nothing is capped.

    A tool description stating a number the operator did not choose is worse than no
    number at all: the model plans against 25,000 while the server trims at 4,000, or
    budgets for a truncation that a limit of zero means will never happen. The sentence
    is therefore built from the resolved limit, and omitted entirely when there isn't one.

    Read once, when the tools are registered. That is the same moment the server reads
    everything else out of its environment, so the two only disagree if something mutates
    os.environ while the process runs.
    """
    limit = token_limit(RESPONSE_TOKEN_LIMIT_VARIABLE, DEFAULT_RESPONSE_TOKEN_LIMIT)
    if not limit:
        return ""
    return (
        f" Results are truncated if they would exceed {limit:,} tokens, "
        f"set by {RESPONSE_TOKEN_LIMIT_VARIABLE}."
    )


def retrieval_limit_note() -> str:
    """The same, for the page loader, whose default is no limit at all."""
    limit = token_limit(RETRIEVAL_TOKEN_LIMIT_VARIABLE, DEFAULT_RETRIEVAL_TOKEN_LIMIT)
    if not limit:
        return (
            " A whole page is returned unless max_tokens is given: the operator has set "
            "no default limit."
        )
    return (
        f" Pages are truncated at {limit:,} tokens unless max_tokens overrides it, "
        f"set by {RETRIEVAL_TOKEN_LIMIT_VARIABLE}. Pass next_offset back as offset to "
        "read the rest."
    )


def fit_within_token_limit(items: list, limit: int) -> list:
    """Return the longest leading run of `items` whose JSON fits within `limit` tokens.

    Adding up each item's own estimate is not the same as estimating the list. The
    serialization of a list carries what JSON puts between its members -- a comma
    after every one of them, and the enclosing brackets -- and none of that appears
    in any single member's estimate. On a response of a few hundred small records
    that is around 1.5% unaccounted for, all of it on the side that overruns the
    budget rather than undershooting it.

    The per-item pass is a cheap way to get close without serializing the whole list
    once per candidate. The trim afterwards is what makes the answer true: it is
    measured against the list itself, the way estimate_tokens will measure it.

    A limit of zero means no limit, so the list comes back untouched.
    """
    if not limit:
        return items

    kept: list = []
    running = 0

    for item in items:
        item_tokens = estimate_tokens(item)
        if running + item_tokens > limit:
            break
        kept.append(item)
        running += item_tokens

    while kept and estimate_tokens(kept) > limit:
        kept.pop()

    return kept


# The coefficients above are calibrated on JSON search responses. A converted documentation
# page is a different shape of text — 66% letters, 19% punctuation and 15% spaces, against
# 63/25/5 for a search response — and borrowing the JSON coefficients for it lands anywhere
# from 25% under to 29% over, measured on the corpus below. Under-estimating is the one
# outcome a truncation cap cannot afford, so page text gets its own fit.
#
# Fitted the same way on 80 real documentation pages pulled through this server's own
# extract_section + html_to_text (1.6k-344k characters, 20 queries across 120 docsets),
# two thirds train, validated on the held-out third against the same six tokenizers. Spaces
# earn a coefficient here that they do not have in JSON: in prose a leading space is usually
# absorbed into the following word's token, so a space costs about a fifth of a token rather
# than a whole one, and at 15% of the text that difference matters.
#
# Held-out ratio of estimate to real count: 1.01-1.55 across the six, never under. The
# spread is wider than the JSON fit's because pages are genuinely heterogeneous — a prose
# tutorial and a generated C++ signature table tokenize differently and no two-or-three term
# model closes that gap. The cost of the spread is returning less than the budget allows;
# the alternative risks overrunning the caller's context, which is worse.
_TEXT_TOKENS_PER_LETTER = 0.1366
_TEXT_TOKENS_PER_SYMBOL = 1.1528
_TEXT_TOKENS_PER_SPACE = 0.2197


def estimate_text_tokens(text: str) -> int:
    """Estimate how many tokens a block of documentation text will occupy.

    Unlike `estimate_tokens`, this measures the text itself rather than a JSON payload, and
    uses the page calibration described above.
    """
    letters = sum(len(run) for run in _LETTER_RUN_RE.findall(text))
    spaces = text.count(" ")
    symbols = len(text) - letters - spaces

    estimate = (
        letters * _TEXT_TOKENS_PER_LETTER
        + symbols * _TEXT_TOKENS_PER_SYMBOL
        + spaces * _TEXT_TOKENS_PER_SPACE
    )
    return round(estimate)


def take_token_budget(
    text: str, offset: int, max_tokens: int
) -> tuple[str, Optional[int]]:
    """Return the slice of `text` starting at `offset` that fits in `max_tokens`.

    Returns the slice and the offset to resume from, or None when the text is exhausted.
    The cut is moved back to the last line break inside the slice so a page never breaks
    mid-sentence, unless that would return nothing — a single line longer than the budget
    is cut where the budget runs out, because making no progress is worse than a hard cut.
    """
    remainder = text[offset:]
    if not remainder:
        return "", None

    if estimate_text_tokens(remainder) <= max_tokens:
        return remainder, None

    # Estimates rise monotonically with length, so the longest fitting prefix can be found
    # by bisection rather than by re-estimating on every character.
    low, high = 0, len(remainder)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_text_tokens(remainder[:middle]) <= max_tokens:
            low = middle
        else:
            high = middle - 1

    cut = max(1, low)
    line_end = remainder.rfind("\n", 0, cut)
    if line_end > 0:
        cut = line_end + 1

    return remainder[:cut], offset + cut


@mcp.tool(
    description=(
        "List all installed documentation sets in Dash. An empty list is returned if "
        "the user has no docsets installed." + response_limit_note()
    )
)
async def list_installed_docsets(ctx: Context) -> DocsetResults:
    """List all installed documentation sets in Dash.

    The description registered with the tool is built above, so that the truncation
    sentence carries the operator's own limit rather than a number baked in here.
    """
    try:
        base_url = await working_api_base_url(ctx)
        if base_url is None:
            return DocsetResults(
                error="Failed to connect to Dash API Server. Please ensure Dash is running and the API server is enabled (in Dash Settings > Integration)."
            )
        await ctx.debug("Fetching installed docsets from Dash API")

        with httpx.Client(timeout=30.0) as client:
            response = client.get(f"{base_url}/docsets/list")
            response.raise_for_status()
            result = response.json()

        docsets = result.get("docsets", [])
        await ctx.info(f"Found {len(docsets)} installed docsets")

        # Build result list with token limit checking
        limit = token_limit(
            RESPONSE_TOKEN_LIMIT_VARIABLE, DEFAULT_RESPONSE_TOKEN_LIMIT
        )
        limited_docsets = fit_within_token_limit(
            [
                DocsetResult(
                    name=docset["name"],
                    identifier=docset["identifier"],
                    platform=docset["platform"],
                    full_text_search=docset["full_text_search"],
                    notice=docset.get("notice"),
                )
                for docset in docsets
            ],
            limit,
        )

        if len(limited_docsets) < len(docsets):
            await ctx.warning(
                f"Token limit reached. Returning {len(limited_docsets)} of "
                f"{len(docsets)} docsets to stay under the {limit} token limit "
                f"({RESPONSE_TOKEN_LIMIT_VARIABLE})."
            )
            await ctx.info(
                f"Returned {len(limited_docsets)} docsets (truncated from {len(docsets)} due to token limit)"
            )

        return DocsetResults(docsets=limited_docsets)

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            await ctx.warning("No docsets found. Install some in Settings > Downloads.")
            return DocsetResults(
                error="No docsets found. Instruct the user to install some docsets in Settings > Downloads."
            )
        return DocsetResults(error=f"HTTP error: {e}")
    except Exception as e:
        await ctx.error(f"Failed to get installed docsets: {e}")
        return DocsetResults(error=f"Failed to get installed docsets: {e}")


@mcp.tool(
    description=(
        "Search for documentation across docset identifiers and snippets.\n\n"
        "Args:\n"
        "    query: The search query string\n"
        "    docset_identifiers: Comma-separated list of docset identifiers to search "
        "in (from list_installed_docsets)\n"
        "    search_snippets: Whether to include snippets in search results\n"
        "    max_results: Maximum number of results to return (1-1000)"
        + response_limit_note()
    )
)
async def search_documentation(
    ctx: Context,
    query: str,
    docset_identifiers: str,
    search_snippets: bool = True,
    max_results: int = 100,
) -> SearchResults:
    """Search for documentation across docset identifiers and snippets.

    The description registered with the tool is built above, so that the truncation
    sentence carries the operator's own limit rather than a number baked in here.
    """
    if not query.strip():
        await ctx.error("Query cannot be empty")
        return SearchResults(error="Query cannot be empty")

    if not docset_identifiers.strip():
        await ctx.error(
            "docset_identifiers cannot be empty. Get the docset identifiers using list_installed_docsets"
        )
        return SearchResults(
            error="docset_identifiers cannot be empty. Get the docset identifiers using list_installed_docsets"
        )

    if max_results < 1 or max_results > 1000:
        await ctx.error("max_results must be between 1 and 1000")
        return SearchResults(error="max_results must be between 1 and 1000")

    try:
        base_url = await working_api_base_url(ctx)
        if base_url is None:
            return SearchResults(
                error="Failed to connect to Dash API Server. Please ensure Dash is running and the API server is enabled (in Dash Settings > Integration)."
            )

        params = {
            "query": query,
            "docset_identifiers": docset_identifiers,
            "search_snippets": search_snippets,
            "max_results": max_results,
        }

        await ctx.debug(f"Searching Dash API with query: '{query}'")

        with httpx.Client(timeout=30.0) as client:
            response = client.get(f"{base_url}/search", params=params)
            response.raise_for_status()
            result = response.json()

        # Check for warning message in response
        warning_message = None
        if "message" in result:
            warning_message = result["message"]
            await ctx.warning(warning_message)

        results = result.get("results", [])
        # Filter out empty dict entries (Dash API returns [{}] for no results)
        results = [r for r in results if r]

        if not results and " " in query:
            return SearchResults(
                results=[], error="Nothing found. Try to search for fewer terms."
            )

        await ctx.info(f"Found {len(results)} results")

        # Build result list with token limit checking
        limit = token_limit(
            RESPONSE_TOKEN_LIMIT_VARIABLE, DEFAULT_RESPONSE_TOKEN_LIMIT
        )
        limited_results = fit_within_token_limit(
            [
                SearchResult(
                    name=item["name"],
                    type=item["type"],
                    platform=item.get("platform"),
                    load_url=item["load_url"],
                    docset=item.get("docset"),
                    description=item.get("description"),
                    language=item.get("language"),
                    tags=item.get("tags"),
                )
                for item in results
            ],
            limit,
        )

        if len(limited_results) < len(results):
            await ctx.warning(
                f"Token limit reached. Returning {len(limited_results)} of "
                f"{len(results)} results to stay under the {limit} token limit "
                f"({RESPONSE_TOKEN_LIMIT_VARIABLE})."
            )
            await ctx.info(
                f"Returned {len(limited_results)} results (truncated from {len(results)} due to token limit)"
            )

        return SearchResults(results=limited_results, error=warning_message)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            error_text = e.response.text
            if "Docset with identifier" in error_text and "not found" in error_text:
                await ctx.error(
                    "Invalid docset identifier. Run list_installed_docsets to see available docsets."
                )
                return SearchResults(
                    error="Invalid docset identifier. Run list_installed_docsets to see available docsets, then use the exact identifier from that list."
                )
            elif "No docsets found" in error_text:
                await ctx.error("No valid docsets found for search.")
                return SearchResults(
                    error="No valid docsets found for search. Either provide valid docset identifiers from list_installed_docsets, or set search_snippets=true to search snippets only."
                )
            else:
                await ctx.error(f"Bad request: {error_text}")
                return SearchResults(
                    error=f"Bad request: {error_text}. Please ensure Dash is running and the API server is enabled (in Dash Settings > Integration)."
                )
        elif e.response.status_code == 403:
            error_text = e.response.text
            if "API access blocked due to Dash trial expiration" in error_text:
                await ctx.error(
                    "Dash trial expired. Purchase Dash to continue using the API."
                )
                return SearchResults(
                    error="Your Dash trial has expired. Purchase Dash at https://kapeli.com/dash to continue using the API. During trial expiration, API access is blocked."
                )
            else:
                await ctx.error(f"Forbidden: {error_text}")
                return SearchResults(
                    error=f"Forbidden: {error_text}. Please ensure Dash is running and the API server is enabled (in Dash Settings > Integration)."
                )
        await ctx.error(f"HTTP error: {e}")
        return SearchResults(
            error=f"HTTP error: {e}. Please ensure Dash is running and the API server is enabled (in Dash Settings > Integration)."
        )
    except Exception as e:
        await ctx.error(f"Search failed: {e}")
        return SearchResults(
            error=f"Search failed: {e}. Please ensure Dash is running and the API server is enabled (in Dash Settings > Integration)."
        )


@mcp.tool()
async def enable_docset_fts(ctx: Context, identifier: str) -> bool:
    """
    Enable full-text search for a specific docset.

    Args:
        identifier: The docset identifier (from list_installed_docsets)

    Returns:
        True if FTS was successfully enabled, False otherwise
    """
    if not identifier.strip():
        await ctx.error("Docset identifier cannot be empty")
        return False

    try:
        base_url = await working_api_base_url(ctx)
        if base_url is None:
            return False

        await ctx.debug(f"Enabling FTS for docset: {identifier}")

        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                f"{base_url}/docsets/enable_fts", params={"identifier": identifier}
            )
            response.raise_for_status()
            result = response.json()

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            await ctx.error(f"Bad request: {e.response.text}")
            return False
        elif e.response.status_code == 404:
            await ctx.error(f"Docset not found: {identifier}")
            return False
        await ctx.error(f"HTTP error: {e}")
        return False
    except Exception as e:
        await ctx.error(f"Failed to enable FTS: {e}")
        return False
    return True


@mcp.tool(
    description=(
        "Load a documentation page from a load_url returned by search_documentation.\n\n"
        "Args:\n"
        "    load_url: The load_url value from a search result (must point to the local "
        "Dash API at 127.0.0.1)\n"
        "    offset: Character offset to start reading from. Pass the next_offset of a "
        "previous call to continue a page that was truncated. Defaults to the start of "
        "the page.\n"
        "    max_tokens: Maximum size of the returned content, in tokens. Zero means no "
        "limit. Omit it to use the limit the operator configured.\n\n"
        "Returns:\n"
        "    The documentation page content as plain text with markdown-style links. "
        "Pages larger than the budget are cut at a line boundary; truncated is then true "
        "and next_offset says where to resume."
        + retrieval_limit_note()
    )
)
async def load_documentation_page(
    ctx: Context,
    load_url: str,
    offset: int = 0,
    max_tokens: Optional[int] = None,
) -> DocumentationPage:
    """Load a documentation page from a load_url returned by search_documentation.

    The description registered with the tool is built above, so that the size sentence
    carries the operator's own limit rather than a number baked in here.
    """
    if offset < 0:
        await ctx.error("offset cannot be negative")
        return DocumentationPage(
            content="", load_url=load_url, error="offset cannot be negative"
        )

    if max_tokens is not None and max_tokens < 0:
        await ctx.error("max_tokens cannot be negative")
        return DocumentationPage(
            content="",
            load_url=load_url,
            error="max_tokens cannot be negative. Use 0 for no limit.",
        )

    budget = (
        max_tokens
        if max_tokens is not None
        else token_limit(RETRIEVAL_TOKEN_LIMIT_VARIABLE, DEFAULT_RETRIEVAL_TOKEN_LIMIT)
    )

    if not load_url.startswith("http://127.0.0.1"):
        await ctx.error(
            "Invalid URL: load_url must point to the local Dash API (http://127.0.0.1)"
        )
        return DocumentationPage(
            content="",
            load_url=load_url,
            error="Invalid URL: load_url must point to the local Dash API (http://127.0.0.1). Only URLs returned by search_documentation are supported.",
        )

    try:
        await ctx.debug(f"Loading documentation page: {load_url}")

        with httpx.Client(timeout=30.0) as client:
            response = client.get(load_url)
            response.raise_for_status()

        anchor_id = parse_fragment(load_url)
        cleaned_html = extract_section(response.text, anchor_id)
        full_content = html_to_text(cleaned_html)
        if budget:
            content, next_offset = take_token_budget(full_content, offset, budget)
        else:
            content, next_offset = full_content[offset:], None

        if next_offset is not None:
            await ctx.warning(
                f"Page truncated at {budget} tokens. Returned characters "
                f"{offset}-{next_offset} of {len(full_content)}; call again with "
                f"offset={next_offset} for the rest."
            )
        await ctx.info(
            f"Successfully loaded documentation page ({len(content)} characters)"
        )
        return DocumentationPage(
            content=content,
            load_url=load_url,
            truncated=next_offset is not None,
            next_offset=next_offset,
            total_characters=len(full_content),
        )

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            error_text = e.response.text
            if "API access blocked due to Dash trial expiration" in error_text:
                await ctx.error(
                    "Dash trial expired. Purchase Dash to continue using the API."
                )
                return DocumentationPage(
                    content="",
                    load_url=load_url,
                    error="Your Dash trial has expired. Purchase Dash at https://kapeli.com/dash to continue using the API.",
                )
            await ctx.error(f"Forbidden: {error_text}")
            return DocumentationPage(
                content="", load_url=load_url, error=f"Forbidden: {error_text}"
            )
        elif e.response.status_code == 404:
            await ctx.error("Documentation page not found.")
            return DocumentationPage(
                content="", load_url=load_url, error="Documentation page not found."
            )
        await ctx.error(f"HTTP error: {e}")
        return DocumentationPage(
            content="", load_url=load_url, error=f"HTTP error: {e}"
        )
    except Exception as e:
        await ctx.error(f"Failed to load documentation page: {e}")
        return DocumentationPage(
            content="",
            load_url=load_url,
            error=f"Failed to load documentation page: {e}",
        )


def main():
    mcp.run()


if __name__ == "__main__":
    main()
