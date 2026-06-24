import "./globals.css";
import Masthead from "../components/Masthead";

export const metadata = {
  title: "Ledger — CSE Annual Report Intelligence",
  description: "Ask questions across Colombo Stock Exchange annual reports. "
    + "Every figure traceable to its source page.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <Masthead />
        <main style={{ minHeight: "calc(100vh - 140px)" }}>{children}</main>
        <footer className="rule-t" style={{ marginTop: 48 }}>
          <div className="wrap" style={{ padding: "20px 32px", display: "flex",
            justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
            <span className="eyebrow">LEDGER · CSE ANNUAL REPORT INTELLIGENCE</span>
            <span className="eyebrow">HYBRID RETRIEVAL · CROSS-ENCODER RERANK · GROUNDED ANSWERS</span>
          </div>
        </footer>
      </body>
    </html>
  );
}
