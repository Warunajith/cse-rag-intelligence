// API helpers. All calls go through /api/* which Next.js proxies to the backend.
// Server components call the backend directly via absolute URL; client
// components use the relative /api path (proxied, same-origin → SSE-friendly).

const SERVER_BASE = process.env.BACKEND_URL || "http://localhost:8000";

// ---- server-side fetchers (used in RSC / server components) ----
async function serverGet(path) {
  const res = await fetch(`${SERVER_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

export async function getCorpusStats() {
  try { return await serverGet("/corpus/stats"); }
  catch { return { companies: 0, indexed: 0, by_status: {} }; }
}

export async function getCompanies(params = {}) {
  const qs = new URLSearchParams(params).toString();
  try { return await serverGet(`/companies${qs ? "?" + qs : ""}`); }
  catch { return { companies: [], count: 0 }; }
}

export async function getCompany(symbol) {
  return serverGet(`/companies/${encodeURIComponent(symbol)}`);
}

// ---- client-side: streaming ask over POST + SSE ----
// EventSource only supports GET, so we read the POST response body as a stream
// and parse SSE frames manually. onToken(text), onDone(sources), onError(e).
export async function askStream(body, { onToken, onDone, onError }) {
  try {
    const res = await fetch("/api/ask/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) throw new Error(`stream → ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line
      const frames = buffer.split("\n\n");
      buffer = frames.pop(); // keep incomplete frame
      for (const frame of frames) {
        const evMatch = frame.match(/^event:\s*(.+)$/m);
        const dataMatch = frame.match(/^data:\s*(.+)$/m);
        if (!dataMatch) continue;
        const event = evMatch ? evMatch[1].trim() : "message";
        const data = JSON.parse(dataMatch[1]);
        if (event === "token") onToken?.(data.text);
        else if (event === "done") onDone?.(data.sources || []);
      }
    }
  } catch (e) {
    onError?.(e);
  }
}

// client-side company search (debounced in the component)
export async function searchCompanies(q) {
  const res = await fetch(`/api/companies?q=${encodeURIComponent(q)}`);
  if (!res.ok) return { companies: [] };
  return res.json();
}

// ---- catalog / coverage (Postgres-backed, via /api proxy) ----
export async function getCatalogStats() {
  try { return await serverGet("/catalog/stats"); }
  catch { return { companies: 0, reports_available_total: 0, indexed: 0,
                   available: 0, processing: 0, flagged: 0, failed: 0 }; }
}

// client-side: live coverage stats (polled)
export async function fetchCatalogStats() {
  const res = await fetch("/api/catalog/stats");
  if (!res.ok) throw new Error("catalog stats");
  return res.json();
}

// client-side: one company's full report catalog with statuses
export async function fetchCompanyCatalog(symbol) {
  const res = await fetch(`/api/catalog/company/${encodeURIComponent(symbol)}`);
  if (!res.ok) throw new Error("company catalog");
  return res.json();
}

// trigger a discovery pass
export async function refreshCatalog(body = {}) {
  const res = await fetch("/api/catalog/refresh", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
  return res.json();
}

// enqueue indexing for one report
export async function indexReport(doc_id) {
  const res = await fetch("/api/index/report", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_id }) });
  return res.json();
}

// enqueue indexing for all available reports of a company
export async function indexCompany(symbol) {
  const res = await fetch("/api/index/company", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol }) });
  return res.json();
}
