from __future__ import annotations

from typing import Dict, Any
import os
import httpx
from pydantic import BaseModel, Field
from langchain_core.tools import tool
import logging
import asyncio


def _get_backend_cfg() -> tuple[str, str]:
    base = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
    token = os.getenv("BACKEND_INGEST_TOKEN", "")
    return base, token


class GetFormSchemaArgs(BaseModel):
    subtype: str = Field(..., description="Content subtype, e.g., 'longform'")


@tool("get_form_schema", args_schema=GetFormSchemaArgs)
async def get_form_schema(subtype: str) -> Dict[str, Any]:
    """Fetch the JSON Schema for a given content subtype from the backend.

    Returns: { subtype, version, schema }
    """
    BACKEND_BASE_URL, BACKEND_INGEST_TOKEN = _get_backend_cfg()
    headers = {"Content-Type": "application/json"}
    if BACKEND_INGEST_TOKEN:
        headers["X-Internal-Token"] = BACKEND_INGEST_TOKEN
    url = f"{BACKEND_BASE_URL}/api/v1/forms/schema?subtype={subtype}"
    logger = logging.getLogger(__name__)
    attempts = 3
    last_err: Dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        try:
            try:
                logger.info("tool.get_form_schema: start url=%s attempt=%d", url, attempt)
            except Exception:
                pass
            timeout = httpx.Timeout(30.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                try:
                    detail = resp.json()
                except Exception:
                    detail = {"status": resp.status_code}
                last_err = {"type": "upstream", "message": "schema fetch failed", "detail": detail}
                try:
                    logger.warning("tool.get_form_schema: http_error status=%s detail=%s", resp.status_code, detail)
                except Exception:
                    pass
                await asyncio.sleep(0.4 * attempt)
                continue
            data = resp.json()
            if isinstance(data, dict):
                try:
                    logger.info("tool.get_form_schema: success subtype=%s hasSchema=%s", subtype, bool(data.get("schema")))
                except Exception:
                    pass
                return data
            last_err = {"type": "protocol", "message": "invalid backend response"}
            try:
                logger.error("tool.get_form_schema: invalid_response subtype=%s", subtype)
            except Exception:
                pass
            await asyncio.sleep(0.4 * attempt)
        except Exception as e:
            last_err = {"type": "network", "message": str(e)}
            try:
                logger.exception("tool.get_form_schema: exception subtype=%s", subtype)
            except Exception:
                pass
            await asyncio.sleep(0.4 * attempt)
    return {"ok": False, "error": last_err or {"type": "unknown", "message": "schema fetch failed"}}


