# Independent metric judgments

The active evaluator now makes separate VC, CV and BDA calls for each recorded
check prefix. RS and CP have separate fixed-state calls. Python joins validated
labels by existing target and event IDs and computes the original six formulas.
VCS remains a deterministic conjunction, without a model call. No new client,
retry, voting, dependency, version reconstruction or image recording is added.
194 related tests pass; six archived cases are skipped because their inputs
are unavailable. Targeted checks were rerun after the final validation edits.

VC returns five fields per target, CV four and BDA seven. Matched target text is
filled from the fixed catalogue. CV/BDA receive shared target definitions and
original evidence without VC/CV verdicts. Each stage has its own schema, input,
original response, settings and cache. A malformed or incomplete response raises.
Independent stage disagreements remain inspectable instead of being forced
into agreement. Target selection is shared, so a missed VC target can still be
missed downstream; separate calls do not establish independent target recall.
New unmatched condition text can change its generated target ID. Repair aliases
must match the current check labels; the scoring join rejects stale aliases
instead of treating old bindings as relations for newly generated targets.

RS receives associated repair goals, while CP receives all necessary goals.
Both judge the same audited artifact versions from the original execution
evidence, without receiving the other metric's labels. Browser execution and
exact assertions are reused. `states` and `preservation_states` keep their labels
distinct. Historical joint outputs remain readable, but cannot be reused as
fresh independent check judgments.

## Real Winston smoke run

Source: SmartRecruiters / Vision2Web L2, Claude Opus 4.8 / Claude Code. The
recorded prefix is `verification-0283-0286-2f3e250cc0`; actual image #284 and
judgment #286. Original task, catalogue and recorded images were reused.
The catalogue is still draft; this is a single-round pilot, not a new full-task
score or validation of model accuracy.

Actual model: `gemini-3.1-pro-preview` for all three requests. All returned
`finish_reason=stop` and passed stage and stored-response validation.
Reported usage across the three calls is 48,138 input and 5,138 output tokens,
including reported reasoning tokens. This is recorded usage, not a monetary bill.

| Stage | Request SHA-256 | Raw output characters |
| --- | --- | ---: |
| visual_vc | aeac4d027564ab16fbb01f8160e021699755c7b23c5f6ded785dcd5a2b2f15e7 | 596 |
| visual_cv | 993d6adbb2c1d70dd150044da4ef0b52a13bfed71a00726a4052fe1f980a9fa1 | 468 |
| visual_bda | 3045206522123cef7962d4b15b35402312be3c57ade607cccb68f40870d136df | 895 |

The VC call identifies two fully covered fixed goals (`winston_capabilities`,
`winston_metrics`) and one unmatched white-box inspection. CV marks all three
checks valid. BDA labels two normal targets and one correctly identified error;
the round's CV and BDA are both 100. These match the historical round's broad
outcome; they do not demonstrate improved accuracy. The catalogue denominator
is 27, but an isolated round's coverage fraction is not a new whole-trajectory VC.

[Result and original request references](../runs/vsv_eval/independent_metrics_winston_20261004/result.json)
are saved with per-stage input files and raw responses under that directory.
No repair state judge or whole-trajectory evaluator was called in this smoke.
The final generic identity-reuse instruction was added after this recorded
smoke; its saved prompts remain unchanged and authoritative.

## Validation

Tests cover isolated inputs, fixed criterion backfilling, distinct schemas and
ID constraints, complete stage responses, rejection of future/missing/duplicate
references, preservation of raw inputs, stored-response tampering, active CLI
evaluation/reuse, and separate RS/CP states with independent metric aggregation.
The existing browser integration checks continue to exercise the shared executor.

The CLI commands remain those in
[the evaluation guide](../docs/self_verification_evaluation.md). For a fresh
independent run, use a new output directory and the updated protocol config.
Do not pass an old joint-result directory to `--reuse-checks`.
