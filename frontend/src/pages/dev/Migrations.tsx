/**
 * Migrations — alembic chain inspector.
 *
 * Backend :
 *   GET /api/v1/dev/migrations         → full chain + CURRENT marker
 *   GET /api/v1/dev/migrations/{rev}   → source + upgrade/downgrade bodies
 *
 * Surfaces three things in one panel :
 *   1. The **shape of the chain** — every revision base → head, with
 *      its parent / docstring / Create Date.
 *   2. The **drift** between the deployed code and the DB — every
 *      revision is tagged ``applied`` / ``current`` / ``pending``.
 *      If ``pending`` > 0, the API was started against a DB that
 *      hasn't run all the migrations the codebase declares — i.e.
 *      somebody forgot ``alembic upgrade head``.
 *   3. The **code of each migration** — click a row, see ``def
 *      upgrade()`` / ``def downgrade()`` side by side, plus the full
 *      file content for context.
 *
 * Why this is senior-level : drift between models.py and the DB is
 * caught by the DB Schema tab in DIFF mode ; drift between the alembic
 * chain and the DB is caught here. Together they bracket every way
 * the schema can go out of sync.
 */
import { useEffect, useMemo, useState } from "react";

interface Revision {
  id: string;
  down_revision: string | null;
  filename: string;
  title: string;
  created: string | null;
  parent_title: string | null;
  status: "applied" | "current" | "pending";
}

interface ChainResp {
  chain: Revision[];
  head: string | null;
  current: string | null;
  n_total: number;
  n_applied: number;
  n_pending: number;
  in_sync: boolean;
}

interface RevDetail {
  id: string;
  filename: string;
  content: string;
  upgrade: string;
  downgrade: string;
}


