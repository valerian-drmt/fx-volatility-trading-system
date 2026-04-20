import type { ReactNode } from "react";
import { Header } from "./Header";

export interface AppShellProps {
  left: ReactNode;
  center: ReactNode;
  right: ReactNode;
}

/** 3-column shell mirroring the PyQt desktop layout (left ~345px, center flex, right). */
export function AppShell({ left, center, right }: AppShellProps): JSX.Element {
  return (
    <div className="app-shell" data-testid="app-shell">
      <Header />
      <div className="app-grid">
        <aside className="app-col app-col-left">{left}</aside>
        <section className="app-col app-col-center">{center}</section>
        <aside className="app-col app-col-right">{right}</aside>
      </div>
    </div>
  );
}
