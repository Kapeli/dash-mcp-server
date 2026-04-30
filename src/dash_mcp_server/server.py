import asyncio
from typing import Optional
import html2text
import httpx

import subprocess
import json
from pathlib import Path
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from urllib.parse import urlparse, unquote

mcp = FastMCP("Dash Documentation API")

DASH_API_UNAVAILABLE_ERROR = (
    "Failed to connect to Dash API Server. Please ensure Dash is running and the "
    "API server is enabled (in Dash Settings > Integration), or run "
    "ensure_dash_api_ready to launch Dash and enable the API server."
)

READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

SETUP_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


async def check_api_health(ctx: Context, port: int) -> bool:
    """Check if the Dash API server is responding at the given port."""
    base_url = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{base_url}/health")
            response.raise_for_status()
        await ctx.debug(f"Successfully connected to Dash API at {base_url}")
        return True
    except Exception as e:
        await ctx.debug(f"Health check failed for {base_url}: {e}")
        return False


async def working_api_base_url(ctx: Context) -> Optional[str]:
    """Return the Dash API base URL if it is already running and reachable."""
    if not await check_dash_running():
        await ctx.debug("Dash is not running")
        return None

    port = await get_dash_api_port(ctx)
    if port is None:
        await ctx.debug("Dash API server is not reachable")
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
        with open(status_file) as f:
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


async def check_dash_running() -> bool:
    """Check if Dash app is running by looking for the process."""
    try:
        # Use pgrep to check for Dash process
        result = await asyncio.to_thread(
            subprocess.run, ["pgrep", "-f", "Dash"], capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


async def launch_dash(ctx: Context) -> bool:
    """Launch Dash if it is not already running."""
    if await check_dash_running():
        return True

    await ctx.info("Dash is not running. Launching Dash...")
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["open", "-g", "-j", "-b", "com.kapeli.dashdoc"],
            timeout=10,
        )
        if result.returncode != 0:
            await asyncio.to_thread(
                subprocess.run,
                ["open", "-g", "-j", "-b", "com.kapeli.dash-setapp"],
                check=True,
                timeout=10,
            )
        await asyncio.sleep(4)
        if not await check_dash_running():
            await ctx.error("Failed to launch Dash application")
            return False
        await ctx.info("Dash launched successfully")
        return True
    except subprocess.CalledProcessError:
        await ctx.error("Failed to launch Dash application")
        return False
    except Exception as e:
        await ctx.error(f"Error launching Dash: {e}")
        return False


async def enable_dash_api_server(ctx: Context) -> bool:
    """Enable the Dash API Server preference for direct and Setapp installs."""
    try:
        for domain in ("com.kapeli.dashdoc", "com.kapeli.dash-setapp"):
            await asyncio.to_thread(
                subprocess.run,
                ["defaults", "write", domain, "DHAPIServerEnabled", "YES"],
                check=True,
                timeout=10,
            )
        await asyncio.sleep(2)
        await ctx.info("Dash API Server preference enabled")
        return True
    except Exception as e:
        await ctx.error(f"Failed to enable Dash API Server automatically: {e}")
        return False


