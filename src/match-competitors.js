/**
 * Phase 2: Match each TCL SKU on Amazon and Best Buy.
 * Strategy:
 *   1. Search Google for "site:amazon.com TCL <model>" and "site:bestbuy.com TCL <model>"
 *   2. Extract product page URLs from search results
 *   3. Save matched URLs to database
 *
 * Falls back to manual URL if Google is rate-limited.
 */
import { getActiveSkus, updateMatchUrls } from './db.js';

async function delay(ms) {
  return new Promise(r => setTimeout(r, ms + Math.random() * 2000));
}

async function googleSearch(query) {
  const url = `https://www.google.com/search?q=${encodeURIComponent(query)}&num=5`;
  try {
    const res = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.9',
      },
    });
    if (!res.ok) return null;
    return await res.text();
  } catch {
    return null;
  }
}

function extractAmazonUrl(html) {
  if (!html) return null;
  // Look for Amazon product page URLs in Google results
  const patterns = [
    /https?:\/\/www\.amazon\.com\/[^"&\s]*\/dp\/([A-Z0-9]{10})/g,
    /https?:\/\/www\.amazon\.com\/dp\/([A-Z0-9]{10})/g,
  ];
  for (const pat of patterns) {
    const match = pat.exec(html);
    if (match) {
      return `https://www.amazon.com/dp/${match[1]}`;
    }
  }
  return null;
}

function extractBestBuyUrl(html) {
  if (!html) return null;
  // Look for Best Buy product page URLs
  const patterns = [
    /https?:\/\/www\.bestbuy\.com\/site\/[^"&\s]*?\/(\d{7})\.p/g,
    /https?:\/\/www\.bestbuy\.com\/site\/[^"&\s]*\.p\?skuId=(\d+)/g,
  ];
  for (const pat of patterns) {
    const match = pat.exec(html);
    if (match) {
      return match[0].split('&')[0].split('"')[0];
    }
  }
  return null;
}

// Alternative: use DuckDuckGo HTML search (less likely to block)
async function duckDuckGoSearch(query) {
  const url = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`;
  try {
    const res = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
      },
    });
    if (!res.ok) return null;
    return await res.text();
  } catch {
    return null;
  }
}

async function findUrl(model, title, site) {
  const searchQuery = `site:${site} TCL ${model}`;

  // Try Google first
  let html = await googleSearch(searchQuery);
  let url = site.includes('amazon') ? extractAmazonUrl(html) : extractBestBuyUrl(html);

  if (!url) {
    // Try DuckDuckGo
    await delay(1000);
    html = await duckDuckGoSearch(`TCL ${model} ${site}`);
    url = site.includes('amazon') ? extractAmazonUrl(html) : extractBestBuyUrl(html);
  }

  return url;
}

async function main() {
  const skus = getActiveSkus();
  const needsMatch = skus.filter(s => !s.amazon_url || !s.bestbuy_url);

  if (needsMatch.length === 0 && !process.argv.includes('--force')) {
    console.log('All SKUs already matched. Use --force to re-match.');
    return;
  }

  const toProcess = process.argv.includes('--force') ? skus : needsMatch;
  console.log(`=== Matching ${toProcess.length} SKUs on Amazon & Best Buy ===\n`);

  let amazonFound = 0, bestbuyFound = 0;

  for (const sku of toProcess) {
    console.log(`🔍 ${sku.model}: ${sku.title.substring(0, 60)}...`);
    const updates = {};

    if (!sku.amazon_url || process.argv.includes('--force')) {
      const amazonUrl = await findUrl(sku.model, sku.title, 'www.amazon.com');
      if (amazonUrl) {
        updates.amazon_url = amazonUrl;
        amazonFound++;
        console.log(`  ✅ Amazon: ${amazonUrl}`);
      } else {
        console.log(`  ❌ Amazon: not found`);
      }
      await delay(3000);
    }

    if (!sku.bestbuy_url || process.argv.includes('--force')) {
      const bestbuyUrl = await findUrl(sku.model, sku.title, 'www.bestbuy.com');
      if (bestbuyUrl) {
        updates.bestbuy_url = bestbuyUrl;
        bestbuyFound++;
        console.log(`  ✅ Best Buy: ${bestbuyUrl}`);
      } else {
        console.log(`  ❌ Best Buy: not found`);
      }
      await delay(3000);
    }

    if (Object.keys(updates).length > 0) {
      updateMatchUrls(sku.model, updates);
    }
    console.log('');
  }

  console.log(`=== Matching complete: Amazon ${amazonFound}/${toProcess.length}, Best Buy ${bestbuyFound}/${toProcess.length} ===`);
  console.log(`\nTip: You can manually add URLs by editing the database or using:`);
  console.log(`  node src/manual-match.js <model> --amazon <url> --bestbuy <url>`);
}

main().catch(console.error);
