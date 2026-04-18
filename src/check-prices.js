/**
 * Phase 3: Daily price checker.
 * Fetches current prices from all three platforms for each active SKU.
 */
import { chromium } from 'playwright';
import { getActiveSkus, recordPrice } from './db.js';

const TCL_BASE = 'https://us.tcl.com';

async function delay(ms) {
  return new Promise(r => setTimeout(r, ms + Math.random() * 1000));
}

// ── TCL (Shopify API — no browser needed) ──────────────────────────

async function checkTclPrices(skus) {
  console.log('\n── Checking us.tcl.com prices ──');
  // Fetch all products from Shopify API
  const allProducts = [];
  let page = 1;
  while (true) {
    const res = await fetch(`${TCL_BASE}/products.json?limit=250&page=${page}`);
    if (!res.ok) break;
    const data = await res.json();
    if (!data.products || data.products.length === 0) break;
    allProducts.push(...data.products);
    if (data.products.length < 250) break;
    page++;
  }

  // Build lookup by handle
  const productMap = new Map();
  for (const p of allProducts) {
    productMap.set(p.handle, p);
  }

  for (const sku of skus) {
    // Extract handle from URL
    const handle = sku.tcl_url.replace(`${TCL_BASE}/products/`, '');
    const product = productMap.get(handle);

    if (!product) {
      console.log(`  ⚠ ${sku.model}: product not found on TCL`);
      recordPrice({ sku_id: sku.id, platform: 'tcl', price: null, compare_at_price: null, in_stock: false });
      continue;
    }

    const variant = product.variants?.[0];
    const price = variant ? parseFloat(variant.price) : null;
    const compareAt = variant?.compare_at_price ? parseFloat(variant.compare_at_price) : null;
    const inStock = variant?.available ?? false;

    recordPrice({ sku_id: sku.id, platform: 'tcl', price, compare_at_price: compareAt, in_stock: inStock });
    console.log(`  ✅ ${sku.model}: $${price}${compareAt ? ` (was $${compareAt})` : ''} ${inStock ? '✓' : '✗'}`);
  }
}

// ── Amazon ──────────────────────────────────────────────────────────

