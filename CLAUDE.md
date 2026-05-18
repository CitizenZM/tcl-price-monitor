# TCL Price Monitor

Daily price monitoring for TCL products across us.tcl.com, Amazon, and Best Buy.

## Architecture

| Layer | Technology | Notes |
|-------|-----------|-------|
| TCL Scraping | Shopify JSON API | `/products.json` — fast, reliable, no browser |
| Amazon Scraping | Playwright (headless) | Works with standard headless Chrome |
| Best Buy Scraping | Playwright headless Chromium | Search-page scrape via `data-product-id` attr + scroll-to-render. BB blocks product-page paths; search pages work. Only records price when scraped skuId exactly matches our stored BB skuId — no false matches. |
| Storage | SQLite via better-sqlite3 | `data/prices.db` |
| Scheduling | macOS launchd | Daily at 7 PM PST via `~/Library/LaunchAgents/com.celldigital.tcl-price-monitor.plist` |
| Scheduling | GitHub Actions cron | `0 3 * * *` UTC (7 PM PST fallback / CI) |

## Commands

```bash
npm run build-catalog      # Phase 1: scan us.tcl.com, build SKU catalog
node src/match-competitors.js  # Phase 2: find Amazon/Best Buy URLs
node src/manual-match.js --import data/seed-urls.json  # Import known URLs
npm run check-prices       # Phase 3: fetch prices from all platforms
npm run report             # Phase 4: generate comparison report
npm run run-all            # Run full pipeline (catalog → prices → report)
npm run schedule           # Start daily scheduler (7 AM)
```

## Manual URL Management

```bash
node src/manual-match.js --list               # Show all SKUs + match status
node src/manual-match.js --unmatched          # Show unmatched only
node src/manual-match.js 65QM6K --amazon URL  # Set Amazon URL
node src/manual-match.js --import urls.json   # Bulk import
```

## Data Files

- `data/prices.db` — SQLite database (prices, SKUs, history)
- `data/seed-urls.json` — Known Amazon/Best Buy URLs
- `reports/tcl-prices-YYYY-MM-DD.csv` — Daily CSV reports
- `logs/run-YYYY-MM-DD.log` — Daily run logs

## Key Decisions

- **QM7L/QM8L series**: Available on Amazon, not Best Buy (2026 models)
- **98QM7L, 98X11L**: Not available on Amazon
- **Best Buy is fully blocked** — HTTP2 INTERNAL_ERROR on all `/site/` paths from any Playwright browser (bundled Chromium, system Chrome via CDP, MCP browser). BB's bot-detection rejects non-browser TLS fingerprints. The ONLY working solution is `BESTBUY_API_KEY` in `.env` which enables the official Products API. Register free at https://developer.bestbuy.com/
- TCL "compare_at_price" tracked for sale detection
- Seed URLs are in `data/seed-urls.json` — always re-import after DB reset: `node src/manual-match.js --import data/seed-urls.json`

## CI/CD

- **GitHub repo**: CitizenZM/tcl-price-monitor (private)
- **Daily cron**: `.github/workflows/daily-prices.yml` — runs at 11:00 UTC (7 AM ET)
- **Manual trigger**: `gh workflow run daily-prices.yml`
- **SQLite DB**: cached between runs via `actions/cache`
- **Artifacts**: PDF + CSV reports uploaded, retained 30 days
- **CI mode**: `CI=true` switches Best Buy to headless Chrome, PDF outputs to `reports/`
