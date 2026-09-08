"""Crewai-free budget-allocation output schema.

Extracted from ``crews/portfolio_crew.py`` so the Strands A2A runtime can use it
as a ``structured_output`` target without importing ``crewai``. Shape is
identical to the CrewAI ``output_pydantic`` model (business-rules BR-2).
"""

from pydantic import BaseModel, Field


class ChannelAllocationOut(BaseModel):
    """Single-channel allocation."""

    budget: float = Field(default=0.0, ge=0)
    percentage: float = Field(default=0.0, ge=0, le=100)
    rationale: str = ""


class BudgetAllocationOutput(BaseModel):
    """Structured output for the budget allocation step (per channel)."""

    branding: ChannelAllocationOut = Field(default_factory=ChannelAllocationOut)
    mobile_app: ChannelAllocationOut = Field(default_factory=ChannelAllocationOut)
    ctv: ChannelAllocationOut = Field(default_factory=ChannelAllocationOut)
    performance: ChannelAllocationOut = Field(default_factory=ChannelAllocationOut)
