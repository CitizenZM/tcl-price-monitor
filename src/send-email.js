/**
 * Email Report — sends TCL Price Report PDF to stakeholders
 * Requires GMAIL_APP_PASSWORD in .env (Google App Password, not regular password)
 * Generate at: https://myaccount.google.com/apppasswords
 */
import nodemailer from 'nodemailer';
import { existsSync, readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { getAllLatestPrices } from './db.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');

// Load .env if present
const envPath = resolve(ROOT, '.env');
if (existsSync(envPath)) {
  for (const line of readFileSync(envPath, 'utf-8').split('\n')) {
    const match = line.match(/^([^#=]+)=(.*)$/);
    if (match) process.env[match[1].trim()] = match[2].trim();
  }
}

const SENDER = 'barronzuo@gmail.com';
const RECIPIENTS = [
  'lillian.li@celldigital.co',
  'fanfan@celldigital.co',
  'shane@celldigital.co',
];

const today = new Date().toISOString().split('T')[0];

function buildSummary() {
  const data = getAllLatestPrices();
  if (data.length === 0) return 'No price data available.';

  const withAmazon = data.filter(d => d.amazon_price !== null);
  const withBestBuy = data.filter(d => d.bestbuy_price !== null);
  const amazonCheaper = data.filter(d => d.amazon_price && d.tcl_price && d.amazon_price < d.tcl_price * 0.95);
  const bbCheaper = data.filter(d => d.bestbuy_price && d.tcl_price && d.bestbuy_price < d.tcl_price * 0.95);
  const tclCheapest = data.filter(d => {
    const ps = [d.tcl_price, d.amazon_price, d.bestbuy_price].filter(p => p !== null);
    return ps.length > 1 && d.tcl_price === Math.min(...ps);
  });

  // Top alerts
  const alerts = [];
  for (const row of data) {
    if (row.tcl_price && row.amazon_price && row.amazon_price < row.tcl_price * 0.95) {
      const pct = ((row.tcl_price - row.amazon_price) / row.tcl_price * 100).toFixed(1);
      alerts.push({ model: row.model, platform: 'Amazon', gap: pct, tclPrice: row.tcl_price, compPrice: row.amazon_price });
    }
    if (row.tcl_price && row.bestbuy_price && row.bestbuy_price < row.tcl_price * 0.95) {
      const pct = ((row.tcl_price - row.bestbuy_price) / row.tcl_price * 100).toFixed(1);
      alerts.push({ model: row.model, platform: 'Best Buy', gap: pct, tclPrice: row.tcl_price, compPrice: row.bestbuy_price });
    }
  }
  alerts.sort((a, b) => parseFloat(b.gap) - parseFloat(a.gap));

  const fmt = p => '$' + p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  let html = `
<div style="font-family: Arial, sans-serif; max-width: 700px;">
  <h2 style="color: #1a1a2e; margin-bottom: 4px;">TCL Price Monitoring Report</h2>
  <p style="color: #666; margin-top: 0;">${today} | us.tcl.com vs Amazon & Best Buy</p>

  <table style="border-collapse: collapse; width: 100%; margin: 16px 0;">
    <tr>
      <td style="padding: 12px; text-align: center; border: 1px solid #ddd;"><strong style="font-size: 24px;">${data.length}</strong><br><small>Total SKUs</small></td>
      <td style="padding: 12px; text-align: center; border: 1px solid #ddd;"><strong style="font-size: 24px;">${withAmazon.length}</strong><br><small>Amazon Matched</small></td>
      <td style="padding: 12px; text-align: center; border: 1px solid #ddd;"><strong style="font-size: 24px;">${withBestBuy.length}</strong><br><small>Best Buy Matched</small></td>
      <td style="padding: 12px; text-align: center; border: 1px solid #ddd;"><strong style="font-size: 24px; color: ${amazonCheaper.length > 0 ? '#c62828' : '#2e7d32'};">${amazonCheaper.length}</strong><br><small>Amazon Cheaper &gt;5%</small></td>
      <td style="padding: 12px; text-align: center; border: 1px solid #ddd;"><strong style="font-size: 24px; color: #2e7d32;">${tclCheapest.length}</strong><br><small>TCL Cheapest</small></td>
    </tr>
  </table>`;

  if (alerts.length > 0) {
    html += `
  <h3 style="color: #c62828;">Price Alerts (${alerts.length})</h3>
  <table style="border-collapse: collapse; width: 100%; font-size: 13px;">
    <tr style="background: #c62828; color: white;">
      <th style="padding: 6px 8px; text-align: left;">Model</th>
      <th style="padding: 6px 8px; text-align: left;">Competitor</th>
      <th style="padding: 6px 8px; text-align: right;">TCL Price</th>
      <th style="padding: 6px 8px; text-align: right;">Comp Price</th>
      <th style="padding: 6px 8px; text-align: right;">Gap</th>
    </tr>`;

    for (const a of alerts.slice(0, 15)) {
      html += `
    <tr style="border-bottom: 1px solid #eee;">
      <td style="padding: 5px 8px;">${a.model}</td>
      <td style="padding: 5px 8px;">${a.platform}</td>
      <td style="padding: 5px 8px; text-align: right;">${fmt(a.tclPrice)}</td>
      <td style="padding: 5px 8px; text-align: right;">${fmt(a.compPrice)}</td>
      <td style="padding: 5px 8px; text-align: right; color: #c62828; font-weight: bold;">-${a.gap}%</td>
    </tr>`;
    }
    if (alerts.length > 15) {
      html += `<tr><td colspan="5" style="padding: 5px 8px; color: #666;">...and ${alerts.length - 15} more (see attached PDF)</td></tr>`;
    }
    html += '</table>';
  } else {
    html += '<p style="color: #2e7d32;"><strong>No price alerts — TCL pricing is competitive across all platforms.</strong></p>';
  }

  html += `
  <p style="color: #999; font-size: 11px; margin-top: 20px;">
    Full report attached as PDF. Data from us.tcl.com, amazon.com, bestbuy.com.<br>
    Generated by TCL Price Monitor | ${new Date().toISOString()}
  </p>
</div>`;

  return html;
}

async function main() {
  const appPassword = process.env.GMAIL_APP_PASSWORD;
  if (!appPassword) {
    console.log('⚠ GMAIL_APP_PASSWORD not set — skipping email');
    console.log('  1. Enable 2FA at https://myaccount.google.com/security');
    console.log('  2. Generate App Password at https://myaccount.google.com/apppasswords');
    console.log('  3. Add to .env: GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx');
    return;
  }

  // Find PDF
  const pdfPath = resolve(process.env.HOME, 'Downloads', `TCL-Price-Report-${today}.pdf`);
  const ciPdfPath = resolve(ROOT, 'reports', `TCL-Price-Report-${today}.pdf`);
  const attachPath = existsSync(pdfPath) ? pdfPath : existsSync(ciPdfPath) ? ciPdfPath : null;

  if (!attachPath) {
    console.log('⚠ No PDF report found. Run: npm run report:pdf');
    return;
  }

  const transporter = nodemailer.createTransport({
    service: 'gmail',
    auth: { user: SENDER, pass: appPassword },
  });

  const htmlBody = buildSummary();

  const mailOptions = {
    from: `TCL Price Monitor <${SENDER}>`,
    to: RECIPIENTS.join(', '),
    subject: `TCL Price Report — ${today}`,
    html: htmlBody,
    attachments: [{ filename: `TCL-Price-Report-${today}.pdf`, path: attachPath }],
  };

  try {
    const info = await transporter.sendMail(mailOptions);
    console.log(`✅ Email sent to ${RECIPIENTS.join(', ')}`);
    console.log(`   Message ID: ${info.messageId}`);
  } catch (e) {
    console.error(`❌ Email failed: ${e.message}`);
    if (e.message.includes('535')) {
      console.log('  → Check GMAIL_APP_PASSWORD in .env (must be App Password, not regular password)');
    }
  }
}

main();
