# Protocol implementation and SmartRecruiters pilot

Date: 2026-10-03. Local baseline: `4238d7bf4a00234546765befd976e1131f8236a3`.
The local development tree was used; GitHub was not used to replace it.

## Implementation

`score_vsv.py --rounds-json --catalogue` now evaluates the existing referenced
rounds using six metrics: VC, CV, BDA, RS, CP and VCS. The main target judge
returns structured evidence labels; Python computes the metrics. Visual,
text and mixed targets share one contract, including legitimate unmatched
checks. No overall weighted score is produced.

Task-only catalogue drafting and human approval validation are in `catalogue.py`.
Time-bounded packets and original response validation are in `evaluation.py`.
Exact state execution and repair/preservation records are in `states.py`.
Metric formulas and macro aggregation are in `metrics.py`. Existing extraction,
event pairing, source/version tracking, assertions and JudgeClient are reused.

The original browser replay function was moved from the pilot CLI into
`replay.py` and is shared by both callers. Unused imports were removed. Current
and historical commands are distinguished explicitly; archived files/results
were retained. No new dependency, provider client, retry system, snapshot
system, diff system, training run or GitHub upload was introduced.

The [implementation guide](../docs/self_verification_evaluation.md) gives
commands, contracts and limits. The user's protocol document is preserved.

## Real input and outputs

The input is SmartRecruiters (Vision2Web L2), Claude Opus 4.8, Claude Code.
The fixture contains 356 normalized events and 136 ID-paired calls.
Source SHA-256:
`b79e6b0600907598b04070ed7067891fede37a691a989a300c75e6171833bc2c`.

The actual source hash is recorded in the extraction and evaluation JSON;
those records are authoritative. The pilot reuses the existing conversation-
reviewed extraction rather than inventing new trajectory events.

- 93 rule candidates; 42 retained calls organized into 25 rounds.
- 8 rounds with actual image inputs, 1 visual attempt without an image,
  and 16 nonvisual rounds. The 5 mixed-evidence rounds overlap the image group.
- The joint target judge evaluates 22 rounds and returns 70 target records.
  Three scope exclusions retain their original extraction records for review.
- The catalogue draft contains 27 necessary goals. It remains unapproved.
- Five audited code states cover original Edits 238–308; all six touched files
  match the recorded final snapshot.
- All five states execute six routes and five functional probes: 30 route
  captures and 25 probe executions. Executed probes are not automatically
  passing targets.
- V0, V1, V3 and V4 have accepted model state labels. V2 retains four exact
  assertion results and 28 unknown semantic/visual states after a malformed
  response and an explicit subsequent API timeout.
- Original repair links at 216 and 225/227 lack recoverable before/after
  artifact pairs. They remain explicit repair gaps, rather than successes or
  silently omitted cases.

All paths below are relative to `runs/vsv_eval/protocol_smartrecruiters_20261003/`:

| Artifact | Path |
| --- | --- |
| Final computed results | `combined/scores.json` |
| Target report and class counts | `combined/report.md` |
| Core events, relationships and actual label request references | `episode_review.json` |
| Necessary-goal draft | `catalogue_atomic_retry/catalogue.json` |
| Audited state specification | `state_spec.json` |
| Validated partial state/repair results | `states_retry/repair_results.json` |
| Actual browser evidence | `states/replays/` |
| Concrete label disputes | `label_review.md` |
| Logged request/token accounting | `api_usage.json` |

## Automatic pilot scores

These values summarize uncalibrated model labels. They are not gold labels,
benchmark success rates, or confirmed model capability findings.

| Metric | Score (0–100) | Success | Failure | Unknown |
| --- | ---: | ---: | ---: | ---: |
| VC | 70.37 | 19 | 8 | 0 |
| CV | 88.57 | 62 | 8 | 0 |
| BDA | 72.71 | 50 | 13 | 7 |
| RS | 66.67 | 2 | 1 | 3 |
| CP | 100.00 | 50 | 0 | 109 |
| VCS | 63.16 | 12 | 7 | 3 |

VC covers 19/27 fixed draft goals, with four further partially covered goals.
BDA balances normal and error class accuracies; its aggregate counts are not
used as a pooled accuracy. BDA-V is 66.48; BDA-T is 91.67; BDA-M is NA because
there is no decidable sample in that group.

RS has three known target transitions and three unknown target transitions;
one passing baseline is excluded. Two additional repair source episodes remain
unassessed, and their target counts are deliberately not guessed.
CP has 50 known preservation results and 109 unknown baseline/after results,
including the two unavailable artifact pairs. Its measured 100 does not
establish complete preservation or absence of regression. VCS has 12 known
successes, 7 known failures and 3 unknown chains.

## Judge quality and limitations

All actual model roles in this pilot use `gemini-3.1-pro-preview`. The planned
Qwen/DeepSeek service IDs had no callable channel for this account, and the
user explicitly approved a Gemini-only experiment. Successful requests used
temperature 1, high reasoning effort, a 16,384 output limit and structured
JSON schema for target/state responses. The proposed 32,768-token configuration
was not executed. Actual request IDs, reported model IDs, settings, usage,
prompts, original responses and timestamps remain in the cache. A reported
alias alone does not prove immutable backend weights.

Source review found issues that require human calibration:

1. The task and prototype disagree on a homepage hero button label.
2. The metrics criterion does not precisely distinguish the dedicated metrics
   area from metrics in a case study; episode and state labels disagree.
