#!/usr/bin/env python3
"""
TCL Price Monitor v4.1
Name-based matching with RED/YELLOW/GREEN alerts.
Searches Amazon/Best Buy by MODEL NAME only, validates exact title match.
Generates HTML report in template format with trend tracking.
"""

import os
import json
import re
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
from dataclasses import dataclass, asdict

from supabase_storage import BaselineStorage

# === BASE PATH ===
BASE = '/Volumes/workssd/CoworkOS' if os.path.exists('/Volumes/workssd') else os.path.expanduser('~/CoworkOS')

# === DATA CLASSES ===
@dataclass
class Product:
    model_name: str
    name: str
    category: str
    dtc_price: float | None = None
    dtc_url: str | None = None
    dtc_on_sale: bool = False
    amazon_price: float | None = None
    amazon_url: str | None = None
    amazon_seller: str | None = None
    bestbuy_price: float | None = None
    bestbuy_url: str | None = None

# === CONSTANTS ===
WINDOWS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
DATE = datetime.now(timezone.utc).strftime('%Y-%m-%d')
HOUR = datetime.now(timezone.utc).strftime('%H:%M:%S')

# === UTILS ===
def log_run(status, msg):
    log_path = Path(BASE) / 'logs' / 'price_check_runs.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, 'a') as f:
        f.write(f"{DATE} {HOUR} | status={status} | {msg}\n")
    print(f"[{status}] {msg}")

def already_ran_today(force=False):
    if force:
        return False
    log_path = Path(BASE) / 'logs' / 'price_check_runs.log'
    if not log_path.exists():
        return False
    with open(log_path, 'r') as f:
        lines = f.readlines()
    return lines and DATE in lines[-1] if lines else False

