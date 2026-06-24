"use client";
import { useState, useMemo } from "react";
import Link from "next/link";

const STATUSES = ["ALL", "INDEXED", "FLAGGED", "FAILED", "PENDING", "NO_REPORT"];

export default function CompanyBrowser({ companies }) {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("ALL");

  const filtered = useMemo(() => {
    const query = q.toLowerCase();
    return companies.filter((c) => {
      const matchQ = !query || c.name.toLowerCase().includes(query)
        || c.symbol.toLowerCase().includes(query);
      const matchS = status === "ALL" || (c.status || "PENDING") === status;
      return matchQ && matchS;
    });
  }, [companies, q, status]);

  return (
    <div className="wrap" style={{ paddingTop: 36, paddingBottom: 40 }}>
      <p className="eyebrow" style={{ marginBottom: 12 }}>BROWSE · {companies.length} COMPANIES</p>
      <h1 className="serif" style={{ fontSize: 44, color: "var(--teal-d)",
        marginBottom: 28 }}>The corpus</h1>

      {/* controls */}
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 24 }}>
        <input value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Search name or symbol…"
          style={{ flex: "1 1 280px", padding: "12px 16px", border: "1px solid var(--rule)",
            background: "var(--paper-card)", fontSize: 15, fontFamily: "var(--sans)" }} />
        <div style={{ display: "flex", gap: 0, border: "1px solid var(--rule)",
          background: "var(--paper-card)" }}>
          {STATUSES.map((s) => (
            <button key={s} onClick={() => setStatus(s)} className="mono"
              style={{ padding: "12px 14px", fontSize: 11, letterSpacing: ".4px",
                border: "none", borderRight: s !== "NO_REPORT" ? "1px solid var(--rule)" : "none",
                background: status === s ? "var(--teal)" : "transparent",
                color: status === s ? "var(--paper)" : "color-mix(in srgb, var(--ink) 65%, transparent)" }}>
              {s}
            </button>
          ))}
        </div>
      </div>

      <p className="mono" style={{ fontSize: 11, marginBottom: 20,
        color: "color-mix(in srgb, var(--ink) 50%, transparent)" }}>
        {filtered.length} shown
      </p>

      {/* grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
        gap: 14 }}>
        {filtered.map((c) => (
          <Link key={c.symbol} href={`/company/${encodeURIComponent(c.symbol)}`}
            style={{ border: "1px solid var(--rule)", background: "var(--paper-card)",
              padding: "18px 18px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between",
              alignItems: "baseline", gap: 8 }}>
              <span className="mono" style={{ fontSize: 13, color: "var(--teal-d)",
                fontWeight: 500 }}>{c.symbol.split(".")[0]}</span>
              <span className={`badge badge-${(c.status || "pending").toLowerCase()}`}>
                {c.status || "PENDING"}
              </span>
            </div>
            <div style={{ fontSize: 15, lineHeight: 1.35, color: "var(--ink)",
              minHeight: 40 }}>{c.name}</div>
            <div className="mono" style={{ fontSize: 11,
              color: "color-mix(in srgb, var(--ink) 55%, transparent)" }}>
              {c.fiscal_year ? `FY${c.fiscal_year}` : "—"}
              {c.chunks ? ` · ${c.chunks} chunks` : ""}
            </div>
          </Link>
        ))}
      </div>
      {filtered.length === 0 && (
        <p style={{ padding: "40px 0", fontSize: 15,
          color: "color-mix(in srgb, var(--ink) 60%, transparent)" }}>
          No company matches these filters. Try a different search or status.
        </p>
      )}
    </div>
  );
}
