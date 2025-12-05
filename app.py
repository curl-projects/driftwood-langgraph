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
from tools import tavily_search_tool, propose_field_edits, get_form_schema, planned_fetch_everything, generate_image
from prompts.enricher.system import get_system_prompt as get_enricher_prompt


logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger.setLevel(logging.INFO)


"""Using built-in MessagesState to accumulate messages across nodes."""


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

# Default enricher model and tools
_enricher_default_model = "gpt-4o"
llm = ChatOpenAI(model=_enricher_default_model, streaming=True, **({"api_key": openai_key} if openai_key else {}))
llm_with_tools = llm.bind_tools([tavily_search_tool, propose_field_edits, get_form_schema, planned_fetch_everything, generate_image])

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
    for t in [tavily_search_tool, propose_field_edits, get_form_schema, planned_fetch_everything, generate_image]:
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


# ---- Content Form Enricher graph ----
def build_enricher_graph():
    _enricher_default_model = "gpt-4o"
    
    def _bind_enricher_tools(llm_inst: ChatOpenAI):
        bound = llm_inst.bind_tools([
            tavily_search_tool,
            propose_field_edits,
            get_form_schema,
            planned_fetch_everything,
            generate_image,
        ])
        try:
            logger.info("enricher.tools_bound: %s", [
                getattr(x, "name", None) or "unknown" for x in [tavily_search_tool, propose_field_edits, get_form_schema, planned_fetch_everything, generate_image]
            ])
        except Exception:
            pass
        return bound

    eg = StateGraph(MessagesState)

    async def model_node(state: MessagesState):
        msgs = state.get("messages") or []
        try:
            logger.info("enricher:model_node: start messages_count=%d", len(msgs))
        except Exception:
            pass
        
        # Always prepend the enricher system prompt
        sys_prompt = get_enricher_prompt()
        try:
            logger.info("enricher:system_prompt: len=%d head=%s", len(sys_prompt or ""), (sys_prompt or "")[:160].replace("\n"," "))
        except Exception:
            pass
        msgs = [SystemMessage(content=sys_prompt)] + msgs
        
        # Per-run model override
        override = _extract_llm_model(msgs) or _enricher_default_model
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
    eg.add_node("tools", ToolNode([tavily_search_tool, propose_field_edits, get_form_schema, planned_fetch_everything, generate_image]))
    eg.add_edge("tools", "model")
    # Log routing decision from tools_condition
    def _logged_tools_condition(state: MessagesState):
        try:
            last = (state.get("messages") or [])[-1] if (state.get("messages") or []) else None
            has_tc = bool(getattr(last, "tool_calls", None))
        except Exception:
            has_tc = False
        res = tools_condition(state)
        try:
            logger.info("enricher:route_decision: has_tool_calls=%s -> %s", has_tc, res)
        except Exception:
            pass
        return res
    eg.add_conditional_edges("model", _logged_tools_condition, {"tools": "tools", END: END})
    eg.set_entry_point("model")
    return eg.compile()


enricher_graph = build_enricher_graph()

# Export the enricher graph as the main graph for content form usage
graph = enricher_graph