#!/usr/bin/env python3
"""
price_check_v4_1.py — TCL Price Monitor v4.1

Fetches live prices from us.tcl.com, Amazon, Best Buy.
Classifies price gaps vs DTC:  RED >15% | YELLOW 5-15% | GREEN <5%
Tracks vs Supabase baselines.  Outputs HTML + PDF.  Sends email.

Usage:
  python3 price_check_v4_1.py [--force]
  --force  skip 24-h cooldown; always run a fresh fetch
"""

import argparse
import datetime
import json
import os
import re
import smtplib
import sys
import time
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
SEED_FILE  = SCRIPT_DIR / "data" / "seed-urls.json"
STATE_FILE = SCRIPT_DIR / "data" / "last-run-v4.json"

TCL_BASE = "https://us.tcl.com"

SUPABASE_URL      = "https://vyluxphyxfiygdkaprks.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZ5bHV4cGh5eGZpeWdka2FwcmtzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI0MzA5NTQsImV4cCI6MjA4ODAwNjk1NH0"
    ".zEfEvLSwRi0H3vYOvyDXXvFwDxVY4RtCSuplLoKRe_0"
)

TODAY        = datetime.date.today().isoformat()
GENERATED_AT = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

EMAIL_FROM = "affiliate@celldigital.co"
EMAIL_TO   = "affiliate@celldigital.co"

REPORT_HTML_DIR = Path.home() / "CoworkOS" / "reports" / "price-check"
REPORT_PDF_DIR  = Path.home() / "Downloads"

RED_THRESHOLD    = 0.15   # competitor >15% cheaper  → RED
YELLOW_THRESHOLD = 0.05   # competitor 5–15% cheaper → YELLOW

REQ_DELAY = 1.2           # seconds between external HTTP calls

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS_BROWSER = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
SB_HEADERS = {
    "apikey": SUPABASE_ANON_KEY,
    "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
    "Content-Type": "application/json",
}

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def load_env():
    env_path = SCRIPT_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            m = re.match(r'^([^#=\s]+)\s*=\s*(.*)$', line)
            if m:
                os.environ.setdefault(m.group(1), m.group(2).strip())

def fmt(p):
    if p is None:
        return "N/A"
    return f"${p:,.2f}"

def gap_pct(tcl, comp):
    """Return % gap as float (positive = competitor cheaper). None if either price missing."""
    if tcl is None or comp is None or tcl == 0:
        return None
    return (tcl - comp) / tcl  # positive → competitor is cheaper

def classify(tcl, comp):
    g = gap_pct(tcl, comp)
    if g is None:
        return None
    if g > RED_THRESHOLD:
        return "RED"
    if g > YELLOW_THRESHOLD:
        return "YELLOW"
    return "GREEN"

def worst_level(*levels):
    order = {"RED": 0, "YELLOW": 1, "GREEN": 2, None: 3}
    valid = [l for l in levels if l is not None]
    if not valid:
        return None
    return min(valid, key=lambda l: order[l])

def extract_model(title):
    m = re.search(r'[–\-]\s*([A-Z0-9][A-Z0-9\-]{2,})\s*$', title, re.I)
    if m:
        return m.group(1).strip().upper()
    m = (re.search(r'\b([A-Z]{1,3}\d{2,4}[A-Z0-9]+)\b', title) or
         re.search(r'\b(\d{2,3}[A-Z][A-Z0-9]+)\b', title))
    if m:
        return m.group(1).strip().upper()
    m = re.search(r'(NXTPAPER[\s\-]+\d+(?:[\s\-]+(?:Plus|Pro|Gen\s*\d))?)', title, re.I)
    if m:
        return re.sub(r'\s+', '-', m.group(1)).upper()
    return None

def bb_sku_id(url):
    m = re.search(r'/(\d{6,7})\.p', url or "")
    return m.group(1) if m else None

def title_matches_model(title, model):
    """Strict validation: title must contain the model (ignoring hyphens/spaces)."""
    if not title or not model:
        return True
    clean = lambda s: re.sub(r'[\s\-]', '', s).lower()
    return clean(model) in clean(title)

def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ─────────────────────────────────────────────────────────────────────────────
# Cooldown guard
# ─────────────────────────────────────────────────────────────────────────────

def check_cooldown(force):
    if force:
        return True
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            last = datetime.date.fromisoformat(state.get("date", "2000-01-01"))
            if last >= datetime.date.today():
                log(f"Already ran today ({last}). Use --force to override.")
                return False
        except Exception:
            pass
    return True

def mark_ran():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"date": TODAY, "ts": GENERATED_AT}))

# ─────────────────────────────────────────────────────────────────────────────
# TCL Shopify API
# ─────────────────────────────────────────────────────────────────────────────

