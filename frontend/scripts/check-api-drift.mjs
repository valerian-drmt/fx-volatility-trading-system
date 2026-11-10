// OpenAPI drift check (#348, audit 09#5): regenerate the types from an
// OpenAPI document and compare them with the committed src/api/schema.d.ts.
// Cross-platform (no /tmp, no POSIX diff): runs the same on Windows and CI.
//
//   node scripts/check-api-drift.mjs                  # from the running api (localhost:8000)
//   node scripts/check-api-drift.mjs ../openapi.json  # from a dumped file (what CI does)
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = process.argv[2] ?? "http://localhost:8000/openapi.json";
const dir = mkdtempSync(join(tmpdir(), "fxvol-api-"));
const generated = join(dir, "schema.d.ts");
try {
  const bin = join(root, "node_modules", "openapi-typescript", "bin", "cli.js");
  execFileSync(process.execPath, [bin, source, "-o", generated], { stdio: "inherit", cwd: root });
  const norm = (s) => s.replace(/\r\n/g, "\n");
  const committed = norm(readFileSync(join(root, "src", "api", "schema.d.ts"), "utf8"));
  const fresh = norm(readFileSync(generated, "utf8"));
  if (committed !== fresh) {
    const a = committed.split("\n");
    const b = fresh.split("\n");
    const i = a.findIndex((line, k) => line !== b[k]);
    console.error(`schema.d.ts drifted from ${source} (first difference at line ${i + 1}):`);
    console.error(`  committed: ${a[i] ?? "<end of file>"}`);
    console.error(`  generated: ${b[i] ?? "<end of file>"}`);
    console.error("Regenerate: npm run gen:api (or openapi-typescript <openapi.json> -o src/api/schema.d.ts)");
    process.exit(1);
  }
  console.log("schema.d.ts matches the OpenAPI document");
} finally {
  rmSync(dir, { recursive: true, force: true });
}
