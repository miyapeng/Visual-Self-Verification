# Requirement-level evaluation revision

Protocol: `requirement-level-20261006`. All six metrics remain. This is an
implementation and archived-data audit, not a new Judge accuracy experiment.

## Changes

- Task planning uses one independently falsifiable, user-facing requirement per
  criterion. Shared details do not become separate scores merely because they
  have separate DOM/CSS elements. Independently failing content and behavior
  remain distinct. No page-specific rule, fixed goal count or score-based pruning
  was added. Existing frozen catalogues remain unchanged.
- Observation judgment precedes diagnosis comparison. The observation packet
  withholds agent narrative/reasoning; BDA returns only target ID, agent state,
  issue match and diagnosis IDs. Python joins these with the fixed observed state.
  This reduces direct anchoring; trajectory-derived target selection still can
  introduce bias, and improved semantic accuracy has not been measured.
- Recheck alignment matches existing target IDs across differently worded checks
  within recorded repair links. The model only identifies corresponding conditions.
  Python checks version timing and outcome. Independent acceptance after an edit
  does not establish that the agent itself rechecked the condition.
- BDA uses mean class accuracy over the classes present, retaining missing-diagnosis
  penalties. A one-class task has a defined class accuracy and visible sample count.
  Cross-task aggregation is the equal-weight mean of task scores. Definition IDs
  prevent silent mixing with the earlier two-class-only statistic.
- Incomplete evidence no longer produces a full CV/RS/CP/VCS score by discarding
  unknown labels. Known-subset values and uncertainty bounds remain diagnostic.
  A missing repair group with an unknown denominator has no invented bounds.
  Missing evaluator evidence is not labeled as an agent failure. Empty opportunity
  sets remain not applicable, not fabricated zeroes or perfect scores.
- Failure instances retain original IDs in `findings`. No extra model call writes
  findings or numerical scores. The experiment freezes its protocol version.

## Validation

The VSV test suite passed: **285 passed, 8 skipped**. New cases cover withholding
agent narration, immutable observation verdicts, changed target IDs, unrelated
rechecks, stale captures, absent diagnoses, saved-response validation, and incomplete
aggregate scores. These are implementation tests, not a substitute for Judge validation.

The SmartRecruiters archive was revalidated against original inputs, model responses,
images and acceptance records. It contains 25 extracted rounds, 22 scored rounds and
81 target instances. The audit made **0 API requests**.

| Metric | Archived-label recomputation |
| --- | ---: |
| VC | 74.07% |
| CV | 100.00% |
| BDA | 82.73% |
| RS | 88.89% |
| CP | 97.18% |
| VCS | 14 confirmed chains, 7 failed, 1 lacking new target alignment |

These are historical labels under the existing 27-goal catalogue, not scores from
new requirement planning or observation-first judging. The unresolved chain is
`verification-0219-0224-936ac3709b`: old and later conditions have different IDs.
Exact-ID mismatch cannot establish that the agent omitted a recheck. The new
alignment call resolves such correspondences; it has not been executed on this
archive because no usable API balance is available in the current session.
Do not present the VCS diagnostic bound (63.64–68.18%) as a fresh point estimate.

Archived labels identify 16 missing diagnoses, 1 failed repair acceptance, and 4
baseline-pass/after-fail preservation instances. They contain no labeled false
alarms, missed defects or incorrect defect localization. These describe this
archive and Judge only; they do not establish absence of such failures in a model.

Output: `runs/vsv_eval/requirement_level_audit_20261006/validated_archive/scores.json`.
The earlier `smartrecruiters/` directory is marked as a superseded intermediate
exact-ID experiment. Source trajectories and archived scores were not modified.

## Calls and next executable run

Each nonempty observation prefix uses VC, CV, OBSERVATION and BDA, batching all
its targets. Empty prefixes use VC only. Recheck alignment is batched across the
trajectory; it never runs once per candidate pair. Existing extraction, reference
mapping, planning and RS/CP acceptance calls remain separate and cached.

The archived prefix layout had 27 VC and 24 each CV/BDA calls. At the same layout,
the new check stages would use 99 calls plus alignment batches, excluding planning,
extraction, reference mapping and acceptance. Actual counts depend on the newly
returned targets and are reported by the existing `model_calls.json` mechanism.

```bash
# Reproduce the archived-label audit without API calls.
python scripts/vision2web/score_vsv.py \
  --check-results runs/vsv_eval/bda_scoped_smartrecruiters_20261005/complete/check_results.json \
  --repair-results runs/vsv_eval/joined_smartrecruiters_20261005/evidence_linked/repair_results.json \
  --output-dir runs/vsv_eval/requirement_level_reproduction

# Run prepared cases with fresh judges and the existing system entry point.
python scripts/vision2web/evaluate_vsv.py \
  --manifest path/to/prepared-experiment.json --output-dir runs/vsv_eval/fresh-experiment
```

A fresh protocol experiment must omit `check_results` so it actually executes the
new judgments. If catalogue granularity changes, regenerate its acceptance plan
and target bindings; do not reuse acceptance labels under a changed denominator.
Archived repair aliases must also match the new target IDs. The existing validator
rejects stale bindings instead of silently relabeling them. Prepared runtime
versions and observation pixels are still required; an API alone cannot supply them.
