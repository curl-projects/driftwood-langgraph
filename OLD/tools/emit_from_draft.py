from __future__ import annotations

from typing import Any, Dict
import os
import httpx
from pydantic import BaseModel, Field
from langchain_core.tools import tool


def _cfg() -> tuple[str, str]:
    base = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
    token = os.getenv("BACKEND_INGEST_TOKEN", "")
    return base, token


class EmitFromDraftArgs(BaseModel):
    draftId: str = Field(...)


@tool("emit_from_draft", args_schema=EmitFromDraftArgs)
async def emit_from_draft(draftId: str) -> Dict[str, Any]:
    """Emit a staged item from the current working draft (by draftId). Should be called only after validate_draft passes."""
    base, tok = _cfg()
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["X-Internal-Token"] = tok
    # Fetch the draft
    url_get = f"{base}/api/v1/ai/enrichment/working-draft/{draftId}"
    async with httpx.AsyncClient(timeout=20) as client:
        g = await client.get(url_get, headers=headers)
        if g.status_code >= 400:
            try:
                detail = g.json()
            except Exception:
                detail = {"status": g.status_code}
            return {"ok": False, "error": {"type": "upstream", "message": "fetch draft failed", "detail": detail}}
        d = g.json()
        if not isinstance(d, dict) or not (d.get("ok") and isinstance(d.get("draft"), dict)):
            return {"ok": False, "error": {"type": "protocol", "message": "invalid draft response"}}
        draft = d["draft"]
        subtype = draft.get("subtype") or ""
        payload = draft.get("fields") or {}
        attachments = draft.get("attachments") or []

        # Return in the same shape expected by downstream review UI
        return {
            "ok": True,
            "type": "content",
            "subtype": subtype,
            "payload": payload,
            "attachments": attachments,
            "source": {"kind": "working_draft", "draftId": draftId},
        }


