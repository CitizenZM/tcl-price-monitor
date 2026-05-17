/**
 * Phase 3: Daily price checker.
 * TCL  — Shopify JSON API (no browser, instant)
 * Amazon — headless Chromium (works reliably)
 * Best Buy — search-page scrape via Playwright browser (BB blocks product-page paths;
 *            search pages render price cards with data-product-id attributes)
 */
import { chromium } from 'playwright';
import { getActiveSkus, recordPrice } from './db.js';

const TCL_BASE = 'https://us.tcl.com';
const delay = ms => new Promise(r => setTimeout(r, ms + Math.random() * 500));

// ── TCL via Shopify API ──────────────────────────────────────────────

async function checkTclPrices(skus) {
  console.log('\n── TCL.com prices ──');
  const allProducts = [];
  let page = 1;
  while (true) {
    const res = await fetch(`${TCL_BASE}/products.json?limit=250&page=${page}`);
    if (!res.ok) break;
    const data = await res.json();
    if (!data.products?.length) break;
    allProducts.push(...data.products);
    if (data.products.length < 250) break;
    page++;
  }

  const productMap = new Map(allProducts.map(p => [p.handle, p]));

  for (const sku of skus) {
    const handle = sku.tcl_url.replace(`${TCL_BASE}/products/`, '');
    const product = productMap.get(handle);
    if (!product) {
      console.log(`  ⚠  ${sku.model}: not found`);
      recordPrice({ sku_id: sku.id, platform: 'tcl', price: null, compare_at_price: null, in_stock: false });
      continue;
    }
    const v = product.variants?.[0];
    const price = v ? parseFloat(v.price) : null;
    const compareAt = v?.compare_at_price ? parseFloat(v.compare_at_price) : null;
    recordPrice({ sku_id: sku.id, platform: 'tcl', price, compare_at_price: compareAt, in_stock: v?.available ?? false });
    console.log(`  ✅ ${sku.model}: $${price}${compareAt ? ` (was $${compareAt})` : ''}`);
  }
}

// ── Amazon via headless Chromium ─────────────────────────────────────

const AMZ_PRICE_SELECTORS = [
  '.a-price .a-offscreen',
  '.apexPriceToPay .a-offscreen',
  '#corePrice_feature_div .a-offscreen',
  '.priceToPay .a-offscreen',
  '#priceblock_ourprice',
  '#priceblock_dealprice',
];

