#!/usr/bin/env python3
"""
TCL Price Monitor v4.1
Fetches TCL.com active products, cross-checks Amazon + Best Buy,
classifies price gaps (RED/YELLOW/GREEN), and generates HTML + PDF reports.

Usage: python3 price_check_v4_1.py [--force]
"""

import argparse
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Config ────────────────────────────────────────────────────────────────────

TODAY = date.today().isoformat()
NOW_UTC = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / 'data'
REPORTS_DIR = Path.home() / 'CoworkOS' / 'reports' / 'price-check'
DOWNLOADS_DIR = Path.home() / 'Downloads'
SEED_URLS_FILE = DATA_DIR / 'seed-urls.json'
BASELINE_FILE = DATA_DIR / 'price-baselines.json'

TCL_BASE = 'https://us.tcl.com'

SUPABASE_URL = 'https://vyluxphyxfiygdkaprks.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZ5bHV4cGh5eGZpeWdka2FwcmtzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI0MzA5NTQsImV4cCI6MjA4ODAwNjk1NH0.zEfEvLSwRi0H3vYOvyDXXvFwDxVY4RtCSuplLoKRe_0'

BESTBUY_API_KEY = ''
GMAIL_APP_PASSWORD = ''
SENDER_EMAIL = 'affiliate@celldigital.co'
RECIPIENT_EMAIL = 'affiliate@celldigital.co'

# Alert thresholds: competitor price vs TCL price, expressed as fraction
RED_THRESHOLD = 0.15     # competitor >15% cheaper than TCL
YELLOW_THRESHOLD = 0.05  # competitor 5-15% cheaper

AMZ_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Cache-Control': 'max-age=0',
}

ALERT_STYLES = {
    'RED':    {'bg': '#ffebee', 'color': '#c62828', 'icon': '🔴', 'label': 'RED'},
    'YELLOW': {'bg': '#fff8e1', 'color': '#f57f17', 'icon': '🟡', 'label': 'YELLOW'},
    'GREEN':  {'bg': '#e8f5e9', 'color': '#2e7d32', 'icon': '🟢', 'label': 'GREEN'},
    'PARITY': {'bg': '#f5f5f5', 'color': '#616161', 'icon': '⚪', 'label': 'PARITY'},
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_session():
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retry))
    return session


def load_env():
    """Load .env file into os.environ and refresh globals."""
    global BESTBUY_API_KEY, GMAIL_APP_PASSWORD
    env_file = SCRIPT_DIR / '.env'
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            m = re.match(r'^([^#=\s][^=]*)=(.*)$', line.strip())
            if m:
                os.environ.setdefault(m.group(1).strip(), m.group(2).strip())
    BESTBUY_API_KEY = os.getenv('BESTBUY_API_KEY', '')
    GMAIL_APP_PASSWORD = os.getenv('GMAIL_APP_PASSWORD', '')


def fmt_price(p):
    return f'${p:,.2f}' if p is not None else '—'


def gap_pct(tcl, comp):
    """Return (comp - tcl) / tcl * 100. Negative = competitor cheaper."""
    if not tcl or not comp:
        return None
    return (comp - tcl) / tcl * 100


# ── Phase 1: Fetch TCL products ───────────────────────────────────────────────

def extract_model(title):
    m = re.search(r'[–\-]\s*([A-Z0-9][A-Z0-9\-]+)\s*$', title, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'\b([A-Z]{1,3}\d{2,4}[A-Z0-9]+)\b', title) \
        or re.search(r'\b(\d{2,3}[A-Z][A-Z0-9]+)\b', title)
    return m.group(1).strip() if m else None


def fetch_tcl_products(session):
    print('\n── Phase 1: Fetching TCL.com products (Shopify API) ──')
    all_products = []
    page = 1
    while True:
        try:
            resp = session.get(f'{TCL_BASE}/products.json?limit=250&page={page}', timeout=15)
            if not resp.ok:
                break
            data = resp.json()
            batch = data.get('products') or []
            if not batch:
                break
            all_products.extend(batch)
            if len(batch) < 250:
                break
            page += 1
        except Exception as e:
            print(f'  ❌ TCL API error: {e}')
            break

    products = []
    seen_models = set()
    for p in all_products:
        v = (p.get('variants') or [{}])[0]
        price = float(v['price']) if v.get('price') else None
        if not price:
            continue
        compare_at = float(v['compare_at_price']) if v.get('compare_at_price') else None
        model = extract_model(p['title'])
        if not model or model in seen_models:
            continue
        seen_models.add(model)
        products.append({
            'model': model,
            'title': p['title'],
            'tcl_price': price,
            'tcl_compare_at': compare_at,
            'tcl_on_sale': bool(compare_at and compare_at > price),
            'tcl_url': f"{TCL_BASE}/products/{p['handle']}",
            'tcl_in_stock': bool(v.get('available', False)),
            'amazon_url': None,
            'bestbuy_url': None,
            'amazon_price': None,
            'bestbuy_price': None,
            'amazon_title': None,
            'bestbuy_title': None,
        })

    on_sale = sum(1 for p in products if p['tcl_on_sale'])
    print(f'  {len(all_products)} raw products → {len(products)} unique SKUs, {on_sale} on sale')
    return products


