# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Publisher Crew - Top-level orchestration crew.

The Publisher Crew coordinates all inventory specialists and functional
agents under the Inventory Manager's strategic direction.
"""

from crewai import Crew, Process, Task

from ..agents.level1 import create_inventory_manager
from ..agents.level2 import (
    create_ctv_inventory_agent,
    create_display_inventory_agent,
    create_linear_tv_inventory_agent,
    create_mobile_app_inventory_agent,
    create_native_inventory_agent,
    create_video_inventory_agent,
)
from ..config import get_settings


class PublisherCrew:
    """Publisher Crew for coordinating seller operations.

    This crew manages the full lifecycle of seller operations:
    - Product definition and catalog management
    - Proposal evaluation and negotiation
    - Deal creation and execution

    The Inventory Manager (Level 1) serves as the manager agent,
    coordinating Level 2 inventory specialists.
    """

    def __init__(self) -> None:
        """Initialize the Publisher Crew."""
        settings = get_settings()

        # Create agents
        self.inventory_manager = create_inventory_manager()
        self.display_agent = create_display_inventory_agent()
        self.video_agent = create_video_inventory_agent()
        self.ctv_agent = create_ctv_inventory_agent()
        self.mobile_app_agent = create_mobile_app_inventory_agent()
        self.native_agent = create_native_inventory_agent()
        self.linear_tv_agent = create_linear_tv_inventory_agent()

        self._settings = settings

    def create_proposal_evaluation_crew(
        self,
        proposal_data: dict,
    ) -> Crew:
        """Create a crew for evaluating an incoming proposal.

        Args:
            proposal_data: The proposal to evaluate

        Returns:
            Crew configured for proposal evaluation
        """
        # Strategic evaluation task for Inventory Manager
        strategic_evaluation_task = Task(
            description=f"""Evaluate the following proposal from a strategic yield perspective:

Proposal Details:
{proposal_data}

Consider:
1. Does this proposal align with our yield optimization objectives?
2. What is the short-term revenue value vs long-term relationship value?
3. Should we accept, counter, or reject?
4. If countering, what terms should we propose?
5. Are there upsell opportunities?

Delegate to inventory specialists to get channel-specific assessments.
Then synthesize their input into a final recommendation.""",
            expected_output="""A strategic recommendation including:
- Decision: Accept / Counter / Reject
- Revenue impact analysis
- Relationship value assessment
- Counter-terms if applicable
- Upsell opportunities identified
- Rationale for the decision""",
            agent=self.inventory_manager,
        )

        # Channel assessment tasks based on proposal inventory type
        channel_tasks = []

        # Add relevant channel assessment based on proposal
        inventory_type = proposal_data.get("inventory_type", "display")

        if inventory_type == "display" or "display" in str(proposal_data):
            display_task = Task(
                description=f"""Assess this proposal from a display inventory perspective:

Proposal: {proposal_data}

Evaluate:
- Is this a good fit for our display inventory?
- Pricing appropriateness for display positions
- Viewability and brand safety considerations
- Recommended display placements""",
                expected_output="Display inventory assessment with pricing and placement recommendations",
                agent=self.display_agent,
            )
            channel_tasks.append(display_task)

        if inventory_type == "video" or "video" in str(proposal_data):
            video_task = Task(
                description=f"""Assess this proposal from a video inventory perspective:

Proposal: {proposal_data}

Evaluate:
- Is this a good fit for our video inventory?
- Pre-roll, mid-roll, or outstream placement options
- Completion rate expectations
- Pricing for video positions""",
                expected_output="Video inventory assessment with format and pricing recommendations",
                agent=self.video_agent,
            )
            channel_tasks.append(video_task)

        if (
            inventory_type == "ctv"
            or "ctv" in str(proposal_data)
            or "streaming" in str(proposal_data)
        ):
            ctv_task = Task(
                description=f"""Assess this proposal from a CTV/streaming perspective:

Proposal: {proposal_data}

Evaluate:
- CTV inventory availability and fit
- Household targeting capabilities
- Premium streaming vs FAST placement options
- CTV-specific pricing considerations""",
                expected_output="CTV inventory assessment with streaming placement recommendations",
                agent=self.ctv_agent,
            )
            channel_tasks.append(ctv_task)

        proposal_str = str(proposal_data)
        if (
            inventory_type == "linear_tv"
            or "linear" in proposal_str
            or "broadcast" in proposal_str
            or "cable" in proposal_str
            or "tv " in proposal_str.lower()
        ):
            linear_tv_task = Task(
                description=f"""Assess this proposal from a linear TV perspective:

Proposal: {proposal_data}

Evaluate:
- Daypart appropriateness and pricing tier
- Upfront vs scatter market fit
- Addressable TV capability and household coverage
- Cross-platform opportunities (linear + CTV + digital)
- Makegood provisions and audience guarantee terms
- Dual currency assessment (CPP and CPM)""",
                expected_output="Linear TV assessment with daypart, pricing, and deal structure recommendations",
                agent=self.linear_tv_agent,
            )
            channel_tasks.append(linear_tv_task)

        # Create the crew
        return Crew(
            agents=[
                self.inventory_manager,
                self.display_agent,
                self.video_agent,
                self.ctv_agent,
                self.mobile_app_agent,
                self.native_agent,
                self.linear_tv_agent,
            ],
            tasks=[strategic_evaluation_task] + channel_tasks,
            process=Process.hierarchical,
            manager_agent=self.inventory_manager,
            verbose=self._settings.crew_verbose,
            memory=self._settings.crew_memory_enabled,
        )

    def create_catalog_management_crew(self) -> Crew:
        """Create a crew for managing the product catalog.

        Returns:
            Crew configured for catalog management
        """
        catalog_strategy_task = Task(
            description="""Review our current product catalog and provide
strategic recommendations for optimization:

1. Are products properly categorized by inventory type?
2. Is pricing competitive and aligned with market rates?
3. Are commercial terms (deal types, minimums) appropriate?
4. What new products should we consider adding?
5. What underperforming products should we retire?

Coordinate with inventory specialists to get channel-specific input.""",
            expected_output="""Catalog optimization report including:
- Current catalog assessment
- Pricing recommendations by channel
- New product opportunities
- Products to retire or modify
- Commercial terms adjustments""",
            agent=self.inventory_manager,
        )

        return Crew(
            agents=[
                self.inventory_manager,
                self.display_agent,
                self.video_agent,
                self.ctv_agent,
                self.mobile_app_agent,
                self.native_agent,
                self.linear_tv_agent,
            ],
            tasks=[catalog_strategy_task],
            process=Process.hierarchical,
            manager_agent=self.inventory_manager,
            verbose=self._settings.crew_verbose,
            memory=self._settings.crew_memory_enabled,
        )


def create_publisher_crew() -> PublisherCrew:
    """Create a Publisher Crew instance.

    Returns:
        PublisherCrew: Configured publisher crew
    """
    return PublisherCrew()
