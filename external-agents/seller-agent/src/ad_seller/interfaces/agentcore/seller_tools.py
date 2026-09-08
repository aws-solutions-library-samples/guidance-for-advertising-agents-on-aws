"""Crewai-free in-process seller read tools for the Strands A2A runtime.

Option A (requirements Q2 revised B→A): instead of the internal MCP server (whose
`list_products` pulls `crewai` via `ProductSetupFlow`), the Strands agent uses these
Strands `@tool`s. They call the internal FastAPI **REST** endpoints (localhost:8001),
which serve a cached static catalog and are crewai-free. `create_deal` stays in
`deal_logic`. No `crewai` import anywhere on this path (AC-5).
"""

import json
import logging
import os

import httpx
from strands import tool

logger = logging.getLogger(__name__)


def _base() -> str:
    return os.environ.get("SELLER_AGENT_URL", "http://localhost:8001")


@tool
def list_products() -> str:
    """List all products in the seller's inventory catalog (id, pricing, type, deal types)."""
    try:
        r = httpx.get(f"{_base()}/products", timeout=30)
        r.raise_for_status()
        return json.dumps(r.json(), indent=2)
    except Exception as e:  # noqa: BLE001
        logger.error("list_products failed: %s", e)
        return f"Error listing products: {e}"


@tool
def get_product_details(product_id: str) -> str:
    """Get details for one product by id (pricing, inventory type, supported deal types).

    Args:
        product_id: The product id to look up.
    """
    try:
        r = httpx.get(f"{_base()}/products/{product_id}", timeout=30)
        r.raise_for_status()
        return json.dumps(r.json(), indent=2)
    except Exception as e:  # noqa: BLE001
        return f"Error getting product {product_id}: {e}"


@tool
def get_pricing(product_id: str, buyer_tier: str = "public", volume: int = 0) -> str:
    """Calculate tiered pricing for a product by buyer identity and volume.

    Args:
        product_id: The product to price.
        buyer_tier: 'public', 'registered', 'preferred', or 'strategic'.
        volume: Impressions for volume-discount calculation (0 to skip).
    """
    try:
        body = {"product_id": product_id, "buyer_tier": buyer_tier}
        if volume:
            body["volume"] = volume
        r = httpx.post(f"{_base()}/pricing", json=body, timeout=30)
        r.raise_for_status()
        return json.dumps(r.json(), indent=2)
    except Exception as e:  # noqa: BLE001
        return f"Error getting pricing: {e}"


@tool
def discover_inventory(query: str = "") -> str:
    """Discover inventory matching a natural-language buyer requirement.

    Args:
        query: Natural-language description of what the buyer wants.
    """
    try:
        r = httpx.post(f"{_base()}/discovery", json={"query": query or ""}, timeout=30)
        r.raise_for_status()
        return json.dumps(r.json(), indent=2)
    except Exception as e:  # noqa: BLE001
        return f"Error discovering inventory: {e}"


@tool
def get_rate_card() -> str:
    """Get the rate card: base CPMs organized by inventory type."""
    try:
        r = httpx.get(f"{_base()}/products", timeout=30)
        r.raise_for_status()
        products = r.json()
        items = products if isinstance(products, list) else products.get("products", [])
        rate_card: dict = {}
        for p in items:
            inv = p.get("inventory_type", p.get("channel", "unknown"))
            rate_card.setdefault(inv, []).append(
                {
                    "name": p.get("name", p.get("product_name", "unknown")),
                    "base_cpm": p.get("base_cpm", p.get("avg_cpm_usd", 0)),
                }
            )
        return json.dumps({"rate_card": rate_card}, indent=2)
    except Exception as e:  # noqa: BLE001
        return f"Error getting rate card: {e}"


SELLER_READ_TOOLS = [
    list_products,
    get_product_details,
    get_pricing,
    discover_inventory,
    get_rate_card,
]
