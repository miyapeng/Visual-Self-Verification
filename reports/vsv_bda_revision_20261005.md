# Explicit diagnosis accuracy and coverage revision

> Superseded: the expressed-only BDA definition and its 100% score were rejected. See `reports/vsv_bda_penalty_restoration_20261005.md` for the restored missing-diagnosis penalty and full-target uncertainty bounds. This file preserves the earlier experiment history.

## Scope and implementation

This revision follows the request to count actual incorrect judgments as BDA failures.
It changes the BDA definition rather than merely correcting arithmetic. Original
experiment outputs and trajectory events are unchanged. No task-specific rules,
new client, voting stage, or dependency was introduced.

- `metrics.py`: BDA is balanced accuracy over definite expressed conclusions.
  Missing conclusions and explicit abstentions have separate counts; unavailable
  ground truth remains unknown. VCS still requires a definite conclusion on
  decidable observations. Cross-task aggregation rejects mixed BDA definitions.
- `evaluation.py`: include the direct model response after the final observation,
  stopping at the next operation or session boundary. Preserve the original
  episode core and reference the shared response. Saved-input validation checks
  the same eligibility rule.
- `check_stages.py`: distinguish checking coverage from artifact correctness;
  treat illustrative values as examples and forbid static evidence from proving
  interactive behavior. An unresolved acceptance criterion remains unknown for
  BDA, enforced by validation, while sufficient inspection can receive full VC.
- `report.py`: report absent/uncertain diagnosis counts alongside BDA.
- `docs/self_verification_evaluation.md`: document the revised conditional metric,
  evidence boundaries, and source-conflict handling.

## SmartRecruiters trial

Model: `gemini-3-flash-preview`. All 25 extracted rounds were processed with the
existing independent VC/CV/BDA stages. A first pass still resolved a conflicting
hero requirement as a failure. The final pass enforces the frozen ambiguity rule
and rejudges BDA for every round containing an unresolved check ID. Identical
VC/CV requests use their original cached responses. This is a prompt/validation
revision followed by a rerun, not voting or selection of the better score.

Final automatic BDA: **100%**, with 56/56 normal diagnoses and 8/8 error diagnoses
judged correct. There are 20 targets without an associated definite diagnosis,
zero explicit abstentions, and four expressed diagnoses with unknown correctness.
The total is 88 targets. The result does not establish perfect error detection,
correctness of all broad completion claims, or independent judge calibration.
It is not directly comparable to the old 78.67%, which counted missing conclusions
as failures and used a different generated target set.

Concrete regression checks:

- #230's zero-error console return is associated with #231's explicit confirmation.
- Winston's 56% metric is not rejected for differing from an illustrative 33%.
- The testimonial white-box defect remains present as a real diagnosed failure.
- Homepage hero inspection is full coverage in the visual rounds; its acceptance
  verdict remains unknown because the task and prototype disagree on button text.
- Unsupported interaction evidence can remain unknown instead of forcing a
  false positive diagnosis verdict.

## Coverage interpretation and remaining limits

The hero screenshots show the headline and both buttons, and the agent performs
an overall visual comparison. This supports visual content coverage. It does not
establish button-click behavior or resolve the expected button wording.

The automatic aggregate VC is 21/27 = 77.78%, with five partial goals, one absent
goal, and zero unknown coverage goals. This is not a calibrated final VC result:
the new model labels also change sticky navigation from partial to full and careers
search/filter from full to partial relative to the previous run. The initial
static homepage screenshot does not by itself justify full sticky-navigation
coverage. Some initial-homepage defects are also matched to nearby catalogue
objects rather than preserved as unmatched targets. These are remaining VC target
scope/matching issues; do not attribute the aggregate change solely to resolving
the hero ambiguity or claim that BDA changes fixed all target extraction errors.

The final response at #354 is not automatically attached across intervening
operations. Missing associations remain visible in the separate absent count;
they no longer become false BDA error labels. Explicit delayed-summary linking
would require semantic evidence attribution, not unrestricted future context.

This run evaluates checks only. Existing repair executions were not rerun or
joined to changed target identities; RS/CP results from the previous experiment
remain separate. No historical score files were overwritten.

## Artifacts and reproduction

- Final labels, linked inputs/responses and metrics:
  `runs/vsv_eval/bda_revision_smartrecruiters_20261005/validated/scores.json`
- Readable report: the same directory's `report.md`.
- First-pass records: `runs/vsv_eval/bda_revision_smartrecruiters_20261005/checks/`.
- Frozen model configuration and reference associations: run-root `config.json`
  and `reference_mapping.json`.

With credentials exported, run from the repository root:

```bash
bash runs/vsv_eval/bda_revision_smartrecruiters_20261005/reproduce.sh
PYTHONPATH=src python runs/vsv_eval/bda_revision_smartrecruiters_20261005/recheck_disputed.py
```

Validation: 132 tests passed and four were skipped. Added tests cover shared
response boundaries, missing/uncertain conclusions versus genuine wrong judgments,
ambiguous correctness with known coverage, unresolved-verdict rejection, and
rejection of mixed BDA definitions. Finalization validates saved model responses,
original event IDs, image hashes, and temporal references. The real-trajectory
console, Winston metric, and hero coverage/ambiguity assertions also passed.

Completed new model calls in this run (including the superseded first BDA pass):

| Stage | Calls |
| --- | ---: |
| `text_bda` | 17 |
| `text_cv` | 15 |
| `text_vc` | 18 |
| `visual_bda` | 13 |
| `visual_cv` | 9 |
| `visual_vc` | 9 |

Total: 81 completed non-cache calls; 75 response references are used in the final result.
