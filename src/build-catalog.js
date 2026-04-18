/**
 * Phase 1: Scrape us.tcl.com Shopify API for all purchasable SKUs.
 * Builds the master catalog in SQLite.
 */
import { upsertSku } from './db.js';

const BASE = 'https://us.tcl.com';
const COLLECTIONS = [
  { slug: 'tv-for-sale', category: 'TV' },
  { slug: 'sound-bars', category: 'Soundbar' },
  { slug: 'tablets', category: 'Tablet' },
  { slug: 'mobile', category: 'Mobile' },
  { slug: 'monitors', category: 'Monitor' },
  { slug: 'window-air-conditioners', category: 'AC' },
  { slug: 'portable-air-conditioner', category: 'AC' },
  { slug: 'through-the-wall-air-conditioners', category: 'AC' },
  { slug: 'dehumidifiers', category: 'Dehumidifier' },
  { slug: 'compact-refrigerators', category: 'Refrigerator' },
  { slug: 'chest-freezers', category: 'Freezer' },
  { slug: 'upright-freezers', category: 'Freezer' },
  { slug: 'wine-beverage-centers', category: 'Wine/Beverage' },
  { slug: 'headphones', category: 'Audio' },
];

// Extract model number from title
function extractModel(title) {
  // Try explicit model after dash/em-dash at end: "– 98QM7L" or "- S510W"
  const dashMatch = title.match(/[–-]\s*([A-Z0-9][A-Z0-9-]+)\s*$/i);
  if (dashMatch) return dashMatch[1].trim();
  // Alphanumeric patterns like 98QM7L, 32R84, Q75H, S55H, TAB10, TP300K
  const modelMatch = title.match(/\b([A-Z]{1,3}\d{2,4}[A-Z0-9]*)\b/)
    || title.match(/\b(\d{2,3}[A-Z][A-Z0-9]+)\b/);
  if (modelMatch) return modelMatch[1].trim();
  // Product names like "NXTPAPER 14", "NXTPAPER 11 Plus"
  const nxtMatch = title.match(/(NXTPAPER\s+\d+(?:\s+(?:Plus|Pro|Gen\s*\d))?)/i);
  if (nxtMatch) return nxtMatch[1].replace(/\s+/g, '-').toUpperCase();
  return null;
}

// Extract specs from title
function extractSpecs(title) {
  const specs = {};
  // Size
  const sizeMatch = title.match(/(\d{2,3})[""'']/);
  if (sizeMatch) specs.size = sizeMatch[1] + '"';
  // Resolution
  if (/4K/i.test(title)) specs.resolution = '4K UHD';
  if (/8K/i.test(title)) specs.resolution = '8K';
  if (/1080p/i.test(title)) specs.resolution = '1080p';
  // Panel tech
  if (/Mini.?LED/i.test(title)) specs.panel = 'Mini LED';
  if (/QLED/i.test(title)) specs.panel = 'QLED';
  if (/OLED/i.test(title)) specs.panel = 'OLED';
  // Smart platform
  if (/Google TV/i.test(title)) specs.platform = 'Google TV';
  if (/Roku/i.test(title)) specs.platform = 'Roku';
  if (/Fire TV/i.test(title)) specs.platform = 'Fire TV';
  return JSON.stringify(specs);
}

async function fetchCollectionProducts(slug) {
  const products = [];
  let page = 1;
  while (true) {
    const url = `${BASE}/collections/${slug}/products.json?limit=250&page=${page}`;
    const res = await fetch(url);
    if (!res.ok) {
      console.error(`  Failed to fetch ${slug} page ${page}: ${res.status}`);
      break;
    }
    const data = await res.json();
    if (!data.products || data.products.length === 0) break;
    products.push(...data.products);
    if (data.products.length < 250) break;
    page++;
  }
  return products;
}

async function main() {
  console.log('=== TCL Catalog Builder ===');
  console.log(`Scanning ${COLLECTIONS.length} collections...\n`);

  const seen = new Set();
  let total = 0;
  let skipped = 0;

  for (const { slug, category } of COLLECTIONS) {
    console.log(`📦 Collection: ${slug}`);
    const products = await fetchCollectionProducts(slug);

    for (const product of products) {
      // Check if any variant is available for purchase
      const availableVariant = product.variants?.find(v => v.available);
      if (!availableVariant) {
        console.log(`  ⏭ SKIP (unavailable): ${product.title}`);
        skipped++;
        continue;
      }

      const model = extractModel(product.title);
      if (!model) {
        console.log(`  ⚠ No model found: ${product.title}`);
        continue;
      }

      if (seen.has(model)) continue; // dedup across collections
      seen.add(model);

      const tcl_url = `${BASE}/products/${product.handle}`;
      const specs = extractSpecs(product.title);

      upsertSku({ model, title: product.title, specs, tcl_url, category });
      console.log(`  ✅ ${model}: $${availableVariant.price} — ${product.title}`);
      total++;
    }
  }

  console.log(`\n=== Done: ${total} purchasable SKUs cataloged, ${skipped} skipped (unavailable) ===`);
}

main().catch(console.error);
