#!/usr/bin/env python3
"""Convert CCSR category descriptions to patient-friendly names via local Ollama.

Reads the 554 CCSR categories from ``umls.mrconso WHERE SAB='CCSR_ICD10CM'
AND TTY='SD'``, sends each to an Ollama model with a prompt asking for a
plain-English patient-friendly name, and writes the result to a CSV.

Why direct Ollama calls instead of DuckDB's ``ai`` community extension:
the extension (``leonardovida/duckdb-ai``) isn't published as a binary for
any DuckDB 1.1-1.5 release — only source tags on GitHub. Building from
source requires DuckDB dev headers + C++ toolchain. Direct ``urllib`` calls
to ``/api/generate`` give identical throughput (the extension is just an
HTTP wrapper) with zero install pain.

Throughput: client-side ``ThreadPoolExecutor(max_workers=4)`` saturates
the local GPU at ~7 calls/s on ``gemma4:12b``. 554 categories → ~1.5 min.

Usage::

  PYTHONPATH=src python3 scripts/ccsr_to_patient_friendly.py \\
      --db /mnt/d/medterm4ds/data/umls_current.duckdb

Smoke::

  PYTHONPATH=src python3 scripts/ccsr_to_patient_friendly.py \\
      --db /mnt/d/medterm4ds/data/umls_current.duckdb --limit 20

Switch model::

  PYTHONPATH=src python3 scripts/ccsr_to_patient_friendly.py \\
      --db /mnt/d/medterm4ds/data/umls_current.duckdb --model qwen3.6:latest
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma4:12b"
DEFAULT_CONCURRENCY = 4
DEFAULT_OUTPUT = Path("reports/fhir4px/ccsr_patient_friendly.csv")
DEFAULT_CACHE = Path("reports/fhir4px/ccsr_patient_friendly_cache.json")

# --- Prompt profiles -------------------------------------------------------
#
# Two profiles share the same cache + plumbing. The detailed profile is the
# default — it produces structured multi-attribute output (name + explanation
# + pronunciation + severity) at the cost of ~5x latency.
#
# Cache keys are (model, code, profile) so you can switch profiles without
# losing prior work, and so side-by-side quality comparisons are possible.

PROMPT_SIMPLE = (
    "Convert this clinical category name to a plain-English patient-friendly "
    "name. Reply with just the name, no quotes, no extra text.\n"
    "Category: '{category}'"
)

# Detailed prompt — adapted from the user's health-literacy template.
# Adapted for CCSR category labels (not individual diagnoses): "Diagnosis" →
# "Category". Output is JUST the patient-friendly name (no table, no
# pronunciation/explanation/severity) — the rich prompt context is what makes
# the output higher quality than PROMPT_SIMPLE, not the output structure.
PROMPT_DETAILED = """You are an expert medical communicator specializing in health literacy and patient-centered communication.

Your task is to convert this clinical category label into a plain, easy-to-understand name for patients.

Category: '{category}'

### Instructions:
- Provide a short, plain-English patient-friendly name (2-5 words).
- The name MUST include a noun naming what the thing is. For specific medical conditions, prefer the standard clinical noun (e.g., "anemia", "stroke", "infection", "surgery") over generic terms like "condition" or "issue" (wrong: "Low blood cell count"; right: "Bone marrow failure anemia").
- Do NOT add filler nouns like "condition", "disorder", "disease", "procedure" unless they are part of the standard clinical name (e.g., "Heart failure", "Metabolic syndrome" are fine; "Heart condition", "Circulatory condition" are not). Prefer specific plain terms ("Heart attack" over "Heart condition").
- When the original starts with a coding catch-all prefix ("Other specified", "Other and ill-defined", "Not elsewhere classified", "Other unspecified", etc.), collapse the prefix to just "Other" — but still translate the clinical head into plain English. Example: "Other specified and unspecified hematologic conditions" → "Other blood conditions". Do NOT copy the original verbatim.
- Preserve clinical specificity — if the original names a specific syndrome (e.g., "Heart failure", "Stroke", "Sepsis"), keep that exact term in the name.
- Always spell out acronyms and abbreviations. Never keep abbreviations like "PTS", "MDS", "COPD", "CKD" — use the full plain-English form. Example: "Postthrombotic syndrome" → "Long-term blood clot effects" (not "...and PTS"). When an acronym aids recognition, you may include it in parentheses after the plain form (e.g., "Bone marrow disorder (MDS)").
- If the category is NOT a medical condition (administrative encounter, external cause code, etc.), give a plain-English name for what it represents.
- Use spaces only between words — no underscores, no hyphens, no slashes.

### Rules & Safety Guardrails:
- Tone: Empathetic, calm, clear, and non-alarmist.
- Serious Categories: For oncology, cardiac, or life-altering conditions, maintain clear accuracy without sugarcoating, but use compassionate, human language.
- Never Give Medical Advice: Do not suggest treatments, prognoses, or lifestyle changes.

