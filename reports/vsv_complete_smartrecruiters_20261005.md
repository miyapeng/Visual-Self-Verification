# Complete SmartRecruiters evaluation — 2026-10-05

## Scope

This run evaluates every extracted round in the local SmartRecruiters trajectory, including visual and text-only checks, and all six recorded repair transitions. All 25 rounds were processed: 23 contain evaluated checks and two were excluded as unrelated. All seven states, six repair transitions and 126 independent workflow nodes were executed.

The necessary catalogue remains frozen at 27 criteria. This experiment does not change the scoring formulas or convert unknown evidence into failure or success. Its original catalogue review is reused; all new model judgments use `gemini-3-flash-preview`.

## Reconstruction

Seven isolated states cover original events 215, 216, 227, 245, 262, 269, and 308. The two font edits are reversed from the archived first snapshot and replayed to confirm exact reproduction. The audited resource relocation is represented by its original command hash and explicit asset mount; no historical shell command is executed. All modified final files match the archived final snapshot. Original code and resource files remain untouched.

## Evaluation corrections

- Preserve actual checked conditions outside the fixed catalogue, including defects explicitly inspected in the raw response.
- Require BDA evidence to precede the cited diagnosis. An absent diagnosis does not erase actual observed evidence.
- Support a single original target referring to multiple fixed repair criteria. Every linked criterion must satisfy the existing repair conjunction.
- Execute horizontal scrolling explicitly and preserve initial workflow observations for before/after interaction context.
- Record image loading and external font/stylesheet requests. Evaluator network deadlines remain distinguishable from implementation failures.
- Resolve unique exact visible text when a planned HTML role is incorrect; ambiguous or semantically different labels still use the existing constrained GUI judge.
- Run isolated versions on separate ports using the existing worker limit and a separate JudgeClient instance per replay.
- Reset an earlier filter before selecting a case from a different industry. Only the affected independent workflow is rerun; unchanged workflows are retained with source hashes.
- Judge the required visual element itself, rather than treating surrounding text as proof that an empty visual block is correct.

- Serialize image-view publication so parallel workers never record hashes of partly written crops. Original image bytes are unchanged.
- Batch RS/CP judgments by the existing independent workflow, so unrelated pages are not judged in the same model response. Saved-result validation rejects duplicate or omitted criteria across batches. This corrected observed false negatives for the repaired pricing mark and testimonial header in this run.

## Artifacts

Run root: `runs/vsv_eval/complete_smartrecruiters_20261005/`.

- `reconstructed_versions/reconstruction.json`: all seven audited states.
- `checks_complete/scores.json`: all 25 extracted rounds, before repair-state joining.
- `state_spec_corrected.json`: final reviewed acceptance workflows and six transitions.
- `states_final/`: original complete browser execution for each state.
- `customer_replay/`: corrected customer workflow executions after resetting the filter.
- `states_corrected/`: validated combined execution evidence. Each replay records the hashes of both source executions.
- `states_grouped/`: final RS/CP judgments, grouped by the existing independent workflow. Browser evidence and metric formulas are unchanged.
- `alignment_complete/`: model-produced current target-to-repair-criterion references.
- `final/scores.json`: validated joined metrics.

Earlier execution directories and rejected model responses are retained as debugging evidence. They must not be mistaken for the final evaluation.

## Final results

| Metric | Result | Known / unknown |
|---|---:|---|
| VC | 74.07%–77.78% | 20 full, 5 partial, 1 absent, 1 unresolved / 27 |
| CV | 95.29% | 81 success, 4 failure, 7 unknown |
| BDA | 78.67% | 66 success, 19 failure, 7 unknown |
| BDA-V | 75.48% | 26 success, 10 failure, 3 unknown |
| BDA-T | 80.91% | 40 success, 9 failure, 4 unknown |
| RS | 87.50% | 7 success, 1 failure, 1 unknown |
| CP | 97.01% | 130 success, 4 failure, 8 unknown |
| VCS | 57.14% | 12 success, 9 failure, 2 unknown |

CV by evidence modality: visual 88.89%; text 100.00%.
BDA is balanced accuracy across normal and failing conditions, not the pooled success fraction.

### Every recorded repair transition

| Original edit events | Criterion | Before → after |
|---|---|---|
| 216 | repair_asset_loading | fail → pass |
| 225, 227 | repair_font_loading | unknown → pass |
| 238, 240, 243, 245 | homepage_metrics | fail → pass |
| 238, 240, 243, 245 | repair_home_gartner | fail → fail |
| 238, 240, 243, 245 | repair_home_winston_content | fail → pass |
| 260, 262 | repair_home_gartner | fail → pass |
| 260, 262 | repair_winston_gartner | fail → pass |
| 269 | repair_pricing_brandmark | fail → pass |
| 308 | repair_winston_testimonial | fail → pass |

### Remaining uncertainty

The fixed homepage hero criterion remains unresolved because of the previously recorded requirement conflict. It contributes one unresolved VC criterion and six unknown preservation comparisons. Two additional CP comparisons are unknown because the V5 global-navigation state was judged insufficiently established, despite completed actions.
The baseline font state is unknown because its external stylesheet requests exceeded the evaluator network deadline. It was executed and inspected; no implementation failure was fabricated from that deadline.
The four CP regression labels concern careers search (V0→V1 and V2→V3) and customer testimonials (V0→V1 and V5→V6). Careers search code was not changed in V2→V3, so that label is particularly suspect and requires adjudication against the recorded interaction evidence. Do not interpret all four model labels as confirmed implementation regressions.

These are automatic Gemini labels. The experiment is complete in execution scope; model label accuracy is not independently calibrated. Inspect the raw observations and responses when interpreting individual regression claims.

### Calls and validation

| Stage | Unique model responses used by final result |
|---|---:|
| gui_step | 112 |
| plan_review | 1 |
| repair_alignment | 1 |
| text_bda | 16 |
| text_cv | 16 |
| text_vc | 18 |
| visual_bda | 9 |
| visual_cp | 63 |
| visual_cv | 9 |
| visual_rs | 13 |
| visual_vc | 9 |

Total responses used: 267. The existing catalogue review is reused and is not a new API request.
The run tree contains 526 completed non-cache call records including discarded debugging attempts. Cancelled in-flight HTTP calls are not necessarily represented; this is not a billing count.
Validation: 131 tests passed; two archived-path tests were skipped. Final joining revalidated original model responses, criterion IDs, temporal evidence, artifact hashes, source executions and all repair references.

### Recompute the final metrics without model calls

```bash
PYTHONPATH=src python scripts/vision2web/score_vsv.py \
  --check-results runs/vsv_eval/complete_smartrecruiters_20261005/checks_complete/scores.json \
  --repair-results runs/vsv_eval/complete_smartrecruiters_20261005/states_grouped/repair_results.json \
  --output-dir runs/vsv_eval/complete_smartrecruiters_20261005/final
```
