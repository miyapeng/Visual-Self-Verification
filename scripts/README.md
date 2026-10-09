# Commands

Run these commands from the repository root. Reusable implementation lives in
`src/multimodalcode/`; these entry points use it directly.

| Entry | Purpose |
| --- | --- |
| `vsv/prepare_benchmark.py` | Import selected tasks and trajectories |
| `vsv/prepare_catalogue.py` | Prepare task requirements with the configured Judge |
| `vsv/extract.py` | Extract verification rounds; `--offline` emits rule candidates |
| `vsv/evaluate.py` | Run the shared evaluator; `--check-only` validates inputs |
| `vision2web/` | Web image recovery, replay and benchmark-specific commands |
| `agents/run.py` | Run a coding-agent scaffold |
| `run.py` | Run the existing single-pass generation/evaluation workflow |
| `research/run.py`, `research/self_verify.py` | Run earlier research workflows |
| `research/tmux.sh` | Manage research sessions |
| `serving/run_4gpu_vllm.sh` | Launch the existing vLLM server configuration |
| `training/prepare.py` | Prepare handoffs, collect/review teacher suffixes and export SFT/RL datasets |
| `training/train_sft.sh`, `training/train_rl.sh` | Launch the pinned ms-swift and verl integrations |
| `training/smoke.py`, `training/check_upstream.py` | CPU Web smoke test and upstream interface checks |

Other subdirectories retain their named benchmark or cluster workflows. Consult
[the scoring guide](../docs/VSV_SYSTEM.md),
[benchmark adapters](../docs/VSV_BENCHMARKS.md) and
[earlier workflows](../docs/LEGACY_WORKFLOWS.md) for required inputs and environments.
