# Archived-text proxy evaluation

`score_vsv.py --text-proxy` is an opt-in protocol for incomplete trajectory archives.
It estimates VC, CV, BDA, RS, CP and VCS from recorded operations, text observations,
source edits and specific agent descriptions. It supplies no image pixels, executes
no historical code and creates no snapshots. It does not replace strict evaluation.

```bash
python scripts/vision2web/score_vsv.py \
  --text-proxy --rounds-json ROUNDS_JSON --catalogue CATALOGUE_JSON \
  --task-root TASK_ROOT --config JUDGE_CONFIG --primary-profile gemini3flash \
  --allow-draft --workers 3 --reuse-checks STRICT_RESULT_DIRECTORY \
  --output-dir NEW_PROXY_DIRECTORY
```

The default invocation without `--text-proxy` is unchanged. Proxy artifacts use schema
`self-verification-text-proxy-1`, which the strict finalizer rejects. Use a separate
output directory. Existing results are never replaced. API configuration, profiles,
raw responses, cache keys and request accounting use the existing JudgeClient.

## Reuse established labels

With `--reuse-checks`, original strict episode records are validated against their source
packets and responses. Known coverage, validity and diagnosis labels are preserved. Only
unknown coverage, unresolved validity and unknown states/matches receive proxy calls.
Original agent diagnoses and missing-diagnosis penalties are retained. A partially completed
strict run can supply its validated episode files; only genuinely missing rounds need new
proxy target extraction. `proxy_coverage` fills unresolved coverage of fixed existing targets;
`proxy_vc` creates targets only for rounds without a validated base. These are disjoint sets.
For seeded BDA, the input is rebuilt at the original diagnosis cutoff. Batches do not combine
different cutoffs, even within the same round. Prior metric verdicts are not judge input.

## Evidence and labels

- `direct`: factual recorded tool/source evidence establishes the assessed property.
- `text_inferred`: a reasoned inference from operations, code or contextual evidence.
- `agent_report`: a specific agent description is the sole basis, not independently verified.
- `unavailable`: no defensible basis; it does not default to pass or fail.

Each metric has a separate request stage (`proxy_vc` or `proxy_coverage`, `proxy_cv`, `proxy_bda`, `proxy_rs`,
`proxy_cp`). VCS is computed, with no extra model request. Input and schema size limit
batching. Raw events appear once in a batch ledger; each item references only its own
admissible event IDs. Diagnosis batches do not combine different rounds. They assess
the final round, and references after the cited diagnosis are rejected. Future edits
and rechecks appear only in the repair/preservation stage, not the original check packet.

VC returns attempted conditions and catalogue associations. The program assigns target
IDs and derives modality from cited recorded image receipt. Missing archived pixels do
not turn a visual check into a text check. Full coverage requires a sufficiently specific
inspection; a generic screenshot never covers all visible elements automatically.
CV and BDA receive condition identities and raw evidence, not one another's verdicts.

The existing scalar formulas and missing-diagnosis penalties are reused. In particular,
BDA remains balanced normal/error accuracy; a missing class is N/A, and unresolved labels
remain counts/bounds. Self-report-based BDA measures consistency with the report, not
independent correctness. Aggregate estimates must always be accompanied by evidence-basis
counts. They cannot be substituted for strict benchmark scores.

## Repair scope

Recorded repair links provide candidate actual edits and corresponding rechecks. Shared
identical edit groups are counted once for CP. RS includes conditions addressed by the
actual edits, excluding already-normal baseline targets. Post-edit source reasoning can
support an estimate, but does not establish executable acceptance. A recheck of one page
cannot establish another page's state.

CP compares each fixed catalogue condition before/after each extracted repair group.
Unrelated code is not presumed correct merely because no regression was reported.
The scope is the linked repair groups, not every code change in the trajectory. For each condition, the packet includes its latest relevant pre-edit check, even when
that check is outside the defect round. Intervening recorded edits are supplied and flag
that this historical baseline may have changed. Missing baselines remain unknown.
CP compares comparable initialized runtime conditions: an interrupted launch/test shell
does not establish that the edit broke all page properties. RS/CP/VCS based on inferred
states are proxy estimates only.

## Saved artifacts

`inputs/` and `judge_cache/` retain every supplied fact and original model response.
`vc_labels.json`, `coverage_labels.json`, `cv_labels.json`, `bda_labels.json`, `rs_labels.json`, and `cp_labels.json`
retain validated stage labels and request hashes. `scores.json` contains source hashes,
all metric counts, original references and evidence-basis counts. A malformed response
fails explicitly; no automatic semantic correction, voting or default-pass loop is added.
