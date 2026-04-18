#!/usr/bin/env python3
"""
TCL Price Monitor Optimized (Daily)
Uses pre-mapped SKU templates to compare prices only.
Minimal network calls: 3 URLs per product.
"""

import os
import json
import sys
import time
import urllib.request
import urllib.error
import smtplib
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime, timezone
from pathlib import Path

WINDOWS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
BASE = os.getenv('BASE_PATH', '/Volumes/workssd/CoworkOS' if os.path.exists('/Volumes/workssd') else os.path.expanduser('~/CoworkOS'))
DATE = datetime.now(timezone.utc).strftime('%Y-%m-%d')
HOUR = datetime.now(timezone.utc).strftime('%H:%M:%S')

def log_run(status, msg):
    log_path = Path(BASE) / 'logs' / 'price_check_runs.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, 'a') as f:
        f.write(f"{DATE} {HOUR} | status={status} | {msg}\n")
    print(f"[{status}] {msg}")

def load_mapping():
    """Load product mapping template."""
    mapping_path = Path(BASE) / 'data' / 'products_mapping.json'
    if not mapping_path.exists():
        log_run("ERROR", "products_mapping.json not found. Run product_mapping_setup.py first.")
        sys.exit(1)
    with open(mapping_path, 'r') as f:
        data = json.load(f)
    return data['products']

def fetch_price(url, retailer_name):
    """Fetch price from a single URL."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': WINDOWS_UA})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            # Extract price patterns (handles $X.XX, various formats)
            import re
            prices = re.findall(r'\$(\d+(?:,\d{3})*(?:\.\d{2})?)', html)
            if prices:
                # Clean and convert first price found
                price_str = prices[0].replace(',', '')
                return float(price_str)
    except Exception as e:
        log_run("WARN", f"{retailer_name} fetch failed: {e}")
    time.sleep(2)  # Rate limit
    return None

def classify_alert(sku, tcl_price, amazon_price, bestbuy_price):
    """Classify price gap alerts."""
    gaps = []

    if amazon_price and tcl_price:
        gap = amazon_price - tcl_price
        gap_pct = round((gap / tcl_price) * 100, 1)
        gaps.append({
            'retailer': 'Amazon',
            'price': amazon_price,
            'gap': gap,
            'gap_pct': gap_pct
        })

    if bestbuy_price and tcl_price:
        gap = bestbuy_price - tcl_price
        gap_pct = round((gap / tcl_price) * 100, 1)
        gaps.append({
            'retailer': 'Best Buy',
            'price': bestbuy_price,
            'gap': gap,
            'gap_pct': gap_pct
        })

    if not gaps:
        return None

    max_gap = max(g['gap'] for g in gaps)
    max_gap_pct = max(g['gap_pct'] for g in gaps)

    if max_gap_pct >= 15:
        return 'RED'
    elif max_gap_pct >= 5:
        return 'YELLOW'
    else:
        return 'GREEN'

def generate_html_report(products, prices, alerts):
    """Generate HTML report with price comparisons."""
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>TCL Price Monitor {DATE}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 20px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
        th {{ background: #f5f5f5; }}
        .red {{ background: #fee; color: #c00; font-weight: bold; }}
        .yellow {{ background: #ffe; color: #880; }}
        .green {{ background: #efe; color: #080; }}
        a {{ color: #0066cc; }}
        h2 {{ margin-top: 30px; }}
    </style>
</head>
<body>
<h1>TCL Price Monitor Report</h1>
<p>Generated: {DATE} {HOUR} UTC</p>

<h2>🔴 RED ALERTS (Price gap >15%)</h2>
<table>
    <tr><th>SKU</th><th>Product</th><th>DTC Price</th><th>Amazon</th><th>Best Buy</th><th>Max Gap</th></tr>
"""

    for sku, alert_type in alerts.items():
        if alert_type == 'RED':
            p = prices[sku]
            gap = max((p['amazon_price'] or 0) - p['tcl_price'], (p['bestbuy_price'] or 0) - p['tcl_price'])
            html += f"""    <tr class="red">
        <td>{sku}</td><td>{p['name']}</td>
        <td>${p['tcl_price']:.2f}</td>
        <td>${p['amazon_price']:.2f if p['amazon_price'] else '—'}</td>
        <td>${p['bestbuy_price']:.2f if p['bestbuy_price'] else '—'}</td>
        <td>${gap:.2f}</td>
    </tr>
"""

    html += """</table>

<h2>🟡 YELLOW ALERTS (Price gap 5-15%)</h2>
<table>
    <tr><th>SKU</th><th>Product</th><th>DTC Price</th><th>Amazon</th><th>Best Buy</th></tr>
"""

    for sku, alert_type in alerts.items():
        if alert_type == 'YELLOW':
            p = prices[sku]
            html += f"""    <tr class="yellow">
        <td>{sku}</td><td>{p['name']}</td>
        <td>${p['tcl_price']:.2f}</td>
        <td>${p['amazon_price']:.2f if p['amazon_price'] else '—'}</td>
        <td>${p['bestbuy_price']:.2f if p['bestbuy_price'] else '—'}</td>
    </tr>
"""

    html += """</table>

<h2>🟢 GREEN (Competitive pricing)</h2>
<table>
    <tr><th>SKU</th><th>Product</th><th>DTC Price</th><th>Amazon</th><th>Best Buy</th></tr>
"""

    for sku, alert_type in alerts.items():
        if alert_type == 'GREEN':
            p = prices[sku]
            html += f"""    <tr class="green">
        <td>{sku}</td><td>{p['name']}</td>
        <td>${p['tcl_price']:.2f}</td>
        <td>${p['amazon_price']:.2f if p['amazon_price'] else '—'}</td>
        <td>${p['bestbuy_price']:.2f if p['bestbuy_price'] else '—'}</td>
    </tr>
"""

    html += f"""</table>

<p style="color: #999; font-size: 12px; margin-top: 40px;">
    TCL Price Monitor v5 (Optimized) | {len([a for a in alerts.values() if a == 'RED'])} RED | {len([a for a in alerts.values() if a == 'YELLOW'])} YELLOW
</p>
</body>
</html>"""

    return html

