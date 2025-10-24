from __future__ import annotations

from typing import Optional, List, Dict, Any
import os
import httpx


async def fetch_general_mode(
    *,
    url: Optional[str],
    urls: Optional[List[str]] = None,
    max_chars: int = 12000,
) -> Dict[str, Any]:
    """Fetch cleaned page text + metadata for model reasoning using Tavily extract.

    Requires TAVILY_API_KEY. No fallback and no chunk/summarize.
    """
    candidates: List[str] = []
    if url:
        candidates.append(url)
    if urls:
        candidates.extend([u for u in urls if isinstance(u, str)])

    if not candidates:
        return {"ok": False, "error": {"code": "validation", "message": "No URL provided"}}

    tavily_key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if not tavily_key:
        return {"ok": False, "error": {"code": "config", "message": "TAVILY_API_KEY not set"}}

    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for idx, src in enumerate(candidates):
            try:
                body = {"api_key": tavily_key, "url": src}
                r = await client.post("https://api.tavily.com/extract", json=body)
                if r.status_code != 200:
                    continue
                data = r.json()
                item = None
                if isinstance(data, dict) and isinstance(data.get("results"), list) and data["results"]:
                    item = data["results"][0]
                elif isinstance(data, dict):
                    item = data
                if not isinstance(item, dict):
                    continue
                title = item.get("title") or None
                content = item.get("content") or item.get("text") or item.get("extracted_content") or ""
                desc = item.get("description") or item.get("meta_description") or None
                if not (isinstance(content, str) and content.strip()):
                    continue
                content = content[:max_chars]
                return {
                    "ok": True,
                    "contentMarkdown": content,
                    "contentText": content,
                    "title": title,
                    "description": desc,
                    "citations": [src],
                    "coverage": {"title": bool(title), "description": bool(desc), "textChars": len(content)},
                    "provenance": {"canonicalUrl": src, "urlsTried": candidates[: idx + 1]},
                }
            except Exception:
                continue

    return {"ok": False, "error": {"code": "not_found", "message": "Tavily extract returned no content"}, "citations": candidates}



