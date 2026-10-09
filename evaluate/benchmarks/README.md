# Full benchmark checkouts

Local checkouts are organized here:

- `Vision2Web/`
- `SWE-bench/`
- `3dcodebench/`
- `gamedevbench/`

[sources.json](sources.json) records their remotes, revisions and tracked local
changes at the time of reorganization. Each checkout retains its own `.git`.
The parent repository ignores these directories to avoid committing datasets,
results or accidental Git submodules. A fresh source-only clone will not contain
them; clone the recorded repository into its named directory and check out the
recorded revision when that workflow needs it. Any recorded local changes require
the separate local backup.

These full checkouts are separate from the pinned evaluator snapshots under
`evaluate/vision2web/`, `evaluate/swe_mm/` and the other evaluator directories.
Keep the pinned snapshots and their provenance intact.

Project-owned benchmark adapters live in `src/multimodalcode/vsv_eval/`.
See [VSV_BENCHMARKS.md](../../docs/VSV_BENCHMARKS.md) for supported inputs,
reconstruction requirements and evaluation commands.
