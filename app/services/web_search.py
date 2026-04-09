"""Web search via DuckDuckGo Lite — more stable HTML than the full version."""
from typing import List, Dict
import httpx
import re
import html as html_lib


async def web_search(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Search the web and return a list of {title, snippet, url} results."""
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        ) as client:
            # Try DuckDuckGo Lite first (simpler HTML)
            resp = await client.post(
                "https://lite.duckduckgo.com/lite/",
                data={"q": query},
            )
            resp.raise_for_status()
            page = resp.text

        results = _parse_lite(page, max_results)

        if not results:
            print(f"[web_search] No results parsed for query: {query!r} (HTML length={len(page)})")
            # Try fallback: DDG HTML version
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            ) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                )
                resp.raise_for_status()
                page = resp.text
            results = _parse_html(page, max_results)

        print(f"[web_search] query={query!r} -> {len(results)} results")
        return results

    except Exception as e:
        print(f"[web_search] FAILED for query={query!r}: {e}")
        return []


def _parse_lite(html: str, max_results: int) -> List[Dict[str, str]]:
    """Parse DuckDuckGo Lite results page."""
    results = []

    # DDG Lite uses simple table rows with links and snippets
    # Pattern: <a href="URL" class="result-link">TITLE</a> ... <td class="result-snippet">SNIPPET</td>
    link_pattern = re.findall(
        r'<a[^>]+class="result-link"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        html, re.DOTALL
    )
    snippet_pattern = re.findall(
        r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
        html, re.DOTALL
    )

    for i in range(min(len(link_pattern), len(snippet_pattern), max_results)):
        url, title_raw = link_pattern[i]
        snippet_raw = snippet_pattern[i]
        title = _clean_html(title_raw)
        snippet = _clean_html(snippet_raw)
        if title and snippet:
            results.append({"title": title, "snippet": snippet, "url": url})

    return results


def _parse_html(html: str, max_results: int) -> List[Dict[str, str]]:
    """Parse DuckDuckGo HTML version (fallback)."""
    results = []

    # Try multiple regex patterns for robustness
    # Pattern 1: result__a + result__snippet
    blocks = re.findall(
        r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        html, re.DOTALL
    )
    snippets = re.findall(
        r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)>',
        html, re.DOTALL
    )

    for i in range(min(len(blocks), len(snippets), max_results)):
        url, title_raw = blocks[i]
        snippet_raw = snippets[i]
        title = _clean_html(title_raw)
        snippet = _clean_html(snippet_raw)
        if title and snippet:
            results.append({"title": title, "snippet": snippet, "url": url})

    return results


def _clean_html(text: str) -> str:
    """Strip HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    return text.strip()
