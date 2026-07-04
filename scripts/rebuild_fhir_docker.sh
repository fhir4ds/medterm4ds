#!/usr/bin/env bash
# Rebuild the medterm4ds FHIR server Docker image and restart the container.
#
# Usage:
#   scripts/rebuild_fhir_docker.sh
#
# Required env: UMLS_API_KEY (for the in-container lookup.duckdb build)
# Optional env: HF_TOKEN (for private HF datasets), FHIR_IMAGE_TAG (default: latest)
#
# The container listens on host port 8001 -> container 7860 (HF Spaces convention).

set -euo pipefail

IMAGE_NAME="medterm4ds-fhir"
IMAGE_TAG="${FHIR_IMAGE_TAG:-latest}"
CONTAINER_NAME="medterm4ds-fhir"
HOST_PORT="${FHIR_HOST_PORT:-8001}"
CONTAINER_PORT=7860
# Volume name is the legacy fhir4ds-data; renaming would lose the cached
# lookup.duckdb (8-min rebuild). Keep the name as-is.
DATA_VOLUME="fhir4ds-data"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKERFILE="$REPO_ROOT/deploy/hf-spaces/fhir-server/Dockerfile"

# --- preflight ---
if [[ -z "${UMLS_API_KEY:-}" ]]; then
    echo "ERROR: UMLS_API_KEY is not set in your environment." >&2
    echo "       Get one at https://uts.nlm.nih.gov/ and export UMLS_API_KEY=..." >&2
    exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is not installed or not on PATH." >&2
    exit 1
fi

# Strip stray \r from env values — .env files saved with CRLF line endings
# (common on WSL/Windows) otherwise leak \r into the value and break HTTP
# headers downstream (we saw this with HF_TOKEN → "Illegal header value").
UMLS_API_KEY="${UMLS_API_KEY%$'\r'}"
HF_TOKEN="${HF_TOKEN%$'\r'}"

# --- build ---
echo "[1/4] Building image ${IMAGE_NAME}:${IMAGE_TAG}..."
docker build -f "$DOCKERFILE" -t "${IMAGE_NAME}:${IMAGE_TAG}" "$REPO_ROOT"

# --- teardown old container ---
if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    echo "[2/4] Stopping + removing existing container ${CONTAINER_NAME}..."
    docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
    docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
else
    echo "[2/4] No existing container to remove."
fi

# --- run ---
echo "[3/4] Starting new container on host port ${HOST_PORT}..."
DOCKER_RUN_ARGS=(
    -d
    --name "$CONTAINER_NAME"
    -p "${HOST_PORT}:${CONTAINER_PORT}"
    -e UMLS_API_KEY="$UMLS_API_KEY"
    -v "${DATA_VOLUME}:/data"
)
if [[ -n "${HF_TOKEN:-}" ]]; then
    DOCKER_RUN_ARGS+=(-e HF_TOKEN="$HF_TOKEN")
fi
# Forward optional medterm4ds env vars if set in the shell
for var in MEDTERM4DS_SEARCH_INDEX_DIR MEDTERM4DS_EMBEDDING_MODEL_DIR MEDTERM4DS_FHIR4PX_BASELINE; do
    if [[ -n "${!var:-}" ]]; then
        DOCKER_RUN_ARGS+=(-e "$var=${!var}")
    fi
done

docker run "${DOCKER_RUN_ARGS[@]}" "${IMAGE_NAME}:${IMAGE_TAG}"

# --- health probe ---
# Cold start builds lookup.duckdb from UMLS RRF (~8 min) + downloads ~3 GB
# from HF. Warm start (cached volume) is seconds. Default timeout is 15 min.
WAIT_TIMEOUT="${FHIR_STARTUP_TIMEOUT:-900}"
echo "[4/4] Waiting for /health to come up (up to ${WAIT_TIMEOUT}s; first run builds lookup.duckdb from UMLS RRF + downloads ~3 GB from HF)..."
HEALTH_URL="http://127.0.0.1:${HOST_PORT}/health"
start=$(date +%s)
last_log_line=""
while true; do
    elapsed=$(( $(date +%s) - start ))
    if curl -sf --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
        echo "OK: /health returned 200 after ${elapsed}s"
        echo
        curl -s "$HEALTH_URL" | python3 -m json.tool 2>/dev/null || curl -s "$HEALTH_URL"
        echo
        echo "Container logs (last 15 lines):"
        docker logs --tail 15 "$CONTAINER_NAME" 2>&1
        echo
        echo "Done. Server is live at http://127.0.0.1:${HOST_PORT}/fhir/metadata"
        exit 0
    fi
    if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
        echo "ERROR: container exited during startup. Logs:" >&2
        docker logs "$CONTAINER_NAME" 2>&1 >&2
        exit 1
    fi
    if [[ "$elapsed" -gt "$WAIT_TIMEOUT" ]]; then
        echo "ERROR: /health did not come up within ${WAIT_TIMEOUT}s. Container logs:" >&2
        docker logs --tail 50 "$CONTAINER_NAME" 2>&1 >&2
        exit 1
    fi
    # Stream the latest log line so the user can see progress (UMLS download,
    # RRF extraction, HF download, etc.) instead of a silent wait.
    current_log=$(docker logs --tail 1 "$CONTAINER_NAME" 2>&1 | head -1)
    if [[ -n "$current_log" && "$current_log" != "$last_log_line" ]]; then
        echo "  [${elapsed}s] $current_log"
        last_log_line="$current_log"
    fi
    sleep 2
done
