.PHONY: test lint compile verify help benchmark-smoke acceptance-smoke notebook-smoke fhir-conformance wheel-install-smoke real-data-smoke bulk-validation-smoke mapping-quality-smoke api-smoke mcp-smoke website-install website-start website-build version build publish-test publish ci

PYTHON ?= python3
PYTHONPATH ?= src
UMLS_DB ?= /mnt/d/medterm4ds/data/umls_current.duckdb
SMOKE_SOURCES ?= ICD10CM,CVX

help:
	@printf '%s\n' \
	  'Targets:' \
	  '  test              Run pytest.' \
	  '  lint              Run ruff.' \
	  '  compile           Compile Python files.' \
	  '  verify            Run lint, test, and compile.' \
	  '  benchmark-smoke   Run a small local DuckDB benchmark against UMLS_DB.' \
	  '  acceptance-smoke  Exercise CLI JSONL resume, CSV, and FHIR output.' \
	  '  notebook-smoke    Execute example notebooks against a synthetic DuckDB fixture.' \
	  '  wheel-install-smoke Build/check package and import it from a fresh venv.' \
	  '  real-data-smoke   Exercise lookup, mapping, hierarchy, and discovery against UMLS_DB.' \
	  '  bulk-validation-smoke Run bounded bulk mapping and patient-friendly workflows.' \
	  '  mapping-quality-smoke Sample crosswalk mappings and write review flags.' \
	  '  api-smoke         Import the API app factory.' \
	  '  mcp-smoke         Import the MCP server factory.' \
	  '  website-install   Install Docusaurus website dependencies.' \
	  '  website-start     Start the Docusaurus website in WSL polling mode.' \
	  '  website-build     Build the Docusaurus website.' \
	  '  version           Show the Hatch package version.' \
	  '  build             Build wheel and sdist with Hatch.' \
	  '  publish-test      Build and upload to TestPyPI with Hatch.' \
	  '  publish           Build and upload to PyPI with Hatch.'

test:
	PYTHONPATH=$(PYTHONPATH) pytest -q

lint:
	ruff check src tests scripts

compile:
	$(PYTHON) -m py_compile $$(find src scripts tests -name '*.py' -print)

verify: lint test compile

ci: verify notebook-smoke wheel-install-smoke

acceptance-smoke:
	PYTHONPATH=src $(PYTHON) scripts/run_cli_acceptance.py \
	  --db $(UMLS_DB) \
	  --sources $(SMOKE_SOURCES) \
	  --limit 2 \
	  --partial-limit 1 \
	  --fhir-limit 1 \
	  --work-dir acceptance_outputs \
	  --output-json acceptance_patient_friendly_smoke.json

notebook-smoke:
	PYTHONPATH=src:scripts $(PYTHON) scripts/run_notebook_smoke.py \
	  --output-json notebook_smoke.json

fhir-conformance:
	PYTHONPATH=src $(PYTHON) -m pytest tests/fhir_conformance/ -v --tb=short

wheel-install-smoke:
	$(PYTHON) scripts/build_package.py --skip-verify
	$(PYTHON) scripts/test_wheel_install.py

real-data-smoke:
	PYTHONPATH=src $(PYTHON) scripts/run_real_data_smoke.py \
	  --db $(UMLS_DB) \
	  --source ICD10CM \
	  --target-source SNOMEDCT_US \
	  --memory-profile low \
	  --output-json real_data_smoke.json

bulk-validation-smoke:
	PYTHONPATH=src $(PYTHON) scripts/run_bulk_validation.py \
	  --db $(UMLS_DB) \
	  --work-dir validation_outputs \
	  --output-json bulk_validation_report.json \
	  --limit 100 \
	  --batch-size 50 \
	  --memory-profile low

mapping-quality-smoke:
	PYTHONPATH=src $(PYTHON) scripts/review_mapping_quality.py \
	  --db $(UMLS_DB) \
	  --per-source 50 \
	  --output-json reports/quality/mapping_quality_report.json \
	  --output-csv reports/quality/mapping_review_cases.csv

api-smoke:
	PYTHONPATH=src $(PYTHON) -c "from medterm4ds.apps.api import create_app; print(create_app)"

mcp-smoke:
	PYTHONPATH=src $(PYTHON) -c "from medterm4ds.apps.mcp import create_mcp_server; print(create_mcp_server)"

website-install:
	cd web/website && npm install

website-start:
	cd web/website && npm run start:wsl

website-build:
	cd web/website && npm run build

version:
	hatch version

build:
	hatch build

publish-test:
	hatch build
	hatch publish -r testpypi

publish:
	hatch build
	hatch publish
