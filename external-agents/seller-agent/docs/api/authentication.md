# Authentication

The seller agent supports authenticated and anonymous access. Authentication unlocks tiered pricing, negotiation, and richer data in responses.

## Authentication Methods

Two methods are accepted. Both can be used on any endpoint:

### Bearer Token

```
Authorization: Bearer <api_key>
```

### API Key Header

```
X-Api-Key: <api_key>
```

When both headers are present, the system validates whichever is found first. Anonymous requests (no key) are allowed on most endpoints but receive public-tier access only.

## API Key Lifecycle

### Create a Key

```bash
curl -X POST http://localhost:8000/auth/api-keys \
  -H "Content-Type: application/json" \
  -d '{
    "seat_id": "seat-acme-001",
    "seat_name": "Acme DSP",
    "agency_id": "agency-mega",
    "agency_name": "Mega Agency",
    "advertiser_id": "adv-widget-co",
    "advertiser_name": "Widget Co",
    "label": "Widget Co production key",
    "expires_in_days": 365
  }'
```

The response contains the **full API key** which is shown **only once**. Store it securely --- it cannot be retrieved again.

### List Keys

```bash
curl http://localhost:8000/auth/api-keys
```

Returns metadata for all keys (no secrets). Includes key ID, label, identity, creation date, and status.

### Get Key Details

```bash
curl http://localhost:8000/auth/api-keys/{key_id}
```

### Revoke a Key

```bash
curl -X DELETE http://localhost:8000/auth/api-keys/{key_id}
```

Revoked keys immediately return HTTP 401 on use.

## Access Tiers

Access tiers control pricing visibility, discount eligibility, and negotiation access:

| Tier | Description | Pricing Visibility | Negotiation |
|------|-------------|-------------------|-------------|
| `public` | Anonymous / unknown buyer | Price ranges only | No |
| `seat` | Identified DSP seat | Exact prices, no discounts | Limited |
| `agency` | Agency-level identity | Tier discounts applied | Standard |
| `advertiser` | Full advertiser identity | Full discounts + volume | Premium |

The tier is determined automatically from the API key's identity fields. If no key is provided, the tier falls back to `public` (or to the `buyer_tier` body parameter for backward compatibility).

## Agent Registry Trust Levels

When a buyer agent provides its `agent_url`, the seller looks up the agent in its registry and maps trust level to a maximum access tier:

| Trust Status | Description | Max Access Tier |
|-------------|-------------|-----------------|
| `unknown` | Never seen before | `public` |
| `registered` | Fetched agent card, not yet verified | `seat` |
| `approved` | Manually approved by seller operator | `advertiser` |
| `preferred` | Trusted partner with custom pricing rules | `advertiser` |
| `blocked` | Rejected --- returns HTTP 403, zero data | None |

The effective tier is the **minimum** of the API key tier and the agent trust tier. A `preferred` agent with a `seat`-level API key gets `seat` access. A `public` API key with an `approved` agent gets `public` access.

### Managing Trust

```bash
# Discover and register an agent
curl -X POST http://localhost:8000/registry/agents/discover \
  -H "Content-Type: application/json" \
  -d '{"agent_url": "https://buyer.example.com"}'

# Approve the agent
curl -X PUT http://localhost:8000/registry/agents/{agent_id}/trust \
  -H "Content-Type: application/json" \
  -d '{"trust_status": "approved", "notes": "Verified by ops team"}'

# Block a malicious agent
curl -X PUT http://localhost:8000/registry/agents/{agent_id}/trust \
  -H "Content-Type: application/json" \
  -d '{"trust_status": "blocked", "notes": "Abuse detected"}'
```
