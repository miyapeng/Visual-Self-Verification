# Visual Self-Verification

Research code for evaluating how coding agents inspect their own outputs,
judge the evidence, make repairs and check the result. The shared evaluator
reports VC, CV, BDA, RS, CP and VCS with source evidence and model-call records.

Start with the [evaluation guide](docs/VSV_SYSTEM.md),
[benchmark adapters](docs/VSV_BENCHMARKS.md) and
[research reports](reports/README.md).

The [training guide](docs/VSV_TRAINING.md) describes prefix-conditioned teacher
distillation with ms-swift and verification GRPO with verl. CPU integration has
been checked; distributed GPU training and improvements remain unvalidated.

## Layout

```text
src/multimodalcode/     Core implementation
  vsv_eval/            Extraction, judges, scoring and acceptance
  training/            Handoffs, teacher collection, rewards and training adapters
  agent_harness/       Agent integration and trajectory recording
  research/            Research workflows
scripts/               Commands, grouped by workflow and benchmark
  vsv/                 Shared import, extraction and evaluation
  vision2web/          Web replay and image recovery
  agents/              Coding-agent launcher
  research/            Research launchers
  serving/             Model-server launcher
  training/            Data preparation, SFT/RL launchers and CPU smoke test
evaluate/              Upstream evaluators and benchmark source
  benchmarks/          Full local benchmark checkouts
configs/               Experiment settings, Judge profiles and prompts
requirements/          Environment-specific dependencies
tests/                 Project tests
docs/                  Guides and protocol history
reports/               Research findings and local report artifacts
scaffolds/             Pinned agent framework source
data/                  Local datasets and task material
runs/                  Local trajectories and evaluation outputs
.local/                Local environments, caches and maintenance backups
```

See [the layout guide](docs/REPOSITORY_LAYOUT.md) for previous-to-current path
mappings and [the command index](scripts/README.md) for entry points.

## Evaluate trajectories

The shared workflow has adapters for Vision2Web, SWE-bench Multimodal,
3DCodeBench and GameDevBench. Usable evidence and runtime support determine
which stages can execute; adapter availability does not imply a reproduced
benchmark score. [The adapter guide](docs/VSV_BENCHMARKS.md) documents the
requirements and current limits.

Run from this directory in a prepared Python environment:

```bash
# Inspect inputs without model calls or application execution.
python scripts/vsv/evaluate.py \
  --manifest path/to/experiment.json \
  --output-dir runs/vsv_eval/experiment --check-only

# Run with the Judge configuration and credentials specified by the manifest.
python scripts/vsv/evaluate.py \
  --manifest path/to/experiment.json \
  --output-dir runs/vsv_eval/experiment

# Extract rule candidates without an API call.
python scripts/vsv/extract.py \
  --run-json path/to/run.json \
  --output-dir runs/vsv_eval/candidates --offline
```

`requirements.txt` includes the base dependencies from `requirements/base.txt`.
Agent and benchmark environments use the separate files described in
[requirements/README.md](requirements/README.md). Native benchmark runtimes
have additional requirements; installing this package alone does not recreate
those environments. Keep API credentials in environment variables or ignored
local configuration.

The existing single-pass and research workflows remain available through
`scripts/run.py`, `scripts/agents/run.py` and `scripts/research/`.
Their earlier documentation is preserved in [LEGACY_WORKFLOWS.md](docs/LEGACY_WORKFLOWS.md).

## Tests

In an environment with the required test dependencies:

```bash
PYTHONPATH=src python -m pytest tests/test_vsv*.py -q
```

Pytest writes its cache to `.local/cache/pytest/`. Some integration tests need
local experiment fixtures or external runtimes; see their skip/failure messages.

## Source and experiment artifacts

Git tracks source, configuration, tests, Markdown reports and pinned upstream
source with its provenance. Datasets, trajectories, model weights, environments,
credentials, generated sites and full benchmark clones are excluded. A source
clone alone does not restore historical experiments. See
[evaluator provenance](evaluate/README.md) and
[full benchmark revisions](evaluate/benchmarks/sources.json).
