"""Crewai-free planning helpers for the Strands buyer A2A runtime.

Extracted verbatim (behavior-preserving) from:
- ``interfaces/agentcore/crew_tools.py`` — ``_parse_brief_from_prompt``
- ``flows/deal_booking_flow.py`` — the audience heuristics (``_create_audience_plan`` /
  ``_estimate_channel_coverage`` / ``_identify_audience_gaps``) rewritten as pure functions.

No agent framework imports — reused by the Strands pipeline (business-rules Q6=A).
"""

import re
import uuid
from typing import Any

_REQUIRED_BRIEF_FIELDS = ["objectives", "budget", "start_date", "end_date", "target_audience"]


def parse_brief_from_prompt(prompt: str) -> dict[str, Any]:
    """Extract a structured campaign brief from a natural-language prompt."""
    budget = 100000.0
    m = re.search(r"\$\s*([\d,]+)\s*K\b", prompt, re.IGNORECASE)
    if m:
        budget = float(m.group(1).replace(",", "")) * 1000
    else:
        m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s*M\b", prompt, re.IGNORECASE)
        if m:
            budget = float(m.group(1).replace(",", "")) * 1_000_000

    start_date, end_date = "2026-10-01", "2026-12-31"
    quarter_map = {
        "q1": ("2026-01-01", "2026-03-31"),
        "q2": ("2026-04-01", "2026-06-30"),
        "q3": ("2026-07-01", "2026-09-30"),
        "q4": ("2026-10-01", "2026-12-31"),
    }
    qm = re.search(r"\b(Q[1-4])\b", prompt, re.IGNORECASE)
    if qm:
        start_date, end_date = quarter_map.get(qm.group(1).lower(), (start_date, end_date))

    audience = "general"
    am = re.search(r"targeting\s+(.+?)(?:\.|,|$)", prompt, re.IGNORECASE)
    if am:
        audience = am.group(1).strip()

    return {
        "name": prompt[:120],
        "objectives": ["awareness", "consideration"],
        "budget": budget,
        "start_date": start_date,
        "end_date": end_date,
        "target_audience": {"description": audience} if isinstance(audience, str) else audience,
    }


def validate_brief(brief: dict[str, Any]) -> list[str]:
    """Return a list of validation errors (empty == valid). Mirrors DealBookingFlow."""
    errors = [f"Missing required field: {f}" for f in _REQUIRED_BRIEF_FIELDS if f not in brief]
    if brief.get("budget", 0) <= 0:
        errors.append("Budget must be greater than 0")
    return errors


def create_audience_plan(target_audience: dict[str, Any]) -> dict[str, Any]:
    """Build an audience plan from a target_audience spec (no UCP calls)."""
    demographics = target_audience.get("demographics", {})
    interests = target_audience.get("interests", [])
    behaviors = target_audience.get("behaviors", [])
    exclusions = target_audience.get("exclusions", [])

    signal_types = []
    if demographics:
        signal_types.append("identity")
    if interests or target_audience.get("content_categories"):
        signal_types.append("contextual")
    if behaviors or target_audience.get("intent"):
        signal_types.append("reinforcement")

    return {
        "plan_id": f"plan_{uuid.uuid4().hex[:8]}",
        "target_demographics": demographics,
        "target_interests": interests if isinstance(interests, list) else [],
        "target_behaviors": behaviors if isinstance(behaviors, list) else [],
        "exclusions": exclusions if isinstance(exclusions, list) else [],
        "requested_signal_types": signal_types,
        "audience_expansion_enabled": target_audience.get("expand_audience", True),
        "expansion_factor": target_audience.get("expansion_factor", 1.0),
    }


def estimate_channel_coverage(target_audience: dict[str, Any]) -> dict[str, float]:
    """Estimate audience coverage per channel (heuristic, unchanged from source)."""
    base_factors = {"branding": 0.85, "ctv": 0.65, "mobile_app": 0.70, "performance": 0.80}
    penalty = 0.0
    if target_audience.get("demographics"):
        penalty += 0.10
    if target_audience.get("behaviors"):
        penalty += 0.20
    if target_audience.get("interests"):
        penalty += 0.05
    return {ch: round(max(0.1, base - penalty) * 100, 1) for ch, base in base_factors.items()}


CHANNELS = ["branding", "mobile_app", "ctv", "performance"]


def clamp_allocations(raw: dict[str, dict], total: float) -> dict[str, dict]:
    """Clamp per-channel budgets so the aggregate never exceeds ``total`` (BR-2).

    ``raw`` maps channel -> {"budget", "rationale"}. Returns only funded channels
    (budget > 0), each with a non-negative budget and a recomputed percentage.
    Guarantees: every budget >= 0, and sum(budgets) <= total. Pure function
    (property-tested).
    """
    import math

    total = max(0.0, float(total or 0))
    out: dict[str, dict] = {}
    running = 0.0  # accumulates the ROUNDED, stored budgets, so sum(stored) == running
    for ch in CHANNELS:
        item = raw.get(ch) or {}
        b = round(max(0.0, float(item.get("budget", 0) or 0)), 2)
        if running + b > total:
            # Floor the remainder to cents so the rounded value can never push the
            # aggregate back over `total` (a naive round() could round up).
            b = math.floor(max(0.0, total - running) * 100) / 100
        if b > 0:
            running += b
            out[ch] = {
                "budget": b,
                "percentage": round((b / total * 100) if total else 0, 1),
                "rationale": item.get("rationale", "") or "",
            }
    return out


def identify_audience_gaps(
    target_audience: dict[str, Any], coverage_estimates: dict[str, float]
) -> list[str]:
    """Identify audience requirements that may have coverage gaps."""
    gaps: list[str] = []
    if target_audience.get("behaviors"):
        gaps.append("behavioral_targeting: coverage may be limited (35-45%)")
    if target_audience.get("demographics", {}).get("income"):
        gaps.append("income_targeting: coverage typically 50-60%")
    for channel, coverage in coverage_estimates.items():
        if coverage < 40:
            gaps.append(f"{channel}: low coverage ({coverage}%), consider broader targeting")
    return gaps
