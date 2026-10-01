# MultimodalCode

## GitHub source backup

This repository backs up the Visual Self-Verification research code, configuration,
tests, Markdown reports, and bundled evaluator/scaffold source. The CVPR 2027
research entry is [reports/README.md](reports/README.md).

Datasets, coding trajectories, generated report sites, screenshots, model weights,
Python environments, caches, archives, and local credentials are intentionally
excluded. Existing report links to those artifacts may therefore require the
separate local/object-storage backup. Cloning this repository does not restore a
complete experiment environment or establish that an experiment was reproduced.
Historical absolute paths and service endpoints in experiment configurations
must be adapted to the new machine before use. Keep credentials in environment
variables or untracked local configuration.

Bundled upstream source retains its existing license and provenance files; the
source-only backup omits upstream data/media that those inventories may reference.

`MultimodalCode` is a configuration-driven runner for multimodal web-code
benchmarks. It separates three concerns that upstream projects often mix:

1. dataset adaptation;
2. model inference and artifact generation;
3. benchmark-specific evaluation.

It supports vLLM, SGLang, LMDeploy, any OpenAI-compatible endpoint, direct
Transformers inference, and arbitrary executables through a JSON-over-stdin
command backend.

## Coding-agent benchmark path

`run.py` is intentionally a single-pass generation/evaluation runner. For real
coding-agent interaction, use `agent_run.py`: SWE-MM, Design2Code, and
ChartMimic run through the repository-owned `scaffolds/mini_swe_agent`
snapshot, while Vision2Web runs `scaffolds/openhands` inside the
digest-pinned ClusterX task image. The image supplies only its Python 3.12 and
system dependencies; `PYTHONPATH` makes the byte-pinned in-repository source
authoritative. Vision2Web defaults to an unmodified `official` OpenHands
profile; local compatibility/context overrides live under a separately named
`research` profile and a separate result directory.

## Active visual self-verification study

The active paper studies whether a multimodal coding agent inspects and repairs
its own rendered application. Vision2Web experiments use scaffold-specific
conditions: OpenHands has `official`, `browser_enabled`, and `guided_vsv`;
Claude Code has `official` and `guided_vsv` because its released scaffold
already exposes `playwright-cli`.

Start with [`reports/README.md`](reports/README.md) for active entry points,
verified artifacts, and the distinction between current and historical scores.
The canonical protocol is
[`reports/visual_self_verification_two_level_evaluation_design.md`](reports/visual_self_verification_two_level_evaluation_design.md):
trajectory-level observation and fresh-session checkpoint verification share
one offline Test / Visual Judgment / Safe Repair evaluator. Vision2Web retains
all three levels. Claude Code `official` is the primary generation baseline;
OpenHands `browser_enabled` is the scaffold comparison. Guided prompts and
cross-model verification handoff are supplementary analyses, not required loops.
The earlier [`research plan`](visual_self_verification_research_plan.md) remains
available as background, not the implementation-status entry point.
The frozen guided prompt is
[`prompts/vision2web/guided_vsv.txt`](prompts/vision2web/guided_vsv.txt).

## Included benchmark registrations

| Name | Data source | Evaluation |
|---|---|---|
| `design2code` | `data/design2code`, 484 pairs | Vendored Design2Code Block-Match, Text, Position, Color, CLIP |
| `flame-react-eval` | `data/flame-react-eval`, official 80 items | Flame-compatible code score and image cosine/pass@k |
| `web2code` | `data/web2code`, 1,198-image subset | Web2Code ten-criterion VLM judge |
| `ui2code-real` | `data/ui2code-real`, 115 screenshots | UI2Code_N two-image 0–100 VLM judge |

All four registered datasets and their required benchmark source files are
inside this repository. No runtime path points to `../UI2Code_N` or
`../Benchmarks`. Provenance and copied licenses are recorded in
[`evaluate/README.md`](evaluate/README.md).

The coding-agent path additionally has task/scaffold integrations for SWE-MM,
ChartMimic, and Vision2Web. Their official evaluator sources are frozen under
`evaluate/` and no longer read sibling benchmark checkouts. Runtime protocol
requirements—SWE instance images, ChartMimic's pinned Python environment, and
Vision2Web's Docker boundary—are tracked in `evaluate/README.md`.

FronTalk is self-contained and intentionally uses its released native
multi-turn protocol instead of the generic `run.py` or `agent_run.py` paths.
InteractWeb-Bench source and past runs are retained only as archived
exploratory evidence and are not part of the current paper. The authoritative
three-benchmark scope is recorded in
[`reports/research_scope_decision.md`](reports/research_scope_decision.md).

## Installation

Use the existing `mmcode` environment:

```bash
cd /data/miyapeng/mmcode/MultimodalCode
conda activate mmcode

python -m pip install -r requirements.txt
python -m playwright install chromium
# Run once as root if Chromium reports missing Linux shared libraries:
python -m playwright install-deps chromium
```

