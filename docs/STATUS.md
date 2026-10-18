# Status - tcl-price-monitor (updated 2026-10-17)

## Purpose
Daily price monitoring for TCL products (us.tcl.com, Amazon, Best Buy) with SQLite persistence, scheduled scraping via macOS launchd and GitHub Actions cron, and CSV/PDF reporting.

## Current state
- Last recorded activity: 2026-08-15 (auto-sync of `data/last-run-v4.json`)
- Daily price-check runs active through 2026-06-30
- Stack: TCL via Shopify JSON API, Amazon/Best Buy via Playwright headless, SQLite (better-sqlite3)
- Scheduling: launchd (7 PM PST) + GitHub Actions (11:00 UTC / 7 AM ET)
- Best Buy requires `BESTBUY_API_KEY` env var — direct scraping blocked by bot detection

## Next steps
- (inferred) Verify scheduled runs still executing: check `data/last-run-v4.json` timestamp vs. today
- (inferred) Review GitHub Actions logs in `.github/workflows/daily-prices.yml` for silent failures
- (inferred) Confirm launchd plist registered: `launchctl list | grep tcl`
- (inferred) Test full pipeline: `npm run run-all` to validate all phases
- (inferred) Audit `data/prices.db` schema and row counts
