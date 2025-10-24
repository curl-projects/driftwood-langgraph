from typing import Any, Dict, List, Optional
import logging
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, END, MessagesState
from langgraph.prebuilt.tool_node import ToolNode, tools_condition
from dotenv import load_dotenv
import os
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field
from json import dumps as _dumps
import json
import requests

# Import tools from package
from tools import tavily_search_tool, emit_candidate, propose_field_edits, spawn_scouts
from prompts.orchestrator import get_system_prompt as get_orchestrator_prompt
from prompts.scout import get_system_prompt as get_scout_prompt
from prompts.enricher import get_system_prompt as get_enricher_prompt
from tools.working_draft import set_current_deep_dive_id as _set_current_deep_dive_id


logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger.setLevel(logging.INFO)


"""Using built-in MessagesState to accumulate messages across nodes."""


"""Removed unused make_web_search helper (rely on tavily_search_tool)."""


def spawn_scouts_tool():
    """
    Tool signature: spawn_scouts(directions: list[dict], campaign_id?: str)
    Behavior: server-side spawner to create one thread/run per direction on the LangGraph server
    and persist to Firestore via a Cloud Function/HTTP endpoint. For now, we OMIT actual calls and just return a plan echo.
    """
    async def _run(directions: list[dict], campaign_id: str | None = None):
        try:
            logger.info("tool.start name=spawn_scouts args_keys=%s", ["directions", "campaign_id"])
        except Exception:
            pass
        # Placeholder: this would POST to a spawner endpoint with {campaignId, directions}
        # Example (commented):
        # spawner_url = os.getenv("SCOUT_SPAWNER_URL")
        # if spawner_url:
        #     resp = requests.post(spawner_url, json={"campaignId": campaign_id, "directions": directions}, timeout=30)
        #     resp.raise_for_status()
        result = {"ok": True, "spawned": 0, "plan": directions, "campaignId": campaign_id}
        try:
            logger.info("tool.end name=spawn_scouts result_keys=%s", list(result.keys()))
        except Exception:
            pass
        return result
    _run.name = "spawn_scouts"
    return _run


# Auto-load .env so OPENAI_API_KEY/TAVILY_API_KEY are available locally
load_dotenv()

# Helper envs
openai_key = os.getenv("OPENAI_API_KEY")
# Log presence (sanitized) of Tavily key for troubleshooting
_tav_key = os.getenv("TAVILY_API_KEY") or ""
try:
    logger.info(
        "env: OPENAI_API_KEY set=%s TAVILY_API_KEY set=%s suffix=%s",
        bool(openai_key),
        bool(_tav_key),
        (_tav_key[-4:] if _tav_key else ""),
    )
except Exception:
    pass
LANGGRAPH_BASE_URL = os.getenv("LANGGRAPH_BASE_URL", "http://localhost:2024").rstrip("/")
LANGGRAPH_API_KEY = os.getenv("LANGGRAPH_API_KEY", "")

# LangSmith tracing optional
if os.getenv("LANGSMITH_API_KEY"):
    os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGCHAIN_TRACING_V2", "true")
    os.environ["LANGSMITH_API_KEY"] = os.getenv("LANGSMITH_API_KEY", "true")

# Default orchestrator model and tools (fallback); per-run model overrides are applied inside nodes
_orchestrator_default_model = "gpt-4o"
llm = ChatOpenAI(model=_orchestrator_default_model, streaming=True, **({"api_key": openai_key} if openai_key else {}))
llm_with_tools = llm.bind_tools([tavily_search_tool, spawn_scouts])

# --- Tool registry diagnostics (startup) ---
try:
    def _schema_for(tool_obj):
        try:
            return getattr(tool_obj, "args_schema").model_json_schema()
        except Exception:
            try:
                return getattr(tool_obj, "args_schema").schema()
            except Exception:
                return {"error": "no_schema"}

    tools_info = []
    for t in [tavily_search_tool]:
        schema = _schema_for(t)
        tools_info.append({
            "name": getattr(t, "name", None) or "unknown",
            "description": getattr(t, "description", None),
            "schema_required": list((schema.get("required") or [])) if isinstance(schema, dict) else None,
            "schema_properties": list(((schema.get("properties") or {}).keys())) if isinstance(schema, dict) else None,
        })
    logger.info("tool_registry: %s", _dumps(tools_info))
except Exception:
    pass


def _extract_llm_model(all_msgs: List[Any]) -> Optional[str]:
    # Look for a system preamble line like llm_model=MODEL
    try:
        for m in reversed(all_msgs):
            role = getattr(m, "role", getattr(m, "type", None))
            content = getattr(m, "content", None)
            if role == "system" and isinstance(content, str) and "llm_model=" in content:
                for line in content.splitlines():
                    if "llm_model=" in line:
                        val = line.split("llm_model=", 1)[-1].strip()
                        if val:
                            return val
    except Exception:
        pass
    return None


