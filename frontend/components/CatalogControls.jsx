"use client";
import { useState, useEffect, useCallback } from "react";
import { fetchCatalogStats, refreshCatalog } from "../lib/api";

export default function CatalogControls() {
  const [stats, setStats] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    try { setStats(await fetchCatalogStats()); } catch {}
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 5000);   // keep the strip live
    return () => clearInterval(iv);
  }, [load]);

  async function onRefresh() {
    setRefreshing(true);
    const res = await refreshCatalog({});            // discover ALL companies
    if (res?.status === "already_pending") {
      setNote("A catalog refresh is already running.");
      setTimeout(() => setNote(""), 4000);
    }
    setTimeout(load, 1500);
    setTimeout(() => setRefreshing(false), 1500);
  }

  const s = stats || {};
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between",
        alignItems: "flex-end", flexWrap: "wrap", gap: 16, marginBottom: 28 }}>
        <div>
          <p className="eyebrow" style={{ marginBottom: 12 }}>CSE CATALOG COVERAGE</p>
          <h1 className="serif" style={{ fontSize: 44, color: "var(--teal-d)",
            lineHeight: 1.05 }}>The holdings</h1>
        </div>
        <button onClick={onRefresh} disabled={refreshing} className="mono"
          style={{ fontSize: 12, letterSpacing: ".4px", padding: "11px 20px",
            border: "1px solid var(--teal)",
            background: refreshing ? "transparent" : "var(--teal)",
            color: refreshing ? "var(--teal)" : "var(--paper)" }}>
          {refreshing ? "DISCOVERING…" : "↻ REFRESH CSE CATALOG"}
        </button>
      </div>
      {note && (
        <p className="mono" style={{ fontSize: 11, color: "var(--gold)",
          marginTop: -16, marginBottom: 20 }}>{note}</p>
      )}

      <div className="rule-t rule-b" style={{ padding: "26px 0", display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 28,
        marginBottom: 36 }}>
        <Stat label="COMPANIES" value={s.companies} color="var(--teal-d)" />
        <Stat label="REPORTS KNOWN" value={s.reports_available_total} color="var(--teal-d)" />
        <Stat label="INDEXED" value={s.indexed} color="var(--gold)" />
        <Stat label="AVAILABLE" value={s.available} color="var(--ink)" />
        <Stat label="PROCESSING" value={s.processing} color="var(--teal)" />
        {(s.failed > 0 || s.flagged > 0) && (
          <Stat label="NEEDS ATTENTION" value={(s.failed || 0) + (s.flagged || 0)}
            color="var(--flag)" />
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <div>
      <div className="mono" style={{ fontSize: 38, fontWeight: 500, lineHeight: 1,
        color }}>{value ?? "—"}</div>
      <div className="eyebrow" style={{ marginTop: 8 }}>{label}</div>
    </div>
  );
}
