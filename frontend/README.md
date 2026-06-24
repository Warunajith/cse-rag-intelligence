# Frontend — Next.js App Router

The UI for the system. Ask questions with streaming answers and clickable page
citations, browse companies, and manage the report catalogue with on-demand
indexing. Design identity: **"Ledger"** — read every annual report like a single
ledger.

## Pages

| Route | Purpose |
|-------|---------|
| `/` | Landing + live corpus stats |
| `/ask` | Main Q&A — streaming answers, ledger-margin citations |
| `/companies` | Searchable / filterable company grid |
| `/company/[symbol]` | Report history + scoped ask |
| `/catalog` | Coverage view — per-company report grid, on-demand indexing, live status |

## Key components

- **`AskInterface`** — streams the answer over SSE (`/ask/stream`), renders
  figures in mono and `[p.X]` markers as clickable citations that scroll the
  source into the ledger margin.
- **`CoverageGrid`** — per-company report grid with Index / Retry buttons; polls
  every few seconds while jobs are active so status flips live
  (`AVAILABLE → QUEUED → PROCESSING → INDEXED`).
- **`CatalogControls`** — coverage stat strip + "Refresh CSE Catalog" (discovery).

## How it connects

`next.config.js` proxies `/api/*` to the backend, so the browser always hits
same-origin — which is what makes SSE streaming work without CORS setup. Server
components (landing, company list/detail) fetch the backend directly server-side;
client components (ask, coverage) stream / poll over `/api/*`.

```
browser → /api/* (Next proxy) → rag-api:8000
```

## Design

Deep teal-black ink on warm paper; **brass-gold reserved for figures and
citations** (the "currency" colour). Instrument Serif for display, DM Sans for
body, JetBrains Mono for every figure, symbol and citation. The signature element
is the citation system: the answer is the entry, the sources panel is the audit
trail — trust made visible, which is what a financial tool needs.

## Run

```bash
# via root compose (recommended):
docker compose up -d --build frontend     # → http://localhost:3000

# or standalone for frontend-only dev:
cp .env.local.example .env.local          # set BACKEND_URL
npm install && npm run dev
```

In Docker, `BACKEND_URL` comes from the compose environment
(`http://rag-api:8000`); `.env.local` is only needed for standalone `npm run dev`.
