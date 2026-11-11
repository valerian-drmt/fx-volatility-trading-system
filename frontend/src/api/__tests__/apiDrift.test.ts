/// <reference types="node" />
// @vitest-environment node
/**
 * #416: the OpenAPI drift gate catches a renamed response FIELD, now that the
 * read routes declare response models. Runs scripts/check-api-drift.mjs on a
 * copy of the dumped spec with one desk field renamed and expects it to fail.
 * Needs ../openapi.json (the CI frontend job dumps it before the tests);
 * skipped where it was not dumped.
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const root = resolve(__dirname, "../../..");
const spec = resolve(root, "..", "openapi.json");
const check = join(root, "scripts", "check-api-drift.mjs");

describe.skipIf(!existsSync(spec))("OpenAPI drift gate", () => {
  it("fails when a response model renames a field the desk reads", () => {
    const doc = JSON.parse(readFileSync(spec, "utf8"));
    const props = doc.components.schemas.PortfolioEquityCurveRow.properties;
    expect(props).toHaveProperty("net_liq_usd");
    props.net_liq = props.net_liq_usd;
    delete props.net_liq_usd;
    const dir = mkdtempSync(join(tmpdir(), "fxvol-drift-"));
    try {
      const renamed = join(dir, "openapi.json");
      writeFileSync(renamed, JSON.stringify(doc));
      const run = spawnSync(process.execPath, [check, renamed], { cwd: root, encoding: "utf8" });
      expect(run.status).toBe(1);
      expect(run.stderr).toContain("schema.d.ts drifted");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  }, 60_000);
});
