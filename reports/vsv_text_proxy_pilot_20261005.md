# Archived-text proxy evaluation pilot — 2026-10-05

> **2026-10-06 audit: these pilot scores require reassessment.** The audit found 31 coverage labels with `evidence_basis=unavailable` but `coverage=none`, plus a response-boundary omission in the Finalsite/Opus example. The evaluator now rejects unsupported noncoverage and includes consecutive responses across round labels. Historical scores below are retained for provenance and have not been rerun. See [evidence audit](../runs/vsv_eval/leaderboard_text_proxy_20261005/evidence_audit_20261006.json).

This experiment uses the same Finalsite and Black Belt AC & Electric L2 traces across
Claude Opus 4.8, Kimi K3, GLM 5.3 Flash, and DeepSeek V4 Flash Vision Exp as the strict
pilot. Gemini 3 Flash (`gemini-3-flash-preview`, existing high-reasoning profile) judges
all cases. Each task retains its existing shared draft catalogue and exact source hashes.

## Outputs

- [Six-metric comparison](../runs/vsv_eval/leaderboard_text_proxy_20261005/comparison.md)
- [CSV with denominators, modality splits and evidence bases](../runs/vsv_eval/leaderboard_text_proxy_20261005/comparison.csv)
- [JSON with full metric records](../runs/vsv_eval/leaderboard_text_proxy_20261005/comparison.json)
- [Protocol and command](../docs/VSV_TEXT_PROXY.md)
- [Original strict comparison](../runs/vsv_eval/leaderboard_matched_models_20261005/all_metrics.md)

Accepted proxy results are under `runs/vsv_eval/leaderboard_text_proxy_20261005/completed/`.
The earlier task/model directories are smoke runs; `results/`, `audited/`, `augmented/`, and `final/` contain development
runs before the accepted strict-label augmentation and CP context review. Those files and responses are retained.
Accepted runs share response caches with development runs; unchanged requests are reused,
while changed prompts or target identities receive distinct cache keys. Request accounting
resolves this shared cache without counting the same physical log twice.

The final experiment reuses validated strict labels and fills only their unresolved fields.
Seven traces reuse all strict episodes; Finalsite/DeepSeek reuses its 24 valid strict episodes
and constructs targets only for the remaining round. This prevents a proxy re-evaluation
from replacing already established text judgments, coverage labels or missing-diagnosis
penalties. Original strict results remain byte-for-byte unchanged. The independent raw
checks are revalidated before reuse; a proxy result cannot be supplied as a strict baseline.

## Meaning of the estimates

The opt-in `--text-proxy` branch retains the existing metric arithmetic and evaluates
recorded facts plus supported textual estimates. It never loads image pixels, executes
historical code, creates snapshots or writes to strict result files. The strict finalizer
rejects the proxy schema. No new model client, retry system or voting stage was added.

Each label records direct, text_inferred, agent_report, or unavailable support. A precise
agent visual description may support a proxy state; generic approval alone does not.
BDA based only on agent_report is self-report agreement, not independent correctness.
Inferred repair and preservation estimates are not execution acceptance. A high proxy
score is not evidence of equivalent strict capability or final product quality.

Unknowns remain in reported ranges; subset percentages are not shown as complete scores.
BDA still requires both normal and error classes. CP covers the extracted actual repair
groups and fixed catalogue, not every edit in the original development trajectory.
The six previously Codex-reviewed extractions remain explicitly identified. This is a
small development pilot with shared draft criteria, not an independently calibrated ranking.

## Implementation and checks

`text_proxy.py` reuses the existing packet builder, source validation, JudgeClient and
scalar aggregation. VC, CV, BDA, RS and CP use separate named stages; VCS is calculated.
Input events are deduplicated in a batch ledger. Schema size and input length bound each
request. BDA batches stay within a check round; future cited evidence is rejected. Repair
and preservation calls receive the actual linked edits and rechecks separately.

Modality is derived by the program from the cited original image receipt, avoiding the
repeated model-generated modality mismatch that blocked the earlier DeepSeek case.
Repeated citations are normalized without changing judgments. Inapplicable symptom-match
values are cleared only where the existing scalar formula already ignores them. Malformed
references, unavailable-but-successful labels and future diagnosis evidence still fail.

The smoke review found two scope errors worth documenting. A general page-content target
had swallowed a form-behavior requirement that the described inspection had not exercised.
Also, direct URL visits and image reads were incorrectly labeled as navigation-link clicks.
The final prompts explicitly separate recorded actions from estimated artifact states and
require full coverage of the entire matched criterion. These are general rules; no task,
page, filename or anomaly-specific exception was introduced.

