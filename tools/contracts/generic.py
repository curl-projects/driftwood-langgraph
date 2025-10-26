from __future__ import annotations

from typing import Optional, Dict, Any, List

import time

from ..fetch_everything import _fetch_general_mode
from .base import Contract


class GenericContract(Contract):
    name = "generic"

    async def collect(self, *, url: Optional[str], urls: Optional[List[str]], field_id: Optional[str]) -> Dict[str, Any]:
        start = time.time()
        gen = await _fetch_general_mode(url=url, urls=urls)
        out: Dict[str, Any] = {"debug": {"steps": []}}
        # Merge top-level metadata and citations similar to existing behavior
        for k in ("citations", "provenance", "metadata", "thumbnail", "title", "description"):
            v = gen.get(k)
            if v is not None:
                out[k] = v
        try:
            out["debug"]["steps"].extend(gen.get("debug", {}).get("steps", []))
        except Exception:
            pass
        out.setdefault("timings", {})["generalMs"] = int((time.time() - start) * 1000)
        return out


