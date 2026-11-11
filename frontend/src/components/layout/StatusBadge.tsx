import type { JSX } from "react";
import { useEffect, useState } from "react";
import { apiFetch } from "../../api/client";
import { useMarketOpen } from "../../hooks/useMarketOpen";

export function StatusBadge(): JSX.Element {
  // One market-hours rule for the dev pages and the desk (utils/marketHours).
  const open = useMarketOpen();
  // 'paper' | 'live' | null. Read from the singleton ib_session_state row
  // which the execution-engine heartbeat keeps fresh.
  const [accountType, setAccountType] = useState<string | null>(null);

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
