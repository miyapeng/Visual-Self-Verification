# Training integration validation — 2026-10-09

The local implementation adds prefix-conditioned teacher continuation and
ms-swift export, fixed-evidence diagnosis and Web execution rewards, and a verl
ToolAgentLoop/V1 GRPO extension. The benchmark scoring definitions are unchanged.
See [the training guide](../docs/VSV_TRAINING.md) for contracts and commands.

## Verification performed

| Check | Result |
| --- | --- |
| Combined evaluator, training and backend test run | 343 passed, 8 skipped |
| Final focused run after browser/pixel restoration changes | 17 passed |
| Pinned Swift loss implementation on CPU | Student and tool context masked; teacher response and final suffix supervised |
| Pinned verl source interfaces and Hydra composition | Passed |
| Actual Chromium restore/edit/recheck/acceptance/export smoke | Passed, zero model API calls |
| Real model processor/template and GPU optimizer step | Not run |
| Training effectiveness or benchmark improvement | Not measured |

Commands:

```bash
PYTHONPATH=src:.local/cache/commit-test-deps \
  /root/miniconda3/envs/swemm/bin/python -m pytest -q \
  tests/test_vsv*.py tests/test_verification_training.py \
  tests/test_training_framework_contracts.py tests/test_openai_backend.py

PYTHONPATH=.local/cache/training-config-deps \
  /root/miniconda3/envs/swemm/bin/python scripts/training/check_upstream.py \
  --sources .local/upstream/training

/root/miniconda3/envs/swemm/bin/python scripts/training/smoke.py \
  --output .local/runs/training-smoke-20261009-validated
```

The final smoke artifacts are under
`.local/runs/training-smoke-20261009-validated/`:

- `checkpoint.json` and `snapshot/environment.json`: actual native prefix and
  restorable state, with an expressed student diagnosis before the repair.
- `teacher/suffix.json`, `teacher/tool_records.json`: two real tool executions
  requested by the explicitly scripted teacher.
- `teacher/evaluation/before-acceptance.json` and `after-acceptance.json`:
  independent execution evidence for the target and preserved interaction.
- `teacher/evaluation/reward.json`: reward 1.0; one target changed from fail to
  pass, and the previously passing interaction remained passing.
- `swift_row.json`, `verl_row.json`: exported framework inputs.
- `summary.json`: machine-readable validation summary.

These fixture labels and scripted continuations are not model-produced research
demonstrations. The original workspace and checkpoint files remained unchanged.
An actual model API would be used only by an explicitly launched collection or
review command.

## Existing trajectory reuse

Candidate extraction was run on
`runs/vsv_eval/verification_rounds_smartrecruiters_20261002/verification_rounds.json`.
It produced 56 candidate references: 25 evidence, 25 diagnosis and 6 repair sites.
Output: `.local/runs/training-handoff-candidates-20261009.json`.

These are candidate locations, not 56 validated training handoffs. They still
require original visible message boundaries, actual images, appropriate private
evaluation inputs and, for execution, a successfully restored environment. No
SmartRecruiters benchmark trajectory was added to the training data.

## Remaining validation and scope

The machine has no GPU, and no funded model API was used. The framework adapter
test checks the native output contract with test doubles; the source check uses
the pinned upstream source. Neither substitutes for loading Qwen3.5's real
processor, inspecting encoded labels/pixels, and completing an optimizer step.

The local GRPO implementation consumes checkpoint datasets collected with an
explicit policy revision. Automatic in-step state mining and branching is not
implemented; refresh the dataset between collection/training rounds. Executable
training restore currently covers resettable Web applications. Other benchmark
evaluation adapters are not being represented as live training environments.

No historical scores, original trajectories, or model weights were modified.
