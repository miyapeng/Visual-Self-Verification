# Executable acceptance plans and Winston repair check

The acceptance runner now completes the SmartRecruiters V3/V4 pilot with a
model-generated Winston test and independent RS/CP judgments. This validates
the evaluation path; it does not mean the agent's repair passed.

## Implementation

- `acceptance.py` supplies the executor vocabulary as an output schema. Model
  check assignments are `check_id`/`assertions` rows; Python converts them to
  the existing executor map without rewriting actions or the raw response.
- `catalogue.py` includes that schema in the existing JSON-object plan request.
  The plan must cover every necessary and supplementary repair goal exactly
  once or explicitly mark it unresolved. Invalid output fails directly.
- The plan prompt requires target-specific visual evidence and source-grounded
  routes. Unspecified destinations are reached through UI controls.
- `states.py` scopes RS/CP inputs and image attachments to their targets.
  Earlier observations in the same workflow can supply pre-action context.
  Pass/fail still requires the assigned result node; unrelated workflows and
  later nodes are rejected. This permits a legitimate Load More before/after
  comparison that the previous validation rejected.
- The default plan profile is now `gpt54`, with low reasoning and a 32,768-token
  plan allowance. Other default metric profiles are unchanged. No scoring
  formula, retry loop, model client, or version reconstruction system was added.

## Actual pilot

The frozen [plan](../runs/vsv_eval/plan_grounded_gpt54_low_smartrecruiters_20261004/state_spec.json)
contains 27 necessary goals, five supplementary repair goals, seven workflows
and 15 nodes. `homepage_hero_content` remains unresolved because task text and
reference content conflict. Its absence is not treated as a pass.

Plan generation and RS/CP used **GPT-5.4**, reported by the endpoint as
`gpt-5.4-2026-03-05`. GUI grounding used
`gemini-3-flash-preview-nothinking`; it only selected current controls for
prescribed actions. It did not assign acceptance labels.

Both V3 and V4 executed all 15 nodes, with zero blocked nodes and zero evaluator
errors. Their setups, action records, goal IDs and assertion rules match.
The evaluated transition is Edit 308, associated with the original Winston
check `verification-0283-0286-2f3e250cc0`.

The generated Winston workflow starts at `/`, clicks Product, clicks Winston AI,
then scrolls and captures the full page. Its repair observation is
`V3/V4:workflow:wf_nav_to_winston:node:1`. The actual URL is `/winston-ai`;
the model did not need to invent a route. Each RS request receives one repair
criterion and nine image views, including its own reference and actual capture.

GPT-5.4 labels `repair_winston_testimonial` **fail → fail**. Inspection of the
actual captures agrees: V3 contains a solid block, while V4 removes the header
brandmark instead of restoring the Starbucks mark visible in the reference.
The criterion explicitly excludes deleting required reference content as a repair.

The unchanged metric functions produce:

| Metric | Pilot result | Counts |
| --- | --- | --- |
| RS | 0% | 0 successes, 1 failure, 0 unknown |
| CP | 93.33%, incomplete | 14 preserved, 1 labelled regression, 5 unknown |

The CP regression label is `homepage_case_studies`; capture stability and label
reliability need further verification before interpreting it as a code regression.
This pilot does not rerun VC/CV/BDA or establish a full-trajectory score or judge
accuracy. Unknown interactions and the unresolved criterion remain recorded.

## Evidence and validation

- [Raw plan response](../runs/vsv_eval/plan_grounded_gpt54_low_smartrecruiters_20261004/judge_cache/plan_review/5a17ee91dac88fbeacbfaac6317c110b2adc3e4330fff9e9a9d77d2762e27e4f.json)
- [Repair results and response references](../runs/vsv_eval/acceptance_gpt54_validated_smartrecruiters_20261004/repair_results.json)
- [Computed RS/CP metrics](../runs/vsv_eval/acceptance_gpt54_validated_smartrecruiters_20261004/repair_metrics.json)
- [V3 state judgments](../runs/vsv_eval/acceptance_gpt54_validated_smartrecruiters_20261004/state_results/V3.json)
- [V4 state judgments](../runs/vsv_eval/acceptance_gpt54_validated_smartrecruiters_20261004/state_results/V4.json)

The final judgment run reused the immutable browser execution caches from
`acceptance_gpt54_smartrecruiters_20261004` after the context-validation fix.
Original executions and rejected responses remain intact. The repair loader
validated version, resource, execution, input and image hashes.

Earlier trials exposed real limitations: Gemini Pro requests timed out or
returned invalid plans; Flash produced a legal plan but missed the repair region
and labelled an unrelated viewport as passing. An initial GPT plan guessed a
route, and a medium-reasoning request returned empty output. These trials are
preserved and are not the reported result. There is no automatic correction loop.

Tests: **205 passed, 6 skipped** in `tests/test_vsv_*.py`; skipped tests require
unavailable archived fixtures. The tests cover executable output, duplicate IDs,
missing repair goals, unchanged raw records, scoped images and valid pre-action
context. `git diff --check` passed.

## Commands

Credentials are read from `OPENAI_BASE_URL` and `OPENAI_API_KEY`. Use a fresh
output directory for each new plan. The tested server environment is:

```bash
export PYTHONPATH=src:/tmp/vsv-crop-test-deps-20261001:/tmp/vsv-process-test-deps-20261001
export PATH=/tmp/vsv-viewer-browser/node_modules/node/bin:"$PATH"
export VSV_PLAYWRIGHT_PACKAGE=/tmp/vsv-viewer-browser/node_modules/playwright
export VSV_CHROMIUM=/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome
export LD_LIBRARY_PATH=/tmp/vsv-viewer-browser/libs/unpacked/usr/lib/x86_64-linux-gnu

python3 scripts/vision2web/score_vsv.py \
  --prepare-plan \
  --task-root data/vision2web/extracted/frontend/smartrecruiters \
  --catalogue runs/vsv_eval/plan_gpt54_smartrecruiters_20261004/catalogue.json \
  --state-spec runs/vsv_eval/plan_gpt54_smartrecruiters_20261004/seed_state_spec.json \
  --output-dir runs/vsv_eval/new_model_plan

python3 scripts/vision2web/score_vsv.py \
  --rounds-json runs/vsv_eval/verification_minimal_smartrecruiters_20261003/verification_rounds.json \
  --catalogue runs/vsv_eval/plan_grounded_gpt54_low_smartrecruiters_20261004/catalogue.json \
  --state-spec runs/vsv_eval/plan_grounded_gpt54_low_smartrecruiters_20261004/state_spec.json \
  --config runs/vsv_eval/plan_grounded_gpt54_low_smartrecruiters_20261004/state_config.json \
  --workers 2 --port 18963 \
  --output-dir runs/vsv_eval/new_acceptance_execution

python3 -m pytest -q tests/test_vsv_*.py
```
