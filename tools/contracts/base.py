from __future__ import annotations

from typing import Protocol, Optional, Dict, Any, List


class Contract(Protocol):
    """Minimal interface for content contracts.

    Each contract collects a portion of a NormalizedContent bundle
    (e.g., media attachments, article markdown) and returns a dict
    that can be merged into the final response.
    """

    name: str

    async def collect(
        self,
        *,
        url: Optional[str],
        urls: Optional[List[str]],
        field_id: Optional[str],
    ) -> Dict[str, Any]:
        raise NotImplementedError


