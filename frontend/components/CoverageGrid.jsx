"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import { fetchCompanyCatalog, indexReport, indexCompany } from "../lib/api";

const STATUS_STYLE = {
  INDEXED: { label: "INDEXED", cls: "badge-indexed" },
  AVAILABLE: { label: "AVAILABLE", cls: "badge-pending" },
  QUEUED: { label: "QUEUED", cls: "badge-pending" },
  PROCESSING: { label: "PROCESSING", cls: "badge-pending" },
  FLAGGED: { label: "FLAGGED", cls: "badge-flagged" },
  FAILED: { label: "FAILED", cls: "badge-failed" },
};

const ACTIVE = new Set(["QUEUED", "PROCESSING"]);

export default function CoverageGrid({ symbol }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState({});       // doc_id -> true while request in flight
  const pollRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const d = await fetchCompanyCatalog(symbol);
      setData(d);
      return d;
    } catch { return null; }
  }, [symbol]);

  useEffect(() => { load(); }, [load]);

  // Poll while any report is QUEUED/PROCESSING, so statuses flip live.
  useEffect(() => {
    const anyActive = data?.reports?.some((r) => ACTIVE.has(r.status));
    clearInterval(pollRef.current);
    if (anyActive) {
      pollRef.current = setInterval(load, 4000);
    }
    return () => clearInterval(pollRef.current);
  }, [data, load]);

  async function onIndexReport(doc_id) {
    setBusy((b) => ({ ...b, [doc_id]: true }));
    await indexReport(doc_id);
    await load();
    setBusy((b) => ({ ...b, [doc_id]: false }));
  }

  async function onIndexAll() {
    setBusy((b) => ({ ...b, __all: true }));
    await indexCompany(symbol);
    await load();
    setBusy((b) => ({ ...b, __all: false }));
  }

  if (!data) {
    return <p className="mono" style={{ fontSize: 12, color: "var(--ink)",
      opacity: .5 }}>Loading catalog…</p>;
  }

  const reports = data.reports || [];
  const indexed = reports.filter((r) => r.status === "INDEXED").length;
  const indexable = reports.filter((r) =>
    ["AVAILABLE", "FAILED", "FLAGGED"].includes(r.status)).length;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between",
        alignItems: "baseline", flexWrap: "wrap", gap: 12, marginBottom: 16 }}>
        <p className="eyebrow">
          COVERAGE · {indexed} OF {reports.length} INDEXED
        </p>
        {indexable > 0 && (
          <button onClick={onIndexAll} disabled={busy.__all}
            className="mono" style={{ fontSize: 11, letterSpacing: ".4px",
              padding: "7px 14px", background: busy.__all ? "transparent" : "var(--teal)",
              color: busy.__all ? "var(--ink)" : "var(--paper)",
              border: "1px solid var(--teal)" }}>
            {busy.__all ? "QUEUING…" : `INDEX ALL AVAILABLE (${indexable})`}
          </button>
        )}
      </div>

      {/* ledger of holdings — one row per annual report year */}
      <div style={{ border: "1px solid var(--rule)" }}>
        {reports.map((r, i) => {
          const st = STATUS_STYLE[r.status] || STATUS_STYLE.AVAILABLE;
          const active = ACTIVE.has(r.status);
          const canIndex = ["AVAILABLE", "FAILED", "FLAGGED"].includes(r.status);
          return (
            <div key={r.doc_id} style={{ display: "flex", alignItems: "center",
              gap: 16, padding: "13px 18px",
              borderBottom: i < reports.length - 1 ? "1px solid var(--rule)" : "none",
              background: active ? "#fffaf0" : "transparent" }}>
              {/* year */}
              <span className="mono" style={{ fontSize: 15, color: "var(--teal-d)",
                fontWeight: 500, minWidth: 56 }}>
                {r.fiscal_year ? `FY${r.fiscal_year}` : "—"}
              </span>
              {/* status */}
              <span className={`badge ${st.cls}`} style={{ minWidth: 92,
                textAlign: "center" }}>
                {active ? <PulseLabel text={st.label} /> : st.label}
              </span>
              {/* detail */}
              <span className="mono" style={{ fontSize: 11, flex: 1,
                color: "color-mix(in srgb, var(--ink) 50%, transparent)",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {r.status === "INDEXED"
                  ? `${r.pages || "?"}p · ${r.tables || "?"} tables · ${r.chunks || "?"} chunks`
                  : r.status === "FAILED" ? (r.error || "").slice(0, 60)
                  : (r.cse_file_text || "").slice(0, 50)}
              </span>
              {/* action */}
              {canIndex && (
                <button onClick={() => onIndexReport(r.doc_id)}
                  disabled={busy[r.doc_id]}
                  className="mono" style={{ fontSize: 11, letterSpacing: ".3px",
                    padding: "6px 14px", border: "1px solid var(--rule)",
                    background: "var(--paper-card)",
                    color: busy[r.doc_id] ? "var(--ink)" : "var(--teal)" }}>
                  {busy[r.doc_id] ? "QUEUING…"
                    : r.status === "AVAILABLE" ? "Index" : "Retry"}
                </button>
              )}
              {r.status === "INDEXED" && (
                <span className="mono" style={{ fontSize: 11, color: "var(--gold)",
                  minWidth: 60, textAlign: "right" }}>✓ ready</span>
              )}
              {active && (
                <span className="mono" style={{ fontSize: 11, color: "var(--teal)",
                  minWidth: 60, textAlign: "right" }}>working…</span>
              )}
            </div>
          );
        })}
        {reports.length === 0 && (
          <div style={{ padding: "20px 18px", fontSize: 13,
            color: "color-mix(in srgb, var(--ink) 55%, transparent)" }}>
            No reports discovered yet. Run a catalog refresh from the coverage page.
          </div>
        )}
      </div>
    </div>
  );
}

// subtle pulsing label for active (queued/processing) states
function PulseLabel({ text }) {
  return (
    <span style={{ animation: "cse-pulse 1.4s ease-in-out infinite" }}>
      {text}
      <style>{`@keyframes cse-pulse { 0%,100%{opacity:1} 50%{opacity:.45} }`}</style>
    </span>
  );
}
