/**
 * Scheduler: runs the full pipeline daily at 7:00 AM local time.
 * Keep this running in the background, or use the launchd plist for persistence.
 */
import cron from 'node-cron';
import { execSync } from 'child_process';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');

console.log('TCL Price Monitor Scheduler started');
console.log('Schedule: Daily at 7:00 AM local time');
console.log('Press Ctrl+C to stop\n');

// Run daily at 7:00 AM
cron.schedule('0 7 * * *', () => {
  console.log(`[${new Date().toISOString()}] Running daily price check...`);
  try {
    execSync(`node ${resolve(ROOT, 'src', 'run-all.js')}`, {
      cwd: ROOT,
      stdio: 'inherit',
      timeout: 900000, // 15 min
    });
  } catch (e) {
    console.error(`[${new Date().toISOString()}] Daily run failed: ${e.message}`);
  }
});

// Also run immediately on start if --now flag
if (process.argv.includes('--now')) {
  console.log('Running immediately (--now flag)...\n');
  try {
    execSync(`node ${resolve(ROOT, 'src', 'run-all.js')}`, {
      cwd: ROOT,
      stdio: 'inherit',
      timeout: 900000,
    });
  } catch (e) {
    console.error(`Immediate run failed: ${e.message}`);
  }
}
