from __future__ import annotations

from typing import Any, Dict, List, Optional
import os
import httpx
from pydantic import BaseModel, Field
from langchain_core.tools import tool
import logging


# Run-scoped deep dive id injection (set by app on each turn)
_CURRENT_DEEP_DIVE_ID: Optional[str] = None


def set_current_deep_dive_id(value: Optional[str]) -> None:
    global _CURRENT_DEEP_DIVE_ID
    try:
        _CURRENT_DEEP_DIVE_ID = (str(value).strip() or None) if isinstance(value, str) else None
    except Exception:
        _CURRENT_DEEP_DIVE_ID = None


def _cfg() -> tuple[str, str]:
    base = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
    token = os.getenv("BACKEND_INGEST_TOKEN", "")
    return base, token


class InitDraftArgs(BaseModel):
    subtype: str = Field(...)
    # Make optional; if omitted, the tool will fetch it from the backend
    formSchema: Optional[Dict[str, Any]] = Field(default=None, description="JSON Schema document for the subtype")
    seed: Optional[Dict[str, Any]] = Field(default=None)


class MergeProposalsArgs(BaseModel):
    draftId: str = Field(...)
    proposals: List[Dict[str, Any]] = Field(default_factory=list)
    citations: Optional[List[str]] = Field(default=None)
    attachments: Optional[List[Dict[str, Any]]] = Field(default=None)


class GetSummaryArgs(BaseModel):
    draftId: str = Field(...)


