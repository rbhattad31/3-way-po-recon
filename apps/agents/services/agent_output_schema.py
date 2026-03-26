"""Pydantic schema for the standard agent JSON output block."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


VALID_RECOMMENDATION_TYPES = {
    "AUTO_CLOSE",
    "SEND_TO_AP_REVIEW",
    "SEND_TO_PROCUREMENT",
    "SEND_TO_VENDOR_CLARIFICATION",
    "REPROCESS_EXTRACTION",
    "ESCALATE_TO_MANAGER",
}


class DecisionSchema(BaseModel):
    decision: str = ""
    rationale: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class AgentOutputSchema(BaseModel):
    reasoning: str = ""
    recommendation_type: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    decisions: List[DecisionSchema] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    tools_used: List[str] = Field(default_factory=list,
        description="Tool names called during this agent run")

    @field_validator("recommendation_type", mode="before")
    @classmethod
    def validate_rec_type(cls, v):
        if v is None:
            return v
        if v not in VALID_RECOMMENDATION_TYPES:
            return "SEND_TO_AP_REVIEW"
        return v

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    @model_validator(mode="after")
    def clamp_confidence(self):
        self.confidence = max(0.0, min(1.0, self.confidence))
        return self
