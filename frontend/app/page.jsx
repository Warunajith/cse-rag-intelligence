import Link from "next/link";
import { getCorpusStats } from "../lib/api";

export default async function Home() {
  const stats = await getCorpusStats();

  return (
    <div className="wrap" style={{ paddingTop: 72, paddingBottom: 40 }}>
      <div style={{ maxWidth: 720 }}>
        <p className="eyebrow" style={{ marginBottom: 20 }}>
          COLOMBO STOCK EXCHANGE · {stats.companies} COMPANIES
        </p>
        <h1 className="serif" style={{ fontSize: 64, lineHeight: 1.05,
          color: "var(--teal-d)", marginBottom: 24 }}>
          Read every annual report like a single ledger.
        </h1>
        <p style={{ fontSize: 18, lineHeight: 1.6,
          color: "color-mix(in srgb, var(--ink) 80%, transparent)", marginBottom: 36 }}>
          Ask a question in plain language across Sri Lanka&rsquo;s listed companies.
          Every figure in the answer is traceable to the exact report and page it
          came from — the entry and its audit trail, side by side.
        </p>
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
          <Link href="/ask" style={{ padding: "13px 28px", background: "var(--teal)",
            color: "var(--paper)", fontSize: 15, fontWeight: 500 }}>
            Ask a question
          </Link>
          <Link href="/companies" style={{ padding: "13px 28px",
            border: "1px solid var(--rule)", color: "var(--teal-d)", fontSize: 15,
            fontWeight: 500 }}>
            Browse companies
          </Link>
        </div>
      </div>

      {/* corpus stat strip — figures in mono, the 'currency' treatment */}
      <div className="rule-t" style={{ marginTop: 72, paddingTop: 32, display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 32 }}>
        <Stat label="COMPANIES TRACKED" value={stats.companies} />
        <Stat label="REPORTS INDEXED" value={stats.indexed} />
        <Stat label="PENDING / FLAGGED"
          value={(stats.by_status?.PENDING || 0) + (stats.by_status?.FLAGGED || 0)} />
        <Stat label="NO REPORT FILED" value={stats.by_status?.NO_REPORT || 0} />
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <div className="mono" style={{ fontSize: 40, color: "var(--gold)", fontWeight: 500,
        lineHeight: 1 }}>{value}</div>
      <div className="eyebrow" style={{ marginTop: 8 }}>{label}</div>
    </div>
  );
}
