import type { JSX } from "react";
import { fetchHealth } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { toSemver } from "../../utils/version";

/**
 * Release and build the desk is talking to (#353): version and short commit
 * from `/api/v1/health`, the same identity the deploy smoke asserts. One
 * version source (pyproject), nothing baked into the bundle.
 */
export function BuildStamp(): JSX.Element | null {
  const health = useFetch(() => fetchHealth(), 600_000);
  const d = health.data as { version?: string; git_sha?: string } | null;
  if (!d?.version) return null;
  const sha = d.git_sha && d.git_sha !== "unknown" ? d.git_sha.slice(0, 7) : null;
  return (
    <div className="build-stamp mono" data-testid="build-stamp" title={d.git_sha ?? undefined}>
      v{toSemver(d.version)}
      {sha ? ` · ${sha}` : ""}
    </div>
  );
}