def save_report(html_content, alerts):
    """Save HTML report and generate PDF."""
    report_path = Path(BASE) / 'reports' / 'price-check' / f'report_optimized_{DATE}.html'
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with open(report_path, 'w') as f:
        f.write(html_content)

    log_run("OK", f"HTML report saved to {report_path.name}")

    # Generate PDF
    pdf_path = Path.home() / 'Downloads' / f'tcl_price_monitor_{DATE}.pdf'
    try:
        from weasyprint import HTML
        HTML(string=html_content).write_pdf(str(pdf_path))
        log_run("OK", f"PDF report saved to {pdf_path.name}")
    except Exception as e:
        log_run("WARNING", f"PDF generation failed: {e}")

    return str(pdf_path)

def send_email_report(pdf_path, alerts):
    """Send PDF report via email."""
    email_sender = os.getenv('EMAIL_SENDER')
    email_password = os.getenv('EMAIL_PASSWORD')
    email_recipient = os.getenv('EMAIL_RECIPIENT', 'affiliate@celldigital.co')

    if not email_sender or not email_password:
        log_run("INFO", "Email credentials not configured, skipping email send")
        return

    try:
        red_count = len([a for a in alerts.values() if a == 'RED'])
        yellow_count = len([a for a in alerts.values() if a == 'YELLOW'])

        msg = MIMEMultipart()
        msg['From'] = email_sender
        msg['To'] = email_recipient
        msg['Subject'] = f"TCL Price Monitor {DATE} — {red_count} RED, {yellow_count} YELLOW"

        body = f"TCL Price Monitor Report\n\nRED: {red_count} | YELLOW: {yellow_count}\n\nSee attached PDF for details."
        msg.attach(MIMEText(body, 'plain'))

        if Path(pdf_path).exists():
            with open(pdf_path, 'rb') as attachment:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename= {Path(pdf_path).name}')
            msg.attach(part)

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(email_sender, email_password)
        server.send_message(msg)
        server.quit()

        log_run("OK", f"Email sent to {email_recipient}")
    except Exception as e:
        log_run("WARNING", f"Email send failed: {e}")

def main():
    log_run("OK", "Starting price check (optimized)...")

    # Load product mapping
    products = load_mapping()
    log_run("OK", f"Loaded {len(products)} product templates")

    # Fetch prices from all 3 URLs per product
    prices = {}
    for sku, product in products.items():
        tcl_price = fetch_price(product['tcl_url'], 'TCL')
        amazon_price = fetch_price(product['amazon_url'], 'Amazon') if product['amazon_url'] else None
        bestbuy_price = fetch_price(product['bestbuy_url'], 'Best Buy') if product['bestbuy_url'] else None

        if tcl_price:
            prices[sku] = {
                'name': product['name'],
                'tcl_price': tcl_price,
                'amazon_price': amazon_price,
                'bestbuy_price': bestbuy_price
            }

    log_run("OK", f"Fetched prices for {len(prices)} products")

    # Classify alerts
    alerts = {}
    for sku, price_data in prices.items():
        alert = classify_alert(
            sku,
            price_data['tcl_price'],
            price_data['amazon_price'],
            price_data['bestbuy_price']
        )
        if alert:
            alerts[sku] = alert

    # Generate and save reports
    html = generate_html_report(products, prices, alerts)
    pdf_path = save_report(html, alerts)

    # Send email
    send_email_report(pdf_path, alerts)

    # Summary
    red = len([a for a in alerts.values() if a == 'RED'])
    yellow = len([a for a in alerts.values() if a == 'YELLOW'])
    green = len([a for a in alerts.values() if a == 'GREEN'])
    log_run("OK", f"alerts: RED={red}, YELLOW={yellow}, GREEN={green}, products={len(prices)}")

if __name__ == '__main__':
    main()
