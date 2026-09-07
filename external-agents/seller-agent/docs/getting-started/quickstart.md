# Quickstart

Get the seller agent running locally and make your first API calls.

## Prerequisites

- Python 3.11 or later
- pip

## Installation

Clone the repository and install in editable mode:

```bash
pip install -e ".[all]"
```

For a minimal install without optional dependencies:

```bash
pip install -e .
```

## Run the Server

Start the FastAPI server with auto-reload for development:

```bash
uvicorn ad_seller.interfaces.api.main:app --reload
```

The server starts at `http://localhost:8000` by default.

## Verify It Works

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status": "healthy"}
```

## Browse the API Docs

Open `http://localhost:8000/docs` in a browser for the auto-generated Swagger UI with all 58 endpoints.

## First API Calls

### List Products

```bash
curl http://localhost:8000/products
```

Returns the full product catalog with product IDs, names, base CPMs, floor CPMs, and supported deal types.

### Get Pricing

```bash
curl -X POST http://localhost:8000/pricing \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": "display",
    "buyer_tier": "agency",
    "volume": 500000
  }'
```

Returns tiered pricing with base price, tier discount, volume discount, final price, and pricing rationale.

### Create a Quote

```bash
curl -X POST http://localhost:8000/api/v1/quotes \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": "display",
    "deal_type": "PG",
    "impressions": 1000000,
    "flight_start": "2026-04-01",
    "flight_end": "2026-04-30"
  }'
```

Returns a non-binding price quote with a 24-hour TTL. Use the `quote_id` from the response to book a deal.

### Book a Deal from a Quote

```bash
curl -X POST http://localhost:8000/api/v1/deals \
  -H "Content-Type: application/json" \
  -d '{
    "quote_id": "<quote_id from previous step>"
  }'
```

Returns a confirmed deal with a Deal ID, OpenRTB parameters, and DSP activation instructions.

## Next Steps

- [API Overview](../api/overview.md) --- see all 58 endpoints
- [Authentication](../api/authentication.md) --- set up API keys for authenticated access
- [Buyer Agent Integration](../integration/buyer-agent.md) --- connect a buyer agent
