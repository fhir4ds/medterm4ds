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
Serves the **real medterm4ds FHIR R4 terminology API unmodified**
(`medterm4ds.create_fhir_app`) on free-tier hardware (2 vCPU / 16 GB); the
Space **sleeps after 48 h idle**. It is a demo, not an uptime-bearing API.

Live at: **https://fhir4ds-terminology-demo-server.hf.space**

## API — native FHIR R4 (plus two demo endpoints)

The FHIR surface is the app that ships with medterm4ds; this Space is only
a deployment wrapper (access control, rate limiting, readiness) around it.
Every FHIR route it registers is served as-is:

- `GET /fhir/metadata` — CapabilityStatement (authoritative route list)
- `POST /fhir` — FHIR batch/transaction bundles
- `CodeSystem/$lookup`, `CodeSystem/$validate-code`, `CodeSystem/$search`,
  `CodeSystem/$extract` (GET and POST), `CodeSystem/$subsumes`,
  `CodeSystem/$closure`
- `ValueSet/$validate-code`, `ValueSet/$expand` (plus
  `ValueSet/{id}/$expand` and `ValueSet/{id}/$validate-code`)
- `ConceptMap/$translate` (plus `ConceptMap/{id}/$translate`)
- Resource-level reads/search (`GET/POST /fhir/{type}[/{id}]`)

The FHIR app's own `/docs` (Swagger) and `/openapi.json`, generated from
the real routes, are served at the root.

Demo-only endpoints added by the wrapper:

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | readiness + version. Reports `db_ready` and `search_ready` separately (cheap: in-memory booleans only); `status: "ready"` only when both are true. **Wins over the FHIR app's own `/health`** (registered first on the composed app) because it carries the split readiness signal |
| `/capabilities` | GET | demo inventory: mounted terminology release, DB sources, surfaced FHIR routes, limits, allow-list, `search_warm` |

`$search` and `$extract` need the SapBERT/FAISS/GLiNER models; until the
background warm-up finishes they return **503 + Retry-After** instead of
blocking behind a model load.

**Excluded routes: none.** Every route of the FHIR app was exercised within
the free-tier limits (2 vCPU / 16 GB): `$lookup`, `$validate-code`,
`$search` (all four modes), `$extract`, `$subsumes`, `$closure`,
`$expand` (url-form and filter-form), `$translate`, batch `POST /fhir`
and `GET /fhir/metadata` all answer within seconds.

## Access control

No API key in the browser (none to leak). Instead:

- **Origin allow-list** — configurable via the `DEMO_ALLOWED_ORIGINS` env var
  (comma-separated; an explicit enumerated list — no wildcards, no suffix
  matching, no `*.fhir4ds.com` patterns). Default:
  `https://terminology.fhir4ds.com` (canonical browser Origin — the docs
  site is GitHub Pages with a CNAME, fronted by Cloudflare for DNS only),
  `https://fhir4ds.com`, `https://www.fhir4ds.com` (main fhir4ds site —
  examples also live there), `http://localhost:3000`,
  `http://localhost:3001` (local dev — the docs site is served on both),
  `http://127.0.0.1:3000` (local dev), and `https://fhir4ds.github.io`
  (Pages default host; harmless, covers previews). The active allow-list
  is printed at startup and exposed in `GET /capabilities`, so operators
  can see what is accepted without reading code. CORS preflight responses
  echo the allow-listed origin; a request with a non-allowed `Origin` gets
  **403**. No credentials are used: no cookies, no Authorization header
  from the page, and `Access-Control-Allow-Credentials` stays off.
- **HF Spaces edge behaviour (important, verified live 2026-11-03):** the
  `*.hf.space` edge proxy intercepts **every** OPTIONS request — including
  plain OPTIONS to nonexistent paths — and answers it itself with
  `200`, `access-control-allow-methods: <requested>`, and an
  `access-control-allow-origin` **echoing whatever Origin was sent**. It
  also decorates all other responses with an echoed ACAO when the app
  didn't set one (including this app's 403 refusals). The app cannot see
  preflights and cannot strip platform-added headers, so a strict
  "preflight from a stranger → 403, no ACAO" is **not achievable behind
  `*.hf.space`** — this is platform behaviour, identical for every Space.
  The enforcement that actually protects data is the app-level **403 on
  the actual request**: a browser on a non-allowed origin may read the
  403 error body (edge-echoed ACAO), but no data response is ever
  returned to it. Verified: `POST /fhir/CodeSystem/$lookup` with
  `Origin: https://evil.example` → 403 from the app, always. For strict
  preflight refusal, front the Space with a proxy you control (e.g. the
  existing Cloudflare, via a transform rule that overrides the CORS
  headers) on a custom domain.
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
>   https://fhir4ds-terminology-demo-server.hf.space (source: https://huggingface.co/spaces/fhir4ds/terminology-demo-server)
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

