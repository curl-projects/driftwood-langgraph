from __future__ import annotations

from typing import Optional, List, Dict, Any, Tuple
from pydantic import BaseModel, Field
from langchain_core.tools import tool
import os
import httpx
import asyncio
import re
from urllib.parse import urlsplit
import time
import uuid


def _backend_base() -> Optional[str]:
    b = os.getenv("BACKEND_BASE_URL", "").strip()
    return b or None


class FetchEverythingArgs(BaseModel):
    url: Optional[str] = Field(default=None, description="Primary URL to fetch from")
    urls: Optional[List[str]] = Field(default=None, description="Optional list of URLs to consider")
    fieldId: Optional[str] = Field(default=None, description="Target field id (e.g., 'image')")
    contentType: Optional[str] = Field(default=None, description="Current content subtype (e.g., 'images', 'longform')")
    targetContract: Optional[str] = Field(default=None, description="Override planner with a specific contract: media | article | thread | transcript | generic")
    # Bundle mode: the agent plans fields it intends to change; we infer contracts and fetch once
    fields: Optional[List[str]] = Field(default=None, description="List of fieldIds to fill; enables bundle mode and contract inference")
    contracts: Optional[List[str]] = Field(default=None, description="Explicit list of contracts to fetch in bundle mode; deduped with inferred contracts")


_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}
_METADATA_CACHE: Dict[str, Dict[str, Any]] = {}


