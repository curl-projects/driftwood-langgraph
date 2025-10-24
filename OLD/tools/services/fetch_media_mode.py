from __future__ import annotations

from typing import Optional, List, Dict, Any
import os
import httpx


def _backend_base() -> Optional[str]:
    b = os.getenv("BACKEND_BASE_URL", "").strip()
    return b or None


async def fetch_media_mode(
    *,
    url: Optional[str],
    urls: Optional[List[str]] = None,
    field_id: Optional[str] = None,
    session_id: Optional[str] = None,
    deep_dive_id: Optional[str] = None,
    run_id: Optional[str] = None,
    max_images: int = 4,
) -> Dict[str, Any]:
    base = _backend_base()
    if not base:
        return {"ok": False, "error": {"code": "config", "message": "BACKEND_BASE_URL not set"}}

    tried: List[str] = []
    trace: List[Dict[str, Any]] = []
    sources: List[str] = []
    if url:
        sources.append(url)
    if urls:
        sources.extend([u for u in urls if isinstance(u, str)])

    timeout = httpx.Timeout(45.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for src in sources:
            if not isinstance(src, str) or not src.strip():
                continue
            tried.append(src)
            # First try direct media staging
            try:
                trace.append({"step": "direct_stage_attempt", "url": src})
                resp = await client.post(
                    f"{base}/api/v1/ai/enrichment/fetch-media",
                    headers={"Content-Type": "application/json"},
                    json={
                        "url": src,
                        "fieldId": field_id,
                        "sessionId": session_id,
                        "deepDiveId": deep_dive_id,
                        "runId": run_id,
                    },
                )
                trace.append({"step": "direct_stage_response", "status": resp.status_code})
                if resp.status_code < 400:
                    data = resp.json()
                    if isinstance(data, dict) and data.get("ok"):
                        attachments = []
                        # Newer backend returns { ok: true, staged: { ... } }
                        if isinstance(data.get("staged"), dict):
                            attachments = [
                                {
                                    "tokenId": "img0",
                                    "staged": data["staged"],
                                }
                            ]
                        elif isinstance(data.get("attachments"), list):
                            attachments = data.get("attachments")
                        elif isinstance(data.get("data"), dict) and isinstance(data["data"].get("attachments"), list):
                            attachments = data["data"]["attachments"]
                        elif isinstance(data.get("data"), list):
                            attachments = data.get("data")
                        trace.append({"step": "direct_stage_ok", "attachments": len(attachments)})
                        return {
                            "ok": True,
                            "fieldId": field_id,
                            "attachments": attachments if isinstance(attachments, list) else [attachments],
                            "selected": 0 if attachments else None,
                            "coverage": {"tried": len(tried), "succeeded": 1, "directMediaFound": True},
                            "provenance": {"primaryUrl": tried[0] if tried else src, "urlsTried": tried},
                            "trace": trace,
                        }
            except Exception as e:
                trace.append({"step": "direct_stage_exception", "url": src, "error": str(e)})

            # Fallback: attempt HTML extraction for hero/inline images with staging
            try:
                trace.append({"step": "html_extract_attempt", "url": src})
                resp2 = await client.post(
                    f"{base}/api/v1/ai/enrichment/extract-article-media",
                    headers={"Content-Type": "application/json"},
                    json={
                        "url": src,
                        "maxImages": max_images,
                        "withStaging": True,
                        "fieldKey": field_id or "image",
                        "sessionId": session_id,
                        "deepDiveId": deep_dive_id,
                        "runId": run_id,
                    },
                )
                trace.append({"step": "html_extract_response", "status": resp2.status_code})
                if resp2.status_code < 400:
                    data2 = resp2.json()
                    if isinstance(data2, dict) and data2.get("ok"):
                        atts = data2.get("attachments") or []
                        trace.append({"step": "html_extract_ok", "attachments": len(atts)})
                        if atts:
                            return {
                                "ok": True,
                                "fieldId": field_id,
                                "attachments": atts,
                                "selected": 0,
                                "coverage": {"tried": len(tried), "succeeded": len(atts), "directMediaFound": False},
                                "provenance": {"primaryUrl": tried[0] if tried else src, "urlsTried": tried},
                                "trace": trace,
                            }
            except Exception as e:
                trace.append({"step": "html_extract_exception", "url": src, "error": str(e)})

    return {
        "ok": False,
        "fieldId": field_id,
        "attachments": [],
        "coverage": {"tried": len(tried), "succeeded": 0, "directMediaFound": False},
        "provenance": {"primaryUrl": sources[0] if sources else None, "urlsTried": tried},
        "errors": [{"code": "not_found", "message": "No media could be fetched or extracted from provided URLs"}],
        "trace": trace,
    }



