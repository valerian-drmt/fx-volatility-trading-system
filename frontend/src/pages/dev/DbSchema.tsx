/**
 * DB Schema dev tab — interactive ER-diagram laid out by dagre.
 *
 * Source : /api/v1/dev/db-schema (introspects Base.metadata at import
 * time). Add a class to persistence/models.py → it shows up on the
 * next page open without any front-end change.
 *
 * Modern ER conventions :
 *   - **Crow's foot** at every line endpoint (replaces the legacy
 *     Chen-style diamond label). A "many" end has the 3-prong crow
 *     foot ; a "one" end has the single vertical bar ; an *optional*
 *     end adds a small circle inboard of the bar/foot.
 *   - Column **PostgreSQL types** (``INTEGER``, ``VARCHAR(n)``,
 *     ``TIMESTAMP WITH TIME ZONE``), not the SQLAlchemy Python repr.
 *   - **PK / FK / UNIQUE / INDEX** badges on every column, so a DBA
 *     can read query-side at a glance without a separate schema doc.
 *
 * Interactions :
 *   - **click** a table card → highlight it + every directly-related
 *     edge + every directly-related table. Everything else dims to
 *     0.12 so the local subgraph reads in isolation.
 *   - **search** at the top filters tables (substring match on
 *     table name) ; FKs whose both ends are visible stay drawn.
 *   - **drag** empty canvas → pan.  **wheel** → zoom around cursor.
 *     **double-click** → reset pan + zoom + selection.
 *   - **TB / LR toggle** swaps dagre rank direction.
 *
 * Layout : dagre's hierarchical algorithm with proper non-overlap
 * constraints (nodes sized to actual card dimensions, edges given a
 * label box so the crow's-foot anchors land in clean gaps).
 */
import dagre from "@dagrejs/dagre";
import { apiFetch } from "../../api/client";
import { useEffect, useMemo, useRef, useState } from "react";

interface Column {
  name: string;
  type: string;             // already a SQL type from the backend
  nullable: boolean;
  pk: boolean;
  fk: boolean;              // simple bool now ; FK target is on Relationship
  unique: boolean;
  indexed: boolean;
  default: string | null;
  comment: string | null;
}
interface CheckConstraint {
  name: string;
  sql: string;
}
interface CompositeUnique {
  name: string;
  columns: string[];
}
interface Table {
  name: string;
  columns: Column[];
  n_columns: number;
  comment: string | null;
  check_constraints: CheckConstraint[];
  composite_unique: CompositeUnique[];
}
interface Relationship {
  from_table: string;
  to_table: string;
  from_columns: string[];
  to_columns: string[];
  label: string;
  cardinality: "1:1" | "N:1" | "M:N";
  optional: boolean;
  composite: boolean;
  self_loop: boolean;
  on_delete: string;
  on_update: string;
}
interface Schema {
  tables: Table[];
  relationships: Relationship[];
  n_tables: number;
  n_relationships: number;
}

interface DagreEdge {
  x: number; y: number;
  width: number; height: number;
  points: Array<{ x: number; y: number }>;
  meta: Relationship;
}

// ── Card layout constants ───────────────────────────────────────────
const COL_W = 300;             // wider — has to fit PK FK UQ IX badges + type
const HEADER_H = 30;
const ROW_H = 19;

// ── Visual encoding ────────────────────────────────────────────────
const ON_DELETE_COLOR: Record<string, string> = {
  "CASCADE":  "#e66",
  "SET NULL": "#ec6",
  "RESTRICT": "#6c6",
};
function colorFor(on_delete: string): string {
  return ON_DELETE_COLOR[on_delete] ?? "#888";
}

// Extra height per CHECK / composite-UQ row at the bottom of the card.
const FOOTER_ROW_H = 14;
const FOOTER_TOP_PAD = 6;

function cardHeight(t: Table): number {
  const cols = HEADER_H + t.n_columns * ROW_H + 12;
  const footer = t.check_constraints.length + t.composite_unique.length;
  return footer > 0
    ? cols + FOOTER_TOP_PAD + footer * FOOTER_ROW_H + 6
    : cols;
}


// ── Domain grouping ────────────────────────────────────────────────
// Tables are bucketed into bounded contexts so dagre can lay each
// bucket out as an independent subgraph (compound layout). This is
// the single most effective fix for the "many tables stacked
// vertically in the same rank" symptom — splitting a 25-table flat
// graph into 5 sub-graphs of 4-6 tables each gives every sub-graph
// short ranks and a healthy aspect ratio.
//
// Domain is detected from the table name prefix ; this avoids a
// hand-maintained allow-list and survives table renames as long as
// the prefix stays consistent. New tables that don't match any
// prefix land in the "other" bucket.
const DOMAINS: Array<{
  key: string;
  label: string;
  color: string;
  match: (name: string) => boolean;
}> = [
  {
    key: "trading", label: "Trading",  color: "#7af",
    match: (n) => /^(open_position|booked_position|account_history|book_state|trade_|hedge_|exit_(?!rules_)|structure_|package)/.test(n),
  },
  {
    key: "vol",     label: "Volatility", color: "#ec6",
    match: (n) => /^(vol_)/.test(n),
  },
  {
    key: "pca",     label: "PCA",       color: "#c8f",
    match: (n) => /^pca_/.test(n),
  },
  {
    key: "regime",  label: "Regime",    color: "#6c6",
    match: (n) => /^(regime_|feature_|event_)/.test(n),
  },
  {
    key: "config",  label: "Config",    color: "#fc6",
    match: (n) => /^config_/.test(n),
  },
  {
    key: "runtime", label: "Runtime",   color: "#f9a",
    match: (n) => /^runtime_/.test(n),
  },
];
const DEFAULT_DOMAIN = { key: "other", label: "Other", color: "#888" };

function domainOf(name: string): typeof DOMAINS[number] | typeof DEFAULT_DOMAIN {
  for (const d of DOMAINS) {
    if (d.match(name)) return d;
  }
  return DEFAULT_DOMAIN;
}


/** Canonical form for a SQL type string so equivalent types from ORM
 *  vs LIVE compare equal.
 *
 *  PostgreSQL has many aliases — ``INT`` / ``INTEGER`` / ``INT4`` ;
 *  ``BOOL`` / ``BOOLEAN`` ; ``FLOAT`` / ``DOUBLE PRECISION`` / ``FLOAT8`` ;
 *  ``TIMESTAMP WITH TIME ZONE`` / ``TIMESTAMPTZ``. SQLAlchemy emits one
 *  spelling on the ORM side via ``compile(postgresql.dialect())`` and
 *  a *different* spelling on the LIVE side because the inspector
 *  reads the catalog and re-stringifies via the type's repr. Without
 *  normalising, every column shows up as drifted.
 *
 *  This collapses both to a canonical form. If you want to debug
 *  what's matching what, look at the ``~col type`` lines in the drift
 *  tooltip — they show the *raw* (non-normalised) values. */
