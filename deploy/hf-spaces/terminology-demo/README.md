---
title: terminology-demo
emoji: 🔎
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: other
---

# terminology-demo

Public demo service backing the [medterm4ds docs example](https://terminology.fhir4ds.com/docs/examples/recipes/terminology-search).
Serves medterm4ds terminology operations over HTTP on **free-tier hardware
(2 vCPU / 16 GB)**; the Space **sleeps after 48 h idle** and takes up to
~60 s to wake. It is a demo, not an uptime-bearing API.

Live at: **https://fhir4ds-terminology-demo.hf.space**

## Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | readiness + version |
| `/capabilities` | GET | endpoints, search modes, code sources, limits |
| `/search` | POST | `{query, mode: lexical\|semantic\|canonical\|hybrid, count?, sources?, result_types?}` |
| `/extract` | POST | `{text}` → concepts + annotated text (GLiNER + medspaCy NER, resolved to codes) |
| `/map` | POST | `{codes: [{source, code}], target_sources: [...]}` → cross-system mappings |
| `/translate` | POST | `{codes: [{source, code}]}` → patient-friendly names |
| `/lookup` | POST | `{codes: [{source, code}]}` → best atom per code |
| `/hierarchy` | POST | `{codes, direction: parents\|children\|ancestors\|descendants, max_depth?, limit?}` |

## Access control

No API key in the browser (none to leak). Instead:

- **Origin allow-list** — requests carrying an `Origin` header are refused
  with 403 unless the origin is in `DEMO_ALLOWED_ORIGINS`. Server-side
  clients (curl, no Origin) are not affected.
- **Per-IP rate limit** — 30 requests/minute sliding window per client IP
  (`DEMO_RATE_LIMIT_PER_MINUTE`), 429 + `Retry-After` beyond. `/health` is
  exempt.

## Data (mounted read-only volumes — NOT baked into the image)

The image contains code only. Two volumes are attached to the Space and
mounted read-only:

1. `/data/lookup.duckdb` — ~217 MB CPT-excluded filtered UMLS DuckDB
   (7 sources: SNOMED CT, ICD-10-CM, ICD-10-PCS, RxNorm, LOINC, HCPCS, CVX)
   built from UMLS 2025AB RRF with `deploy/scripts/export_lookup_db.py`
   and verified with `deploy/scripts/verify_lookup_db.py`
   (asserts zero CPT rows/edges).
2. `/data/cache/terminology/2026.10.31.1/` — the `fhir4ds/terminology`
   release 2026.10.31.1 (`without-cpt` variant): `canonical/`, `lexical/`,
   `semantic/` (anchors.faiss 837 MB + per-domain FAISS) and
   `model/esp_468d01ebbab03648/` (SapBERT).

The SapBERT weights and FAISS indexes are loaded from this mount; only the
GLiNER NER weights (~200 MB) are baked into the image at build time.

Note: the mount must contain the **real release directory** (with
`manifest.json` at its root), not symlinks to elsewhere — symlinks pointing
outside the volume resolve to nothing inside the container, the release check
then fails, and the app falls back to trying to download artifacts.

## Licence compliance — IMPORTANT

### CPT exclusion (AMA)

CPT is **excluded entirely** from every artifact this Space serves. The AMA
license (category 3) forbids use of CPT content "in publicly accessible
systems". Both the terminology release (`without-cpt` variant, attested in
its `manifest.json`) and `lookup.duckdb` (verified: zero `SAB='CPT'` rows,
zero edges touching CPT atoms) contain no CPT codes, names, or derived text.

### Required notices (UMLS Metathesaurus license)

This product uses publicly available data courtesy of the U.S. National
Library of Medicine (NLM), National Institutes of Health, Department of
Health and Human Services. Use of the UMLS Metathesaurus is subject to the
UMLS license terms: https://www.nlm.nih.gov/research/umls/licensedcontent/umlslicense.html

- **LOINC(R)** — This material contains content from LOINC(R) Regenstrief.
  Use of LOINC is subject to the LOINC license: https://loinc.org/license/
- **UCUM** — This product includes all or a portion of the UCUM table,
  UCUM codes, and UCUM definitions and is copyright ©1999-2026 Regenstrief
  Institute, Inc. and the Unified Medical Language System (UMLS), and is
  subject to the UCUM license: https://unitsofmeasure.org/license
  (UCUM is distributed with the UMLS Metathesaurus.)
- **SNOMED CT(R)** — This material contains SNOMED CT(R) content from
  SNOMED International. Use of SNOMED CT is subject to SNOMED CT license
  terms: https://www.snomed.org/get-snomed
