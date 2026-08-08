"""
web_search.py — General-purpose web search, the "just search the web and tell me" tool
none of the security-scoped lookups (lookup_cve, search_exploitdb, shodan_lookup) cover.

Zero-API-key by default: scrapes DuckDuckGo's HTML results page (no key, no account,
no rate-limit tier to manage) via beautifulsoup4. If BRAVE_SEARCH_API_KEY is set in .env,
uses the Brave Search API instead — cleaner structured results, still no cost at low
volume, same "local by default, cloud strictly optional" pattern voice.py already
established for STT/TTS.
"""

import os


def web_search(query: str, max_results: int = 5) -> dict:
    """Search the web for `query`, returning up to max_results {title, url, snippet}."""
    query = (query or "").strip()
    if not query:
        return {"query": query, "results": [], "error": "empty query"}

    api_key = os.getenv("BRAVE_SEARCH_API_KEY")
    if api_key:
        return _brave_search(query, max_results, api_key)
    return _duckduckgo_search(query, max_results)


def _brave_search(query: str, max_results: int, api_key: str) -> dict:
    try:
        import requests
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {"query": query, "results": [], "error": f"Brave Search API failed: {e}", "source": "brave"}

    results = [
        {"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("description", "")}
        for item in data.get("web", {}).get("results", [])[:max_results]
    ]
    return {"query": query, "results": results, "source": "brave"}


def _duckduckgo_search(query: str, max_results: int) -> dict:
    try:
        import requests
        from bs4 import BeautifulSoup
        r = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (JarvisAgent/1.0)"},
            timeout=15,
        )
        r.raise_for_status()
    except Exception as e:
        return {"query": query, "results": [], "error": f"DuckDuckGo search failed: {e}", "source": "duckduckgo"}

    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for result_div in soup.select(".result")[:max_results * 2]:  # a few extra get filtered below
        title_el = result_div.select_one(".result__a")
        snippet_el = result_div.select_one(".result__snippet")
        if not title_el or not title_el.get("href"):
            continue
        # separator=" " matters here — DDG's snippet HTML wraps matched keywords in <b>
        # tags with no surrounding whitespace in the text nodes themselves; get_text()'s
        # default (no separator) concatenates adjacent element text directly and runs
        # words together ("tophomenetworksecurity...") instead of a real, readable snippet.
        results.append({
            "title": title_el.get_text(separator=" ", strip=True),
            "url": title_el["href"],
            "snippet": snippet_el.get_text(separator=" ", strip=True) if snippet_el else "",
        })
        if len(results) >= max_results:
            break

    return {"query": query, "results": results, "source": "duckduckgo"}