function normalizeType(s: string): string {
  if (!s) return "";
  let t = s.toUpperCase().replace(/\s+/g, " ").trim();

  // Strip explicit timezone qualifier suffixes first (so the synonym
  // step below can replace TIMESTAMPTZ uniformly).
  t = t.replace(/TIMESTAMP WITH TIME ZONE/g, "TIMESTAMPTZ");
  t = t.replace(/TIMESTAMP WITHOUT TIME ZONE/g, "TIMESTAMP");
  t = t.replace(/TIME WITH TIME ZONE/g, "TIMETZ");
  t = t.replace(/TIME WITHOUT TIME ZONE/g, "TIME");

  // Integer family.
  t = t.replace(/\bINT4\b/g, "INTEGER")
       .replace(/\bINT8\b/g, "BIGINT")
       .replace(/\bINT2\b/g, "SMALLINT")
       .replace(/\bINT\b/g, "INTEGER")
       .replace(/\bSERIAL4\b/g, "INTEGER")
       .replace(/\bSERIAL8\b/g, "BIGINT")
       .replace(/\bSERIAL\b/g, "INTEGER")
       .replace(/\bBIGSERIAL\b/g, "BIGINT");

  // Float family. SQLAlchemy's ``Float()`` compiles to ``FLOAT`` but
  // PG stores it as ``DOUBLE PRECISION`` ; treat them as equal.
  t = t.replace(/\bFLOAT8\b/g, "DOUBLE PRECISION")
       .replace(/\bFLOAT4\b/g, "REAL")
       .replace(/\bDOUBLE_PRECISION\b/g, "DOUBLE PRECISION")
       .replace(/\bFLOAT\b(?!\s*\()/g, "DOUBLE PRECISION");
  // REAL is single-precision float ; keep distinct from DOUBLE PRECISION.

  // Boolean.
  t = t.replace(/\bBOOL\b/g, "BOOLEAN");

  // Character family. ``CHARACTER VARYING(n)`` ⇄ ``VARCHAR(n)``,
  // ``CHARACTER(n)`` ⇄ ``CHAR(n)``.
  t = t.replace(/\bCHARACTER VARYING\b/g, "VARCHAR")
       .replace(/\bCHARACTER\b(?!\s+VARYING)/g, "CHAR");

  // VARCHAR with no length spec: SQLAlchemy ``String()`` may emit
  // ``VARCHAR`` while PG stores as ``TEXT``-equivalent — treat as
  // ``VARCHAR``.

  // Numeric/Decimal.
  t = t.replace(/\bDECIMAL\b/g, "NUMERIC");

  // UUID, JSON, JSONB pass through unchanged.

  // Normalise whitespace inside parens : ``NUMERIC(20, 6)`` → ``NUMERIC(20,6)``.
  t = t.replace(/\(\s+/g, "(").replace(/\s+\)/g, ")").replace(/,\s+/g, ",");

  return t.trim();
}


/** Per-table drift verdict computed by the front-end when DIFF mode
 *  is active. ``orm-only`` / ``live-only`` mean the table is absent
 *  on the other side ; ``diff`` means it exists in both but their
 *  shapes differ ; ``same`` means structurally identical. */
type DriftStatus = "same" | "orm-only" | "live-only" | "diff";

interface Drift {
  status: DriftStatus;
  reasons: string[];      // human-readable bullets : "+col foo", "-col bar"
  sqlFixes: string[];     // ALTER statements that would close the drift
                          //   (applied to LIVE so it matches ORM)
}


export function DbSchema(): JSX.Element {
  const [ormSchema, setOrmSchema] = useState<Schema | null>(null);
  const [liveSchema, setLiveSchema] = useState<Schema | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rankdir, setRankdir] = useState<"TB" | "LR">("LR");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [source, setSource] = useState<"orm" | "live" | "diff">("orm");
  // Toast for the "copy SQL fix" affordance. Disappears after 2.2 s.
  const [toast, setToast] = useState<string | null>(null);
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 2200);
    return () => clearTimeout(id);
  }, [toast]);

  const copyDriftSql = (tableName: string, drift: Drift): void => {
    if (drift.sqlFixes.length === 0) return;
    const header = [
      `-- Schema drift fix for table '${tableName}'`,
      `-- Generated by DB Schema dev tab — review before applying !`,
      `-- These statements bring the LIVE DB in line with models.py.`,
      `-- The correct way is usually to add them to an alembic migration.`,
      "",
    ];
    const body = [...header, ...drift.sqlFixes].join("\n");
    void navigator.clipboard.writeText(body).then(
      () => setToast(`✓ Copied ${drift.sqlFixes.filter((s) => !s.startsWith("--")).length} statement(s) for ${tableName}`),
      () => setToast("✗ Clipboard write failed (need https or user gesture)"),
    );
  };

  // Pan + zoom.
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  const [scale, setScale] = useState(1);
  const dragRef = useRef<{
    startX: number; startY: number;
    origTx: number; origTy: number;
    moved: boolean;
  } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  // Load whichever source(s) the current mode needs. DIFF always
  // needs both. ORM/LIVE only need their own — but we cache both so
  // switching modes doesn't refetch.
  useEffect(() => {
    const load = async () => {
      try {
        const need: Array<"orm" | "live"> = source === "diff"
          ? ["orm", "live"]
          : [source];
        const fetches = need.map(async (s) => {
          if (s === "orm" && ormSchema) return;
          if (s === "live" && liveSchema) return;
          const r = await apiFetch(`/api/v1/dev/db-schema?source=${s}`);
          if (!r.ok) throw new Error(`HTTP ${r.status} on source=${s}`);
          const j: Schema = await r.json();
          if (s === "orm") setOrmSchema(j);
          else setLiveSchema(j);
        });
        await Promise.all(fetches);
        setError(null);
      } catch (e) { setError(String(e)); }
    };
    void load();
    // Intentionally only on `source` — refetching when ormSchema/liveSchema
    // change would re-enter immediately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  // The "currently displayed" schema depends on mode :
  //   ORM   → ormSchema
  //   LIVE  → liveSchema
  //   DIFF  → unionMerged(orm, live) so every table from either side
  //           shows up, with a drift verdict attached.
  const { schema, driftMap, driftStats } = useMemo(() => {
    if (source === "orm") {
      return { schema: ormSchema, driftMap: new Map<string, Drift>(), driftStats: null };
    }
    if (source === "live") {
      return { schema: liveSchema, driftMap: new Map<string, Drift>(), driftStats: null };
    }
    // DIFF — need both.
    if (!ormSchema || !liveSchema) {
      return { schema: null, driftMap: new Map<string, Drift>(), driftStats: null };
    }
    const ormByName = new Map(ormSchema.tables.map((t) => [t.name, t]));
    const liveByName = new Map(liveSchema.tables.map((t) => [t.name, t]));
    const allNames = new Set([
      ...ormByName.keys(),
      ...liveByName.keys(),
    ]);
    const mergedTables: Table[] = [];
    const drift = new Map<string, Drift>();
    let nSame = 0, nOrmOnly = 0, nLiveOnly = 0, nDiff = 0;
    for (const name of Array.from(allNames).sort()) {
      const o = ormByName.get(name);
      const l = liveByName.get(name);
      if (o && !l) {
        mergedTables.push(o);
        drift.set(name, {
          status: "orm-only",
          reasons: ["missing in live DB — generate & apply an alembic migration"],
          sqlFixes: [
            `-- '${name}' is declared in models.py but not in the DB.`,
            `-- Fix : run \`alembic revision --autogenerate -m "add ${name}"\``,
            `-- then \`alembic upgrade head\`.`,
          ],
        });
        nOrmOnly++;
      } else if (l && !o) {
        mergedTables.push(l);
        drift.set(name, {
          status: "live-only",
          reasons: ["missing in ORM models — add a class or drop the table"],
          sqlFixes: [
            `-- '${name}' exists in the DB but not in models.py.`,
            `-- Either reflect it as an ORM class, OR drop it :`,
            `DROP TABLE ${name};  -- ⚠ destructive, data is lost`,
          ],
        });
        nLiveOnly++;
      } else if (o && l) {
        // Compare columns. Strategy : only flag REAL structural drift.
        // Cosmetic differences (PG type aliases, default representation,
        // nullable on PK which is implicitly NOT NULL anyway) are NOT
        // flagged — they create noise and DBAs ignore them.
        const oc = new Map(o.columns.map((c) => [c.name, c]));
        const lc = new Map(l.columns.map((c) => [c.name, c]));
        const reasons: string[] = [];
        const sqlFixes: string[] = [];

        for (const [cn, ocol] of oc) {
          if (!lc.has(cn)) {
            reasons.push(`+${cn} (ORM only)`);
            // ORM has it, LIVE doesn't → ADD COLUMN on LIVE.
            const nullClause = ocol.nullable ? "" : " NOT NULL";
            const defClause = ocol.default ? ` DEFAULT ${ocol.default}` : "";
            sqlFixes.push(
              `ALTER TABLE ${name} ADD COLUMN ${cn} ${ocol.type}${defClause}${nullClause};`,
            );
          }
        }
        for (const [cn] of lc) {
          if (!oc.has(cn)) {
            reasons.push(`-${cn} (LIVE only)`);
            // LIVE has it, ORM doesn't → DROP COLUMN on LIVE (or add
            // to the model — we surface both options as a comment).
            sqlFixes.push(
              `-- '${cn}' exists in DB but not in models.py — either add it to`,
              `-- the ORM class, OR drop it from the table :`,
              `ALTER TABLE ${name} DROP COLUMN ${cn};  -- ⚠ destructive`,
            );
          }
        }
        for (const [cn, ocol] of oc) {
          const lcol = lc.get(cn);
          if (!lcol) continue;
          if (!ocol.pk && !lcol.pk && ocol.nullable !== lcol.nullable) {
            reasons.push(
              `~${cn} nullable ORM=${ocol.nullable} LIVE=${lcol.nullable}`,
            );
            if (ocol.nullable && !lcol.nullable) {
              sqlFixes.push(
                `ALTER TABLE ${name} ALTER COLUMN ${cn} DROP NOT NULL;`,
              );
            } else {
              sqlFixes.push(
                `-- Ensure no NULL rows exist first :`,
                `-- SELECT COUNT(*) FROM ${name} WHERE ${cn} IS NULL;`,
                `ALTER TABLE ${name} ALTER COLUMN ${cn} SET NOT NULL;`,
              );
            }
          }
          if (ocol.pk !== lcol.pk) {
            reasons.push(`~${cn} PK differs`);
            sqlFixes.push(
              `-- PK shape differs on '${cn}' — review the constraint manually,`,
              `-- a PK change usually means recreating the constraint from scratch.`,
            );
          }
          if (normalizeType(ocol.type) !== normalizeType(lcol.type)) {
            reasons.push(
              `~${cn} type ORM=${ocol.type} LIVE=${lcol.type}`,
            );
            sqlFixes.push(
              `ALTER TABLE ${name} ALTER COLUMN ${cn} TYPE ${ocol.type} USING ${cn}::${ocol.type};`,
            );
          }
        }

        mergedTables.push(o);
        if (reasons.length === 0) {
          drift.set(name, { status: "same", reasons: [], sqlFixes: [] });
          nSame++;
        } else {
          drift.set(name, { status: "diff", reasons, sqlFixes });
          nDiff++;
        }
      }
    }
    // Relationships : union by (from, to, columns).
    const relKey = (r: Relationship) =>
      `${r.from_table}→${r.to_table}:${r.from_columns.join(",")}`;
    const relMap = new Map<string, Relationship>();
    for (const r of ormSchema.relationships) relMap.set(relKey(r), r);
    for (const r of liveSchema.relationships) {
      if (!relMap.has(relKey(r))) relMap.set(relKey(r), r);
    }
    return {
      schema: {
        tables: mergedTables,
        relationships: Array.from(relMap.values()),
        n_tables: mergedTables.length,
        n_relationships: relMap.size,
      } as Schema,
      driftMap: drift,
      driftStats: { nSame, nOrmOnly, nLiveOnly, nDiff },
    };
  }, [source, ormSchema, liveSchema]);

  // ── Filter by search ──
  const filtered = useMemo(() => {
    if (!schema) return null;
    const q = search.trim().toLowerCase();
    if (!q) return schema;
    const matchingTables = schema.tables.filter((t) =>
      t.name.toLowerCase().includes(q),
    );
    const matchingSet = new Set(matchingTables.map((t) => t.name));
    const filteredRels = schema.relationships.filter((r) =>
      matchingSet.has(r.from_table) && matchingSet.has(r.to_table),
    );
    return {
      ...schema,
      tables: matchingTables,
      relationships: filteredRels,
    };
  }, [schema, search]);

  // ── Adjacency map : table → set of tables connected by any FK ──
  const adjacency = useMemo(() => {
    const m = new Map<string, Set<string>>();
    if (!schema) return m;
    for (const t of schema.tables) m.set(t.name, new Set());
    for (const r of schema.relationships) {
      m.get(r.from_table)?.add(r.to_table);
      m.get(r.to_table)?.add(r.from_table);
    }
    return m;
  }, [schema]);

  // ── dagre layout (compound, grouped by domain) ──
  const layout = useMemo(() => {
    if (!filtered) return null;
    const g = new dagre.graphlib.Graph({ multigraph: false, compound: true });
    g.setGraph({
      rankdir,
      nodesep: 70,
      ranksep: 130,
      edgesep: 24,
      marginx: 40,
      marginy: 40,
      ranker: "network-simplex",
    });
    g.setDefaultEdgeLabel(() => ({}));

    // Find domains present in the filtered set so we don't emit empty
    // cluster boxes.
    const tablesByDomain = new Map<string, Table[]>();
    for (const t of filtered.tables) {
      const d = domainOf(t.name);
      const arr = tablesByDomain.get(d.key) ?? [];
      arr.push(t);
      tablesByDomain.set(d.key, arr);
    }

    // Cluster nodes. dagre treats anything with ``id.startsWith("cluster")``
    // as a subgraph container ; we use the literal "cluster_<key>" naming.
    // The cluster's size is derived after layout from the bounding box of
    // its children — we just give dagre a placeholder so the parent
    // relation is recorded.
    tablesByDomain.forEach((_, domainKey) => {
      g.setNode(`cluster_${domainKey}`, {
        label: domainKey, clusterLabelPos: "top",
      });
    });

    const heights = new Map<string, number>();
    for (const t of filtered.tables) {
      const h = cardHeight(t);
      heights.set(t.name, h);
      g.setNode(t.name, { width: COL_W, height: h });
      const d = domainOf(t.name);
      g.setParent(t.name, `cluster_${d.key}`);
    }
    // Self-loops are *not* given to dagre — it doesn't lay them out
    // cleanly and a self-edge confuses ranking. We handle them
    // separately as arcs glued to the right side of the table card.
    const selfLoops = filtered.relationships.filter((r) => r.self_loop);
    for (const r of filtered.relationships) {
      if (r.self_loop) continue;
      if (!heights.has(r.from_table) || !heights.has(r.to_table)) continue;
      // Reserve enough room for the rendered edge label : the column
      // name (or "(c1, c2)") fits in label.length*5.5 px width.
      const labelW = Math.max(28, r.label.length * 5.5 + 14);
      g.setEdge(r.from_table, r.to_table, {
        width: labelW, height: 16, labelpos: "c", meta: r,
      });
    }
    dagre.layout(g);

    // Read clusters back (dagre assigns them x/y/width/height after layout).
    const clusters: Array<{
      key: string; label: string; color: string;
      x: number; y: number; w: number; h: number;
    }> = [];
    tablesByDomain.forEach((_, domainKey) => {
      const n = g.node(`cluster_${domainKey}`) as
        unknown as { x: number; y: number; width: number; height: number };
      if (!n || n.width == null) return;
      const d = DOMAINS.find((dom) => dom.key === domainKey) ?? DEFAULT_DOMAIN;
      clusters.push({
        key: domainKey, label: d.label, color: d.color,
        x: n.x - n.width / 2, y: n.y - n.height / 2,
        w: n.width, h: n.height,
      });
    });

    return { g, heights, clusters, selfLoops };
  }, [filtered, rankdir]);

  // ── Pointer handlers ──
  const onMouseDown = (e: React.MouseEvent): void => {
    dragRef.current = {
      startX: e.clientX, startY: e.clientY,
      origTx: tx, origTy: ty, moved: false,
    };
  };
  const onMouseMove = (e: React.MouseEvent): void => {
    if (!dragRef.current) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) dragRef.current.moved = true;
    setTx(dragRef.current.origTx + dx);
    setTy(dragRef.current.origTy + dy);
  };
  const onMouseUp = (): void => {
    // Click on empty canvas (no drag) → clear selection.
    if (dragRef.current && !dragRef.current.moved) setSelected(null);
    dragRef.current = null;
  };
  const onWheel = (e: React.WheelEvent): void => {
    e.preventDefault();
    const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const cx = (px - tx) / scale;
    const cy = (py - ty) / scale;
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const next = Math.max(0.15, Math.min(3, scale * factor));
    setScale(next);
    setTx(px - cx * next);
    setTy(py - cy * next);
  };
  const onDoubleClick = (): void => {
    setTx(0); setTy(0); setScale(1); setSelected(null);
  };

  // ── PNG export ──
  // Serialize the SVG (with the full dagre-laid-out viewBox so the
  // export captures the whole graph, not just what's currently
  // visible after pan/zoom), rasterize via Canvas, trigger download.
  const exportPng = (): void => {
    if (!svgRef.current || !layout) return;
    const fullW = (graphBBox.width ?? 0) + 80;
    const fullH = (graphBBox.height ?? 0) + 80;

    // Clone the SVG and force it to the full graph bbox (no pan/zoom
    // transform on the inner <g>) so the exported image captures
    // everything dagre laid out, regardless of current viewport.
    const clone = svgRef.current.cloneNode(true) as SVGSVGElement;
    clone.setAttribute("width", String(fullW));
    clone.setAttribute("height", String(fullH));
    clone.setAttribute("viewBox", `0 0 ${fullW} ${fullH}`);
    const innerG = clone.querySelector("g[data-export-root]");
    if (innerG) innerG.setAttribute("transform", "translate(40, 40) scale(1)");

    const svgString = new XMLSerializer().serializeToString(clone);
    const blob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);

    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = fullW;
      canvas.height = fullH;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.fillStyle = "#0a0a0a";
      ctx.fillRect(0, 0, fullW, fullH);
      ctx.drawImage(img, 0, 0);
      URL.revokeObjectURL(url);
      canvas.toBlob((pngBlob) => {
        if (!pngBlob) return;
        const dlUrl = URL.createObjectURL(pngBlob);
        const a = document.createElement("a");
        a.href = dlUrl;
        a.download = `db-schema-${rankdir}.png`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(dlUrl);
      }, "image/png");
    };
    img.onerror = () => { URL.revokeObjectURL(url); };
    img.src = url;
  };

  // ── SVG export ──
  // Same idea as PNG but skips the canvas rasterization step — the
  // SVG is itself the deliverable. Vector → scales infinitely, good
  // for embedding in slides / PDFs.
  const exportSvg = (): void => {
    if (!svgRef.current || !layout) return;
    const fullW = (graphBBox.width ?? 0) + 80;
    const fullH = (graphBBox.height ?? 0) + 80;
    const clone = svgRef.current.cloneNode(true) as SVGSVGElement;
    clone.setAttribute("width", String(fullW));
    clone.setAttribute("height", String(fullH));
    clone.setAttribute("viewBox", `0 0 ${fullW} ${fullH}`);
    const innerG = clone.querySelector("g[data-export-root]");
    if (innerG) innerG.setAttribute("transform", "translate(40, 40) scale(1)");
    // Inject a dark background rect so the SVG looks the same outside
    // the dev tab (without our CSS).
    const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    bg.setAttribute("width", String(fullW));
    bg.setAttribute("height", String(fullH));
    bg.setAttribute("fill", "#0a0a0a");
    clone.insertBefore(bg, clone.firstChild);
    const svgString = new XMLSerializer().serializeToString(clone);
    const blob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `db-schema-${rankdir}.svg`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  if (error) return <div style={{ color: "#fcc", padding: 12 }}>✗ {error}</div>;
  if (!schema || !filtered || !layout) {
    return <div style={{ color: "#888", padding: 12 }}>loading schema…</div>;
  }

  const { g, heights, clusters, selfLoops } = layout;
  const graphBBox = g.graph();

  // ── Compute opacity for a given table / edge based on selection. ──
  const tableOpacity = (name: string): number => {
    if (!selected) return 1;
    if (name === selected) return 1;
    if (adjacency.get(selected)?.has(name)) return 1;
    return 0.12;
  };
  const edgeOpacity = (rel: Relationship): number => {
    if (!selected) return 1;
    if (rel.from_table === selected || rel.to_table === selected) return 1;
    return 0.07;
  };

  return (
    <div style={{ padding: 12 }}>
      <div style={{
        display: "flex", gap: 12, alignItems: "center", marginBottom: 8,
        color: "#aaa", fontSize: 12, flexWrap: "wrap",
      }}>
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="search tables…"
          style={{
            padding: "4px 10px", background: "#1a1a1a", color: "#ddd",
            border: "1px solid #333", borderRadius: 3, fontSize: 12,
            fontFamily: "Consolas, monospace", minWidth: 180,
          }}
        />
        <span>
          src
          {(["orm", "live", "diff"] as const).map((s) => (
            <button
              key={s} type="button"
              onClick={() => setSource(s)}
              style={{
                marginLeft: 4, padding: "2px 8px", fontSize: 11,
                background: s === source ? "#2a4a6a" : "transparent",
                color: s === source ? "#fff" : "#aaa",
                border: "1px solid #333", borderRadius: 3, cursor: "pointer",
                textTransform: "uppercase",
              }}>{s}</button>
          ))}
        </span>
        <span><b style={{ color: "#7af" }}>{filtered.tables.length}</b>/{schema.n_tables} tables</span>
        <span><b style={{ color: "#7af" }}>{filtered.relationships.length}</b>/{schema.n_relationships} FKs</span>
        {driftStats && (
          <span style={{
            padding: "2px 8px", borderRadius: 3,
            background: driftStats.nDiff + driftStats.nOrmOnly + driftStats.nLiveOnly > 0
              ? "#3a1a1a" : "#1a3a1a",
            color: driftStats.nDiff + driftStats.nOrmOnly + driftStats.nLiveOnly > 0
              ? "#fbb" : "#bfb",
            fontWeight: 700, fontSize: 11,
          }}>
            {driftStats.nDiff + driftStats.nOrmOnly + driftStats.nLiveOnly === 0
              ? "✓ in sync"
              : `⚠ ${driftStats.nDiff + driftStats.nOrmOnly + driftStats.nLiveOnly} drift`}
            <span style={{ marginLeft: 6, fontWeight: 400, color: "#888" }}>
              ✓{driftStats.nSame}
              · ≠{driftStats.nDiff}
              · ORM-only {driftStats.nOrmOnly}
              · LIVE-only {driftStats.nLiveOnly}
            </span>
          </span>
        )}
        <span>zoom <b style={{ color: "#9aa6c8" }}>{(scale * 100).toFixed(0)}%</b></span>
        <span>
          dir
          {(["TB", "LR"] as const).map((r) => (
            <button
              key={r} type="button"
              onClick={() => setRankdir(r)}
              style={{
                marginLeft: 4, padding: "2px 8px", fontSize: 11,
                background: r === rankdir ? "#2a4a6a" : "transparent",
                color: r === rankdir ? "#fff" : "#aaa",
                border: "1px solid #333", borderRadius: 3, cursor: "pointer",
              }}>{r}</button>
          ))}
        </span>
        <button
          type="button"
          onClick={exportPng}
          style={{
            padding: "3px 10px", fontSize: 11,
            background: "#1a2a3a", color: "#9bf",
            border: "1px solid #335", borderRadius: 3, cursor: "pointer",
          }}>↓ PNG</button>
        <button
          type="button"
          onClick={exportSvg}
          style={{
            padding: "3px 10px", fontSize: 11,
            background: "#1a2a3a", color: "#9bf",
            border: "1px solid #335", borderRadius: 3, cursor: "pointer",
          }}>↓ SVG</button>
        {selected && (
          <span style={{ color: "#7af" }}>
            ● <b>{selected}</b> · {adjacency.get(selected)?.size ?? 0} related
            <button
              type="button"
              onClick={() => setSelected(null)}
              style={{
                marginLeft: 6, padding: "1px 6px", fontSize: 10,
                background: "transparent", color: "#888",
                border: "1px solid #333", borderRadius: 3, cursor: "pointer",
              }}>clear</button>
          </span>
        )}
        <span style={{ color: "#666", marginLeft: "auto", fontSize: 10.5 }}>
          <span style={{ color: "#e66" }}>━</span>CASCADE
          {"  "}<span style={{ color: "#ec6" }}>━</span>SET&nbsp;NULL
          {"  "}<span style={{ color: "#6c6" }}>━</span>RESTRICT
          {"  "}<span style={{ color: "#888" }}>━</span>NO&nbsp;ACTION
          {"  "}|  ⟝ many · ⊢ one · ○ optional
        </span>
      </div>
      <div
        style={{
          width: "100%",
          // Fills the viewport from the bottom of the toolbar down to
          // the bottom edge of the dev pane. 175 px = DevLayout Header
          // + nav tabs + this page's top padding + toolbar row + a
          // small safety margin. Adjust here if the toolbar grows.
          height: "calc(100vh - 175px)",
          minHeight: 600,
          background: "#0a0a0a",
          border: "1px solid #222",
          borderRadius: 4,
          overflow: "hidden",
          cursor: dragRef.current ? "grabbing" : "grab",
          position: "relative",
        }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        onWheel={onWheel}
        onDoubleClick={onDoubleClick}
      >
        <svg
          ref={svgRef}
          width="100%" height="100%"
          xmlns="http://www.w3.org/2000/svg"
          style={{ display: "block", userSelect: "none", overflow: "hidden" }}
        >
          <defs>
            {/* Crow's-foot markers : 4 variants × 4 colours = 16 markers.
                We use one set per ON DELETE colour so the line stays
                visually unified end to end. */}
            {Object.entries({
              cascade:  "#e66",
              setnull:  "#ec6",
              restrict: "#6c6",
              noaction: "#888",
            }).map(([key, color]) => (
              <g key={key}>
                {/* ─ "one" : single vertical bar */}
                <marker id={`one-${key}`} viewBox="0 0 14 12"
                        markerWidth="14" markerHeight="12"
                        refX="13" refY="6" orient="auto">
                  <line x1="11" y1="1" x2="11" y2="11"
                        stroke={color} strokeWidth="1.6" />
                </marker>
                {/* ─ "one optional" : circle inboard + bar */}
                <marker id={`one-opt-${key}`} viewBox="0 0 22 12"
                        markerWidth="22" markerHeight="12"
                        refX="21" refY="6" orient="auto">
                  <circle cx="5" cy="6" r="3"
                          fill="#0a0a0a" stroke={color} strokeWidth="1.4" />
                  <line x1="19" y1="1" x2="19" y2="11"
                        stroke={color} strokeWidth="1.6" />
                </marker>
                {/* ─ "many" : crow's foot (3 prongs converging inboard) */}
                <marker id={`many-${key}`} viewBox="0 0 16 12"
                        markerWidth="16" markerHeight="12"
                        refX="15" refY="6" orient="auto">
                  <line x1="2" y1="6"  x2="14" y2="1"
                        stroke={color} strokeWidth="1.4" />
                  <line x1="2" y1="6"  x2="14" y2="11"
                        stroke={color} strokeWidth="1.4" />
                  <line x1="2" y1="6"  x2="14" y2="6"
                        stroke={color} strokeWidth="1.4" />
                </marker>
                {/* ─ "many optional" : circle inboard + crow's foot */}
                <marker id={`many-opt-${key}`} viewBox="0 0 24 12"
                        markerWidth="24" markerHeight="12"
                        refX="23" refY="6" orient="auto">
                  <circle cx="5" cy="6" r="3"
                          fill="#0a0a0a" stroke={color} strokeWidth="1.4" />
                  <line x1="10" y1="6" x2="22" y2="1"
                        stroke={color} strokeWidth="1.4" />
                  <line x1="10" y1="6" x2="22" y2="11"
                        stroke={color} strokeWidth="1.4" />
                  <line x1="10" y1="6" x2="22" y2="6"
                        stroke={color} strokeWidth="1.4" />
                </marker>
                {/* Same family, "start" orientation (orient="auto-start-reverse")
                    so the foot/bar points outward at the line's source end. */}
                <marker id={`one-${key}-s`} viewBox="0 0 14 12"
                        markerWidth="14" markerHeight="12"
                        refX="1" refY="6" orient="auto-start-reverse">
                  <line x1="3" y1="1" x2="3" y2="11"
                        stroke={color} strokeWidth="1.6" />
                </marker>
                <marker id={`many-${key}-s`} viewBox="0 0 16 12"
                        markerWidth="16" markerHeight="12"
                        refX="1" refY="6" orient="auto-start-reverse">
                  <line x1="14" y1="6"  x2="2" y2="1"
                        stroke={color} strokeWidth="1.4" />
                  <line x1="14" y1="6"  x2="2" y2="11"
                        stroke={color} strokeWidth="1.4" />
                  <line x1="14" y1="6"  x2="2" y2="6"
                        stroke={color} strokeWidth="1.4" />
                </marker>
                <marker id={`many-opt-${key}-s`} viewBox="0 0 24 12"
                        markerWidth="24" markerHeight="12"
                        refX="1" refY="6" orient="auto-start-reverse">
                  <circle cx="19" cy="6" r="3"
                          fill="#0a0a0a" stroke={color} strokeWidth="1.4" />
                  <line x1="14" y1="6" x2="2" y2="1"
                        stroke={color} strokeWidth="1.4" />
                  <line x1="14" y1="6" x2="2" y2="11"
                        stroke={color} strokeWidth="1.4" />
                  <line x1="14" y1="6" x2="2" y2="6"
                        stroke={color} strokeWidth="1.4" />
                </marker>
              </g>
            ))}
          </defs>
          <g data-export-root
             transform={`translate(${tx + 40}, ${ty + 40}) scale(${scale})`}>
            {/* Cluster backdrops first → edges + cards paint on top. */}
            {clusters.map((c) => (
              <g key={c.key} opacity={selected ? 0.4 : 0.85}>
                <rect x={c.x} y={c.y} width={c.w} height={c.h} rx={10}
                      fill={c.color} fillOpacity={0.05}
                      stroke={c.color} strokeOpacity={0.45}
                      strokeWidth={1.2} strokeDasharray="6,4" />
                <text x={c.x + 14} y={c.y + 20}
                      fill={c.color} fontSize={14} fontWeight={700}
                      fontFamily="Consolas, monospace"
                      opacity={0.8}>
                  {c.label.toUpperCase()}
                </text>
              </g>
            ))}
            {/* Edges next → cards paint on top. */}
            {g.edges().map((e, i) => {
              const ed = g.edge(e) as unknown as DagreEdge;
              return (
                <EdgeView key={i} edge={ed}
                          opacity={edgeOpacity(ed.meta)} />
              );
            })}
            {filtered.tables.map((t) => {
              const n = g.node(t.name);
              if (!n) return null;
              const h = heights.get(t.name) ?? cardHeight(t);
              return (
                <TableCard key={t.name}
                  table={t} cx={n.x} cy={n.y} h={h}
                  isSelected={selected === t.name}
                  opacity={tableOpacity(t.name)}
                  drift={driftMap.get(t.name) ?? null}
                  onCopyFix={(d) => copyDriftSql(t.name, d)}
                  onClick={() => setSelected(
                    selected === t.name ? null : t.name,
                  )} />
              );
            })}
            {/* Self-loops drawn AFTER cards so they sit on top of the
                right-side edge and aren't hidden behind a neighbour. */}
            {selfLoops.map((r, i) => {
              const n = g.node(r.from_table);
              if (!n) return null;
              const h = heights.get(r.from_table) ?? 100;
              return (
                <g key={`sl-${i}`} opacity={edgeOpacity(r)}>
                  <SelfLoop rel={r} cx={n.x} cy={n.y} h={h} />
                </g>
              );
            })}
          </g>
        </svg>
      </div>
      {toast && (
        <div style={{
          position: "fixed", bottom: 24, right: 24,
          padding: "10px 16px", borderRadius: 4,
          background: toast.startsWith("✓") ? "#1a3a1a" : "#3a1a1a",
          color: toast.startsWith("✓") ? "#bfb" : "#fbb",
          border: `1px solid ${toast.startsWith("✓") ? "#2a5a2a" : "#5a2a2a"}`,
          fontSize: 12, fontFamily: "Consolas, monospace",
          boxShadow: "0 4px 12px rgba(0,0,0,0.5)",
          zIndex: 1000, pointerEvents: "none",
        }}>
          {toast}
        </div>
      )}
    </div>
  );
}


function TableCard({
  table, cx, cy, h, isSelected, opacity, onClick, drift, onCopyFix,
}: {
  table: Table;
  cx: number; cy: number; h: number;
  isSelected: boolean;
  opacity: number;
  onClick: () => void;
  drift: Drift | null;
  onCopyFix: (d: Drift) => void;
}): JSX.Element {
  const x = cx - COL_W / 2;
  const y = cy - h / 2;
  // Drift colour overrides the default neutral border so a DBA sees
  // disagreement at a glance. Selection still wins (the user
  // explicitly focused this card).
  let driftBorderColor: string | null = null;
  let driftBadge: { text: string; color: string } | null = null;
  if (drift) {
    if (drift.status === "orm-only") {
      driftBorderColor = "#e88";
      driftBadge = { text: "ORM only", color: "#e88" };
    } else if (drift.status === "live-only") {
      driftBorderColor = "#8e8";
      driftBadge = { text: "LIVE only", color: "#8e8" };
    } else if (drift.status === "diff") {
      driftBorderColor = "#ec6";
      driftBadge = {
        text: `≠ ${drift.reasons.length} diff`,
        color: "#ec6",
      };
    }
  }
  const headerFill = isSelected ? "#2e4a78" : "#1f2a3f";
  const borderColor = isSelected
    ? "#7af"
    : (driftBorderColor ?? "#3a4a6a");
  const borderWidth = isSelected ? 2.2 : (driftBorderColor ? 2 : 1.5);
  return (
    <g opacity={opacity}
       onMouseDown={(e) => { e.stopPropagation(); }}
       onClick={(e) => { e.stopPropagation(); onClick(); }}
       style={{ cursor: "pointer" }}>
      <rect x={x + 3} y={y + 3} width={COL_W} height={h} rx={6}
            fill="#000" fillOpacity={0.4} />
      <rect x={x} y={y} width={COL_W} height={h} rx={6}
            fill="#111" stroke={borderColor} strokeWidth={borderWidth} />
      <rect x={x} y={y} width={COL_W} height={HEADER_H} rx={6}
            fill={headerFill} />
      <text x={x + COL_W / 2} y={y + 19} textAnchor="middle"
            fill="#9bf" fontSize={13} fontWeight={700}
            fontFamily="Consolas, monospace">
        {table.name}
        {table.comment && <title>{table.comment}</title>}
      </text>
      {driftBadge && drift && (
        <g transform={`translate(${x + COL_W - 96}, ${y + 6})`}
           onMouseDown={(e) => { e.stopPropagation(); }}
           onClick={(e) => {
             e.stopPropagation();
             if (drift.sqlFixes.length > 0) onCopyFix(drift);
           }}
           style={{ cursor: drift.sqlFixes.length > 0 ? "copy" : "default" }}>
          <rect width={90} height={16} rx={3}
                fill="#000" stroke={driftBadge.color} strokeWidth={1} />
          <text x={6} y={11.5}
                fill={driftBadge.color} fontSize={9} fontWeight={700}
                fontFamily="Consolas, monospace">
            {driftBadge.text}
            {drift.reasons.length > 0 && (
              <title>
                {drift.reasons.join("\n")}
                {drift.sqlFixes.length > 0
                  ? "\n\n(click → copy SQL fix to clipboard)"
                  : ""}
              </title>
            )}
          </text>
          {drift.sqlFixes.length > 0 && (
            <text x={82} y={12} textAnchor="middle"
                  fill={driftBadge.color} fontSize={11}
                  fontFamily="Consolas, monospace">
              ⧉
            </text>
          )}
        </g>
      )}
      {table.columns.map((c, i) => {
        const rowY = y + HEADER_H + 6 + i * ROW_H;
        return <ColumnRow key={c.name} col={c} x={x} y={rowY} />;
      })}
      {/* Footer : CHECK constraints + composite UNIQUE constraints,
          rendered as a dimmed strip at the bottom of the card so the
          column list above stays clean. */}
      {(table.check_constraints.length > 0 || table.composite_unique.length > 0) && (() => {
        const colsEnd = y + HEADER_H + 6 + table.n_columns * ROW_H + 6;
        return (
          <g>
            <line x1={x + 6} y1={colsEnd}
                  x2={x + COL_W - 6} y2={colsEnd}
                  stroke="#2a2f3a" strokeWidth={1} />
            {table.composite_unique.map((u, i) => {
              const rowY = colsEnd + FOOTER_TOP_PAD + i * FOOTER_ROW_H;
              return (
                <text key={`uq-${i}`} x={x + 8} y={rowY + 10}
                      fill="#cf8" fontSize={10}
                      fontFamily="Consolas, monospace">
                  UQ ({u.columns.join(", ")})
                </text>
              );
            })}
            {table.check_constraints.map((c, i) => {
              const rowY = colsEnd + FOOTER_TOP_PAD
                          + (table.composite_unique.length + i) * FOOTER_ROW_H;
              const text = c.name
                ? `CK ${c.name}`
                : `CK ${c.sql.slice(0, 30)}${c.sql.length > 30 ? "…" : ""}`;
              return (
                <text key={`ck-${i}`} x={x + 8} y={rowY + 10}
                      fill="#f9a" fontSize={10}
                      fontFamily="Consolas, monospace">
                  {text}
                  <title>{c.sql}</title>
                </text>
              );
            })}
          </g>
        );
      })()}
    </g>
  );
}


/** One column row : badges on the left, name in the middle, SQL type
 *  right-aligned. NOT NULL is shown by bold name colour, NULL by dim.
 *  A trailing ``· DEFAULT …`` after the type if a server-side default
 *  exists. Column comment surfaced as a browser tooltip via <title>. */
function ColumnRow({ col, x, y }: {
  col: Column; x: number; y: number;
}): JSX.Element {
  const badges: Array<{ k: string; color: string }> = [];
  if (col.pk)      badges.push({ k: "PK", color: "#fc6" });
  if (col.fk)      badges.push({ k: "FK", color: "#7af" });
  if (col.unique)  badges.push({ k: "UQ", color: "#cf8" });
  if (col.indexed) badges.push({ k: "IX", color: "#c8f" });

  const nameColor = col.nullable ? "#999" : "#eee";
  const typeText = col.default
    ? `${col.type} · DEF ${col.default}`
    : col.type;

  return (
    <g>
      {col.comment && <title>{col.comment}</title>}
      {badges.map((b, i) => (
        <g key={b.k} transform={`translate(${x + 6 + i * 22}, ${y + 1})`}>
          <rect width={20} height={12} rx={2}
                fill="#000" stroke={b.color} strokeWidth={0.8} />
          <text x={10} y={9} textAnchor="middle"
                fill={b.color} fontSize={8.5} fontWeight={700}
                fontFamily="Consolas, monospace">
            {b.k}
          </text>
        </g>
      ))}
      <text x={x + 6 + badges.length * 22 + 4} y={y + 12}
            fill={nameColor} fontSize={11} fontWeight={col.nullable ? 400 : 600}
            fontFamily="Consolas, monospace">
        {col.name}
        {col.comment && (
          <tspan dx={3} fill="#7af" fontSize={9}>•</tspan>
        )}
      </text>
      <text x={x + COL_W - 8} y={y + 12} textAnchor="end"
            fill={col.default ? "#aaa" : "#666"} fontSize={10}
            fontFamily="Consolas, monospace">
        {typeText}
      </text>
    </g>
  );
}


/** Pick the right pair of crow's-foot markers for an edge.
 *  Convention :
 *    - Source end (from_table = child / FK-bearing) :
 *        "one" if 1:1, else "many".
 *    - Target end (to_table = parent / PK-side) :
 *        always "one" cardinally ; nullable FK ⇒ "one optional".
 *  Optional flag at the parent end uses the *-opt variant. */
function endpointMarkers(rel: Relationship, colorKey: string): {
  start: string; end: string;
} {
  const fromMany = rel.cardinality !== "1:1";
  const toOptional = rel.optional;
  const start = fromMany
    ? `many-${colorKey}-s`
    : `one-${colorKey}-s`;
  const end = toOptional
    ? `one-opt-${colorKey}`
    : `one-${colorKey}`;
  return { start, end };
}

function colorKeyFor(rel: Relationship): string {
  switch (rel.on_delete) {
    case "CASCADE":  return "cascade";
    case "SET NULL": return "setnull";
    case "RESTRICT": return "restrict";
    default:         return "noaction";
  }
}


function EdgeView({
  edge, opacity,
}: {
  edge: DagreEdge;
  opacity: number;
}): JSX.Element {
  const rel = edge.meta;
  const lineColor = colorFor(rel.on_delete);
  const dasharray = rel.optional ? "6,4" : undefined;
  const pts = edge.points.map((p) => `${p.x},${p.y}`).join(" ");
  const colorKey = colorKeyFor(rel);
  const markers = endpointMarkers(rel, colorKey);

  const labelW = Math.max(28, rel.label.length * 5.5 + 14);
  const labelH = 14;

  return (
    <g opacity={opacity}>
      <polyline
        points={pts}
        fill="none"
        stroke={lineColor} strokeWidth={1.4}
        strokeDasharray={dasharray}
        markerStart={`url(#${markers.start})`}
        markerEnd={`url(#${markers.end})`}
      />
      {/* FK column-name label rendered as a small pill at the slot
          dagre reserved (edge.x / edge.y). Without this you lose the
          FK column name when you drop the legacy Chen diamond. */}
      <rect
        x={edge.x - labelW / 2} y={edge.y - labelH / 2}
        width={labelW} height={labelH} rx={2.5}
        fill="#0e1118" stroke={lineColor} strokeWidth={0.8}
        opacity={0.95}
      />
      <text x={edge.x} y={edge.y + 4} textAnchor="middle"
            fill={rel.composite ? "#fc6" : "#cdd"} fontSize={10}
            fontFamily="Consolas, monospace">
        {rel.label}
      </text>
    </g>
  );
}


/** Self-loop arc on the right side of a table card.
 *
 *  A D-shaped curve that exits the card's right edge, loops outward,
 *  and re-enters slightly below the exit. We draw a small bar marker
 *  at the return point to keep the crow's-foot semantics (the loop
 *  represents both endpoints of the FK on the same table). */
function SelfLoop({
  rel, cx, cy, h,
}: {
  rel: Relationship;
  cx: number; cy: number; h: number;
}): JSX.Element {
  const lineColor = colorFor(rel.on_delete);
  const dasharray = rel.optional ? "6,4" : undefined;
  // Right edge anchor.
  const x0 = cx + COL_W / 2;
  const yTop = cy - Math.min(40, h / 4);
  const yBot = cy + Math.min(40, h / 4);
  // Bulge 70 px to the right of the card.
  const bulge = 70;
  const d = `M ${x0} ${yTop}
             C ${x0 + bulge} ${yTop - 20},
               ${x0 + bulge} ${yBot + 20},
               ${x0} ${yBot}`;
  return (
    <g>
      <path d={d} fill="none"
            stroke={lineColor} strokeWidth={1.4}
            strokeDasharray={dasharray} />
      {/* Small bar at the return (target = parent) to mirror the
          crow's-foot convention. */}
      <line x1={x0 - 1} y1={yBot - 5} x2={x0 - 1} y2={yBot + 5}
            stroke={lineColor} strokeWidth={1.6} />
      <text x={x0 + bulge + 6} y={cy + 4}
            fill={lineColor} fontSize={10}
            fontFamily="Consolas, monospace">
        ↻ {rel.label}
      </text>
    </g>
  );
}
