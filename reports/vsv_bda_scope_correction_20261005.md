# BDA scope and source-conflict correction — 2026-10-05

Current result: `runs/vsv_eval/bda_scoped_smartrecruiters_20261005/complete/scores.json`.

This is a Gemini 3 Flash development-trajectory evaluation. The trajectory was used to refine the judge prompts; it is not held-out judge validation. All labels come from saved model responses, and the program validates source events, temporal cutoffs, image hashes, target identities and merged stage outputs.

## Final policy

The original balanced diagnosis accuracy formula is retained: 100 × (normal-class accuracy + error-class accuracy) / 2. Missing diagnoses still receive zero credit on decidable evidence. No observed-unknown label was programmatically rewritten as an application failure or a pass. A temporary ordinary-accuracy experiment was not adopted; intermediate JSON files in `final`, `validated` and `reviewed` are not the current score definition.

- Judge the actual condition, not a broader catalogue goal or the semantic wording of a target ID.
- A static appearance check does not inherit the interaction requirement of its catalogue match. Direct URL loading is not a navigation-link test.
- Minor conflicts between supplied visual/text sources may accept either variant when meaning and functionality are preserved. Unresolved catalogue IDs no longer force every matched target to unknown.
- A successful inspection that reveals an artifact defect has `actual_state=fail`. Correctly reporting that defect is a matching fail diagnosis.
- Keep explicit observed defects and text checks inside visual rounds. A future inspection plan does not count as a completed inspection.
- Retain unknown for genuinely missing evidence or material unresolved conflicts on other inputs; this trajectory has no remaining unknown state or issue match in the latest model labels.

## Results

| Metric | Score | Correct | Zero credit | Unknown |
| --- | ---: | ---: | ---: | ---: |
| BDA | 82.73% | 65 | 16 | 0 |
| BDA-V | 91.67% | 31 | 5 | 0 |
| BDA-T | 76.39% | 34 | 11 | 0 |

The evaluated target set changed from 88 to 81 after scope correction and re-extraction. The 25 extracted round boundaries and raw trajectory did not change. This is not a relabeling of just the original 11 unknown rows. All 16 zero-credit labels in this result are absent diagnoses (five visual, eleven text); they are not 16 explicit false assertions.

Overall BDA = 100 × (52/66 + 13/15) / 2 = 82.73%. The ordinary correct-target ratio is 65/81; it is not the BDA formula.

VC is 20/27 = 74.07% (four partial, three untested goals), and CV is 81/81 = 100% under these model-generated targets. RS/CP were not rerun or joined. The check-only VCS must not be presented as a complete repair-chain result.

## Review of the original unknown categories

- HTTP image accessibility is an independent, observed check; it no longer inherits a hero-copy dispute.
- A screenshot-capture attempt with only a text return is not an inspection of hero pixels. The actual console/capture operations remain recorded.
- Hero appearance is judged under the minor-source-variant policy.
- Navigation and logo appearance are judged as static appearance; these labels do not prove sticky navigation or carousel interaction.
- Direct page opening and visible pricing content are separated from clicking a navigation link.
- The console summary is evaluated against the five pages with explicit zero-error returns. The result does not establish that all six pages were checked in the same implementation state.

Target omissions were also checked: the final first-homepage result includes the reported Gartner and Winston defects, and the concluding careers round includes the supported console check. Automatic target granularity still depends on the judge. Completed calls and reference validation do not establish independently calibrated semantic accuracy.

## Code and validation

- `src/multimodalcode/vsv_eval/check_stages.py`: scoped prompts, evidence-first VC field order, and removal of the unconditional unresolved-criterion override. Schema shape and judge client remain unchanged.
- `tests/test_vsv_check_stages.py`: allow supported pass/fail for source-conflicted targets while retaining observation, pixel, exact-fact and reference validation.
- `docs/self_verification_evaluation.md`: document the revised judgment policy. Metric formulas and extraction boundaries remain unchanged.
- Final test run: **228 passed, 8 skipped** (`python -m pytest tests/test_vsv*.py -q`).

## Model calls

Every actual request used `gemini-3-flash-preview` with the existing `gemini3flash` profile. Calls below include prompt-development runs, not just the selected final responses. Shared-cache logs are grouped by their recorded input directory, avoiding the misleading zero count from scanning only a symlinked output cache.

| Phase | API requests | Cache hits | Errors |
| --- | ---: | ---: | ---: |
| checks | 75 | 0 | 0 |
| final | 75 | 0 | 0 |
| validated | 24 | 57 | 0 |
| reviewed | 1 | 2 | 0 |
| complete | 9 | 0 | 0 |

Total actual API requests: **184**; API errors: **0**. Final arithmetic/validation required no API calls.

## Recompute the current result without API calls

```bash
PYTHONPATH=src python scripts/vision2web/score_vsv.py \
  --check-results runs/vsv_eval/bda_scoped_smartrecruiters_20261005/complete/scores.json \
  --output-dir runs/vsv_eval/bda_scoped_smartrecruiters_20261005/recomputed
```

Inputs and untouched raw responses are referenced from each episode request in `complete/scores.json`. `summary.json` lists every zero-credit target, and `model_calls.json` records each API call. The reproduction scripts preserve intermediate runs and the specific scope/omission reviews.
