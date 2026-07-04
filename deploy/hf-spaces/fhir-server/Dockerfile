FROM python:3.11-slim

WORKDIR /app

# System deps for DuckDB + faiss
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxext6 libxrender-dev libgomp1 \
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

# HF Spaces listens on 7860
EXPOSE 7860

# Required: UMLS_API_KEY (builds lookup.duckdb from NLM RRF)
# Optional: HF_TOKEN (only needed for private datasets or writing to HF)
CMD ["python", "app.py"]
