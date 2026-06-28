# Local Docker testing for the FHIR terminology server

# Build the image
docker build -t fhir4ds-fhir .

# Run with local data (skip HF download)
docker run -p 7860:7860 \
  -e MEDTERM4DS_DB=/data/lookup.duckdb \
  -e MEDTERM4DS_FHIR4PX_BASELINE=/data/fhir4px \
  -e MEDTERM4DS_SEARCH_INDEX_DIR=/data/bm25 \
  -v /tmp/lookup.duckdb:/data/lookup.duckdb:ro \
  -v /mnt/d/medterm4ds/reports/fhir4px:/data/fhir4px:ro \
  -v /mnt/d/fhir4px-model/dist/naming_bm25:/data/bm25:ro \
  fhir4ds-fhir

# Test
curl http://localhost:7860/fhir/metadata
curl "http://localhost:7860/fhir/CodeSystem/\$lookup?system=http://snomed.info/sct&code=44054006"

# Run conformance suite against the container
PYTHONPATH=src pytest tests/fhir_conformance/ -v --base-url http://localhost:7860
