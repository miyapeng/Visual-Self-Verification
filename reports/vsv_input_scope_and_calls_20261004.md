# Scoped evaluation inputs and model call accounting

The current implementation removes redundant input and reference annotation
without combining independent metrics or changing any scoring formula.
Historical source data, frozen plans and responses remain intact.

## Changes

- `states.py` removes exact goals before scoping RS/CP model evidence. Assigned
  nodes and preceding observations in the same workflow remain available.
  Rule-derived labels are still joined by the program after model validation.
- `check_stages.py` retains the full catalogue for VC and only matched criteria
  for CV/BDA. Raw events, target identities, task grounds for unmatched checks,
  image sources and cutoffs are preserved. No verdict is shared between judges.
- `evaluation.py` validates saved checks before reference annotation. Full reuse
  makes no annotation request; partial reuse annotates only missing rounds.
  Online runs no longer write an unused copy of each base evidence packet.
  Offline runs retain their pending packets. Readers validate both original
  archived inputs and the new scoped inputs against recorded prompts.
- New plan generation limits supplementary goals to the supplied repair
  transitions. Necessary CP criteria and preparation actions remain available.
  Existing frozen plans are not pruned after approval. This change was tested
  with a synthetic plan; this trial reused its existing frozen real plan.
- `catalogue.py` skips pixel conversion/cropping when image views already exist.
- `JudgeClient` appends minimal call references to `model_calls.jsonl`.
  `score_vsv.py` writes `model_calls.json` for its invocation, including errors.
  Actual HTTP attempts, retries, cached reads and response errors are separate.
  It retains the existing model client, raw responses and retry policy.

## Validation

The full related test run passed **211 tests**, with **6 skipped** for missing
archived fixtures. The browser tests used the existing Playwright runtime.
After the final reference-map validation edit, all **58** protocol and metric
tests passed again. `git diff --check` passed.

Added cases cover pending-goal evidence scope with a required workflow baseline,
matched and unmatched target inputs, full/partial reuse, focused supplementary
plans, image-view reuse and HTTP/cache/retry/failure accounting. Archived Winston
check records and the previous V3/V4 repair result still pass provenance checks.

Real CP inputs retain the same nine pending goals per version while reducing
image views **45 to 36**, evidence nodes **13 to 8**, and JSON characters
**61,512 to 42,065**. These are input-size measurements, not measured token
savings or proof of improved semantic accuracy.

## Actual bounded trial

Source: SmartRecruiters, Claude Opus 4.8 with Claude Code. This trial evaluates
the Winston round ending at event 286 and the Edit 308 transition V3 to V4.
It is not a new full-trajectory evaluation.

All seven new semantic requests used **GPT-5.4**. Winston previously used
Gemini Pro, so its new target labels are not a controlled comparison of input
pruning alone. The new VC response retains four targets: two matched content
goals, a partially inspected navigation goal and an unmatched visible defect.
CV/BDA receive three matched catalogue entries instead of all 27; the unmatched
target keeps its identity, condition and original task/evidence.

The state trial uses the same GPT-5.4 profile and frozen browser captures as
the previous state assessment. It performs no new GUI grounding or deployment.
An initial invocation used the default port and was rejected by the replay
cache before any model request. Re-running with the original port 18963 reused
the immutable executions without altering their specifications.

Validated state results:

| Metric | Current bounded result | Counts |
| --- | --- | --- |
| RS | 0% | 0 successes, 1 failure, 0 unknown |
| CP | 100%, incomplete | 13 preserved, 0 regressions, 6 unknown |

RS remains fail to fail. CP labels changed for two goals:

| Goal | Previous before/after | Current before/after |
| --- | --- | --- |
| `homepage_case_studies` | pass / fail | fail / pass |
| `careers_browse_departments` | pass / pass | pass / unknown |

The changed labels also change which baseline checks enter the CP denominator.
The higher conditional CP score is not evidence that input pruning improved
accuracy or that preservation is complete. Model consistency and these two
interpretations remain unvalidated. The trial preserves both sets of responses;
it does not add voting or silently overwrite historical labels.

