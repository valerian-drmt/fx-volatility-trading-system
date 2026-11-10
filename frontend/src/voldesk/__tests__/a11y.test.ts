import { describe, expect, it } from "vitest";

import css from "../voldesk.css?raw";

// #349 (audit 09#13): the faint text token stays readable on every panel
// background (WCAG AA 4.5:1 for small text), and keyboard focus is visible.

function token(name: string): string {
  const m = css.match(new RegExp(`--${name}:(#[0-9a-fA-F]{6})`));
  if (!m) throw new Error(`token --${name} not found`);
  return m[1]!;
}

function luminance(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const lin = c.map((x) => (x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4));
  return 0.2126 * lin[0]! + 0.7152 * lin[1]! + 0.0722 * lin[2]!;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

describe("accessibility tokens", () => {
  it.each(["bg", "bg-2", "surface", "bg-3"])("--text-faint reaches 4.5:1 on --%s", (bg) => {
    expect(contrast(token("text-faint"), token(bg))).toBeGreaterThanOrEqual(4.5);
  });

  it("keyboard focus draws a ring on interactive elements", () => {
    expect(css).toMatch(/:focus-visible\{outline:2px solid var\(--accent\)/);
  });
});
