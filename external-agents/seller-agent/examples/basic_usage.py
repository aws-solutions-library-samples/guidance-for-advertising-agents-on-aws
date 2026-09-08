# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Basic usage example for the Ad Seller System.

Demonstrates:
- Setting up products
- Processing discovery queries
- Handling proposals
- Generating deals
"""

import asyncio
from ad_seller.flows import (
    ProductSetupFlow,
    DiscoveryInquiryFlow,
    ProposalHandlingFlow,
    DealGenerationFlow,
)
from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity, AccessTier


async def main():
    """Run basic usage example."""
    print("=" * 60)
    print("Ad Seller System - Basic Usage Example")
    print("=" * 60)

    # Step 1: Initialize products
    print("\n1. Setting up products...")
    setup_flow = ProductSetupFlow()
    await setup_flow.kickoff()

    print(f"   Created {len(setup_flow.state.products)} products:")
    for product in setup_flow.state.products.values():
        print(f"   - {product.name}: ${product.base_cpm:.2f} CPM")

    # Step 2: Process a discovery query (anonymous)
    print("\n2. Processing discovery query (anonymous)...")
    discovery_flow = DiscoveryInquiryFlow()
    discovery_flow.state.query = "What CTV inventory do you have?"
    discovery_flow.state.products = setup_flow.state.products
    discovery_flow.kickoff()

    print(f"   Access tier: {discovery_flow.state.response_data.get('access_tier', 'public')}")

    # Step 3: Process discovery with agency identity
    print("\n3. Processing discovery query (agency tier)...")
    agency_identity = BuyerIdentity(
        agency_id="agency-123",
        agency_name="Test Agency",
    )
    agency_context = BuyerContext(
        identity=agency_identity,
        is_authenticated=True,
    )

    discovery_flow2 = DiscoveryInquiryFlow()
    discovery_flow2.state.query = "What is the pricing for CTV?"
    discovery_flow2.state.buyer_context = agency_context
    discovery_flow2.state.products = setup_flow.state.products
    discovery_flow2.kickoff()

    if "pricing" in discovery_flow2.state.response_data:
        pricing = discovery_flow2.state.response_data["pricing"]
        print(f"   Agency tier discount: {pricing.get('discount_from_msrp', 'N/A')}")

    # Step 4: Submit a proposal
    print("\n4. Submitting a proposal...")
    proposal_data = {
        "product_id": list(setup_flow.state.products.keys())[0],  # First product
        "deal_type": "preferred_deal",
        "price": 12.0,
        "impressions": 1000000,
        "start_date": "2026-02-01",
        "end_date": "2026-03-31",
    }

    proposal_flow = ProposalHandlingFlow()
    result = proposal_flow.handle_proposal(
        proposal_id="prop-001",
        proposal_data=proposal_data,
        buyer_context=agency_context,
        products=setup_flow.state.products,
    )

    print(f"   Recommendation: {result['recommendation']}")
    print(f"   Status: {result['status']}")

    # Step 5: Generate a deal (if accepted or counter-accepted)
    if result["recommendation"] in ["accept", "counter"]:
        print("\n5. Generating deal...")

        deal_flow = DealGenerationFlow()
        deal_result = deal_flow.generate_deal(
            proposal_id="prop-001",
            proposal_data={
                **proposal_data,
                "status": "accepted",
            },
        )

        if deal_result.get("deal_id"):
            print(f"   Deal ID: {deal_result['deal_id']}")
            print(f"   Deal Type: {deal_result['deal_type']}")
            print(f"   Price: ${deal_result['price']:.2f} CPM")
            print("\n   Activation instructions:")
            for platform, instructions in deal_result["activation_instructions"].items():
                print(f"   - {platform}: {instructions}")
    else:
        print("\n5. Deal not generated (proposal rejected)")

    print("\n" + "=" * 60)
    print("Example completed!")


if __name__ == "__main__":
    asyncio.run(main())
