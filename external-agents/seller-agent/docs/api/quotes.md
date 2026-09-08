# Quotes

Quotes are non-binding price offers from the seller. They have a 24-hour TTL and can be converted into deals via the [Deal Booking](overview.md#deal-booking) endpoints.

## Create a Quote

**POST** `/api/v1/quotes`

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `product_id` | string | Yes | Product from the catalog |
| `deal_type` | string | Yes | `PG` (Programmatic Guaranteed), `PD` (Preferred Deal), or `PA` (Private Auction) |
| `impressions` | integer | No | Required for PG deals |
| `flight_start` | string | No | ISO date, defaults to today |
| `flight_end` | string | No | ISO date, defaults to today + 30 days |
| `target_cpm` | float | No | Buyer's desired CPM; accepted if above floor |
| `buyer_identity` | object | No | `seat_id`, `agency_id`, `advertiser_id`, `dsp_platform` |

### Deal Types

- **PG (Programmatic Guaranteed)** --- Fixed price, guaranteed impressions. Requires `impressions` field. Auction type `at=1` (first price).
- **PD (Preferred Deal)** --- Fixed price, non-guaranteed. Buyer gets first look. Auction type `at=1`.
- **PA (Private Auction)** --- Floor price, competitive. Multiple buyers can bid. Auction type `at=3` (private auction).

### Pricing Calculation

The seller evaluates the quote using the `PricingRulesEngine`:

1. Looks up the product's `base_cpm`
2. Applies tier discount based on buyer identity (public/seat/agency/advertiser)
3. Applies volume discount based on `impressions`
4. If `target_cpm` is provided and is above the product's `floor_cpm`, the target is accepted
5. Returns the final CPM with a rationale string

### Example: Create a PG Quote

```bash
curl -X POST http://localhost:8000/api/v1/quotes \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <api_key>" \
  -d '{
    "product_id": "display",
    "deal_type": "PG",
    "impressions": 2000000,
    "flight_start": "2026-04-01",
    "flight_end": "2026-06-30",
    "buyer_identity": {
      "agency_id": "agency-mega",
      "advertiser_id": "adv-widget-co"
    }
  }'
```

Response:

```json
{
  "quote_id": "qt-a1b2c3d4e5f6",
  "status": "available",
  "product": {
    "product_id": "display",
    "name": "Premium Display",
    "inventory_type": "display"
  },
  "pricing": {
    "base_cpm": 12.0,
    "tier_discount_pct": 10.0,
    "volume_discount_pct": 5.0,
    "final_cpm": 10.26,
    "currency": "USD",
    "pricing_model": "cpm",
    "rationale": "Agency tier discount (10%) + volume discount (5%) applied"
  },
  "terms": {
    "impressions": 2000000,
    "flight_start": "2026-04-01",
    "flight_end": "2026-06-30",
    "guaranteed": true
  },
  "deal_type": "PG",
  "buyer_tier": "advertiser",
  "expires_at": "2026-04-02T00:00:00Z",
  "created_at": "2026-04-01T00:00:00Z"
}
```

### Example: Create a PD Quote

```bash
curl -X POST http://localhost:8000/api/v1/quotes \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": "video",
    "deal_type": "PD",
    "flight_start": "2026-05-01",
    "flight_end": "2026-05-31",
    "target_cpm": 18.50
  }'
```

## Retrieve a Quote

**GET** `/api/v1/quotes/{quote_id}`

Returns the quote if it exists and has not expired.

- **404** --- Quote not found
- **410 Gone** --- Quote has expired. Request a new quote.

```bash
curl http://localhost:8000/api/v1/quotes/qt-a1b2c3d4e5f6
```

## Quote Lifecycle

1. **available** --- Active, can be booked into a deal
2. **booked** --- Converted into a deal via `POST /api/v1/deals`
3. **expired** --- TTL elapsed (24 hours), returns 410 on retrieval
