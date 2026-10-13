# Evidence, diagnosis and repair: implementation audit

Date: 2026-10-13. Protocol: `evidence-diagnosis-repair-20261013`.

This revision aligns the evaluator with the research questions rather than
renaming archived scores. The complete specification is in
[the evaluation guide](../docs/self_verification_evaluation.md).

## Implemented workflow

1. Freeze task requirements and acceptance conditions across models.
2. Locate the earliest complete implementation attempt, retaining its original
   event ID and cached response. Analyze subsequent checks; keep earlier events
   as context. Completeness does not require a successful build or correct output.
3. Label actual checking attempts and their validity. VC requires full scope,
   a suitable method and sufficient observed feedback. Repeated checks cannot
   increase requirement coverage.
4. Independently audit every fixed requirement against the current observations,
   withholding the agent's conclusions and acquisition labels. Compare the
   original judgments only after this audit establishes diagnosis opportunities.
   Observable omitted defects lower EDR; missing acceptance of observable normal
   conditions lowers CAR. Image receipt and cited evidence determine modality.
5. Apply the same acceptance procedure to before/after versions. Share each
   acceptance label between RSR and CPR. RSR requires a confirmed failing target;
   CPR includes previously passing non-target conditions. Later rechecks remain
   references, without a separate alignment-model call or headline metric.
6. Compute point estimates and opportunity counts in Python. Average repeated
   runs within each task, then tasks equally, using the common completed task set
   across models. Report excluded tasks explicitly.

The primary rates are VTR, VC, EDR, CAR, RSR and CPR. EDR/CAR have visual and
textual breakdowns. CV remains an intermediate coverage label; old BDA bounds,
VCS scoring and separate RS/CP acceptance judgments are removed from the main path.
No-opportunity cases are N/A. Missing evaluation inputs prevent a completed score,
rather than being guessed or removed silently from its denominator.

`scope.py` adds the boundary annotation. `check_stages.py`, `evaluation.py`,
`states.py` and `metrics.py` contain the main changes. The runner, acceptance
preparation, reports and training stage names use these interfaces. Existing
parsers, source IDs, version restoration, benchmark adapters, JudgeClient,
response storage and caches are reused. No dependency or snapshot system was added.

## Validation

Run the relevant evaluator and training contract tests with:

```bash
PYTHONPATH=src /root/miniconda3/envs/3dcodebench/bin/python -m pytest -q \
  tests/test_vsv*.py tests/test_training*.py tests/test_verification_training.py \
  --tb=short -r s
```

Result: **347 passed, 10 skipped**. Skips require local Playwright/Chromium,
archived evaluation artifacts, or pandas for the training dataset export test.

The controlled tests cover boundary selection, blinded inputs, independently
discovered diagnosis opportunities, missing judgments, validity-gated coverage,
shared acceptance labels, target exclusion from preservation, cache validation,
model comparison and training interfaces. Adapter tests execute real subprocess
probes on controlled SWE-MM, 3DCodeBench and GameDevBench fixtures; their semantic
responses are test fixtures, not new benchmark results.

The broader repository test run encountered missing `pandas` during collection.
With `test_interactweb_official_run.py` excluded, the exploratory run recorded
531 passed, 19 failed and 14 skipped. Failures concern unavailable Playwright,
local/archived datasets, upstream snapshot consistency, and two existing recorder
version-detection tests. Those recorder functions are unchanged; the failures were
not consistent between isolated current/baseline runs and remain unresolved here.
This is not a claim that the whole repository passes in this environment.

`git diff --check` and the shared evaluation CLI help command also pass.

## SmartRecruiters source audit

The offline audit reuses the original Opus trajectory and its existing round
annotations, without treating them as fresh model labels:

- Source: `data/vision2web/vsv_eval_fixtures/frontend__smartrecruiters__claude-opus-4-8__self_verify/trajectory/run.json`.
- Source SHA-256: `b79e6b0600907598b04070ed7067891fede37a691a989a300c75e6171833bc2c`.
- 356 events; 25 existing extracted rounds.
- Reference initial-implementation boundary: event 191. Event 189 describes the
  completed pages; events 190-191 write the server and return. This boundary is
  a source-reading reference, not a saved boundary-model judgment.
- 22 rounds follow that boundary: 8 with actual image input and 14 with text only.
- Visual cores: 231-237, 247-259, 275-278, 279-282, 283-286, 287-292, 293-298,
  and 340-346. These counts are structural audit results, not metric scores.
- A ninth legacy visual-attempt label at 229-230 contains no actual image input.
  It remains a check attempt but does not establish a visual diagnosis opportunity.
- All recorded observation image files needed by these rounds are available.
- Removing exact duplicated call payloads produces a 412,238-character boundary
  packet without truncating source code or observations.

Local audit records and offline inputs are in
[`runs/vsv_eval/protocol_audit_20261013/smartrecruiters/`](../runs/vsv_eval/protocol_audit_20261013/smartrecruiters/),
including `structural_audit.json`. Runtime outputs are intentionally not tracked.
The old 27-requirement catalogue still marks `homepage_hero_content` unresolved
because task text and prototype button wording conflict. A fresh, consistently
resolved acceptance specification is needed before a new scored run; archived
catalogues and responses were not overwritten.

## Model calls and remaining empirical work

Actual API requests in this revision: **0**. The default profile remains
`gemini-3-flash-preview`. For a new scored run, the boundary needs one annotation;
each judgment prefix uses up to four calls (acquisition, validity, observation,
verdict); acceptance is shared per version/evidence group. Extraction, reference
association, planning and GUI actions retain their existing calls.
`model_calls.json` records measured counts and cache hits.

The implementation and controlled tests do not establish semantic judge accuracy.
Fresh boundary/observation/acceptance labels, a fixed judge configuration and an
independent calibration set are needed for reported model comparisons. Replayed
images retain reconstruction provenance; their equivalence to historical pixels
is not assumed. No claim is made that old scores are new-protocol scores.
