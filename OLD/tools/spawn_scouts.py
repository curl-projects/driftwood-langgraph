from __future__ import annotations

import os
from typing import List, Optional, Dict, Any

import httpx
from pydantic import BaseModel, Field
from langchain_core.tools import tool


def _get_backend_cfg() -> tuple[str, str]:
    # Read env at call time to avoid import-order issues with load_dotenv
    base = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
    token = os.getenv("BACKEND_INGEST_TOKEN", "")
    return base, token


class Direction(BaseModel):
    label: str = Field(..., description="Human label for this scout direction")
    hint: Optional[str] = Field(default=None, description="Optional hint/seed queries")


class SpawnArgs(BaseModel):
    directions: List[Direction]
    deep_dive_id: Optional[str] = Field(default=None)
    campaign_id: Optional[str] = Field(default=None)
    max_concurrency: Optional[int] = Field(default=1, description="Client hint; server enforces its own cap")


@tool("spawn_scouts", args_schema=SpawnArgs)
async def spawn_scouts(directions: List[Direction], deep_dive_id: Optional[str] = None, campaign_id: Optional[str] = None, max_concurrency: Optional[int] = 1) -> Dict[str, Any]:
    """Spawn one Scout child run per direction via backend spawner.

    Returns: { ok, deepDiveId, children: [{ taskId, runId, threadId, assistant, direction }] }
    """
    BACKEND_BASE_URL, BACKEND_INGEST_TOKEN = _get_backend_cfg()
    # Deterministically inject deep_dive_id if missing using system preamble in the first message
    # The backend sends a system message like: deep_dive_id=XYZ
    try:
        import inspect
        from langchain_core.tools import ToolCall
        # Walk the current frame to find the messages passed into the model
        deep_dive_hint: Optional[str] = None
        for frame_info in inspect.stack():
            loc = frame_info.frame.f_locals
            msgs = loc.get('msgs') or loc.get('messages')
            if isinstance(msgs, list) and msgs:
                first = msgs[0]
                content = getattr(first, 'content', None) or (first.get('content') if isinstance(first, dict) else None)
                role = getattr(first, 'role', None) or (first.get('role') if isinstance(first, dict) else None)
                if role == 'system' and isinstance(content, str) and content.startswith('deep_dive_id='):
                    deep_dive_hint = content.split('=', 1)[1].strip()
                    break
        if not deep_dive_id and deep_dive_hint:
            deep_dive_id = deep_dive_hint
    except Exception:
        pass
    if not BACKEND_BASE_URL:
        return {"ok": False, "error": {"type": "config", "message": "BACKEND_BASE_URL not set"}}
    payload = {
        "deepDiveId": deep_dive_id,
        "directions": [d.model_dump() for d in directions],
        "maxConcurrency": max_concurrency,
        **({"campaignId": campaign_id} if campaign_id else {}),
    }
    headers = {"Content-Type": "application/json"}
    if BACKEND_INGEST_TOKEN:
        headers["X-Internal-Token"] = BACKEND_INGEST_TOKEN
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{BACKEND_BASE_URL}/api/v1/pipeline/spawn-scouts", headers=headers, json=payload)
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = {"status": resp.status_code}
            return {"ok": False, "error": {"type": "upstream", "message": "spawn failed", "detail": detail}}
        data = resp.json()
        return data if isinstance(data, dict) else {"ok": False, "error": {"type": "protocol", "message": "invalid backend response"}}


