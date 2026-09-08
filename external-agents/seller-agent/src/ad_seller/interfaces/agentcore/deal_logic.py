"""Crewai-free deal creation logic for the AAMP seller.

Extracted from ``crew_tools.CreateDealTool._create_deal_direct`` so both the
retained CrewAI tool and the Strands A2A runtime can share one implementation
without importing ``crewai``. Behavior (pricing floor, deal types, arithmetic)
is unchanged — see business-rules.md BR-3/BR-4.

This module imports no agent framework; it uses only the seller's own domain
code (models, ad-server client) plus the internal REST API when available.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def create_deal_direct(
    product_id: str,
    deal_type: str = "PD",
    max_cpm: float = 0.0,
    impressions: int = 0,
) -> str:
    """Create a deal in-process, bypassing the REST auth layer.

    Resolves the product from (1) the internal REST catalog, then (2) the ad
    server client (CSV/S3), builds a priced deal, and returns it as JSON.
    Enforces the seller price floor (BR-3) and valid deal types (BR-4).

    Returns a JSON string: the deal on success, or ``{"error": ...}``.
    """
    try:
        from ad_seller.models.core import DealType

        deal_type_map = {
            "PG": DealType.PROGRAMMATIC_GUARANTEED,
            "PD": DealType.PREFERRED_DEAL,
            "PA": DealType.PRIVATE_AUCTION,
        }
        dt_str = (deal_type or "PD").upper()
        if dt_str not in deal_type_map:
            return json.dumps({"error": f"Invalid deal type: {deal_type}. Use PG, PD, or PA."})

        product_data = _resolve_product(product_id)
        if not product_data:
            return json.dumps({"error": f"Product not found: {product_id}"})

        floor_cpm = product_data.get(
            "floor_cpm",
            product_data.get("floor_price_cpm", product_data.get("base_cpm", 25.0)),
        )
        base_cpm = product_data.get("base_cpm", floor_cpm)
        product_name = product_data.get("name", product_data.get("product_name", product_id))

        # BR-3: reject an offer below the seller minimum (floor * 0.85).
        if max_cpm and max_cpm < floor_cpm * 0.85:
            return json.dumps(
                {
                    "error": "price_below_floor",
                    "message": (
                        f"Offered ${max_cpm:.2f} CPM is below seller minimum "
                        f"${floor_cpm * 0.85:.2f} CPM"
                    ),
                    "seller_minimum_cpm": round(floor_cpm * 0.85, 2),
                    "buyer_max_cpm": max_cpm,
                    "product_id": product_id,
                    "deal_type": dt_str,
                }
            )

        deal_id = f"DEAL-{uuid.uuid4().hex[:8].upper()}"
        final_cpm = max_cpm if max_cpm else base_cpm
        total_impressions = impressions if impressions else 1_000_000
        total_cost = (final_cpm / 1000) * total_impressions

        now = datetime.utcnow()
        deal = {
            "deal_id": deal_id,
            "product_id": product_id,
            "product_name": product_name,
            "deal_type": dt_str,
            "status": "booked",
            "cpm": round(final_cpm, 2),
            "floor_cpm": round(floor_cpm, 2),
            "impressions": total_impressions,
            "total_cost": round(total_cost, 2),
            "currency": "USD",
            "start_date": now.strftime("%Y-%m-%d"),
            "end_date": (now + timedelta(days=30)).strftime("%Y-%m-%d"),
            "created_at": now.isoformat(),
            "openrtb_params": {
                "id": deal_id,
                "bidfloor": round(final_cpm, 2),
                "bidfloorcur": "USD",
                "at": 3 if dt_str in ("PG", "PD") else 1,
            },
            "activation_instructions": {
                "dsp": f"Use Deal ID {deal_id} in your DSP bid request",
                "deal_type": dt_str,
            },
        }
        logger.info(
            "Deal created: %s for %s at $%.2f CPM (%s)",
            deal_id,
            product_id,
            final_cpm,
            dt_str,
        )
        return json.dumps(deal, indent=2)

    except Exception as e:  # noqa: BLE001 - surface a structured error, never raise into the tool
        logger.error("Direct deal creation failed: %s", e)
        return json.dumps({"error": f"Deal creation failed: {e}"})


def _resolve_product(product_id: str) -> dict | None:
    """Resolve product pricing data: internal REST catalog first, then ad server."""
    import httpx

    base_url = os.environ.get("SELLER_AGENT_URL", "http://localhost:8001")
    try:
        resp = httpx.get(f"{base_url}/products/{product_id}", timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception:  # noqa: BLE001 - fall through to the ad server client
        pass

    try:
        import asyncio

        from ad_seller.clients.ad_server_base import get_ad_server_client

        client = get_ad_server_client()

        async def _lookup():
            async with client:
                for item in await client.list_inventory():
                    if item.id == product_id:
                        raw = getattr(item, "raw", {}) or {}
                        floor = raw.get("floor_price_cpm", 25.0)
                        return {
                            "product_id": item.id,
                            "name": item.name,
                            "base_cpm": floor,
                            "floor_cpm": floor * 0.85,
                            "inventory_type": raw.get("inventory_type", "display"),
                        }
            return None

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(asyncio.run, _lookup()).result(timeout=15)
            return asyncio.run(_lookup())
        except RuntimeError:
            return asyncio.run(_lookup())
    except Exception as e:  # noqa: BLE001
        logger.warning("ad_server_client product lookup failed: %s", e)
        return None
