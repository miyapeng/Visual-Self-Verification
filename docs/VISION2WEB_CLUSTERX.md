# Vision2Web on ClusterX

## What is adapted

Vision2Web's frozen evaluator remains byte-for-byte identical to upstream
commit `577f9397b3db8fc6d828adde254a830caa65d515`. The released code expects a
Docker sandbox and issues this lifecycle:

1. create and start the sandbox image;
2. copy the generated workspace into `/workspace`;
3. run `start.sh` and wait for port 3000;
4. invoke Claude Code and `playwright-cli` for the released interactive tests;
5. run the released visual VLM judge;
6. copy `test_results` back and stop the sandbox.

On ClusterX, the task is already a container created from that sandbox image.
`evaluate/vision2web/clusterx_transport/docker` maps only those Docker commands
onto the current task container. It does not replace the functional tester,
prompts, workflows, screenshots, visual scorer, or aggregation. Unsupported
commands return exit code 125 instead of guessing.

The transport also preserves Docker's host/container separation. Before
evaluation, `run_clusterx.py` copies the generated `/workspace` into a
persistent case directory under `/data`. The official evaluator then treats
that directory as the host result and `/workspace` as the sandbox workspace.
This is why simply deleting all `docker` calls would not be equivalent.

## ClusterX task configuration

Use a normal single-replica **CPU task** unless the model server also runs in
this task. Browser execution and the official judges call remote endpoints and
do not themselves require a GPU.

- Image:
  `registry.pjlab.org.cn/ccr-t-llm-frontier/vision2web:official-577f939`
- Recorded digest:
  `sha256:3d2fa9b0999a1fbb10c9d4ccc653c7d5560a713668d6c18c80d0d06e36e43056`
- Mount the persistent AFS volume at `/data`.
- Shared memory: 8 GiB or more is recommended for Chrome; 60 GiB is safe.
- No Docker daemon, privileged mode, or GPU is required for this adapter.
- Use one case per job while validating the pipeline. Later, submit multiple
  ordinary CPU jobs with controlled concurrency.

ClusterX starts the image as root but does not expose Chromium's kernel
sandbox. The transport therefore points Playwright CLI at
`evaluate/vision2web/clusterx_transport/playwright-cli.config.json`, whose
only setting is `chromiumSandbox: false`. This is a browser process-isolation
adaptation for the outer container; it does not alter the frozen workflows,
screenshots, judge prompts, or score aggregation. A deterministic check is
available at `scripts/vision2web/diagnose_official_playwright.sh`.

Select the immutable tag in the ClusterX image field. The
`VISION2WEB_RUNTIME_IMAGE_DIGEST` variable is an explicit run attestation and
is recorded with every result; it does not inspect the platform's container
runtime. The immutable tag must therefore also be selected in the ClusterX UI.

## Endpoint requirements

Three model roles are deliberately separate:

- Coding model: used by OpenHands to generate the site. Its `--base-url` must
  be reachable from the ClusterX job; `127.0.0.1` only works when the server is
  in the same job.
- Functional judge: the released evaluator invokes **Claude Code**, so the
  base URL must speak the Anthropic API protocol. A plain OpenAI-compatible
  vLLM endpoint is not enough unless an Anthropic-compatible gateway is placed
  in front of it.
- Visual judge: an OpenAI-compatible multimodal chat-completions endpoint.

Using judge models other than the released defaults is useful for research,
but must be reported as a nonstandard judge configuration.

Inject secrets through ClusterX environment variables; do not put them in the
startup command:

```text
OPENAI_API_KEY                    coding-model token (if needed)
VISION2WEB_FUNCTIONAL_API_KEY     functional-judge token
VISION2WEB_FUNCTIONAL_BASE_URL    Anthropic-compatible URL
VISION2WEB_FUNCTIONAL_MODEL       functional model name
VISION2WEB_VISUAL_API_KEY         visual-judge token
VISION2WEB_VISUAL_BASE_URL        OpenAI-compatible URL
VISION2WEB_VISUAL_MODEL           multimodal visual model name
VISION2WEB_RUNTIME_IMAGE_DIGEST   sha256:3d2fa9b...e43056
```

