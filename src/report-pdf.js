/**
 * PDF Report Generator — TCL Price Comparison Report
 * Outputs to ~/Downloads/TCL-Price-Report-YYYY-MM-DD.pdf
 */
import PDFDocument from 'pdfkit';
import { getAllLatestPrices } from './db.js';
import { createWriteStream, mkdirSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const today = new Date().toISOString().split('T')[0];
const REPORTS_DIR = process.env.CI
  ? resolve(dirname(fileURLToPath(import.meta.url)), '..', 'reports')
  : resolve(process.env.HOME, 'Downloads');
mkdirSync(REPORTS_DIR, { recursive: true });
const OUTPUT = resolve(REPORTS_DIR, `TCL-Price-Report-${today}.pdf`);

// ── Colors ──
const C = {
  dark: '#1a1a2e',
  red: '#c62828',
  green: '#2e7d32',
  orange: '#e65100',
  muted: '#666',
  lightGray: '#f4f4f8',
  headerBg: '#16213e',
  alertBg: '#ffebee',
  white: '#fff',
  black: '#000',
};

function fmt(p) {
  if (p === null || p === undefined) return '—';
  return '$' + p.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function fmtExact(p) {
  if (p === null || p === undefined) return '—';
  return '$' + p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pctDiff(base, compare) {
  if (!base || !compare) return null;
  return ((compare - base) / base * 100).toFixed(1);
}

function extractSize(model, specs) {
  if (specs) {
    try {
      const parsed = typeof specs === 'string' ? JSON.parse(specs) : specs;
      if (parsed.size) return parsed.size.replace('"', '"');
    } catch {}
  }
  const m = model.match(/^(\d{2,3})/);
  return m ? m[1] + '"' : '—';
}

function cheapestLabel(tcl, amz, bb) {
  const prices = [];
  if (tcl) prices.push({ p: tcl, l: 'TCL' });
  if (amz) prices.push({ p: amz, l: 'AMZ' });
  if (bb) prices.push({ p: bb, l: 'BB' });
  if (prices.length < 2) return '—';
  prices.sort((a, b) => a.p - b.p);
  return prices[0].l;
}

function main() {
  const data = getAllLatestPrices();
  if (data.length === 0) {
    console.log('No price data. Run: npm run check-prices');
    return;
  }

  const doc = new PDFDocument({
    size: 'A4',
    layout: 'landscape',
    margins: { top: 36, bottom: 30, left: 36, right: 36 },
  });
  doc.pipe(createWriteStream(OUTPUT));

  const pageW = doc.page.width - 72; // usable width

  // ── Header Bar ──
  doc.rect(0, 0, doc.page.width, 56).fill(C.headerBg);
  doc.fontSize(20).font('Helvetica-Bold').fillColor(C.white)
    .text('TCL Price Monitoring Report', 36, 14, { width: pageW });
  doc.fontSize(9).font('Helvetica').fillColor('#aab')
    .text(`${today}  |  us.tcl.com vs Amazon & Best Buy  |  ${data.length} SKUs`, 36, 38, { width: pageW });
  doc.fillColor(C.black);
  doc.y = 68;

  // ── Executive Summary (compact 2-column layout) ──
  const withAmazon = data.filter(d => d.amazon_price !== null);
  const withBestBuy = data.filter(d => d.bestbuy_price !== null);
  const amazonCheaper = data.filter(d => d.amazon_price && d.tcl_price && d.amazon_price < d.tcl_price * 0.95);
  const bbCheaper = data.filter(d => d.bestbuy_price && d.tcl_price && d.bestbuy_price < d.tcl_price * 0.95);
  const tclCheapest = data.filter(d => {
    const ps = [d.tcl_price, d.amazon_price, d.bestbuy_price].filter(p => p !== null);
    return ps.length > 1 && d.tcl_price === Math.min(...ps);
  });

  // Summary boxes
  const boxes = [
    { label: 'Total SKUs', value: String(data.length), color: C.dark },
    { label: 'Amazon Matched', value: `${withAmazon.length}`, color: C.dark },
    { label: 'Best Buy Matched', value: `${withBestBuy.length}`, color: C.dark },
    { label: 'Amazon Cheaper >5%', value: `${amazonCheaper.length}`, color: amazonCheaper.length > 0 ? C.red : C.green },
    { label: 'Best Buy Cheaper >5%', value: `${bbCheaper.length}`, color: bbCheaper.length > 0 ? C.red : C.green },
    { label: 'TCL Cheapest', value: `${tclCheapest.length}`, color: C.green },
  ];

  const boxW = (pageW - 25) / 6;
  const boxH = 38;
  const boxY = doc.y;
  for (let i = 0; i < boxes.length; i++) {
    const bx = 36 + i * (boxW + 5);
    doc.rect(bx, boxY, boxW, boxH).lineWidth(0.5).stroke('#ddd');
    doc.fontSize(16).font('Helvetica-Bold').fillColor(boxes[i].color)
      .text(boxes[i].value, bx, boxY + 5, { width: boxW, align: 'center' });
    doc.fontSize(7).font('Helvetica').fillColor(C.muted)
      .text(boxes[i].label, bx, boxY + 25, { width: boxW, align: 'center' });
  }
  doc.y = boxY + boxH + 12;

  // ── Group by category ──
  const categories = {};
  for (const row of data) {
    const cat = row.category || 'Other';
    if (!categories[cat]) categories[cat] = [];
    categories[cat].push(row);
  }

  // Table column definitions — compact
  const cols = [
    { label: 'Model', width: 72, align: 'left' },
    { label: 'Size', width: 32, align: 'center' },
    { label: 'TCL', width: 62, align: 'right' },
    { label: 'Was', width: 58, align: 'right' },
    { label: 'Amazon', width: 62, align: 'right' },
    { label: 'Best Buy', width: 62, align: 'right' },
    { label: 'AMZ vs TCL', width: 52, align: 'right' },
    { label: 'BB vs TCL', width: 52, align: 'right' },
    { label: 'Best', width: 36, align: 'center' },
  ];
  const tableW = cols.reduce((s, c) => s + c.width, 0);
  const startX = 36;
  const rowH = 15;

  function drawHeader(y) {
    doc.rect(startX, y, tableW, rowH + 1).fill(C.headerBg);
    let x = startX;
    doc.font('Helvetica-Bold').fontSize(7).fillColor(C.white);
    for (const col of cols) {
      doc.text(col.label, x + 2, y + 4, { width: col.width - 4, align: col.align });
      x += col.width;
    }
    return y + rowH + 1;
  }

  function needsNewPage(neededH) {
    return doc.y + neededH > doc.page.height - 35;
  }

  // Draw tables by category
  for (const [category, rows] of Object.entries(categories)) {
    // Check if at least header + 2 rows fit
    if (needsNewPage(rowH * 3 + 22)) doc.addPage();

    // Category title
    doc.fontSize(11).font('Helvetica-Bold').fillColor(C.dark).text(category, startX, doc.y);
    doc.moveDown(0.15);

    let y = drawHeader(doc.y);

    for (let i = 0; i < rows.length; i++) {
      if (y + rowH > doc.page.height - 35) {
        doc.addPage();
        // Re-draw category continuation header
        doc.fontSize(9).font('Helvetica').fillColor(C.muted).text(`${category} (cont.)`, startX, 36);
        doc.y = 50;
        y = drawHeader(doc.y);
      }

      const row = rows[i];

      // Alternate row bg
      if (i % 2 === 0) {
        doc.rect(startX, y, tableW, rowH).fill(C.lightGray);
      }

      const size = extractSize(row.model, row.specs);
      const amzPct = pctDiff(row.tcl_price, row.amazon_price);
      const bbPct = pctDiff(row.tcl_price, row.bestbuy_price);
      const cheapest = cheapestLabel(row.tcl_price, row.amazon_price, row.bestbuy_price);

      const amzStr = amzPct !== null ? `${amzPct > 0 ? '+' : ''}${amzPct}%` : '—';
      const bbStr = bbPct !== null ? `${bbPct > 0 ? '+' : ''}${bbPct}%` : '—';

      const values = [
        row.model, size, fmt(row.tcl_price), fmt(row.tcl_compare_price),
        fmt(row.amazon_price), fmt(row.bestbuy_price), amzStr, bbStr, cheapest,
      ];

      let x = startX;
      for (let j = 0; j < cols.length; j++) {
        let color = C.black;
        let font = 'Helvetica';

        // Color code diff columns
        if (j === 6 && amzPct !== null) {
          const v = parseFloat(amzPct);
          color = v < -5 ? C.red : v > 5 ? C.green : C.muted;
          font = Math.abs(v) > 5 ? 'Helvetica-Bold' : 'Helvetica';
        }
        if (j === 7 && bbPct !== null) {
          const v = parseFloat(bbPct);
          color = v < -5 ? C.red : v > 5 ? C.green : C.muted;
          font = Math.abs(v) > 5 ? 'Helvetica-Bold' : 'Helvetica';
        }
        if (j === 8) {
          color = values[j] === 'TCL' ? C.green : values[j] === 'AMZ' ? C.red : values[j] === 'BB' ? C.orange : C.muted;
          font = 'Helvetica-Bold';
        }

        doc.font(font).fontSize(7).fillColor(color)
          .text(values[j], x + 2, y + 4, { width: cols[j].width - 4, align: cols[j].align });
        x += cols[j].width;
      }
      y += rowH;
    }
    doc.y = y + 8;
    doc.fillColor(C.black);
  }

  // ── Price Alerts (only if there are any) ──
  const alerts = [];
  for (const row of data) {
    if (row.tcl_price && row.amazon_price && row.amazon_price < row.tcl_price * 0.95) {
      alerts.push({
        model: row.model, platform: 'Amazon', category: row.category,
        tclPrice: row.tcl_price, compPrice: row.amazon_price,
        pct: Math.abs(pctDiff(row.tcl_price, row.amazon_price)),
      });
    }
    if (row.tcl_price && row.bestbuy_price && row.bestbuy_price < row.tcl_price * 0.95) {
      alerts.push({
        model: row.model, platform: 'Best Buy', category: row.category,
        tclPrice: row.tcl_price, compPrice: row.bestbuy_price,
        pct: Math.abs(pctDiff(row.tcl_price, row.bestbuy_price)),
      });
    }
  }
  alerts.sort((a, b) => parseFloat(b.pct) - parseFloat(a.pct));

  if (alerts.length > 0) {
    if (needsNewPage(alerts.length * 14 + 40)) doc.addPage();

    // Alert header with red accent bar
    doc.rect(startX, doc.y, 3, 16).fill(C.red);
    doc.fontSize(13).font('Helvetica-Bold').fillColor(C.red)
      .text(`  Price Alerts (${alerts.length})`, startX + 6, doc.y + 1);
    doc.moveDown(0.3);

    const aCols = [
      { label: 'Model', width: 80 },
      { label: 'Category', width: 72 },
      { label: 'Competitor', width: 65 },
      { label: 'TCL Price', width: 70 },
      { label: 'Comp Price', width: 70 },
      { label: 'Gap', width: 50 },
      { label: 'Severity', width: 80 },
    ];
    const aRowH = 14;
    let ay = doc.y;

    // Alert header row
    doc.rect(startX, ay, aCols.reduce((s, c) => s + c.width, 0), aRowH).fill(C.red);
    let ax = startX;
    doc.font('Helvetica-Bold').fontSize(7).fillColor(C.white);
    for (const col of aCols) {
      doc.text(col.label, ax + 2, ay + 4, { width: col.width - 4 });
      ax += col.width;
    }
    ay += aRowH;

    for (let i = 0; i < alerts.length; i++) {
      if (ay + aRowH > doc.page.height - 35) {
        doc.addPage();
        ay = 36;
        // Redraw header
        doc.rect(startX, ay, aCols.reduce((s, c) => s + c.width, 0), aRowH).fill(C.red);
        ax = startX;
        doc.font('Helvetica-Bold').fontSize(7).fillColor(C.white);
        for (const col of aCols) {
          doc.text(col.label, ax + 2, ay + 4, { width: col.width - 4 });
          ax += col.width;
        }
        ay += aRowH;
      }

      const a = alerts[i];
      if (i % 2 === 0) {
        doc.rect(startX, ay, aCols.reduce((s, c) => s + c.width, 0), aRowH).fill(C.alertBg);
      }

      const severity = parseFloat(a.pct) > 20 ? 'CRITICAL' : parseFloat(a.pct) > 10 ? 'HIGH' : 'MONITOR';
      const sevColor = severity === 'CRITICAL' ? C.red : severity === 'HIGH' ? C.orange : C.muted;

      const vals = [a.model, a.category, a.platform, fmtExact(a.tclPrice), fmtExact(a.compPrice), `-${a.pct}%`, severity];
      ax = startX;
      for (let j = 0; j < aCols.length; j++) {
        if (j === 5) {
          doc.font('Helvetica-Bold').fillColor(C.red);
        } else if (j === 6) {
          doc.font('Helvetica-Bold').fillColor(sevColor);
        } else {
          doc.font('Helvetica').fillColor(C.black);
        }
        doc.fontSize(7).text(vals[j], ax + 2, ay + 4, { width: aCols[j].width - 4 });
        ax += aCols[j].width;
      }
      ay += aRowH;
    }
    doc.y = ay + 8;
  }

  // ── Price Parity Analysis (compact) ──
  const parity = data.filter(d => d.amazon_price && d.tcl_price);
  if (parity.length > 0) {
    if (needsNewPage(80)) doc.addPage();

    doc.rect(startX, doc.y, 3, 14).fill(C.dark);
    doc.fontSize(11).font('Helvetica-Bold').fillColor(C.dark)
      .text('  Price Parity Analysis', startX + 6, doc.y + 1);
    doc.moveDown(0.3);

    const exact = parity.filter(d => Math.abs(d.amazon_price - d.tcl_price) < 3);
    const amzLower = parity.filter(d => d.amazon_price < d.tcl_price * 0.97);
    const tclLower = parity.filter(d => d.tcl_price < d.amazon_price * 0.97);
    const avgDiff = parity.reduce((s, d) => s + ((d.amazon_price - d.tcl_price) / d.tcl_price * 100), 0) / parity.length;

    doc.fontSize(8).font('Helvetica').fillColor(C.black);
    const stats = [
      `${parity.length} SKUs compared with Amazon:  ${exact.length} at parity (<$3)  |  ${amzLower.length} Amazon cheaper (>3%)  |  ${tclLower.length} TCL cheaper (>3%)  |  Avg delta: ${avgDiff.toFixed(1)}%`,
    ];
    doc.text(stats[0], startX + 10, doc.y, { width: pageW - 20 });
  }

  // ── Footer ──
  doc.moveDown(0.5);
  doc.fontSize(7).fillColor('#999')
    .text(`TCL Price Monitor  |  ${new Date().toISOString()}  |  us.tcl.com / amazon.com / bestbuy.com`, { align: 'center' });

  doc.end();
  console.log(`PDF report saved: ${OUTPUT}`);
}

main();