Tests cover strict/proxy isolation, missing-image receipt, source immutability, causal
reference boundaries, shared-edit grouping, unknown denominators, single-class BDA,
reference normalization and shared-cache request accounting. The final CP review supplies each condition's latest pre-edit check outside the defect
round and flags intervening recorded edits. Historical observations are not automatically
treated as the immediate pre-edit state. Raw input packets and model
responses remain linked from each accepted score file. API counts include smoke/debugging
and transient gateway failures; the comparison reports them separately from cache hits.

## Reproduction

From the repository root, with the existing API environment configured:

```bash
python runs/vsv_eval/leaderboard_text_proxy_20261005/run_pilot.py
PYTHONPATH=src python runs/vsv_eval/leaderboard_text_proxy_20261005/build_comparison.py
python -m pytest tests/test_vsv*.py -q
```

The runner skips existing complete outputs. A resumed incomplete run uses the original
request cache. The comparison builder refuses missing case results rather than producing
a complete-looking table from a partial subset.

The CP audit exposed an execution-context confounder: a terminated startup test shell
and an absent server had been interpreted as regressions in eleven Finalsite/Opus UI
conditions after a launcher-only cleanup. The final uniform CP prompt requires comparable
initialized runtime conditions and distinguishes interrupted setup from artifact failure.
It permits source-based preservation only with a supported scope argument; otherwise the
state stays unknown. All eight cases use this same prompt, with previous responses retained.

## Validation and interpretation

The relevant suite passed: **250 passed, 8 skipped** (`tests/test_vsv*.py`).
`git diff --check` also passed. The accepted-case audit checks all eight source hashes,
unchanged known strict target labels and diagnoses, and the absence of image paths in
every accepted proxy request. It is saved as `audit.json` beside the comparison.

Both Opus cases have no applicable RS target in the extracted links: their linked edit
removes redundant launcher arguments after a passing startup check. This is cleanup
of an already passing condition, not a measured defect repair. RS is therefore N/A,
not a missing model call or a zero repair score.

Finalsite/Opus has one fully covered catalogue requirement, seven partial and eight
uncovered requirements under the retained coverage policy. Partial coverage still
earns no full-coverage credit. Its low VC must not be interpreted as an overall
product-quality ranking. More specific descriptions yield more usable proxy evidence,
so archive detail is a confounder in comparisons across models.

Reported ranges are unresolved-label bounds, not statistical confidence intervals.
Do not collapse them to a midpoint or rank models on overlapping BDA/CP bounds.
High proxy RS frequently relies on source inference or agent reports and does not
replace executing the repaired artifact. Original strict outputs remain separate.

## Accepted results

All values below use the 0–100 scale.

| Task | Model | Targets | VC | CV | BDA | RS | CP | VCS |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| finalsite | Claude Opus 4.8 | 45 | 6.25 | 73.33 | 55.56–95.35 | N/A | 0.00–100.00 | 35.29–41.18 |
| finalsite | Kimi K3 | 65 | 93.75 | 81.54 | 82.35–94.89 | 100.00 | 66.67–100.00 | 60.00 |
| finalsite | GLM 5.3 Flash | 65 | 75.00 | 90.77 | 75.00–79.81 | 100.00 | 48.24–100.00 | 32.00–56.00 |
| finalsite | DeepSeek V4 Flash Vision Exp | 63 | 87.50 | 98.41 | 73.33–87.96 | 100.00 | 25.00–100.00 | 56.52–73.91 |
| blackbeltacandelectric | Claude Opus 4.8 | 43 | 21.05–26.32 | 81.40 | 43.61–50.79 | N/A | 94.74–100.00 | 33.33–55.56 |
| blackbeltacandelectric | Kimi K3 | 36 | 26.32 | 77.78 | 61.11–100.00 | 100.00 | 26.32–100.00 | 50.00 |
| blackbeltacandelectric | GLM 5.3 Flash | 75 | 52.63 | 89.33 | 54.77–68.85 | 100.00 | 66.67–100.00 | 42.86–57.14 |
| blackbeltacandelectric | DeepSeek V4 Flash Vision Exp | 88 | 84.21 | 94.32 | 85.29–92.01 | 100.00 | 50.00–100.00 | 65.38–73.08 |


All six cases with applicable RS targets received a proxy RS of 100. Their target
counts range from one to ten. This ceiling limits discrimination and cannot be read
as proof that every repair actually passed execution. Coverage, recorded method
validity and closure statistics offer more useful descriptive variation in this pilot.

The accepted outputs reference **149 distinct proxy requests**: coverage 16, CV 16,
BDA 86, CP 23, RS 7, and one missing-round VC extraction. VCS requires no model call.
Including smoke runs, prompt audits, retries and development reruns, logs record
**599 actual API requests and 361 cache hits**. These are separate accounting measures;
149 is not the total development consumption. Existing strict source judging is not
part of this new experiment count.

The final artifact audit passed for all eight cases: 475 existing strict targets
retain their known labels and diagnoses, and all 149 accepted proxy requests contain
no image paths and name the configured Gemini model.