def preflight(force=False):
    script_path = Path(BASE) / 'skills' / 'price-monitor' / 'price_check_v4_1.py'
    if not script_path.exists():
        log_run("ABORT", "script not found")
        sys.exit(1)

    data_dir = Path(BASE) / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)

    if already_ran_today(force=force):
        log_run("SKIP", "already_ran_today")
        sys.exit(0)

    try:
        req = urllib.request.Request("https://us.tcl.com", headers={'User-Agent': 'curl'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status != 200:
                log_run("ABORT", f"network_unreachable (http {resp.status})")
                sys.exit(1)
    except Exception as e:
        log_run("ABORT", f"network_error: {e}")
        sys.exit(1)

    log_run("OK", "preflight passed")

def extract_model_name(title):
    """Extract model number from title: 'TCL 55QM6K 4K Smart TV' → '55QM6K'"""
    match = re.search(r'(\d+[A-Z0-9]{4,})', title.upper())
    return match.group(1) if match else None

def fetch_tcl_active_sales():
    """Fetch active sales from us.tcl.com dynamically with model name extraction."""
    products = {}
    try:
        page = 1
        total_fetched = 0
        while True:
            req = urllib.request.Request(
                f"https://us.tcl.com/products.json?limit=250&page={page}",
                headers={'User-Agent': WINDOWS_UA}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            product_list = data.get('products', [])
            if not product_list:
                break

            for product in product_list:
                title = product.get('title', '').upper()
                handle = product.get('handle', '')
                product_type = product.get('product_type', '').upper()

                # Extract model name (primary key)
                model_name = extract_model_name(title)
                if not model_name:
                    continue

                variants = product.get('variants', [])
                if not variants:
                    continue

                v = variants[0]
                dtc_price = float(v.get('price', 0)) if v.get('price') else None
                compare_at = float(v.get('compare_at_price', 0)) if v.get('compare_at_price') else None

                products[model_name] = Product(
                    model_name=model_name,
                    name=title,
                    category=product_type,
                    dtc_price=dtc_price,
                    dtc_url=f"https://us.tcl.com/products/{handle}",
                    dtc_on_sale=bool(compare_at and compare_at > dtc_price),
                )
                total_fetched += 1

            if len(product_list) < 250:
                break
            page += 1

        log_run("OK", f"tcl_dtc fetched {total_fetched} products")
        return products
    except Exception as e:
        log_run("ERROR", f"tcl_dtc_fetch_failed: {e}")
        return {}

def fetch_amazon_price(model_name):
    """Search Amazon by model name, verify exact title match."""
    try:
        search_term = model_name.replace(' ', '+')
        req = urllib.request.Request(
            f"https://www.amazon.com/s?k={search_term}",
            headers={'User-Agent': WINDOWS_UA}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode('utf-8', errors='ignore')

        # Extract first product link
        asin_match = re.search(r'/dp/([A-Z0-9]{10})', html)
        if not asin_match:
            return None, None, None

        asin = asin_match.group(1)
        url = f"https://www.amazon.com/dp/{asin}"

        # Fetch product page
        req = urllib.request.Request(url, headers={'User-Agent': WINDOWS_UA})
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode('utf-8', errors='ignore')

        # Verify model name in title
        title_match = re.search(r'<span id="productTitle"[^>]*>([^<]+)</span>', html)
        if not title_match or model_name not in title_match.group(1).upper():
            return None, None, None  # Title doesn't match model

        # Extract price
        price_match = re.search(r'class="a-offscreen">\$([\d,]+\.?\d*)', html)
        if price_match:
            price = float(price_match.group(1).replace(',', ''))
            return price, url, "Amazon"

        return None, url, None
    except Exception as e:
        return None, None, None

def fetch_bestbuy_price(model_name):
    """Search Best Buy by model name, verify exact title match."""
    try:
        search_term = model_name.replace(' ', '+')
        req = urllib.request.Request(
            f"https://www.bestbuy.com/site/searchpage.jsp?st={search_term}",
            headers={'User-Agent': WINDOWS_UA}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode('utf-8', errors='ignore')

        # Extract first product link
        product_match = re.search(r'href="(/site/[^"]*)"[^>]*>.*?<h4[^>]*>([^<]+)</h4>', html, re.DOTALL)
        if not product_match:
            return None, None

        product_url = "https://www.bestbuy.com" + product_match.group(1)
        product_title = product_match.group(2)

        # Verify model name in title
        if model_name not in product_title.upper():
            return None, None  # Title doesn't match model

        # Fetch product page for exact price
        req = urllib.request.Request(product_url, headers={'User-Agent': WINDOWS_UA})
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode('utf-8', errors='ignore')

        # Extract price
        price_match = re.search(r'\$\s*([\d,]+\.?\d*)', html)
        if price_match:
            price = float(price_match.group(1).replace(',', ''))
            return price, product_url

        return None, product_url
    except Exception as e:
        return None, None

def fetch_channel_prices(products):
    """Fetch prices from Amazon and Best Buy with rate limiting and name matching."""
    for i, (model_name, product) in enumerate(products.items()):
        if i > 0 and i % 5 == 0:
            time.sleep(8)
        else:
            time.sleep(3)

        # Amazon
        amazon_price, amazon_url, seller = fetch_amazon_price(model_name)
        product.amazon_price = amazon_price
        product.amazon_url = amazon_url
        product.amazon_seller = seller

        # Best Buy
        bestbuy_price, bestbuy_url = fetch_bestbuy_price(model_name)
        product.bestbuy_price = bestbuy_price
        product.bestbuy_url = bestbuy_url

    amazon_found = sum(1 for p in products.values() if p.amazon_price)
    bestbuy_found = sum(1 for p in products.values() if p.bestbuy_price)
    log_run("OK", f"channel_prices fetched (amazon={amazon_found}, bestbuy={bestbuy_found})")

def classify_alerts(products):
    """Classify alerts: RED (>15%), YELLOW (5-15%), GREEN (<5%)"""
    alerts = {'RED': [], 'YELLOW': [], 'GREEN': []}

    for model_name, product in products.items():
        if not product.dtc_price:
            continue

        # Get competing prices
        channel_prices = []
        if product.amazon_price:
            channel_prices.append(('Amazon', product.amazon_price))
        if product.bestbuy_price:
            channel_prices.append(('Best Buy', product.bestbuy_price))

        if not channel_prices:
            alerts['GREEN'].append({
                'model': model_name,
                'dtc': product.dtc_price,
                'amazon': None,
                'bestbuy': None,
                'max_gap': 0,
                'gap_pct': 0,
                'issue': 'No competing prices found',
            })
            continue

        min_channel_price = min([p[1] for p in channel_prices])
        gap_abs = product.dtc_price - min_channel_price
        gap_pct = (gap_abs / product.dtc_price) * 100

        alert = {
            'model': model_name,
            'dtc': product.dtc_price,
            'amazon': product.amazon_price,
            'bestbuy': product.bestbuy_price,
            'max_gap': round(gap_abs, 2),
            'gap_pct': round(gap_pct, 1),
            'dtc_url': product.dtc_url,
            'amazon_url': product.amazon_url,
            'bestbuy_url': product.bestbuy_url,
        }

        if gap_pct > 15:
            alert['issue'] = 'Significant channel undercut (>15%)'
            alerts['RED'].append(alert)
        elif gap_pct > 5:
            alert['issue'] = 'Moderate gap (5-15%)'
            alerts['YELLOW'].append(alert)
        else:
            alert['issue'] = 'DTC competitive or lowest'
            alerts['GREEN'].append(alert)

    return alerts

def calculate_changes(products, baseline):
    """Track price changes vs previous run."""
    changes = []

    for model_name, product in products.items():
        if model_name not in baseline:
            continue

        old_dtc = baseline[model_name].get('dtc_price')
        new_dtc = product.dtc_price

        if old_dtc and new_dtc and old_dtc != new_dtc:
            delta = new_dtc - old_dtc
            changes.append({
                'model': model_name,
                'old_dtc': old_dtc,
                'new_dtc': new_dtc,
                'change': round(delta, 2),
                'change_pct': round((delta / old_dtc) * 100, 1),
                'type': 'sale' if delta < 0 else 'increase',
            })

    return sorted(changes, key=lambda x: abs(x['change']), reverse=True)[:10]

def generate_html_report(products, alerts, changes):
    """Generate HTML report in template format."""
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>TCL Daily Price Monitor — {DATE}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        h1 {{ color: #333; border-bottom: 3px solid #1976d2; padding-bottom: 10px; }}
        h2 {{ color: #1976d2; margin-top: 30px; }}
        .header {{ background: white; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
        table {{ width: 100%; border-collapse: collapse; background: white; margin: 15px 0; }}
        th {{ background: #f0f0f0; padding: 10px; text-align: left; border-bottom: 2px solid #ddd; font-weight: bold; }}
        td {{ padding: 10px; border-bottom: 1px solid #eee; }}
        tr:hover {{ background: #f9f9f9; }}
        a {{ color: #1976d2; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .red {{ color: #d32f2f; font-weight: bold; }}
        .yellow {{ color: #f57c00; font-weight: bold; }}
        .green {{ color: #388e3c; font-weight: bold; }}
        .recs {{ background: #e3f2fd; padding: 15px; border-left: 4px solid #1976d2; margin: 15px 0; }}
        .alert-count {{ display: inline-block; margin: 0 10px; }}
    </style>
</head>
<body>
    <h1>TCL Daily Price Monitor — {DATE}</h1>
    <div class="header">
        <p><strong>Mode:</strong> Name-based matching | <strong>Products:</strong> {len(products)} |
        <strong>Channels:</strong> DTC (us.tcl.com), Amazon, Best Buy |
        <strong>Alerts:</strong>
        <span class="alert-count red">🔴 {len(alerts['RED'])} RED</span>
        <span class="alert-count yellow">🟡 {len(alerts['YELLOW'])} YELLOW</span>
        <span class="alert-count green">🟢 {len(alerts['GREEN'])} GREEN</span></p>
    </div>

    <h2>🔴 RED ALERTS — Immediate Attention Required</h2>
    <table>
        <tr>
            <th>Model</th>
            <th>DTC</th>
            <th>Amazon</th>
            <th>Best Buy</th>
            <th>Max Gap</th>
            <th>Issue</th>
            <th>Links</th>
        </tr>
"""

    for alert in alerts['RED']:
        amazon_cell = f"${alert['amazon']:.2f}" if alert['amazon'] else "—"
        bestbuy_cell = f"${alert['bestbuy']:.2f}" if alert['bestbuy'] else "—"

        links = []
        if alert['dtc_url']:
            links.append(f"<a href='{alert['dtc_url']}' target='_blank'>DTC</a>")
        if alert['amazon_url']:
            links.append(f"<a href='{alert['amazon_url']}' target='_blank'>Amazon</a>")
        if alert['bestbuy_url']:
            links.append(f"<a href='{alert['bestbuy_url']}' target='_blank'>BB</a>")
        links_str = " | ".join(links)

        html += f"""
        <tr>
            <td><strong>{alert['model']}</strong></td>
            <td>${alert['dtc']:.2f}</td>
            <td>{amazon_cell}</td>
            <td>{bestbuy_cell}</td>
            <td class="red">${alert['max_gap']} ({alert['gap_pct']}%)</td>
            <td>{alert['issue']}</td>
            <td>{links_str}</td>
        </tr>
"""

    html += """
    </table>

    <h2>🟡 YELLOW ALERTS — Monitor</h2>
    <table>
        <tr>
            <th>Model</th>
            <th>DTC</th>
            <th>Amazon</th>
            <th>Best Buy</th>
            <th>Gap</th>
            <th>Links</th>
        </tr>
"""

    for alert in alerts['YELLOW']:
        amazon_cell = f"${alert['amazon']:.2f}" if alert['amazon'] else "—"
        bestbuy_cell = f"${alert['bestbuy']:.2f}" if alert['bestbuy'] else "—"

        links = []
        if alert['dtc_url']:
            links.append(f"<a href='{alert['dtc_url']}' target='_blank'>DTC</a>")
        if alert['amazon_url']:
            links.append(f"<a href='{alert['amazon_url']}' target='_blank'>Amazon</a>")
        if alert['bestbuy_url']:
            links.append(f"<a href='{alert['bestbuy_url']}' target='_blank'>BB</a>")
        links_str = " | ".join(links)

        html += f"""
        <tr>
            <td><strong>{alert['model']}</strong></td>
            <td>${alert['dtc']:.2f}</td>
            <td>{amazon_cell}</td>
            <td>{bestbuy_cell}</td>
            <td class="yellow">${alert['max_gap']} ({alert['gap_pct']}%)</td>
            <td>{links_str}</td>
        </tr>
"""

    html += """
    </table>

    <h2>🟢 GREEN — DTC Competitive</h2>
    <table>
        <tr>
            <th>Model</th>
            <th>DTC</th>
            <th>Best Buy</th>
            <th>Status</th>
            <th>Links</th>
        </tr>
"""

    for alert in alerts['GREEN']:
        bestbuy_cell = f"${alert['bestbuy']:.2f}" if alert['bestbuy'] else "—"

        links = []
        if alert['dtc_url']:
            links.append(f"<a href='{alert['dtc_url']}' target='_blank'>DTC</a>")
        if alert['bestbuy_url']:
            links.append(f"<a href='{alert['bestbuy_url']}' target='_blank'>BB</a>")
        links_str = " | ".join(links) if links else "—"

        html += f"""
        <tr>
            <td><strong>{alert['model']}</strong></td>
            <td>${alert['dtc']:.2f}</td>
            <td>{bestbuy_cell}</td>
            <td class="green">DTC Competitive ✓</td>
            <td>{links_str}</td>
        </tr>
"""

    html += """
    </table>

    <h2>📊 Changes vs. Previous Run</h2>
    <table>
        <tr>
            <th>Model</th>
            <th>Old DTC</th>
            <th>New DTC</th>
            <th>Change</th>
            <th>Type</th>
        </tr>
"""

    for change in changes:
        change_sign = "+" if change['change'] > 0 else ""
        change_type = f"<span class='red'>{change['type'].upper()}</span>"
        html += f"""
        <tr>
            <td><strong>{change['model']}</strong></td>
            <td>${change['old_dtc']:.2f}</td>
            <td>${change['new_dtc']:.2f}</td>
            <td>{change_sign}${change['change']} ({change_sign}{change['change_pct']}%)</td>
            <td>{change_type}</td>
        </tr>
"""

    html += """
    </table>

    <h2>💡 Recommendations</h2>
    <div class="recs">
"""

    for alert in alerts['RED'][:3]:
        html += f"<p><strong>{alert['model']}</strong> — {alert['issue']}. Review pricing strategy to reduce cart abandonment.</p>"

    html += """
    </div>
    <p style="color: #999; font-size: 12px; margin-top: 40px;">Generated {DATE} {HOUR} UTC | TCL Price Monitor v4.1</p>
</body>
</html>
""".format(DATE=DATE, HOUR=HOUR)

    return html

def send_email_report(pdf_path, alerts):
    """Send PDF report via email to configured recipient."""
    email_sender = os.getenv('EMAIL_SENDER')
    email_password = os.getenv('EMAIL_PASSWORD')
    email_recipient = os.getenv('EMAIL_RECIPIENT', 'affiliate@celldigital.co')

    if not email_sender or not email_password:
        log_run("INFO", "Email credentials not configured, skipping email send")
        return

    try:
        # Count alerts for subject line
        red_count = len(alerts.get('RED', []))
        yellow_count = len(alerts.get('YELLOW', []))
        green_count = len(alerts.get('GREEN', []))

        # Create message
        msg = MIMEMultipart()
        msg['From'] = email_sender
        msg['To'] = email_recipient
        msg['Subject'] = f"TCL Price Monitor Report {DATE} — {red_count} RED, {yellow_count} YELLOW"

        # Email body
        body = f"""TCL Price Monitor Report — {DATE}

Alert Summary:
• RED (>15% gap): {red_count}
• YELLOW (5-15% gap): {yellow_count}
• GREEN (<5% gap): {green_count}

PDF report attached with detailed price comparisons across DTC, Amazon, and Best Buy.

Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC
"""

        msg.attach(MIMEText(body, 'plain'))

        # Attach PDF
        if Path(pdf_path).exists():
            with open(pdf_path, 'rb') as attachment:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(attachment.read())

            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename= {Path(pdf_path).name}')
            msg.attach(part)

        # Send email via Gmail SMTP
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(email_sender, email_password)
        server.send_message(msg)
        server.quit()

        log_run("OK", f"Email report sent to {email_recipient}")

    except Exception as e:
        log_run("WARNING", f"Email send failed: {e}")

def save_report(html_content, alerts):
    """Save HTML report and generate PDF."""
    # Save HTML to reports directory
    report_path = Path(BASE) / 'reports' / 'price-check' / f'report_v4_1_{DATE}.html'
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with open(report_path, 'w') as f:
        f.write(html_content)

    log_run("OK", f"HTML report saved to {report_path.name}")

    # Generate PDF to Downloads (default output)
    pdf_path = Path.home() / 'Downloads' / f'tcl_price_monitor_{DATE}.pdf'
    try:
        from weasyprint import HTML
        HTML(string=html_content).write_pdf(str(pdf_path))
        log_run("OK", f"PDF report saved to {pdf_path.name}")
    except ImportError:
        try:
            import subprocess
            # Fallback: try using system tools if available
            temp_html = Path('/tmp') / f'tcl_report_{DATE}.html'
            temp_html.write_text(html_content)
            subprocess.run(['wkhtmltopdf', str(temp_html), str(pdf_path)], timeout=30, check=False)
            log_run("OK", f"PDF report saved to {pdf_path.name}")
            temp_html.unlink(missing_ok=True)
        except Exception as e:
            log_run("WARNING", f"PDF generation skipped: {e}")
    except Exception as e:
        log_run("WARNING", f"PDF generation failed: {e}")

def main(force=False):
    preflight(force=force)

    # Fetch products
    products = fetch_tcl_active_sales()
    if not products:
        log_run("ERROR", "no products found")
        sys.exit(1)

    # Load baseline
    storage = BaselineStorage(BASE)
    baseline = storage.load()

    # Fetch channel prices
    fetch_channel_prices(products)

    # Classify alerts
    alerts = classify_alerts(products)

    # Calculate changes
    changes = calculate_changes(products, baseline)

    # Generate HTML report
    html = generate_html_report(products, alerts, changes)

    # Save HTML and PDF
    pdf_path = Path.home() / 'Downloads' / f'tcl_price_monitor_{DATE}.pdf'
    save_report(html, alerts)

    # Send email report
    send_email_report(str(pdf_path), alerts)

    # Update baseline
    new_baseline = {
        model_name: {
            'dtc_price': p.dtc_price,
            'amazon_price': p.amazon_price,
            'bestbuy_price': p.bestbuy_price,
        }
        for model_name, p in products.items()
    }
    storage.save(new_baseline)

    # Summary
    log_run("OK", f"alerts: RED={len(alerts['RED'])}, YELLOW={len(alerts['YELLOW'])}, GREEN={len(alerts['GREEN'])}, products={len(products)}")

if __name__ == '__main__':
    force = '--force' in sys.argv
    main(force=force)