async function checkAmazonPrice(page, sku) {
  if (!sku.amazon_url) return;
  try {
    await page.goto(sku.amazon_url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await delay(1500);

    // Try multiple price selectors
    let price = null;
    const priceSelectors = [
      '.a-price .a-offscreen',
      '#priceblock_ourprice',
      '#priceblock_dealprice',
      '.apexPriceToPay .a-offscreen',
      '#corePrice_feature_div .a-offscreen',
      '.priceToPay .a-offscreen',
    ];

    for (const sel of priceSelectors) {
      const el = await page.$(sel);
      if (el) {
        const text = await el.textContent();
        const match = text.match(/[\d,]+\.?\d*/);
        if (match) {
          price = parseFloat(match[0].replace(/,/g, ''));
          break;
        }
      }
    }

    // Check availability
    const availEl = await page.$('#availability');
    const availText = availEl ? await availEl.textContent() : '';
    const inStock = !availText.toLowerCase().includes('unavailable');

    recordPrice({ sku_id: sku.id, platform: 'amazon', price, compare_at_price: null, in_stock: inStock });
    console.log(`  ✅ ${sku.model} Amazon: $${price ?? 'N/A'} ${inStock ? '✓' : '✗'}`);
  } catch (e) {
    console.error(`  ❌ ${sku.model} Amazon error: ${e.message}`);
    recordPrice({ sku_id: sku.id, platform: 'amazon', price: null, compare_at_price: null, in_stock: false });
  }
}

// ── Best Buy (public Products API — free key from developer.bestbuy.com) ─
// Set BESTBUY_API_KEY env var, or it falls back to Google Shopping search

function extractBestBuySkuId(url) {
  const match = url.match(/\/(\d{7})\.p/);
  return match ? match[1] : null;
}

async function checkBestBuyPrices(skus) {
  const apiKey = process.env.BESTBUY_API_KEY;

  if (apiKey) {
    // Use official Best Buy Products API
    console.log('  Using Best Buy Products API');
    const skuIds = skus.map(s => extractBestBuySkuId(s.bestbuy_url)).filter(Boolean);
    const batchSize = 10;

    for (let i = 0; i < skuIds.length; i += batchSize) {
      const batch = skuIds.slice(i, i + batchSize);
      const skuFilter = batch.map(id => `sku=${id}`).join('|');
      const apiUrl = `https://api.bestbuy.com/v1/products(${skuFilter})?apiKey=${apiKey}&format=json&show=sku,name,salePrice,regularPrice,onSale,inStoreAvailability,onlineAvailability`;

      try {
        const res = await fetch(apiUrl);
        if (!res.ok) {
          console.error(`  Best Buy API error: ${res.status}`);
          continue;
        }
        const data = await res.json();

        for (const product of data.products || []) {
          const sku = skus.find(s => extractBestBuySkuId(s.bestbuy_url) === String(product.sku));
          if (!sku) continue;

          const price = product.salePrice ?? product.regularPrice ?? null;
          const inStock = product.onlineAvailability ?? false;
          recordPrice({ sku_id: sku.id, platform: 'bestbuy', price, compare_at_price: null, in_stock: inStock });
          console.log(`  ✅ ${sku.model} Best Buy: $${price ?? 'N/A'} ${inStock ? '✓' : '✗'}${product.onSale ? ' SALE' : ''}`);
        }
      } catch (e) {
        console.error(`  Best Buy API batch error: ${e.message}`);
      }
      await delay(1000);
    }
  } else {
    // No API key — try DuckDuckGo price search as fallback
    console.log('  No BESTBUY_API_KEY set — using search fallback (less reliable)');
    console.log('  Get free API key at: https://developer.bestbuy.com/');

    for (const sku of skus) {
      try {
        const searchUrl = `https://html.duckduckgo.com/html/?q=bestbuy.com+TCL+${sku.model}+price`;
        const res = await fetch(searchUrl, {
          headers: { 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36' },
          signal: AbortSignal.timeout(10000),
        });
        if (res.ok) {
          const html = await res.text();
          // Look for price patterns in search snippets
          const priceMatch = html.match(/\$[\d,]+\.?\d*/);
          const price = priceMatch ? parseFloat(priceMatch[0].replace(/[$,]/g, '')) : null;
          if (price && price > 10) { // sanity check
            recordPrice({ sku_id: sku.id, platform: 'bestbuy', price, compare_at_price: null, in_stock: true });
            console.log(`  ✅ ${sku.model} Best Buy (search): $${price}`);
          } else {
            console.log(`  ⚠ ${sku.model} Best Buy: price not found in search`);
          }
        }
      } catch {
        console.log(`  ⚠ ${sku.model} Best Buy: search failed`);
      }
      await delay(2000);
    }
  }
}

// ── Main ────────────────────────────────────────────────────────────

async function main() {
  const skus = getActiveSkus();
  if (skus.length === 0) {
    console.log('No SKUs in catalog. Run: npm run build-catalog');
    process.exit(1);
  }

  console.log(`=== Daily Price Check: ${skus.length} SKUs ===`);
  console.log(`Timestamp: ${new Date().toISOString()}\n`);

  // 1. TCL prices via Shopify API (fast, no browser)
  await checkTclPrices(skus);

  // 2. Amazon (headless) + Best Buy (non-headless, BB blocks headless)
  const skusWithAmazon = skus.filter(s => s.amazon_url);
  const skusWithBestBuy = skus.filter(s => s.bestbuy_url);

  // Amazon — headless works fine
  if (skusWithAmazon.length > 0) {
    console.log(`\n── Checking Amazon (${skusWithAmazon.length} SKUs) ──`);
    const amzBrowser = await chromium.launch({
      headless: true,
      args: ['--disable-blink-features=AutomationControlled'],
    });
    const amzContext = await amzBrowser.newContext({
      userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
      viewport: { width: 1920, height: 1080 },
    });
    const amzPage = await amzContext.newPage();

    for (const sku of skusWithAmazon) {
      await checkAmazonPrice(amzPage, sku);
      await delay(2000);
    }
    await amzBrowser.close();
  }

  // Best Buy — API or search fallback (no browser needed)
  if (skusWithBestBuy.length > 0) {
    console.log(`\n── Checking Best Buy (${skusWithBestBuy.length} SKUs) ──`);
    await checkBestBuyPrices(skusWithBestBuy);
  }

  console.log('\n=== Price check complete ===');
}

main().catch(console.error);