def fetch_tcl_catalog():
    log("Fetching us.tcl.com/products.json …")
    products, page = [], 1
    while True:
        url = f"{TCL_BASE}/products.json?limit=250&page={page}"
        try:
            r = requests.get(url, headers={"User-Agent": "TCLPriceMonitor/4.1",
                                            "Accept": "application/json"}, timeout=20)
            r.raise_for_status()
            batch = r.json().get("products", [])
            if not batch:
                break
            products.extend(batch)
            log(f"  Page {page}: +{len(batch)} products ({len(products)} total)")
            if len(batch) < 250:
                break
            page += 1
            time.sleep(0.5)
        except Exception as e:
            log(f"  ⚠ TCL fetch error page {page}: {e}")
            break
    return products

def build_sku_list(products, seed_urls):
    """Match TCL products to seed URLs. Returns list of sku dicts."""
    skus, seen = [], set()
    active_sales = []

    for p in products:
        avail = [v for v in p.get("variants", []) if v.get("available")]
        if not avail:
            continue
        model = extract_model(p["title"])
        if not model or model in seen:
            continue
        seen.add(model)

        v = avail[0]
        price = float(v["price"]) if v.get("price") else None
        compare = float(v["compare_at_price"]) if v.get("compare_at_price") else None
        on_sale = bool(compare and compare > (price or 0))

        seed = seed_urls.get(model, {})
        sku = {
            "model":        model,
            "title":        p["title"],
            "tcl_url":      f"{TCL_BASE}/products/{p['handle']}",
            "amazon_url":   seed.get("amazon_url"),
            "bestbuy_url":  seed.get("bestbuy_url"),
            "tcl_price":    price,
            "tcl_compare":  compare,
            "tcl_on_sale":  on_sale,
            "amazon_price": None,
            "amazon_live":  False,
            "bb_price":     None,
            "bb_live":      False,
        }
        skus.append(sku)
        if on_sale:
            active_sales.append(f"{model}: ${price:.2f} (was ${compare:.2f})")

    log(f"  {len(skus)} purchasable SKUs | {len(active_sales)} active TCL sales")
    for s in active_sales[:10]:
        log(f"    🔖 Sale: {s}")
    if len(active_sales) > 10:
        log(f"    … and {len(active_sales) - 10} more sales")
    return skus

# ─────────────────────────────────────────────────────────────────────────────
# Amazon price fetcher (requests best-effort)
# ─────────────────────────────────────────────────────────────────────────────

AMZ_PATTERNS = [
    r'"priceAmount"\s*:\s*"([\d.]+)"',
    r'"price"\s*:\s*\{\s*"amount"\s*:\s*"([\d.]+)"',
    r'"buyingPrice"\s*:\s*"([\d.]+)"',
    r'"offerPrice"\s*:\s*\{\s*"amount"\s*:\s*"([\d.]+)"',
    r'"currentPrice"\s*:\s*"([\d.]+)"',
    r'id="priceblock_ourprice"[^>]*>\s*\$([\d,. ]+)<',
    r'id="priceblock_dealprice"[^>]*>\s*\$([\d,. ]+)<',
    r'class="a-offscreen">\$([\d,.]+)<',
    r'"price"\s*:\s*"?\$([\d,.]+)"?',
    r'apexPriceToPay[^$]{0,200}\$([\d,.]+)',
    r'priceToPay[^$]{0,200}\$([\d,.]+)',
]

def fetch_amazon_price(sku, session):
    url = sku.get("amazon_url")
    if not url:
        return None
    try:
        r = session.get(url, headers=HEADERS_BROWSER, timeout=22, allow_redirects=True)
        if r.status_code != 200:
            return None
        html = r.text
        if any(kw in html.lower() for kw in ["robot check", "captcha", "enter the characters"]):
            return None
        # Validate title
        tm = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        if tm and not title_matches_model(tm.group(1), sku["model"]):
            return None
        for pat in AMZ_PATTERNS:
            m = re.search(pat, html, re.I | re.DOTALL)
            if m:
                try:
                    price = float(m.group(1).replace(',', '').strip())
                    if 1.0 < price < 50000:
                        return price
                except (ValueError, IndexError):
                    continue
    except Exception:
        pass
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Best Buy price fetcher (API or web)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_bb_price_api(sku_id, model, api_key, session):
    url = (f"https://api.bestbuy.com/v1/products/{sku_id}"
           f"?apiKey={api_key}&format=json"
           f"&show=sku,name,salePrice,regularPrice,onSale,onlineAvailability")
    try:
        r = session.get(url, timeout=15)
        if r.ok:
            d = r.json()
            name = d.get("name", "")
            if name and not title_matches_model(name, model):
                return None
            return d.get("salePrice") or d.get("regularPrice")
    except Exception:
        pass
    return None