export function Migrations(): JSX.Element {
  const [data, setData] = useState<ChainResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RevDetail | null>(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "pending" | "applied">("all");
  const [toast, setToast] = useState<string | null>(null);
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 1800);
    return () => clearTimeout(id);
  }, [toast]);

  const loadChain = (): void => {
    fetch("/api/v1/dev/migrations")
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status} : ${await r.text()}`);
        return r.json() as Promise<ChainResp>;
      })
      .then((j) => {
        setData(j);
        setError(null);
        // Auto-select the CURRENT one on first load so the right pane
        // isn't blank.
        if (selectedId === null && j.current) setSelectedId(j.current);
        else if (selectedId === null && j.chain.length > 0) {
          setSelectedId(j.chain[j.chain.length - 1]!.id);
        }
      })
      .catch((e) => setError(String(e)));
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(loadChain, []);

  // Load the selected revision's source on demand.
  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    fetch(`/api/v1/dev/migrations/${encodeURIComponent(selectedId)}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status} : ${await r.text()}`);
        return r.json() as Promise<RevDetail>;
      })
      .then(setDetail)
      .catch((e) => setError(String(e)));
  }, [selectedId]);

  const filteredChain = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    return data.chain.filter((r) => {
      if (filter === "pending" && r.status !== "pending") return false;
      if (filter === "applied" && r.status === "pending") return false;
      if (q && !r.id.toLowerCase().includes(q)
            && !r.title.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [data, search, filter]);

  const copyUpgradeCmd = (): void => {
    const cmd = "docker compose exec api alembic -c src/persistence/alembic.ini upgrade head";
    void navigator.clipboard.writeText(cmd).then(
      () => setToast("✓ copied alembic upgrade command"),
      () => setToast("✗ clipboard write failed"),
    );
  };

  if (error) return (
    <div style={{ color: "#fcc", padding: 16 }}>
      ✗ {error}
      <button onClick={loadChain} style={{
        marginLeft: 10, padding: "3px 10px", fontSize: 11,
        background: "#1a2a3a", color: "#9bf",
        border: "1px solid #335", borderRadius: 3, cursor: "pointer",
      }}>retry</button>
    </div>
  );
  if (!data) return <div style={{ color: "#888", padding: 16 }}>loading…</div>;

  return (
    <div style={{ padding: 12, display: "flex", flexDirection: "column",
                   height: "calc(100vh - 130px)" }}>
      {/* ── Status banner ── */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "8px 12px", marginBottom: 8, borderRadius: 4,
        background: data.in_sync ? "#1a3a1a" : "#3a2a1a",
        border: `1px solid ${data.in_sync ? "#2a5a2a" : "#5a4a2a"}`,
        fontFamily: "Consolas, monospace", fontSize: 12,
      }}>
        <span style={{
          fontSize: 16, fontWeight: 700,
          color: data.in_sync ? "#bfb" : "#fc6",
        }}>
          {data.in_sync ? "✓ in sync" : `⚠ ${data.n_pending} pending`}
        </span>
        <span style={{ color: "#aaa" }}>
          DB at <b style={{ color: "#9bf" }}>{data.current ?? "(empty)"}</b>
          {" · code head "}<b style={{ color: "#9bf" }}>{data.head ?? "—"}</b>
          {" · "}<b style={{ color: "#bfb" }}>{data.n_applied}</b>/{data.n_total} applied
        </span>
        {!data.in_sync && (
          <button onClick={copyUpgradeCmd} style={{
            marginLeft: "auto", padding: "4px 10px", fontSize: 11,
            background: "#5a4a2a", color: "#fc6",
            border: "1px solid #6a5a3a", borderRadius: 3, cursor: "pointer",
            fontFamily: "Consolas, monospace",
          }}>
            ⧉ copy upgrade command
          </button>
        )}
        <button onClick={loadChain} style={{
          padding: "4px 10px", fontSize: 11,
          background: "transparent", color: "#9bf",
          border: "1px solid #335", borderRadius: 3, cursor: "pointer",
          fontFamily: "Consolas, monospace",
          marginLeft: data.in_sync ? "auto" : 0,
        }}>↻ refresh</button>
      </div>

      {/* ── Filter row ── */}
      <div style={{
        display: "flex", gap: 8, marginBottom: 8, alignItems: "center",
        color: "#aaa", fontSize: 12,
      }}>
        <input type="text" value={search}
               onChange={(e) => setSearch(e.target.value)}
               placeholder="search revision / title…"
               style={inputStyle("260px")} />
        <span>show
          {(["all", "applied", "pending"] as const).map((f) => (
            <button key={f} type="button"
                    onClick={() => setFilter(f)}
                    style={{
                      marginLeft: 4, padding: "2px 8px", fontSize: 11,
                      background: f === filter ? "#2a4a6a" : "transparent",
                      color: f === filter ? "#fff" : "#aaa",
                      border: "1px solid #333", borderRadius: 3, cursor: "pointer",
                      fontFamily: "Consolas, monospace",
                    }}>{f}</button>
          ))}
        </span>
        <span style={{ marginLeft: "auto", color: "#666" }}>
          {filteredChain.length}/{data.chain.length} revisions
        </span>
      </div>

      {/* ── Body : 2 columns (chain + detail) ── */}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(380px, 1fr) 2fr",
                     gap: 10, flex: 1, minHeight: 0 }}>
        {/* Chain list */}
        <div style={{
          overflow: "auto",
          background: "#0a0a0a", border: "1px solid #222", borderRadius: 4,
        }}>
          {/* Render head-first (most recent on top). */}
          {[...filteredChain].reverse().map((r) => (
            <RevisionRow key={r.id} rev={r}
                         selected={r.id === selectedId}
                         onClick={() => setSelectedId(r.id)} />
          ))}
          {filteredChain.length === 0 && (
            <div style={{ padding: 16, color: "#666", textAlign: "center",
                          fontSize: 12 }}>
              no revisions match
            </div>
          )}
        </div>

        {/* Detail panel */}
        <div style={{
          overflow: "auto",
          background: "#0a0a0a", border: "1px solid #222", borderRadius: 4,
        }}>
          {detail ? <Detail rev={detail} /> : (
            <div style={{ padding: 16, color: "#666", fontSize: 12 }}>
              pick a revision on the left
            </div>
          )}
        </div>
      </div>

      {toast && (
        <div style={{
          position: "fixed", bottom: 24, right: 24,
          padding: "10px 16px", borderRadius: 4,
          background: toast.startsWith("✓") ? "#1a3a1a" : "#3a1a1a",
          color: toast.startsWith("✓") ? "#bfb" : "#fbb",
          border: `1px solid ${toast.startsWith("✓") ? "#2a5a2a" : "#5a2a2a"}`,
          fontSize: 12, fontFamily: "Consolas, monospace",
          zIndex: 1000, pointerEvents: "none",
          boxShadow: "0 4px 12px rgba(0,0,0,0.5)",
        }}>{toast}</div>
      )}
    </div>
  );
}


function RevisionRow({
  rev, selected, onClick,
}: {
  rev: Revision;
  selected: boolean;
  onClick: () => void;
}): JSX.Element {
  const statusStyle = {
    applied: { color: "#bfb", bg: "#1a3a1a", label: "APPLIED" },
    current: { color: "#fc6", bg: "#3a2a1a", label: "● CURRENT" },
    pending: { color: "#fbb", bg: "#3a1a1a", label: "PENDING" },
  }[rev.status];
  return (
    <div onClick={onClick}
         style={{
           padding: "8px 10px",
           borderBottom: "1px solid #1a1a1a",
           cursor: "pointer",
           background: selected ? "#1a2a3a" : "transparent",
           borderLeft: selected ? "3px solid #7af" : "3px solid transparent",
         }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center",
                    fontSize: 11, fontFamily: "Consolas, monospace" }}>
        <span style={{
          padding: "1px 7px", borderRadius: 2,
          background: statusStyle.bg, color: statusStyle.color,
          fontWeight: 700, fontSize: 9.5,
        }}>{statusStyle.label}</span>
        <span style={{ color: "#7af" }}>{shortRev(rev.id)}</span>
        {rev.created && (
          <span style={{ color: "#666", marginLeft: "auto" }}>
            {rev.created.slice(0, 10)}
          </span>
        )}
      </div>
      <div style={{
        marginTop: 3, fontSize: 12, color: "#ddd",
        fontFamily: "Consolas, monospace",
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>
        {rev.title || rev.filename}
      </div>
      <div style={{
        marginTop: 2, fontSize: 10, color: "#666",
        fontFamily: "Consolas, monospace",
      }}>
        ← {rev.down_revision ? shortRev(rev.down_revision) : "(base)"}
        {" · "}{rev.filename}
      </div>
    </div>
  );
}


function Detail({ rev }: { rev: RevDetail }): JSX.Element {
  return (
    <div style={{ padding: 12 }}>
      <div style={{
        display: "flex", gap: 8, alignItems: "baseline", marginBottom: 6,
        fontFamily: "Consolas, monospace",
      }}>
        <span style={{ color: "#7af", fontSize: 13, fontWeight: 700 }}>
          {rev.id}
        </span>
        <span style={{ color: "#666", fontSize: 11 }}>{rev.filename}</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr",
                     gap: 8, marginBottom: 12 }}>
        <CodeBlock title="def upgrade()" color="#bfb" code={rev.upgrade} />
        <CodeBlock title="def downgrade()" color="#fbb" code={rev.downgrade} />
      </div>
      <CodeBlock title={`full source — ${rev.filename}`} color="#9bf"
                 code={rev.content} maxHeight={420} />
    </div>
  );
}


function CodeBlock({
  title, code, color, maxHeight = 260,
}: {
  title: string; code: string; color: string; maxHeight?: number;
}): JSX.Element {
  return (
    <div>
      <div style={{
        padding: "2px 8px",
        background: "#1a1a1a", borderTop: `1px solid ${color}`,
        color, fontSize: 10, fontFamily: "Consolas, monospace",
      }}>
        {title}
      </div>
      <pre style={{
        margin: 0, padding: 10, background: "#000", color: "#cdc",
        fontSize: 11, fontFamily: "Consolas, monospace",
        overflow: "auto", maxHeight, whiteSpace: "pre",
        border: "1px solid #222", borderTop: "none",
      }}>{code || <span style={{ color: "#555" }}>(empty)</span>}</pre>
    </div>
  );
}


function shortRev(rev: string): string {
  // Trim ``001_initial_schema`` → ``001_initial``.
  return rev.length > 22 ? rev.slice(0, 22) + "…" : rev;
}


function inputStyle(width: string): React.CSSProperties {
  return {
    background: "#1a1a1a", color: "#ddd",
    border: "1px solid #333", borderRadius: 3,
    padding: "3px 8px", fontSize: 12, width,
    fontFamily: "Consolas, monospace",
  };
}
