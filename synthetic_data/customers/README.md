# A4A Demo — Customer-Specific Datasets

## How to Add a New Customer

1. **Copy the template:**
   ```bash
   cp templates/customer_config_template.yaml customers/{customer_id}/config.yaml
   ```

2. **Research and fill in** the config.yaml:
   - Content rights (what sports/shows do they own?)
   - Audience segments (what 1P data do they have?)
   - Market context (CPMs, currency, regulations)
   - Demo scenarios (what prompts will land?)

3. **Generate synthetic data** — ask Quick:
   ```
   "Generate A4A demo data from customers/{customer_id}/config.yaml"
   ```
   This produces: mcp_mocks/, aamp_seller/, kb/ folders with all CSVs and JSONs.

4. **Activate for demo:**
   - AdCP Lambda: point deploy script at `customers/{customer_id}/mcp_mocks/`
   - AAMP Seller: set `CSV_DATA_DIR` to `customers/{customer_id}/aamp_seller/`
   - Bedrock KB: upload `customers/{customer_id}/kb/*.json`

5. **Revert:** switch paths back to base data, re-deploy.

## Active Customers

| Customer | Market | Status | Content |
|----------|--------|--------|---------|
| `nineseven` | AU | ✅ Active | NRL, AFL, Cricket, Tennis, Stan |
| *(next)* | | | |

## File Structure

```
synthetic_data/
├── mcp_mocks/              ← base US data (always deployed by default)
├── advertising-data/       ← base Bedrock KB
├── configs/                ← tab-configurations.json
├── templates/              ← reusable templates
│   └── customer_config_template.yaml
└── customers/              ← customer-specific datasets (.gitignored)
    └── nineseven/
        ├── config.yaml     ← source of truth
        ├── mcp_mocks/      ← merged CSVs for AdCP Lambda
        ├── aamp_seller/    ← AAMP seller data
        ├── kb/             ← Bedrock KB documents
        └── README.md       ← activation instructions
```

## .gitignore

Add to repo `.gitignore`:
```
synthetic_data/customers/
```
