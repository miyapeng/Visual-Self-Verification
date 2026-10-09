# Check scope and shared-plan repair

## Implementation

The scoring formulas, round boundaries and model-call stages are unchanged.
No new dependencies or target fields were introduced.

- `evaluation.build_packet` retains the direct preceding response as context,
  including a plan spanning several rounds. Existing context observations bring
  their ID-paired actions, and actions bring their actual paired results. Cutoffs
  still exclude future evidence. Shared screenshots retain capture-time versions.
- VC retains the actual attempted condition in `target` instead of replacing it
  with the complete matched catalogue criterion. Narrower conditions can receive
  partial coverage while their own method is valid. Unmatched meaningful checks
  retain null catalogue IDs.
- After structural/ID validation, targets without a cited current core action or
  observation are omitted from this round's target set. This removes absent goals
  and context-only historical checks. Filtering does not depend on coverage or
  success: failed calls and missing observations remain eligible through their
  action reference. Raw model responses remain unchanged and inspectable.
- CV/BDA receive actual target identities, original task/references and the
  time-bounded raw events. The coverage catalogue is not duplicated into those
  calls: its broader conditions and suggested acceptance procedures must not
  redefine an individual attempt. Relevant earlier steps can support a current
  check; a current check need not complete a whole plan. Static appearance still
  requires pixels; suitable programmatic feedback can establish behavior.
- Merged target evidence remains the VC evidence defining the target and modality.
  CV/BDA retain their distinct citations in their linked original responses.

These changes do not make an inadequate check valid by definition. For example,
a positive result count does not establish keyword matching or exhaustive filtering.
ID validation and context filtering cannot establish semantic judge accuracy.

## Verification

109 tests passed across `test_vsv_check_stages.py`, `test_vsv_protocol.py`,
`test_vsv_rounds.py` and `test_vsv_windows.py`. Five new regression tests cover:

1. A valid narrow check can have partial catalogue coverage.
2. Absent/context-only targets are omitted; failed attempts remain eligible.
3. A split plan retains its preceding response and paired setup, without unrelated
   calls/results, future feedback or imported historical diagnosis IDs.
4. BDA citations do not overwrite VC evidence; original stage references persist.
5. An unattempted goal remains in VC's fixed denominator but not CV's denominator.

The real smoke uses four original SmartRecruiters rounds: #192–194 (HTTP),
#231–237 (homepage), #299–301 (careers filter/search), and #310–312 (FAQ/testimonial).
Only the episode list is subsetted; raw events, source trajectory and IDs remain
unchanged. The last two include shared-plan/history context. The model is
`gemini-3-flash-preview`, using the existing client, schemas, cache and call ledger.

Artifacts are under `runs/vsv_eval/scope_fix_smartrecruiters_20261005/`.
`rounds.json` binds the source, `final/` contains the final experiment,
`config.json` freezes the profile, and `reproduce.sh` records the CLI command.
Earlier `checks/` and `verified/` responses are retained as development records.
The intermediate run exposed screenshot-biased catalogue instructions and a
context-only target response; neither was resolved by hand-editing labels.

This is a bounded regression experiment, not a new whole-trajectory score.
The previously reported whole-trajectory percentages remain superseded by the
scope audit until all rounds are reevaluated under a frozen configuration.

## Observed outcomes

| Round | Retained actual targets | Check |
| --- | ---: | --- |
| HTTP #192–194 | 8 | Route and asset availability; no substituted UI-navigation targets |
| Homepage #231–237 | 5 | Homepage observations; unrelated-page goals no longer enter CV |
| Careers #299–301 | 3 | Department filter, keyword search and listing presence; original plan retained |
| FAQ/testimonial #310–312 | 2 | Current interactions retained; nine context-only targets filtered before CV/BDA |

All four saved rounds passed source, response and reference revalidation.
The final run used nine new API requests and three exact VC cache hits. Including
intermediate development runs, this change used 33 requests and four cache hits.
No model correction loop or alternate judge was introduced.

Remaining semantic uncertainty: Flash rates the keyword-search check valid even
though its feedback exposes only a result count. This needs judge calibration;
it was not hand-corrected or accepted as a gold label. The structural regression
results do not validate all visual diagnoses or establish complete target recall.

A remaining target-scope error also survives structural validation: the homepage
logo target includes carousel behavior although this round only observes a static
image. It cites real core evidence, so an ID filter cannot identify that semantic
overreach. This label remains disputed; no whole-trajectory replacement score is
published from the smoke. Task-specific keyword rules were not added.
