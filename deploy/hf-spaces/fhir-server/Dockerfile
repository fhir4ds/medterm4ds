FROM python:3.11-slim

WORKDIR /app

# OCI image labels — provenance metadata for registries / HF Spaces.
LABEL org.opencontainers.image.title="medterm4ds FHIR Terminology Server" \
      org.opencontainers.image.source="https://github.com/fhir4ds/medterm4ds" \
      org.opencontainers.image.description="UMLS-backed FHIR R4 terminology server with $lookup, $validate-code, $translate, $subsumes, $expand, $closure, $search, $extract." \
      org.opencontainers.image.licenses="GPL-3.0-only"

# System deps for DuckDB + faiss
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxext6 libxrender-dev libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY deploy/hf-spaces/fhir-server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app + medterm4ds source (build context = repo root)
COPY deploy/hf-spaces/fhir-server/app.py .
COPY src/ ./src/
COPY scripts/ ./scripts/

# Use PYTHONPATH instead of pip install (avoids build backend dependency)
ENV PYTHONPATH=/app/src:/app/scripts
# Unbuffered stdout/stderr so print() and log lines show up in `docker logs`
# immediately, instead of being buffered until the buffer fills or process exits.
ENV PYTHONUNBUFFERED=1

# HF Spaces persistent storage (caches UMLS build + HF downloads)
ENV MEDTERM4DS_DATA_DIR=/data
RUN mkdir -p /data

# Note: deliberately NOT switching to a non-root USER. Doing so requires
# volume-ownership migration (DuckDB writes to /data/lookup.duckdb.tmp/),
# which breaks existing persistent volumes on rebuild. For a localhost
# sidecar / HF Spaces deployment where the platform provides isolation,
# root-inside-container is acceptable. For production hardening, front the
# deployment with an authenticating proxy and run the image with
# `--user 1000:1000` plus a chown entrypoint.

# HF Spaces listens on 7860
EXPOSE 7860

# Health check: hit the lightweight /health endpoint (no executor / DB / model
# touch, returns in <5ms). 30s interval catches a stuck uvicorn without
# false-failing on the 9-min first-start UMLS build.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -sf http://127.0.0.1:7860/health || exit 1

# Required: UMLS_API_KEY (builds lookup.duckdb from NLM RRF)
# Optional: HF_TOKEN (only needed for private datasets or writing to HF)
CMD ["python", "app.py"]
