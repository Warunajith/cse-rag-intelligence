# Serving layer — FastAPI + uvicorn, built with uv. LEAN: no ML deps here.
# Reranking is a SEPARATE service (see reranker_service/), called over HTTP,
# so this image stays small and fast to rebuild.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app

COPY pyproject.toml ./
RUN uv pip install --system --no-cache \
    fastapi==0.115.* \
    "uvicorn[standard]==0.34.*" \
    opensearch-py==2.* \
    openai==1.* \
    pydantic==2.* \
    requests==2.* \
    redis==5.* \
    psycopg2-binary==2.* 
    

COPY rag_service.py frontend_api.py catalog_api.py  ./

RUN useradd -m appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
    sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health/live').status==200 else 1)"

CMD ["uvicorn", "rag_service:app", "--host", "0.0.0.0", "--port", "8000"]
