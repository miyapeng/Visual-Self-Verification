# Verification distillation and local GRPO

This implements a first training integration for **ms-swift → verl**, with the
existing verification evaluator supplying supervision. It does not change the
six benchmark metrics. The current execution adapter supports resettable Web
applications; fixed-evidence diagnosis does not require a live application.

CPU tests cover browser restoration, repair acceptance, data export, message
masks and the native rollout output contract. A distributed optimizer step,
Qwen3.5's actual image processor/template, and training improvements have **not**
been validated on this machine, which has no GPU. No teacher or Judge API was
called for the integration smoke test.

## Components

| File under `src/multimodalcode/training/` | Responsibility |
| --- | --- |
| `handoff.py` | Immutable handoff identities, evidence hashes, candidate boundaries, SFT/RL exports |
| `environment.py` | Isolated Web workspace, tools, browser observations, reset/replay checkpoints |
| `collector.py` | Native teacher/student continuation, raw responses, independent teacher review |
| `reward.py` | Existing verification labels and acceptance converted to local rewards |
| `verl_integration.py` | ToolAgentLoop subclass, native dataset loading, weighted GRPO advantages |
| `cli.py` | Preparation commands |

The collector extends the existing `OpenAICompatibleBackend` with native chat
history. Evaluator requests still use `JudgeClient`, including its profiles,
cache, original responses and call log. Evidence extraction reuses
`extract_verification_rounds`; VC/CV and BDA reuse the existing stage prompts and
validators. Acceptance reuses the browser actions, assertions and state Judge.

Upstream sources are pinned in `configs/training/upstream_sources.json`:

