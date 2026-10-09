# Restore the missing-diagnosis penalty

The expressed-only BDA definition and its 100% result are superseded. The user
confirmed that failure to provide a definite judgment on sufficient evidence
must receive zero credit. The latest result is
`runs/vsv_eval/bda_penalty_restored_smartrecruiters_20261005/scores.json`.

## Definition

- Known actual state: an incorrect, absent, or uncertain agent diagnosis receives
  zero credit. Wrong statements and missing conclusions remain distinguishable in
  the existing `agent_state` field.
- Unknown actual state or unresolved issue match: preserve unknown; do not invent
  an evaluator verdict. These targets participate in whole-set bounds.
- `score` is null while uncertainty remains. `known_score` is a diagnostic subset
  statistic, not the full BDA result. The report displays the full-set range.
- `absent_count` and `uncertain_count` describe all targets and overlap with outcome
  counts. They are not denominator exclusions.

Bounds retain the normal/error balanced-accuracy formula. For each feasible
allocation of unknown actual states to the two classes, compute the minimum and
maximum number of correct diagnoses allowed by the recorded agent labels and
unknown issue matches. Missing and uncertain conclusions never receive credit.
Take the minimum and maximum balanced accuracies over those allocations. Empty
actual classes do not define balanced accuracy. These are uncertainty bounds,
not confidence intervals or a claim that the missing observations were recovered.

## Results using unchanged model labels

| Outcome | Targets |
| --- | ---: |
| Correct diagnosis | 64 |
| Zero credit on decidable evidence | 13 |
| Evaluator uncertainty | 11 |
| Total | 88 |

All 13 current zero-credit labels are missing diagnoses on known normal states.
No known explicit wrong statement is labeled in this saved model output. Of the
20 absent diagnoses in the previous report, 13 now receive zero credit and seven
remain unknown because their actual states are unresolved. The other four
unknowns have explicit positive diagnoses. All 88 targets are retained.

- Normal class on known evidence: 56/69 = 81.16%.
- Error class on known evidence: 8/8 = 100%.
- Known-evidence BDA: 90.58%, explicitly not the full result.
- Full-target BDA uncertainty range: **61.63%–87.50%**.

The known-subset score can exceed the full upper bound because seven additional
unknown-state targets have no diagnosis; they cannot earn credit under any
resolution. The range accounts for those targets rather than dropping them.

Homepage hero coverage remains `full`; its unresolved acceptance verdict remains
unknown. The console response association, example-value handling, and rejection
of unsupported conflict resolution are preserved. Remaining target-scope and
missing-summary-association limitations from the preceding audit also remain;
these are automatic model labels, not independently calibrated ground truth.
The range measures uncertainty in those labels, not all possible judge errors.
Repair evaluation was not rerun or rejoined in this check-only correction.

## Changes and validation

- `metrics.py`: restore zero credit for missing/uncertain diagnoses, retain all
  unknown targets, compute bounds, and reject cross-definition aggregation.
- `report.py`: display BDA bounds and explain the known subset and overlapping
  absence counts. A separate missing-judgment exclusion no longer exists.
- `docs/self_verification_evaluation.md`: update the current definition.
- `tests/test_vsv_protocol.py`: test penalties, unknown retention, and bounds
  against an exhaustive enumeration of small possible completions.

Validation: **133 passed, 4 skipped**. Finalization revalidated saved inputs,
responses, temporal references and image hashes. Episode labels are byte-for-byte
identical as JSON values to the input result; no judge response was edited.
New API calls: **0**. Historical JSON results are retained as experiment records;
the previous report is marked superseded.

```bash
PYTHONPATH=src python scripts/vision2web/score_vsv.py \
  --check-results runs/vsv_eval/bda_revision_smartrecruiters_20261005/validated/scores.json \
  --output-dir runs/vsv_eval/bda_penalty_restored_smartrecruiters_20261005
```