def fetch_bb_price_web(sku_id, model, session):
    search_url = f"https://www.bestbuy.com/site/searchpage.jsp?st={sku_id}+TCL"
    try:
        r = session.get(search_url, headers=HEADERS_BROWSER, timeout=25)
        if not r.ok:
            return None
        html = r.text
        # JSON blob with numeric sku
        for pat in [
            rf'"sku"\s*:\s*"{sku_id}"[^}}]{{0,600}}"salePrice"\s*:\s*([\d.]+)',
            rf'"sku"\s*:\s*{sku_id}[^}}]{{0,600}}"salePrice"\s*:\s*([\d.]+)',
            rf'data-product-id="{sku_id}"[^>]*>(?:[^$]{{0,300}})\$([\d,.]+)',
        ]:
            m = re.search(pat, html, re.DOTALL | re.I)
            if m:
                try:
                    return float(m.group(1).replace(',', ''))
                except ValueError:
                    continue
    except Exception:
        pass
    return None

def fetch_bb_price(sku, session, api_key=None):
    url = sku.get("bestbuy_url")
    if not url:
        return None
    sid = bb_sku_id(url)
    if not sid:
        return None
    if api_key:
        p = fetch_bb_price_api(sid, sku["model"], api_key, session)
        if p is not None:
            return p
    return fetch_bb_price_web(sid, sku["model"], session)

# ─────────────────────────────────────────────────────────────────────────────
# Supabase baseline integration
# ─────────────────────────────────────────────────────────────────────────────

def load_baselines():
    """Returns dict: {(model, platform): row_dict}."""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/tcl_price_baselines",
            headers={**SB_HEADERS, "Prefer": ""},
            params={"select": "model,platform,baseline_price,current_price,last_checked"},
            timeout=15,
        )
        if r.ok:
            rows = r.json()
            log(f"  Supabase: {len(rows)} baseline rows loaded")
            return {(row["model"], row["platform"]): row for row in rows}
    except Exception as e:
        log(f"  ⚠ Supabase load failed: {e}")
    return {}

def upsert_current_price(model, platform, price, baselines):
    """Write current price back to Supabase (PATCH existing, POST new)."""
    if price is None:
        return
    key = (model, platform)
    payload = {"current_price": str(round(price, 2)), "last_checked": TODAY}
    try:
        if key in baselines:
            r = requests.patch(
                f"{SUPABASE_URL}/rest/v1/tcl_price_baselines"
                f"?model=eq.{model}&platform=eq.{platform}",
                headers=SB_HEADERS,
                json=payload,
                timeout=10,
            )
        else:
            payload.update({"model": model, "platform": platform,
                            "baseline_price": str(round(price, 2))})
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/tcl_price_baselines",
                headers={**SB_HEADERS, "Prefer": "return=minimal"},
                json=payload,
                timeout=10,
            )
    except Exception:
        pass

def baseline_price(model, platform, baselines):
    row = baselines.get((model, platform))
    if row:
        try:
            return float(row["baseline_price"])
        except (TypeError, ValueError):
            pass
    return None

def change_indicator(current, base):
    """Returns (symbol, amount_str) for price change vs baseline."""
    if current is None or base is None:
        return "", ""
    diff = current - base
    if abs(diff) < 0.005:
        return "→", ""
    if diff < 0:
        return "↓", fmt(abs(diff))
    return "↑", fmt(diff)

# ─────────────────────────────────────────────────────────────────────────────
# Report builder
# ─────────────────────────────────────────────────────────────────────────────

LEVEL_COLOR = {"RED": "#c62828", "YELLOW": "#e65100", "GREEN": "#2e7d32"}
LEVEL_BG    = {"RED": "#ffebee", "YELLOW": "#fff3e0", "GREEN": "#e8f5e9"}

def gap_badge(tcl, comp):
    g = gap_pct(tcl, comp)
    if g is None:
        return '<span style="color:#ccc">—</span>'
    level = classify(tcl, comp)
    color = LEVEL_COLOR.get(level, "#999")
    bg    = LEVEL_BG.get(level, "#f5f5f5")
    sign  = "+" if g < 0 else "-"
    label = f"{sign}{abs(g)*100:.1f}%"
    return (f'<span style="display:inline-block;padding:2px 7px;border-radius:10px;'
            f'font-size:11px;font-weight:700;background:{bg};color:{color}">{label}</span>')

def change_cell(current, base_price):
    sym, amt = change_indicator(current, base_price)
    if not sym or sym == "→":
        return '<span style="color:#aaa;font-size:11px">stable</span>'
    color = "#2e7d32" if sym == "↓" else "#c62828"
    return f'<span style="color:{color};font-size:11px;font-weight:600">{sym} {amt}</span>'

