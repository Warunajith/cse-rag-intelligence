"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Home" },
  { href: "/ask", label: "Ask" },
  { href: "/companies", label: "Companies" },
  { href: "/catalog", label: "Coverage" },
];

export default function Masthead() {
  const path = usePathname();
  return (
    <header className="rule-b">
      <div className="wrap" style={{ padding: "20px 32px", display: "flex",
        alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <Link href="/" style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
          <span className="serif" style={{ fontSize: 30, color: "var(--teal-d)",
            letterSpacing: ".5px" }}>Ledger</span>
          <span className="mono" style={{ fontSize: 12, color: "var(--ink)", opacity: .6,
            letterSpacing: ".5px" }}>CSE ANNUAL REPORT INTELLIGENCE</span>
        </Link>
        <nav style={{ display: "flex", gap: 24 }}>
          {NAV.map((n) => {
            const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
            return (
              <Link key={n.href} href={n.href} className="mono"
                style={{ fontSize: 13, letterSpacing: ".3px",
                  color: active ? "var(--teal)" : "var(--ink)",
                  opacity: active ? 1 : .6,
                  borderBottom: active ? "1px solid var(--teal)" : "1px solid transparent",
                  paddingBottom: 3 }}>
                {n.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