## Actual model requests

| Request | Stage | Location |
| --- | --- | --- |
| 1 | `visual_vc` | Winston round, cutoff 286 |
| 2 | `visual_cv` | Winston round, cutoff 286 |
| 3 | `visual_bda` | Winston round, cutoff 286 |
| 4 | `visual_rs` | V3, `repair_winston_testimonial` |
| 5 | `visual_rs` | V4, `repair_winston_testimonial` |
| 6 | `visual_cp` | V3, nine pending necessary checks |
| 7 | `visual_cp` | V4, the same nine pending necessary checks |

Total: **7 HTTP requests**, **0 request retries**, **0 response errors**.
Repeating the Winston invocation produced **3 cache hits**, **0 HTTP requests**
and identical labels. Running `--reuse-checks` without a reference map produced
**0 model calls**, including annotation, and identical metrics. Summation,
VCS, exact assertions and replay-cache reuse produced no model requests.
The cumulative call log therefore contains ten logical judge invocations:
seven new requests and three cached reads. Tests use mocked HTTP responses
and are excluded from this real trial's request count.

- [Every call, context, timestamp and original response](../runs/vsv_eval/optimization_smartrecruiters_20261004/model_calls.json)
- [Winston inputs and results](../runs/vsv_eval/optimization_smartrecruiters_20261004/checks/scores.json)
- [Validated version results](../runs/vsv_eval/optimization_smartrecruiters_20261004/states/repair_results.json)
- [Computed RS/CP metrics](../runs/vsv_eval/optimization_smartrecruiters_20261004/states/repair_metrics.json)
- [Saved-result reuse with no model calls](../runs/vsv_eval/optimization_smartrecruiters_20261004/reuse/model_calls.json)

## Reproduction

Run from the project root with credentials in `OPENAI_BASE_URL` and
`OPENAI_API_KEY`; no credential is stored in these artifacts.

```bash
export PYTHONPATH=src:/tmp/vsv-crop-test-deps-20261001:/tmp/vsv-process-test-deps-20261001

python3 scripts/vision2web/score_vsv.py \
  --rounds-json runs/vsv_eval/optimization_smartrecruiters_20261004/checks/rounds.json \
  --catalogue runs/vsv_eval/protocol_smartrecruiters_20261003/catalogue_atomic_retry/catalogue.json \
  --task-root data/vision2web/vsv_eval_fixtures/frontend__smartrecruiters__claude-opus-4-8__self_verify/agent_visible \
  --reference-map runs/vsv_eval/optimization_smartrecruiters_20261004/checks/reference_mapping.json \
  --primary-profile gpt54 --allow-draft \
  --output-dir runs/vsv_eval/optimization_smartrecruiters_20261004/checks

python3 scripts/vision2web/score_vsv.py \
  --rounds-json runs/vsv_eval/verification_minimal_smartrecruiters_20261003/verification_rounds.json \
  --catalogue runs/vsv_eval/plan_grounded_gpt54_low_smartrecruiters_20261004/catalogue.json \
  --state-spec runs/vsv_eval/acceptance_gpt54_validated_smartrecruiters_20261004/spec.json \
  --config runs/vsv_eval/plan_grounded_gpt54_low_smartrecruiters_20261004/state_config.json \
  --port 18963 --workers 2 \
  --output-dir runs/vsv_eval/optimization_smartrecruiters_20261004/states
```

Existing trial outputs use cached requests. A fresh output directory makes new
requests; a state run in a fresh directory also requires its existing runtime
and browser environment. The CLI's report covers that invocation. To regenerate
the cumulative trial report, without a model request:

```bash
PYTHONPATH=src python3 - <<'PY'
from multimodalcode.vsv_eval.judge import write_model_call_report
write_model_call_report('runs/vsv_eval/optimization_smartrecruiters_20261004')
PY
```
