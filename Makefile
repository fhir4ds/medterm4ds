.PHONY: test lint compile verify help fhir-conformance website-install website-start website-build version build publish-test publish ci

PYTHON ?= python3
PYTHONPATH ?= src

help:
	@printf '%s\n' \
	  'Targets:' \
	  '  test              Run pytest.' \
	  '  lint              Run ruff.' \
	  '  compile           Compile Python files.' \
	  '  verify            Run lint, test, and compile.' \
	  '  fhir-conformance  Run FHIR R4 conformance test suite.' \
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
	ruff check src tests

compile:
	$(PYTHON) -m py_compile $$(find src tests -name '*.py' -print)

verify: lint test compile

ci: verify

fhir-conformance:
	PYTHONPATH=src $(PYTHON) -m pytest tests/fhir_conformance/ -v --tb=short

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
