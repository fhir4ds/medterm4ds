# AMUX-63 depth sweep results (2026-11-04/05)

Production `SearchService.hybrid()` (real grading) over the AMUX-46
golden families, at candidate depths 10/25/50/100. Raw rows:
`/tmp/sweep/*.jsonl` (checkpointed subprocess retries — see
docs/notes/2026-11-04-wsl2-native-corruption.md for why).

| depth | n | hit@1 | hit@5 | FP@1 | abstain | median ms | p90 ms |
|---|---|---|---|---|---|---|---|
| 10 | 32 | 63.0% | 85.2% | 15.6% | 40.6% | 312.0 | 1100.3 |
| 25 | 32 | 63.0% | 85.2% | 15.6% | 40.6% | 327.3 | 1112.1 |
| 50 | 32 | 63.0% | 85.2% | 15.6% | 40.6% | 317.1 | 1028.6 |
| 100 | 32 | 63.0% | 85.2% | 15.6% | 40.6% | 165.9 | 932.6 |

## Caveats on the latency columns

The sweep ran overnight in retry processes with varying machine load
(and spanned the lexical orphan-pass fix landing mid-run), so the
latency columns are NOT comparable across depths — they measure the
machine, not the depth. Controlled phase timing (same process, warm,
5-call medians) shows latency is depth-independent:

| depth | query | lexical | semantic | hybrid total |
|---|---|---|---|---|
| 100 | 'Tylenol' | 181.5 | 77.5 | 261.6 |
| 100 | 'lisinopril 5mg' | 254.3 | 94.8 | 358.6 |
| 25 | 'Tylenol' | 183.8 | 116.9 | 315.2 |
| 10 | 'Tylenol' | 180.1 | 120.6 | 306.7 |

Lexical dominated and was flat across depths → the latency root cause
was the AMUX-20 orphan surface pass rebuilding `f"{name} {friendly}"`
and lowercasing it for every row on every query (fixed in AMUX-63:
cached `_orphan_surfaces_lower` per index, longest-token-first plain
loop; 'Tylenol' 180ms -> 40ms, 'lisinopril 5mg' 256ms -> 56ms).

## Quality per-query

Only ONE query differs between depths: 'ACE inhibitors' grades
'certain' at depth 100 vs 'probable' at 10/25 (hit@1 true at every
depth — the row is found either way; only the agreement evidence
threshold shifts). Everything else is identical.

## Decision

`HYBRID_CANDIDATE_DEPTH` 100 -> **25**: quality flat 10..100, latency
unaffected by depth, 25 keeps agreement-evidence headroom over 10 at
no measured cost. The real latency fix is the orphan-surface cache.
