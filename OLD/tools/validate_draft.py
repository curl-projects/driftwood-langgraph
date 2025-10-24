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


class ValidateDraftArgs(BaseModel):
    draftId: str = Field(...)


@tool("validate_draft", args_schema=ValidateDraftArgs)
async def validate_draft(draftId: str) -> Dict[str, Any]:
    """Validate the current working draft (by draftId) against its JSON Schema.

    Returns: { ok, errors[], warnings[], subtype }
    """
    base, tok = _cfg()
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["X-Internal-Token"] = tok
    # Fetch full draft
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

        # Validate via backend forms endpoint
        url_val = f"{base}/api/v1/forms/validate"
        body = {"subtype": subtype, "payload": payload, "attachments": attachments}
        v = await client.post(url_val, headers=headers, json=body)
        if v.status_code >= 400:
            try:
                detail = v.json()
            except Exception:
                detail = {"status": v.status_code}
            return {"ok": False, "error": {"type": "upstream", "message": "validation failed", "detail": detail}}
        data = v.json() or {}
        if not isinstance(data, dict):
            return {"ok": False, "error": {"type": "protocol", "message": "invalid validation response"}}
        data["subtype"] = subtype
        return data