3. The Winston repair judge passes a screenshot that omits a mark present above
   the prototype testimonial, despite a criterion forbidding that deletion.
4. The joint judge excludes implementation asset diagnostics at 264–268;
   whether this is an intended scope exclusion needs human confirmation.

See `label_review.md` for concrete inputs and images. No labels were changed to
raise scores. These Codex source checks are not independent human calibration.
The protocol's human catalogue review and target/repair calibration remain
required before formal results. The 8 image rounds remain the three home rounds
plus Pricing, About, Winston, Customers and Careers; evaluator captures do not
create additional agent image checks or a Winston agent recheck.

An initial V2 model response degenerates into repeated text and reaches the
output limit. The program rejects it. A separate manual attempt with unchanged
parameters times out. Online failures still raise directly. An explicitly
requested offline pass reuses four valid cached responses and precise execution
facts; unavailable semantic states remain unknown and state status is partial.
There is no automatic schema repair, default acceptance/drop, or model voting.

## Executed commands

Commands run from the project root with the API environment already configured:

```bash
python3 scripts/vision2web/score_vsv.py \
  --rounds-json runs/vsv_eval/verification_minimal_smartrecruiters_20261003/verification_rounds.json \
  --catalogue runs/vsv_eval/protocol_smartrecruiters_20261003/catalogue_atomic_retry/catalogue.json \
  --task-root runs/vsv_eval/protocol_smartrecruiters_20261003/task \
  --output-dir runs/vsv_eval/protocol_smartrecruiters_20261003/checks_v3 \
  --primary-profile gemini31 --allow-draft --workers 2 \
  --reference-map runs/vsv_eval/protocol_smartrecruiters_20261003/checks/reference_mapping.json \
  --reuse-checks runs/vsv_eval/protocol_smartrecruiters_20261003/checks_v2

python3 scripts/vision2web/score_vsv.py \
  --rounds-json runs/vsv_eval/verification_minimal_smartrecruiters_20261003/verification_rounds.json \
  --catalogue runs/vsv_eval/protocol_smartrecruiters_20261003/catalogue_atomic_retry/catalogue.json \
  --task-root runs/vsv_eval/protocol_smartrecruiters_20261003/task \
  --state-spec runs/vsv_eval/protocol_smartrecruiters_20261003/state_spec.json \
  --output-dir runs/vsv_eval/protocol_smartrecruiters_20261003/states_retry \
  --primary-profile gemini31 --allow-draft --workers 2 --offline

python3 scripts/vision2web/score_vsv.py \
  --check-results runs/vsv_eval/protocol_smartrecruiters_20261003/checks_v3/scores.json \
  --repair-results runs/vsv_eval/protocol_smartrecruiters_20261003/states_retry/repair_results.json \
  --output-dir runs/vsv_eval/protocol_smartrecruiters_20261003/combined
```

The final command validates stored evidence and responses and makes no API call.
The check pilot explicitly reuses completed earlier labels, retaining their
actual prompts. This includes pilot prompt revisions; a formal comparison must
use one frozen configuration and a fresh output directory.

The real browser run used Node 22.23.3 and Chromium 153.0.8010.12. Local setup:

```bash
export PATH=/tmp/vsv-viewer-browser/node_modules/node/bin:$PATH
export VSV_PLAYWRIGHT_PACKAGE=/tmp/vsv-viewer-browser/node_modules/playwright
export VSV_CHROMIUM=/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome
export LD_LIBRARY_PATH=/tmp/vsv-viewer-browser/libs/unpacked/usr/lib/x86_64-linux-gnu
```

These are this server's existing tools, not portable dependency-install commands.
For a fresh online state experiment, select a new output directory and omit
`--offline`; the state runner restores audited versions through the existing
reconstruction implementation and uses the same fixed criteria.

## Validation

166 relevant tests pass. Six archived integration tests are explicitly skipped
because their scorer inputs or old `/data/...` source/version paths are absent.
The skip guards only check fixture availability; scoring assertions were not
relaxed. The new protocol tests cover fixed denominators and unknown bounds,
absent diagnoses, balanced class macro means, repair baseline eligibility,
incomplete regression checks, original event/time/image provenance, profile
cache parameters, truncation rejection, source/label/resource tampering,
shared unavailable repairs and an offline end-to-end repair calculation.

Offline extraction also reports 93 candidates and no model selection. Offline
check evaluation preserves 25 pending rounds and does not invent scores.
Compilation, CLI help and `git diff --check` pass. The supplied credential is
absent from the modified/new source, configuration and authored documentation.

```bash
PYTHONPATH=src:/tmp/vsv-crop-test-deps-20261001:/tmp/vsv-process-test-deps-20261001 \
python3 -m pytest tests/test_vsv_protocol.py tests/test_vsv_rounds.py \
  tests/test_vsv_windows.py tests/test_vsv_checks.py tests/test_vsv_eval.py \
  tests/test_vsv_visual_judgment.py tests/test_vsv_pipeline.py \
  tests/test_vsv_repair_pilot.py tests/test_vsv_checkpoint_tasks.py -q -rs
```

## Recorded API usage

50 distinct recorded requests; 48 have returned usage. Logged totals: 1,002,369 input tokens and 188,492 output tokens, including reported reasoning tokens.

These totals include rejected pilot responses and explicit reruns, deduplicated
across copied caches. Interrupted/unreported usage is not estimated. This is
not a monetary bill or a clean frozen-protocol per-task cost measurement.
