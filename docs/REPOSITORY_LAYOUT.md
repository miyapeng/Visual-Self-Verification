# Repository layout

Keep implementation, commands, external sources and experiment artifacts separate.
Run commands from the repository root unless a guide states otherwise.

| Directory | Purpose |
| --- | --- |
| `src/multimodalcode/vsv_eval/` | Verification extraction, evidence links, judges, scoring and acceptance |
| `src/multimodalcode/agent_harness/` | Agent integration and trajectory recording |
| `src/multimodalcode/research/` | Research workflow implementation |
| `scripts/vsv/` | Shared evaluation and data preparation commands |
| `scripts/vision2web/` | Web replay, image recovery and Vision2Web utilities |
| `scripts/agents/`, `scripts/research/`, `scripts/serving/` | Agent, research and model-server launchers |
| `evaluate/` | Upstream evaluator snapshots, licenses and full benchmark checkouts |
| `configs/` | Experiment settings, Judge profiles and prompts |
| `requirements/` | Dependencies for separate runtimes |
| `tests/` | Project tests |
| `docs/` | Usage guides; historical proposals are under `protocols/` |
| `reports/` | Research reports and locally generated report sites |
| `scaffolds/` | Pinned agent framework source |
| `data/`, `runs/` | Local task inputs, trajectories, reconstruction and evaluation outputs |
| `.local/` | Local environments, runtime caches and maintenance backups |

Keep reusable Python logic in `src/`; scripts provide command-line entry points.
Benchmark-specific adapters share the evaluator in `vsv_eval/`. Do not duplicate
the scoring implementation under `evaluate/`. That directory holds upstream
sources, including versions that must remain pinned independently.

`pyproject.toml`, `setup.py` and the short `requirements.txt` remain at the root
for standard packaging and installation tools. `requirements.txt` includes
`requirements/base.txt`.

## Path changes on 2026-10-08

| Previous path | Current path |
| --- | --- |
| `benchmark/` | `evaluate/benchmarks/` |
| `prompts/` | `configs/prompts/` |
| `run.py` | `scripts/run.py` |
| `agent_run.py` | `scripts/agents/run.py` |
| `research_run.py` | `scripts/research/run.py` |
| `self_verify_run.py` | `scripts/research/self_verify.py` |
| `scripts/research_tmux.sh` | `scripts/research/tmux.sh` |
| `scripts/run_4b_guarded_ablations.sh` | `scripts/research/run_4b_guarded_ablations.sh` |
| `scripts/run_final_4b_matrix.sh` | `scripts/research/run_final_4b_matrix.sh` |
| `scripts/run_4gpu_vllm.sh` | `scripts/serving/run_4gpu_vllm.sh` |
| Root protocol and research-plan Markdown files | `docs/protocols/` |
| `requirements-agents.txt`, `requirements-chartmimic.txt` | `requirements/agents.txt`, `requirements/chartmimic.txt` |
| `.envs/`, `.venvs/`, `.runtime/` | `.local/envs/`, `.local/venvs/`, `.local/runtime/` |
| `.pytest_cache/`, root `__pycache__/` | `.local/cache/pytest/`, `.local/cache/root_pycache/` |
| `backend/`, `frontend/` | `.local/legacy-root/backend/`, `.local/legacy-root/frontend/` |

The former frontend/backend directories each contained only an empty npm lockfile;
they did not contain application source. They are retained pending deletion approval.

No dataset, run, screenshot or historical score was deleted or relocated.
Historical reports and frozen manifests retain their original path strings and
hashes. Use this table to locate moved files when reading old records. Existing
shells open inside a moved directory should `cd` to its new path.

The one-time local migration record, pre-edit backups and external integration
backups are under `.local/maintenance/reorganization-20261008/`. This directory is
ignored by Git. The active Conda editable imports and workspace environment guides
were updated for the relocated Vision2Web and GameDevBench checkouts.
Copied environments under `.local/envs/` and `.local/venvs/` already contained
old-machine interpreter links; moving them does not recreate usable environments.

## Storage and version control

Track source, configuration, tests, documentation and upstream provenance.
Keep datasets, trajectories, credentials, environments, model weights, generated
websites and full benchmark clones out of this repository's commits. The full
clones retain their own Git histories; `evaluate/benchmarks/sources.json` records
their revisions, not a backup of their uncommitted changes.

Do not delete old runs solely because of their date. Reports, Judge caches,
reconstructed images and acceptance evidence can reference them. Review concrete
deletion targets separately from this layout change.
