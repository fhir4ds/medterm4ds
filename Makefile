.PHONY: test compile verify help benchmark-smoke parity-smoke acceptance-smoke api-smoke mcp-smoke

PYTHON ?= python3
PYTHONPATH ?= src:/mnt/d/medterm/src
UMLS_DB ?= /mnt/d/medterm/data/umls_local.duckdb
SMOKE_SOURCES ?= ICD10CM,CVX

help:
	@printf '%s\n' \
	  'Targets:' \
	  '  test              Run pytest.' \
	  '  compile           Compile Python files.' \
	  '  verify            Run test and compile.' \
	  '  benchmark-smoke   Run a small LocalLite benchmark against UMLS_DB.' \
	  '  parity-smoke      Compare a small sample against /mnt/d/medterm.' \
	  '  acceptance-smoke  Exercise CLI JSONL resume, CSV, and FHIR output.' \
	  '  api-smoke         Import the API app factory.' \
	  '  mcp-smoke         Import the MCP server factory.'

test:
	PYTHONPATH=$(PYTHONPATH) pytest -q

compile:
	$(PYTHON) -m py_compile $$(find src scripts tests -name '*.py' -print)

verify: test compile

benchmark-smoke:
	PYTHONPATH=src $(PYTHON) scripts/benchmark_locallite_patient_friendly.py \
	  --db $(UMLS_DB) \
	  --prepare-cache \
	  --no-cache-indexes \
	  --memory-limit 1GB \
	  --sizes 1000 \
	  --sample-mode balanced

parity-smoke:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/compare_patient_friendly_parity.py \
	  --db $(UMLS_DB) \
	  --medterm-path /mnt/d/medterm \
	  --sources ICD10CM \
	  --per-source 1 \
	  --no-prepare-cache \
	  --output-json parity_patient_friendly_smoke.json \
	  --output-md parity_patient_friendly_smoke.md

acceptance-smoke:
	PYTHONPATH=src $(PYTHON) scripts/run_cli_acceptance.py \
	  --db $(UMLS_DB) \
	  --sources $(SMOKE_SOURCES) \
	  --limit 2 \
	  --partial-limit 1 \
	  --fhir-limit 1 \
	  --work-dir acceptance_outputs \
	  --output-json acceptance_patient_friendly_smoke.json

api-smoke:
	PYTHONPATH=src $(PYTHON) -c "from medterm4ds.apps.api import create_app; print(create_app)"

mcp-smoke:
	PYTHONPATH=src $(PYTHON) -c "from medterm4ds.apps.mcp import create_mcp_server; print(create_mcp_server)"
