# TCL Price Monitor — Remote Agent Deployment

This document covers scheduling the price monitor as a remote Claude Code agent.

## Prerequisites

1. **GitHub Access**: Repository must be public (remote agents clone via HTTPS)
2. **Supabase MCP**: Connected via claude.ai settings
   - Connector UUID: `80c7c5cb-ff95-4924-a440-98fda6cc20d1`
   - URL: `https://mcp.supabase.com/mcp`
3. **Environment Setup**: `SUPABASE_URL` and `SUPABASE_KEY` configured in trigger

## Trigger Configuration

### Basic Setup
- **Name**: `price-monitor-enhanced`
- **Repository**: `https://github.com/[USERNAME]/tcl-price-monitor`
- **Model**: Claude Sonnet 4.6
- **Schedule**: `0 16 * * *` (4pm UTC = 8am PST daily)
- **MCP Connections**: Supabase
- **Enabled**: true

### Environment Variables (Remote)
Set in trigger job config:
```json
{
  "SUPABASE_URL": "https://izeixnkpquztaczehhum.supabase.co",
  "SUPABASE_KEY": "your_publishable_key_here",
  "BASE_PATH": "~/CoworkOS"
}
```

### Execution Prompt
```
Execute the price_check_v4_1.py script with --force flag to:

1. Fetch active sales from us.tcl.com/products.json
2. Extract model names (e.g., 55QM6K, 75QM7K)
3. Search Amazon and Best Buy by model name with strict title matching
4. Classify alerts: RED (>15% gap), YELLOW (5-15%), GREEN (<5%)
5. Track vs baseline from Supabase storage
6. Generate HTML + PDF reports with 3-platform comparison links

Output: HTML report saved to CoworkOS/reports/price-check/
        PDF report saved to ~/Downloads/tcl_price_monitor_YYYY-MM-DD.pdf

Report format: Color-coded alert sections (RED/YELLOW/GREEN), model names, 
prices from all platforms, clickable links, price change tracking.

Keep token usage minimal for Haiku re-runs.
```

## Expected Runtime

- **Duration**: 60-120 seconds
- **Tokens**: ~15K (input) + ~8K (output) for Sonnet
- **Network Calls**: ~30-50 HTTP requests (rate-limited)

## Monitoring

### Logs
Check execution results via:
```bash
# From CoworkOS logs directory
tail -f ~/CoworkOS/logs/price_check_runs.log
```

### Alert Counts
Each run logs:
```
[OK] alerts: HIGH={count}, MEDIUM={count}, products={total}
```

### PDF Output Verification
```bash
# Check if PDF was generated
ls -lh ~/Downloads/tcl_price_monitor_*.pdf
```

## Troubleshooting Remote Execution

### Network Errors
- Remote agent has internet access to us.tcl.com, Amazon, Best Buy
- If timeouts occur, check retailer availability (not local ISP issue)

### Supabase Connection Failed
- Baseline defaults to empty dict (first run)
- Script continues with local-only tracking
- Check MCP connection status in trigger config

### File Paths
- Remote agent cannot write to local ~/CoworkOS directly
- Reports saved in remote session's ephemeral filesystem
- Logs available via `get_logs()` within trigger execution

## Updating the Script

To deploy code changes:
1. Edit `price_check_v4_1.py` in GitHub repository
2. Trigger will pull latest version on next scheduled run
3. No need to update trigger configuration

## Disable/Reschedule

Edit trigger settings:
- **Change schedule**: Update cron expression
- **Change model**: Switch between Sonnet/Haiku (Haiku cheaper for repeated runs)
- **Disable**: Set `enabled: false`

---

**First Deployment Date**: 2026-04-17  
**Trigger Update Guide**: See claude.ai/code/scheduled