## Deploying (status + Joel's remaining steps)

**Done (2026-11-03):** Space created (Docker SDK, cpu-basic, PRO account);
code pushed to the Space repo root (`Dockerfile`, `app.py`,
`startup_data.py`, `requirements.txt`, `README.md`, `NOTICE`); secrets set
(`DEMO_ALLOWED_ORIGINS`, `DEMO_HF_TOKEN`) and variables set
(`DEMO_RATE_LIMIT_PER_MINUTE=30`, `DEMO_LOOKUP_DATASET`);
the CPT-excluded `lookup.duckdb` (207 MB) uploaded to the PRIVATE dataset
**fhir4ds/terminology-demo-data** (never in this public repo — UMLS
redistribution requires the private, token-gated path or a volume mount).

**Data provisioning:** volume mounts win when attached (see below);
otherwise `startup_data.py` fetches `lookup.duckdb` from the private
dataset (with the `DEMO_HF_TOKEN` secret) and the terminology release from
the public `fhir4ds/terminology` repo into the Space's ephemeral `/data`
at container start. Nothing licence-restricted is ever baked into the
image or the public repo.

**Remaining for Joel — attach volumes (UI-only; the hub API exposes no
volume-mount endpoint as of 2026-11-03):**

1. Open https://huggingface.co/spaces/fhir4ds/terminology-demo-server/settings
2. *Storage* section → mount the repos read-only:
   - dataset `fhir4ds/terminology-demo-data` (private) → `/data`
     (places `lookup.duckdb` at `/data/lookup.duckdb`)
   - model `fhir4ds/terminology` (public) → `/data/cache`
     (the release resolves as `/data/cache/terminology/<release>/`; if the
     mount lands at a different sub-path, set `MEDTERM4DS_TERMINOLOGY_DIR`
     accordingly)
3. Restart the Space. With both volumes attached, the runtime download
   fallback no longer runs and cold start drops by the download time.

**Verify (live):**

```bash
B=https://fhir4ds-terminology-demo-server.hf.space
curl -s $B/health                     # db_ready + search_ready flags
curl -s $B/fhir/metadata              # CapabilityStatement
curl -s -X POST $B/fhir/CodeSystem/\$lookup -H 'Content-Type: application/json' \
     -d '{"resourceType":"Parameters","parameter":[
          {"name":"system","valueUri":"http://snomed.info/sct"},
          {"name":"code","valueCode":"22298006"}]}'
curl -s -X POST $B/fhir/CodeSystem/\$search -H 'Content-Type: application/json' \
     -d '{"resourceType":"Parameters","parameter":[
          {"name":"query","valueString":"heart attack"},
          {"name":"searchMode","valueCode":"hybrid"},
          {"name":"count","valueInteger":3}]}'
```

Hardware: free tier (2 vCPU / 16 GB) is sufficient — everything runs on
CPU (`MEDTERM4DS_MEMORY_PROFILE=low`, DuckDB threads=2).

Cold start (stopped container → query-ready):
- **Live measurement (deployed Space, volumes attached, 2026-11-03):**
  `db_ready` at **t+2 s**, `search_ready` at **t+55 s** after app start.
  First `$search` (hybrid) after ready: **1.3 s**; first `$extract`: 0.4 s.
- Locally (2 vCPU / 16 GB, data on local disk): db_ready ~1-3 s,
  search_ready ~32-42 s. The background warm-up loads the lexical
  indexes, SapBERT, FAISS and the GLiNER/medspaCy pipeline before
  `/health` reports `search_ready: true`. `$search` and `$extract` return
  **503 + Retry-After** until then, rather than blocking behind the model
  load. If warm-up FAILS, `/health` carries a `warm_error` field with the
  reason instead of silently staying "starting".
- On the Space WITHOUT volumes, add the download time for the 207 MB
  lookup DB and the ~4 GB terminology release on first start.
Idle-sleep wake adds Space scheduling on top; allow up to ~2 minutes
(volume-mounted). Health-check start-period is 120 s.

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
