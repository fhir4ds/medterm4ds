# terminology-demo — HF Docker Space for medterm4ds operations.
#
# Build context: REPO ROOT (so src/medterm4ds is available), e.g. from the
# repo root:
#   docker build -f deploy/hf-spaces/terminology-demo/Dockerfile -t terminology-demo .
#
# Runtime volumes (read-only):
#   /data/lookup.duckdb      CPT-excluded lookup DB (deploy/scripts/export_lookup_db.py)
#   /data/cache              terminology release root, containing
#                            terminology/2026.10.31.1/{canonical,lexical,semantic,model}

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 libffi-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY deploy/hf-spaces/terminology-demo/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir \
        https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

# medterm4ds from the repo tree (PYTHONPATH, matching the fhir-server Space)
COPY src /app/src
COPY pyproject.toml /app/pyproject.toml
ENV PYTHONPATH=/app/src

# Bake the pinned GLiNER NER weights into the image so /extract needs no
# runtime download (pinned revision from services/extraction.py).
RUN python - <<'EOF'
from huggingface_hub import snapshot_download
from medterm4ds.services.extraction import DEFAULT_NER_MODEL, DEFAULT_NER_MODEL_REVISION
snapshot_download(DEFAULT_NER_MODEL, revision=DEFAULT_NER_MODEL_REVISION, local_dir='/app/models/gliner')
print('GLiNER weights baked to /app/models/gliner')
EOF
ENV MEDTERM4DS_NER_MODEL=/app/models/gliner \
    MEDTERM4DS_NER_MODEL_REVISION= \
    HF_HUB_OFFLINE=0

# Data layout (mounted read-only by the platform)
ENV MEDTERM4DS_DB=/data/lookup.duckdb \
    MEDTERM4DS_CACHE_DIR=/data/cache \
    MEDTERM4DS_MEMORY_PROFILE=low \
    DEMO_DB_THREADS=2

COPY deploy/hf-spaces/terminology-demo/app.py /app/app.py

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD curl -sf http://localhost:7860/health || exit 1

CMD ["python", "app.py"]
