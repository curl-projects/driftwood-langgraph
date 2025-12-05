from __future__ import annotations

from typing import Optional, Dict, Any, List

import time

from ..fetch_everything import _fetch_markdown_mode
from .base import Contract


class ArticleContract(Contract):
    name = "article"

    async def collect(self, *, url: Optional[str], urls: Optional[List[str]], field_id: Optional[str], form_values: Optional[Dict[str, Any]] = None, schema_doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        start = time.time()
        md = await _fetch_markdown_mode(url=url, urls=urls, field_id=field_id)
        out: Dict[str, Any] = {"debug": {"steps": []}}
        if md.get("ok"):
            out["article"] = {
                "markdown": md.get("proposedMarkdown") or "",
                "attachments": md.get("attachments") or [],
            }
        try:
            out["debug"]["steps"].extend(md.get("debug", {}).get("steps", []))
        except Exception:
            pass
        out.setdefault("timings", {})["articleMs"] = int((time.time() - start) * 1000)
        return out