# ── Phase 2: Load seed URLs ───────────────────────────────────────────────────

def load_seed_urls(products):
    print('\n── Phase 2: Matching seed URLs ──')
    if not SEED_URLS_FILE.exists():
        print(f'  ⚠ {SEED_URLS_FILE} not found')
        return products
    seeds = json.loads(SEED_URLS_FILE.read_text())
    matched = 0
    for p in products:
        seed = seeds.get(p['model']) or seeds.get(p['model'].upper())
        if seed:
            p['amazon_url'] = seed.get('amazon_url')
            p['bestbuy_url'] = seed.get('bestbuy_url')
            matched += 1
    print(f'  Matched {matched}/{len(products)} products to seed URLs')
    return products


# ── Phase 3: Fetch Amazon prices ──────────────────────────────────────────────

def _extract_amazon_price(html_text):
    """Try multiple HTML patterns to extract price."""
    # Pattern 1: a-price-whole + fraction
    m = re.search(r'class="[^"]*a-price-whole[^"]*"[^>]*>([0-9,]+)<', html_text)
    if m:
        whole = m.group(1).replace(',', '')
        m2 = re.search(r'class="[^"]*a-price-fraction[^"]*"[^>]*>(\d+)<', html_text)
        frac = m2.group(1) if m2 else '00'
        try:
            return float(f'{whole}.{frac}')
        except ValueError:
            pass
    # Pattern 2: offscreen span with $
    m = re.search(r'class="[^"]*a-offscreen[^"]*"[^>]*>\$([0-9,]+\.?\d*)<', html_text)
    if m:
        try:
            return float(m.group(1).replace(',', ''))
        except ValueError:
            pass
    # Pattern 3: JSON-LD price
    m = re.search(r'"price"\s*:\s*"?(\d+\.?\d*)"?', html_text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _validate_amazon_title(html_text, model):
    """Return (valid, title_str). Valid when model appears in page title."""
    m = re.search(r'id="productTitle"[^>]*>\s*([^<]+)', html_text, re.I)
    if m:
        title = m.group(1).strip()
        return model.upper() in title.upper(), title
    return False, None


def fetch_amazon_prices(session, products):
    print('\n── Phase 3: Fetching Amazon prices ──')
    amz = [p for p in products if p.get('amazon_url')]
    print(f'  {len(amz)} products with Amazon URLs')

    for i, p in enumerate(amz):
        time.sleep(1.5 + (i % 4) * 0.4)
        try:
            resp = session.get(p['amazon_url'], headers=AMZ_HEADERS, timeout=25, allow_redirects=True)
            if resp.status_code == 200:
                price = _extract_amazon_price(resp.text)
                valid, title = _validate_amazon_title(resp.text, p['model'])
                if price:
                    p['amazon_price'] = price
                    p['amazon_title'] = title
                    icon = '✅' if valid else '⚠ '
                    print(f'  {icon} {p["model"]:12s} Amazon: {fmt_price(price)}'
                          + (f'  (title mismatch)' if not valid else ''))
                else:
                    print(f'  ❌ {p["model"]:12s} Amazon: price not found in page')
            elif resp.status_code in (503, 429):
                print(f'  🤖 {p["model"]:12s} Amazon: bot-blocked ({resp.status_code})')
            else:
                print(f'  ❌ {p["model"]:12s} Amazon: HTTP {resp.status_code}')
        except Exception as e:
            print(f'  ❌ {p["model"]:12s} Amazon: {str(e)[:70]}')

    found = sum(1 for p in amz if p['amazon_price'])
    print(f'  Amazon: {found}/{len(amz)} prices fetched')
    return products


# ── Phase 4: Fetch Best Buy prices ───────────────────────────────────────────

def _bb_sku(url):
    m = re.search(r'/(\d{6,7})\.p', url or '')
    return m.group(1) if m else None


def fetch_bestbuy_prices(session, products):
    print('\n── Phase 4: Fetching Best Buy prices ──')
    bb = [p for p in products if p.get('bestbuy_url')]
    print(f'  {len(bb)} products with Best Buy URLs')

    if not BESTBUY_API_KEY:
        print('  ⚠ BESTBUY_API_KEY not set — skipping Best Buy (set in .env to enable)')
        return products

    batch_size = 10
    for i in range(0, len(bb), batch_size):
        batch = bb[i:i + batch_size]
        sku_ids = [_bb_sku(p['bestbuy_url']) for p in batch]
        sku_ids = [s for s in sku_ids if s]
        if not sku_ids:
            continue
        filter_str = '|'.join(f'sku={s}' for s in sku_ids)
        try:
            resp = session.get(
                f'https://api.bestbuy.com/v1/products({filter_str})',
                params={
                    'apiKey': BESTBUY_API_KEY,
                    'format': 'json',
                    'show': 'sku,name,salePrice,regularPrice,onSale,onlineAvailability',
                },
                timeout=15,
            )
            if resp.ok:
                for prod in resp.json().get('products', []):
                    match = next((p for p in batch if _bb_sku(p['bestbuy_url']) == str(prod['sku'])), None)
                    if not match:
                        continue
                    price = prod.get('salePrice') or prod.get('regularPrice')
                    title = prod.get('name', '')
                    # Strict title validation: model must appear in product name
                    valid = match['model'].upper() in title.upper()
                    if price:
                        match['bestbuy_price'] = float(price)
                        match['bestbuy_title'] = title
                        icon = '✅' if valid else '⚠ '
                        sale_tag = ' SALE' if prod.get('onSale') else ''
                        print(f'  {icon} {match["model"]:12s} BB: {fmt_price(price)}{sale_tag}'
                              + (f'  (title mismatch)' if not valid else ''))
                    else:
                        print(f'  ❌ {match["model"]:12s} BB: no price in API response')
            else:
                print(f'  ❌ BB API error: HTTP {resp.status_code}')
        except Exception as e:
            print(f'  ❌ BB batch error: {str(e)[:70]}')
        time.sleep(0.4)

    found = sum(1 for p in bb if p['bestbuy_price'])
    print(f'  Best Buy: {found}/{len(bb)} prices fetched')
    return products


# ── Phase 5: Price baselines (Supabase + local fallback) ─────────────────────

def load_supabase_baselines():
    try:
        resp = requests.get(
            f'{SUPABASE_URL}/rest/v1/tcl_price_baselines',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            params={'select': 'model,platform,baseline_price'},
            timeout=10,
        )
        if resp.ok:
            rows = resp.json()
            return {f'{r["model"]}_{r["platform"]}': float(r["baseline_price"] or 0) for r in rows if r["baseline_price"]}
    except Exception as e:
        print(f'  ⚠ Supabase load error: {e}')
    return {}


def save_supabase_baselines(products):
    rows = []
    for p in products:
        for platform, key in [('tcl', 'tcl_price'), ('amazon', 'amazon_price'), ('bestbuy', 'bestbuy_price')]:
            price = p.get(key)
            if price:
                rows.append({
                    'model': p['model'],
                    'platform': platform,
                    'baseline_price': price,
                    'current_price': price,
                    'last_checked': TODAY,
                })
    if not rows:
        return
    try:
        resp = requests.post(
            f'{SUPABASE_URL}/rest/v1/tcl_price_baselines',
            headers={
                'apikey': SUPABASE_KEY,
                'Authorization': f'Bearer {SUPABASE_KEY}',
                'Content-Type': 'application/json',
                'Prefer': 'resolution=ignore-duplicates',
            },
            json=rows,
            timeout=20,
        )
        if resp.ok or resp.status_code == 201:
            print(f'  ✅ Supabase: saved {len(rows)} baseline records')
        else:
            print(f'  ⚠ Supabase save HTTP {resp.status_code}: {resp.text[:120]}')
    except Exception as e:
        print(f'  ⚠ Supabase save error: {e}')


def load_local_baselines():
    if BASELINE_FILE.exists():
        try:
            return json.loads(BASELINE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_local_baselines(products):
    baselines = load_local_baselines()
    for p in products:
        for platform, key in [('tcl', 'tcl_price'), ('amazon', 'amazon_price'), ('bestbuy', 'bestbuy_price')]:
            price = p.get(key)
            if price:
                bkey = f'{p["model"]}_{platform}'
                if bkey not in baselines:
                    baselines[bkey] = price
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(json.dumps(baselines, indent=2))


# ── Phase 6: Alert classification ────────────────────────────────────────────

def classify_alert(tcl_price, comp_price):
    """Classify based on competitor price relative to TCL.
    Returns 'RED'|'YELLOW'|'GREEN'|'PARITY'|None.
    """
    if not tcl_price or not comp_price:
        return None
    # gap < 0 means competitor cheaper; gap > 0 means TCL cheaper
    gap = (comp_price - tcl_price) / tcl_price
    if gap < -RED_THRESHOLD:
        return 'RED'
    if gap < -YELLOW_THRESHOLD:
        return 'YELLOW'
    if gap > YELLOW_THRESHOLD:
        return 'GREEN'
    return 'PARITY'


def build_alerts(products, baselines):
    alerts = []
    for p in products:
        tcl = p['tcl_price']
        for platform, price_key, url_key in [
            ('amazon',  'amazon_price',  'amazon_url'),
            ('bestbuy', 'bestbuy_price', 'bestbuy_url'),
        ]:
            comp = p.get(price_key)
            level = classify_alert(tcl, comp)
            if level is None:
                continue
            g = gap_pct(tcl, comp)  # negative = comp cheaper
            bkey = f'{p["model"]}_{platform}'
            baseline = baselines.get(bkey)
            price_change = (comp - baseline) if (comp and baseline) else None
            alerts.append({
                'model':        p['model'],
                'title':        p['title'],
                'platform':     platform,
                'level':        level,
                'tcl_price':    tcl,
                'tcl_url':      p['tcl_url'],
                'tcl_on_sale':  p['tcl_on_sale'],
                'tcl_compare_at': p['tcl_compare_at'],
                'comp_price':   comp,
                'comp_url':     p.get(url_key),
                'gap_pct':      g,
                'baseline':     baseline,
                'price_change': price_change,
            })

    level_order = {'RED': 0, 'YELLOW': 1, 'PARITY': 2, 'GREEN': 3}
    alerts.sort(key=lambda a: (level_order.get(a['level'], 9), a['gap_pct'] or 0))
    return alerts


# ── Phase 7: HTML report ──────────────────────────────────────────────────────

def _alert_table(alert_list):
    if not alert_list:
        return '<p style="color:#999;font-style:italic;padding:8px">None.</p>'
    rows = []
    for a in alert_list:
        st = ALERT_STYLES.get(a['level'], ALERT_STYLES['PARITY'])
        g = a['gap_pct']
        gap_str = f'{g:+.1f}%' if g is not None else '—'
        gap_color = st['color'] if g is not None and g < 0 else '#2e7d32'
        change_str = ''
        if a['price_change'] is not None:
            arrow = '↓' if a['price_change'] < 0 else '↑'
            change_str = f'{arrow}${abs(a["price_change"]):.2f}'
        sale_tag = ' <span style="background:#e65100;color:white;padding:1px 5px;border-radius:3px;font-size:10px;margin-left:4px">SALE</span>' if a['tcl_on_sale'] else ''
        tcl_link = f'<a href="{a["tcl_url"]}" target="_blank">{fmt_price(a["tcl_price"])}</a>{sale_tag}'
        comp_link = (f'<a href="{a["comp_url"]}" target="_blank">{fmt_price(a["comp_price"])}</a>'
                     if a["comp_url"] else fmt_price(a["comp_price"]))
        baseline_str = fmt_price(a['baseline']) if a['baseline'] else '—'
        rows.append(
            f'<tr style="background:{st["bg"]}">'
            f'<td style="font-weight:600">{st["icon"]} {a["model"]}</td>'
            f'<td>{a["platform"].title()}</td>'
            f'<td>{tcl_link}</td>'
            f'<td>{comp_link}</td>'
            f'<td style="color:{gap_color};font-weight:700">{gap_str}</td>'
            f'<td style="color:#888">{baseline_str}</td>'
            f'<td style="color:{"#c62828" if a["price_change"] and a["price_change"]<0 else "#2e7d32"}">{change_str}</td>'
            '</tr>'
        )
    return (
        '<table><tr>'
        '<th>Model</th><th>Platform</th><th>TCL.com</th>'
        '<th>Competitor</th><th>Gap</th><th>Baseline</th><th>Δ Baseline</th>'
        '</tr>' + ''.join(rows) + '</table>'
    )


def _all_products_table(products):
    rows = []
    for p in sorted(products, key=lambda x: x['model']):
        amz_level = classify_alert(p['tcl_price'], p['amazon_price'])
        bb_level  = classify_alert(p['tcl_price'], p['bestbuy_price'])
        amz_color = ALERT_STYLES.get(amz_level, {}).get('color', '#333') if amz_level in ('RED', 'YELLOW') else '#333'
        bb_color  = ALERT_STYLES.get(bb_level,  {}).get('color', '#333') if bb_level  in ('RED', 'YELLOW') else '#333'

        sale = ' <span style="background:#e65100;color:white;padding:1px 4px;border-radius:2px;font-size:10px">SALE</span>' if p['tcl_on_sale'] else ''
        tcl_was = f' <small style="color:#999;text-decoration:line-through">{fmt_price(p["tcl_compare_at"])}</small>' if p['tcl_compare_at'] else ''

        amz_g  = gap_pct(p['tcl_price'], p['amazon_price'])
        bb_g   = gap_pct(p['tcl_price'], p['bestbuy_price'])
        amz_gs = f'{amz_g:+.1f}%' if amz_g is not None else '—'
        bb_gs  = f'{bb_g:+.1f}%' if bb_g is not None else '—'

        amz_cell = (f'<a href="{p["amazon_url"]}" target="_blank" style="color:{amz_color}">'
                    f'{fmt_price(p["amazon_price"])}</a>' if p['amazon_url'] else fmt_price(p['amazon_price']))
        bb_cell  = (f'<a href="{p["bestbuy_url"]}" target="_blank" style="color:{bb_color}">'
                    f'{fmt_price(p["bestbuy_price"])}</a>' if p['bestbuy_url'] else fmt_price(p['bestbuy_price']))

        rows.append(
            '<tr>'
            f'<td><a href="{p["tcl_url"]}" target="_blank"><strong>{p["model"]}</strong></a></td>'
            f'<td>{fmt_price(p["tcl_price"])}{tcl_was}{sale}</td>'
            f'<td>{amz_cell}</td>'
            f'<td>{bb_cell}</td>'
            f'<td style="color:{amz_color};font-weight:{"600" if amz_level in ("RED","YELLOW") else "400"}">{amz_gs}</td>'
            f'<td style="color:{bb_color};font-weight:{"600" if bb_level in ("RED","YELLOW") else "400"}">{bb_gs}</td>'
            '</tr>'
        )
    return (
        '<table>'
        '<tr><th>Model</th><th>TCL.com</th><th>Amazon</th><th>Best Buy</th>'
        '<th>Amz vs TCL</th><th>BB vs TCL</th></tr>'
        + ''.join(rows) + '</table>'
    )


def generate_html_report(products, alerts):
    red    = [a for a in alerts if a['level'] == 'RED']
    yellow = [a for a in alerts if a['level'] == 'YELLOW']
    green  = [a for a in alerts if a['level'] == 'GREEN']

    n_amz = sum(1 for p in products if p['amazon_price'])
    n_bb  = sum(1 for p in products if p['bestbuy_price'])
    n_sale = sum(1 for p in products if p['tcl_on_sale'])

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>TCL Price Check — {TODAY}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         margin: 0; padding: 20px; background: #f0f2f5; color: #1a1a1a; font-size: 14px; }}
  .header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
             color: #fff; padding: 22px 28px; border-radius: 10px; margin-bottom: 18px; }}
  .header h1 {{ margin: 0 0 5px; font-size: 22px; letter-spacing: -0.3px; }}
  .header p  {{ margin: 0; opacity: .75; font-size: 12px; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
            gap: 10px; margin-bottom: 18px; }}
  .stat {{ background: #fff; border-radius: 8px; padding: 14px 12px; text-align: center;
           box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  .stat .val {{ font-size: 26px; font-weight: 700; line-height: 1; }}
  .stat .lbl {{ font-size: 11px; color: #777; margin-top: 4px; }}
  .card {{ background: #fff; border-radius: 8px; padding: 18px 20px; margin-bottom: 14px;
           box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  .card h2 {{ margin: 0 0 12px; font-size: 15px; display: flex; align-items: center; gap: 6px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  th {{ background: #f0f0f0; padding: 7px 9px; text-align: left; font-size: 10.5px;
        text-transform: uppercase; letter-spacing: .5px; font-weight: 600; color: #444; }}
  td {{ padding: 6px 9px; border-bottom: 1px solid #f3f3f3; vertical-align: middle; }}
  tr:last-child td {{ border-bottom: none; }}
  a {{ color: #1a73e8; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .border-red    {{ border-left: 4px solid #c62828; }}
  .border-yellow {{ border-left: 4px solid #f57f17; }}
  .border-green  {{ border-left: 4px solid #2e7d32; }}
  .border-blue   {{ border-left: 4px solid #1a73e8; }}
  footer {{ color: #aaa; font-size: 10.5px; text-align: center; margin-top: 20px; }}
</style>
</head>
<body>

<div class="header">
  <h1>TCL Price Monitor v4.1</h1>
  <p>{TODAY} &nbsp;·&nbsp; us.tcl.com vs Amazon &amp; Best Buy &nbsp;·&nbsp; {len(products)} products &nbsp;·&nbsp; Generated {NOW_UTC}</p>
</div>

<div class="stats">
  <div class="stat"><div class="val">{len(products)}</div><div class="lbl">Products</div></div>
  <div class="stat"><div class="val">{n_sale}</div><div class="lbl">On Sale (TCL)</div></div>
  <div class="stat"><div class="val">{n_amz}</div><div class="lbl">Amazon Prices</div></div>
  <div class="stat"><div class="val">{n_bb}</div><div class="lbl">Best Buy Prices</div></div>
  <div class="stat"><div class="val" style="color:#c62828">{len(red)}</div><div class="lbl">🔴 RED Alerts</div></div>
  <div class="stat"><div class="val" style="color:#f57f17">{len(yellow)}</div><div class="lbl">🟡 YELLOW Alerts</div></div>
  <div class="stat"><div class="val" style="color:#2e7d32">{len(green)}</div><div class="lbl">🟢 TCL Cheapest</div></div>
</div>

<div class="card border-red">
  <h2 style="color:#c62828">🔴 RED — Competitor &gt;15% Cheaper ({len(red)})</h2>
  {_alert_table(red)}
</div>

<div class="card border-yellow">
  <h2 style="color:#f57f17">🟡 YELLOW — Competitor 5–15% Cheaper ({len(yellow)})</h2>
  {_alert_table(yellow)}
</div>

<div class="card border-green">
  <h2 style="color:#2e7d32">🟢 GREEN — TCL Competitive / Cheapest ({len(green)})</h2>
  {_alert_table(green)}
</div>

<div class="card border-blue">
  <h2>📊 All Products — 3-Platform Comparison</h2>
  {_all_products_table(products)}
</div>

<footer>
  TCL Price Monitor v4.1 &nbsp;·&nbsp; {NOW_UTC} &nbsp;·&nbsp;
  Data: <a href="https://us.tcl.com">us.tcl.com</a>,
  <a href="https://amazon.com">amazon.com</a>,
  <a href="https://bestbuy.com">bestbuy.com</a>
</footer>
</body>
</html>'''


# ── Phase 8: PDF report ───────────────────────────────────────────────────────

def generate_pdf_report(products, alerts, output_path):
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable,
    )

    C_DARK   = HexColor('#1a1a2e')
    C_RED    = HexColor('#c62828')
    C_YELLOW = HexColor('#f57f17')
    C_GREEN  = HexColor('#2e7d32')
    C_MUTED  = HexColor('#666666')
    C_LIGHT  = HexColor('#f8f9fa')
    C_RED_BG    = HexColor('#ffebee')
    C_YELLOW_BG = HexColor('#fff8e1')
    C_GREEN_BG  = HexColor('#e8f5e9')

    doc = SimpleDocTemplate(
        str(output_path), pagesize=landscape(A4),
        leftMargin=14*mm, rightMargin=14*mm,
        topMargin=11*mm, bottomMargin=11*mm,
    )
    base = getSampleStyleSheet()
    def style(name, **kw):
        s = ParagraphStyle(name, parent=base['Normal'], **kw)
        return s

    title_s  = style('T',  fontSize=18, textColor=C_DARK, fontName='Helvetica-Bold', spaceAfter=3)
    sub_s    = style('S',  fontSize=9,  textColor=C_MUTED, spaceAfter=10)
    h2_s     = style('H2', fontSize=11, textColor=C_DARK, fontName='Helvetica-Bold', spaceBefore=10, spaceAfter=5)
    h2_red_s = style('HR', fontSize=11, textColor=C_RED,  fontName='Helvetica-Bold', spaceBefore=10, spaceAfter=5)
    h2_yel_s = style('HY', fontSize=11, textColor=C_YELLOW, fontName='Helvetica-Bold', spaceBefore=10, spaceAfter=5)
    h2_grn_s = style('HG', fontSize=11, textColor=C_GREEN,  fontName='Helvetica-Bold', spaceBefore=10, spaceAfter=5)
    foot_s   = style('F',  fontSize=7,  textColor=C_MUTED, alignment=1)

    story = []

    red    = [a for a in alerts if a['level'] == 'RED']
    yellow = [a for a in alerts if a['level'] == 'YELLOW']
    green  = [a for a in alerts if a['level'] == 'GREEN']

    story.append(Paragraph(f'TCL Price Monitor — {TODAY}', title_s))
    story.append(Paragraph(
        f'{len(products)} products &nbsp;·&nbsp; '
        f'{sum(1 for p in products if p["amazon_price"])} Amazon &nbsp;·&nbsp; '
        f'{sum(1 for p in products if p["bestbuy_price"])} Best Buy &nbsp;·&nbsp; '
        f'🔴 {len(red)} RED &nbsp; 🟡 {len(yellow)} YELLOW &nbsp; 🟢 {len(green)} GREEN',
        sub_s))
    story.append(HRFlowable(width='100%', thickness=0.75, color=C_DARK))
    story.append(Spacer(1, 5))

    HEADER_STYLE = [
        ('BACKGROUND', (0,0), (-1,0), C_DARK),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTNAME',   (0,1), (-1,-1), 'Helvetica'),
        ('FONTSIZE',   (0,0), (-1,-1), 7.5),
        ('GRID',       (0,0), (-1,-1), 0.25, colors.lightgrey),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING',   (0,0), (-1,-1), 4),
        ('RIGHTPADDING',  (0,0), (-1,-1), 4),
    ]

    # Alert sections
    for level_list, heading, h2style, bg in [
        (red,    f'🔴 RED — Competitor >15% Cheaper ({len(red)})',       h2_red_s, C_RED_BG),
        (yellow, f'🟡 YELLOW — Competitor 5-15% Cheaper ({len(yellow)})', h2_yel_s, C_YELLOW_BG),
        (green,  f'🟢 GREEN — TCL Competitive/Cheapest ({len(green)})',   h2_grn_s, C_GREEN_BG),
    ]:
        if not level_list:
            continue
        story.append(Paragraph(heading, h2style))
        data = [['Model', 'Platform', 'TCL Price', 'Comp Price', 'Gap %', 'Baseline', 'Δ Baseline']]
        for a in level_list:
            g = a['gap_pct']
            gap_str = f'{g:+.1f}%' if g is not None else '—'
            chg = ''
            if a['price_change'] is not None:
                arrow = '↓' if a['price_change'] < 0 else '↑'
                chg = f'{arrow}${abs(a["price_change"]):.2f}'
            data.append([
                a['model'], a['platform'].title(),
                fmt_price(a['tcl_price']), fmt_price(a['comp_price']),
                gap_str,
                fmt_price(a['baseline']) if a['baseline'] else '—',
                chg or '—',
            ])
        t = Table(data, colWidths=[58, 52, 62, 62, 50, 62, 62])
        ts = list(HEADER_STYLE) + [
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, bg]),
            ('ALIGN', (2,0), (-1,-1), 'RIGHT'),
        ]
        if level_list is red:
            ts += [('TEXTCOLOR', (4,1), (4,-1), C_RED), ('FONTNAME', (4,1), (4,-1), 'Helvetica-Bold')]
        elif level_list is yellow:
            ts += [('TEXTCOLOR', (4,1), (4,-1), C_YELLOW)]
        t.setStyle(TableStyle(ts))
        story.append(t)
        story.append(Spacer(1, 6))

    # Full product table
    story.append(Paragraph('📊 All Products — 3-Platform Comparison', h2_s))
    data = [['Model', 'TCL Price', 'On Sale?', 'Amazon', 'Best Buy', 'Amz vs TCL', 'BB vs TCL']]
    for p in sorted(products, key=lambda x: x['model']):
        amz_g = gap_pct(p['tcl_price'], p['amazon_price'])
        bb_g  = gap_pct(p['tcl_price'], p['bestbuy_price'])
        data.append([
            p['model'],
            fmt_price(p['tcl_price']),
            'YES' if p['tcl_on_sale'] else 'no',
            fmt_price(p['amazon_price']),
            fmt_price(p['bestbuy_price']),
            f'{amz_g:+.1f}%' if amz_g is not None else '—',
            f'{bb_g:+.1f}%'  if bb_g  is not None else '—',
        ])
    t = Table(data, colWidths=[58, 58, 46, 60, 60, 58, 58])
    ts = list(HEADER_STYLE) + [
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, C_LIGHT]),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
    ]
    t.setStyle(TableStyle(ts))
    story.append(t)
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f'TCL Price Monitor v4.1 · {NOW_UTC} · us.tcl.com / amazon.com / bestbuy.com',
        foot_s))

    doc.build(story)
    print(f'  ✅ PDF saved: {output_path}')


# ── Phase 9: Send email ───────────────────────────────────────────────────────

def send_email(html_body, pdf_path, alerts):
    if not GMAIL_APP_PASSWORD:
        print('  ⚠ GMAIL_APP_PASSWORD not set — skipping email')
        print('    Set GMAIL_APP_PASSWORD in .env (Google App Password) to enable')
        return

    red_c    = sum(1 for a in alerts if a['level'] == 'RED')
    yellow_c = sum(1 for a in alerts if a['level'] == 'YELLOW')
    subject  = f'TCL Price Report — {TODAY} | 🔴{red_c} RED 🟡{yellow_c} YELLOW'

    msg = MIMEMultipart('mixed')
    msg['From']    = f'TCL Price Monitor <{SENDER_EMAIL}>'
    msg['To']      = RECIPIENT_EMAIL
    msg['Subject'] = subject
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    if pdf_path and Path(pdf_path).exists():
        with open(pdf_path, 'rb') as f:
            part = MIMEBase('application', 'pdf')
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename="{Path(pdf_path).name}"')
        msg.attach(part)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=20) as s:
            s.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
            s.sendmail(SENDER_EMAIL, [RECIPIENT_EMAIL], msg.as_string())
        print(f'  ✅ Email sent to {RECIPIENT_EMAIL}')
    except Exception as e:
        print(f'  ❌ Email failed: {e}')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='TCL Price Monitor v4.1')
    parser.add_argument('--force', action='store_true', help='Force run (ignore cache)')
    args = parser.parse_args()

    load_env()

    print(f'╔══════════════════════════════════════════════════════╗')
    print(f'║  TCL Price Monitor v4.1   {TODAY}           ║')
    print(f'║  Force={str(args.force):<5}  BestBuy API={"yes" if BESTBUY_API_KEY else "no "}  Email={"yes" if GMAIL_APP_PASSWORD else "no "}  ║')
    print(f'╚══════════════════════════════════════════════════════╝')

    t0 = time.time()
    session = make_session()

    # Phase 1–2: TCL + seed URLs
    products = fetch_tcl_products(session)
    products = load_seed_urls(products)

    # Phase 3–4: Competitor prices
    products = fetch_amazon_prices(session, products)
    products = fetch_bestbuy_prices(session, products)

    # Phase 5: Load baselines
    print('\n── Phase 5: Loading price baselines ──')
    baselines = load_supabase_baselines()
    if baselines:
        print(f'  ✅ Loaded {len(baselines)} baselines from Supabase')
    else:
        print('  ⚠ Supabase empty/unavailable — using local baselines fallback')
        baselines = load_local_baselines()
        print(f'  Loaded {len(baselines)} local baselines')

    # Phase 6: Classify
    print('\n── Phase 6: Classifying alerts ──')
    alerts = build_alerts(products, baselines)
    red_n    = sum(1 for a in alerts if a['level'] == 'RED')
    yellow_n = sum(1 for a in alerts if a['level'] == 'YELLOW')
    green_n  = sum(1 for a in alerts if a['level'] == 'GREEN')
    parity_n = sum(1 for a in alerts if a['level'] == 'PARITY')
    print(f'  🔴 RED={red_n}  🟡 YELLOW={yellow_n}  🟢 GREEN={green_n}  ⚪ PARITY={parity_n}')

    # Phase 7: Save baselines
    print('\n── Phase 7: Saving baselines ──')
    save_supabase_baselines(products)
    save_local_baselines(products)
    print('  ✅ Local baselines updated')

    # Phase 8: HTML report
    print('\n── Phase 8: Generating HTML report ──')
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    html_path = REPORTS_DIR / f'tcl-price-check-{TODAY}.html'
    html_content = generate_html_report(products, alerts)
    html_path.write_text(html_content, encoding='utf-8')
    print(f'  ✅ HTML saved: {html_path}')

    # Phase 9: PDF report
    print('\n── Phase 9: Generating PDF report ──')
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = DOWNLOADS_DIR / f'TCL-Price-Report-{TODAY}.pdf'
    try:
        generate_pdf_report(products, alerts, pdf_path)
    except Exception as e:
        print(f'  ❌ PDF failed: {e}')
        import traceback; traceback.print_exc()
        pdf_path = None

    # Phase 10: Email
    print('\n── Phase 10: Sending email report ──')
    send_email(html_content, pdf_path, alerts)

    elapsed = time.time() - t0
    n_req = (
        len(products)                                        # TCL pages
        + sum(1 for p in products if p.get('amazon_url'))   # Amazon
        + (1 if BESTBUY_API_KEY else 0)                     # BB API batches
        + 2                                                  # Supabase read + write
    )

    print(f'''
╔══════════════════════════════════════════════════════╗
║  COMPLETE — {elapsed:5.1f}s   ~{n_req} HTTP requests           ║
╠══════════════════════════════════════════════════════╣
║  Products tracked : {len(products):<34} ║
║  Amazon prices    : {sum(1 for p in products if p["amazon_price"]):<34} ║
║  Best Buy prices  : {sum(1 for p in products if p["bestbuy_price"]):<34} ║
║  🔴 RED alerts    : {red_n:<34} ║
║  🟡 YELLOW alerts : {yellow_n:<34} ║
║  🟢 GREEN         : {green_n:<34} ║
╠══════════════════════════════════════════════════════╣
║  HTML : {str(html_path)[:44]:<44} ║
║  PDF  : {str(pdf_path)[:44]:<44} ║
╚══════════════════════════════════════════════════════╝''')


if __name__ == '__main__':
    main()
