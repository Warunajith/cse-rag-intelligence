"use client";
import { useState, useRef, useEffect, useCallback } from "react";
import { askStream, searchCompanies } from "../lib/api";

// renders answer text with [p.X] citation chips + mono-styled figures
function renderAnswer(text, onCite) {
  const parts = text.split(/(\[p\.\d+(?:,\s*\d+)*\])/g);
  return parts.map((p, i) => {
    const m = p.match(/\[p\.([\d,\s]+)\]/);
    if (m) {
      const firstPage = parseInt(m[1]);
      return (
        <button key={i} onClick={() => onCite(firstPage)}
          className="mono"
          style={{ fontSize: ".78em", color: "var(--gold)", background: "transparent",
            border: "none", borderBottom: "1px dotted var(--gold)", padding: "0 1px",
            margin: "0 1px", verticalAlign: "baseline" }}>
          p.{m[1].trim()}
        </button>
      );
    }
    const figs = p.split(/(Rs\.?\s?[\d,]+(?:\.\d+)?\s?(?:Mn|Bn|million|billion)?|\d+\.\d+\s?%)/g);
    return figs.map((f, j) =>
      /Rs\.?\s?[\d,]/.test(f) || /\d+\.\d+\s?%/.test(f)
        ? <span key={`${i}-${j}`} className="mono" style={{ fontSize: ".9em",
            color: "var(--teal-d)", fontWeight: 500 }}>{f}</span>
        : <span key={`${i}-${j}`}>{f}</span>);
  });
}

const EXAMPLES = [
  "What was profit after tax in the latest year?",
  "How did revenue change year over year?",
  "What are the main risks the company flags?",
];

