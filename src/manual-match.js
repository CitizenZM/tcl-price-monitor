/**
 * Manual URL matching tool.
 * Usage:
 *   node src/manual-match.js <model> --amazon <url> --bestbuy <url>
 *   node src/manual-match.js --list           # show all SKUs with match status
 *   node src/manual-match.js --unmatched      # show only unmatched SKUs
 *   node src/manual-match.js --import <file>  # import from JSON file
 */
import { getActiveSkus, updateMatchUrls } from './db.js';

function showList(filter = 'all') {
  const skus = getActiveSkus();
  console.log(`\n${'Model'.padEnd(16)} ${'Amazon'.padEnd(8)} ${'BestBuy'.padEnd(8)} Title`);
  console.log('─'.repeat(100));

  for (const sku of skus) {
    const hasAmazon = sku.amazon_url ? '✅' : '❌';
    const hasBestBuy = sku.bestbuy_url ? '✅' : '❌';

    if (filter === 'unmatched' && sku.amazon_url && sku.bestbuy_url) continue;

    console.log(`${sku.model.padEnd(16)} ${hasAmazon.padEnd(8)} ${hasBestBuy.padEnd(8)} ${sku.title.substring(0, 60)}`);
  }

  const matched = {
    amazon: skus.filter(s => s.amazon_url).length,
    bestbuy: skus.filter(s => s.bestbuy_url).length,
  };
  console.log(`\nTotal: ${skus.length} | Amazon: ${matched.amazon} | Best Buy: ${matched.bestbuy}`);
}

async function importFile(filePath) {
  const { readFileSync } = await import('fs');
  const data = JSON.parse(readFileSync(filePath, 'utf-8'));
  for (const [model, urls] of Object.entries(data)) {
    console.log(`${model}: ${JSON.stringify(urls)}`);
    updateMatchUrls(model, urls);
  }
  console.log(`\nImported ${Object.keys(data).length} entries.`);
}

const args = process.argv.slice(2);

if (args.includes('--list')) {
  showList('all');
} else if (args.includes('--unmatched')) {
  showList('unmatched');
} else if (args.includes('--import')) {
  const idx = args.indexOf('--import');
  importFile(args[idx + 1]);
} else if (args.length >= 1) {
  const model = args[0];
  const updates = {};
  const amzIdx = args.indexOf('--amazon');
  const bbIdx = args.indexOf('--bestbuy');
  if (amzIdx >= 0 && args[amzIdx + 1]) updates.amazon_url = args[amzIdx + 1];
  if (bbIdx >= 0 && args[bbIdx + 1]) updates.bestbuy_url = args[bbIdx + 1];

  if (Object.keys(updates).length > 0) {
    updateMatchUrls(model, updates);
    console.log(`Updated ${model}: ${JSON.stringify(updates)}`);
  } else {
    console.log('Usage: node src/manual-match.js <model> --amazon <url> --bestbuy <url>');
  }
} else {
  console.log('Usage:');
  console.log('  node src/manual-match.js --list                  # show all SKUs');
  console.log('  node src/manual-match.js --unmatched             # show unmatched only');
  console.log('  node src/manual-match.js <model> --amazon <url>  # set Amazon URL');
  console.log('  node src/manual-match.js --import urls.json      # bulk import');
}
