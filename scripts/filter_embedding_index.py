#!/usr/bin/env python3
"""Filter the full embedding index to a specific list of codes.

Useful for producing per-ValueSet indices that the model team can load
on demand. Reads a CSV with (source, code) pairs and a source JSONL
index, writes a filtered JSONL with only matching records.

CSV format (header required): source,code columns (any other columns
ignored).

Usage:
  python3 scripts/filter_embedding_index.py \\
    --codes reports/fhir4px/valueset_X_patient_friendly.csv \\
    --input reports/fhir4px/embedding_index_full.jsonl \\
    --output reports/fhir4px/embedding_index_valueset_X.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codes", required=True,
                        help="CSV with header row containing 'source' and 'code' columns.")
    parser.add_argument("--input", default="reports/fhir4px/embedding_index_full.jsonl",
                        help="Source embedding index JSONL to filter.")
    parser.add_argument("--output", required=True,
                        help="Output JSONL path.")
    args = parser.parse_args()

    codes_path = Path(args.codes)
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not codes_path.exists():
        print(f"Codes CSV not found: {codes_path}", file=sys.stderr)
        return 2
    if not input_path.exists():
        print(f"Input index not found: {input_path}", file=sys.stderr)
        return 2

    # Load target codes
    wanted: set[tuple[str, str]] = set()
    with codes_path.open() as f:
        reader = csv.DictReader(f)
        if "source" not in reader.fieldnames or "code" not in reader.fieldnames:
            print(f"CSV must have 'source' and 'code' columns; got {reader.fieldnames}",
                  file=sys.stderr)
            return 2
        for row in reader:
            src = row["source"].strip()
            code = row["code"].strip()
            if src and code:
                wanted.add((src, code))
    print(f"Looking for {len(wanted):,} codes from {codes_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    n_written = 0
    n_seen = 0
    missing = set(wanted)
    with input_path.open() as fin, output_path.open("w", encoding="utf-8") as fout:
        for line in fin:
            r = json.loads(line)
            key = (r["code"]["source"], r["code"]["code"])
            n_seen += 1
            if key in wanted:
                fout.write(line)
                n_written += 1
                missing.discard(key)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    pct = 100 * n_written / max(len(wanted), 1)
    print(f"Wrote {n_written:,}/{len(wanted):,} records ({pct:.1f}%) to {output_path} ({size_mb:.1f} MB)")
    print(f"Scanned {n_seen:,} records from {input_path}")
    if missing:
        print(f"Missing {len(missing):,} codes (first 5 shown):")
        for src, code in list(missing)[:5]:
            print(f"  {src} {code}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
