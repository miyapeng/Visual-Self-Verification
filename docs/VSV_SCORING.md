# Visual Self-Verification scoring

This page describes the historical three-component evaluator. For the current
six-metric implementation, use [the scoring system](VSV_SYSTEM.md) and
[the current protocol](self_verification_evaluation.md). The historical model
assignments below are not the current experiment configuration.

The scorer is offline: `workflow.json`, replay results, and Judge outputs are read only after the coding trajectory ends.

## Components

| Component | Source of truth | Model use |
|---|---|---|
| Episode extraction | event order, deployed URL, workspace hashes | none |
| Action validity | recorded result plus isolated Playwright replay | none |
| Task necessity | workflow candidate rules | `Qwen3.5-35B-A3B` resolves semantics |
| Visual Judgment | task, prototypes, observed screenshots, subsequent policy behavior | `Qwen3-VL-30B-A3B-Instruct` |
| Safe Repair | `P_before/P_after` target and regression replay | text Judge selects patch-affected checks; VL Judge only resolves ambiguous visuals |
| Summary | structured component results | `Qwen3.5-35B-A3B` |

Claude Opus 4.8 is an audit Judge for non-Opus policies. `gpt-4o` audits Opus-generated trajectories. Audit disagreements are recorded as unresolved fields and do not alter the reproducible primary score.

## 1. Parse without model calls

```bash
conda activate mmcode
python scripts/vision2web/score_vsv.py \
  --run-json reports/vision2web_scaffold_model_comparison/data/runs/claude-opus-4-8-claude_code.json \
  --task-root data/vision2web/extracted/frontend/smartrecruiters \
  --output-dir runs/vsv_eval/opus-smartrecruiters \
  --offline
```

This produces `episodes.json`, `replay_requests.json`, `scores.json`, and `index.html`. Missing Judge or replay evidence remains `null/not_evaluable`.

## 2. Primary open-model scoring

Serve the frozen text and visual Judges in the separate vLLM environment:

```bash
CUDA_VISIBLE_DEVICES=0,1 conda run -n vllm vllm serve /data/miyapeng/model/Qwen3.5-35B-A3B \
  --host 127.0.0.1 --port 8010 \
  --served-model-name Qwen3.5-35B-A3B \
  --tensor-parallel-size 2 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 65536

CUDA_VISIBLE_DEVICES=2,3 conda run -n vllm vllm serve /data/miyapeng/model/Qwen3-VL-30B-A3B \
  --host 127.0.0.1 --port 8011 \
  --served-model-name Qwen3-VL-30B-A3B-Instruct \
  --tensor-parallel-size 2 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 65536
```

Then score in `mmcode`:

```bash
conda activate mmcode
export VSV_PRIMARY_BASE_URL=http://127.0.0.1:8010/v1
export VSV_VISUAL_BASE_URL=http://127.0.0.1:8011/v1
python scripts/vision2web/score_vsv.py \
  --run-json PATH/TO/RUN.json \
  --task-root data/vision2web/extracted/LEVEL/CASE \
  --output-dir runs/vsv_eval/RUN_NAME
```

Judge requests are content-addressed under `judge_cache/`, so reruns resume without repeated calls.

## 3. Closed-model audit

```bash
read -rsp 'API Key: ' LLM_RELAY_API_KEY; echo
export LLM_RELAY_API_KEY
export VSV_CLOSED_BASE_URL=http://35.220.164.252:3888/v1
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
export NO_PROXY=35.220.164.252

python scripts/vision2web/score_vsv.py \
  --run-json PATH/TO/RUN.json \
  --task-root data/vision2web/extracted/LEVEL/CASE \
  --output-dir runs/vsv_eval/RUN_NAME \
  --audit
```

## 4. Mechanical replay

Create a declarative plan with reset-separated scenarios. Actions support `navigate`, `click`, `fill`, `hover`, `press`, `select`, `scroll`, `go_back`, and `wait`. Targets use either `{role,name,exact}` or `{selector}`. Optional assertions support `url_contains`, `text_visible`, `text_equals`, and `count`.

```bash
python scripts/vision2web/replay_vsv.py \
  --episode-id EPISODE_ID \
  --plan PLAN.json \
  --before-workspace P_BEFORE \
  --after-workspace P_AFTER \
  --output-dir runs/vsv_eval/RUN_NAME/replay/EPISODE_ID

python scripts/vision2web/score_vsv.py \
  --run-json PATH/TO/RUN.json \
  --task-root data/vision2web/extracted/LEVEL/CASE \
  --output-dir runs/vsv_eval/RUN_NAME \
  --replay-results runs/vsv_eval/RUN_NAME/replay/EPISODE_ID/replay_results.json
```

New trajectories store content-addressed program files at each recorded state. Historical trajectories without these blobs remain analyzable but some intermediate Safe Repair episodes cannot be replayed exactly.

`scores.json` keeps the three components separate. There is deliberately no weighted total: action execution, workflow coverage, visual-judgment accuracy, target-fix rate, and regression-free rate have different denominators and missing-evidence rules.

## Archived-text proxy estimates

The opt-in `--text-proxy` workflow is documented in [VSV_TEXT_PROXY.md](VSV_TEXT_PROXY.md). It uses separate result files and does not replace execution-verified scoring.