Transport audit logs hash environment values and never write raw API values.

## First run: one case

Use this as the ClusterX startup command after supplying the environment
variables above. Replace the coding endpoint and model with the reachable
service you want to test.

```bash
set -euo pipefail
cd /data/miyapeng/mmcode/MultimodalCode
export PYTHONPATH="$PWD/src"

python3.12 evaluate/vision2web/run_clusterx.py --preflight

python3.12 -m multimodalcode.agent_harness.cli run \
  vision2web frontend/afl \
  --model litellm_proxy/Qwen3.5-9B \
  --base-url http://REACHABLE_LITELLM_HOST:4000 \
  --openhands-profile official \
  --openhands-max-retries 2 \
  --wall-time 7200 \
  --output-root /data/miyapeng/mmcode/MultimodalCode/runs/agents

python3.12 evaluate/vision2web/run_clusterx.py \
  --case frontend/afl \
  --workspace /workspace \
  --model-label Qwen3.5-9B \
  --output-root /data/miyapeng/mmcode/MultimodalCode/runs/vision2web-official \
  --runtime-image-digest "$VISION2WEB_RUNTIME_IMAGE_DIGEST"
```

The evaluator result and transport audit are written under:

```text
runs/vision2web-official/<model>/<task>/<project>/
├── clusterx_evaluation.json
├── official_results/
│   └── <task>/<framework>/<model>/<project>/
│       ├── evaluation_result.json
│       └── test_results/
└── transport/
    ├── state.json
    └── transport.jsonl
```

The frozen `workflow.json` is read only by the trusted evaluator. It is never
copied into the agent-visible workspace.

The coding endpoint shown above is intentionally a LiteLLM proxy in front of
vLLM. The released OpenHands CLI accepts prototype pixels through its
`file_editor(view)` observation, but only advertises image viewing when its
model registry says `supports_vision=true`. A raw private vLLM model alias is
unknown to the frozen registry. The proxy's `/v1/model/info` response supplies
that metadata without patching OpenHands; direct raw-vLLM runs that would be
text-only are rejected by the official profile.

The repository smoke scripts can start this proxy inside the ClusterX case
container. The upstream image installs the LiteLLM SDK but not its proxy
extras, and its unpinned FastAPI is incompatible with that proxy entry point.
To avoid altering the frozen OpenHands environment, the scripts use the
separate reusable venv `.local/venvs/vision2web-litellm-proxy-py312`, pinned to
`litellm[proxy]==1.79.0` and `fastapi==0.115.14`. Only the OpenAI-compatible
request transport and `/v1/model/info` capability declaration pass through
this process; the OpenHands process continues to run with the packages from
the official image.

## Resume behavior

- If `evaluation_result.json` exists, the command returns a `resumed-skip`
  result without spending judge calls again.
- The generated site is staged to `/data` before the official evaluator is
  started. If a ClusterX job is interrupted after that point, a new job with
  the same case, model label, and output root reuses the staged workspace even
  when the new job's `/workspace` is empty.
- If interruption occurs during a workflow, the official evaluator restarts
  that case; it does not invent step-level resume semantics that upstream does
  not have.
- To intentionally rerun a completed case, use a new output root or archive
  the existing case directory first. Do not delete results implicitly.

## When the result can be called official

The scoring code is official, but the sandbox transport is new. Before making
an unqualified leaderboard claim, perform a paired validation on a machine
with Docker:

1. freeze one generated workspace and all model/judge endpoints;
2. run `run_official.py` through native Docker and `run_clusterx.py` through
   ClusterX using the same image digest;
3. compare HTTP readiness, generated screenshots (pixel hash or lossless
   image diff), `result.json` structure, visual-score records, and aggregate
   output;
4. include screenshot-only, interactive, and stateful multi-workflow cases;
5. retain both command traces and report nondeterminism from remote judges.

Until this paired check passes, the precise paper label should be:
**official Vision2Web evaluator with an audited ClusterX transport**.
