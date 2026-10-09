# Opus leaderboard pilot and current SmartRecruiters scores — 2026-10-05

## Current SmartRecruiters result

Source: `runs/vsv_eval/joined_smartrecruiters_20261005/audited/scores.json`.

| Metric | Result | Support |
|---|---:|---|
| VC | 74.07% | 20/27 full; 4 partial; 3 not covered |
| CV | 100% | 81/81 scoped targets |
| BDA | 82.73% | Balanced accuracy: 52/66 normal and 13/15 error; 16 missing diagnoses count against accuracy |
| BDA visual / text | 91.67% / 76.39% | 36 visual and 45 text targets |
| RS | 88.89% | 8/9 post-repair acceptance checks |
| CP | 97.18% | 138/142 preservation comparisons |
| VCS | 14 pass, 7 fail, 1 unresolved | Known-only 14/21 = 66.67%; all-round bounds 14/22–15/22 |

The current check labels were joined with existing validated state executions. No deployments,
state observations, RS/CP verdicts or scoring formulas were rerun or changed. Target aliases
were reassociated to updated check identities. Model response validation rejected an initial
invalid mapping. A later evidence audit rejected one semantically unsupported alias: inspecting
font-family metadata is not the same condition as eliminating remote font-loading errors.
Original model responses and the audit are preserved. This unresolved VCS relation must not be
silently dropped or reported as repaired. These are development-case judgments, not independent
calibration of the evaluator. No composite overall score is defined.

## New data

Source: `/mnt/shared-storage-user/miyapeng/datasets/vision2web-leaderboard/ClaudeCode+Claude-Opus-4.8`.
There are 193 cases: 100 webpage, 66 frontend, and 27 website tasks. Their recorded `success`
status denotes completed generation, not official task correctness.

Only model-facing image blocks were counted, excluding duplicate metadata: 3,619 image inputs,
with usable bytes for five small asset/logo diagnostic images. No complete runtime screenshot
was retained among those five. The other payloads are omitted placeholders. The downloaded
file hashes match the download manifest; downloading those same JSON files again does not
restore their omitted pixels.

The download scope excludes generated websites and assets. The pilot has no audited intermediate
code versions or runnable artifact manifests. Task prompts match local benchmark prompts exactly,
and local prototypes are available. Those reference images cannot replace the agent's observed
application images. The leaderboard SmartRecruiters trace is a different execution from the
existing SmartRecruiters fixture.

## Actual pilot results

Judge: `gemini-3-flash-preview`, using the existing JudgeClient and separate VC/CV/BDA calls.
Catalogues remain drafts and runs use `--allow-draft`. This is a transfer/debugging pilot.

| Case | Extraction | Text-only scoring |
|---|---|---|
| Academy GovLoop | 53 candidates; 14 rounds: 5 with images, 1 visual attempt without image, 8 nonvisual | 21 targets; CV 21/21; BDA 100% (20/20 normal, 1/1 error) |
| Orchira | 53 candidates; 11 rounds: 8 with images, 3 nonvisual | 5 targets; CV 5/5; 5/5 normal diagnoses correct; BDA N/A because there are no error-state examples |
| Holyoke | Extraction validation failed | No valid score |

These are model labels, not independently verified correctness. A high text CV does not establish
website quality: startup, resource availability and file-delivery checks can be valid within a
narrow scope without covering visual or interactive requirements. Academy also contains real
DOM/search/pagination observations. Do not interpret text-subset VC as total trajectory coverage.
The full-round partial outputs retain every unassessed visual round; RS/CP are unavailable because
no executable version pairs were supplied. Known-only CV/VCS values in partial outputs must be
read together with the pending rounds, not reported as full-trajectory results.

## Failures and changes

1. Imported native Claude conversations through the existing ID-paired parser. Original result
   JSON files remain unchanged; normalized events record original conversation line/block IDs.
2. Base64 placeholders are no longer decoded permissively or silently discarded. Image receipt
   is retained separately from local pixel availability.
3. Extraction inputs explicitly enumerate valid model-response IDs and clarify that a response
   judging one page and announcing the next belongs to one core; the next round references it.
4. Three annotation iterations were attempted per case. Initial errors included action IDs used
   as response IDs, missing judgment-subset membership, duplicate core ownership and one truncated
   response. The final Holyoke attempt still duplicated policy ownership and was rejected.
5. Full metric evaluation on the two extracted cases failed because the model asserted visual
   full coverage without attached observation pixels. The validator rejected these labels.
   Valid text results were reused only after checking original prompts, responses, IDs and hashes.
   The remaining nonvisual rounds were then evaluated. No failed response was silently repaired.
6. No scoring formulas changed. No batch of 193 runs was started and no generated website was executed.

Tests: `python -m pytest tests/test_vsv*.py -q` — **231 passed, 8 skipped**.
`git diff --check` passed.

## Model calls

65 requests for the new-data pilot: 9 extraction annotation, 3 draft catalogues, 4 reference
associations, 14 text VC, 14 text CV, 14 text BDA, 5 visual VC and 2 visual CV.
Three additional requests reassociated old SmartRecruiters repair targets: **68 requests total**.
These totals include failed debugging attempts. A schema/semantic rejection can follow a successful
HTTP response, so request-level `error_calls` alone is not the number of invalid annotations.
See both run directories' `model_calls.json` files for the original per-call records.

## Files and reproduction

- Pilot summary: `runs/vsv_eval/leaderboard_opus48_pilot_20261005/summary.json`
- Pilot commands and artifact meanings: `runs/vsv_eval/leaderboard_opus48_pilot_20261005/README.md`
- Extraction JSON: `<pilot>/<case>/extracted_final/verification_rounds.json`
- Model annotation: `<pilot>/<case>/extracted_final/model_annotations.json`
- Text subset scores: `<pilot>/<case>/text_checks/scores.json`
- Full-round partial records: `<pilot>/<case>/partial_all_rounds/scores.json`
- Existing-case final join: `runs/vsv_eval/joined_smartrecruiters_20261005/audited/scores.json`

To enable full evaluation, obtain the original tool-return screenshot bytes with their event
links, and executable version checkpoints with the corresponding source/assets, dependencies and
startup commands. Final website source may help future outcome tests, but does not by itself
reconstruct historical screenshot evidence or repair-before states. Resolve extraction reference
stability before scaling beyond this pilot.
