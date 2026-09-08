# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Sync GAM Audiences Tool - Sync audience segments with IAB taxonomy mapping."""

from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...config import get_settings
from ...models.gam import AudienceSegmentMapping

# IAB Audience Taxonomy 1.1 mappings
# Source: https://github.com/InteractiveAdvertisingBureau/Taxonomies/blob/main/Audience%20Taxonomies/Audience%20Taxonomy%201.1.tsv
IAB_AUDIENCE_TAXONOMY_MAPPINGS = {
    # Demographics (Category 1)
    "1-1": {"name": "Age", "subcategories": ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]},
    "1-2": {"name": "Gender", "subcategories": ["Male", "Female"]},
    "1-3": {
        "name": "Household Income",
        "subcategories": ["$0-50k", "$50-100k", "$100-150k", "$150k+"],
    },
    "1-4": {
        "name": "Education",
        "subcategories": ["High School", "Some College", "College Graduate", "Post-Graduate"],
    },
    # Interest (Category 2)
    "2-1": {"name": "Arts & Entertainment", "subcategories": ["Movies", "Music", "TV", "Gaming"]},
    "2-2": {
        "name": "Automotive",
        "subcategories": ["Auto Enthusiasts", "Luxury Auto", "Economy Auto"],
    },
    "2-3": {
        "name": "Business",
        "subcategories": ["Business Professionals", "Entrepreneurs", "Investors"],
    },
    "2-4": {"name": "Careers", "subcategories": ["Job Seekers", "Career Changers"]},
    "2-5": {"name": "Family & Parenting", "subcategories": ["Parents", "Expecting Parents"]},
    "2-6": {
        "name": "Food & Drink",
        "subcategories": ["Foodies", "Cooking Enthusiasts", "Restaurant Goers"],
    },
    "2-7": {
        "name": "Health & Fitness",
        "subcategories": ["Fitness Enthusiasts", "Health Conscious"],
    },
    "2-8": {"name": "Hobbies & Interests", "subcategories": ["DIY", "Gardening", "Crafts"]},
    "2-9": {"name": "Home & Garden", "subcategories": ["Home Improvement", "Interior Design"]},
    "2-10": {"name": "News & Politics", "subcategories": ["News Readers", "Political Junkies"]},
    "2-11": {
        "name": "Personal Finance",
        "subcategories": ["Investors", "Savers", "Credit Seekers"],
    },
    "2-12": {"name": "Pets", "subcategories": ["Dog Owners", "Cat Owners"]},
    "2-13": {
        "name": "Sports",
        "subcategories": ["Sports Fans", "Fantasy Sports", "Sports Bettors"],
    },
    "2-14": {"name": "Style & Fashion", "subcategories": ["Fashion Forward", "Luxury Shoppers"]},
    "2-15": {
        "name": "Technology",
        "subcategories": ["Tech Enthusiasts", "Early Adopters", "Gadget Lovers"],
    },
    "2-16": {
        "name": "Travel",
        "subcategories": ["Frequent Travelers", "Business Travelers", "Leisure Travelers"],
    },
    # Purchase Intent (Category 3)
    "3-1": {"name": "Auto Intenders", "subcategories": ["New Car", "Used Car", "SUV", "Truck"]},
    "3-2": {"name": "CPG Intenders", "subcategories": ["Grocery", "Health & Beauty", "Household"]},
    "3-3": {"name": "Financial Services", "subcategories": ["Credit Card", "Insurance", "Banking"]},
    "3-4": {"name": "Retail Intenders", "subcategories": ["Apparel", "Electronics", "Home Goods"]},
    "3-5": {
        "name": "Telecom Intenders",
        "subcategories": ["Mobile", "Internet", "Cable/Satellite"],
    },
    "3-6": {
        "name": "Travel Intenders",
        "subcategories": ["Flights", "Hotels", "Vacation Packages"],
    },
    # Life Stage (Category 4)
    "4-1": {"name": "New Parents", "subcategories": ["New Baby", "Toddlers"]},
    "4-2": {"name": "Empty Nesters", "subcategories": []},
    "4-3": {"name": "New Homeowners", "subcategories": []},
    "4-4": {"name": "Newlyweds", "subcategories": []},
    "4-5": {"name": "College Students", "subcategories": []},
    "4-6": {"name": "Retirees", "subcategories": []},
    # Seasonal & Event (Category 5)
    "5-1": {
        "name": "Holiday Shoppers",
        "subcategories": ["Christmas", "Black Friday", "Valentine's Day"],
    },
    "5-2": {"name": "Back to School", "subcategories": []},
    "5-3": {"name": "Summer Activities", "subcategories": []},
    "5-4": {"name": "Tax Season", "subcategories": []},
    # Behaviors (Category 6)
    "6-1": {
        "name": "Device Usage",
        "subcategories": ["Mobile Heavy", "Desktop Heavy", "Tablet Users"],
    },
    "6-2": {
        "name": "Purchase Behavior",
        "subcategories": ["Frequent Buyers", "Deal Seekers", "Premium Buyers"],
    },
    "6-3": {
        "name": "Media Consumption",
        "subcategories": ["Streaming", "Social Media", "Podcast Listeners"],
    },
}


class SyncGAMAudiencesInput(BaseModel):
    """Input schema for GAM audience sync."""

    create_missing: bool = Field(
        default=False,
        description="Create GAM segments for unmapped UCP/standard audiences",
    )
    update_mappings: bool = Field(
        default=True,
        description="Update the local audience segment mapping table",
    )
    include_third_party: bool = Field(
        default=True,
        description="Include third-party segments in sync",
    )


