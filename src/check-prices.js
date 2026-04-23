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

// ── Best Buy ──────────────────────────────────────────────────────────
// Two modes:
//   1. API (BESTBUY_API_KEY) — works from CI/cloud, exact prices
//   2. Browser (system Chrome) — works locally, BB blocks data center IPs

function extractBestBuySkuId(url) {
  const match = url.match(/\/(\d{7})\.p/);
  return match ? match[1] : null;
}

async function checkBestBuyPricesViaApi(skus, apiKey) {
  console.log('  Using Best Buy Products API');
  const batchSize = 10;
  for (let i = 0; i < skus.length; i += batchSize) {
    const batch = skus.slice(i, i + batchSize);
    const skuIds = batch.map(s => extractBestBuySkuId(s.bestbuy_url)).filter(Boolean);
    const skuFilter = skuIds.map(id => `sku=${id}`).join('|');
    const apiUrl = `https://api.bestbuy.com/v1/products(${skuFilter})?apiKey=${apiKey}&format=json&show=sku,name,salePrice,regularPrice,onSale,onlineAvailability`;

    try {
      const res = await fetch(apiUrl);
      if (!res.ok) { console.error(`  API error: ${res.status}`); continue; }
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
      console.error(`  API batch error: ${e.message}`);
    }
    await delay(500);
  }
}

async function checkBestBuyPriceViaBrowser(page, sku) {
  if (!sku.bestbuy_url) return;
  try {
    await page.goto(sku.bestbuy_url, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await delay(2500);

    const result = await page.evaluate(() => {
      const body = document.body.innerText;
      const priceMatches = body.match(/\$[\d,]+\.?\d*/g) || [];
      const prices = priceMatches.map(p => parseFloat(p.replace(/[$,]/g, ''))).filter(p => p > 10);
      let compareAt = null;
      const compMatch = body.match(/Comp\.\s*Value:\s*\$([\d,]+\.?\d*)/i);
      if (compMatch) compareAt = parseFloat(compMatch[1].replace(/,/g, ''));
      const wasMatch = body.match(/Was\s*\$([\d,]+\.?\d*)/i);
      if (!compareAt && wasMatch) compareAt = parseFloat(wasMatch[1].replace(/,/g, ''));
      const soldOut = body.toLowerCase().includes('sold out') || body.toLowerCase().includes('coming soon');
      return { price: prices[0] || null, compareAt, inStock: !soldOut };
    });

    recordPrice({ sku_id: sku.id, platform: 'bestbuy', price: result.price, compare_at_price: result.compareAt, in_stock: result.inStock });
    console.log(`  ✅ ${sku.model} Best Buy: $${result.price ?? 'N/A'}${result.compareAt ? ` (was $${result.compareAt})` : ''} ${result.inStock ? '✓' : '✗'}`);
  } catch (e) {
    console.error(`  ❌ ${sku.model} Best Buy error: ${e.message.split('\n')[0]}`);
    recordPrice({ sku_id: sku.id, platform: 'bestbuy', price: null, compare_at_price: null, in_stock: false });
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

  const isCI = !!process.env.CI;

  // 2. Amazon + Best Buy via browser
  const skusWithAmazon = skus.filter(s => s.amazon_url);
  const skusWithBestBuy = skus.filter(s => s.bestbuy_url);

  // Amazon — bundled Chromium headless works fine
  if (skusWithAmazon.length > 0) {
    console.log(`\n── Checking Amazon (${skusWithAmazon.length} SKUs) ──`);
    try {
      const amzBrowser = await chromium.launch({
        headless: true,
        args: [
          '--disable-blink-features=AutomationControlled',
          '--no-sandbox',
          '--disable-setuid-sandbox',
        ],
      });
      const amzContext = await amzBrowser.newContext({
        userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        viewport: { width: 1920, height: 1080 },
        ignoreHTTPSErrors: true,
      });
      const amzPage = await amzContext.newPage();

      for (const sku of skusWithAmazon) {
        await checkAmazonPrice(amzPage, sku);
        await delay(2000);
      }
      await amzBrowser.close();
    } catch (e) {
      console.error(`Amazon browser error: ${e.message}`);
    }
  }

  // Best Buy — API if key available (works in CI), browser otherwise (local only)
  if (skusWithBestBuy.length > 0) {
    console.log(`\n── Checking Best Buy (${skusWithBestBuy.length} SKUs) ──`);
    const bbApiKey = process.env.BESTBUY_API_KEY;

    if (bbApiKey) {
      await checkBestBuyPricesViaApi(skusWithBestBuy, bbApiKey);
    } else if (!isCI) {
      // Browser scraping only works from residential IPs (local machine)
      try {
        let bbLaunchOpts = {
          channel: 'chrome',
          headless: false,
          args: ['--disable-blink-features=AutomationControlled'],
        };
        // Fall back to bundled Chromium if system Chrome not installed
        try {
          await chromium.launch({ channel: 'chrome', headless: true }).then(b => b.close());
        } catch {
          bbLaunchOpts = {
            headless: true,
            args: ['--disable-blink-features=AutomationControlled', '--no-sandbox', '--disable-setuid-sandbox'],
          };
          console.log('  (system Chrome not found, using bundled Chromium)');
        }
        const bbBrowser = await chromium.launch(bbLaunchOpts);
        const bbContext = await bbBrowser.newContext({ viewport: { width: 1440, height: 900 }, ignoreHTTPSErrors: true });
        const bbPage = await bbContext.newPage();
        await bbPage.addInitScript(() => {
          Object.defineProperty(navigator, 'webdriver', { get: () => false });
        });
        await bbPage.goto('https://www.bestbuy.com', { waitUntil: 'domcontentloaded', timeout: 20000 });
        await delay(2000);
        for (const sku of skusWithBestBuy) {
          await checkBestBuyPriceViaBrowser(bbPage, sku);
          await delay(2500);
        }
        await bbBrowser.close();
      } catch (e) {
        console.error(`Best Buy browser error: ${e.message}`);
      }
    } else {
      console.log('  Skipping — BESTBUY_API_KEY not set (browser scraping blocked from CI IPs)');
      console.log('  Get free API key at: https://developer.bestbuy.com/');
    }
  }

  console.log('\n=== Price check complete ===');
}

main().catch(console.error);
