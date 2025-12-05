from __future__ import annotations

from typing import Optional, List, Dict, Any
import logging
from pydantic import BaseModel, Field
from pydantic import ConfigDict  # pydantic v2
from langchain_core.tools import tool

# Import internal helpers directly to avoid nested tool invocations
from .fetch_everything import (
    _load_form_schema,
    _infer_contracts_for_fields,
    _infer_subtype_from_fields,
    _infer_contract_from_schema,
    _classify_field_mode,
    _fetch_media_mode,
    _fetch_markdown_mode,
    _fetch_general_mode,
)


logger = logging.getLogger(__name__)


class PlannedFetchEverythingArgs(BaseModel):
    # Allow extra keys so LLM-sent args (e.g., targetContract, contracts, fieldId) won't fail validation
    model_config = ConfigDict(extra="ignore")

    url: Optional[str] = Field(default=None, description="Primary URL to fetch from")
    urls: Optional[List[str]] = Field(default=None, description="Optional list of URLs to consider")
    contentType: Optional[str] = Field(default=None, description="Current content subtype (e.g., 'images', 'videos', 'longform')")
    fields: Optional[List[str]] = Field(default=None, description="FieldIds to fill; enables bundle execution")
    # Common optional pass-throughs
    targetContract: Optional[str] = Field(default=None, description="Optional explicit contract override")
    contracts: Optional[List[str]] = Field(default=None, description="Optional list of contracts to union in bundle mode")
    fieldId: Optional[str] = Field(default=None, description="Optional primary field id for collection preference")
    formContext: Optional[Dict[str, Any]] = Field(default=None, description="Current form field values for context")