The runner talks to vLLM over HTTP, so the vLLM server may use a separate
environment. If `vllm serve` already works in an existing environment, there
is no need to reinstall it. `requirements.txt` intentionally does not pin
vLLM, Torch, CUDA, or FlashAttention, so it does not replace the GPU stack in
the existing `mmcode` environment.

For direct Hugging Face/Transformers inference:

```bash
python -m pip install transformers accelerate
```

The project requirements contain only Design2Code's evaluation-time packages.
Do not install the upstream Design2Code repository's entire
`requirements.txt` into the vLLM environment: it contains unrelated training
packages and pins its own Torch/FlashAttention versions.

## Dataset inspection

```bash
python run.py list
python run.py inspect design2code --limit 1
```

All paths are registered in
[`configs/benchmarks.json`](configs/benchmarks.json). A different registry can
be selected with `--config` or `MULTIMODALCODE_CONFIG`.

## Normal vLLM workflow

Start the model:

```bash
CUDA_VISIBLE_DEVICES=3 OMP_NUM_THREADS=1 \
vllm serve /data/miyapeng/model/Qwen3-VL-30B-A3B \
  --host 0.0.0.0 \
  --port 8001 \
  --served-model-name Qwen3-VL-30B-A3B-Instruct \
  --gpu-memory-utilization 0.98 \
  --max-model-len 16384
```

Then run the selected benchmarks:

```bash
python run.py \
  design2code flame-react-eval web2code ui2code-real \
  --output-root runs/Qwen3-VL-30B-A3B-Instruct \
  --backend vllm \
  --model Qwen3-VL-30B-A3B-Instruct \
  --base-url http://127.0.0.1:8001/v1 \
  --workers 4 \
  --render-workers 4 \
  --judge-workers 4
```

This runs inference, renders the generated pages, and calls each benchmark's
registered evaluator. For Web2Code and UI2Code-Real, the same vLLM endpoint is
also used as the visual judge by default. To use a different judge, add:

```bash
  --judge-backend openai-compatible \
  --judge-model YOUR_JUDGE_MODEL \
  --judge-base-url https://api.openai.com/v1
```

`--base-url` must include `/v1` for the usual vLLM deployment. The runner adds
`/chat/completions`.

For a two-item smoke test, append `--limit 2`. Runs are resumable by default.
Successful `item_id + sample_index` pairs are skipped.

## Four-GPU replica evaluation

When the model fits on one GPU, four independent vLLM replicas provide more
benchmark throughput than four-way tensor parallelism. The bundled script
starts one replica on each physical GPU at ports 8001 through 8004, waits for
all endpoints, distributes generation and judge requests round-robin, and
stops the replicas after model-based scoring:

The script deliberately uses two separate Conda environments regardless of
which environment is active in the current shell:

- `/data/miyapeng/miniconda3/envs/vllm/bin/vllm` starts the four servers;
- `/data/miyapeng/miniconda3/envs/mmcode/bin/python` runs rendering and
  evaluation.

```bash
# Smoke test
bash scripts/run_4gpu_vllm.sh --limit 2

# Full run
bash scripts/run_4gpu_vllm.sh
```

If generation has already completed, resume from rendering with the same
output directory. Successful prediction and render records are skipped:

```bash
START_STAGE=render \
OUTPUT_ROOT=runs/Qwen3-VL-30B-A3B-Instruct-4gpu \
bash scripts/run_4gpu_vllm.sh
```

Other resume points are `START_STAGE=judge` and
`START_STAGE=design2code`. Override `VLLM_ENV_PREFIX` or
`EVAL_ENV_PREFIX` only when the Conda environments live elsewhere.

Defaults match the Qwen model path used above. They can be overridden without
editing the script:

```bash
GPU_IDS="0 1 2 3" \
MODEL_PATH=/data/miyapeng/model/Qwen3-VL-30B-A3B \
SERVED_MODEL_NAME=Qwen3-VL-30B-A3B-Instruct \
BASE_PORT=8001 \
WORKERS=8 \
bash scripts/run_4gpu_vllm.sh --limit 2
```

Web2Code and UI2Code-Real reuse the four Qwen replicas as their judge unless
separate `--judge-*` arguments are supplied. The script then stops all four
replicas and runs the local Design2Code CLIP metric on physical GPU 0; set
`LOCAL_EVAL_GPU` to change that GPU. Self-judging runs end to end but are not
directly comparable with results produced using the benchmark's designated
judge model.

An already-running replica pool can also be supplied manually:

```bash
python run.py ui2code-real web2code \
  --output-root runs/qwen-pool \
  --backend vllm \
  --model Qwen3-VL-30B-A3B-Instruct \
  --base-url \
  http://127.0.0.1:8001/v1,http://127.0.0.1:8002/v1,http://127.0.0.1:8003/v1,http://127.0.0.1:8004/v1 \
  --workers 8 --render-workers 4 --judge-workers 8
```

