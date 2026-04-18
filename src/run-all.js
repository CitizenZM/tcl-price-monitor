/**
 * Orchestrator: runs catalog build → price check → report in sequence.
 * Used by the daily scheduler.
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
  const ts = new Date().toISOString();
  const line = `[${ts}] ${msg}`;
  console.log(line);
  appendFileSync(logFile, line + '\n');
}

function run(cmd, label) {
  log(`Starting: ${label}`);
  try {
    const output = execSync(`node ${resolve(ROOT, 'src', cmd)}`, {
      cwd: ROOT,
      encoding: 'utf-8',
      timeout: 600000, // 10 min
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    log(`Completed: ${label}`);
    appendFileSync(logFile, output + '\n');
    return true;
  } catch (e) {
    log(`FAILED: ${label} — ${e.message}`);
    appendFileSync(logFile, (e.stdout || '') + '\n' + (e.stderr || '') + '\n');
    return false;
  }
}

async function main() {
  log('═══ TCL Price Monitor — Daily Run ═══');

  // Step 1: Refresh catalog
  run('build-catalog.js', 'Catalog refresh');

  // Step 2: Check prices
  run('check-prices.js', 'Price check');

  // Step 3: Generate reports
  run('report.js', 'CSV report generation');
  run('report-pdf.js', 'PDF report generation');

  // Step 4: Email report
  run('send-email.js', 'Email report to stakeholders');

  log('═══ Daily run complete ═══');
}

main().catch(e => {
  log(`Fatal error: ${e.message}`);
  process.exit(1);
});
