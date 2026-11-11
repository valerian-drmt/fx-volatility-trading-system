import type { JSX } from "react";
/**
 * Source attribution for data shown on the desk (#307, audit 10#13). The
 * wording is the one each provider asks for; docs/ops/data-terms.md is the
 * record (what is fetched, what is stored, what each source's terms require).
 */
export const FRED_NOTICE =
  "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis.";

export function EventsAttribution(): JSX.Element {
  return (
    <p className="data-attrib dim small" data-testid="events-attribution">
      Release calendars: FRED® (Federal Reserve Bank of St. Louis), Federal Reserve, ECB, Bank of England,
      Eurostat, ONS (contains public sector information licensed under the Open Government Licence v3.0). {FRED_NOTICE}
    </p>
  );
}

export function MarketDataAttribution(): JSX.Element {
  return (
    <div className="data-attrib mono" data-testid="market-attribution" title="Interactive Brokers paper account, delayed market data">
      Market data: IB, delayed · paper
    </div>
  );
}