## Individual and staged commands

```bash
python run.py generate ui2code-real \
  --run-dir runs/Qwen3-VL-30B-A3B-Instruct/ui2code-real \
  --backend vllm \
  --model Qwen3-VL-30B-A3B-Instruct \
  --base-url http://127.0.0.1:8001/v1

python run.py render \
  --run-dir runs/Qwen3-VL-30B-A3B-Instruct/ui2code-real

python run.py evaluate \
  --run-dir runs/Qwen3-VL-30B-A3B-Instruct/ui2code-real \
  --judge-backend vllm \
  --judge-model Qwen3-VL-30B-A3B-Instruct \
  --judge-base-url http://127.0.0.1:8001/v1
```

The same Python entry also supports staged cluster jobs. Render-only and
local-metric evaluation stages do not require dummy model arguments:

```bash
# GPU generation job
python run.py design2code web2code ui2code-real \
  --output-root runs/my-vlm \
  --stages generate \
  --backend vllm --model my-vlm \
  --base-url http://127.0.0.1:8000/v1

# CPU/browser job
python run.py design2code web2code ui2code-real \
  --output-root runs/my-vlm \
  --stages render

# Judge/metric job
python run.py design2code web2code ui2code-real \
  --output-root runs/my-vlm \
  --stages evaluate \
  --judge-backend openai-compatible \
  --judge-model YOUR_JUDGE_MODEL \
  --judge-base-url https://api.openai.com/v1
```

Use `--overwrite` only when previous artifacts should be regenerated.

## Direct Transformers and custom deployments

```bash
python run.py generate ui2code-real \
  --run-dir runs/local-model/ui2code-real \
  --backend transformers \
  --model /path/to/model \
  --trust-remote-code \
  --workers 1
```

The command backend receives one JSON object on stdin:

```json
{
  "prompt": "...",
  "image_paths": ["/absolute/image.png"],
  "max_tokens": 8192,
  "temperature": 0.0,
  "system_prompt": null
}
```

It may return plain text or `{"output": "..."}`:

```bash
python run.py generate ui2code-real \
  --run-dir runs/custom/ui2code-real \
  --backend command \
  --model custom \
  --command 'python /path/to/adapter.py'
```

## Output layout

```text
runs/<model>/<benchmark>/
├── run.json
├── predictions.jsonl
├── raw/
├── html/
├── sites/
├── rendered/
├── renders.jsonl
├── scores.jsonl
├── generation_summary.json
├── render_summary.json
└── evaluation_summary.json
```

Raw model responses are always retained. HTML extraction and React wrapping
produce derived artifacts without destroying the raw output.

## Evaluation fidelity

### Design2Code

The runner materializes reference HTML from the local data and invokes the
vendored `visual_eval_v3_multi` implementation. It reports the five official
dimensions. The source code and placeholder image are project-local. The
OpenAI CLIP ViT-B/32 checkpoint is not present in the source checkout and may
still be downloaded on first use unless it is already cached.

The released `visual_score.py` contains a whitespace error around its
empty-block handling branch. The isolated driver repairs only that known
syntax error in memory; it does not modify the vendored reference file.

### Flame

The released Flame evaluator is incomplete: its shell script refers to missing
files, and the visual metric expects an unpublished image-embedding service.
The available reference evaluator and 80-item dataset are stored locally.

If `FLAME_EMBEDDING_URL` is set to a compatible `/infer` endpoint returning
`last_hidden_state_list`, MultimodalCode uses the Flame embedding-cosine
method. Otherwise it emits a clearly labelled **non-official pixel-cosine
fallback**. The code score and pass@k aggregation follow the released Flame
logic.

```bash
export FLAME_EMBEDDING_URL=http://HOST:PORT/infer
```

Use multiple samples for meaningful pass@k:

```bash
python run.py generate flame-react-eval \
  --run-dir runs/my-vlm/flame-react-eval \
  --backend vllm --model my-vlm \
  --base-url http://127.0.0.1:8000/v1 \
  --samples 5 --temperature 0.8
```

Flame JSX is converted to a browser harness using pinned React and Babel CDN
assets. The current machine's package proxy returned HTTP 403 when those
browser assets were requested, so they could not be vendored in this pass.
Flame rendering therefore still needs access to those pinned URLs. Pages
importing additional third-party packages may require a custom React renderer.

### Web2Code

The ten questions and four aggregate dimensions follow the released Web2Code
visual-judge implementation. The local data contains one prompt per 1,198
unique image, not the full 5,990-row upstream evaluation file; results must be
described as the UI2Code_N-preprocessed Web2Code subset.

## Tests

Core tests do not require a GPU or browser:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```
