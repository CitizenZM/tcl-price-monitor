/**
 * Phase 4: Generate price comparison report.
 * Outputs a formatted console report + CSV file.
 */
import { getAllLatestPrices, getPreviousPrice } from './db.js';
import { writeFileSync, mkdirSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPORTS_DIR = resolve(__dirname, '..', 'reports');
mkdirSync(REPORTS_DIR, { recursive: true });

function formatPrice(p) {
  if (p === null || p === undefined) return 'N/A';
  return `$${p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function priceDiff(base, compare) {
  if (base === null || compare === null || base === undefined || compare === undefined) return { diff: null, pct: null };
  const diff = compare - base;
  const pct = ((diff / base) * 100).toFixed(1);
  return { diff, pct };
}

function changeIndicator(currentPrice, previousPrice) {
  if (!currentPrice || !previousPrice) return '';
  if (currentPrice < previousPrice) return ` ↓${formatPrice(previousPrice - currentPrice)}`;
  if (currentPrice > previousPrice) return ` ↑${formatPrice(currentPrice - previousPrice)}`;
  return ' →';
}

function main() {
  const data = getAllLatestPrices();
  if (data.length === 0) {
    console.log('No price data. Run: npm run check-prices');
    return;
  }

  const today = new Date().toISOString().split('T')[0];
  console.log(`\n${'═'.repeat(120)}`);
  console.log(`  TCL PRICE MONITORING REPORT — ${today}`);
  console.log(`${'═'.repeat(120)}\n`);

  // Group by category
  const categories = {};
  for (const row of data) {
    const cat = row.category || 'Other';
    if (!categories[cat]) categories[cat] = [];
    categories[cat].push(row);
  }

  const csvRows = [
    ['Date', 'Model', 'Category', 'Title',
     'TCL Price', 'TCL Was', 'TCL Stock',
     'Amazon Price', 'Amazon Stock',
     'BestBuy Price', 'BestBuy Stock',
     'Amazon vs TCL ($)', 'Amazon vs TCL (%)',
     'BestBuy vs TCL ($)', 'BestBuy vs TCL (%)',
     'TCL Price Change', 'Amazon Price Change', 'BestBuy Price Change',
     'Cheapest Platform',
    ].join(','),
  ];

  const alerts = [];

  for (const [category, skus] of Object.entries(categories)) {
    console.log(`\n── ${category} ${'─'.repeat(110 - category.length)}`);
    console.log(`  ${'Model'.padEnd(14)} ${'TCL'.padEnd(14)} ${'Amazon'.padEnd(14)} ${'Best Buy'.padEnd(14)} ${'Amz vs TCL'.padEnd(14)} ${'BB vs TCL'.padEnd(14)} ${'Change'.padEnd(20)}`);
    console.log(`  ${'─'.repeat(104)}`);

    for (const row of skus) {
      // Get previous prices for change detection
      const prevTcl = getPreviousPrice(row.id, 'tcl');
      const prevAmz = getPreviousPrice(row.id, 'amazon');
      const prevBb = getPreviousPrice(row.id, 'bestbuy');

      const amzVsTcl = priceDiff(row.tcl_price, row.amazon_price);
      const bbVsTcl = priceDiff(row.tcl_price, row.bestbuy_price);

      const tclChange = changeIndicator(row.tcl_price, prevTcl?.price);
      const amzChange = changeIndicator(row.amazon_price, prevAmz?.price);
      const bbChange = changeIndicator(row.bestbuy_price, prevBb?.price);

      // Determine cheapest
      const prices = [
        { platform: 'TCL', price: row.tcl_price, stock: row.tcl_in_stock },
        { platform: 'Amazon', price: row.amazon_price, stock: row.amazon_in_stock },
        { platform: 'BestBuy', price: row.bestbuy_price, stock: row.bestbuy_in_stock },
      ].filter(p => p.price !== null && p.stock);
      const cheapest = prices.sort((a, b) => a.price - b.price)[0]?.platform ?? 'N/A';

      // Console output
      const amzDiffStr = amzVsTcl.diff !== null
        ? `${amzVsTcl.diff >= 0 ? '+' : ''}${formatPrice(amzVsTcl.diff)} (${amzVsTcl.pct}%)`
        : 'N/A';
      const bbDiffStr = bbVsTcl.diff !== null
        ? `${bbVsTcl.diff >= 0 ? '+' : ''}${formatPrice(bbVsTcl.diff)} (${bbVsTcl.pct}%)`
        : 'N/A';

      console.log(
        `  ${row.model.padEnd(14)} ` +
        `${formatPrice(row.tcl_price).padEnd(14)} ` +
        `${formatPrice(row.amazon_price).padEnd(14)} ` +
        `${formatPrice(row.bestbuy_price).padEnd(14)} ` +
        `${amzDiffStr.padEnd(14)} ` +
        `${bbDiffStr.padEnd(14)} ` +
        `${tclChange || amzChange || bbChange ? [tclChange, amzChange, bbChange].filter(Boolean).join(' ') : 'stable'}`
      );

      // Alerts
      if (row.tcl_price && row.amazon_price && row.amazon_price < row.tcl_price * 0.95) {
        alerts.push(`⚠ ${row.model}: Amazon is ${Math.abs(amzVsTcl.pct)}% CHEAPER than TCL ($${row.amazon_price} vs $${row.tcl_price})`);
      }
      if (row.tcl_price && row.bestbuy_price && row.bestbuy_price < row.tcl_price * 0.95) {
        alerts.push(`⚠ ${row.model}: Best Buy is ${Math.abs(bbVsTcl.pct)}% CHEAPER than TCL ($${row.bestbuy_price} vs $${row.tcl_price})`);
      }
      if (prevTcl && row.tcl_price && row.tcl_price !== prevTcl.price) {
        alerts.push(`📊 ${row.model}: TCL price changed from $${prevTcl.price} to $${row.tcl_price}`);
      }

      // CSV row
      csvRows.push([
        today, row.model, category, `"${row.title}"`,
        row.tcl_price ?? '', row.tcl_compare_price ?? '', row.tcl_in_stock ? 'Yes' : 'No',
        row.amazon_price ?? '', row.amazon_in_stock ? 'Yes' : 'No',
        row.bestbuy_price ?? '', row.bestbuy_in_stock ? 'Yes' : 'No',
        amzVsTcl.diff ?? '', amzVsTcl.pct ?? '',
        bbVsTcl.diff ?? '', bbVsTcl.pct ?? '',
        prevTcl ? (row.tcl_price - prevTcl.price) || 0 : '',
        prevAmz ? (row.amazon_price - prevAmz.price) || 0 : '',
        prevBb ? (row.bestbuy_price - prevBb.price) || 0 : '',
        cheapest,
      ].join(','));
    }
  }

  // Alerts section
  if (alerts.length > 0) {
    console.log(`\n\n${'═'.repeat(120)}`);
    console.log('  PRICE ALERTS');
    console.log(`${'═'.repeat(120)}`);
    for (const alert of alerts) {
      console.log(`  ${alert}`);
    }
  }

  // Summary stats
  const totalSkus = data.length;
  const withAmazon = data.filter(d => d.amazon_price !== null).length;
  const withBestBuy = data.filter(d => d.bestbuy_price !== null).length;
  const tclCheapest = data.filter(d => {
    const prices = [d.tcl_price, d.amazon_price, d.bestbuy_price].filter(p => p !== null);
    return prices.length > 1 && d.tcl_price === Math.min(...prices);
  }).length;

  console.log(`\n\n${'═'.repeat(120)}`);
  console.log('  SUMMARY');
  console.log(`${'═'.repeat(120)}`);
  console.log(`  Total SKUs tracked:         ${totalSkus}`);
  console.log(`  Matched on Amazon:          ${withAmazon}`);
  console.log(`  Matched on Best Buy:        ${withBestBuy}`);
  console.log(`  TCL is cheapest:            ${tclCheapest} SKUs`);
  console.log(`  Price alerts:               ${alerts.length}`);
  console.log(`${'═'.repeat(120)}\n`);

  // Save CSV
  const csvPath = resolve(REPORTS_DIR, `tcl-prices-${today}.csv`);
  writeFileSync(csvPath, csvRows.join('\n'), 'utf-8');
  console.log(`📄 CSV report saved: ${csvPath}`);
}

main();