async def model_node(state: MessagesState):
    msgs = state.get("messages") or []
    try:
        logger.info("orchestrator:model_node: start messages_count=%d", len(msgs))
        if msgs:
            last = msgs[-1]
            content = getattr(last, "content", "")
            logger.info("orchestrator:model_node: last_message_role=%s content_snippet=%s", getattr(last, "role", getattr(last, "type", "")), str(content)[:120])
    except Exception:
        pass
    # Always prepend the orchestrator system prompt
    msgs = [SystemMessage(content=get_orchestrator_prompt())] + msgs
    # Choose model per-run (fallback to default)
    override = _extract_llm_model(msgs) or _orchestrator_default_model
    orch_llm = ChatOpenAI(model=override, streaming=True, **({"api_key": openai_key} if openai_key else {}))
    orch_llm_with_tools = orch_llm.bind_tools([tavily_search_tool, spawn_scouts])
    res = await orch_llm_with_tools.ainvoke(msgs)
    try:
        tc = getattr(res, "tool_calls", None)
        logger.info("orchestrator:model_node: got response tool_calls=%s content_len=%d", bool(tc), len(getattr(res, "content", "") or ""))
    except Exception:
        pass
    # Optional: compact log of planned tool call for adapter backfill correlation
    try:
        tc = getattr(res, "tool_calls", None) or []
        if tc:
            logger.info("tool_calls.count=%d", len(tc))
            first = tc[0]
            if isinstance(first, dict):
                func = first.get("function") or {}
                name = (func.get("name") or first.get("name") or "").strip()
                args_str = func.get("arguments") or "{}"
                logger.info("tool_schema: tool=%s function_keys=%s raw_args_str_len=%d", name, list(func.keys()), len(str(args_str or "")))
            else:
                func = getattr(first, "function", None)
                name = (getattr(func, "name", None) or getattr(first, "name", None) or "").strip()
                args_str = getattr(func, "arguments", "{}") or "{}"
                try:
                    k = list(getattr(func, "dict", lambda: {} )().keys()) if func else []
                except Exception:
                    k = []
                logger.info("tool_schema: tool=%s function_keys=%s raw_args_str_len=%d", name, k, len(str(args_str or "")))
            try:
                args = json.loads(args_str)
            except Exception:
                args = {}
            logger.info("tool_plan: name=%s args_keys=%s", name, list(args.keys()) if isinstance(args, dict) else "?")
        else:
            logger.info("tool_calls.empty=True")
    except Exception:
        pass
    return {"messages": [res]}


# Build the graph and export as `graph` (entrypoint in langgraph.json)
graph_builder = StateGraph(MessagesState)
graph_builder.add_node("model", model_node)
graph_builder.add_node("tools", ToolNode([tavily_search_tool, spawn_scouts]))
graph_builder.add_edge("tools", "model")
graph_builder.add_conditional_edges("model", tools_condition, {"tools": "tools", END: END})
graph_builder.set_entry_point("model")
graph = graph_builder.compile()


# ---- Scout graph (parallel explorer) ----
def build_scout_graph():
    _scout_default_model = "gpt-4o"

    sg = StateGraph(MessagesState)

    async def model_node(state: MessagesState):
        msgs = state.get("messages") or []
        try:
            logger.info("scout:model_node: start messages_count=%d", len(msgs))
        except Exception:
            pass
        # Always prepend the scout system prompt
        msgs = [SystemMessage(content=get_scout_prompt())] + msgs
        # Per-run model override
        override = _extract_llm_model(msgs) or _scout_default_model
        s_llm = ChatOpenAI(model=override, streaming=True, **({"api_key": openai_key} if openai_key else {}))
        s_llm_with_tools = s_llm.bind_tools([tavily_search_tool, emit_candidate])
        res = await s_llm_with_tools.ainvoke(msgs)
        try:
            tc = getattr(res, "tool_calls", None)
            logger.info("scout:model_node: got response tool_calls=%s content_len=%d", bool(tc), len(getattr(res, "content", "") or ""))
        except Exception:
            pass
        return {"messages": [res]}
    sg.add_node("model", model_node)
    sg.add_node("tools", ToolNode([tavily_search_tool, emit_candidate]))
    sg.add_edge("tools", "model")
    sg.add_conditional_edges("model", tools_condition, {"tools": "tools", END: END})
    sg.set_entry_point("model")
    return sg.compile()


scout_graph = build_scout_graph()


