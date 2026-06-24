"use client";
import { useState, useEffect, useCallback } from "react";
import { searchCompanies } from "../lib/api";
import CoverageGrid from "./CoverageGrid";

export default function CatalogCompanyPicker() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [selected, setSelected] = useState(null);
  const [open, setOpen] = useState(false);

  const search = useCallback(async (query) => {
    const r = await searchCompanies(query);
    setResults(r.companies || []);
  }, []);

  useEffect(() => {
    const t = setTimeout(() => search(q), 220);
    return () => clearTimeout(t);
  }, [q, search]);

  return (
    <div>
      <p className="eyebrow" style={{ marginBottom: 12 }}>SELECT A COMPANY</p>
      <div style={{ position: "relative", maxWidth: 460, marginBottom: 32 }}>
        <input value={q} onFocus={() => setOpen(true)}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          placeholder="Search name or symbol…"
          style={{ width: "100%", padding: "13px 16px", border: "1px solid var(--rule)",
            background: "var(--paper-card)", fontSize: 15, fontFamily: "var(--sans)" }} />
        {open && results.length > 0 && (
          <div style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 20,
            background: "var(--paper-card)", border: "1px solid var(--rule)",
            maxHeight: 300, overflowY: "auto" }}>
            {results.map((c) => (
              <button key={c.symbol}
                onClick={() => { setSelected(c); setOpen(false);
                  setQ(c.name); }}
                style={{ display: "flex", flexDirection: "column", gap: 2,
                  width: "100%", textAlign: "left", padding: "11px 16px",
                  background: "transparent", border: "none",
                  borderBottom: "1px solid var(--rule)" }}>
                <span style={{ fontSize: 13, fontWeight: 500 }}>{c.name}</span>
                <span className="mono" style={{ fontSize: 10,
                  color: "color-mix(in srgb, var(--ink) 55%, transparent)" }}>
                  {c.symbol.split(".")[0]}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {selected ? (
        <div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 14,
            marginBottom: 20 }}>
            <span className="mono" style={{ fontSize: 15, color: "var(--gold)",
              fontWeight: 500 }}>{selected.symbol.split(".")[0]}</span>
            <span className="serif" style={{ fontSize: 26, color: "var(--teal-d)" }}>
              {selected.name}
            </span>
          </div>
          <CoverageGrid symbol={selected.symbol} />
        </div>
      ) : (
        <p style={{ fontSize: 14, lineHeight: 1.6,
          color: "color-mix(in srgb, var(--ink) 55%, transparent)" }}>
          Search for a company above to see its full annual-report catalog and
          index reports on demand.
        </p>
      )}
    </div>
  );
}