### Output Format:
Reply with just the patient-friendly name — no quotes, no extra text, no explanation."""

CSV_COLUMNS = (
    "ccsr_category",
    "ccsr_category_display",
    "patient_friendly_name",
    "source",
    "model",
    "latency_ms",
)

DEFAULT_OVERRIDES = Path("reports/fhir4px/ccsr_patient_friendly_overrides.csv")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument(
        "--prompt-profile",
        choices=("simple", "detailed"),
        default="detailed",
        help="Prompt profile: 'simple' (name only, fast) or 'detailed' (name + pronunciation + explanation + severity, ~5x slower). Default: detailed.",
    )
    p.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature (default 0.0 = deterministic/greedy).",
    )
    p.add_argument(
        "--manual-overrides",
        default=str(DEFAULT_OVERRIDES),
        help=(
            "CSV of (ccsr_category, patient_friendly_name) rows that override "
            "LLM output. Default: reports/fhir4px/ccsr_patient_friendly_overrides.csv. "
            "Missing file = no overrides applied. Use to hand-fix clinical errors "
            "(e.g., 'somatic disorders' mistranslated as 'physical health conditions')."
        ),
    )
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p.add_argument("--cache", default=str(DEFAULT_CACHE))
    p.add_argument("--limit", type=int, default=0, help="Cap categories (0 = no cap; for smoke tests)")
    p.add_argument("--no-cache", action="store_true", help="Ignore cache; re-query every category")
    p.add_argument("--progress", action="store_true")
    return p.parse_args()


def load_categories(db_path: Path, limit: int) -> list[tuple[str, str]]:
    """All active CCSR_ICD10CM category codes + their canonical display."""
    import duckdb
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        sql = """
            SELECT CODE, STR
            FROM umls.mrconso
            WHERE SAB = 'CCSR_ICD10CM'
              AND TTY = 'SD'
              AND SUPPRESS = 'N'
              AND CODE IS NOT NULL
              AND STR IS NOT NULL
            ORDER BY CODE
        """
        params: list[object] = []
        if limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    return [(str(code), str(display)) for code, display in rows]


def load_cache(cache_path: Path) -> dict[str, dict]:
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  cache unreadable ({exc}); starting fresh", file=sys.stderr)
        return {}


def load_overrides(overrides_path: Path) -> dict[str, str]:
    """Load manual overrides as {ccsr_code: patient_friendly_name}.

    The file is a CSV with required columns ``ccsr_category`` and
    ``patient_friendly_name``. Other columns are ignored so the file can be a
    hand-edited subset of the main output CSV. Missing file = empty dict.
    """
    if not overrides_path.exists():
        return {}
    out: dict[str, str] = {}
    with overrides_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("ccsr_category") or "").strip()
            name = (row.get("patient_friendly_name") or "").strip()
            if code and name:
                out[code] = name
    return out


def save_cache(cache_path: Path, cache: dict[str, dict]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache_path)


def cache_key(model: str, ccsr_code: str, profile: str, temperature: float) -> str:
    """Cache is keyed by (model, code, profile, temperature) so changing any
    of these produces a fresh entry rather than silently returning stale output."""
    return f"{model}::{profile}::{temperature!r}::{ccsr_code}"


def call_ollama(
    *,
    url: str,
    model: str,
    category_display: str,
    profile: str,
    temperature: float = 0.0,
    timeout: float = 180.0,
) -> tuple[str, int]:
    """Return (patient_friendly_name, latency_ms). Raises on HTTP error.

    ``temperature=0.0`` makes output deterministic (greedy decoding) so re-runs
    produce byte-identical results — required for reproducible CSVs.
    """
    template = PROMPT_DETAILED if profile == "detailed" else PROMPT_SIMPLE
    num_predict = 60
    payload = json.dumps({
        "model": model,
        "prompt": template.format(category=category_display),
        "stream": False,
        "think": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read())
    latency_ms = int((time.perf_counter() - t0) * 1000)
    reply = (body.get("response") or "").strip()
    if reply and reply[0] in "'\"" and reply[-1] == reply[0]:
        reply = reply[1:-1].strip()
    # Belt-and-suspenders for temp=0 tokenization quirks: underscores slip
    # in where the model wanted a hyphen or space.
    reply = reply.replace("_", " ")
    return reply, latency_ms


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2
    output_path = Path(args.output)
    cache_path = Path(args.cache)

    print(f"Loading CCSR categories from {db_path}...", file=sys.stderr)
    categories = load_categories(db_path, args.limit)
    print(f"  {len(categories)} categories", file=sys.stderr)

    cache = {} if args.no_cache else load_cache(cache_path)
    cached_hits = sum(1 for code, _ in categories if cache_key(args.model, code, args.prompt_profile, args.temperature) in cache)
    print(
        f"  {cached_hits} already cached for model={args.model}, profile={args.prompt_profile}, temp={args.temperature}; "
        f"{len(categories) - cached_hits} to query",
        file=sys.stderr,
    )

    # Pre-populate results from cache; build the to-do list
    results: dict[str, dict] = {}
    todo: list[tuple[str, str]] = []
    for code, display in categories:
        key = cache_key(args.model, code, args.prompt_profile, args.temperature)
        if key in cache:
            results[code] = cache[key]
        else:
            todo.append((code, display))

    # Concurrent Ollama calls
    failures: list[tuple[str, str]] = []
    if todo:
        print(
            f"Querying Ollama (model={args.model}, profile={args.prompt_profile}, "
            f"concurrency={args.concurrency}, url={args.ollama_url})...",
            file=sys.stderr,
        )
        t_start = time.perf_counter()
        completed = 0
        failures: list[tuple[str, str]] = []

        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futures = {
                ex.submit(
                    call_ollama,
                    url=args.ollama_url,
                    model=args.model,
                    category_display=display,
                    profile=args.prompt_profile,
                    temperature=args.temperature,
                ): (code, display)
                for code, display in todo
            }
            for future in as_completed(futures):
                code, display = futures[future]
                try:
                    reply, latency_ms = future.result()
                    if not reply:
                        raise RuntimeError("empty response")
                    if args.prompt_profile == "detailed":
                        # Detailed profile: rich prompt context, but output is
                        # still just the patient-friendly name. No parser needed.
                        pass
                    entry = {
                        "ccsr_category": code,
                        "ccsr_category_display": display,
                        "patient_friendly_name": reply,
                        "source": "llm",
                        "model": args.model,
                        "latency_ms": latency_ms,
                    }
                    results[code] = entry
                    cache[cache_key(args.model, code, args.prompt_profile, args.temperature)] = entry
                    completed += 1
                    if args.progress and completed % 20 == 0:
                        elapsed = time.perf_counter() - t_start
                        rate = completed / max(1e-6, elapsed)
                        remaining = (len(todo) - completed) / max(0.1, rate)
                        print(
                            f"  {completed}/{len(todo)} done "
                            f"({rate:.1f}/s, ~{remaining:.0f}s remaining)",
                            file=sys.stderr,
                        )
                except (urllib.error.URLError, RuntimeError, json.JSONDecodeError) as exc:
                    failures.append((code, f"{type(exc).__name__}: {exc}"))
                    if args.progress:
                        print(f"  FAIL {code}: {exc}", file=sys.stderr)

        # Save cache after the run (successes only)
        save_cache(cache_path, cache)
        elapsed = time.perf_counter() - t_start
        print(
            f"  {completed} ok, {len(failures)} failed in {elapsed:.1f}s",
            file=sys.stderr,
        )

    # Apply manual overrides AFTER LLM calls + cache load. Overrides win.
    overrides_path = Path(args.manual_overrides)
    overrides = load_overrides(overrides_path)
    if overrides:
        applied = 0
        for code, override_name in overrides.items():
            if code in results:
                results[code]["patient_friendly_name"] = override_name
                results[code]["source"] = "override"
                # Update cache so re-runs preserve the fix
                cache[cache_key(args.model, code, args.prompt_profile, args.temperature)] = results[code]
                applied += 1
            else:
                # Override for a code not in our category set — add as a row
                # so the override isn't silently lost (defensive).
                results[code] = {
                    "ccsr_category": code,
                    "ccsr_category_display": "",  # unknown — caller can fill in
                    "patient_friendly_name": override_name,
                    "source": "override",
                    "model": args.model,
                    "latency_ms": "",
                }
        save_cache(cache_path, cache)
        print(f"  Applied {applied} manual overrides from {overrides_path}", file=sys.stderr)
    elif overrides_path.exists():
        print(f"  Overrides file present but empty: {overrides_path}", file=sys.stderr)

    # Write CSV (sorted by category code for stable diffs)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for code, display in categories:
            entry = results.get(code)
            if entry:
                row = {k: entry.get(k, "") for k in CSV_COLUMNS}
                # Backfill source for cache entries stored before the column existed
                if not row.get("source"):
                    row["source"] = "llm"
                writer.writerow(row)
            else:
                # Failed categories still get a row so the consumer can see gaps
                blank = {col: "" for col in CSV_COLUMNS}
                blank["ccsr_category"] = code
                blank["ccsr_category_display"] = display
                blank["model"] = args.model
                writer.writerow(blank)

    print(f"\nWrote {output_path} ({len(categories)} rows, profile={args.prompt_profile})", file=sys.stderr)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