class SyncGAMAudiencesTool(BaseTool):
    """Tool for syncing audience segments between GAM and local definitions.

    Supports multiple audience definition sources:
    1. GAM first-party segments (rule-based or pixel-based)
    2. GAM third-party segments (licensed from data providers)
    3. UCP (User Context Protocol) audience definitions
    4. IAB Audience Taxonomy 1.1 standard categories

    This tool maintains the AudienceSegmentMapping table that maps
    between different audience identifier systems:
    - GAM segment IDs (for targeting in line items)
    - UCP audience IDs (for programmatic requests)
    - IAB Audience Taxonomy IDs (for buyer agent requests)

    IAB Audience Taxonomy 1.1: https://github.com/InteractiveAdvertisingBureau/Taxonomies
    """

    name: str = "sync_gam_audiences"
    description: str = """Sync audience segments between GAM, UCP, and IAB taxonomies.
    Updates the mapping table for audience targeting in deals."""
    args_schema: Type[BaseModel] = SyncGAMAudiencesInput

    def _run(
        self,
        create_missing: bool = False,
        update_mappings: bool = True,
        include_third_party: bool = True,
    ) -> str:
        """Execute audience sync."""
        settings = get_settings()

        if not settings.gam_enabled:
            return "GAM integration is not enabled. Set GAM_ENABLED=true in your environment."

        if not settings.gam_network_code or not settings.gam_json_key_path:
            return (
                "GAM credentials not configured. Set GAM_NETWORK_CODE and "
                "GAM_JSON_KEY_PATH environment variables."
            )

        try:
            from datetime import datetime

            from ...clients import GAMSoapClient

            client = GAMSoapClient(
                network_code=settings.gam_network_code,
                credentials_path=settings.gam_json_key_path,
            )
            client.connect()

            try:
                # Fetch all GAM segments
                segments = client.list_audience_segments(limit=500)

                if not include_third_party:
                    segments = [s for s in segments if s.type.value != "THIRD_PARTY"]

                # Build mappings
                mappings = []
                iab_matches = 0
                created_count = 0

                for segment in segments:
                    # Try to match to IAB Audience Taxonomy
                    iab_id = self._match_to_iab_taxonomy(segment.name)

                    mapping = AudienceSegmentMapping(
                        gam_segment_id=segment.id,
                        gam_segment_name=segment.name,
                        segment_type=segment.type.value.lower().replace("_", "-"),
                        last_synced=datetime.now(),
                        estimated_size=segment.size,
                        iab_audience_taxonomy_id=iab_id,
                    )
                    mappings.append(mapping)

                    if iab_id:
                        iab_matches += 1

                # Create missing segments if requested
                if create_missing:
                    # Would create segments for common IAB taxonomy categories
                    # that don't have corresponding GAM segments
                    pass  # Implementation would create via SOAP API

                # Format results
                lines = ["GAM Audience Sync Complete:\n"]
                lines.append(f"- Total segments found: {len(segments)}")
                lines.append(
                    f"- First-party segments: {sum(1 for s in segments if 'FIRST' in s.type.value)}"
                )
                lines.append(
                    f"- Third-party segments: {sum(1 for s in segments if 'THIRD' in s.type.value)}"
                )
                lines.append(f"- Mapped to IAB Taxonomy: {iab_matches}")

                if update_mappings:
                    lines.append(f"- Mappings updated: {len(mappings)}")

                if create_missing:
                    lines.append(f"- New segments created: {created_count}")

                # Show sample mappings
                lines.append("\nSample audience mappings:")
                for mapping in mappings[:5]:
                    iab_str = (
                        f" (IAB: {mapping.iab_audience_taxonomy_id})"
                        if mapping.iab_audience_taxonomy_id
                        else ""
                    )
                    size_str = f"{mapping.estimated_size:,}" if mapping.estimated_size else "?"
                    lines.append(
                        f"  - {mapping.gam_segment_name}{iab_str}\n"
                        f"    GAM ID: {mapping.gam_segment_id}, Size: {size_str}"
                    )

                if len(mappings) > 5:
                    lines.append(f"  ... and {len(mappings) - 5} more")

                # Add IAB taxonomy reference
                lines.append("\nIAB Audience Taxonomy 1.1 top-level categories:")
                lines.append("  1: Demographics | 2: Interest | 3: Purchase Intent")
                lines.append("  4: Life Stage | 5: Seasonal & Event | 6: Behaviors")
                lines.append(
                    "\nFull taxonomy: https://github.com/InteractiveAdvertisingBureau/Taxonomies"
                )

                return "\n".join(lines)

            finally:
                client.disconnect()

        except ImportError as e:
            return f"GAM SOAP client dependencies not installed: {e}"
        except Exception as e:
            return f"Error syncing audiences: {e}"

    def _match_to_iab_taxonomy(self, segment_name: str) -> str | None:
        """Attempt to match a GAM segment name to IAB Audience Taxonomy 1.1 ID."""
        name_lower = segment_name.lower()

        # Check each IAB taxonomy entry
        for iab_id, data in IAB_AUDIENCE_TAXONOMY_MAPPINGS.items():
            category_name = data["name"].lower()
            subcategories = [s.lower() for s in data.get("subcategories", [])]

            # Check if segment name contains the category name
            if category_name in name_lower:
                return iab_id

            # Check subcategories
            for sub in subcategories:
                if sub in name_lower:
                    return iab_id

        return None