export default function AskInterface({ initialScope = null }) {
  const [question, setQuestion] = useState("");
  const [scope, setScope] = useState(initialScope);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [scopeQuery, setScopeQuery] = useState("");
  const [scopeResults, setScopeResults] = useState([]);
  const [streaming, setStreaming] = useState(false);
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState([]);
  const [highlight, setHighlight] = useState(null);
  const [submitted, setSubmitted] = useState("");
  const srcRefs = useRef({});

  // debounced company search against /api/companies?q=
  useEffect(() => {
    if (!scopeOpen) return;
    const t = setTimeout(async () => {
      const r = await searchCompanies(scopeQuery);
      setScopeResults(r.companies || []);
    }, 220);
    return () => clearTimeout(t);
  }, [scopeQuery, scopeOpen]);

  const ask = useCallback(() => {
    if (!question.trim() || streaming) return;
    setStreaming(true); setAnswer(""); setSources([]); setHighlight(null);
    setSubmitted(question);
    const body = { question };
    if (scope?.symbol) body.ticker = scope.symbol;
    askStream(body, {
      onToken: (t) => setAnswer((a) => a + t),
      onDone: (s) => { setSources(s); setStreaming(false); },
      onError: () => { setAnswer("Could not reach the answer service. Check the backend is running."); setStreaming(false); },
    });
  }, [question, scope, streaming]);

  function jumpTo(page) {
    setHighlight(page);
    const el = srcRefs.current[page];
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return (
    <div className="wrap" style={{ paddingTop: 28, paddingBottom: 40 }}>
      {/* scope + question bar */}
      <div style={{ display: "flex", border: "1px solid var(--rule)",
        background: "var(--paper-card)", position: "relative" }}>
        <div style={{ position: "relative", borderRight: "1px solid var(--rule)" }}>
          <button onClick={() => setScopeOpen((o) => !o)}
            style={{ height: "100%", minWidth: 200, padding: "14px 18px",
              background: "transparent", border: "none", textAlign: "left",
              display: "flex", flexDirection: "column", gap: 3 }}>
            <span className="eyebrow">SCOPE</span>
            <span style={{ fontSize: 14, fontWeight: 500, color: "var(--teal-d)" }}>
              {scope ? scope.symbol.split(".")[0] : "All companies"}
            </span>
          </button>
          {scopeOpen && (
            <div style={{ position: "absolute", top: "100%", left: -1, zIndex: 30,
              width: 340, background: "var(--paper-card)", border: "1px solid var(--rule)" }}>
              <div style={{ position: "sticky", top: 0, background: "var(--paper-card)",
                borderBottom: "1px solid var(--rule)", padding: 8 }}>
                <input autoFocus value={scopeQuery}
                  onChange={(e) => setScopeQuery(e.target.value)}
                  placeholder="Filter by name or symbol…"
                  style={{ width: "100%", padding: "8px 10px", border: "1px solid var(--rule)",
                    background: "var(--paper)", fontSize: 13, fontFamily: "var(--sans)" }} />
              </div>
              <div style={{ maxHeight: 280, overflowY: "auto" }}>
                <button onClick={() => { setScope(null); setScopeOpen(false); setScopeQuery(""); }}
                  style={dropItem}>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>All companies</span>
                  <span className="mono" style={dropSub}>CROSS-CORPUS</span>
                </button>
                {scopeResults.map((c) => (
                  <button key={c.symbol}
                    onClick={() => { setScope(c); setScopeOpen(false); setScopeQuery(""); }}
                    style={dropItem}>
                    <span style={{ fontSize: 13, fontWeight: 500 }}>{c.name}</span>
                    <span className="mono" style={dropSub}>
                      {c.symbol.split(".")[0]}{c.fiscal_year ? ` · FY${c.fiscal_year}` : ""}
                    </span>
                  </button>
                ))}
                {scopeQuery && scopeResults.length === 0 && (
                  <div style={{ padding: "14px 16px", fontSize: 12,
                    color: "color-mix(in srgb, var(--ink) 55%, transparent)" }}>
                    No company matches &ldquo;{scopeQuery}&rdquo;
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
        <input value={question} onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && ask()}
          placeholder="Ask about revenue, profit, risks, ratios…"
          style={{ flex: 1, padding: "14px 18px", border: "none", background: "transparent",
            fontSize: 16, fontFamily: "var(--sans)", color: "var(--ink)" }} />
        <button onClick={ask} disabled={streaming}
          style={{ padding: "0 26px", background: streaming
            ? "color-mix(in srgb, var(--ink) 35%, transparent)" : "var(--teal)",
            color: "var(--paper)", border: "none", fontSize: 14, fontWeight: 500 }}>
          {streaming ? "Reading…" : "Ask"}
        </button>
      </div>
      <div className="mono" style={{ fontSize: 11, marginTop: 8,
        color: "color-mix(in srgb, var(--ink) 50%, transparent)" }}>
        {scope ? `Scoped to ${scope.name}` : "Searching across all indexed reports"}
      </div>

      {/* two columns */}
      <div className="ask-grid" style={{ display: "grid",
        gridTemplateColumns: "1fr 380px", gap: 0, marginTop: 28 }}>
        <section style={{ paddingRight: 36 }}>
          {!submitted && (
            <div style={{ paddingTop: 36, maxWidth: 560 }}>
              <h1 className="serif" style={{ fontSize: 40, lineHeight: 1.12,
                color: "var(--teal-d)", marginBottom: 14 }}>
                What do you want to know?
              </h1>
              <p style={{ fontSize: 16, lineHeight: 1.6,
                color: "color-mix(in srgb, var(--ink) 75%, transparent)", marginBottom: 26 }}>
                Pick a company in the scope selector, or ask across all of them.
                Click any <span className="mono" style={{ fontSize: ".85em",
                color: "var(--gold)" }}>p.124</span> mark in the answer to open its source.
              </p>
              <div style={{ display: "flex", flexDirection: "column" }}>
                {EXAMPLES.map((ex) => (
                  <button key={ex} onClick={() => setQuestion(ex)}
                    className="rule-b" style={{ textAlign: "left", background: "transparent",
                      border: "none", padding: "11px 0", fontSize: 15, color: "var(--teal)" }}>
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}

          {submitted && (
            <div>
              <p className="eyebrow" style={{ marginBottom: 6 }}>QUESTION</p>
              <p className="serif" style={{ fontSize: 22, color: "var(--teal-d)",
                lineHeight: 1.25, marginBottom: 26 }}>{submitted}</p>
              <p className="eyebrow" style={{ marginBottom: 10 }}>ANSWER</p>
              <div style={{ fontSize: 17, lineHeight: 1.7 }}>
                {renderAnswer(answer, jumpTo)}
                {streaming && <span style={{ display: "inline-block", width: 7, height: 17,
                  background: "var(--teal)", marginLeft: 3, verticalAlign: "text-bottom",
                  opacity: .7 }} />}
              </div>
            </div>
          )}
        </section>

        {/* ledger margin */}
        <aside className="ask-ledger" style={{ borderLeft: "1px solid var(--rule)",
          paddingLeft: 28 }}>
          <p className="eyebrow" style={{ marginBottom: 16 }}>
            SOURCES{sources.length ? ` · ${sources.length}` : ""}
          </p>
          {sources.length === 0 && !streaming && (
            <p style={{ fontSize: 13, lineHeight: 1.6,
              color: "color-mix(in srgb, var(--ink) 55%, transparent)" }}>
              The audit trail appears here. Each figure links to the exact report
              section and page it came from.
            </p>
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {sources.map((s) => {
              const page = (s.pages && s.pages[0]) || 0;
              return (
                <div key={s.chunk_id} ref={(el) => (srcRefs.current[page] = el)}
                  style={{ border: `1px solid ${highlight === page ? "var(--gold)" : "var(--rule)"}`,
                    background: highlight === page ? "#fffaf0" : "var(--paper-card)",
                    padding: "14px 16px", transition: "background .25s, border-color .25s" }}>
                  <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "baseline" }}>
                    <span className="mono" style={{ fontSize: 10, letterSpacing: ".5px",
                      color: s.content_type === "table" ? "var(--teal)"
                        : "color-mix(in srgb, var(--ink) 60%, transparent)",
                      border: "1px solid var(--rule)", padding: "2px 6px" }}>
                      {(s.content_type || "").toUpperCase()}
                    </span>
                    <span className="mono" style={{ fontSize: 11, color: "var(--gold)" }}>
                      {s.pages && s.pages.length ? `p.${s.pages.join(",")}` : ""}
                    </span>
                  </div>
                  {s.company && <div className="mono" style={{ fontSize: 10, marginTop: 8,
                    color: "color-mix(in srgb, var(--ink) 55%, transparent)" }}>{s.company}</div>}
                  <div style={{ fontSize: 13, fontWeight: 500, color: "var(--teal-d)",
                    margin: "4px 0", lineHeight: 1.4 }}>{s.section_path || "—"}</div>
                  <div className="mono" style={{ fontSize: 10,
                    color: "color-mix(in srgb, var(--ink) 50%, transparent)", marginTop: 6 }}>
                    rrf {Number(s.score).toFixed(4)}
                  </div>
                </div>
              );
            })}
          </div>
        </aside>
      </div>

      <style>{`
        @media (max-width: 860px) {
          .ask-grid { grid-template-columns: 1fr !important; }
          .ask-ledger { border-left: none !important; padding-left: 0 !important;
            border-top: 1px solid var(--rule); margin-top: 28px; padding-top: 24px !important; }
          section { padding-right: 0 !important; }
        }
      `}</style>
    </div>
  );
}

const dropItem = { display: "flex", flexDirection: "column", gap: 2, width: "100%",
  textAlign: "left", padding: "11px 16px", background: "transparent", border: "none",
  borderBottom: "1px solid var(--rule)" };
const dropSub = { fontSize: 10, letterSpacing: ".5px",
  color: "color-mix(in srgb, var(--ink) 55%, transparent)" };
