// Reshoot the README screenshots from the public desk (#350, audit 11#5).
// Read-only: the public site serves reads without auth, nothing is clicked
// that writes. Re-run after a visible change and commit the PNGs.
//
//   node scripts/screenshots.mjs                                  # prod
//   node scripts/screenshots.mjs http://localhost/fx-volatility-trading-system/
import { chromium } from "@playwright/test";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const BASE = process.argv[2] ?? "https://valeriandarmente.dev/fx-volatility-trading-system/";
const OUT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "docs", "pictures");
const SETTLE_MS = 12_000; // first fetch round + one WS beat + the 1 s ticker smoothing

const SHOTS = [
  { file: "dashboard.png", hash: "dashboard" },
  { file: "vol-surface.png", hash: "signals" },
  { file: "risk.png", hash: "risk" },
  { file: "positions.png", hash: "trade", panel: "trade-open" },
  { file: "portfolio-performance.png", hash: "portfolio", panel: "perf" },
];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 2, colorScheme: "dark" });
for (const s of SHOTS) {
  await page.goto(`${BASE}#${s.hash}`, { waitUntil: "networkidle" }).catch(() => undefined);
  await page.waitForTimeout(SETTLE_MS);
  const path = resolve(OUT, s.file);
  if (s.panel) {
    await page.locator(`[data-pp="${s.panel}"]`).first().screenshot({ path });
  } else {
    await page.screenshot({ path, fullPage: false });
  }
  console.log(`shot ${s.file}`);
}
await browser.close();
