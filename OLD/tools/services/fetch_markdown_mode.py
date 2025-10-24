from __future__ import annotations

from typing import Optional, List, Dict, Any
import os
import httpx


def _backend_base() -> Optional[str]:
    b = os.getenv("BACKEND_BASE_URL", "").strip()
    return b or None


async def fetch_markdown_mode(
    *,
    url: Optional[str],
    urls: Optional[List[str]] = None,
    field_id: Optional[str] = None,
    session_id: Optional[str] = None,
    deep_dive_id: Optional[str] = None,
    run_id: Optional[str] = None,
    max_images: int = 6,
) -> Dict[str, Any]:
    base = _backend_base()
    if not base:
        return {"ok": False, "error": {"code": "config", "message": "BACKEND_BASE_URL not set"}}

    candidates: List[str] = []
    if url:
        candidates.append(url)
    if urls:
        candidates.extend([u for u in urls if isinstance(u, str)])

    timeout = httpx.Timeout(45.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for src in candidates:
            try:
                resp = await client.post(
                    f"{base}/api/v1/ai/enrichment/extract-article-media",
                    headers={"Content-Type": "application/json"},
                    json={
                        "url": src,
                        "maxImages": max_images,
                        "withStaging": True,
                        "fieldKey": field_id or "content",
                        "sessionId": session_id,
                        "deepDiveId": deep_dive_id,
                        "runId": run_id,
                    },
                )
                if resp.status_code < 400:
                    data = resp.json()
                    if isinstance(data, dict) and data.get("ok"):
                        return {
                            "ok": True,
                            "fieldId": field_id,
                            "proposedMarkdown": data.get("proposedMarkdown") or "",
                            "attachments": data.get("attachments") or [],
                            "preStaging": data.get("preStaging") or None,
                            "coverage": {
                                "textChars": len((data.get("proposedMarkdown") or "")),
                                "imagesFound": len(data.get("attachments") or []),
                                "imagesStaged": len(data.get("attachments") or []),
                            },
                            "provenance": {"canonicalUrl": data.get("canonicalUrl") or src, "citations": data.get("citations") or [src]},
                        }
            except Exception:
                continue

    return {
        "ok": False,
        "fieldId": field_id,
        "proposedMarkdown": "",
        "attachments": [],
        "coverage": {"textChars": 0, "imagesFound": 0, "imagesStaged": 0},
        "provenance": {"canonicalUrl": candidates[0] if candidates else None, "citations": candidates or []},
        "errors": [{"code": "not_found", "message": "Could not extract markdown from provided URLs"}],
    }






