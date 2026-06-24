## Production considerations

The default `docker-compose.yml` is a **development / demo setup** — optimised for
running the whole stack on one machine with minimal friction. It is intentionally
*not* hardened for public deployment. The notes below document the gaps and how
they're addressed in `docker-compose.prod.yml`.

### What the dev compose trades off (and why it's fine for local use)

| Area | Dev setup | Why it's a risk in production |
|------|-----------|-------------------------------|
| OpenSearch auth | Security plugin **disabled** | Index is open — anyone reaching :9200 can read/delete data |
| Port exposure | Postgres, Redis, MinIO, OpenSearch all published to host | Backing stores become reachable if the host firewall allows it |
| Secrets | Default creds (`minioadmin`, `ragpass`) with fallbacks | Defaults in a public repo are an open invitation |
| API auth | None | `/ask` spends OpenAI tokens, `/index` triggers work — both abusable |
| Resources | No limits | A heavy Docling parse can starve other services |
| Redis | No persistence, no password | Queued jobs lost on restart; open to anyone on the network |
| Restart policy | Only on some services | Failed core services stay down |

### How `docker-compose.prod.yml` addresses them

- **Network isolation** — backing services use `expose:` (internal network only),
  not `ports:`. Only the frontend is published. The API sits behind a reverse
  proxy rather than being directly exposed.
- **Required secrets** — every credential uses `${VAR:?error}` syntax, so the
  stack refuses to start unless real values are provided. No insecure defaults.
- **OpenSearch security enabled** — TLS + auth, admin password from env; the API
  and worker connect over SSL with credentials.
- **Redis hardened** — password-protected, AOF persistence (jobs survive
  restarts), memory cap.
- **Resource limits + `restart: always`** on every service.
- **Pinned images** — no `:latest`.
- **Dashboards excluded** — it's an admin tool; access via SSH tunnel when needed.

### Still required outside compose for a real deployment

These are deliberately left to the deploying environment rather than baked in:

1. **Reverse proxy with TLS** (nginx / Caddy / Traefik) in front of the frontend,
   terminating HTTPS and (if the API is exposed) enforcing the API key.
2. **API authentication middleware** — the prod compose passes an `API_KEY`; the
   application/proxy must check it on `/ask` and `/index/*`.
3. **Host firewall / security group** — lock inbound to 443 (and 22 for admin).
   Never expose 5432 / 6379 / 9000 / 9200 / 5601 publicly.
4. **Secrets management** — for anything beyond a single box, use a real secrets
   store (AWS Secrets Manager, Vault) rather than a `.env` file.
5. **Backups** — Postgres and MinIO volumes hold the catalogue and raw PDFs;
   snapshot them.
6. **Observability** — centralised logs + metrics; the worker and API emit
   structured logs suitable for shipping to a log aggregator.

### Running the production stack

```bash
cp .env.prod.example .env     # fill EVERY value with strong secrets
docker compose -f docker-compose.prod.yml up -d --build
```

> This project is a portfolio / learning build. The production compose
> demonstrates the hardening approach; a real deployment would also add the
> reverse proxy, secrets manager, and monitoring described above.
