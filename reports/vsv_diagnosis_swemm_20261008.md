# SWE-MM verification diagnosis — 2026-10-08

## Scope and selection

This is an evidence-linked qualitative pilot by Codex in this session, with zero
paid model API calls. It diagnoses checks and their use; it does not produce a new
six-metric leaderboard or measure the benefit of a new policy.

The selected pair is **Claude Opus 4.7** and **Qwen3.5-Flash**. Their official
SWE-MM score rows both report OpenHands **v1.17.0**, with solvable accuracy **48.5%**
and **27.9%**, respectively. Both metadata files declare vision support. These are
source leaderboard values, not scores calculated in this analysis. Opus 4.8 uses
v1.24.0 in its SWE-MM row, so the existing Opus 4.8 replay was not mixed into this
comparison. The inspected GLM-5.1 and DeepSeek-V4-Pro metadata declare no vision
support, so they were not selected for this visual-capability comparison.

There are still important confounders: the archive roots identify different SDK
revisions (`e9fb43d` versus `e212d45`), and the downloaded native Chart histories
are Opus attempt 1 versus Qwen critic attempt 3. Qwen's WordPress history is also
attempt 3. The Opus WordPress source is a complete original output log, whose
attempt was not established. This supports case diagnosis, not a controlled
causal claim about model strength.

