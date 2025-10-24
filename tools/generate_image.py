from __future__ import annotations

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from langchain_core.tools import tool
import os
import httpx


def _backend_base() -> Optional[str]:
    b = os.getenv("BACKEND_BASE_URL", "").strip()
    return b or None


class GenerateImageArgs(BaseModel):
    prompt: str = Field(..., description="Primary prompt text for image generation")
    fieldId: Optional[str] = Field(default=None, description="Target field id (e.g., 'image')")
    negativePrompt: Optional[str] = Field(default=None, description="Optional negative prompt")
    aspect: Optional[str] = Field(default=None, description="Aspect (e.g., 'square', '16:9', '3:4')")
    width: Optional[int] = Field(default=None, description="Explicit width override")
    height: Optional[int] = Field(default=None, description="Explicit height override")
    style: Optional[str] = Field(default=None, description="Optional stylistic guidance")
    model: Optional[str] = Field(default=None, description="Provider model id (e.g., gpt-image-1)")


@tool("generate_image", args_schema=GenerateImageArgs)
async def generate_image(
    prompt: str,
    fieldId: Optional[str] = None,
    negativePrompt: Optional[str] = None,
    aspect: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    style: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Call backend to generate an image and stage it for previews.

    Returns a shape suitable for proposal consumption: { ok, attachments: [{ staged: {...} }] }
    """
    base = _backend_base()
    if not base:
        return {"ok": False, "error": {"code": "config", "message": "BACKEND_BASE_URL not set"}}
    url = f"{base}/api/v1/ai/enrichment/generate-image"
    body = {
        "prompt": prompt,
        **({"fieldId": fieldId} if fieldId else {}),
        **({"negativePrompt": negativePrompt} if negativePrompt else {}),
        **({"aspect": aspect} if aspect else {}),
        **({"width": width} if isinstance(width, int) and width > 0 else {}),
        **({"height": height} if isinstance(height, int) and height > 0 else {}),
        **({"style": style} if style else {}),
        **({"model": model} if model else {}),
    }
    timeout = httpx.Timeout(60.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers={"Content-Type": "application/json"}, json=body)
        if resp.status_code >= 400:
            try:
                err = resp.json()
            except Exception:
                err = {"status": resp.status_code, "body": (resp.text[:500] if isinstance(resp.text, str) else None)}
            return {"ok": False, "error": {"code": "http", "message": "generation failed", "detail": err}}
        data = resp.json()
        if isinstance(data, dict) and data.get("ok"):
            if isinstance(data.get("staged"), dict):
                return {"ok": True, "attachments": [{"staged": data["staged"]}]}
            if isinstance(data.get("attachments"), list):
                return {"ok": True, "attachments": data["attachments"]}
        return data if isinstance(data, dict) else {"ok": False, "error": {"code": "invalid", "message": "unexpected response"}}
    except Exception as e:
        return {"ok": False, "error": {"code": "exception", "message": str(e)}}


