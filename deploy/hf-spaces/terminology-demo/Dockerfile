# terminology-demo — HF Docker Space for medterm4ds operations.
#
# Build context: THIS DIRECTORY (deploy/hf-spaces/terminology-demo, or the
# root of the HF Space repo, which contains the same files):
#   docker build -t terminology-demo deploy/hf-spaces/terminology-demo
# medterm4ds is pip-installed from the pinned public GitHub commit, so the
# image needs no repo sources in the build context.
#
# Runtime volumes (read-only):
#   /data/lookup.duckdb      CPT-excluded lookup DB (deploy/scripts/export_lookup_db_public.py)
#   /data/cache              terminology release root, containing
#                            terminology/2026.10.31.1/{canonical,lexical,semantic,model}

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 libffi-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir \
        https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

# medterm4ds from the PUBLIC GitHub repo at the pinned dev commit (the
# exact commit this Space was built against; PyPI also carries 0.0.8 but
# the pin guarantees the AMUX-18/20 lexical fixes are included). Installed
# from the release tarball so the image needs no git binary.
RUN pip install --no-cache-dir \
        "medterm4ds[fhir,search,extraction] @ https://github.com/fhir4ds/medterm4ds/archive/72503ab4a721.tar.gz"

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

# Data layout: volume mounts when attached (preferred); otherwise fetched
# at container start from the private lookup dataset + public terminology
# repo (see startup_data.py) — never baked into the public image.
ENV MEDTERM4DS_DB=/data/lookup.duckdb \
    MEDTERM4DS_CACHE_DIR=/data/cache \
    MEDTERM4DS_MEMORY_PROFILE=low \
    DEMO_DB_THREADS=2 \
    DEMO_PROVISION_DATA=true

COPY app.py /app/app.py
COPY startup_data.py /app/startup_data.py

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD curl -sf http://localhost:7860/health || exit 1

CMD ["python", "app.py"]
