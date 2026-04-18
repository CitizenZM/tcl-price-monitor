import Database from 'better-sqlite3';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DB_PATH = resolve(__dirname, '..', 'data', 'prices.db');

// Ensure data directory exists
import { mkdirSync } from 'fs';
mkdirSync(resolve(__dirname, '..', 'data'), { recursive: true });

const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');

// Create tables
db.exec(`
  CREATE TABLE IF NOT EXISTS skus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    specs TEXT,
    tcl_url TEXT NOT NULL,
    amazon_url TEXT,
    bestbuy_url TEXT,
    category TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku_id INTEGER NOT NULL,
    platform TEXT NOT NULL CHECK(platform IN ('tcl', 'amazon', 'bestbuy')),
    price REAL,
    compare_at_price REAL,
    in_stock INTEGER DEFAULT 1,
    checked_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (sku_id) REFERENCES skus(id)
  );

  CREATE INDEX IF NOT EXISTS idx_price_history_sku_date
    ON price_history(sku_id, checked_at DESC);

  CREATE INDEX IF NOT EXISTS idx_price_history_platform
    ON price_history(platform, checked_at DESC);
`);

export default db;

// Helper: upsert a SKU
export function upsertSku({ model, title, specs, tcl_url, category }) {
  const stmt = db.prepare(`
    INSERT INTO skus (model, title, specs, tcl_url, category)
    VALUES (@model, @title, @specs, @tcl_url, @category)
    ON CONFLICT(model) DO UPDATE SET
      title = @title,
      specs = @specs,
      tcl_url = @tcl_url,
      category = @category,
      updated_at = datetime('now')
  `);
  return stmt.run({ model, title, specs, tcl_url, category });
}

// Helper: update Amazon/BestBuy URLs
export function updateMatchUrls(model, { amazon_url, bestbuy_url }) {
  const sets = [];
  const params = { model };
  if (amazon_url !== undefined) {
    sets.push('amazon_url = @amazon_url');
    params.amazon_url = amazon_url;
  }
  if (bestbuy_url !== undefined) {
    sets.push('bestbuy_url = @bestbuy_url');
    params.bestbuy_url = bestbuy_url;
  }
  if (sets.length === 0) return;
  sets.push("updated_at = datetime('now')");
  db.prepare(`UPDATE skus SET ${sets.join(', ')} WHERE model = @model`).run(params);
}

// Helper: record a price check
export function recordPrice({ sku_id, platform, price, compare_at_price, in_stock }) {
  db.prepare(`
    INSERT INTO price_history (sku_id, platform, price, compare_at_price, in_stock)
    VALUES (@sku_id, @platform, @price, @compare_at_price, @in_stock)
  `).run({ sku_id, platform, price, compare_at_price: compare_at_price ?? null, in_stock: in_stock ? 1 : 0 });
}

// Helper: get all active SKUs
export function getActiveSkus() {
  return db.prepare('SELECT * FROM skus WHERE active = 1 ORDER BY category, model').all();
}

// Helper: get latest price for a SKU on a platform
export function getLatestPrice(sku_id, platform) {
  return db.prepare(`
    SELECT * FROM price_history
    WHERE sku_id = @sku_id AND platform = @platform
    ORDER BY checked_at DESC LIMIT 1
  `).get({ sku_id, platform });
}

// Helper: get price history for a SKU on a platform (last N days)
export function getPriceHistory(sku_id, platform, days = 30) {
  return db.prepare(`
    SELECT * FROM price_history
    WHERE sku_id = @sku_id AND platform = @platform
      AND checked_at >= datetime('now', @days_ago)
    ORDER BY checked_at DESC
  `).all({ sku_id, platform, days_ago: `-${days} days` });
}

// Helper: get all latest prices grouped by SKU
export function getAllLatestPrices() {
  return db.prepare(`
    SELECT
      s.id, s.model, s.title, s.category, s.specs,
      s.tcl_url, s.amazon_url, s.bestbuy_url,
      tcl.price as tcl_price, tcl.compare_at_price as tcl_compare_price, tcl.in_stock as tcl_in_stock,
      amz.price as amazon_price, amz.in_stock as amazon_in_stock,
      bb.price as bestbuy_price, bb.in_stock as bestbuy_in_stock,
      tcl.checked_at as tcl_checked_at,
      amz.checked_at as amazon_checked_at,
      bb.checked_at as bestbuy_checked_at
    FROM skus s
    LEFT JOIN (
      SELECT sku_id, price, compare_at_price, in_stock, checked_at,
             ROW_NUMBER() OVER (PARTITION BY sku_id ORDER BY checked_at DESC) as rn
      FROM price_history WHERE platform = 'tcl'
    ) tcl ON s.id = tcl.sku_id AND tcl.rn = 1
    LEFT JOIN (
      SELECT sku_id, price, in_stock, checked_at,
             ROW_NUMBER() OVER (PARTITION BY sku_id ORDER BY checked_at DESC) as rn
      FROM price_history WHERE platform = 'amazon'
    ) amz ON s.id = amz.sku_id AND amz.rn = 1
    LEFT JOIN (
      SELECT sku_id, price, in_stock, checked_at,
             ROW_NUMBER() OVER (PARTITION BY sku_id ORDER BY checked_at DESC) as rn
      FROM price_history WHERE platform = 'bestbuy'
    ) bb ON s.id = bb.sku_id AND bb.rn = 1
    WHERE s.active = 1
    ORDER BY s.category, s.model
  `).all();
}

// Helper: get previous day's price for comparison
export function getPreviousPrice(sku_id, platform) {
  return db.prepare(`
    SELECT * FROM price_history
    WHERE sku_id = @sku_id AND platform = @platform
      AND checked_at < date('now')
    ORDER BY checked_at DESC LIMIT 1
  `).get({ sku_id, platform });
}
