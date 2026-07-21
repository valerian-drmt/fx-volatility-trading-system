import { useEffect, useState } from "react";
import { apiFetch } from "../../api/client";

function isFxMarketOpen(now: Date): boolean {
  // FX cash market: Sun 22:00 UTC → Fri 22:00 UTC.
  const dow = now.getUTCDay();           // 0 = Sun, 5 = Fri, 6 = Sat
  const h = now.getUTCHours();
  if (dow === 6) return false;
  if (dow === 0) return h >= 22;
  if (dow === 5) return h < 22;
  return true;
}

export function StatusBadge(): JSX.Element {
  const [open, setOpen] = useState(() => isFxMarketOpen(new Date()));
  // 'paper' | 'live' | null. Read from the singleton ib_session_state row
  // which the execution-engine heartbeat keeps fresh.
  const [accountType, setAccountType] = useState<string | null>(null);

  useEffect(() => {
    const tick = () => setOpen(isFxMarketOpen(new Date()));
    const id = window.setInterval(tick, 60_000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const load = async () => {
      try {
        const r = await apiFetch("/api/v1/dev/tables/ib_session_state?limit=1");
        if (!r.ok) return;
        const j = await r.json();
        const t = j.rows?.[0]?.account_type ?? null;
        setAccountType(t);
      } catch { /* keep last */ }
    };
    void load();
    const id = window.setInterval(load, 30_000);
    return () => window.clearInterval(id);
  }, []);

  const marketColor = open ? "#3fb950" : "#f85149";
  const accountColor =
    accountType === "live" ? "#f85149"
    : accountType === "paper" ? "#e0b341"
    : "#8a90a0";
  const accountLabel = accountType ? accountType.toUpperCase() : "—";

  return (
    <div className="status-badge">
      <span
        className="status-symbol"
        style={{ color: marketColor, fontWeight: 600 }}
        data-testid="market-status"
        data-open={open}
      >
        {open ? "Market Open" : "Market Closed"}
      </span>
      <span
        style={{
          marginLeft: 12, padding: "2px 8px", borderRadius: 3,
          background: accountColor, color: "#0f1115",
          fontWeight: 700, fontSize: 11, textTransform: "uppercase",
          letterSpacing: 0.5,
        }}
        data-testid="account-type-badge"
        data-account-type={accountType ?? ""}
        title={accountType
          ? `IB account type · ${accountType}`
          : "IB account type unknown (session row not initialised)"}
      >
        {accountLabel}
      </span>
    </div>
  );
}
