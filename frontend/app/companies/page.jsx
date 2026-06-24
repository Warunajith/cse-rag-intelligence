import CompanyBrowser from "../../components/CompanyBrowser";
import { getCompanies } from "../../lib/api";

export const metadata = { title: "Companies — Ledger" };

export default async function CompaniesPage() {
  const { companies } = await getCompanies();
  return <CompanyBrowser companies={companies} />;
}