- [ms-swift v4.0.4](https://github.com/modelscope/ms-swift/tree/ba4631cd60e744e0c1a709068758480abfc429a5),
  using its [assistant message loss flags](https://github.com/modelscope/ms-swift/blob/ba4631cd60e744e0c1a709068758480abfc429a5/swift/loss_scale/base.py).
- [verl fc72e2f](https://github.com/verl-project/verl/tree/fc72e2f384212470868a9928b62baedb9bcc3ff0),
  using its [ToolAgentLoop](https://github.com/verl-project/verl/blob/fc72e2f384212470868a9928b62baedb9bcc3ff0/verl/experimental/agent_loop/tool_agent_loop.py)
  and V1 trainer extension. The integration delegates continuous token handling
  and multimodal processing to this implementation.

## Shared handoffs

A handoff contains the task identity and split, exact visible native messages,
student policy revision, environment restore record, private evaluator inputs
and source references. Its ID hashes these fields. Image bytes and evaluator
input files are hashed separately and checked when loading. Paths must be
accessible on every worker.

Messages use `system`, `user`, `assistant` and `tool`; local observations use
`{"type":"image","image":"/absolute/path.png"}` blocks. Native `tool_calls`
and `tool_call_id` pair actual actions and returns. Missing images or incomplete
tool returns are errors. A handoff must end at a complete user/tool boundary.
Do not insert fictional user messages to split an assistant's explanation from
an action in the same native turn.

The decision kinds are `evidence`, `diagnosis`, `repair` and `task`. Repair
handoffs additionally reference the expressed diagnosis through
`source.diagnosis_message_indices`; the prefix must actually contain it.
These types organize training and rewards; they are not injected as deployment
stage labels into the agent's conversation.

Example checkpoint specification, consumed by `prepare.py checkpoint`:

```json
{
  "task_id": "training-task-001",
  "benchmark": "independent-web-training",
  "kind": "diagnosis",
  "split": "train",
  "policy_revision": "student-weights-r0",
  "messages": [
    {"role": "user", "content": [
      {"type": "text", "text": "The actual task and visible history go here."},
      {"type": "image", "image": "/data/training/observation.png"}
    ]}
  ],
  "evaluation": {
    "catalogue": "/data/training/catalogue.json",
    "diagnosis_packet": "/data/training/diagnosis_packet.json",
    "observation_source": "/data/training/observation_response.json",
    "judge_config": "/data/training/judge_config.json",
    "primary_profile": "configured-judge-profile"
  },
  "source": {"episode_id": "original-episode-id", "cutoff_event_id": 42}
}
```

Supply actual native history, not a prose summary. `diagnosis_packet` uses the
existing evaluator packet with target identities, `observation_verdicts`, raw
evidence events and `image_order`. Its observation source must be an independent
assessment made before collecting the candidate diagnosis. Agent-observation
pixels must belong to the visible prefix; future events are rejected. Old agent
answers are removed from the private BDA input, and the sampled answer receives
new event references. Independently labelled failures and correct candidates
are both useful. Judge labels still need calibration; they are not automatically
ground truth because a model produced them.

For execution handoffs, add the environment record produced by `snapshot`.
For fresh student collection, `--collect-boundaries --policy-revision REV`
saves complete visible histories and environment snapshots before subsequent
assistant turns. Select appropriate boundaries and bind their evaluator inputs
with `checkpoint`; the collector does not invent diagnosis labels or target IDs.
`candidates` locates possible handoff sites in existing verification rounds.
A candidate inside a native assistant turn is not automatically resumable.

## Rewards

Rewards are on a 0–1 scale except for regression penalties. They are separate
training objectives, not a sum of VC/CV/BDA/RS/CP/VCS percentages.

| Kind | Calculation and evidence |
| --- | --- |
| Evidence | Unique newly fully covered conditions with valid method and evidence, divided by the fixed conditions still uncovered at the handoff. Uses VC/CV after rule candidates and model round extraction. Repeated checks cannot increase the numerator. |
| Diagnosis | Mean accuracy across the present pass/fail classes, using existing BDA alignment against fixed independent observation labels. Missing or uncertain agent judgments are incorrect; unavailable observation truth is a data error. |
| Repair | Fraction of initially failing target conditions that pass after an actual edit, minus `regression_weight ×` the fraction of previously passing conditions that regress. Default weight: 1. Correctly preserving an initially passing target without editing earns 1 before regression penalties. |
| Task | `score / max_score` from the configured official runner executed against the resulting workspace. The agent cannot supply this result. |

Additional private evaluator fields:

- Evidence: `catalogue`, `task_prompt`, `judge_config`, `primary_profile`, and
  `covered_check_ids` from evaluation of the same prefix.
- Repair: `catalogue`, `target_ids`, and `acceptance_probes`, plus Judge settings
  for visual criteria. Each probe has `check_id`, `route`, ordered `actions`, and
  optional executable `assertions` in the existing replay format. Probes must
  cover the fixed catalogue exactly once. Exact assertions avoid a Judge call;
  visual states use the existing state Judge and actual captured pixels.
- Task: `official_command` as an argument list and optional `official_timeout`.
  The command receives `{"workspace":"...","task_id":"..."}` on stdin and
  returns `{"score":number,"max_score":positive_number}` on stdout. Adapt the
  selected benchmark's existing official runner to this small contract; no
  benchmark-specific training score is assumed.

Repair acceptance runs before generation and again afterward in separate
browser contexts. It is not exposed to the student as extra observations.
Unresolved requirements, unavailable pixels, unreproducible states or failed
Judge calls stop evaluation. They are not silently converted to zero or one.
Ordinary failed agent actions remain recorded attempts. RL budget exhaustion
retains the actual generated token prefix and evaluates the reached state; it
does not execute a partially emitted tool call. Teacher collection rejects
truncated demonstrations because the SFT mask approves complete messages.

## Stage 1: collect, review, export, train

Use a dedicated Python 3.12 environment for ms-swift. Install the hardware's
compatible PyTorch/CUDA stack, then `requirements/training-sft.txt`. The base
preparation environment only needs this package and its browser dependencies;
neither training framework is imported for ordinary evaluation.

```bash
python scripts/training/prepare.py snapshot \
  --workspace /data/training/source --output runs/training/initial
python scripts/training/prepare.py checkpoint \
  --spec /data/training/checkpoint_spec.json --output runs/training/checkpoint.json

export OPENAI_API_KEY='your-runtime-credential'
python scripts/training/prepare.py collect \
  --checkpoint runs/training/checkpoint.json \
  --model TEACHER_MODEL --base-url "$OPENAI_BASE_URL" \
  --output runs/training/teacher-001
python scripts/training/prepare.py audit \
  --checkpoint runs/training/checkpoint.json --collection runs/training/teacher-001 \
  --judge-config /data/training/judge_config.json --profile REVIEW_PROFILE
```

The collector never runs automatically on import. A collection stores native
suffix messages, executed tool records, original model responses and reward
evidence. The separate reviewer returns only approved assistant message
indices. A high final score does not automatically approve every teacher turn.
An empty approval list is not a usable SFT example.

A distillation manifest is a JSON list:

```json
[{"checkpoint":"/absolute/checkpoint.json",
  "suffix":"/absolute/teacher-001/suffix.json",
  "audit":"/absolute/teacher-001/audit.json"}]
```

```bash
python scripts/training/prepare.py export-sft \
  --manifest /data/training/sft_manifest.json --output runs/training/sft-data
export VSV_STUDENT_MODEL=/models/student
export VSV_SFT_DATA="$PWD/runs/training/sft-data/train.jsonl"
export VSV_SFT_OUTPUT="$PWD/runs/training/distilled"
bash scripts/training/train_sft.sh
```

All student assistant turns have `loss:false`; only approved teacher assistant
turns have `loss:true`. Tools, users and images are context under Swift's default
loss strategy. An unapproved intermediate teacher turn can remain as masked
context for a later approved turn. Unreviewed trailing turns are omitted.
The export uses Qwen/Hermes tool-call text and is intended for the Qwen3.5
prototype. Validate another model's template before using this SFT serializer.
Overlong examples must not be truncated across the handoff boundary. Inspect
Swift's encoded dataset counts and labels before starting a real experiment.

Merge/export the trained LoRA adapter with the pinned Swift tools before RL.
`VSV_DISTILLED_MODEL` must contain the merged model, tokenizer and processor,
not just a LoRA adapter directory. No second generic SFT stage is added.

## Stage 2: local and joint GRPO

Use a separate GPU environment with the pinned verl source and a compatible
vLLM/PyTorch stack. Install `requirements/training-rl.txt` and this repository
with `pip install -e .`. Browser workers also need Playwright Chromium.
The supplied one-GPU configuration is a launcher default, not a memory claim
for Qwen3.5-9B; choose GPU count, sharding and budgets for the actual hardware.

An RL manifest is a JSON list of `{"checkpoint":"/absolute/path.json"}` entries.
Task identities cannot cross train/validation/test splits. Test checkpoints are
never exported into training. Use separate training tasks; do not relabel a
benchmark's test tasks as training data. Refresh local prefixes from the current
student policy and pass its exact revision when exporting.

```bash
python scripts/training/prepare.py export-rl \
  --manifest /data/training/rl_manifest.json --policy-revision distilled-r1 \
  --kinds diagnosis --output runs/training/rl-data
export VSV_DISTILLED_MODEL=/models/merged-distilled-r1
export VSV_TRAIN_DATA="$PWD/runs/training/rl-data/train.parquet"
export VSV_VALIDATION_DATA="$PWD/runs/training/rl-data/validation.parquet"
export VSV_ROLLOUT_DIR="$PWD/runs/training/rollouts"
bash scripts/training/train_rl.sh
```

The default is diagnosis-only GRPO, with four continuations per checkpoint.
For the combined objective, supply equal checkpoint counts per enabled kind in
each split, export with `--kinds task evidence diagnosis repair`, then launch:

```bash
bash scripts/training/train_rl.sh \
  verification.include_task=true \
  'verification.local_kinds=[evidence,diagnosis,repair]' \
  verification.local_weight=1.0 \
  trainer.experiment_name=joint-verification-grpo
```

Rows are interleaved by kind; shuffling is disabled. Use train and PPO mini-batch
sizes that are multiples of the number of kinds. The trainer checks group
identities and refuses a batch missing a configured kind. verl computes GRPO
within each input's continuation group. Type weights multiply advantages
**after** normalization: `N / count(kind)` for task, and
`N / count(kind) × lambda / number_of_local_kinds` for local types. Sequence-mean
actor aggregation gives task mean plus lambda times the local-type mean. KL is
computed separately against the frozen reference initialized from the distilled
model. Do not replace that reference with the latest actor during a refresh.

The first implementation consumes an explicitly collected checkpoint dataset.
Refresh it between collection/training rounds; automatic in-step checkpoint
mining and branching inside the distributed trainer is not implemented. This
distinction matters when describing the method or claiming on-policy state
sampling. Resume actor/optimizer state through verl while retaining the original
distilled reference configuration.

Native response IDs, token masks, rollout log probabilities and multimodal data
come directly from `ToolAgentLoop`. The wrapper only attaches reward/metadata.
Every rollout saves `policy_tokens.json`, suffix messages, actual tool outputs,
an evaluator-compatible timeline and `reward.json`. Text decoding is for
evidence logging only; it is never used to rebuild the policy sequence.

## Environment limits and validation

The Web adapter copies a self-contained workspace and starts either a static
server or a configured server command. Command arguments may contain `{port}`;
omit a fixed port when restoring concurrent branches. Runtime configuration can
also select viewport, Chromium executable and action timeout. All Playwright
operations for one branch stay on one dedicated thread.

Restoration replays recorded actions and screenshot operations, then compares
rendered pixels, DOM, form values, scroll position, storage and code hashes. Screenshot operations
matter because Playwright can alter DOM attributes while restoring the caret.
This is a checked reset/replay contract, not a snapshot of arbitrary JavaScript
memory, backend databases, authentication providers, clocks or game state.
Use deterministic, resettable training fixtures. Dependencies must be available
to the configured server; the adapter does not install them during rollouts.

The 3D, SWE-MM and GameDevBench **evaluation** adapters remain available, but this
change does not claim live training restore support for those environments.
Fixed-evidence diagnosis can use their genuinely recorded visible observations;
execution-based training needs a corresponding state adapter and official runner.
Historical image reconstructions retain their reconstruction provenance and are
not proof of the original model's exact visual context.

```bash
PYTHONPATH=src python -m pytest -q \
  tests/test_verification_training.py tests/test_training_framework_contracts.py
python scripts/training/smoke.py --output runs/training/cpu-smoke
```

The smoke test uses a scripted teacher and exact DOM assertions. It restores a
browser interaction, repairs one defect, verifies preservation, exports both
formats and checks that source files remain unchanged. It makes zero model API
calls and is not a training-quality experiment. The source contract check is:

```bash
python scripts/training/check_upstream.py --sources /path/to/pinned-source-trees
```

It requires `hydra-core`, the pinned source files and verl's YAML configs. It
checks the extension symbols, runs Swift's actual message loss implementation,
and composes the verl config. Before a research run, still execute a real
processor/label inspection and one GPU optimizer step, then compare the original
model, distilled model, task-only GRPO and joint method on independent tasks.
