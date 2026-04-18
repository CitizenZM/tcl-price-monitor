# TCL Price Monitor

Daily price monitoring for TCL products across us.tcl.com, Amazon, and Best Buy.

## Architecture

| Layer | Technology | Notes |
|-------|-----------|-------|
| TCL Scraping | Shopify JSON API | `/products.json` — fast, reliable, no browser |
| Amazon Scraping | Playwright (headless) | Works with standard headless Chrome |
| Best Buy Scraping | Playwright + system Chrome | BB blocks bundled Chromium; `channel: 'chrome'` bypasses |
| Storage | SQLite via better-sqlite3 | `data/prices.db` |
| Scheduling | node-cron + macOS launchd | Daily at 7 AM |

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

- **QM7L/QM8L series**: TCL-only products, not sold on Best Buy
- **98QM7L, 98X11L**: Not available on Amazon
- Best Buy requires system Chrome (`channel: 'chrome'`) — Playwright's bundled Chromium gets `ERR_HTTP2_PROTOCOL_ERROR`
- TCL "compare_at_price" tracked for sale detection
