from typing import Optional
from pydantic import BaseModel, Field
from langchain_core.tools import tool


class EmitCandidateArgs(BaseModel):
    url: str = Field(..., description="Target URL of the candidate")
    contentType: Optional[str] = Field(default=None, description="Structured content subtype, e.g., 'videos', 'images', 'longform'")
    title: Optional[str] = Field(default=None, description="Title for the candidate")
    snippet: Optional[str] = Field(default=None, description="Short snippet/summary")
    source: Optional[str] = Field(default=None, description="Origin/source label")


class EmitCandidateResult(BaseModel):
    ok: bool
    url: str
    contentType: Optional[str] = None
    title: Optional[str] = None
    snippet: Optional[str] = None
    source: Optional[str] = None


@tool("emit_candidate", args_schema=EmitCandidateArgs)
async def emit_candidate(url: str, contentType: Optional[str] = None, title: Optional[str] = None, snippet: Optional[str] = None, source: Optional[str] = None) -> dict:
    """Emit a candidate discovery item for UI display during scouting.

    Returns a structured object with the candidate metadata, including optional contentType.
    """
    return EmitCandidateResult(ok=True, url=url, contentType=contentType, title=title, snippet=snippet, source=source).model_dump()


