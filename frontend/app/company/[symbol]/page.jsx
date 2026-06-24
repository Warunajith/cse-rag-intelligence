import Link from "next/link";
import { getCompany } from "../../../lib/api";
import AskInterface from "../../../components/AskInterface";

export default async function CompanyDetail({ params }) {
  const symbol = decodeURIComponent(params.symbol);
  let data;
  try {
    data = await getCompany(symbol);
  } catch {
    return (
      <div className="wrap" style={{ paddingTop: 60 }}>
        <p style={{ fontSize: 16 }}>Company not found.{" "}
          <Link href="/companies" style={{ color: "var(--teal)" }}>Back to browse</Link>
        </p>
      </div>
    );
  }
  const { company, documents } = data;
  const latest = documents.find((d) => d.status === "INDEXED") || documents[0];

  return (
    <div className="wrap" style={{ paddingTop: 36, paddingBottom: 20 }}>
      <Link href="/companies" className="mono" style={{ fontSize: 12,
        color: "var(--teal)" }}>&larr; All companies</Link>

      <div style={{ marginTop: 18, display: "flex", alignItems: "baseline",
        gap: 16, flexWrap: "wrap" }}>
        <span className="mono" style={{ fontSize: 16, color: "var(--gold)",
          fontWeight: 500 }}>{company.symbol.split(".")[0]}</span>
        <h1 className="serif" style={{ fontSize: 40, color: "var(--teal-d)",
          lineHeight: 1.1 }}>{company.name}</h1>
      </div>

      {/* report history */}
      <div className="rule-t" style={{ marginTop: 28, paddingTop: 20 }}>
        <p className="eyebrow" style={{ marginBottom: 14 }}>REPORT HISTORY</p>
        {documents.length === 0 && (
          <p style={{ fontSize: 14, color: "color-mix(in srgb, var(--ink) 60%, transparent)" }}>
            No reports ingested yet for this company.
          </p>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
          {documents.map((d) => (
            <div key={d.doc_id} className="rule-b" style={{ display: "flex",
              justifyContent: "space-between", alignItems: "center", padding: "12px 0",
              gap: 12, flexWrap: "wrap" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
                <span className="mono" style={{ fontSize: 14, color: "var(--teal-d)" }}>
                  {d.fiscal_year ? `FY${d.fiscal_year}` : "—"}
                </span>
                <span className={`badge badge-${(d.status || "pending").toLowerCase()}`}>
                  {d.status}
                </span>
              </div>
              <span className="mono" style={{ fontSize: 11,
                color: "color-mix(in srgb, var(--ink) 55%, transparent)" }}>
                {d.pages ? `${d.pages}p · ` : ""}{d.tables ? `${d.tables} tables · ` : ""}
                {d.chunks ? `${d.chunks} chunks` : ""}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* scoped ask */}
      {latest && latest.status === "INDEXED" && (
        <div style={{ marginTop: 40 }}>
          <div className="wrap" style={{ padding: 0 }}>
            <p className="eyebrow" style={{ marginBottom: 4 }}>
              ASK ABOUT {company.symbol.split(".")[0]}
            </p>
          </div>
          <AskInterface initialScope={{ symbol: company.symbol, name: company.name,
            fiscal_year: latest.fiscal_year }} />
        </div>
      )}
    </div>
  );
}
