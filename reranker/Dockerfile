# Reranker service. Build arg WITH_LOCAL controls whether the heavy torch stack
# is installed:
#   docker build --build-arg WITH_LOCAL=true  ...   -> local + hosted backends (big)
#   docker build --build-arg WITH_LOCAL=false ...   -> hosted-only, lean image
# Default true so the self-hosted cross-encoder works out of the box.
FROM python:3.12-slim

ARG WITH_LOCAL=true

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app

# Base deps — tiny, needed by all backends
RUN uv pip install --system --no-cache \
    fastapi==0.115.* \
    "uvicorn[standard]==0.34.*" \
    pydantic==2.* \
    requests==2.* \
    cohere==5.*

# Heavy local-backend deps only when requested
RUN if [ "$WITH_LOCAL" = "true" ]; then \
      uv pip install --system --no-cache sentence-transformers==3.* ; \
    fi

COPY reranker_service.py ./

RUN useradd -m appuser
USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; \
    sys.exit(0 if urllib.request.urlopen('http://localhost:8001/health').status==200 else 1)"

CMD ["uvicorn", "reranker_service:app", "--host", "0.0.0.0", "--port", "8001"]
