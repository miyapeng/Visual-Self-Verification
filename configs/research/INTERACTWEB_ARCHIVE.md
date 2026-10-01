# InteractWeb-Bench archive inventory

**Archived on 2026-08-23. This is not an active experiment plan.**

The canonical scope decision is
[`reports/research_scope_decision.md`](../../reports/research_scope_decision.md).
InteractWeb-Bench must not be resumed or reintroduced automatically. An
explicit user reversal is required.

The following material is retained for provenance and reproducibility of
historical exploratory work:

- pristine evaluator/source snapshot under `evaluate/interactweb_bench/`;
- frozen data and manifests under `data/interactweb_bench/`;
- historical outputs under `runs/native_benchmarks/` and
  `runs/research/active_visual_verification/`;
- the `interactweb` environment description and
  `requirements/native/interactweb_bench.txt`;
- frozen `configs/research/interactweb_*` preregistrations, cases, contexts and
  their referenced hashes;
- read-only collection, validation and audit utilities under
  `scripts/interactweb_bench/`;
- historical tests that protect evaluator fidelity and artifact integrity.

Frozen preregistrations and case JSON files are not edited because their hashes
are part of the historical record. Pure InteractWeb launchers and completed
mixed-pilot launchers are guarded so they fail before allocating resources or
submitting jobs.

These artifacts may be inspected as archived evidence, but their results must
not enter current-paper aggregates, claims, data construction or future task
planning.
