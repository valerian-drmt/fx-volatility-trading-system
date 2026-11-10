// License gate for the frontend's shipped (non-dev) packages (#304, audit 10#11).
// Reads package-lock.json for the production tree and each package's
// package.json for its license; fails on copyleft or missing licenses unless
// the package is in EXCEPTIONS with a reason. No dependency, runs on node 22:
//
//     node scripts/ci/check_licenses_npm.mjs          (from the repo root)
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const ALLOWED = new Set([
  "MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "0BSD", "Unlicense",
  "Zlib", "BlueOak-1.0.0", "CC0-1.0", "Python-2.0", "MPL-2.0", "CC-BY-4.0",
]);
const EXCEPTIONS = {
  // MIT per the LICENSE file in the package; the manifest has no license field.
  "stack-trace": "MIT (LICENSE file shipped, no license field in package.json)",
};

const lock = JSON.parse(readFileSync(join(FRONTEND, "package-lock.json"), "utf8"));
const rows = [];
const violations = [];
for (const [path, info] of Object.entries(lock.packages)) {
  if (!path || info.dev) continue;
  const name = path.slice(path.lastIndexOf("node_modules/") + "node_modules/".length);
  let license = "";
  try {
    const pkg = JSON.parse(readFileSync(join(FRONTEND, path, "package.json"), "utf8"));
    const raw = pkg.license ?? pkg.licenses;
    license = Array.isArray(raw) ? raw.map((l) => l.type ?? l).join(" OR ") : typeof raw === "object" && raw ? raw.type : raw ?? "";
  } catch {
    license = "";
  }
  let verdict;
  if (name in EXCEPTIONS) verdict = `exception (${EXCEPTIONS[name]})`;
  else if (!license) verdict = "UNKNOWN";
  else {
    const parts = license.replace(/[()]/g, "").split(/\s+(?:OR|AND)\s+/);
    verdict = parts.every((p) => ALLOWED.has(p.trim())) ? "ok" : /GPL/i.test(license) ? "COPYLEFT" : "UNLISTED";
  }
  if (verdict === "UNKNOWN" || verdict === "COPYLEFT" || verdict === "UNLISTED") violations.push(`${name}@${info.version}: ${license || "(none)"} [${verdict}]`);
  rows.push([name, info.version, license || "-", verdict]);
}
const width = Math.max(...rows.map((r) => r[0].length));
for (const [n, v, l, d] of rows) console.log(`${n.padEnd(width)}  ${v.padEnd(12)} ${d.padEnd(10)} ${l}`);
console.log(`\n${rows.length} shipped packages, ${violations.length} violation(s)`);
for (const v of violations) console.log("  VIOLATION:", v);
process.exit(violations.length ? 1 : 0);
