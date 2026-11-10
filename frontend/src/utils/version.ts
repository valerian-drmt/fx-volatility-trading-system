/** pyproject's PEP 440 version as SemVer: 1.0.0rc1 -> 1.0.0-rc.1 (#353). */
export function toSemver(version: string): string {
  const m = /^(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?$/.exec(version);
  if (!m) return version;
  const [, base, kind, n] = m;
  if (!kind) return base!;
  return `${base}-${{ a: "alpha", b: "beta", rc: "rc" }[kind as "a" | "b" | "rc"]}.${n}`;
}