@tool("planned_fetch_everything", args_schema=PlannedFetchEverythingArgs)
async def planned_fetch_everything(
    url: Optional[str] = None,
    urls: Optional[List[str]] = None,
    contentType: Optional[str] = None,
    fields: Optional[List[str]] = None,
    targetContract: Optional[str] = None,
    contracts: Optional[List[str]] = None,
    fieldId: Optional[str] = None,
    formContext: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Schema-aware fetch wrapper that executes a single bundled fetch and returns a reusable result.

    Consolidated: executes bundle/single planning directly without invoking another tool.
    """
    try:
        logger.info(
            "planned_fetch_everything: entry hasUrl=%s effUrl=%s numUrls=%d contentType=%s fields=%s targetContract=%s contracts=%s fieldId=%s",
            bool(url), (url or (urls[0] if isinstance(urls, list) and urls else None)), len(urls or []), (contentType or ""), fields, targetContract, contracts, fieldId,
        )
    except Exception:
        pass

    # Favor url if provided; else fall back to first of urls for downstream components
    eff_url = url or (urls[0] if isinstance(urls, list) and urls else None)

    # Load schema for subtype when available/inferred
    eff_subtype = (contentType or "").strip() or _infer_subtype_from_fields(fields)
    schema_doc: Optional[Dict[str, Any]] = None
    if isinstance(eff_subtype, str) and eff_subtype:
        schema_doc = await _load_form_schema(eff_subtype)
        try:
            logger.info("PFE.schema: subtype=%s loaded=%s", eff_subtype, bool(schema_doc))
        except Exception:
            pass

    # Bundle planning path when fields are provided
    if isinstance(fields, list) and fields:
        planned_by_field, inferred_contracts = _infer_contracts_for_fields(
            schema_doc, [f for f in fields if isinstance(f, str) and f.strip()], eff_url, contentType
        )
        requested = [c for c in (contracts or []) if isinstance(c, str) and c.strip()]
        union_contracts: List[str] = []
        for c in inferred_contracts + requested:
            if c and c not in union_contracts:
                union_contracts.append(c)
        try:
            logger.info("PFE.bundle.plan: byField=%s contracts=%s", planned_by_field, union_contracts)
        except Exception:
            pass

        # Execute bundle via contract registry (excluding media; we'll handle media per field below)
        results: Dict[str, Any] = {"ok": True, "citations": [eff_url] if isinstance(eff_url, str) else [], "provenance": {"urlsTried": [eff_url] if isinstance(eff_url, str) else []}}
        try:
            from .contracts import resolve as _resolve_contracts  # type: ignore
            try:
                import tools.contracts as _contracts_pkg  # noqa: F401
            except Exception:
                pass
            # Filter out 'media' to avoid running it with an arbitrary field id
            non_media = [c for c in union_contracts if c != "media"]
            contracts_impl = _resolve_contracts(non_media + ["generic"])  # always include generic
            fid = (fieldId or (fields[0] if fields else None))
            for c in contracts_impl:
                part = await c.collect(url=eff_url, urls=urls, field_id=fid, form_values=formContext, schema_doc=schema_doc)
                if not isinstance(part, dict):
                    continue
                for k, v in part.items():
                    if k == "debug":
                        results.setdefault("debug", {}).setdefault("steps", []).extend((v or {}).get("steps", []))
                    elif k == "citations":
                        results.setdefault("citations", []).extend([x for x in (v or []) if x not in results.get("citations", [])])
                    elif k == "provenance":
                        results.setdefault("provenance", {}).update(v or {})
                    else:
                        results[k] = v
        except Exception:
            # Fallback to general
            gen = await _fetch_general_mode(url=eff_url, urls=urls)
            for k in ("title", "description", "metadata", "thumbnail"):
                if isinstance(gen, dict) and k in gen:
                    results[k] = gen.get(k)
            results.setdefault("citations", []).extend([c for c in (gen.get("citations", []) if isinstance(gen, dict) else []) if c not in (results.get("citations") or [])])
            results.setdefault("provenance", {}).update((gen.get("provenance") if isinstance(gen, dict) else {}) or {})
            results.setdefault("debug", {}).setdefault("steps", []).extend((gen.get("debug", {}).get("steps", []) if isinstance(gen, dict) else []))

        results.setdefault("plannedContracts", planned_by_field)

        # Per-field media fetch with explicit mediaKind
        media_by_field: Dict[str, Any] = {}
        flat_media: List[Dict[str, Any]] = []
        for fid2, contr in (planned_by_field or {}).items():
            if contr != "media":
                continue
            mk = None
            try:
                low = str(fid2 or "").strip().lower()
                if "audio" in low:
                    mk = "audio"
                elif "video" in low:
                    mk = "video"
                elif "image" in low or "cover" in low:
                    mk = "image"
            except Exception:
                mk = None
            mm = await _fetch_media_mode(url=eff_url, urls=urls, field_id=fid2, media_kind=mk)
            if isinstance(mm, dict) and mm.get("ok"):
                attachments = mm.get("attachments") or []
                # Coerce kind for audio fields to 'audio' for downstream UI mapping
                if mk == "audio":
                    try:
                        for att in (attachments if isinstance(attachments, list) else [attachments]):
                            st = att.get("staged") if isinstance(att, dict) else None
                            if isinstance(st, dict):
                                st["kind"] = "audio"
                    except Exception:
                        pass
                media_by_field[fid2] = attachments
                if isinstance(attachments, list):
                    flat_media.extend(attachments)
                else:
                    flat_media.append(attachments)
        if media_by_field:
            results["mediaByField"] = media_by_field
            results["media"] = flat_media

        try:
            logger.info("planned_fetch_everything: done ok=%s keys=%s", True, list(results.keys())[:8])
        except Exception:
            pass
        return results

    # Single-field/contract path
    planned_contract: Optional[str] = None
    if isinstance(targetContract, str) and targetContract.strip():
        planned_contract = targetContract.strip()
    if not planned_contract:
        inferred = _infer_contract_from_schema(schema_doc, fieldId)
        if inferred:
            planned_contract = inferred
    if not planned_contract and isinstance(fieldId, str) and fieldId.strip():
        mode = _classify_field_mode(schema_doc or {}, fieldId)
        if mode == "media":
            planned_contract = "media"
        elif mode == "markdown":
            planned_contract = "article"
    if not planned_contract:
        planned_contract = "generic"
    eff_mode = {
        "media": "media",
        "article": "markdown",
        "generic": "general",
        "transcript": "general",
        "thread": "general",
    }.get(planned_contract or "generic", "general")
    try:
        logger.info("PFE.single.mode: %s", eff_mode)
    except Exception:
        pass
    if eff_mode == "media":
        mk_single = None
        try:
            lowf = str(fieldId or "").strip().lower()
            if "audio" in lowf:
                mk_single = "audio"
            elif "video" in lowf:
                mk_single = "video"
            elif "image" in lowf or "cover" in lowf:
                mk_single = "image"
        except Exception:
            mk_single = None
        return await _fetch_media_mode(url=eff_url, urls=urls, field_id=fieldId, media_kind=mk_single)
    if eff_mode == "markdown":
        return await _fetch_markdown_mode(url=eff_url, urls=urls, field_id=fieldId)
    return await _fetch_general_mode(url=eff_url, urls=urls)


