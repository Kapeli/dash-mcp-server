from typing import Optional
import html2text
import httpx

import difflib
import subprocess
import json
from pathlib import Path
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Context
from pydantic import BaseModel, Field
from urllib.parse import urlparse, unquote

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


class SearchedDocset(BaseModel):
    """A docset that a filtered search actually covered."""

    name: str = Field(description="Display name of the docset")
    platform: str = Field(
        description="Platform tag of the docset. Worth reading alongside the name: it is "
        "not derivable from it for most docsets, and it is what distinguishes an official "
        "docset from a user-contributed one, which bears on how much weight to give the "
        "results"
    )


class DocsetResults(BaseModel):
    """Result from listing docsets."""

    docsets: list[DocsetResult] = Field(
        description="Installed docsets matching the filter, or all of them when no "
        "filter was given",
        default_factory=list,
    )
    suggestions: list[DocsetResult] = Field(
        description="Docsets that resemble the filter but do not match it. Only ever "
        "populated when `docsets` is empty, and these are guesses, not results: an empty "
        "`docsets` means no installed docset matches, which usually means the docset is "
        "not installed",
        default_factory=list,
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
    searched_docsets: list[SearchedDocset] = Field(
        description="The docsets that were actually searched, in the order the filter "
        "ranked them. Populated when the caller named a filter rather than identifiers, "
        "so it can see what the filter resolved to — a filter matching more, or fewer, "
        "docsets than intended is otherwise invisible",
        default_factory=list,
    )


class DocumentationPage(BaseModel):
    """Documentation page content."""

    content: str = Field(description="The documentation page content")
    load_url: str = Field(description="The URL that was loaded")
    error: Optional[str] = Field(
        description="Error message if there was an issue", default=None
    )


def html_to_text(html: str) -> str:
    """Convert HTML to Markdown using html2text."""
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    h.unicode_snob = True
    return h.handle(html)


# A near-miss has to look quite like the term before it is worth showing. Measured against
# 198 real docsets: 0.6 keeps the recoveries that matter ("sqlachemy" -> SQLAlchemy at 0.95,
# "postgress" -> PostgreSQL at 0.67) and drops the noise a lower bar lets through
# ("node" -> MongoDB at 0.55, "postgres" -> Man Pages at 0.50).
_SUGGESTION_CUTOFF = 0.6
_MAX_SUGGESTIONS = 5


def _match_rank(docset: dict, term: str) -> Optional[int]:
    """Rank a docset against a search term, or None if it does not match at all.

    Lower is better: an exact name match beats a name that starts with the term, which
    beats one that merely contains it, which beats a match found only in the platform tag
    or the identifier.
    """
    name = docset.get("name", "").casefold()
    platform = docset.get("platform", "").casefold()
    identifier = docset.get("identifier", "").casefold()

    if name == term:
        return 0
    if name.startswith(term):
        return 1
    if term in name:
        return 2
    if term in platform or term in identifier:
        return 3
    return None


def filter_docsets(docsets: list[dict], term: str) -> tuple[list[dict], list[dict]]:
    """Split docsets into those matching `term` and, failing that, near-misses.

    Matching is a case-insensitive substring test across the display name, the platform
    tag and the identifier, because none of the three is reliable alone: PostgreSQL's
    platform is "psql", 22 docsets share the platform "cheatsheet", and the identifier is
    an opaque key the caller may be echoing back.

    When nothing matches, the second list holds docsets whose names merely resemble the
    term. They are returned separately and never alongside real matches, because a
    plausible wrong docset is worse than none: a caller that searches it will find the
    symbol missing and conclude it is undocumented, when the docset is simply not
    installed.
    """
    term = term.strip().casefold()
    if not term:
        return list(docsets), []

    ranked = [
        (rank, docset)
        for docset in docsets
        if (rank := _match_rank(docset, term)) is not None
    ]
    if ranked:
        # Sort is stable, so docsets of equal rank keep the order Dash returned them in.
        ranked.sort(key=lambda pair: pair[0])
        return [docset for _, docset in ranked], []

    scored = []
    for docset in docsets:
        similarity = max(
            difflib.SequenceMatcher(
                None, term, docset.get(field, "").casefold()
            ).ratio()
            for field in ("name", "platform")
        )
        if similarity >= _SUGGESTION_CUTOFF:
            scored.append((similarity, docset))

    scored.sort(key=lambda pair: -pair[0])
    return [], [docset for _, docset in scored[:_MAX_SUGGESTIONS]]


def parse_fragment(load_url: str) -> Optional[str]:
    """Extract the HTML anchor ID from a Dash load_url fragment.

    Handles Dash-specific format: //dash_ref_{html-id}/Type/Name/Index
    Falls back to plain #anchor for non-Dash docsets.
    """
    fragment = unquote(urlparse(load_url).fragment)
    if not fragment:
        return None
    if fragment.startswith("//dash_ref_"):
        anchor = fragment[len("//dash_ref_") :].split("/")[0]
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


def estimate_tokens(obj) -> int:
    """Estimate token count for a serialized object. Rough approximation: 1 token ≈ 4 characters."""
    if isinstance(obj, str):
        return max(1, len(obj) // 4)
    elif isinstance(obj, (list, tuple)):
        return sum(estimate_tokens(item) for item in obj)
    elif isinstance(obj, dict):
        return sum(estimate_tokens(k) + estimate_tokens(v) for k, v in obj.items())
    elif hasattr(obj, "model_dump"):  # Pydantic model
        return estimate_tokens(obj.model_dump())
    else:
        return max(1, len(str(obj)) // 4)


@mcp.tool()
async def list_installed_docsets(ctx: Context, filter: str = "") -> DocsetResults:
    """List installed documentation sets in Dash. An empty list is returned if the user has no docsets installed.

    Args:
        filter: Narrow the list to docsets whose name, platform or identifier contains
            this term, case-insensitively — "python", "postgres", "cheatsheet". Omit it
            to list everything, which on a well-stocked install is several thousand
            tokens; filtering is usually the cheaper way to find the identifier you need.

    If nothing matches, `docsets` is empty and `suggestions` may hold docsets whose names
    resemble the term. A suggestion is a guess, not a match: an empty `docsets` normally
    means that docset is not installed.

    Results are automatically truncated if they would exceed 25,000 tokens.
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

        installed = result.get("docsets", [])
        docsets, suggested = filter_docsets(installed, filter)

        if filter:
            await ctx.info(
                f"{len(docsets)} of {len(installed)} installed docsets match {filter!r}"
            )
            if not docsets:
                await ctx.warning(
                    f"No installed docset matches {filter!r}"
                    + (
                        f"; closest names: {', '.join(d['name'] for d in suggested)}"
                        if suggested
                        else ""
                    )
                )
        else:
            await ctx.info(f"Found {len(docsets)} installed docsets")

        # Build result list with token limit checking
        token_limit = 25000
        current_tokens = 100  # Base overhead for response structure
        limited_docsets = []

        for docset in docsets:
            docset_info = DocsetResult(
                name=docset["name"],
                identifier=docset["identifier"],
                platform=docset["platform"],
                full_text_search=docset["full_text_search"],
                notice=docset.get("notice"),
            )

            # Estimate tokens for this docset
            docset_tokens = estimate_tokens(docset_info)

            if current_tokens + docset_tokens > token_limit:
                await ctx.warning(
                    f"Token limit reached. Returning {len(limited_docsets)} of {len(docsets)} docsets to stay under 25k token limit."
                )
                break

            limited_docsets.append(docset_info)
            current_tokens += docset_tokens

        if len(limited_docsets) < len(docsets):
            await ctx.info(
                f"Returned {len(limited_docsets)} docsets (truncated from {len(docsets)} due to token limit)"
            )

        return DocsetResults(
            docsets=limited_docsets,
            suggestions=[
                DocsetResult(
                    name=docset["name"],
                    identifier=docset["identifier"],
                    platform=docset["platform"],
                    full_text_search=docset["full_text_search"],
                    notice=docset.get("notice"),
                )
                for docset in suggested
            ],
        )

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


@mcp.tool()
async def search_documentation(
    ctx: Context,
    query: str,
    docset_identifiers: str,
    search_snippets: bool = True,
    max_results: int = 100,
) -> SearchResults:
    """
    Search for documentation across docset identifiers and snippets.

    Args:
        query: The search query string
        docset_identifiers: Comma-separated list of docset identifiers to search in (from list_installed_docsets)
        search_snippets: Whether to include snippets in search results
        max_results: Maximum number of results to return (1-1000)

    Results are automatically truncated if they would exceed 25,000 tokens.
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
        token_limit = 25000
        current_tokens = 100  # Base overhead for response structure
        limited_results = []

        for item in results:
            search_result = SearchResult(
                name=item["name"],
                type=item["type"],
                platform=item.get("platform"),
                load_url=item["load_url"],
                docset=item.get("docset"),
                description=item.get("description"),
                language=item.get("language"),
                tags=item.get("tags"),
            )

            # Estimate tokens for this result
            result_tokens = estimate_tokens(search_result)

            if current_tokens + result_tokens > token_limit:
                await ctx.warning(
                    f"Token limit reached. Returning {len(limited_results)} of {len(results)} results to stay under 25k token limit."
                )
                break

            limited_results.append(search_result)
            current_tokens += result_tokens

        if len(limited_results) < len(results):
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
async def search_documentation_by_filter(
    ctx: Context,
    query: str,
    docset_filter: str,
    search_snippets: bool = True,
    max_results: int = 100,
) -> SearchResults:
    """
    Search documentation in the docsets matching a filter, without needing identifiers.

    Prefer this over search_documentation. Dash requires an explicit list of docsets and
    rejects a search that names none, but its identifiers are opaque eight-character keys
    that can only be learned by listing every installed docset first. This tool takes the
    name you already know — "python", "rust", "postgres" — resolves it, and searches what
    it matches, so the listing round-trip is not needed.

    Scoping matters more than it looks. Dash matches entry names and has no notion of what
    the caller meant, so an unscoped query returns whichever docset happens to contain the
    word: searching every installed docset for "yield" returns Rust and Haskell before
    Python. Naming the docset is how intent is expressed.

    Args:
        query: The search query string
        docset_filter: Name, platform tag or identifier of the docsets to search, matched
            case-insensitively as a substring — "python" finds Python, Pythonista and
            IPython; "cheatsheet" finds all of them
        search_snippets: Whether to include snippets in search results
        max_results: Maximum number of results to return (1-1000)

    The docsets the filter resolved to are named in `searched_docsets`, so a filter that
    matched more or fewer than intended is visible rather than silent. When the filter
    matches nothing, `error` says so and names any near-misses; nothing is searched,
    because searching a plausible wrong docset and finding the symbol absent would suggest
    it is undocumented when the docset is simply not installed.
    """
    problems = []
    if not query.strip():
        problems.append("Query cannot be empty")
    if not docset_filter.strip():
        problems.append(
            "docset_filter cannot be empty. Name the language, library or platform to "
            "search, e.g. 'python'. To search by identifier instead, use "
            "search_documentation"
        )
    if max_results < 1 or max_results > 1000:
        problems.append("max_results must be between 1 and 1000")

    if problems:
        for problem in problems:
            await ctx.error(problem)
        return SearchResults(error="; ".join(problems))

    listing = await list_installed_docsets(ctx, filter=docset_filter)
    if listing.error is not None:
        return SearchResults(error=listing.error)

    if not listing.docsets:
        message = f"No installed docset matches '{docset_filter}'."
        if listing.suggestions:
            names = ", ".join(docset.name for docset in listing.suggestions)
            message += f" Did you mean: {names}?"
        else:
            message += (
                " Run list_installed_docsets to see what is installed, or install the "
                "docset in Dash."
            )
        await ctx.error(message)
        return SearchResults(error=message)

    identifiers = ",".join(docset.identifier for docset in listing.docsets)
    await ctx.debug(f"Filter '{docset_filter}' resolved to: {identifiers}")

    results = await search_documentation(
        ctx, query, identifiers, search_snippets, max_results
    )
    results.searched_docsets = [
        SearchedDocset(name=docset.name, platform=docset.platform)
        for docset in listing.docsets
    ]
    return results


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


@mcp.tool()
async def load_documentation_page(ctx: Context, load_url: str) -> DocumentationPage:
    """
    Load a documentation page from a load_url returned by search_documentation.

    Args:
        load_url: The load_url value from a search result (must point to the local Dash API at 127.0.0.1)

    Returns:
        The documentation page content as plain text with markdown-style links
    """
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
        content = html_to_text(cleaned_html)
        await ctx.info(
            f"Successfully loaded documentation page ({len(content)} characters)"
        )
        return DocumentationPage(content=content, load_url=load_url)

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