- **RxNorm** — This product uses publicly available data courtesy of the
  U.S. National Library of Medicine: https://www.nlm.nih.gov/research/umls/rxnorm/
- **ICD-10-CM / ICD-10-PCS / HCPCS** — U.S. Centers for Medicare &
  Medicaid Services; public domain.

### NLM notification (required before distributing UMLS-derived data)

The UMLS license requires notifying NLM before distributing any Metathesaurus
-derived content. Draft email (send from the licence holder's account,
i.e. the account that accepted the UMLS license):

> **To:** umlslicense@nlm.nih.gov
> **Subject:** Notification of distribution of UMLS Metathesaurus derived content
>
> Dear NLM UMLS licensing team,
>
> In accordance with the UMLS Metathesaurus license agreement, we are
> notifying you that we distribute content derived from the UMLS
> Metathesaurus (2025AB) as part of a public demonstration service:
>
> - Service: terminology-demo, a Hugging Face Docker Space at
>   https://fhir4ds-terminology-demo.hf.space (source: https://huggingface.co/spaces/fhir4ds/terminology-demo)
> - Content distributed: a filtered subset (7 sources: SNOMED CT,
>   ICD-10-CM, ICD-10-PCS, RxNorm, LOINC, HCPCS, CVX; active atoms,
>   hierarchy edges, RxNorm NDC attributes) served via a search/lookup API
>   as read-only DuckDB databases mounted into the service. CPT is excluded.
> - The required license notices for LOINC, UCUM, SNOMED CT, RxNorm and the
>   Metathesaurus attribution are displayed in the Space README at the URL
>   above.
> - Our account under the UMLS license: [LICENSEE ACCOUNT/EMAIL]
>
> Please let us know if any additional steps are required.
>
> Regards,
> [NAME], fhir4ds project

Send this **before** the Space is made public.

## Deploying (Joel's runbook)

Prerequisites: `pip install "huggingface_hub[cli]"`, `hf auth login`
(Joel's account, with write access to the `fhir4ds` namespace).

```bash
# 1. Create the Space (Docker SDK)
hf api create-space fhir4ds/terminology-demo --sdk docker

# 2. Secrets / variables (allow-list = docs site + local dev)
hf api add-space-secret  fhir4ds/terminology-demo DEMO_ALLOWED_ORIGINS "https://terminology.fhir4ds.com,http://localhost:3000"
hf api add-space-variable fhir4ds/terminology-demo DEMO_RATE_LIMIT_PER_MINUTE "30"

# 3. Push the Space code (from the repo root; the Space repo needs
#    app.py, Dockerfile, requirements.txt at its root)
cd deploy/hf-spaces/terminology-demo
hf upload-space-repo . fhir4ds/terminology-demo --commit-message "terminology-demo initial deploy"

# 4. Attach volumes (Settings → Volumes, or the API) — read-only mounts:
#      /data/lookup.duckdb   <- built artifact from deploy/scripts/export_lookup_db.py
#      /data/cache           <- fhir4ds/terminology release 2026.10.31.1
#    If the platform does not offer read-only mounts, upload the release
#    and lookup DB as private dataset repos and copy them into /data at
#    startup instead (see deploy/hf-spaces/terminology-demo/README deploy notes).

# 5. Verify after the build finishes:
curl -s https://fhir4ds-terminology-demo.hf.space/health
curl -s https://fhir4ds-terminology-demo.hf.space/capabilities
curl -s -X POST https://fhir4ds-terminology-demo.hf.space/search \
     -H 'Content-Type: application/json' \
     -d '{"query": "myocardial infarction", "mode": "lexical", "count": 3}'
```

Hardware: free tier (2 vCPU / 16 GB) is sufficient — everything runs on
CPU (`MEDTERM4DS_MEMORY_PROFILE=low`, DuckDB threads=2). Cold start after
idle sleep: up to ~60 s (health-check start-period is 120 s).

## Building the data artifacts (reproducibility)

```bash
# lookup.duckdb from the local UMLS 2025AB RRF (CPT excluded by design)
python3 deploy/scripts/export_lookup_db.py \
    --rrf  data/rrf_2025ab/umls-2025AB-metathesaurus-full.nlm \
    --output lookup.duckdb
python3 deploy/scripts/verify_lookup_db.py --db lookup.duckdb   # gate: zero CPT
```

Terminology release: `fhir4ds/terminology` release `2026.10.31.1`,
`without-cpt` variant (see its `manifest.json` for per-family attestation).