def row_html(sku, baselines):
    model       = sku["model"]
    base_amz    = baseline_price(model, "amazon", baselines)
    base_bb     = baseline_price(model, "bestbuy", baselines)

    amz_live_tag = ' <sup style="color:#1565c0;font-size:9px">LIVE</sup>' if sku["amazon_live"] else (
                   ' <sup style="color:#999;font-size:9px">est</sup>' if sku["amazon_price"] else "")
    bb_live_tag  = ' <sup style="color:#1565c0;font-size:9px">LIVE</sup>' if sku["bb_live"] else (
                   ' <sup style="color:#999;font-size:9px">est</sup>' if sku["bb_price"] else "")

    tcl_cell = ""
    if sku["tcl_price"]:
        if sku["tcl_on_sale"]:
            tcl_cell = (f'<a href="{sku["tcl_url"]}" style="color:#e65100;font-weight:700;text-decoration:none">'
                        f'{fmt(sku["tcl_price"])}</a>'
                        f'<br><small style="text-decoration:line-through;color:#999">{fmt(sku["tcl_compare"])}</small>'
                        f'<span style="background:#ff6f00;color:white;font-size:9px;font-weight:700;'
                        f'padding:1px 4px;border-radius:3px;margin-left:4px">SALE</span>')
        else:
            tcl_cell = f'<a href="{sku["tcl_url"]}" style="color:#1a1a1a;text-decoration:none;font-weight:600">{fmt(sku["tcl_price"])}</a>'
    else:
        tcl_cell = '<span style="color:#aaa;font-style:italic">N/A</span>'

    amz_cell = ""
    if sku["amazon_price"]:
        amz_cell = (f'<a href="{sku["amazon_url"]}" style="color:#1565c0;text-decoration:none;font-weight:600">'
                    f'{fmt(sku["amazon_price"])}</a>{amz_live_tag}')
    elif sku["amazon_url"]:
        amz_cell = f'<a href="{sku["amazon_url"]}" style="color:#90a4ae;text-decoration:none">link →</a>'
    else:
        amz_cell = '<span style="color:#ddd">—</span>'

    bb_cell = ""
    if sku["bb_price"]:
        bb_cell = (f'<a href="{sku["bestbuy_url"]}" style="color:#1565c0;text-decoration:none;font-weight:600">'
                   f'{fmt(sku["bb_price"])}</a>{bb_live_tag}')
    elif sku["bestbuy_url"]:
        bb_cell = f'<a href="{sku["bestbuy_url"]}" style="color:#90a4ae;text-decoration:none">link →</a>'
    else:
        bb_cell = '<span style="color:#ddd">—</span>'

    title_short = sku["title"][:55] + ("…" if len(sku["title"]) > 55 else "")

    return f"""
    <tr>
      <td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;font-weight:700;font-size:13px;white-space:nowrap">
        {model}
        <br><span style="font-weight:normal;font-size:11px;color:#888">{title_short}</span>
      </td>
      <td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;white-space:nowrap">{tcl_cell}</td>
      <td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;white-space:nowrap">{amz_cell}</td>
      <td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;white-space:nowrap">{bb_cell}</td>
      <td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;text-align:center">{gap_badge(sku["tcl_price"], sku["amazon_price"])}</td>
      <td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;text-align:center">{gap_badge(sku["tcl_price"], sku["bb_price"])}</td>
      <td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;text-align:center">{change_cell(sku["amazon_price"], base_amz)}</td>
      <td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;text-align:center">{change_cell(sku["bb_price"], base_bb)}</td>
    </tr>"""

def table_html(skus, baselines):
    header = """
  <table style="width:100%;border-collapse:collapse;font-size:13px">
    <thead>
      <tr style="background:#f9f9f9">
        <th style="padding:8px 10px;text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.3px;color:#555;border-bottom:1px solid #eee">Model</th>
        <th style="padding:8px 10px;text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.3px;color:#555;border-bottom:1px solid #eee">TCL.com</th>
        <th style="padding:8px 10px;text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.3px;color:#555;border-bottom:1px solid #eee">Amazon</th>
        <th style="padding:8px 10px;text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.3px;color:#555;border-bottom:1px solid #eee">Best Buy</th>
        <th style="padding:8px 10px;text-align:center;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.3px;color:#555;border-bottom:1px solid #eee">Amz Gap</th>
        <th style="padding:8px 10px;text-align:center;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.3px;color:#555;border-bottom:1px solid #eee">BB Gap</th>
        <th style="padding:8px 10px;text-align:center;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.3px;color:#555;border-bottom:1px solid #eee">Amz Δ</th>
        <th style="padding:8px 10px;text-align:center;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.3px;color:#555;border-bottom:1px solid #eee">BB Δ</th>
      </tr>
    </thead>
    <tbody>"""
    rows = "".join(row_html(s, baselines) for s in skus)
    return header + rows + "\n    </tbody>\n  </table>"