async def _load_form_schema(subtype: str) -> Optional[Dict[str, Any]]:
    cached = _SCHEMA_CACHE.get(subtype)
    if isinstance(cached, dict) and cached:
        return cached
    base = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
    token = os.getenv("BACKEND_INGEST_TOKEN", "")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Internal-Token"] = token
    url = f"{base}/api/v1/forms/schema?subtype={subtype}"
    timeout = httpx.Timeout(15.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code >= 400:
            return None
        data = resp.json()
        if isinstance(data, dict) and data.get("schema"):
            _SCHEMA_CACHE[subtype] = data
            return data
        return None
    except Exception:
        return None


def _classify_field_mode(schema_doc: Dict[str, Any], field_id: str) -> str:
    """Return 'media' | 'markdown' | 'general' based on schema for the field."""
    schema = schema_doc.get("schema") if isinstance(schema_doc, dict) else None
    props = (schema or {}).get("properties") if isinstance(schema, dict) else None
    field_schema = (props or {}).get(field_id) if isinstance(props, dict) else None
    if isinstance(field_schema, dict):
        # Media: anyOf includes a ref to StagedMedia
        any_of = field_schema.get("anyOf")
        if isinstance(any_of, list):
            for opt in any_of:
                if isinstance(opt, dict) and opt.get("$ref") == "#/$defs/StagedMedia":
                    return "media"
        # Markdown: x-kind indicates markdown with attach tokens
        if field_schema.get("x-kind") == "markdownWithAttachTokens":
            return "markdown"
    return "general"


async def _plan_field_fetch(subtype: Optional[str], field_id: Optional[str]) -> Optional[str]:
    if not field_id:
        return "general"
    if not (isinstance(subtype, str) and subtype.strip()):
        # Caller should validate this; return None to indicate invalid state
        return None
    schema_doc = await _load_form_schema(subtype.strip())
    if not schema_doc:
        # Do not fallback silently; indicate failure by returning None
        return None
    return _classify_field_mode(schema_doc, field_id)


def _infer_contract_from_schema(schema_doc: Optional[Dict[str, Any]], field_id: Optional[str]) -> Optional[str]:
    try:
        if not schema_doc or not field_id:
            return None
        schema = schema_doc.get("schema") if isinstance(schema_doc, dict) else None
        props = (schema or {}).get("properties") if isinstance(schema, dict) else None
        field_schema = (props or {}).get(field_id) if isinstance(props, dict) else None
        if not isinstance(field_schema, dict):
            return None
        # Explicit contract
        x_contract = field_schema.get("x-contract")
        if isinstance(x_contract, str) and x_contract.strip():
            return x_contract.strip()
        # Infer: media vs article from existing classifier
        mode = _classify_field_mode(schema_doc, field_id)
        if mode == "media":
            return "media"
        if mode == "markdown":
            return "article"
        return None
    except Exception:
        return None


def _host_for(url: Optional[str]) -> Optional[str]:
    try:
        if not url:
            return None
        return urlsplit(url).netloc.lower()
    except Exception:
        return None


def _infer_contracts_for_fields(schema_doc: Optional[Dict[str, Any]], fields: List[str], url: Optional[str], content_type: Optional[str]) -> Tuple[Dict[str, str], List[str]]:
    """Return (planned_by_field, unique_contracts)."""
    planned: Dict[str, str] = {}
    unique: List[str] = []
    host = _host_for(url)
    for fid in fields:
        c = _infer_contract_from_schema(schema_doc, fid)
        if not c:
            # Fallback inference from existing classifier
            try:
                mode = _classify_field_mode(schema_doc or {}, fid)
                c = 'media' if mode == 'media' else ('article' if mode == 'markdown' else None)
            except Exception:
                c = None
        if not c and host and 'reddit.com' in host and (content_type or '').strip() in ('tweets', 'reddit_thread'):
            c = 'thread'
        # Only coerce to media for videos if this field is actually a media field
        if not c and (content_type or '').strip() == 'videos' and host and ('youtube.com' in host or host == 'youtu.be'):
            try:
                mode2 = _classify_field_mode(schema_doc or {}, fid)
                if mode2 == 'media':
                    c = 'media'
            except Exception:
                pass
        planned[fid] = c or 'generic'
        if planned[fid] not in unique:
            unique.append(planned[fid])
    # Always include generic for metadata context
    if 'generic' not in unique:
        unique.append('generic')
    return planned, unique


def _infer_subtype_from_fields(fields: Optional[List[str]]) -> Optional[str]:
    try:
        if not isinstance(fields, list) or not fields:
            return None
        low = [str(f or '').strip().lower() for f in fields]
        if 'videofile' in low or 'poster' in low:
            return 'videos'
        if 'image' in low:
            return 'images'
        if 'ingredients' in low or 'steps' in low:
            return 'recipes'
        if 'replies' in low:
            return 'tweets'
        # Common markdown content across many types; do not guess subtype here
        return None
    except Exception:
        return None


def _pick_first(values: List[Optional[str]]) -> Optional[str]:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _parse_oembed_link(html: str) -> Optional[str]:
    try:
        # Find <link rel="alternate" type="application/json+oembed" href="...">
        m = re.search(r"<link[^>]+type=\"application/(?:json\+)?oembed\"[^>]*>", html, re.IGNORECASE)
        if not m:
            return None
        tag = m.group(0)
        href_m = re.search(r"href=\"([^\"]+)\"", tag, re.IGNORECASE)
        if href_m:
            return href_m.group(1)
        return None
    except Exception:
        return None


async def _fetch_oembed_for(url: str, client: httpx.AsyncClient, time_budget_s: float = 2.0) -> Dict[str, Any]:
    # Try discovery
    try:
        r = await client.get(url, headers={"User-Agent": _ua()}, timeout=time_budget_s)
        if r.status_code < 400:
            href = _parse_oembed_link(r.text or "")
            if isinstance(href, str) and href:
                if href.startswith("//"):
                    href = ("https:" if urlsplit(url).scheme == "https" else "http:") + href
                o = await client.get(href, headers={"User-Agent": _ua()}, timeout=time_budget_s)
                if o.status_code < 400:
                    try:
                        return {"ok": True, "oembed": o.json()}
                    except Exception:
                        pass
    except Exception:
        pass
    # Try noembed aggregator
    try:
        ne = await client.get(f"https://noembed.com/embed?url={httpx.URL(url)}", headers={"User-Agent": _ua()}, timeout=time_budget_s)
        if ne.status_code < 400:
            data = ne.json()
            # noembed returns {error: ...} on failure
            if isinstance(data, dict) and not data.get("error"):
                return {"ok": True, "oembed": data}
    except Exception:
        pass
    return {"ok": False}


def _parse_meta_tags(html: str) -> Dict[str, Any]:
    res: Dict[str, Any] = {"openGraph": {}, "twitterCard": {}, "html": {}}
    try:
        # OG
        for prop in ("title", "description", "image", "url"):
            m = re.search(rf"<meta[^>]+property=\"og:{prop}\"[^>]+content=\"([^\"]*)\"", html, re.IGNORECASE)
            if m:
                res["openGraph"][f"og:{prop}"] = m.group(1)
        # Twitter
        for name in ("title", "description", "image"):
            m = re.search(rf"<meta[^>]+name=\"twitter:{name}\"[^>]+content=\"([^\"]*)\"", html, re.IGNORECASE)
            if m:
                res["twitterCard"][f"twitter:{name}"] = m.group(1)
        # HTML title and meta description
        m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if m:
            res["html"]["title"] = re.sub(r"\s+", " ", m.group(1)).strip()
        m = re.search(r"<meta[^>]+name=\"description\"[^>]+content=\"([^\"]*)\"", html, re.IGNORECASE)
        if m:
            res["html"]["metaDescription"] = m.group(1)
    except Exception:
        pass
    return res


def _ua() -> str:
    return "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


async def _fetch_site_metadata(url: str, client: httpx.AsyncClient, time_budget_s: float = 2.5) -> Dict[str, Any]:
    try:
        r = await client.get(url, headers={"User-Agent": _ua()}, timeout=time_budget_s)
        if r.status_code >= 400:
            return {"ok": False}
        meta = _parse_meta_tags(r.text or "")
        return {"ok": True, **meta}
    except Exception:
        return {"ok": False}


def _merge_metadata(src_url: str, tav: Optional[Dict[str, Any]], oemb: Optional[Dict[str, Any]], meta: Optional[Dict[str, Any]], *, max_chars: int) -> Tuple[Dict[str, Any], List[str]]:
    sources_used: List[str] = []
    oembed = (oemb or {}).get("oembed") if isinstance(oemb, dict) else None
    og = (meta or {}).get("openGraph") if isinstance(meta, dict) else None
    tw = (meta or {}).get("twitterCard") if isinstance(meta, dict) else None
    html = (meta or {}).get("html") if isinstance(meta, dict) else None

    title = _pick_first([
        (oembed or {}).get("title") if isinstance(oembed, dict) else None,
        (og or {}).get("og:title") if isinstance(og, dict) else None,
        (tw or {}).get("twitter:title") if isinstance(tw, dict) else None,
        (tav or {}).get("title") if isinstance(tav, dict) else None,
        (html or {}).get("title") if isinstance(html, dict) else None,
    ])
    if title:
        sources_used.append("title")

    description = _pick_first([
        (og or {}).get("og:description") if isinstance(og, dict) else None,
        (tw or {}).get("twitter:description") if isinstance(tw, dict) else None,
        (tav or {}).get("description") if isinstance(tav, dict) else None,
        (html or {}).get("metaDescription") if isinstance(html, dict) else None,
    ])
    if description:
        sources_used.append("description")

    # Prefer Tavily for long-form content; otherwise none
    content = None
    if isinstance(tav, dict):
        content = tav.get("content") or tav.get("contentMarkdown") or tav.get("contentText")
    if isinstance(content, str):
        content = content[:max_chars]

    thumbnail = _pick_first([
        (oembed or {}).get("thumbnail_url") if isinstance(oembed, dict) else None,
        (og or {}).get("og:image") if isinstance(og, dict) else None,
        (tw or {}).get("twitter:image") if isinstance(tw, dict) else None,
    ])

    result: Dict[str, Any] = {
        "ok": True,
        **({"title": title} if title else {}),
        **({"description": description} if description else {}),
        **({"contentMarkdown": content, "contentText": content} if isinstance(content, str) and content.strip() else {}),
        "citations": [src_url],
        "coverage": {
            "title": bool(title),
            "description": bool(description),
            "textChars": len(content) if isinstance(content, str) else 0,
        },
        "provenance": {
            "canonicalUrl": (og.get("og:url") if isinstance(og, dict) and og.get("og:url") else src_url),
            "urlsTried": [src_url],
            "sourcesUsed": [s for s in [
                "tavily" if isinstance(tav, dict) else None,
                "oembed" if isinstance(oembed, dict) else None,
                "opengraph" if isinstance(og, dict) else None,
                "twitterCard" if isinstance(tw, dict) else None,
                "html" if isinstance(html, dict) else None,
            ] if s],
        },
        "metadata": {
            **({"oembed": oembed} if isinstance(oembed, dict) else {}),
            **({"openGraph": og} if isinstance(og, dict) else {}),
            **({"twitterCard": tw} if isinstance(tw, dict) else {}),
            **({"html": html} if isinstance(html, dict) else {}),
        },
        **({"thumbnail": thumbnail} if thumbnail else {}),
    }
    return result, sources_used


async def _fetch_general_mode(
    *,
    url: Optional[str],
    urls: Optional[List[str]] = None,
    max_chars: int = 12000,
) -> Dict[str, Any]:
    """Collect Tavily extract and site metadata (oEmbed + OG/Twitter) in parallel and merge."""
    debug_steps: List[Dict[str, Any]] = []
    log_lines: List[str] = []
    start_ts = time.time()
    candidates: List[str] = []
    if url:
        candidates.append(url)
    if urls:
        candidates.extend([u for u in urls if isinstance(u, str)])

    if not candidates:
        return {"ok": False, "error": {"code": "validation", "message": "No URL provided"}, "debug": {"steps": debug_steps}, "log": ["no candidates"]}

    # Try candidates in order; for each, gather metadata in parallel
    for src in candidates:
        try:
            debug_steps.append({"event": "candidate_start", "url": src, "ts": time.time()})
            # Cache check
            cached = _METADATA_CACHE.get(src)
            if isinstance(cached, dict) and cached.get("ok"):
                log_lines.append("metadata cache hit")
                cached.setdefault("debug", {}).setdefault("steps", []).extend(debug_steps)
                cached.setdefault("log", []).extend(log_lines)
                return cached

            timeout = httpx.Timeout(8.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                tavily_key = (os.getenv("TAVILY_API_KEY") or "").strip()
                tav_task = None
                if tavily_key:
                    async def _tavily_call() -> Dict[str, Any]:
                        t0 = time.time()
                        try:
                            body = {"api_key": tavily_key, "url": src}
                            r = await client.post("https://api.tavily.com/extract", json=body)
                            if r.status_code != 200:
                                debug_steps.append({"event": "tavily_http", "status": r.status_code, "ts": time.time(), "elapsed_ms": int((time.time()-t0)*1000)})
                                return {"ok": False}
                            data = r.json()
                            item = None
                            if isinstance(data, dict) and isinstance(data.get("results"), list) and data["results"]:
                                item = data["results"][0]
                            elif isinstance(data, dict):
                                item = data
                            if not isinstance(item, dict):
                                debug_steps.append({"event": "tavily_no_item", "ts": time.time(), "elapsed_ms": int((time.time()-t0)*1000)})
                                return {"ok": False}
                            title = item.get("title") or None
                            content = item.get("content") or item.get("text") or item.get("extracted_content") or ""
                            desc = item.get("description") or item.get("meta_description") or None
                            debug_steps.append({"event": "tavily_ok", "hasTitle": bool(title), "hasDesc": bool(desc), "contentLen": len(content or ""), "ts": time.time(), "elapsed_ms": int((time.time()-t0)*1000)})
                            return {"ok": True, "title": title, "description": desc, "content": content}
                        except Exception as e:
                            debug_steps.append({"event": "tavily_exc", "error": str(e), "ts": time.time()})
                            return {"ok": False}

                    tav_task = _tavily_call()
                oem_task = _fetch_oembed_for(src, client)
                og_task = _fetch_site_metadata(src, client)
                tav_res, oem_res, og_res = await asyncio.gather(
                    (tav_task if tav_task else asyncio.sleep(0, result={"ok": False})),
                    oem_task,
                    og_task,
                )
                debug_steps.append({"event": "oembed_status", "ok": bool((oem_res or {}).get("ok")), "hasTitle": bool(((oem_res or {}).get("oembed") or {}).get("title") if isinstance((oem_res or {}).get("oembed"), dict) else False), "ts": time.time()})
                ogp = (og_res or {}).get("openGraph") if isinstance(og_res, dict) else None
                twp = (og_res or {}).get("twitterCard") if isinstance(og_res, dict) else None
                htmlp = (og_res or {}).get("html") if isinstance(og_res, dict) else None
                debug_steps.append({"event": "og_status", "ok": bool((og_res or {}).get("ok")), "ogTitle": bool((ogp or {}).get("og:title") if isinstance(ogp, dict) else False), "twitterTitle": bool((twp or {}).get("twitter:title") if isinstance(twp, dict) else False), "htmlTitle": bool((htmlp or {}).get("title") if isinstance(htmlp, dict) else False), "ts": time.time()})

                merged, _sources = _merge_metadata(src, tav_res if tav_res.get("ok") else None, oem_res if oem_res.get("ok") else None, og_res if og_res.get("ok") else None, max_chars=max_chars)
                if merged.get("title") or merged.get("contentMarkdown") or merged.get("description"):
                    _METADATA_CACHE[src] = merged
                    log_lines.append("general: merged metadata ok")
                    merged.setdefault("debug", {}).setdefault("steps", []).extend(debug_steps)
                    merged.setdefault("log", []).extend(log_lines)
                    merged.setdefault("timings", {})["totalMs"] = int((time.time()-start_ts)*1000)
                    return merged
        except Exception:
            continue

    return {
        "ok": False,
        "error": {"code": "not_found", "message": "No metadata or content could be extracted"},
        "citations": candidates,
        "debug": {"steps": debug_steps},
        "log": log_lines,
        "timings": {"totalMs": int((time.time()-start_ts)*1000)},
    }


async def _fetch_media_mode(
    *,
    url: Optional[str],
    urls: Optional[List[str]] = None,
    field_id: Optional[str] = None,
    max_images: int = 4,
) -> Dict[str, Any]:
    """Simplified media fetching for content form enricher."""
    debug_steps: List[Dict[str, Any]] = []
    log_lines: List[str] = []
    start_ts = time.time()
    base = _backend_base()
    if not base:
        return {"ok": False, "error": {"code": "config", "message": "BACKEND_BASE_URL not set"}, "debug": {"steps": debug_steps}, "log": ["missing BACKEND_BASE_URL"]}

    tried: List[str] = []
    sources: List[str] = []
    if url:
        sources.append(url)
    if urls:
        sources.extend([u for u in urls if isinstance(u, str)])

    timeout = httpx.Timeout(45.0)
    backend_errors: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for src in sources:
            if not isinstance(src, str) or not src.strip():
                continue
            tried.append(src)
            try:
                t0 = time.time()
                req_id = uuid.uuid4().hex
                resp = await client.post(
                    f"{base}/api/v1/ai/enrichment/fetch-media",
                    headers={"Content-Type": "application/json", "X-Request-Id": req_id},
                    json={
                        "url": src,
                        "fieldId": field_id,
                    },
                )
                debug_steps.append({"event": "fetch_media_http", "status": resp.status_code, "ts": time.time(), "elapsed_ms": int((time.time()-t0)*1000), "url": src})
                if resp.status_code < 400:
                    data = resp.json()
                    if isinstance(data, dict) and data.get("ok"):
                        attachments = []
                        if isinstance(data.get("staged"), dict):
                            attachments = [
                                {
                                    "tokenId": "img0",
                                    "staged": data["staged"],
                                }
                            ]
                        elif isinstance(data.get("attachments"), list):
                            attachments = data.get("attachments")
                        elif isinstance(data.get("data"), dict) and isinstance(data["data"].get("attachments"), list):
                            attachments = data["data"]["attachments"]
                        elif isinstance(data.get("data"), list):
                            attachments = data.get("data")
                        if attachments:
                            log_lines.append("media: attachments returned")
                            return {
                                "ok": True,
                                "fieldId": field_id,
                                "attachments": attachments if isinstance(attachments, list) else [attachments],
                                "selected": 0,
                                "coverage": {"tried": len(tried), "succeeded": 1, "directMediaFound": True},
                                "provenance": {"primaryUrl": tried[0] if tried else src, "urlsTried": tried},
                                "debug": {"steps": debug_steps},
                                "log": log_lines,
                                "timings": {"totalMs": int((time.time()-start_ts)*1000)},
                            }
                # Non-2xx: capture body preview/JSON
                try:
                    body_preview: Any
                    ct = resp.headers.get("content-type", "")
                    if "application/json" in ct:
                        body_preview = resp.json()
                    else:
                        text = resp.text
                        body_preview = (text[:800] + ("…" if len(text) > 800 else "")) if isinstance(text, str) else None
                except Exception:
                    body_preview = None
                backend_errors.append({
                    "code": "backend_error",
                    "message": f"HTTP {resp.status_code} from fetch-media",
                    "status": resp.status_code,
                    "requestId": req_id,
                    "bodyPreview": body_preview,
                    "url": src,
                })
            except Exception as e:
                debug_steps.append({"event": "fetch_media_exc", "error": str(e), "ts": time.time(), "url": src})
                continue

    return {
        "ok": False,
        "fieldId": field_id,
        "attachments": [],
        "coverage": {"tried": len(tried), "succeeded": 0, "directMediaFound": False},
        "provenance": {"primaryUrl": sources[0] if sources else None, "urlsTried": tried},
        "errors": ([{"code": "not_found", "message": "No media could be fetched from provided URLs"}] + backend_errors),
        "debug": {"steps": debug_steps},
        "log": log_lines,
        "timings": {"totalMs": int((time.time()-start_ts)*1000)},
    }


async def _fetch_markdown_mode(
    *,
    url: Optional[str],
    urls: Optional[List[str]] = None,
    field_id: Optional[str] = None,
    max_images: int = 6,
) -> Dict[str, Any]:
    """Simplified markdown fetching for content form enricher."""
    debug_steps: List[Dict[str, Any]] = []
    log_lines: List[str] = []
    start_ts = time.time()
    base = _backend_base()
    if not base:
        return {"ok": False, "error": {"code": "config", "message": "BACKEND_BASE_URL not set"}, "debug": {"steps": debug_steps}, "log": ["missing BACKEND_BASE_URL"]}

    candidates: List[str] = []
    if url:
        candidates.append(url)
    if urls:
        candidates.extend([u for u in urls if isinstance(u, str)])

    timeout = httpx.Timeout(45.0)
    backend_errors: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for src in candidates:
            try:
                t0 = time.time()
                req_id = uuid.uuid4().hex
                resp = await client.post(
                    f"{base}/api/v1/ai/enrichment/extract-article-media",
                    headers={"Content-Type": "application/json", "X-Request-Id": req_id},
                    json={
                        "url": src,
                        "maxImages": max_images,
                        "withStaging": True,
                        "fieldKey": field_id or "content",
                    },
                )
                debug_steps.append({"event": "extract_article_http", "status": resp.status_code, "ts": time.time(), "elapsed_ms": int((time.time()-t0)*1000), "url": src})
                if resp.status_code < 400:
                    data = resp.json()
                    if isinstance(data, dict) and data.get("ok"):
                        return {
                            "ok": True,
                            "fieldId": field_id,
                            "proposedMarkdown": data.get("proposedMarkdown") or "",
                            "attachments": data.get("attachments") or [],
                            "preStaging": data.get("preStaging") or None,
                            "coverage": {
                                "textChars": len((data.get("proposedMarkdown") or "")),
                                "imagesFound": len(data.get("attachments") or []),
                                "imagesStaged": len(data.get("attachments") or []),
                            },
                            "provenance": {"canonicalUrl": data.get("canonicalUrl") or src, "citations": data.get("citations") or [src]},
                            "debug": {"steps": debug_steps},
                            "log": log_lines,
                            "timings": {"totalMs": int((time.time()-start_ts)*1000)},
                        }
                # Non-2xx: capture body preview/JSON
                try:
                    body_preview: Any
                    ct = resp.headers.get("content-type", "")
                    if "application/json" in ct:
                        body_preview = resp.json()
                    else:
                        text = resp.text
                        body_preview = (text[:800] + ("…" if len(text) > 800 else "")) if isinstance(text, str) else None
                except Exception:
                    body_preview = None
                backend_errors.append({
                    "code": "backend_error",
                    "message": f"HTTP {resp.status_code} from extract-article-media",
                    "status": resp.status_code,
                    "requestId": req_id,
                    "bodyPreview": body_preview,
                    "url": src,
                })
            except Exception as e:
                debug_steps.append({"event": "extract_article_exc", "error": str(e), "ts": time.time(), "url": src})
                continue

    return {
        "ok": False,
        "fieldId": field_id,
        "proposedMarkdown": "",
        "attachments": [],
        "coverage": {"textChars": 0, "imagesFound": 0, "imagesStaged": 0},
        "provenance": {"canonicalUrl": candidates[0] if candidates else None, "citations": candidates or []},
        "errors": ([{"code": "not_found", "message": "Could not extract markdown from provided URLs"}] + backend_errors),
        "debug": {"steps": debug_steps},
        "log": log_lines,
        "timings": {"totalMs": int((time.time()-start_ts)*1000)},
    }


@tool("fetch_everything", args_schema=FetchEverythingArgs)
async def fetch_everything(url: Optional[str] = None, urls: Optional[List[str]] = None, fieldId: Optional[str] = None, contentType: Optional[str] = None, targetContract: Optional[str] = None, fields: Optional[List[str]] = None, contracts: Optional[List[str]] = None) -> Dict[str, Any]:
    """Simplified fetch entrypoint for content form enricher.

    media: stage media attachments for media fields
    markdown: return proposedMarkdown + staged inline images
    general: return page content (markdown/text) and citations for reasoning
    """
    # Require contentType when fieldId is provided
    debug_steps: List[Dict[str, Any]] = []
    log_lines: List[str] = []
    t_start = time.time()
    debug_steps.append({"event": "entry", "hasUrl": bool(url), "numUrls": len(urls or []), "fieldId": fieldId, "contentType": contentType, "ts": time.time()})
    if isinstance(fieldId, str) and fieldId.strip() and not (isinstance(contentType, str) and contentType.strip()):
        return {"ok": False, "error": {"code": "validation", "message": "contentType is required when fieldId is provided"}, "debug": {"steps": debug_steps}, "log": log_lines}

    # Contract planning
    planned_contract: Optional[str] = None
    schema_doc: Optional[Dict[str, Any]] = None
    # If contentType is missing or incorrect, infer from fields; else use provided
    eff_subtype = None
    if isinstance(contentType, str) and contentType.strip():
        eff_subtype = contentType.strip()
    if not eff_subtype:
        eff_subtype = _infer_subtype_from_fields(fields)
    if isinstance(eff_subtype, str) and eff_subtype.strip():
        schema_doc = await _load_form_schema(eff_subtype.strip())
    # Bundle mode: plan per field and union contracts
    if isinstance(fields, list) and fields:
        planned_by_field, inferred_contracts = _infer_contracts_for_fields(schema_doc, [f for f in fields if isinstance(f, str) and f.strip()], url, contentType)
        # Merge with explicit contracts if provided
        requested = [c for c in (contracts or []) if isinstance(c, str) and c.strip()]
        union_contracts = []
        for c in inferred_contracts + requested:
            if c and c not in union_contracts:
                union_contracts.append(c)
        debug_steps.append({"event": "planned_contracts", "byField": planned_by_field, "contracts": union_contracts, "ts": time.time()})
        # Execute bundle via contract registry (preserves output shape)
        results: Dict[str, Any] = {"ok": True, "citations": [url] if isinstance(url, str) else [], "provenance": {"urlsTried": [url] if isinstance(url, str) else []}, "debug": {"steps": []}}
        try:
            from .contracts import resolve as _resolve_contracts  # type: ignore
            from .contracts import __init__ as _bootstrap_contracts  # noqa: F401
            contracts_impl = _resolve_contracts(union_contracts + ["generic"])  # always include generic
            # fieldId for collection preference: first requested field if missing
            fid = (fieldId or (fields[0] if fields else None))
            for c in contracts_impl:
                part = await c.collect(url=url, urls=urls, field_id=fid)
                # Shallow merge per contract
                for k, v in (part or {}).items():
                    if k == "debug":
                        results.setdefault("debug", {}).setdefault("steps", []).extend((v or {}).get("steps", []))
                    elif k == "citations":
                        results.setdefault("citations", []).extend([x for x in (v or []) if x not in results.get("citations", [])])
                    elif k == "provenance":
                        results.setdefault("provenance", {}).update(v or {})
                    else:
                        results[k] = v
        except Exception:
            # Fall back to legacy general-only behavior on any error
            gen = await _fetch_general_mode(url=url, urls=urls)
            for k in ("title","description","metadata","thumbnail"):
                if k in gen:
                    results[k] = gen.get(k)
            results.setdefault("citations", []).extend([c for c in gen.get("citations", []) if c not in (results.get("citations") or [])])
            results.setdefault("provenance", {}).update(gen.get("provenance") or {})
            results.setdefault("debug", {}).setdefault("steps", []).extend(gen.get("debug", {}).get("steps", []))
        results.setdefault("plannedContracts", {})
        if isinstance(fields, list) and fields:
            # Include per-field mapping for caller
            results["plannedContracts"] = planned_by_field
        # Merge timing/logs
        results.setdefault("log", []).extend(["bundle: completed"])
        results.setdefault("timings", {})["totalMsAll"] = int((time.time()-t_start)*1000)
        return results
    # 1) explicit override
    if isinstance(targetContract, str) and targetContract.strip():
        planned_contract = targetContract.strip()
    # 2) x-contract on field / inferred from schema
    if not planned_contract:
        inferred = _infer_contract_from_schema(schema_doc, fieldId)
        if inferred:
            planned_contract = inferred
    # 3) domain hint: reddit+tweets => thread
    if not planned_contract:
        host = _host_for(url)
        if host and "reddit.com" in host and (contentType or "").strip() == "tweets":
            planned_contract = "thread"
    # 4) fallback to existing classifier (media/markdown) → media/article; else general
    if not planned_contract and isinstance(fieldId, str) and fieldId.strip():
        mode = _classify_field_mode(schema_doc or {}, fieldId)
        if mode == "media":
            planned_contract = "media"
        elif mode == "markdown":
            planned_contract = "article"
    if not planned_contract:
        planned_contract = "generic"

    debug_steps.append({"event": "planned_contract", "contract": planned_contract, "ts": time.time()})

    # Contract execution mapping (temporary: reuse existing paths)
    # media → _fetch_media_mode; article → _fetch_markdown_mode; generic → _fetch_general_mode
    # thread → for now, fall back to generic until backend adds /fetch-contract
    eff_mode = {
        "media": "media",
        "article": "markdown",
        "generic": "general",
        "transcript": "general",
        "thread": "general",
    }.get(planned_contract or "generic", "general")
    debug_steps.append({"event": "planned_mode", "mode": eff_mode, "ts": time.time()})
    if eff_mode == "media":
        res = await _fetch_media_mode(
            url=url,
            urls=urls,
            field_id=fieldId,
        )
        res.setdefault("debug", {}).setdefault("steps", []).extend(debug_steps)
        res.setdefault("log", []).extend(log_lines)
        res.setdefault("timings", {})["totalMsAll"] = int((time.time()-t_start)*1000)
        return res
    if eff_mode == "markdown":
        res = await _fetch_markdown_mode(
            url=url,
            urls=urls,
            field_id=fieldId,
        )
        res.setdefault("debug", {}).setdefault("steps", []).extend(debug_steps)
        res.setdefault("log", []).extend(log_lines)
        res.setdefault("timings", {})["totalMsAll"] = int((time.time()-t_start)*1000)
        return res
    # general
    res = await _fetch_general_mode(url=url, urls=urls)
    res.setdefault("debug", {}).setdefault("steps", []).extend(debug_steps)
    res.setdefault("log", []).extend(log_lines)
    res.setdefault("timings", {})["totalMsAll"] = int((time.time()-t_start)*1000)
    return res