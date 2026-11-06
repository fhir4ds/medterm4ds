# medterm4ds

Clinical terminology layer for the fhir4ds stack: lexical + semantic + canonical
serving over UMLS-derived vocabularies, shipped on PyPI as `medterm4ds`
(v0.0.9). Consumed by fhir4ds (pinned dep) and the canonical curation team.

## Layout
- `src/medterm4ds/` — the library (lexical/semantic/canonical serving, embedding
  model stacks, fail-loud serving pin machinery)
- `docs/.ai_loop/` — full-cycle + QC loop (state: `evolution.json`); role files
  are the process authority
- `deploy/hf-spaces/` — the demo HF Space (fhir-server); its admin/write keys
  live in `.env` at the repo root (gitignored, never commit)
- `tests/` — pytest gates. ONE gate process at a time (stacked pytest runs spawn
  duplicate GLiNER/HF-download storms — fleet rule, medterm4ds iter-107/110)

## Commands
- Install: `pip install -e .` (or `pip install medterm4ds==0.0.9` for released)
- Test: `pytest -x` (serial; see the one-gate rule)
- Space build: `docker build -f deploy/hf-spaces/fhir-server/Dockerfile ...`
- The AI loop advances via `fleet-loop-v2 status medterm4ds` / amux sends

## Gotchas
- Environment pins: `MEDTERM4DS_EMBEDDING_MODEL_DIR` / `MEDTERM4DS_SPACE` must
  point at the current model stack (see fhir4ds configs).
- Never edit `docs/.ai_loop/` state files by hand; the loop owns them.
- Licensing: demo/public artifacts exclude SNOMED/UMLS-CUI/CPT content
  (Joel ruling 2026-10-30).
