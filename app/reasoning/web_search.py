"""
Web search for questions a business's own numbers cannot answer (competitors, the market, prices elsewhere).

Only the question text leaves the server towards the search service: never the business's data. Results are
untrusted text and are only ever shown or summarised, never executed or followed.

Configured by the server's settings: WEB_SEARCH_PROVIDER ("tavily" | "brave") and WEB_SEARCH_API_KEY. A blank key
means web search is off and the app behaves exactly as before.
"""
import logging
import httpx
from app.core.config import get_settings

logger = logging.getLogger("business_reality_engine")

MAX_RESULTS = 5
_TIMEOUT_SECONDS = 10.0


def _tavily(query: str, key: str) -> list[tuple]:
    r = httpx.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {key}"}, timeout=_TIMEOUT_SECONDS,
                   json={"query": query, "max_results": MAX_RESULTS, "search_depth": "basic"})
    r.raise_for_status()
    return [(x.get("title"), x.get("url"), x.get("content")) for x in r.json().get("results", [])]


def _brave(query: str, key: str) -> list[tuple]:
    r = httpx.get("https://api.search.brave.com/res/v1/web/search", timeout=_TIMEOUT_SECONDS,
                  headers={"X-Subscription-Token": key, "Accept": "application/json"},
                  params={"q": query, "count": MAX_RESULTS})
    r.raise_for_status()
    return [(x.get("title"), x.get("url"), x.get("description")) for x in r.json().get("web", {}).get("results", [])]


_SEARCHERS = {"tavily": _tavily, "brave": _brave}


def is_configured() -> bool:
    s = get_settings()
    return bool(s.WEB_SEARCH_API_KEY) and s.WEB_SEARCH_PROVIDER in _SEARCHERS


def search(query: str) -> list[dict]:
    """Up to MAX_RESULTS pages as {title, url, snippet}. Empty when web search is off or the service fails
    (logged, never raised): a question must still get its honest answer."""
    if not is_configured():
        return []
    s = get_settings()
    try:
        found = _SEARCHERS[s.WEB_SEARCH_PROVIDER](query, s.WEB_SEARCH_API_KEY)
    except Exception as e:
        logger.warning(f"Web search failed ({s.WEB_SEARCH_PROVIDER}): {e}")
        return []
    pages = [{"title": title.strip()[:200], "url": url, "snippet": (snippet or "").strip()[:500]}
             for title, url, snippet in found if title and url and url.startswith(("http://", "https://"))]
    return pages[:MAX_RESULTS]
