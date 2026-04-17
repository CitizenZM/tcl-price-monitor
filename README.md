# TCL Price Monitor

Automated price monitoring and comparison for TCL products across DTC (us.tcl.com), Amazon, and Best Buy.

## Features

- **Dynamic Product Discovery**: Fetches active products from us.tcl.com with automatic model name extraction
- **Multi-Platform Comparison**: Searches Amazon and Best Buy using strict product title matching
- **Smart Alert Classification**: 
  - **RED**: Price gap >15% (MAP concern)
  - **YELLOW**: Price gap 5-15% (watch)
  - **GREEN**: Price gap <5% (healthy)
- **Baseline Tracking**: Stores price history in Supabase for trend analysis
- **HTML + PDF Reports**: Generates styled reports to CoworkOS/reports and ~/Downloads
- **Rate-Limited Scraping**: Respects retailer policies with 3-8 second delays between requests

## Installation

```bash
# Clone repository
git clone https://github.com/[USERNAME]/tcl-price-monitor.git
cd tcl-price-monitor

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your Supabase credentials
```

## Usage

### Manual Execution
```bash
# Standard run (checks if already ran today)
python price_check_v4_1.py

# Force run (bypass daily check)
python price_check_v4_1.py --force
```

### Output
- **HTML Report**: `~/CoworkOS/reports/price-check/report_v4_1_YYYY-MM-DD.html`
- **PDF Report**: `~/Downloads/tcl_price_monitor_YYYY-MM-DD.pdf`
- **Logs**: `~/CoworkOS/logs/price_check_runs.log`

## Architecture

### Data Flow
1. **Fetch DTC**: Paginated query to `us.tcl.com/products.json` → extract model names
2. **Search Amazon**: Query with model name + strict title validation
3. **Search Best Buy**: SKU search + title verification
4. **Classify**: Compare prices, generate RED/YELLOW/GREEN alerts
5. **Track**: Save to Supabase baseline via BaselineStorage
6. **Report**: Generate HTML with links + PDF export to Downloads

### Key Classes

**Product** (dataclass)
- `model_name`: TCL model number (e.g., "55QM6K")
- `name`: Full product title
- `dtc_price`, `amazon_price`, `bestbuy_price`: Current prices
- `dtc_on_sale`: Boolean flag for DTC sale status

**BaselineStorage**
- `load()`: Fetch previous prices from Supabase
- `save(baseline)`: Persist new prices for next run
- Fallback to local JSON if Supabase unavailable

## Scheduled Deployment

This script is designed for automated remote execution via Claude Code triggers.

### Remote Agent Setup
See `docs/DEPLOYMENT.md` for Supabase MCP connection and cron scheduling.

**Recommended Schedule**: `0 16 * * *` (4pm UTC = 8am PST daily)

**Model**: Claude Sonnet 4.6

**MCP Connections**: Supabase (for baseline persistence)

## Configuration

### Environment Variables
- `SUPABASE_URL`: Your Supabase project URL
- `SUPABASE_KEY`: Publishable API key
- `BASE_PATH`: Root directory for reports/logs (defaults to ~/CoworkOS)
- `FORCE_RUN`: Override daily check (CLI flag preferred)

### Alert Thresholds
Modify in `classify_alerts()`:
- `RED`: `gap_abs >= dtc_price * 0.15`
- `YELLOW`: `gap_abs >= dtc_price * 0.05`

## Troubleshooting

### "Network unreachable"
- Check internet connection
- Verify us.tcl.com is accessible
- Check for ISP/firewall blocks

### "Amazon/Best Buy prices null"
- Amazon product may be blocked/unavailable
- Best Buy search may return no results
- Verify model name extraction in logs

### "Supabase connection failed"
- Check SUPABASE_URL and SUPABASE_KEY in .env
- Verify internet connectivity
- Script will fallback to local JSON storage

## License

Internal use only. Do not distribute.

---

**Last Updated**: 2026-04-17  
**Version**: 4.1 (Model Name Matching, RED/YELLOW/GREEN Alerts, PDF Export)
