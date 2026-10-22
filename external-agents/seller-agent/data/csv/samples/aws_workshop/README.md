# AWS Workshop — Synthetic Publisher Inventory

Synthetic data for the IAB-AWS AAMP workshop demo. Models a fictional multi-platform publisher with diverse inventory across 5 channels.

## Publisher Properties

| Property | Channel | Content |
|----------|---------|---------|
| Apex Streaming | CTV + Digital + Display + Audio | Premium series, originals, movies, kids, live sports (basketball, hockey, football, soccer, golf, tennis, motorsport) |
| GNN | CTV + Linear + Digital + Display + Audio | Live and local news, business, elections, newsletters, podcasts, smart speaker briefings |
| Crestline Entertainment | CTV + Linear + Digital + Display + Audio | Reality TV, comedy, food and travel, home, awards specials, podcasts |
| SportsPulse | Linear + Digital + Display + Audio | Live sports broadcasts, studio shows, highlights, fantasy, live scores, radio |
| Horizon Discovery | CTV + Digital + Display + Audio | Nature and science FAST channels, homepage takeovers, native, science podcast |

## Files

| File | Description |
|------|-------------|
| `inventory.csv` | 87 products across CTV, linear, digital video, display, audio |
| `audiences.csv` | 6 audience segments (sports, cord-cutters, news, entertainment, high-income, auto) |
| `rate_card.json` | Base CPM rates by channel + 4-tier discount structure |
| `media_kits.json` | 4 curated media kit packages |

## Inventory Types & Base CPMs

| Channel | Base CPM | Count | Floor CPM range | Products |
|---------|----------|-------|-----------------|----------|
| CTV/Streaming | $45 | 23 | $26-60 | Apex series, movies, originals and live sports (basketball, hockey, football, soccer, golf, tennis, motorsport), Crestline reality, comedy and lifestyle, GNN live news, Horizon FAST documentaries, pause and binge units |
| Linear TV | $25 | 16 | $14-45 | GNN dayparts and business, local news (New York, Los Angeles, Dallas), SportsPulse live and studio, Crestline primetime, late night and daytime |
| Digital Video | $18 | 17 | $12-26 | GNN pre-, mid-roll, live stream and vertical video, SportsPulse highlights, fantasy and live streams, Apex trailers, Crestline recaps and recipes, Horizon explainers |
| Display | $12 | 17 | $6-22 | Homepage takeovers, leaderboards, MPUs, native, newsletters, gameday takeovers, site skins, second-screen companion banners |
| Audio | $8 | 14 | $7-16 | GNN and SportsPulse podcasts, live radio simulcasts, play-by-play, smart speaker briefings, Crestline and Horizon podcasts |

Each row's `floor_price_cpm` is its own floor. The base CPMs above are the `rate_card.json` channel rates. The local news rows are geo-targeted (`US-NY`, `US-CA`, `US-TX`); everything else targets `US`.

`CSVAdServerClient.list_inventory()` defaults to `limit=100`, so keep this file (plus any `inventory_*.csv` overlays) at or under 100 rows, or raise the limit where the catalog is loaded.

## Pricing Tiers

| Tier | Discount | Example (CTV $45 base) |
|------|----------|----------------------|
| Public | 0% | $45.00 |
| Registered Buyer | 5% | $42.75 |
| Preferred Agency | 12% | $39.60 |
| Strategic Advertiser | 15% | $38.25 |

## Media Kit Packages

| Package | Products | CPM Range |
|---------|----------|-----------|
| Apex Premium Sports Bundle | Basketball + Hockey CTV + SportsPulse linear | $42-55 |
| GNN News Reach Package | GNN linear + digital video + display | $18-28 |
| Entertainment Upfront Package | Apex series + Crestline CTV + linear | $35-48 |
| Cross-Platform Reach | 10 products across all channels | $15-45 |

## Key IDs

- Inventory IDs: `inv-ctv-*`, `inv-lin-*`, `inv-dig-*`, `inv-dsp-*`, `inv-aud-*`
- Package IDs: `PKG-APEX-SPORTS`, `PKG-GNN-NEWS`, `PKG-ENT-UPFRONT`, `PKG-CROSS-PLATFORM`
- Audience IDs: `aud-sports-enthusiasts`, `aud-cord-cutters`, `aud-news-engaged`, `aud-entertainment-seekers`, `aud-high-income-pros`, `aud-auto-intenders`

## Usage

Set `CSV_DATA_DIR=./data/csv/samples/aws_workshop` to load this data set. The AgentCore Dockerfile uses this by default.