# ---- Enricher graph (field proposals) ----
def build_enricher_graph():
    _enricher_default_model = "gpt-4o"
    from tools import fetch_everything
    from tools import init_working_draft, merge_field_proposals, get_working_draft_summary
    from tools import validate_draft, emit_from_draft
    from tools.get_form_schema import get_form_schema
    # validate_content_payload is deprecated in favor of validate_draft
    def _bind_enricher_tools(llm_inst: ChatOpenAI):
        return llm_inst.bind_tools([
            tavily_search_tool,
            init_working_draft,
            merge_field_proposals,
            get_working_draft_summary,
            validate_draft,
            fetch_everything,
            emit_from_draft,
            get_form_schema,
        ])

    eg = StateGraph(MessagesState)

    async def model_node(state: MessagesState):
        msgs = state.get("messages") or []
        try:
            logger.info("enricher:model_node: start messages_count=%d", len(msgs))
        except Exception:
            pass
        # Ensure the enricher system prompt is present ONCE at the start
        def _extract_content_type(all_msgs: List[Any]) -> Optional[str]:
            import re as _re
            for m in reversed(all_msgs):
                c = getattr(m, "content", None)
                if not isinstance(c, str) or not c.strip():
                    continue
                # 1) Exact JSON message
                if c.lstrip().startswith("{"):
                    try:
                        obj = json.loads(c)
                    except Exception:
                        obj = None
                    if isinstance(obj, dict):
                        ct = obj.get("contentType") or (obj.get("context") or {}).get("contentType")
                        if isinstance(ct, str) and ct.strip():
                            return ct.strip()
                # 2) Candidate: { ... } pattern
                if "Candidate:" in c:
                    try:
                        idx = c.index("Candidate:") + len("Candidate:")
                        frag = c[idx:].strip()
                        # Extract JSON object substring
                        start = frag.find("{")
                        if start != -1:
                            jtxt = frag[start:]
                            # Trim trailing prose if any by matching a balanced JSON object via a naive heuristic
                            # Fallback: regex to the last closing brace
                            last_brace = jtxt.rfind("}")
                            if last_brace != -1:
                                jtxt = jtxt[: last_brace + 1]
                            obj2 = json.loads(jtxt)
                            if isinstance(obj2, dict):
                                ct2 = obj2.get("contentType")
                                if isinstance(ct2, str) and ct2.strip():
                                    return ct2.strip()
                    except Exception:
                        pass
                # 3) Inline contentType: value pattern anywhere in the text
                try:
                    m_ct = _re.search(r"contentType\s*[:=]\s*([A-Za-z0-9_\-]+)", c, flags=_re.IGNORECASE)
                    if m_ct:
                        return m_ct.group(1)
                except Exception:
                    pass
            return None

        # Derive current subtype from latest message dynamically; do NOT bake into system prompt
        current_subtype = _extract_content_type(msgs) or "unknown"
        enricher_sys = get_enricher_prompt("unknown")
        # Detect if the Enricher system is already present
        has_enricher_sys = False
        try:
            for m in msgs:
                role = getattr(m, "role", getattr(m, "type", None))
                content = getattr(m, "content", "")
                if role == "system" and isinstance(content, str) and "You are the Enricher" in content:
                    has_enricher_sys = True
                    break
        except Exception:
            pass
        if not has_enricher_sys:
            msgs = [SystemMessage(content=enricher_sys)] + msgs
        # Inject lightweight per-turn context line for subtype, without bloating core system prompt
        try:
            msgs.append(SystemMessage(content=f"current_subtype={current_subtype}"))
        except Exception:
            pass
        # Per-run model override
        override = _extract_llm_model(msgs) or _enricher_default_model
        # Extract deep_dive_id from system preamble (provided by backend adapter) and inject for tools
        try:
            ddid_val: Optional[str] = None
            for m in msgs:
                content = getattr(m, "content", None)
                if isinstance(content, str) and "deep_dive_id=" in content:
                    for line in content.splitlines():
                        if "deep_dive_id=" in line:
                            ddid_val = line.split("deep_dive_id=", 1)[-1].strip() or None
                            break
                if ddid_val:
                    break
            _set_current_deep_dive_id(ddid_val)
        except Exception:
            _set_current_deep_dive_id(None)
        e_llm = ChatOpenAI(model=override, streaming=True, **({"api_key": openai_key} if openai_key else {}))
        e_llm_with_tools = _bind_enricher_tools(e_llm)
        res = await e_llm_with_tools.ainvoke(msgs)
        try:
            tc = getattr(res, "tool_calls", None)
            logger.info("enricher:model_node: got response tool_calls=%s content_len=%d", bool(tc), len(getattr(res, "content", "") or ""))
        except Exception:
            pass
        return {"messages": [res]}

    eg.add_node("model", model_node)
    eg.add_node("tools", ToolNode([tavily_search_tool, init_working_draft, merge_field_proposals, get_working_draft_summary, validate_draft, fetch_everything, emit_from_draft, get_form_schema]))
    eg.add_edge("tools", "model")
    eg.add_conditional_edges("model", tools_condition, {"tools": "tools", END: END})
    eg.set_entry_point("model")
    return eg.compile()


enricher_graph = build_enricher_graph()


