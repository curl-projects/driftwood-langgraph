from typing import Any, List, Optional
from pydantic import BaseModel, Field
from langchain_core.tools import tool


class FieldProposal(BaseModel):
    fieldId: str = Field(..., description="Form field key to propose a value for")
    proposedValue: Any = Field(..., description="Proposed value for the field")
    fieldLabel: Optional[str] = Field(default=None, description="Optional human label for the field")
    rationale: Optional[str] = Field(default=None, description="Why this value is proposed")
    confidence: Optional[float] = Field(default=None, description="Confidence in [0,1]")


class ProposeFieldEditsArgs(BaseModel):
    proposals: List[FieldProposal] = Field(..., description="One or more field proposals to apply")


class ProposeFieldEditsResponse(BaseModel):
    ok: bool
    proposals: List[FieldProposal]


@tool("propose_field_edits", args_schema=ProposeFieldEditsArgs)
async def propose_field_edits(proposals: List[FieldProposal]) -> dict:
    """Return one or more field-level proposals for the active content item.

    The response mirrors the input proposals for downstream handling.
    """
    serializable = [p for p in proposals]
    return ProposeFieldEditsResponse(ok=True, proposals=serializable).model_dump()