async function checkAmazonPrice(page, sku) {
  if (!sku.amazon_url) return;
  try {
    await page.goto(sku.amazon_url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await delay(1200);
    let price = null;
    for (const sel of AMZ_PRICE_SELECTORS) {
      const el = await page.$(sel);
      if (el) {
        const m = (await el.textContent()).match(/[\d,]+\.?\d*/);
        if (m) { price = parseFloat(m[0].replace(/,/g, '')); break; }
      }
    }
    const availEl = await page.$('#availability');
    const availText = availEl ? await availEl.textContent() : '';
    const inStock = !availText.toLowerCase().includes('unavailable');
    recordPrice({ sku_id: sku.id, platform: 'amazon', price, compare_at_price: null, in_stock: inStock });
    console.log(`  ✅ ${sku.model} Amazon: $${price ?? 'N/A'}`);
  } catch (e) {
    console.log(`  ❌ ${sku.model} Amazon: ${e.message.split('\n')[0]}`);
    recordPrice({ sku_id: sku.id, platform: 'amazon', price: null, compare_at_price: null, in_stock: false });
  }
}

// ── Best Buy via search-page scrape ──────────────────────────────────
// BB blocks product-page paths for all automated clients (HTTP2 INTERNAL_ERROR).
// Search pages work and render product cards with data-product-id + price attributes.
// Strategy: search by model name → scroll to render virtualized list → extract prices.

function extractBBSkuId(url) {
  const m = url?.match(/\/(\d{6,7})\.p/);
  return m ? m[1] : null;
}

async function checkBestBuyPricesViaApi(skus, apiKey) {
  console.log('  Mode: Best Buy Products API');
  const batchSize = 10;
  for (let i = 0; i < skus.length; i += batchSize) {
    const batch = skus.slice(i, i + batchSize);
    const ids = batch.map(s => extractBBSkuId(s.bestbuy_url)).filter(Boolean);
    if (!ids.length) continue;
    const url = `https://api.bestbuy.com/v1/products(${ids.map(id => `sku=${id}`).join('|')})?apiKey=${apiKey}&format=json&show=sku,name,salePrice,regularPrice,onSale,onlineAvailability`;
    try {
      const res = await fetch(url);
      if (!res.ok) { console.log(`  API error: ${res.status}`); continue; }
      const data = await res.json();
      for (const p of data.products ?? []) {
        const sku = batch.find(s => extractBBSkuId(s.bestbuy_url) === String(p.sku));
        if (!sku) continue;
        const price = p.salePrice ?? p.regularPrice ?? null;
        recordPrice({ sku_id: sku.id, platform: 'bestbuy', price, compare_at_price: null, in_stock: p.onlineAvailability ?? false });
        console.log(`  ✅ ${sku.model} BB: $${price ?? 'N/A'}${p.onSale ? ' SALE' : ''}`);
      }
    } catch (e) { console.log(`  API batch error: ${e.message}`); }
    await delay(400);
  }
}

async function checkBestBuyPricesViaBrowser(skus) {
  console.log('  Mode: Best Buy search-page scrape');

  // Build bbSkuId→sku map for fast lookup
  const bbSkuMap = new Map();
  for (const sku of skus) {
    const id = extractBBSkuId(sku.bestbuy_url);
    if (id) bbSkuMap.set(id, sku);
  }

  const browser = await chromium.launch({
    headless: true,
    args: ['--disable-blink-features=AutomationControlled', '--no-sandbox', '--disable-setuid-sandbox'],
  });
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    viewport: { width: 1440, height: 900 },
  });
  const page = await ctx.newPage();

  // Extract all visible prices from the current page (scroll-triggered)
  const extractPrices = () => page.evaluate(() => {
    const found = {};
    document.querySelectorAll('[data-product-id]').forEach(li => {
      const s = li.getAttribute('data-product-id');
      const p = li.querySelector('[data-testid="price-block-customer-price"] span[aria-hidden="true"]')?.textContent?.trim();
      if (s && p) found[s] = p;
    });
    return found;
  });

  const scrollAndCollect = async () => {
    const all = {};
    Object.assign(all, await extractPrices());
    for (let i = 0; i < 18; i++) {
      await page.evaluate(() => window.scrollBy(0, 700));
      await delay(300);
      Object.assign(all, await extractPrices());
    }
    await page.evaluate(() => window.scrollTo(0, 0));
    return all;
  };

  const found = new Set();

  // Group searches: one search per model family covers multiple sizes efficiently
  const searches = [
    'TCL+QM6K+TV', 'TCL+QM7K+TV', 'TCL+QM8K+TV', 'TCL+QM9K+TV',
    'TCL+QM7L+SQD+Mini+LED', 'TCL+QM8L+SQD+Mini+LED',
    'TCL+A300W+NXTVISION+TV', 'TCL+Q3K+TV',
    'TCL+soundbar+Q+S+class', 'TCL+Q65H+soundbar',
    'TCL+monitor+gaming+G64+R84+R94',
    'TCL+X11L+6668232', 'TCL+115+QM7K+6621473',
    'TCL+Z100+FlexConnect+6646174',
  ];

  // Warm up on homepage
  await page.goto('https://www.bestbuy.com', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await delay(2000);

  for (const q of searches) {
    try {
      await page.goto(`https://www.bestbuy.com/site/searchpage.jsp?st=${q}`, {
        waitUntil: 'domcontentloaded', timeout: 45000,
      });
      await delay(1500);
      const prices = await scrollAndCollect();

      for (const [bbSkuId, priceStr] of Object.entries(prices)) {
        const sku = bbSkuMap.get(bbSkuId);
        if (!sku || found.has(sku.model)) continue;
        const price = parseFloat(priceStr.replace(/[$,]/g, ''));
        if (isNaN(price)) continue;
        recordPrice({ sku_id: sku.id, platform: 'bestbuy', price, compare_at_price: null, in_stock: true });
        console.log(`  ✅ ${sku.model} BB: $${price} (search: ${q.split('+')[1]})`);
        found.add(sku.model);
      }
    } catch (e) {
      console.log(`  ⚠  Search "${q}" failed: ${e.message.split('\n')[0]}`);
    }
    await delay(1000);
  }

  // For any still-missing, search by exact skuId
  const missing = skus.filter(s => !found.has(s.model));
  if (missing.length > 0) {
    console.log(`  Fetching ${missing.length} remaining via direct skuId search...`);
    for (const sku of missing) {
      const bbId = extractBBSkuId(sku.bestbuy_url);
      if (!bbId) continue;
      try {
        await page.goto(`https://www.bestbuy.com/site/searchpage.jsp?st=${bbId}+TCL`, {
          waitUntil: 'domcontentloaded', timeout: 45000,
        });
        await delay(1500);
        const prices = await scrollAndCollect();
        const priceStr = prices[bbId];
        if (priceStr) {
          const price = parseFloat(priceStr.replace(/[$,]/g, ''));
          recordPrice({ sku_id: sku.id, platform: 'bestbuy', price, compare_at_price: null, in_stock: true });
          console.log(`  ✅ ${sku.model} BB: $${price} (direct)`);
          found.add(sku.model);
        } else {
          console.log(`  ⚠  ${sku.model} BB: not found on BB`);
          recordPrice({ sku_id: sku.id, platform: 'bestbuy', price: null, compare_at_price: null, in_stock: false });
        }
      } catch (e) {
        console.log(`  ❌ ${sku.model} BB: ${e.message.split('\n')[0]}`);
        recordPrice({ sku_id: sku.id, platform: 'bestbuy', price: null, compare_at_price: null, in_stock: false });
      }
      await delay(1200);
    }
  }

  await browser.close();
  console.log(`  Best Buy: ${found.size}/${skus.length} prices fetched`);
}