async def ensure_dash_ready(
    ctx: Context, launch: bool, enable_api_server: bool
) -> str | None:
    """Explicitly prepare Dash and return a working API base URL."""
    if launch and not await launch_dash(ctx):
        return None

    base_url = await working_api_base_url(ctx)
    if base_url is not None:
        return base_url

    if enable_api_server:
        await ctx.info("Attempting to enable the Dash API Server...")
        if not await enable_dash_api_server(ctx):
            return None
        return await working_api_base_url(ctx)

    return None


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
    truncated: bool = Field(
        description="Whether results were truncated to stay within the token budget",
        default=False,
    )
    returned_count: int = Field(description="Number of docsets returned", default=0)
    total_count: int = Field(
        description="Total number of docsets reported by Dash", default=0
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
    warning: Optional[str] = Field(
        description="Non-fatal warning returned by Dash", default=None
    )
    truncated: bool = Field(
        description="Whether results were truncated to stay within the token budget",
        default=False,
    )
    returned_count: int = Field(description="Number of results returned", default=0)
    total_count: int = Field(
        description="Total number of results reported by Dash", default=0
    )


class DocumentationPage(BaseModel):
    """Documentation page content."""

    content: str = Field(description="The documentation page content")
    load_url: str = Field(description="The URL that was loaded")
    error: Optional[str] = Field(
        description="Error message if there was an issue", default=None
    )


class SetupResult(BaseModel):
    """Result from preparing Dash for MCP access."""

    success: bool = Field(description="Whether Dash API is ready")
    base_url: Optional[str] = Field(
        description="Working local Dash API base URL", default=None
    )
    error: Optional[str] = Field(description="Error message if setup failed", default=None)


class EnableFTSResult(BaseModel):
    """Result from enabling full-text search for a docset."""

    success: bool = Field(description="Whether full-text search was enabled")
    identifier: str = Field(description="Docset identifier requested")
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


def validate_dash_load_url(load_url: str, expected_port: int) -> Optional[str]:
    """Return an error message if a load URL is not a local Dash API URL."""
    try:
        parsed = urlparse(load_url)
    except ValueError:
        return "Invalid URL: load_url is malformed."

    if parsed.scheme != "http":
        return "Invalid URL: load_url must use http."
    if parsed.username or parsed.password:
        return "Invalid URL: load_url must not include credentials."
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        return "Invalid URL: load_url must point to the local Dash API."
    try:
        port = parsed.port
    except ValueError:
        return "Invalid URL: load_url has an invalid port."
    if port != expected_port:
        return "Invalid URL: load_url must use the active Dash API port."
    if not parsed.path:
        return "Invalid URL: load_url must include a documentation path."
    return None


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


@mcp.tool(annotations=SETUP_TOOL_ANNOTATIONS)
async def ensure_dash_api_ready(
    ctx: Context,
    launch_dash_app: bool = True,
    enable_api_server: bool = True,
) -> SetupResult:
    """
    Explicitly prepare Dash for MCP access.

    Args:
        launch_dash_app: Whether to launch Dash if it is not running.
        enable_api_server: Whether to enable the Dash API Server macOS preference.
    """
    base_url = await ensure_dash_ready(ctx, launch_dash_app, enable_api_server)
    if base_url is None:
        return SetupResult(success=False, error=DASH_API_UNAVAILABLE_ERROR)
    return SetupResult(success=True, base_url=base_url)


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
async def list_installed_docsets(ctx: Context) -> DocsetResults:
    """List all installed documentation sets in Dash.

    An empty list is returned if the user has no docsets installed. Results are
    automatically truncated if they would exceed 25,000 tokens.
    """
    try:
        base_url = await working_api_base_url(ctx)
        if base_url is None:
            return DocsetResults(error=DASH_API_UNAVAILABLE_ERROR)
        await ctx.debug("Fetching installed docsets from Dash API")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{base_url}/docsets/list")
            response.raise_for_status()
            result = response.json()

        docsets = result.get("docsets", [])
        await ctx.info(f"Found {len(docsets)} installed docsets")

        # Build result list with token limit checking
        token_limit = 25000
        current_tokens = 100  # Base overhead for response structure
        limited_docsets: list[DocsetResult] = []
        truncated = False

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
                truncated = True
                await ctx.warning(
                    "Token limit reached. Returning "
                    f"{len(limited_docsets)} of {len(docsets)} docsets to stay "
                    "under 25k token limit."
                )
                break

            limited_docsets.append(docset_info)
            current_tokens += docset_tokens

        if len(limited_docsets) < len(docsets):
            await ctx.info(
                f"Returned {len(limited_docsets)} docsets "
                f"(truncated from {len(docsets)} due to token limit)"
            )

        return DocsetResults(
            docsets=limited_docsets,
            truncated=truncated,
            returned_count=len(limited_docsets),
            total_count=len(docsets),
        )

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            await ctx.warning("No docsets found. Install some in Settings > Downloads.")
            return DocsetResults(
                error=(
                    "No docsets found. Instruct the user to install some docsets "
                    "in Settings > Downloads."
                )
            )
        return DocsetResults(error=f"HTTP error: {e}")
    except Exception as e:
        await ctx.error(f"Failed to get installed docsets: {e}")
        return DocsetResults(error=f"Failed to get installed docsets: {e}")


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
async def search_documentation(
    ctx: Context,
    query: str,
    docset_identifiers: list[str],
    search_snippets: bool = True,
    max_results: int = 100,
) -> SearchResults:
    """
    Search for documentation across docset identifiers and snippets.

    Args:
        query: The search query string
        docset_identifiers: Docset identifiers to search in (from list_installed_docsets)
        search_snippets: Whether to include snippets in search results
        max_results: Maximum number of results to return (1-1000)

    Results are automatically truncated if they would exceed 25,000 tokens.
    """
    if not query.strip():
        await ctx.error("Query cannot be empty")
        return SearchResults(error="Query cannot be empty")

    normalized_docset_identifiers = [
        identifier.strip() for identifier in docset_identifiers if identifier.strip()
    ]

    if not normalized_docset_identifiers:
        docset_error = (
            "docset_identifiers cannot be empty. Get the docset identifiers using "
            "list_installed_docsets"
        )
        await ctx.error(docset_error)
        return SearchResults(
            error=docset_error
        )

    if max_results < 1 or max_results > 1000:
        await ctx.error("max_results must be between 1 and 1000")
        return SearchResults(error="max_results must be between 1 and 1000")

    try:
        base_url = await working_api_base_url(ctx)
        if base_url is None:
            return SearchResults(error=DASH_API_UNAVAILABLE_ERROR)

        params = {
            "query": query,
            "docset_identifiers": ",".join(normalized_docset_identifiers),
            "search_snippets": search_snippets,
            "max_results": max_results,
        }

        await ctx.debug(f"Searching Dash API with query: '{query}'")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{base_url}/search", params=params)
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
        limited_results: list[SearchResult] = []
        truncated = False

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
                truncated = True
                await ctx.warning(
                    "Token limit reached. Returning "
                    f"{len(limited_results)} of {len(results)} results to stay "
                    "under 25k token limit."
                )
                break

            limited_results.append(search_result)
            current_tokens += result_tokens

        if len(limited_results) < len(results):
            await ctx.info(
                f"Returned {len(limited_results)} results "
                f"(truncated from {len(results)} due to token limit)"
            )

        return SearchResults(
            results=limited_results,
            warning=warning_message,
            truncated=truncated,
            returned_count=len(limited_results),
            total_count=len(results),
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            error_text = e.response.text
            if "Docset with identifier" in error_text and "not found" in error_text:
                await ctx.error(
                    "Invalid docset identifier. Run list_installed_docsets to see "
                    "available docsets."
                )
                return SearchResults(
                    error=(
                        "Invalid docset identifier. Run list_installed_docsets to "
                        "see available docsets, then use the exact identifier from "
                        "that list."
                    )
                )
            elif "No docsets found" in error_text:
                await ctx.error("No valid docsets found for search.")
                return SearchResults(
                    error=(
                        "No valid docsets found for search. Either provide valid "
                        "docset identifiers from list_installed_docsets, or set "
                        "search_snippets=true to search snippets only."
                    )
                )
            else:
                await ctx.error(f"Bad request: {error_text}")
                return SearchResults(
                    error=(
                        f"Bad request: {error_text}. Please ensure Dash is running "
                        "and the API server is enabled (in Dash Settings > Integration)."
                    )
                )
        elif e.response.status_code == 403:
            error_text = e.response.text
            if "API access blocked due to Dash trial expiration" in error_text:
                await ctx.error(
                    "Dash trial expired. Purchase Dash to continue using the API."
                )
                return SearchResults(
                    error=(
                        "Your Dash trial has expired. Purchase Dash at "
                        "https://kapeli.com/dash to continue using the API. During "
                        "trial expiration, API access is blocked."
                    )
                )
            else:
                await ctx.error(f"Forbidden: {error_text}")
                return SearchResults(
                    error=(
                        f"Forbidden: {error_text}. Please ensure Dash is running "
                        "and the API server is enabled (in Dash Settings > Integration)."
                    )
                )
        await ctx.error(f"HTTP error: {e}")
        return SearchResults(
            error=(
                f"HTTP error: {e}. Please ensure Dash is running and the API server "
                "is enabled (in Dash Settings > Integration)."
            )
        )
    except Exception as e:
        await ctx.error(f"Search failed: {e}")
        return SearchResults(
            error=(
                f"Search failed: {e}. Please ensure Dash is running and the API "
                "server is enabled (in Dash Settings > Integration)."
            )
        )


@mcp.tool(annotations=SETUP_TOOL_ANNOTATIONS)
async def enable_docset_fts(ctx: Context, identifier: str) -> EnableFTSResult:
    """
    Enable full-text search for a specific docset.

    Args:
        identifier: The docset identifier (from list_installed_docsets)

    Returns:
        A structured result describing whether FTS was successfully enabled.
    """
    if not identifier.strip():
        await ctx.error("Docset identifier cannot be empty")
        return EnableFTSResult(
            success=False,
            identifier=identifier,
            error="Docset identifier cannot be empty",
        )

    try:
        base_url = await working_api_base_url(ctx)
        if base_url is None:
            return EnableFTSResult(
                success=False, identifier=identifier, error=DASH_API_UNAVAILABLE_ERROR
            )

        await ctx.debug(f"Enabling FTS for docset: {identifier}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{base_url}/docsets/enable_fts", params={"identifier": identifier}
            )
            response.raise_for_status()
            response.json()

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            await ctx.error(f"Bad request: {e.response.text}")
            return EnableFTSResult(
                success=False,
                identifier=identifier,
                error=f"Bad request: {e.response.text}",
            )
        elif e.response.status_code == 404:
            await ctx.error(f"Docset not found: {identifier}")
            return EnableFTSResult(
                success=False,
                identifier=identifier,
                error=f"Docset not found: {identifier}",
            )
        await ctx.error(f"HTTP error: {e}")
        return EnableFTSResult(
            success=False, identifier=identifier, error=f"HTTP error: {e}"
        )
    except Exception as e:
        await ctx.error(f"Failed to enable FTS: {e}")
        return EnableFTSResult(
            success=False, identifier=identifier, error=f"Failed to enable FTS: {e}"
        )
    return EnableFTSResult(success=True, identifier=identifier)


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
async def load_documentation_page(ctx: Context, load_url: str) -> DocumentationPage:
    """
    Load a documentation page from a load_url returned by search_documentation.

    Args:
        load_url: The load_url value from a search result.

    Returns:
        The documentation page content as plain text with markdown-style links
    """
    try:
        port = await get_dash_api_port(ctx)
        if port is None:
            return DocumentationPage(
                content="", load_url=load_url, error=DASH_API_UNAVAILABLE_ERROR
            )

        url_error = validate_dash_load_url(load_url, port)
        if url_error is not None:
            await ctx.error(url_error)
            return DocumentationPage(
                content="",
                load_url=load_url,
                error=f"{url_error} Only URLs returned by search_documentation are supported.",
            )

        await ctx.debug(f"Loading documentation page: {load_url}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(load_url)
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
                    error=(
                        "Your Dash trial has expired. Purchase Dash at "
                        "https://kapeli.com/dash to continue using the API."
                    ),
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
