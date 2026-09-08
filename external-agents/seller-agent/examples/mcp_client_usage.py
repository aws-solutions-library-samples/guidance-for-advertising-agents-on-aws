# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""MCP Client usage example.

Demonstrates connecting to the IAB Tech Lab agentic-direct server
via MCP (Model Context Protocol) for OpenDirect 2.1 operations.
"""

import asyncio
from ad_seller.clients import UnifiedClient, Protocol


async def main():
    """Run MCP client usage example."""
    print("=" * 60)
    print("MCP Client Usage Example")
    print("=" * 60)

    # Connect to live IAB Tech Lab server
    server_url = "https://agentic-direct-server-hwgrypmndq-uk.a.run.app"
    print(f"\nConnecting to: {server_url}")

    try:
        async with UnifiedClient(
            base_url=server_url,
            protocol=Protocol.OPENDIRECT_21,
        ) as client:
            print("Connected successfully!\n")

            # List organizations
            print("1. Listing organizations...")
            orgs_result = await client.list_organizations()
            if orgs_result.success:
                orgs = orgs_result.data or []
                print(f"   Found {len(orgs)} organizations")
                for org in orgs[:5]:  # Show first 5
                    print(f"   - {org.get('name', 'Unknown')} ({org.get('role', 'unknown')})")
            else:
                print(f"   Error: {orgs_result.error}")

            # List products
            print("\n2. Listing products...")
            products_result = await client.list_products()
            if products_result.success:
                products = products_result.data or []
                print(f"   Found {len(products)} products")
                for product in products[:5]:
                    print(f"   - {product.get('name', 'Unknown')}")
            else:
                print(f"   Error: {products_result.error}")

            # Create a seller organization (if it doesn't exist)
            print("\n3. Creating seller organization...")
            create_result = await client.create_organization(
                name="Example Publisher",
                role="seller",
            )
            if create_result.success:
                print(f"   Created: {create_result.data}")
            else:
                print(f"   Note: {create_result.error}")

    except Exception as e:
        print(f"\nConnection error: {e}")
        print("\nMake sure the server is accessible and try again.")

    print("\n" + "=" * 60)
    print("MCP client example completed!")


if __name__ == "__main__":
    asyncio.run(main())
