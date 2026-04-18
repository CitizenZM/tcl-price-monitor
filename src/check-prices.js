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

// ── Best Buy (system Chrome — BB blocks Playwright's bundled Chromium) ──

async function checkBestBuyPrice(page, sku) {
  if (!sku.bestbuy_url) return;
  try {
    await page.goto(sku.bestbuy_url, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await delay(2500);

    // Extract price from page — BB renders prices in multiple locations
    let price = null;
    let compareAt = null;

    const result = await page.evaluate(() => {
      const body = document.body.innerText;
      // Find all price patterns
      const priceMatches = body.match(/\$[\d,]+\.?\d*/g) || [];
      const prices = priceMatches
        .map(p => parseFloat(p.replace(/[$,]/g, '')))
        .filter(p => p > 10);

      // Look for "Comp. Value" or "Was" price
      let compareAt = null;
      const compMatch = body.match(/Comp\.\s*Value:\s*\$([\d,]+\.?\d*)/i);
      if (compMatch) compareAt = parseFloat(compMatch[1].replace(/,/g, ''));
      const wasMatch = body.match(/Was\s*\$([\d,]+\.?\d*)/i);
      if (!compareAt && wasMatch) compareAt = parseFloat(wasMatch[1].replace(/,/g, ''));

      // First price is usually the sale/current price
      const salePrice = prices.length > 0 ? prices[0] : null;

      // Check stock
      const soldOut = body.toLowerCase().includes('sold out') || body.toLowerCase().includes('coming soon');

      return { price: salePrice, compareAt, inStock: !soldOut };
    });

    price = result.price;
    compareAt = result.compareAt;
    const inStock = result.inStock;

    recordPrice({ sku_id: sku.id, platform: 'bestbuy', price, compare_at_price: compareAt, in_stock: inStock });
    console.log(`  ✅ ${sku.model} Best Buy: $${price ?? 'N/A'}${compareAt ? ` (was $${compareAt})` : ''} ${inStock ? '✓' : '✗'}`);
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
          ...(isCI ? ['--no-sandbox', '--disable-setuid-sandbox'] : []),
        ],
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
    } catch (e) {
      console.error(`Amazon browser error: ${e.message}`);
    }
  }

  // Best Buy — system Chrome required (BB blocks Playwright's bundled Chromium)
  if (skusWithBestBuy.length > 0) {
    console.log(`\n── Checking Best Buy (${skusWithBestBuy.length} SKUs) ──`);
    try {
      const bbBrowser = await chromium.launch({
        channel: 'chrome',
        headless: isCI,
        args: [
          '--disable-blink-features=AutomationControlled',
          ...(isCI ? ['--no-sandbox', '--disable-setuid-sandbox'] : []),
        ],
      });
      const bbContext = await bbBrowser.newContext({
        viewport: { width: 1440, height: 900 },
      });
      const bbPage = await bbContext.newPage();
      await bbPage.addInitScript(() => {
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
      });

      // Warm up session cookies
      await bbPage.goto('https://www.bestbuy.com', { waitUntil: 'domcontentloaded', timeout: 20000 });
      await delay(2000);

      for (const sku of skusWithBestBuy) {
        await checkBestBuyPrice(bbPage, sku);
        await delay(2500);
      }
      await bbBrowser.close();
    } catch (e) {
      console.error(`Best Buy browser error: ${e.message}`);
    }
  }

  console.log('\n=== Price check complete ===');
}

main().catch(console.error);
