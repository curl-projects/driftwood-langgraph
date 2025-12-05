from __future__ import annotations

from typing import Optional, Dict, Any, List

import time

from ..fetch_everything import _fetch_media_mode
from .base import Contract


class MediaContract(Contract):
    name = "media"

    async def collect(self, *, url: Optional[str], urls: Optional[List[str]], field_id: Optional[str], form_values: Optional[Dict[str, Any]] = None, schema_doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        start = time.time()
        # Derive media kind from field name when possible
        mk: Optional[str] = None
        try:
            fid = (field_id or "").strip().lower()
            if fid:
                if "audio" in fid:
                    mk = "audio"
                elif "video" in fid:
                    mk = "video"
                elif "image" in fid or "cover" in fid:
                    mk = "image"
        except Exception:
            mk = None
        mm = await _fetch_media_mode(url=url, urls=urls, field_id=field_id, media_kind=mk)
        out: Dict[str, Any] = {"debug": {"steps": []}}
        if mm.get("ok"):
            out["media"] = mm.get("attachments") or []
        # Merge debug
        try:
            out["debug"]["steps"].extend(mm.get("debug", {}).get("steps", []))
        except Exception:
            pass
        out.setdefault("timings", {})["mediaMs"] = int((time.time() - start) * 1000)
        return out


