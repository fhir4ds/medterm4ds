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
| `/health` | GET | readiness + version. Reports `db_ready` and `search_ready` separately (cheap: in-memory booleans only); `status: "ready"` only when both are true |
| `/capabilities` | GET | endpoints, search modes, code sources, limits, `search_warm` |
| `/search` | POST | `{query, mode: lexical\|semantic\|canonical\|hybrid, count?, sources?, result_types?}` |
| `/extract` | POST | `{text}` → concepts + annotated text (GLiNER + medspaCy NER, resolved to codes) |
| `/map` | POST | `{codes: [{source, code}], target_sources: [...]}` → cross-system mappings |
| `/translate` | POST | `{codes: [{source, code}]}` → patient-friendly names |
| `/lookup` | POST | `{codes: [{source, code}]}` → best atom per code |
| `/hierarchy` | POST | `{codes, direction: parents\|children\|ancestors\|descendants, max_depth?, limit?}` |

## Access control

No API key in the browser (none to leak). Instead:

- **Origin allow-list** — configurable via the `DEMO_ALLOWED_ORIGINS` env var
  (comma-separated; an explicit enumerated list — no wildcards, no suffix
  matching, no `*.fhir4ds.com` patterns). Default:
  `https://terminology.fhir4ds.com` (canonical browser Origin — the docs
  site is GitHub Pages with a CNAME, fronted by Cloudflare for DNS only),
  `https://fhir4ds.com`, `https://www.fhir4ds.com` (main fhir4ds site —
  examples also live there), `http://localhost:3000`,
  `http://127.0.0.1:3000` (local dev), and `https://fhir4ds.github.io`
  (Pages default host; harmless, covers previews). The active allow-list
  is printed at startup and exposed in `GET /capabilities`, so operators
  can see what is accepted without reading code. CORS preflight responses
  echo the allow-listed origin; a request with a non-allowed `Origin` gets
  **403 with no CORS headers at all** (no `Access-Control-Allow-Origin` on
  the 403 — echoing it would hide the intent). No credentials are used: no
  cookies, no Authorization header from the page, and
  `Access-Control-Allow-Credentials` stays off.
- **No-Origin requests are allowed but rate-limited** — `curl`, server-to-
  server clients and HF health probes send no `Origin` header and pass.
- **Per-IP rate limit** — 30 requests/minute sliding window per client IP
  (`DEMO_RATE_LIMIT_PER_MINUTE`), 429 + `Retry-After` beyond. `/health` is
  exempt.

> **This is a speed bump, not authentication.** The `Origin` header is
> browser-supplied and trivially spoofed by any non-browser client. The
> allow-list stops *other websites* from driving their visitors' browsers
> against this Space; it does not make the Space access-controlled, private
> or authenticated. Treat the Space as public.

### Why the browser calls the Space directly

The docs site is hosted on **GitHub Pages, which is static-only** — there is
no server-side proxy, so nothing can hide a key or sign requests. The example
page therefore calls this Space directly, cross-origin, and the origin
allow-list is the **only** browser-side control. Cloudflare sits in front of
the *website* only (DNS for terminology.fhir4ds.com); it does not touch the
Space or its CORS headers, and the app adds no header manipulation that
assumes a proxy. Preflight (`OPTIONS`) is handled explicitly and is cheap:
it never touches the model or the database, so preflight traffic cannot
trigger index loads or double the cold-start cost.

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

# 2. Secrets / variables (allow-list = fhir4ds sites + local dev + Pages host;
#    explicit enumerated list — no wildcards)
hf api add-space-secret  fhir4ds/terminology-demo DEMO_ALLOWED_ORIGINS "https://terminology.fhir4ds.com,https://fhir4ds.com,https://www.fhir4ds.com,http://localhost:3000,http://127.0.0.1:3000,https://fhir4ds.github.io"
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
CPU (`MEDTERM4DS_MEMORY_PROFILE=low`, DuckDB threads=2).

Cold start (stopped container → query-ready, measured on 2 vCPU / 16 GB):
- **db_ready in ~1 s** (container up, DuckDB open),
- **search_ready in ~32 s** — a background warm-up loads the lexical
  indexes, SapBERT, FAISS and the GLiNER/medspaCy pipeline before
  `/health` reports `search_ready: true`. `/search` and `/extract`
  return **503 + Retry-After** until then, rather than blocking behind
  the model load.
- first `/search` after `search_ready`: **<1 s**; first `/extract`: ~1 s.
Idle-sleep wake adds Space scheduling on top; allow up to ~2 minutes.
Health-check start-period is 120 s.

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
