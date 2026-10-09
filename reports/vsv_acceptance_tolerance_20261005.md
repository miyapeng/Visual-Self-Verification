# Post-repair acceptance and presentation tolerance

This revision implements the user's request to count a repaired target as
successful when its after-state passes, and to tolerate minor presentation
ambiguities that do not change the substantive requirement. It does not convert
unknown labels directly into passes.

## Policy and implementation

- `metrics.py`: RS is `post_repair_acceptance`. A real linked repair with an
  unknown baseline and a passing after-state receives credit. A failing after-state
  fails; an unknown after-state remains unknown. Known baseline passes remain
  excluded because no repair need has been established for that target. The original
  baseline label and evidence remain unchanged. This measures acceptance after a
  repair attempt, not proven causal improvement.
- `states.py`: CP tolerates minor wording, spacing and decoration variations that
  preserve required meaning, visible content and functionality. Missing content,
  broken actions and unreadable controls are not excused. Recorded completed
  navigation actions and destination URLs can establish route behavior without
  screenshots of every intermediate step.
- A source-conflicted visual criterion can reuse completed captures associated
  with the same reference page. The scoped judge input records existing capture
  IDs in `evidence_assignments` and the `allow_minor_source_variants` policy.
  The original execution rows, actions, check assignments and image bytes are
  unchanged. A judge must identify the relevant region and cite actual evidence;
  insufficient observations may still yield unknown. RS and BDA source-conflict
  handling are not automatically relaxed by this CP policy.

## Targeted reassessment

Source: `runs/vsv_eval/complete_smartrecruiters_20261005/states_grouped/repair_results.json`.
Model: `gemini-3-flash-preview`. Eight new calls, all `visual_cp`; no browser replay
or new version reconstruction was required. Other stored state judgments were
retained, not rerun or manually edited.

- Seven calls reassessed homepage hero content in V0–V6 using original captures.
  All seven returned pass under the stated tolerance. This resolves the same
  criterion across six before/after CP comparisons.
- One call reassessed V5 global navigation using its completed action sequence
  and recorded destinations. It returned pass, resolving both V4→V5 and V5→V6.
- Font-loading repair V1→V2 remains unknown→pass in its raw state evidence.
  Under post-repair acceptance it now counts as a successful attempt, without
  fabricating a known failing baseline.

## Updated independent repair metrics

| Metric | Score | Success | Failure | Unknown |
| --- | ---: | ---: | ---: | ---: |
| RS | 88.89% | 8 | 1 | 0 |
| CP | 97.18% | 138 | 4 | 0 |

The RS failure remains the first homepage Gartner repair attempt (V2→V3).
The four previous CP regression labels are unchanged and remain subject to the
previous audit's caveat about judge reliability. Resolving these eight unknown
comparisons does not independently validate all other model labels.

These are updated repair/preservation metrics. The saved repair relations still
use their original check target IDs; they have not yet been realigned to the
latest regenerated check targets for a new complete VCS result.

## Artifacts and validation

- Run root: `runs/vsv_eval/acceptance_tolerance_smartrecruiters_20261005/`.
- `summary.json`: updated RS/CP counts and scores.
- `repair_results.json`: validated paired state records and original execution
  references. Baseline uncertainty is retained even when RS is now decidable.
- `inputs/`, `judge_cache/`, `state_results/`: actual new model inputs, responses
  and merged per-version labels. Unchanged requests point to their original files.
- `reassess.py`: reproducible targeted reassessment using the existing client,
  cache, packet construction and response validation.

Validation: **135 tests passed, 4 skipped**. Tests verify unknown-baseline
post-repair acceptance, after-state failures/unknowns, CP evidence reuse without
inventing actions, separation from RS, and rejection of a pass without execution
evidence. `load_repair_results` revalidated all version identities, source hashes,
image bytes, stored model responses and paired labels. No original run artifacts
were overwritten.

```bash
# Run from the repository root with credentials exported.
PYTHONPATH=src python runs/vsv_eval/acceptance_tolerance_smartrecruiters_20261005/reassess.py
```
