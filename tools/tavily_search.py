from typing import Optional, List
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from langchain_tavily import TavilySearch
import logging


logger = logging.getLogger(__name__)


class TavilyArgs(BaseModel):
    query: str = Field(..., description="Non-empty search query string")
    search_depth: Optional[str] = Field(default=None, description="Optional search depth: basic | advanced")


class SearchResult(BaseModel):
    title: Optional[str] = Field(default=None)
    url: str
    snippet: Optional[str] = Field(default=None)


class WebSearchResponse(BaseModel):
    ok: bool
    results: Optional[List[SearchResult]] = None
    error: Optional[dict] = None


@tool("tavily_search", args_schema=TavilyArgs)
async def tavily_search_tool(query: str, search_depth: Optional[str] = None) -> dict:
    """Perform a web search via Tavily and return structured results.

    Args:
        query: Non-empty search string.
        search_depth: Optional depth hint ("basic" | "advanced").

    Returns:
        { ok: bool, results?: [{title, url, snippet}], error?: {type, message} }
    """
    try:
        logger.info("tavily_search_tool: entry stripped_len=%d", len((query or "").strip()))
    except Exception:
        pass
    q = (query or "").strip()
    if not q:
        try:
            logger.info("tavily_search_tool: validation_error empty_query")
        except Exception:
            pass
        return WebSearchResponse(ok=False, error={"type": "validation", "message": "tavily_search requires non-empty query"}).model_dump()
    t = TavilySearch(max_results=5)
    data = await t.ainvoke(q)
    results: List[SearchResult] = []
    for r in (data.get("results") or []):
        url = r.get("url")
        if not url:
            continue
        results.append(SearchResult(title=r.get("title"), url=url, snippet=r.get("content")))
    return WebSearchResponse(ok=True, results=[x.model_dump() for x in results]).model_dump()


