# Synthetic Data for Agentic Advertising Workflow Demo

This directory contains synthetic CSV files to support mocking out the complete 8-step Premium Video Campaign workflow.

## File Inventory

### Step 1: Campaign Brief (A2A)
- **`campaigns.csv`** - Campaign briefs with objectives, budgets, KPIs, and targeting descriptions
- **`advertisers_agencies.csv`** - Advertiser and agency entities with their partner preferences

### Step 2: Discovery (AdCP)

#### Media Buy Protocol - `get_products`
- **`products.csv`** - Publisher inventory products with pricing, audience composition, and format support

#### Signals Activation Protocol - `get_signals` / `activate_signal`
- **`signals.csv`** - Audience and contextual signals from signal agents (LiveRamp, Experian, Peer39, etc.)

#### Creative Formats - `list_creative_formats`
- **`creative_formats.csv`** - Supported creative specifications by channel and format type

### Step 3: Identity Resolution (MCP)
- **`identity_providers.csv`** - Identity graph providers with match rates, pricing, and integration partners

### Step 4: Brand Safety Verification (MCP)
- **`verification_services.csv`** - Brand safety, viewability, and fraud detection services

### Step 5: Human Approval
- **`approvals.csv`** - Approval workflow records with status, approvers, and notes

### Step 6: Programmatic Execution (AdCP + ARTF)

#### AdCP Media Buy Protocol - `create_media_buy`
- **`media_buy_packages.csv`** - Media buy packages with product selections, budgets, and targeting overlays

#### ARTF Containers
- **`artf_containers.csv`** - Containerized services deployed in exchange infrastructure by intent type

#### Platforms
- **`platforms.csv`** - DSPs and SSPs with their integration capabilities

### Step 7: Measurement Setup (MCP)
- **`measurement_providers.csv`** - Measurement providers with study types, methodologies, and pricing

### Step 8: Ongoing Optimization (A2A)

#### Delivery Metrics - `get_media_buy_delivery`
- **`delivery_metrics.csv`** - Daily delivery and performance data by package

#### Measurement Results
- **`measurement_studies.csv`** - Brand lift and attribution study results

### Creatives
- **`creatives.csv`** - Creative assets with format, approval status, and asset URLs

---

## Data Relationships

```
advertisers_agencies
        │
        ├──> campaigns
        │        │
        │        ├──> media_buy_packages ──> products
        │        │        │
        │        │        ├──> signals (targeting)
        │        │        │
        │        │        └──> delivery_metrics
        │        │
        │        ├──> creatives
        │        │
        │        ├──> measurement_studies ──> measurement_providers
        │        │
        │        └──> approvals
        │
        └──> platforms (DSPs/SSPs)
                 │
                 └──> artf_containers

identity_providers ──> (used in Step 3)
verification_services ──> (used in Step 4)
creative_formats ──> (referenced by products and creatives)
```

---

## Key IDs for the Primary Example

### Acme Energy Drink Campaign (camp_001)
- **Advertiser:** adv_cpg_001 (Acme Consumer Goods)
- **Agency:** agy_omni_001 (Omnicom Media Group)
- **Media Buy:** mb_001
- **Packages:** pkg_001 through pkg_005
- **Primary DSP:** The Trade Desk (dsp_ttd_001)

### Products Used
- prod_espn_ctv_001 (ESPN Premium Sports CTV)
- prod_fox_ctv_001 (Fox Sports CTV)
- prod_paramount_ctv_001 (Paramount+ Sports CTV)
- prod_youtube_olv_001 (YouTube Sports Premium)
- prod_twitch_olv_001 (Twitch Sports & Esports)

### Signals Activated
- sig_lr_004 (Sports Enthusiasts - Active Lifestyle)
- sig_p39_001 (Contextual - Sports News)

### ARTF Containers in Bid Path
- artf_001 (LiveRamp Identity Enrichment) - audienceSegmentation
- artf_002 (UID2 Token Resolution) - audienceSegmentation
- artf_004 (IAS Pre-Bid Safety) - metadataEnhancement
- artf_005 (DoubleVerify Authentic Pre-Bid) - metadataEnhancement
- artf_007 (Peer39 Contextual) - metadataEnhancement

### Measurement Studies
- study_001: Brand Awareness (Lucid)
- study_004: Foot Traffic Attribution (Foursquare)

---

## Usage Notes

1. **Prices are in USD** unless otherwise noted
2. **Dates are ISO 8601** format
3. **Boolean values** are lowercase (`true`/`false`)
4. **Multi-value fields** use comma separation within the field
5. **IDs follow convention:** `{type}_{source}_{sequence}` (e.g., `prod_espn_ctv_001`)

---

## AdCP Protocol Mapping

| Step | Protocol | Task | Data Source |
|------|----------|------|-------------|
| 2 | Media Buy | `get_products` | products.csv |
| 2 | Media Buy | `list_creative_formats` | creative_formats.csv |
| 2 | Signals | `get_signals` | signals.csv |
| 2 | Signals | `activate_signal` | signals.csv → platforms.csv |
| 5 | Media Buy | `create_media_buy` | media_buy_packages.csv |
| 5 | Media Buy | `sync_creatives` | creatives.csv |
| 8 | Media Buy | `get_media_buy_delivery` | delivery_metrics.csv |
| 8 | Media Buy | `update_media_buy` | media_buy_packages.csv |

---

## ARTF Intent Mapping

| Intent | Example Containers | CSV Reference |
|--------|-------------------|---------------|
| `audienceSegmentation` | LiveRamp, UID2, ID5, Oracle | artf_containers.csv |
| `metadataEnhancement` | IAS, DoubleVerify, Human, Peer39, Scope3 | artf_containers.csv |
| `bidValuation` | Chalice | artf_containers.csv |
| `auctionOrchestration` | Index Exchange | artf_containers.csv |
| `dynamicDealCuration` | Magnite | artf_containers.csv |