@tool("init_working_draft", args_schema=InitDraftArgs)
async def init_working_draft(subtype: str, formSchema: Optional[Dict[str, Any]] = None, seed: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Initialize a working draft using a known form schema. Returns { ok, draft: { id, subtype, schemaVersion, requiredMissing } }"""
    base, tok = _cfg()
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["X-Internal-Token"] = tok
    url = f"{base}/api/v1/ai/enrichment/working-draft/init"
    logger = logging.getLogger(__name__)
    # If formSchema not provided, fetch it inline for robustness
    if not isinstance(formSchema, dict) or not formSchema:
        try:
            schema_url = f"{base}/api/v1/forms/schema?subtype={subtype}"
            try:
                logger.info("tool.init_working_draft: fetching schema inline url=%s", schema_url)
            except Exception:
                pass
            async with httpx.AsyncClient(timeout=20) as client:
                sresp = await client.get(schema_url, headers=headers)
            if sresp.status_code >= 400:
                try:
                    detail = sresp.json()
                except Exception:
                    detail = {"status": sresp.status_code}
                try:
                    logger.warning("tool.init_working_draft: schema_fetch_failed status=%s detail=%s", sresp.status_code, detail)
                except Exception:
                    pass
                return {"ok": False, "error": {"type": "upstream", "message": "schema fetch failed", "detail": detail}}
            sdata = sresp.json() or {}
            formSchema = sdata.get("schema") if isinstance(sdata, dict) else None
        except Exception:
            try:
                logger.exception("tool.init_working_draft: exception during inline schema fetch subtype=%s", subtype)
            except Exception:
                pass
            return {"ok": False, "error": {"type": "network", "message": "inline schema fetch failed"}}
    # Prefer injected deep dive id over any model-provided value
    use_ddid = _CURRENT_DEEP_DIVE_ID
    body = {"subtype": subtype, "formSchema": formSchema, **({"seed": seed} if seed is not None else {}), **({"deepDiveId": use_ddid} if isinstance(use_ddid, str) and use_ddid else {})}
    try:
        try:
            logger.info("tool.init_working_draft: start url=%s subtype=%s seedKeys=%s hasSchema=%s", url, subtype, list((seed or {}).keys()), bool(formSchema))
        except Exception:
            pass
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, headers=headers, json=body)
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = {"status": resp.status_code}
            try:
                logger.warning("tool.init_working_draft: http_error status=%s detail=%s", resp.status_code, detail)
            except Exception:
                pass
            return {"ok": False, "error": {"type": "upstream", "message": "init failed", "detail": detail}}
        data = resp.json()
        ok = isinstance(data, dict)
        try:
            did = (data or {}).get("draft", {}).get("id") if isinstance(data, dict) else None
            logger.info("tool.init_working_draft: success subtype=%s ok=%s draftId=%s", subtype, ok, str(did or ""))
        except Exception:
            pass
        return data if ok else {"ok": False, "error": {"type": "protocol", "message": "invalid backend response"}}
    except Exception as e:
        try:
            logger.exception("tool.init_working_draft: exception subtype=%s", subtype)
        except Exception:
            pass
        return {"ok": False, "error": {"type": "network", "message": str(e)}}


@tool("merge_field_proposals", args_schema=MergeProposalsArgs)
async def merge_field_proposals(draftId: str, proposals: List[Dict[str, Any]], citations: Optional[List[str]] = None, attachments: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Merge field proposals, citations, and attachments into a working draft. Returns { ok, delta, summary }."""
    base, tok = _cfg()
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["X-Internal-Token"] = tok
    url = f"{base}/api/v1/ai/enrichment/working-draft/merge-proposals"
    # Normalize proposals to backend shape: { fieldId, proposedValue, ... }
    normalized: List[Dict[str, Any]] = []
    for p in proposals or []:
        try:
            fid = (p.get("fieldId") or p.get("field") or p.get("key") or "").strip()
            if not fid:
                continue
            value = p.get("proposedValue")
            if value is None and ("value" in p):
                value = p.get("value")
            norm: Dict[str, Any] = {"fieldId": fid, "proposedValue": value}
            # Pass through optional provenance-like fields if present
            for k in ("source", "sourceUrl", "tool", "confidence", "rationale"):
                if k in p and p.get(k) is not None:
                    norm[k] = p.get(k)
            normalized.append(norm)
        except Exception:
            continue
    use_ddid = _CURRENT_DEEP_DIVE_ID
    body = {
        "draftId": draftId,
        "proposals": normalized,
        **({"citations": citations} if citations is not None else {}),
        **({"attachments": attachments} if attachments is not None else {}),
        **({"deepDiveId": use_ddid} if isinstance(use_ddid, str) and use_ddid else {}),
    }
    logger = logging.getLogger(__name__)
    try:
        try:
            logger.info("tool.merge_field_proposals: start draftId=%s proposals=%d citations=%d attachments=%d", draftId, len(proposals or []), len(citations or []), len(attachments or []))
        except Exception:
            pass
        # Fetch 'before' draft for debugging/visibility
        before: Optional[Dict[str, Any]] = None
        url_get = f"{base}/api/v1/ai/enrichment/working-draft/{draftId}"
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                gb = await client.get(url_get, headers=headers)
                if gb.status_code < 400:
                    b = gb.json()
                    if isinstance(b, dict):
                        before = b.get("draft") if b.get("ok") else (b.get("draft") or b)
            except Exception:
                before = None
            resp = await client.post(url, headers=headers, json=body)
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = {"status": resp.status_code}
            try:
                logger.warning("tool.merge_field_proposals: http_error status=%s detail=%s", resp.status_code, detail)
            except Exception:
                pass
            return {"ok": False, "error": {"type": "upstream", "message": "merge failed", "detail": detail}}
        data = resp.json()
        # Fetch 'after' draft and include both for visibility
        after: Optional[Dict[str, Any]] = None
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                ga = await client.get(url_get, headers=headers)
                if ga.status_code < 400:
                    a = ga.json()
                    if isinstance(a, dict):
                        after = a.get("draft") if a.get("ok") else (a.get("draft") or a)
        except Exception:
            after = None
        try:
            logger.info("tool.merge_field_proposals: success draftId=%s", draftId)
        except Exception:
            pass
        out: Dict[str, Any] = data if isinstance(data, dict) else {"ok": False, "error": {"type": "protocol", "message": "invalid backend response"}}
        if before is not None:
            out["draftBefore"] = before
        if after is not None:
            out["draftAfter"] = after
        out["normalizedProposals"] = normalized
        return out
    except Exception as e:
        try:
            logger.exception("tool.merge_field_proposals: exception draftId=%s", draftId)
        except Exception:
            pass
        return {"ok": False, "error": {"type": "network", "message": str(e)}}


@tool("get_working_draft_summary", args_schema=GetSummaryArgs)
async def get_working_draft_summary(draftId: str) -> Dict[str, Any]:
    """Fetch a concise summary of the working draft for agent guidance: { requiredMissing, recentlyFilled, attachments, citations }."""
    base, tok = _cfg()
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["X-Internal-Token"] = tok
    url = f"{base}/api/v1/ai/enrichment/working-draft/{draftId}/summary"
    logger = logging.getLogger(__name__)
    try:
        try:
            logger.info("tool.get_working_draft_summary: start draftId=%s", draftId)
        except Exception:
            pass
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            # Also fetch full draft for debugging/visibility in UI
            full: Optional[Dict[str, Any]] = None
            try:
                g = await client.get(f"{base}/api/v1/ai/enrichment/working-draft/{draftId}", headers=headers)
                if g.status_code < 400:
                    d = g.json()
                    if isinstance(d, dict):
                        full = d.get("draft") if d.get("ok") else (d.get("draft") or d)
            except Exception:
                full = None
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = {"status": resp.status_code}
            try:
                logger.warning("tool.get_working_draft_summary: http_error status=%s detail=%s", resp.status_code, detail)
            except Exception:
                pass
            return {"ok": False, "error": {"type": "upstream", "message": "summary failed", "detail": detail}}
        data = resp.json()
        try:
            logger.info("tool.get_working_draft_summary: success draftId=%s", draftId)
        except Exception:
            pass
        out: Dict[str, Any] = data if isinstance(data, dict) else {"ok": False, "error": {"type": "protocol", "message": "invalid backend response"}}
        if full is not None:
            out["draft"] = full
        return out
    except Exception as e:
        try:
            logger.exception("tool.get_working_draft_summary: exception draftId=%s", draftId)
        except Exception:
            pass
        return {"ok": False, "error": {"type": "network", "message": str(e)}}



