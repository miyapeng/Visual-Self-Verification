# Matched-model Vision2Web evaluation pilot — 2026-10-05

## Scope and accepted outputs

Two L2 tasks, Finalsite and Black Belt AC & Electric, were evaluated across Claude Opus 4.8, Kimi K3, GLM 5.3 Flash, and DeepSeek V4 Flash Vision Exp under Claude Code. Selection favored short maximum conversation lengths across the four models before examining scores. This is a small development pilot, not a representative model ranking.

The metric judge is Gemini 3 Flash (`gemini-3-flash-preview`, `gemini3flash`). Each task has one shared draft catalogue. All eight downloaded task prompts match the corresponding local extracted benchmark prompt byte-for-byte. Prompt, workflow, reference and source hashes are saved in the [inventory](../runs/vsv_eval/leaderboard_matched_models_20261005/inventory.json).

- [Comparison table](../runs/vsv_eval/leaderboard_matched_models_20261005/comparison.md)
- [CSV](../runs/vsv_eval/leaderboard_matched_models_20261005/comparison.csv)
- [Complete metrics and denominators](../runs/vsv_eval/leaderboard_matched_models_20261005/comparison.json)
- [Commands and canonical input paths](../runs/vsv_eval/leaderboard_matched_models_20261005/README.md)
- [Extraction provenance](../runs/vsv_eval/leaderboard_matched_models_20261005/review/README.md)

Seven traces produced validated final scores. Finalsite/DeepSeek completed 24/25 rounds but did not produce a valid whole-trace result. In `verification-0248-0255-b90e681b64`, the VC response repeatedly called a Who We Serve target visual while citing only text events 248, 249 and 254; the recorded image event 252 belongs to another inspected page. Other attempts duplicated a catalogue ID. Invalid responses were retained and rejected. Prompt clarifications did not resolve the remaining error, so that row is marked unavailable rather than reporting a partial score as complete. Its saved responses and completed labels are under `finalsite/deepseek_v4_flash_vision/evaluation_modality/`.

## Evidence availability

The local tasks supply original requirements, workflows, prototypes and resource assets. Every downloaded trace has recorded image markers but omits runtime image pixels. None of these imports supplies audited executable historical version pairs. Consequently, visual correctness and replay-based repair success/correctness preservation cannot receive complete scores. Prototype assets cannot replace the images seen by the original coding agent. A newly reconstructed screenshot would be new evidence, not that historical observation.

The main table reports text-target check validity (CV) and balanced diagnosis accuracy (BDA), alongside sample sizes. A text target may occur within a round containing images. CV/BDA ranges include unresolved targets rather than omitting them; these ranges are not confidence intervals; an absent normal/error class yields N/A. VC bounds and VCS pass/fail/unresolved counts are included separately. Known-only aggregate CV/VCS percentages must not be presented as complete trajectory scores.

## Extraction and implementation findings

Only Finalsite/Opus and Blackbelt/Kimi passed automatic extraction. The other six required explicit Codex review of candidate IDs, original events and relation constraints after Gemini generated invalid references, duplicate ownership, cross-edit merges or truncated annotations. Reviewed annotations are stored separately, with an explicit saved-annotation provider and zero remote API requests. They are not relabeled as Gemini output. This makes the experiment Codex-assisted and not independently calibrated.

The importer had treated user-provided skill text and image dimension metadata as agent narration. It now preserves their roles and keeps the following genuine assistant response attached to its tool window. Missing-image references remain intact. The annotation and scoring transport now constrain existing IDs using string enums accepted by the Gemini endpoint, then restore the exact original integer IDs. The VC prompt also states the existing constraint that each matched catalogue ID may appear only once per response; invalid duplicates are rejected, not silently dropped. Existing source, timing, modality and availability validators remain active. Missing pixels constrain visual labels without disabling text observations. The VC prompt clarifies that modality follows each target's cited image receipt, including failed captures inside a round with other image inputs. No scoring formula changed.

Long plans and shared sources remain context. The existing strict modification boundary still has a limitation: Finalsite/GLM inspection 208 is followed by an edit at 210 and comments at 212–213; those later comments are excluded from the earlier core. This can affect missing-diagnosis counts and needs calibration before formal comparison.

## Inspected judgments

Finalsite/Opus events 154–157 report six routes with zero console errors and a shared browser `document.title`. The judge originally treated visible page headings in the task description as required tab titles, incorrectly penalizing five targets. A general prompt clarification separates those properties. Only the affected BDA call was rerun with unchanged targets and evidence; the corrected text BDA is 95.83%, replacing 59.02%. The original output is preserved. The accepted score is `finalsite/opus48/title_scope_review/scores.json`.

Blackbelt/Opus has genuine inconclusive checks: events 211–212 and 236–237 terminate with exit code 144 before producing the intended startup/status results. Events 191–193 attempt form submission but return unrelated textbox lines, followed by the agent explicitly requesting a screenshot to establish the result. These observations do not establish successful execution or a product defect.

Blackbelt/GLM events 263–268 attempt an interaction suite that fails at JavaScript parsing. Its later run at 269–271 returns 60/75 test assertions passed, while the agent identifies case-sensitive text and selector problems in several failed assertions. Raw FAIL lines alone are therefore insufficient to label the website defective. The record preserves both the failed attempt and subsequent test revision, without treating a test-helper edit as an application repair.

Blackbelt/Kimi has only two scored text targets, both normal and correctly diagnosed. Its text CV is 100%, but balanced BDA is N/A because no error-class sample exists. This is not evidence that its overall self-verification is superior.

## Validation and accounting

The relevant suite passed 235 tests with 8 skips (`python -m pytest tests/test_vsv*.py -q`); `git diff --check` passed. After the final VC uniqueness clarification, the focused check-stage suite passed all 30 tests. Tests cover role-preserving import, window context after image metadata, constrained original IDs and rejected invented IDs, plus missing-pixel constraints that preserve text assessment.

The consolidated model-call report contains **791 recorded API requests**, including debugging failures and resumed calls. Stage counts: catalogue 2, reference annotation 13, verification annotation 31, text VC/CV/BDA 169/147/148, and visual VC/CV/BDA 96/93/92. These are request-stage names: a visual-stage packet can also contain text targets. The count includes failed extraction experiments; it is not the minimum cost of eight clean runs. The 10 logged API error calls do not include every semantic validation failure after a successful response. Three obsolete workers were interrupted after discovering the role-import bug; in-flight requests without completion logs may be absent. The count is an observable request total, not a guaranteed billing total. Accepted completed metric judgments were reused with source/response validation rather than rerun indiscriminately.

Before a larger comparison, obtain original runtime image files with tool-result mappings and historical executable code versions, then calibrate the shared catalogues, extraction boundaries and judge labels on an independent sample. The present table is useful for debugging the evaluation and inspecting check behavior; it does not establish a model capability ranking.
