#!/usr/bin/env python3
"""
TCL Product Mapping Setup (One-time)
Maps TCL SKUs to corresponding Amazon & Best Buy URLs.
Saves template for daily price checks.
"""

import os
import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, asdict

WINDOWS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
BASE = '/Volumes/workssd/CoworkOS' if os.path.exists('/Volumes/workssd') else os.path.expanduser('~/CoworkOS')

@dataclass
class ProductMapping:
    sku: str
    name: str
    category: str
    tcl_url: str
    amazon_url: str = None
    bestbuy_url: str = None

def log_msg(status, msg):
    print(f"[{status}] {msg}")

def fetch_tcl_skus():
    """Fetch TCL active products with SKUs."""
    products = {}
    try:
        page = 1
        while True:
            url = f"https://us.tcl.com/products.json?page={page}&limit=50"
            req = urllib.request.Request(url, headers={'User-Agent': WINDOWS_UA})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if not data.get('products'):
                    break
                for p in data['products']:
                    sku = extract_sku(p['title'])
                    if sku and 'sale' in p.get('metafields', [{}])[0].get('value', '').lower():
                        products[sku] = ProductMapping(
                            sku=sku,
                            name=p['title'],
                            category=p.get('product_type', 'TV'),
                            tcl_url=f"https://us.tcl.com/products/{p['handle']}"
                        )
                page += 1
                time.sleep(1)
    except Exception as e:
        log_msg("ERROR", f"fetch_tcl_skus: {e}")
    return products

def extract_sku(title):
    """Extract SKU from title: 'TCL 55QM6K 4K Smart TV' → '55QM6K'"""
    match = re.search(r'(\d+[A-Z0-9]{4,})', title.upper())
    return match.group(1) if match else None

def find_amazon_url(sku, product_name):
    """Find product on Amazon by SKU + title search."""
    try:
        search_query = f"{sku} {product_name.split()[0]}"
        search_url = f"https://www.amazon.com/s?k={urllib.parse.quote(search_query)}"
        req = urllib.request.Request(search_url, headers={'User-Agent': WINDOWS_UA})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode('utf-8')
            # Find first product link with exact SKU in title
            pattern = rf'href="(/[^"]*{re.escape(sku)}[^"]*)"'
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return f"https://www.amazon.com{match.group(1)}"
        time.sleep(3)
    except Exception as e:
        log_msg("WARN", f"amazon search failed for {sku}: {e}")
    return None

def find_bestbuy_url(sku, product_name):
    """Find product on Best Buy by SKU."""
    try:
        search_url = f"https://www.bestbuy.com/site/searchpage.jsp?st={urllib.parse.quote(sku)}"
        req = urllib.request.Request(search_url, headers={'User-Agent': WINDOWS_UA})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode('utf-8')
            # Find product link
            pattern = r'href="(/site/[^"]*)"[^>]*>.*?TCL'
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return f"https://www.bestbuy.com{match.group(1)}"
        time.sleep(3)
    except Exception as e:
        log_msg("WARN", f"bestbuy search failed for {sku}: {e}")
    return None

def save_mapping(products):
    """Save product mapping as JSON template."""
    mapping_path = Path(BASE) / 'data' / 'products_mapping.json'
    mapping_path.parent.mkdir(parents=True, exist_ok=True)

    mapping_data = {
        'generated': datetime.now(timezone.utc).isoformat(),
        'products': {
            sku: asdict(p)
            for sku, p in products.items()
        }
    }

    with open(mapping_path, 'w') as f:
        json.dump(mapping_data, f, indent=2)

    log_msg("OK", f"Mapping saved: {len(products)} products to {mapping_path.name}")
    return mapping_path

def main():
    log_msg("INFO", "Starting TCL Product Mapping Setup...")

    # Fetch TCL SKUs
    products = fetch_tcl_skus()
    if not products:
        log_msg("ERROR", "No products found on TCL")
        sys.exit(1)

    log_msg("OK", f"Found {len(products)} TCL products")

    # Find Amazon URLs
    log_msg("INFO", "Searching Amazon...")
    for sku, p in products.items():
        url = find_amazon_url(sku, p.name)
        if url:
            p.amazon_url = url
            log_msg("OK", f"Amazon: {sku}")
        else:
            log_msg("SKIP", f"Amazon: {sku} not found")

    # Find Best Buy URLs
    log_msg("INFO", "Searching Best Buy...")
    for sku, p in products.items():
        url = find_bestbuy_url(sku, p.name)
        if url:
            p.bestbuy_url = url
            log_msg("OK", f"Best Buy: {sku}")
        else:
            log_msg("SKIP", f"Best Buy: {sku} not found")

    # Save mapping
    save_mapping(products)

    # Summary
    with_amazon = sum(1 for p in products.values() if p.amazon_url)
    with_bestbuy = sum(1 for p in products.values() if p.bestbuy_url)
    log_msg("OK", f"Mapping complete: {with_amazon}/{len(products)} Amazon, {with_bestbuy}/{len(products)} Best Buy")

if __name__ == '__main__':
    main()
