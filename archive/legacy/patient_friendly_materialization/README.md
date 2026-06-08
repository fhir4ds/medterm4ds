# Legacy patient-friendly materialization

This directory contains the archived materialized patient-friendly resolver.

It was removed from the active package because it was not validated against the current runtime patient-friendly policy and did not meet the current performance envelope. The canonical implementation is the runtime resolver used by `get_patient_friendly_names` and `scripts/run_patient_friendly_review.py`.

Current evidence:

- Runtime all-reviewed-source pass resolved 1,127,094 codes in 3:45.57 wall time with `--memory-profile fast`.
- The old materialization command did not complete after 1:01:50 on the reviewed production systems.

Future materialization should reuse the runtime resolver output directly, or first refactor the runtime resolver into shared SQL relation builders so runtime and materialized semantics cannot drift.
