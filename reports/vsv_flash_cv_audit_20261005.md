# CV denominator and target-scope audit

The previous 39.04% overall CV and 26.26% text CV must not be interpreted as
reliable agent capability measurements. Arithmetic matches the saved labels;
target extraction and matching do not consistently match the recorded checks.
No API calls or label edits were made during this audit.

## Denominators

| Group | Valid | Invalid | Unknown | CV |
| --- | ---: | ---: | ---: | ---: |
| Visual | 31 | 16 | 3 | 65.96% |
| Text | 26 | 73 | 35 | 26.26% |
| Combined | 57 | 89 | 38 | 39.04% |

The combined result pools targets: (31 + 26) / (47 + 99). It is not the
unweighted mean of the modality percentages and does not count rounds.

## Confirmed defects

1. **Unperformed catalogue entries enter conditional metrics.** The first two
   homepage inspections (#231–237, #247–259) each include 22 unrelated goals
   with coverage=none, empty evidence, modality=text and invalid CV. They alone
   contribute 44 of the 73 text failures. Across all rounds, 70 rows have
   coverage=none: 54 invalid and 16 unknown. This accounts for 54/73 (73.97%)
   of the text failure count. Missing image references assign these invented
   check records to text, even though many originated in image-bearing rounds.
   Valid JSON and resolvable IDs do not establish a performed check.

2. **Matching expands or replaces the actual condition.** At #192–194, curl
   returns HTTP 200 for application routes. The model maps five routes to
   browser-navigation criteria, then CV rejects curl for not exercising navigation.
   HTTP availability is a legitimate narrower check and should retain that scope,
   without receiving full UI-navigation coverage. At #264–268, image pixel minima
   diagnose nearly white assets, but the model substitutes logo-carousel and
   customer-video-testimonial goals. Both substituted targets are invalidated;
   the actual asset diagnostic is missing.

3. **History replaces current checks.** At #310–312 the script toggles a FAQ item
   and advances a testimonial. Its actual return is
   `{"faqBefore":1,"faqAfter":2,"changed":true}`. VC omits both current targets
   and outputs all 27 catalogue goals, including earlier careers/customer/nav
   checks from context #299–306. The packet correctly separates core #310–312
   from context. This is a semantic attribution failure, not missing input.

4. **Some validity labels need review after correcting scope.** At #299–301,
   the Engineering filter returns three rows, all Engineering, but CV gives
   method_ok=true/evidence_ok=false. The same feedback is treated as valid in
   #310–312, where it is only context. The original test supports this specific
   filter case, not all departments. The keyword-search test only returns a
   count; it cannot establish that every returned row matches the query or that
   location filtering works. Likewise, #305–307 supplies click actions, destination
   URLs and a pricing-active flag, but two route goals are rejected for evidence.
   The fixed goals require starting at the homepage, whereas some clicks start
   on internal pages. Correct target mapping must precede judging those labels;
   neither treating all as valid nor all as invalid is justified.

5. **Merged evidence hides metric provenance.** `merge_stages` retains the VC
   modality but overwrites its evidence_ids with BDA evidence_ids, discarding CV
   references from the merged target. Some final text targets therefore cite
   historical images only through BDA. Original stage responses remain available;
   audits must read them instead of treating the merged evidence as CV evidence.

## Effect and minimal correction

As a sensitivity calculation only, removing every coverage=none row yields
text CV 26/45=57.78%, instead of 26/99=26.26%. This is **not a corrected score**:
none can include real failed attempts, and broadened/missing/duplicated targets
remain. A blanket coverage filter would introduce a different bias.

Keep the existing formulas. Extract actual attempted conditions from core
operations/results first, then link equivalent catalogue criteria for coverage.
Keep narrow legitimate checks with check_id=null when no equivalent goal exists.
The fixed catalogue supplies VC's denominator; omitted goals need not become
per-round target rows. Require grounding in the current check, preserve failed
attempts, and prevent context-only records from becoming fresh CV samples.
Retain raw stage evidence references rather than interpreting BDA citations as
CV citations. Re-run affected target and metric judgments with a frozen prompt
and add regressions for absent goals, narrow HTTP checks and context-only checks.
Do not hand-edit verdicts or raise scores to an expected value.

The downstream BDA and VCS sample sets are also affected. Previously saved raw
outputs remain useful debugging artifacts, not finalized benchmark labels.
See `runs/vsv_eval/flash_smartrecruiters_20261005/cv_audit.json` for counted rows.