def section_html(label, emoji, level, skus, baselines):
    if not skus:
        return ""
    color = LEVEL_COLOR.get(level, "#1565c0")
    bg    = LEVEL_BG.get(level, "#e3f2fd")
    border_color = color
    return f"""
<div style="margin:16px;background:white;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.1);overflow:hidden">
  <div style="padding:12px 16px;font-weight:700;font-size:13px;display:flex;align-items:center;gap:8px;
              background:{bg};color:{color};border-left:4px solid {border_color}">
    {emoji} {label} ({len(skus)})
  </div>
  {table_html(skus, baselines)}
</div>"""

def build_html(skus, baselines):
    # Classify each sku
    for s in skus:
        al = classify(s["tcl_price"], s["amazon_price"])
        bl = classify(s["tcl_price"], s["bb_price"])
        s["amz_level"] = al
        s["bb_level"]  = bl
        s["overall"]   = worst_level(al, bl)

    priority = {"RED": 0, "YELLOW": 1, "GREEN": 2, None: 3}
    skus_sorted = sorted(skus, key=lambda s: (priority[s["overall"]],
                                               -(gap_pct(s["tcl_price"], s["amazon_price"]) or 0)))

    red_items       = [s for s in skus_sorted if s["overall"] == "RED"]
    yellow_items    = [s for s in skus_sorted if s["overall"] == "YELLOW"]
    green_items     = [s for s in skus_sorted if s["overall"] == "GREEN"]
    unmatched_items = [s for s in skus_sorted if s["overall"] is None]

    total        = len(skus)
    on_sale_cnt  = sum(1 for s in skus if s["tcl_on_sale"])
    amz_matched  = sum(1 for s in skus if s["amazon_price"] is not None)
    bb_matched   = sum(1 for s in skus if s["bb_price"] is not None)

    def stat(value, label, color=""):
        return (f'<div style="flex:1;padding:16px 20px;text-align:center;border-right:1px solid #e0e0e0">'
                f'<div style="font-size:28px;font-weight:800;line-height:1;{color}">{value}</div>'
                f'<div style="font-size:11px;color:#666;margin-top:4px;text-transform:uppercase;letter-spacing:0.5px">{label}</div>'
                f'</div>')

    stats_bar = (
        stat(total, "Total SKUs") +
        stat(len(red_items),    "RED &gt;15%",      "color:#c62828") +
        stat(len(yellow_items), "YELLOW 5–15%",     "color:#e65100") +
        stat(len(green_items),  "GREEN &lt;5%",     "color:#2e7d32") +
        stat(on_sale_cnt,       "TCL Sales Active", "color:#ff6f00") +
        stat(amz_matched,       "Amazon Matched") +
        stat(bb_matched,        "Best Buy Matched")
    )

    sections = (
        section_html("RED Alerts — Competitor &gt;15% Cheaper than TCL DTC",
                     "🔴", "RED", red_items, baselines) +
        section_html("YELLOW Alerts — Competitor 5–15% Cheaper",
                     "🟡", "YELLOW", yellow_items, baselines) +
        section_html("GREEN — Pricing Competitive (&lt;5% gap)",
                     "🟢", "GREEN", green_items, baselines) +
        section_html("No Competitor Price Data",
                     "⚪", None, unmatched_items, baselines)
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TCL Price Report — {TODAY}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f5f5f5;color:#1a1a1a;font-size:13px}}
  a{{text-decoration:none}}
  a:hover{{text-decoration:underline}}
  tr:hover td{{background:#fafafa}}
</style>
</head>
<body>

<div style="background:linear-gradient(135deg,#1a237e 0%,#0d47a1 100%);color:white;padding:24px 32px">
  <h1 style="font-size:22px;font-weight:700;letter-spacing:-0.5px">TCL Price Monitoring Report</h1>
  <p style="font-size:12px;opacity:.8;margin-top:4px">
    {TODAY} &nbsp;·&nbsp; us.tcl.com vs Amazon &amp; Best Buy &nbsp;·&nbsp; Generated {GENERATED_AT}
  </p>
</div>

<div style="display:flex;background:white;border-bottom:2px solid #e0e0e0">
  {stats_bar}
</div>

{sections}

<div style="text-align:center;padding:16px;color:#999;font-size:11px;margin-top:8px">
  TCL Price Monitor v4.1 &nbsp;·&nbsp; Data sourced from us.tcl.com, amazon.com, bestbuy.com
  &nbsp;·&nbsp; {GENERATED_AT}
  <br>Gaps shown as competitor discount vs TCL DTC price. Live prices marked LIVE; baseline estimates marked est.
</div>

</body>
</html>"""

# ─────────────────────────────────────────────────────────────────────────────
# PDF generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_pdf_weasyprint(html_path, pdf_path):
    try:
        from weasyprint import HTML as WeasyHTML, CSS
        log("  Generating PDF via weasyprint …")
        WeasyHTML(filename=str(html_path)).write_pdf(str(pdf_path))
        return True
    except Exception as e:
        log(f"  ⚠ weasyprint PDF failed: {e}")
        return False

def generate_pdf_reportlab(skus, baselines, pdf_path):
    """Fallback: simple table PDF via reportlab."""
    try:
        from reportlab.lib.pagesizes import letter, landscape
        from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                        Paragraph, Spacer)
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors as rl_colors

        log("  Generating PDF via reportlab (fallback) …")
        doc = SimpleDocTemplate(str(pdf_path), pagesize=landscape(letter),
                                topMargin=36, bottomMargin=36, leftMargin=36, rightMargin=36)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph(f"TCL Price Report — {TODAY}", styles["Title"]))
        story.append(Paragraph(f"Generated {GENERATED_AT}", styles["Normal"]))
        story.append(Spacer(1, 12))

        priority = {"RED": 0, "YELLOW": 1, "GREEN": 2, None: 3}
        skus_s = sorted(skus, key=lambda s: priority[s.get("overall")])

        data = [["Model", "TCL Price", "Sale?", "Amazon", "Best Buy",
                 "Amz Gap", "BB Gap", "Amz Δ", "BB Δ"]]

        for s in skus_s:
            base_amz = baseline_price(s["model"], "amazon", baselines)
            base_bb  = baseline_price(s["model"], "bestbuy", baselines)

            def g_str(tcl, comp):
                g = gap_pct(tcl, comp)
                if g is None:
                    return "—"
                return f"{'−' if g > 0 else '+' if g < 0 else ''}{abs(g)*100:.1f}%"

            def ch_str(cur, base):
                sym, amt = change_indicator(cur, base)
                if not sym or sym == "→":
                    return "stable"
                return f"{sym} {amt}"

            data.append([
                s["model"],
                fmt(s["tcl_price"]),
                "✓" if s["tcl_on_sale"] else "",
                fmt(s["amazon_price"]),
                fmt(s["bb_price"]),
                g_str(s["tcl_price"], s["amazon_price"]),
                g_str(s["tcl_price"], s["bb_price"]),
                ch_str(s["amazon_price"], base_amz),
                ch_str(s["bb_price"], base_bb),
            ])

        col_widths = [80, 60, 35, 60, 60, 55, 55, 55, 55]
        tbl = Table(data, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#1a237e")),
            ("TEXTCOLOR",  (0, 0), (-1, 0), rl_colors.white),
            ("FONTSIZE",   (0, 0), (-1, 0), 9),
            ("FONTSIZE",   (0, 1), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [rl_colors.white, rl_colors.HexColor("#f9f9f9")]),
            ("GRID", (0, 0), (-1, -1), 0.25, rl_colors.HexColor("#e0e0e0")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))

        # Colour RED/YELLOW rows
        for i, s in enumerate(skus_s, start=1):
            ol = s.get("overall")
            if ol == "RED":
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, i), (-1, i), rl_colors.HexColor("#ffebee"))
                ]))
            elif ol == "YELLOW":
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, i), (-1, i), rl_colors.HexColor("#fff3e0"))
                ]))

        story.append(tbl)
        doc.build(story)
        return True
    except Exception as e:
        log(f"  ⚠ reportlab PDF failed: {e}")
        return False

# ─────────────────────────────────────────────────────────────────────────────
# Email
# ─────────────────────────────────────────────────────────────────────────────

def send_email(pdf_path, skus, app_password):
    if not app_password:
        log("  ⚠ GMAIL_APP_PASSWORD not set — skipping email")
        return False

    red   = [s for s in skus if s.get("overall") == "RED"]
    yell  = [s for s in skus if s.get("overall") == "YELLOW"]
    green = [s for s in skus if s.get("overall") == "GREEN"]
    sales = [s for s in skus if s["tcl_on_sale"]]

    def alert_rows(items, limit=10):
        rows = ""
        for s in items[:limit]:
            g_amz = gap_pct(s["tcl_price"], s["amazon_price"])
            g_bb  = gap_pct(s["tcl_price"], s["bb_price"])
            g = max(g or 0 for g in [g_amz, g_bb])
            rows += (f'<tr style="border-bottom:1px solid #eee">'
                     f'<td style="padding:5px 8px;font-weight:700">{s["model"]}</td>'
                     f'<td style="padding:5px 8px">{fmt(s["tcl_price"])}</td>'
                     f'<td style="padding:5px 8px">{fmt(s["amazon_price"])}</td>'
                     f'<td style="padding:5px 8px">{fmt(s["bb_price"])}</td>'
                     f'<td style="padding:5px 8px;font-weight:700;color:#c62828">-{g*100:.1f}%</td>'
                     f'</tr>')
        if len(items) > limit:
            rows += f'<tr><td colspan="5" style="padding:5px 8px;color:#999">…and {len(items)-limit} more (see PDF)</td></tr>'
        return rows

    html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:720px">
  <h2 style="color:#1a237e;margin-bottom:4px">TCL Price Monitoring Report</h2>
  <p style="color:#666;margin-top:0">{TODAY} &nbsp;|&nbsp; us.tcl.com vs Amazon &amp; Best Buy</p>

  <table style="border-collapse:collapse;width:100%;margin:16px 0">
    <tr>
      <td style="padding:12px;text-align:center;border:1px solid #ddd">
        <strong style="font-size:24px">{len(skus)}</strong><br><small>Total SKUs</small></td>
      <td style="padding:12px;text-align:center;border:1px solid #ddd">
        <strong style="font-size:24px;color:#c62828">{len(red)}</strong><br><small>RED Alerts (&gt;15%)</small></td>
      <td style="padding:12px;text-align:center;border:1px solid #ddd">
        <strong style="font-size:24px;color:#e65100">{len(yell)}</strong><br><small>YELLOW (5–15%)</small></td>
      <td style="padding:12px;text-align:center;border:1px solid #ddd">
        <strong style="font-size:24px;color:#2e7d32">{len(green)}</strong><br><small>GREEN (&lt;5%)</small></td>
      <td style="padding:12px;text-align:center;border:1px solid #ddd">
        <strong style="font-size:24px;color:#ff6f00">{len(sales)}</strong><br><small>Active TCL Sales</small></td>
    </tr>
  </table>
"""

    if red or yell:
        combined = red + yell
        html_body += f"""
  <h3 style="color:#c62828">Price Alerts ({len(combined)})</h3>
  <table style="border-collapse:collapse;width:100%;font-size:13px">
    <tr style="background:#c62828;color:white">
      <th style="padding:6px 8px;text-align:left">Model</th>
      <th style="padding:6px 8px;text-align:left">TCL Price</th>
      <th style="padding:6px 8px;text-align:left">Amazon</th>
      <th style="padding:6px 8px;text-align:left">Best Buy</th>
      <th style="padding:6px 8px;text-align:left">Max Gap</th>
    </tr>
    {alert_rows(combined)}
  </table>"""
    else:
        html_body += '<p style="color:#2e7d32"><strong>✅ No significant alerts — pricing competitive across all platforms.</strong></p>'

    if sales:
        sale_list = ", ".join(
            f'{s["model"]} ({fmt(s["tcl_price"])} was {fmt(s["tcl_compare"])})'
            for s in sales[:8]
        )
        if len(sales) > 8:
            sale_list += f" …+{len(sales)-8} more"
        html_body += f'<p style="margin-top:16px"><strong>🔖 Active TCL Sales:</strong> {sale_list}</p>'

    html_body += f"""
  <p style="color:#999;font-size:11px;margin-top:20px">
    Full report attached as PDF. Data from us.tcl.com, amazon.com, bestbuy.com.<br>
    Generated by TCL Price Monitor v4.1 | {GENERATED_AT}
  </p>
</div>"""

    msg = MIMEMultipart()
    msg["From"]    = f"TCL Price Monitor <{EMAIL_FROM}>"
    msg["To"]      = EMAIL_TO
    msg["Subject"] = f"TCL Price Report — {TODAY} | {len(red)} RED, {len(yell)} YELLOW"
    msg.attach(MIMEText(html_body, "html"))

    if pdf_path and pdf_path.exists():
        with open(pdf_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition",
                        f"attachment; filename={pdf_path.name}")
        msg.attach(part)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, app_password)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        log(f"  ✅ Email sent to {EMAIL_TO}")
        return True
    except Exception as e:
        log(f"  ❌ Email failed: {e}")
        return False

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TCL Price Monitor v4.1")
    parser.add_argument("--force", action="store_true",
                        help="Skip 24-hour cooldown; always run fresh")
    args = parser.parse_args()

    load_env()

    log("=" * 60)
    log(f"TCL Price Monitor v4.1 — {TODAY}")
    log("=" * 60)

    if not check_cooldown(args.force):
        sys.exit(0)

    bb_api_key    = os.environ.get("BESTBUY_API_KEY")
    gmail_pass    = os.environ.get("GMAIL_APP_PASSWORD")

    # ── 1. Load seed URLs ────────────────────────────────────────────────
    log("\n[1/7] Loading seed URLs …")
    seed_urls = {}
    if SEED_FILE.exists():
        seed_urls = json.loads(SEED_FILE.read_text())
        log(f"  {len(seed_urls)} seed URL entries loaded")
    else:
        log("  ⚠ seed-urls.json not found")

    # ── 2. Fetch TCL catalog ─────────────────────────────────────────────
    log("\n[2/7] Fetching TCL catalog …")
    products = fetch_tcl_catalog()
    skus = build_sku_list(products, seed_urls)
    if not skus:
        log("ERROR: No purchasable SKUs found.")
        sys.exit(1)

    # ── 3. Load Supabase baselines ───────────────────────────────────────
    log("\n[3/7] Loading Supabase baselines …")
    baselines = load_baselines()

    # ── 4. Fetch Amazon & Best Buy prices ────────────────────────────────
    log(f"\n[4/7] Fetching competitor prices …")
    log(f"  Best Buy API key: {'✓' if bb_api_key else '✗ (web fallback)'}")

    session = requests.Session()
    session.headers.update(HEADERS_BROWSER)

    amz_skus = [s for s in skus if s["amazon_url"]]
    bb_skus  = [s for s in skus if s["bestbuy_url"]]

    log(f"\n  ── Amazon ({len(amz_skus)} SKUs) ──")
    for i, sku in enumerate(amz_skus, 1):
        log(f"  [{i}/{len(amz_skus)}] {sku['model']} …", )
        price = fetch_amazon_price(sku, session)
        if price:
            sku["amazon_price"] = price
            sku["amazon_live"]  = True
            log(f"    → LIVE ${price:.2f}")
        else:
            # Fall back to Supabase baseline
            bp = baseline_price(sku["model"], "amazon", baselines)
            if bp:
                sku["amazon_price"] = bp
                sku["amazon_live"]  = False
                log(f"    → baseline ${bp:.2f}")
            else:
                log(f"    → no data")
        time.sleep(REQ_DELAY)

    log(f"\n  ── Best Buy ({len(bb_skus)} SKUs) ──")
    for i, sku in enumerate(bb_skus, 1):
        log(f"  [{i}/{len(bb_skus)}] {sku['model']} …")
        price = fetch_bb_price(sku, session, bb_api_key)
        if price:
            sku["bb_price"] = price
            sku["bb_live"]  = True
            log(f"    → LIVE ${price:.2f}")
        else:
            bp = baseline_price(sku["model"], "bestbuy", baselines)
            if bp:
                sku["bb_price"] = bp
                sku["bb_live"]  = False
                log(f"    → baseline ${bp:.2f}")
            else:
                log(f"    → no data")
        time.sleep(REQ_DELAY * 0.8)

    # ── 5. Classify + summary stats ──────────────────────────────────────
    log("\n[5/7] Classifying alerts …")
    for s in skus:
        al = classify(s["tcl_price"], s["amazon_price"])
        bl = classify(s["tcl_price"], s["bb_price"])
        s["amz_level"] = al
        s["bb_level"]  = bl
        s["overall"]   = worst_level(al, bl)

    red_n    = sum(1 for s in skus if s["overall"] == "RED")
    yellow_n = sum(1 for s in skus if s["overall"] == "YELLOW")
    green_n  = sum(1 for s in skus if s["overall"] == "GREEN")
    sales_n  = sum(1 for s in skus if s["tcl_on_sale"])
    log(f"  RED={red_n}  YELLOW={yellow_n}  GREEN={green_n}  TCL sales={sales_n}")

    # ── 6. Write back to Supabase ────────────────────────────────────────
    log("\n[6/7] Updating Supabase baselines …")
    updated = 0
    for s in skus:
        if s["tcl_live"] if "tcl_live" in s else s["tcl_price"]:
            upsert_current_price(s["model"], "tcl", s["tcl_price"], baselines)
            updated += 1
        if s["amazon_live"] and s["amazon_price"]:
            upsert_current_price(s["model"], "amazon", s["amazon_price"], baselines)
            updated += 1
        if s["bb_live"] and s["bb_price"]:
            upsert_current_price(s["model"], "bestbuy", s["bb_price"], baselines)
            updated += 1
    log(f"  {updated} prices upserted")

    # ── 7. Generate reports ───────────────────────────────────────────────
    log("\n[7/7] Generating reports …")

    html_content = build_html(skus, baselines)

    REPORT_HTML_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PDF_DIR.mkdir(parents=True, exist_ok=True)

    html_path = REPORT_HTML_DIR / f"price-check-{TODAY}.html"
    pdf_path  = REPORT_PDF_DIR  / f"TCL-Price-Report-{TODAY}.pdf"

    html_path.write_text(html_content, encoding="utf-8")
    log(f"  HTML saved: {html_path}")

    pdf_ok = generate_pdf_weasyprint(html_path, pdf_path)
    if not pdf_ok:
        pdf_ok = generate_pdf_reportlab(skus, baselines, pdf_path)
    if pdf_ok:
        log(f"  PDF saved:  {pdf_path}")
    else:
        log("  ⚠ PDF generation failed; email will not include attachment")
        pdf_path = None

    # Email
    log("\n  Sending email …")
    send_email(pdf_path, skus, gmail_pass)

    mark_ran()

    log("\n" + "=" * 60)
    log(f"DONE — {len(skus)} SKUs | RED={red_n} YELLOW={yellow_n} GREEN={green_n} | TCL Sales={sales_n}")
    log(f"HTML: {html_path}")
    if pdf_path:
        log(f"PDF:  {pdf_path}")
    log("=" * 60)


if __name__ == "__main__":
    main()