Sources: [Opus scores](https://github.com/OpenHands/openhands-index-results/blob/main/results/claude-opus-4-7/scores.json),
[Qwen scores](https://github.com/OpenHands/openhands-index-results/blob/main/results/Qwen3.5-Flash/scores.json),
[Opus metadata](https://github.com/OpenHands/openhands-index-results/blob/main/results/claude-opus-4-7/metadata.json),
[Qwen metadata](https://github.com/OpenHands/openhands-index-results/blob/main/results/Qwen3.5-Flash/metadata.json).

Only two shared tasks were deeply inspected. Selection follows accessible archive
prefixes, not random sampling. Each archive was fetched in eight 1 MiB ranges;
earlier interrupted prefix reads and a 1 MiB diagnostic read added small transfer
overhead. No full archive or execution image was downloaded. A few other complete
JSON rows encountered in those prefixes are retained but were not diagnosed.

## Cases and outcomes

| Task | Opus evidence | Qwen evidence | Official per-instance outcome |
| --- | --- | --- | --- |
| Chart.js-8705 | 108 native events; 4 tick tests + 46 scale tests; reverting the production fix reproduces the crash | 285 native events; repeated browser navigation and source inspection; build/lint pass but original full browser test runner fails to launch | Both resolved |
| wp-calypso-35531 | Complete output log; edits PlanStorage; 57 targeted tests pass | 145 native events; edits the audio/video nudge; nine source-string checks pass | Both unresolved |

The official outcomes are sidecar summaries of the submissions. They do not
necessarily identify the selected retry. Their causal failure reasons were not
retrieved. A solved task can still contain unsupported verification claims;
passing local tests can coexist with an unresolved official task.

## Findings

### 1. Navigation is mistaken for observing the test result

In Qwen Chart #237–241, navigation succeeds and `browser_get_state` returns three
canvas elements. The call explicitly sets `include_screenshot: false`; the
response contains no test success/error text or console error receipt. The next
thought says the page loaded without errors. That statement is stronger than the
returned evidence. It does not establish that the page actually had an error.

There are 25 browser calls in this history, many opening source files through
`file://`, and **zero recorded output-image inputs**. Browser call count therefore
cannot stand in for visual verification. Opus Chart has no browser calls and also
no output images; its numerical crash can be checked with targeted nonvisual tests.

**Candidate change:** an inspection should retrieve the observable it claims to
check: runtime assertion/error text for a crash, pixels for a visual claim. Keep
this as a small evidence contract, not a requirement to screenshot every action.

### 2. Truncated successful shell commands can hide incomplete tests

Qwen Chart #217 pipes `npm test` into `head -50`. Its returned prefix contains
completed Markdown/TypeScript jobs and warnings from another linter. At #219 it
says the lints pass. This is premature completion inference, **not a direct
contradiction with lint errors**: those entries are warnings, and later #255
actually confirms zero JavaScript lint errors.

The full test attempt #233–234 fails to launch Firefox/Chrome, despite the outer
pipeline's exit code being zero. Qwen correctly acknowledges this at #235.
Opus also encounters module-import and headless setup problems; at #84 a temporary
config runs **zero tests**, then it repairs the runner and obtains real test counts.

**Candidate change:** return the producer's exit status and an explicit completed
assertion count. Reject zero executed tests as a successful test receipt, and
provide a known headless project test command. Environment failures remain
separate from product failures.

### 3. A matching component name can become the wrong visual target

The WordPress issue's reference image shows the storage meter and adjacent
**Upgrade** link. The fetched reference is saved under `references/wp35531-0.png`
and was visually inspected in this session. Qwen #73 edits
`list-plan-upgrade-nudge.jsx`, whose text explicitly concerns audio/video uploads.
At #81 it identifies this as the top-right storage nudge. Its patch contains no
PlanStorage change. The original Opus log instead traces the visible element to
`MediaLibraryFilterBar.renderPlanStorage()` and `PlanStorage` (lines 4151–4155).

**Candidate change:** bind the reported visible element and required role/state
to its render owner before editing. One targeted role-dependent observation is
more valuable than repeated source inspection of a similarly named component.

### 4. The test verifies the patch's spelling instead of the requirement

Qwen WordPress's actual Jest attempts fail (#57–71). The substitute checker at
#109 uses nine `content.includes(...)` predicates: imports, a prop name, the
condition's text, and export syntax. It returns 9/9 at #112. None of these executes
the component for admin/editor/author roles. Nevertheless, #141 reports a
role-by-role success table and no breaking changes. Several summary documents
and repeated inspections after #112 add no new role-specific evidence.

A small **new no-API mutation experiment** was run against those exact nine
predicates. Invert the permission guard, retaining the old spelling in a comment:

- Original implementation: **9/9 predicates pass**.
- Opposite permission guard: **9/9 predicates still pass**.

This demonstrates an insensitive oracle. It is a source-predicate experiment,
not an execution of the full application and not evidence that a proposed agent
policy improves outcomes. Reproduce with:

```bash
python runs/vsv_eval/diagnosis_20261008/swemm/oracle_experiment.py
```

**Candidate change:** use one behavior-level assertion independent of the patch
text, and require it to reject at least one known wrong state. For a permission
bug, render an authorized and an unauthorized state; do not infer them from imports.

### 5. Stronger execution does not eliminate criterion mismatch

Opus WordPress gets 57 tests passing, which is valid execution evidence. It also
hides the entire storage component, and its added test expects no component to
render. The final claim that all relevant behavior is preserved exceeds the
scope of these checks. The official outcome is unresolved, but this audit does
not assert that hiding the meter is the proven official failure cause.

**Candidate change:** freeze the acceptance question from the issue before reading
the patch. Verify the requested nudge behavior and relevant unaffected information
separately; do not silently change the oracle to match the chosen implementation.

## Positive controls

- **Opus Chart #87–93:** targeted tests pass; removing the production change makes
  the same newly added test reproduce the TypeError. This is a real original
  counterfactual check, even though it was performed after the initial patch.
- **Qwen Chart #219–229:** the agent detects that its first guard is placed after
  the unsafe array access, corrects the order, then notices and removes duplicated
  code. This is successful source-level self-correction.
- **Qwen Chart #235:** the agent correctly distinguishes browser environment
  failure from test assertions. Do not relabel this as a product failure.
- **Opus WordPress:** 13 storage and 44 media-library tests really execute and
  pass. Their limited scope should not erase that successful checking behavior.

## Minimal experiments to run when an inference endpoint is available

Use the same starting checkpoints, tools, budgets, and tasks in each condition:

1. Baseline versus a completed-evidence receipt (producer status, nonzero test
   count, actual assertion/error output). Measure unsupported completion claims
   and time spent recovering the runner.
2. Baseline versus one requirement-derived check plus a known failing control.
   Measure defect detection and final patch correctness, not only more tests.
3. For the WordPress checkpoint, require one visual-location-to-render-owner link
   and role-specific observation before editing. Measure wrong-target repairs.

These are intervention hypotheses grounded in the cases, not demonstrated model
improvements. The current sample is too small and attempt-confounded for a ranking.

## Artifacts

All under `runs/vsv_eval/diagnosis_20261008/swemm/`:

- `sample_manifest.json`: source paths/hashes, event counts, metadata, official
  outcome references, and comparability limits.
- `findings.json`: 12 annotated findings with original native UUIDs and history
  indices; the original output-log case uses line ranges instead of invented IDs.
- `rows/`: unchanged complete source JSON records from bounded archive prefixes.
- `selected/`: the original Opus WordPress output log.
- `index/`: retrieved model metadata, score rows, and per-instance sidecars.
- `oracle_experiment.py` and `.json`: the new bounded sensitivity experiment.
- `references/`: fetched task reference images, not agent self-observations.

No scorer formulas, source trajectories, or benchmark implementations were changed.
