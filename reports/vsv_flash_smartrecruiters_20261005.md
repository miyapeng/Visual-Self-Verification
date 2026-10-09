# Gemini 3 Flash evaluation: SmartRecruiters

**Audit update:** target-scope and denominator defects were confirmed after this
pilot. Its CV/BDA/VCS results are not validated capability measurements. See the
[CV audit](vsv_flash_cv_audit_20261005.md). Original labels and scores are preserved.

## Scope and configuration

The default protocol profile is now `gemini3flash`, model
`gemini-3-flash-preview`, for both text and visual judgments. Metric calls remain
independent; there is no automatic expensive-model fallback.

This experiment preserves the existing 25 check rounds, task references and
27-goal model-reviewed catalogue. The extraction has eight image-bearing rounds,
one visual attempt without image receipt, and sixteen nonvisual rounds. The
catalogue was prepared previously; it was not regenerated with Flash. Reference
associations were also reused. New score judgments use Flash throughout.

Artifacts: `runs/vsv_eval/flash_smartrecruiters_20261005/`.
`reproduce.sh` records the CLI invocations; credentials come from the environment.
`config.json` and `state_config.json` freeze the profiles used for this experiment.

## Validation and limits

The first check run stopped when VC assigned partial coverage to an unresolved
criterion. The invalid response is preserved in `checks/judge_cache/text_vc/`;
it is not included in scores. Prompts now explicitly require unknown VC/BDA
and null CV labels for IDs in `unresolved_check_ids`, matching existing validators.
A second run stopped on an absent diagnosis with a nonempty diagnosis reference.
The BDA prompt now explicitly states the existing bidirectional presence constraint.
A third run rejected full coverage citing only agent narration. The VC prompt now
explicitly requires an observation event for full coverage. The continuation reuses
validated completed rounds from that run and judges the remaining rounds with the
clarified prompt. Original prompts remain attached to every result. This is a
development pilot with prompt revisions, not a frozen-protocol model comparison.
Failed runs remain archived. These were manual experiment revisions, not an
automatic correction or model voting loop.

RS/CP reuse the validated V3/V4 browser execution and audited code identities,
with four new Flash state judgments. The archived GUI profile is preserved only
so the executor can validate cache identity; no new GUI calls are needed.
Only the Winston transition at Edit 308 has this acceptance evidence. Other
repair transitions remain unassessed. Its old unmatched-target alias belongs
to earlier check labels and is omitted from the new experiment specification;
no new semantic alias is inferred from chronology. RS can be reported for the
fixed repair criterion, but missing target correspondence remains unknown in VCS.

Flash labels have not been independently calibrated. Passing response validation
checks references and protocol constraints, not semantic judge accuracy.
The state verdict for the Winston repair differs from the previous GPT-5.4
pilot on the same acceptance evidence; the new label must not be represented
as established ground truth. This run does not alter scoring formulas.

Relevant tests: 58 passed (`test_vsv_check_stages.py`, `test_vsv_protocol.py`).

## Results

All 25 extracted rounds were processed: 23 evaluated, two excluded as unrelated
by VC, and none pending. There are 184 target records: 50 visual and 134 text.
Modality is assigned to target evidence, so a visual round can contain text checks.
These are development-pilot scores, with unknown cases retained separately.

| Metric | Result | Scope |
| --- | --- | --- |
| VC | 70.37–74.07% | 19 full, 6 partial, 1 none, 1 unresolved; 27 fixed goals |
| CV | 39.04% | 57/146 valid; 38 unknown |
| CV visual | 65.96% | 31/47 valid; 3 unknown |
| CV text | 26.26% | 26/99 valid; 35 unknown |
| BDA | 95.54% | Balanced normal/error accuracy; 60 known, 124 unknown |
| BDA visual | 96.55% | 29 normal and 3 failing targets; 18 unknown |
| BDA text | 94.44% | 27 normal and 1 failing target; 106 unknown |
| RS | 100% | 1/1 fixed repair target; five other repair episodes unassessed |
| CP | 100% conditional | 14/14 known baseline passes preserved; 141 unknown across all transitions |
| VCS | 0% conditional | 0/14 decidable rounds; 9 unknown, 2 excluded |

VC has no exact score while a frozen criterion remains unresolved. Its bounds
are not a confidence interval. BDA is a class-balanced mean, not 55/60; the
four known failing targets are too few to support a broad error-detection claim.
RS/CP do not establish complete repair/preservation quality. CP's 141 unknowns
include six in the evaluated transition and 135 across five unassessed transitions.
No aggregate score is formed.

## Calls and artifacts

This session made **106 HTTP inference requests**, including three interrupted
runs, and reused 14 exact response-cache entries. There were no HTTP/client
errors; three downstream label-validation errors stopped those runs. These
validation failures are not included in the client's `error_calls` count.
The endpoint reported 986,218 prompt tokens and 441,766 completion tokens
(including its reported reasoning usage). Relay billing was not available.

| Stage | New API requests, including interrupted runs |
| --- | ---: |
| Text VC / CV / BDA | 26 / 21 / 27 |
| Visual VC / CV / BDA | 10 / 9 / 9 |
| Visual RS / CP | 2 / 2 |
| Extraction, catalogue, reference mapping, GUI, aggregation | 0 |

Final scores: `runs/vsv_eval/flash_smartrecruiters_20261005/combined/scores.json`.
Readable report: `combined/report.md` in that directory's parent.
Modality breakdown and token totals: `summary.json`.
All actual calls and per-request locations: `model_calls.json`.
Final round labels: `checks_v4/episode_results/`; each request links its original
input and response, including reused results from earlier runs.
All state judgments: `states/state_results/`.

The final result references 81 original metric responses; this is
distinct from total experiment spending, which includes superseded responses.
