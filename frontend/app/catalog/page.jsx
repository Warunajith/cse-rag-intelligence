import CatalogControls from "../../components/CatalogControls";
import CatalogCompanyPicker from "../../components/CatalogCompanyPicker";

export const metadata = { title: "Coverage — Ledger" };

export default function CatalogPage() {
  return (
    <div className="wrap" style={{ paddingTop: 36, paddingBottom: 40 }}>
      <CatalogControls />
      <CatalogCompanyPicker />
    </div>
  );
}