// ── Main ─────────────────────────────────────────────────────────────

async function main() {
  const skus = getActiveSkus();
  if (!skus.length) { console.log('No SKUs. Run: npm run build-catalog'); process.exit(1); }

  console.log(`=== Price Check: ${skus.length} SKUs — ${new Date().toISOString()} ===`);

  await checkTclPrices(skus);

  const isCI = !!process.env.CI;
  const skusWithAmazon = skus.filter(s => s.amazon_url);
  const skusWithBestBuy = skus.filter(s => s.bestbuy_url);

  // Amazon — headless Chromium, one shared browser/page
  if (skusWithAmazon.length > 0) {
    console.log(`\n── Amazon (${skusWithAmazon.length} SKUs) ──`);
    const browser = await chromium.launch({
      headless: true,
      args: ['--disable-blink-features=AutomationControlled', '--no-sandbox', '--disable-setuid-sandbox'],
    });
    const ctx = await browser.newContext({
      userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
      viewport: { width: 1920, height: 1080 },
      ignoreHTTPSErrors: true,
    });
    const page = await ctx.newPage();
    for (const sku of skusWithAmazon) {
      await checkAmazonPrice(page, sku);
      await delay(1500);
    }
    await browser.close();
  }

  // Best Buy
  if (skusWithBestBuy.length > 0) {
    console.log(`\n── Best Buy (${skusWithBestBuy.length} SKUs) ──`);
    const bbApiKey = process.env.BESTBUY_API_KEY;
    if (bbApiKey) {
      await checkBestBuyPricesViaApi(skusWithBestBuy, bbApiKey);
    } else if (!isCI) {
      await checkBestBuyPricesViaBrowser(skusWithBestBuy);
    } else {
      console.log('  Skipping BB in CI — set BESTBUY_API_KEY secret to enable');
    }
  }

  console.log('\n=== Price check complete ===');
}

main().catch(e => { console.error(e); process.exit(1); });
