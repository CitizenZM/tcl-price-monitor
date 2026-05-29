/**
 * Orchestrator: catalog → seed import → price check → report → email
 */
import { execSync } from 'child_process';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { appendFileSync, mkdirSync } from 'fs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const LOG_DIR = resolve(ROOT, 'logs');
mkdirSync(LOG_DIR, { recursive: true });

const today = new Date().toISOString().split('T')[0];
const logFile = resolve(LOG_DIR, `run-${today}.log`);

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  console.log(line);
  appendFileSync(logFile, line + '\n');
}

function run(label, cmd) {
  log(`START: ${label}`);
  try {
    const out = execSync(`node ${resolve(ROOT, 'src', cmd)}`, {
      cwd: ROOT, encoding: 'utf-8', timeout: 600000,
      stdio: ['pipe', 'pipe', 'pipe'],
      timeout: 1200000, // 20 min — BB browser scrolling needs extra time
    });
    log(`OK: ${label}`);
    if (out.trim()) appendFileSync(logFile, out + '\n');
    return true;
  } catch (e) {
    log(`FAIL: ${label} — ${e.message.split('\n')[0]}`);
    appendFileSync(logFile, (e.stdout || '') + (e.stderr || '') + '\n');
    return false;
  }
}

async function main() {
  log('═══ TCL Price Monitor — Daily Run ═══');
  run('Catalog refresh',    'build-catalog.js');
  run('Seed URL import',    'manual-match.js --import data/seed-urls.json');  // always re-sync known URLs
  run('Price check',        'check-prices.js');
  run('CSV report',         'report.js');
  run('PDF report',         'report-pdf.js');
  run('Email report',       'send-email.js');
  log('═══ Daily run complete ═══');
}

main().catch(e => { log(`Fatal: ${e.message}`); process.exit(1); });
