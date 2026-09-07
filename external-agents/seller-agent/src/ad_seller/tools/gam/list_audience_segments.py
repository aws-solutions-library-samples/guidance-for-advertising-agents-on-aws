# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""List Audience Segments Tool - List GAM audience segments with IAB taxonomy mapping."""

from typing import Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...config import get_settings

# IAB Audience Taxonomy 1.1 top-level categories for reference
# Full taxonomy available at: https://github.com/InteractiveAdvertisingBureau/Taxonomies
IAB_AUDIENCE_TAXONOMY_1_1 = {
    "1": "Demographics",
    "2": "Interest",
    "3": "Purchase Intent",
    "4": "Life Stage",
    "5": "Seasonal & Event-based",
    "6": "Behaviors",
}


class ListAudienceSegmentsInput(BaseModel):
    """Input schema for listing GAM audience segments."""

    include_third_party: bool = Field(
        default=True,
        description="Include third-party audience segments (e.g., from data providers)",
    )
    name_filter: Optional[str] = Field(
        default=None,
        description="Filter segments by name (partial match)",
    )
    iab_category: Optional[str] = Field(
        default=None,
        description="Filter by IAB Audience Taxonomy 1.1 category (1-6)",
    )
    limit: int = Field(
        default=100,
        description="Maximum number of segments to return",
    )


class ListAudienceSegmentsTool(BaseTool):
    """Tool for listing audience segments from Google Ad Manager.

    Returns first-party and third-party audience segments that can be
    used for targeting line items. Supports mapping to IAB Audience
    Taxonomy 1.1 categories for standardized audience targeting.

    IAB Audience Taxonomy 1.1 Categories:
    1 - Demographics (age, gender, income, education, etc.)
    2 - Interest (hobbies, lifestyle, media preferences)
    3 - Purchase Intent (in-market for products/services)
    4 - Life Stage (new parents, homeowners, etc.)
    5 - Seasonal & Event-based (holiday shoppers, etc.)
    6 - Behaviors (device usage, travel patterns, etc.)

    Full taxonomy: https://github.com/InteractiveAdvertisingBureau/Taxonomies
    """

    name: str = "list_gam_audience_segments"
    description: str = """List audience segments from Google Ad Manager.
    Returns segment IDs, names, sizes, and IAB taxonomy mappings for targeting."""
    args_schema: Type[BaseModel] = ListAudienceSegmentsInput

    def _run(
        self,
        include_third_party: bool = True,
        name_filter: Optional[str] = None,
        iab_category: Optional[str] = None,
        limit: int = 100,
    ) -> str:
        """Execute audience segments listing."""
        settings = get_settings()

        if not settings.gam_enabled:
            return "GAM integration is not enabled. Set GAM_ENABLED=true in your environment."

        if not settings.gam_network_code or not settings.gam_json_key_path:
            return (
                "GAM credentials not configured. Set GAM_NETWORK_CODE and "
                "GAM_JSON_KEY_PATH environment variables."
            )

        try:
            from ...clients import GAMSoapClient

            client = GAMSoapClient(
                network_code=settings.gam_network_code,
                credentials_path=settings.gam_json_key_path,
            )
            client.connect()

            try:
                # Build filter statement
                filters = []
                if name_filter:
                    filters.append(f"name LIKE '%{name_filter}%'")

                filter_statement = " AND ".join(filters) if filters else None

                segments = client.list_audience_segments(
                    filter_statement=filter_statement,
                    limit=limit,
                )

                if not segments:
                    return "No audience segments found matching the criteria."

                # Filter by type if needed
                if not include_third_party:
                    segments = [s for s in segments if s.type.value != "THIRD_PARTY"]

                # Group by IAB category (inferred from segment name/description)
                categorized = self._categorize_segments(segments, iab_category)

                # Format results
                lines = [f"Found {len(segments)} audience segment(s):\n"]

                for category_name, category_segments in categorized.items():
                    if category_segments:
                        lines.append(f"\n{category_name}:")
                        for seg in category_segments:
                            size_str = f"{seg.size:,}" if seg.size else "Unknown"
                            lines.append(
                                f"  - {seg.name} (ID: {seg.id})\n"
                                f"    Type: {seg.type.value}\n"
                                f"    Status: {seg.status.value}\n"
                                f"    Size: {size_str} members"
                            )

                lines.append(
                    "\nTo use these segments, include their IDs in "
                    "audience_segment_ids when creating line items."
                )

                # Add IAB taxonomy reference
                lines.append("\nIAB Audience Taxonomy 1.1 Categories:")
                for cat_id, cat_name in IAB_AUDIENCE_TAXONOMY_1_1.items():
                    lines.append(f"  {cat_id}: {cat_name}")

                return "\n".join(lines)

            finally:
                client.disconnect()

        except ImportError as e:
            return f"GAM SOAP client dependencies not installed: {e}"
        except Exception as e:
            return f"Error listing audience segments: {e}"

    def _categorize_segments(
        self,
        segments: list,
        filter_category: Optional[str] = None,
    ) -> dict:
        """Categorize segments by inferred IAB Audience Taxonomy category."""
        # Keywords for IAB Audience Taxonomy 1.1 categories
        category_keywords = {
            "Demographics": ["age", "gender", "income", "education", "occupation", "household"],
            "Interest": ["interest", "hobby", "lifestyle", "entertainment", "sports", "travel"],
            "Purchase Intent": ["intent", "in-market", "shopping", "buyer", "purchase", "auto"],
            "Life Stage": ["parent", "homeowner", "student", "retired", "married", "family"],
            "Seasonal & Event": [
                "holiday",
                "season",
                "event",
                "summer",
                "winter",
                "back-to-school",
            ],
            "Behaviors": ["device", "app", "mobile", "browser", "visitor", "frequent"],
            "Other": [],
        }

        categorized = {cat: [] for cat in category_keywords.keys()}

        for segment in segments:
            name_lower = segment.name.lower()
            desc_lower = (segment.description or "").lower()
            text = f"{name_lower} {desc_lower}"

            matched = False
            for category, keywords in category_keywords.items():
                if category == "Other":
                    continue
                if any(kw in text for kw in keywords):
                    categorized[category].append(segment)
                    matched = True
                    break

            if not matched:
                categorized["Other"].append(segment)

        # Filter to specific category if requested
        if filter_category:
            iab_to_name = {
                "1": "Demographics",
                "2": "Interest",
                "3": "Purchase Intent",
                "4": "Life Stage",
                "5": "Seasonal & Event",
                "6": "Behaviors",
            }
            target_cat = iab_to_name.get(filter_category)
            if target_cat:
                return {target_cat: categorized.get(target_cat, [])}

        return categorized
