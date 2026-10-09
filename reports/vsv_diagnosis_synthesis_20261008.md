# Verification diagnosis and intervention plan — 2026-10-08

This study uses existing trajectories to identify concrete verification mechanisms
worth improving while API evaluation is unavailable. It is an exploratory audit
by Codex in the current session, with zero paid model requests. Findings are tied
to original events; deterministic experiments test selected mechanisms. Neither
the audit nor those experiments establish a model-wide ranking or an end-to-end
improvement from a new agent policy. No scoring formulas were changed.

The [study index](../runs/vsv_eval/diagnosis_20261008/study.json) records scope and
validation. The [finding table](../runs/vsv_eval/diagnosis_20261008/findings_index.csv)
links 43 records to the three original annotation files. Records include positive
controls and repeated environment issues; **43 is not a count of unique errors**.
All referenced evidence paths exist, 22 selected source-file hashes were checked,
and the four mechanism experiments' control outcomes were verified.

## Sample and comparison limits

| Benchmark | Models | Matched tasks | Trajectories | Selection and limits |
| --- | --- | --- | ---: | --- |
| Vision2Web L2 | Claude Opus 4.8 / GLM 5.3 Flash | FSB, Kitten, AAP | 6 | Three shortest common frontend runs by maximum conversation length, selected before outcomes. Same Claude Code family and byte-identical task prompts; short-run convenience sample. |
| 3DCodeBench text | Claude Opus 4.8 / Gemini 3 Flash | AgaveMonocot, AquariumTank, ArmChair | 6 | First three common task IDs alphabetically, seed 0. No GLM/DeepSeek/Qwen logs in the inspected pinned source. Different CLIs, 900s/600s budgets, and some prompt differences. |
| SWE-MM | Claude Opus 4.7 / Qwen3.5-Flash | Chart.js-8705, wp-calypso-35531 | 4 | Bounded archive sample. Both score rows declare OpenHands v1.17.0 and vision support, but underlying SDK revisions and selected critic attempts differ. |

These are 16 task-model trajectories, not 16 independent tasks. The model pairs
implement the requested stronger/comparator exploration; examples do not prove
that every cheaper or open-weight model verifies less effectively. In particular,
GLM supplies strong positive verification examples in this sample.

Original source URLs, pinned revisions, task/model selection and file hashes are
in each benchmark's source inventory. Detailed audits:

- [Vision2Web](vsv_diagnosis_vision2web_20261008.md)
- [3DCodeBench](vsv_diagnosis_3dcodebench_20261008.md)
- [SWE-MM](vsv_diagnosis_swemm_20261008.md)

## What the evidence supports

### A. An executed check can miss its stated purpose

**Vision2Web, Kitten/Opus #246–248:** the agent announces an industry-card click
check but directly opens the destination URL and reads its heading. This checks
that the destination exists, not that the card reaches it. The final response
also claims the card links route correctly. The original link may work; the
verified problem is that the intended behavior was not exercised.

**SWE-MM, Qwen/wp-calypso:** its generated static checks search for source strings.
The controlled counterexample below preserves those strings while reversing a
permission condition; all nine checks still pass. Such checks cannot establish
that the permission behavior is correct. The issue text and reference image identify
the storage meter's adjacent Upgrade link, but the edited component is an audio/video
upload nudge. Checking that different component cannot establish that the requested
visible element was repaired. The task text also supplies this distinction, so the
target-binding mistake does not isolate an image-perception failure.

This is a verification issue even when the final implementation happens to pass.
A command count, a test file, or a green test summary is insufficient evidence of
checking the intended condition.

### B. Completion claims can exceed consumed evidence

**Vision2Web, FSB/Opus:** About is included in a screenshot-generation batch but
its output screenshot is not consumed. Final summaries claim every page was
visually compared. About does have a useful DOM padding measurement; the problem
is specifically the broader visual-comparison claim.

**Kitten/Opus:** the Food prototype is read, but the app's Food page has only a
route/heading check, despite the final all-pages visual-comparison claim.

**AAP/Opus:** mobile responsiveness is reported as verified, while recorded
browser viewports are desktop and no mobile interaction test is present.

These are evidence-scope errors, not proof that the pages are visually wrong.
Deriving the final verification summary from actual observations could reduce
this gap without requiring lengthy new model output.

### C. The test itself can be wrong, and a repaired test can be forgotten

Both Vision2Web models choose incorrect dropdowns or ambiguous links and then
repair their tests. AAP/GLM separates two defective assertions from an actual
mobile-nav class bug, changes the relevant test or app code separately, and
records 16/16 assertions passing. Treating all three earlier FAIL lines as product
defects would mislabel this behavior.

FSB/GLM later recreates a smoke test with the same wrong-dropdown problem after
an earlier correct sequence. Retaining the validated setup and selector is a
specific, small intervention; simply asking for another check can reproduce the
same mistake.

### D. Execution feedback can hide real errors

In 3DCodeBench the recorded Blender-plus-tail command can return zero while the
script raises an exception. The local controlled experiment confirms this and
shows a correction. Tool transport success must remain distinct from successful
artifact execution.

Two Claude 3D tasks additionally require Write while the actual tool rejects it
as disabled. Both recover using a supported file-writing path. In Vision2Web,
broad cleanup/startup commands coincide with seven exit-144 returns across six
traces. These are tool/environment issues, not evidence that the website is wrong;
the exact process-level causes of the seven interruptions were not reproduced.

These fixes are necessary engineering baselines. The Blender flag change alone
is not the proposed scientific contribution.

### E. Initial coding errors can be followed by effective self-verification

Gemini Agave and Aquarium contain three distinct intermediate API/runtime errors.
All three are reproduced from recorded script versions. Subsequent edits and
reruns reach successful execution. Claude ArmChair likewise detects a wrong
script path, copies it into place and retests successfully.

A larger count of initial errors does not imply worse diagnosis or repair. Keep
these as positive chains and distinguish implementation errors, checking errors,
and unsuccessful repairs in the analysis.

### F. Visual and textual evidence often cooperate

FSB/Opus turns a visual padding concern into DOM measurements and repeats the
same check after editing: `titleLeft=0` becomes `40`. Kitten/GLM follows missing
logos with image-load observations, repairs the relevant code, observes nonzero
image dimensions, and reads a new screenshot. These are concrete sequences worth
preserving in a method or training set.

They do not by themselves prove the independent benefit of pixels: a text-only
method might find the same issue. A future matched visual-versus-text intervention
is needed for that causal claim.

## Important data limits

All 131 image receipts in the six selected Vision2Web exports lack pixel bytes;
66 receipts are self-output images and the rest are reference/asset material.
This audit can establish operations, receipts, edits and text observations, but
cannot independently determine the accuracy of all historical visual judgments.

All six sampled 3D prompts explicitly prohibit rendering. Zero agent output-image
inputs therefore cannot be scored as a failure to exploit an available visual
check. A render-permitted experiment is a different protocol and must be reported
separately. Eight Gemini shell-result records also omit stdout; local execution
supplies new evaluator evidence, not the original observations the agent saw.

SWE-MM outcomes, selected attempts, SDK revisions and observation availability
are recorded in its report. Passing an official task does not establish that
every intermediate claim was supported; failing one does not prove every useful
check or repair was wrong. The small, non-random sample does not estimate the
population prevalence of any failure type.

## Experiments completed without a model API

| Experiment | Control | Changed condition | Observed result | What it establishes |
| --- | --- | --- | --- | --- |
| Browser navigation | Correct card link | Wrong card destination; Food route still exists | Direct URL + heading passes both. Actual card click + destination check fails the broken case. | The recorded route-only check has an interaction blind spot. Controlled fixture, not an original-site defect. |
| Static permission oracle | Qwen's edited source | Invert permission guard; retain its old spelling in a comment | Original and mutant both pass 9/9 exact string predicates. | The recorded static oracle is insensitive to this opposite condition. No app behavior or new model rollout was executed. |
| Blender feedback | Failing restored Agave script with original template | Add Python exception exit status and pipefail | Original: 0; pipefail-only: 0; strict: 1. Repaired-script strict control: 0. | A minimal feedback fix distinguishes this failure from successful execution. |
| Oracle sensitivity, supplementary p5 case | Existing pre-edit replay | Existing post-edit replay | Identical canvas pixels; original pixel predicate fails both; six relaxed thresholds also do not distinguish versions; attribute-disable records change. | Changing a threshold to obtain a pass does not establish repair discrimination. Existing replay evidence, not a new gain experiment. |

The 3D study also executed all eight recorded Gemini check versions, obtaining
three exceptions and five clean executions matching the subsequent reported
failure/recovery sequence. The supplementary p5 case is from the earlier Opus4.8
runtime study and is **outside** the 16-trajectory paired sample. Its original
agent recognizes the pixel/lighting discrepancy and inspects the output, a useful
positive example. That replay did not recover the original device-specific visual
failure; internal state changes are not proof of user-visible improvement.

Scripts and results are under `runs/vsv_eval/diagnosis_20261008/`:

- `vision2web/navigation_probe.py` and `.json`
- `swemm/oracle_experiment.py` and `.json`
- `3dcodebench/experiment/audit_replay.py`, `replay_results.json`,
  `check_exit_status.py`, `exit_status_results.json`
- `experiments/oracle_sensitivity.py` and `.json`

## Two method hypotheses to prioritize

### 1. Bind a check to the condition it must distinguish

Before executing a check, retain only a short condition and expected observable.
The existing action and return IDs supply the remaining record; no large new
semantic schema is needed. For example:

> Condition: clicking this industry card opens its matching detail page.
> Observable: destination URL and heading after that click.

The verification policy must select an operation that tests this condition.
Direct navigation is insufficient for this example. A visual-appearance claim
requires consumed output pixels from the relevant state; an interaction can use
appropriate DOM or runtime observations. Reuse the existing tool calls and image
provenance, rather than adding a new agent framework.

For a learned policy, contrast useful checks with recorded near-misses such as
URL bypasses and string-only permission tests. The target is informative evidence
selection and supported conclusions, not a longer verbal explanation. Whether
this improves generation is untested.

### 2. Retain the validated check and reuse it after a relevant change

Store the already successful setup, operation and postcondition by reference.
After a linked application edit, rerun that check against the new version instead
of inventing a weaker replacement. Preserve the distinction between repairing a
test and repairing the product. A test changed only to accept the current output
must not become evidence of a repaired requirement.

This targets repeated selector mistakes and enables precise before/after evidence.
It also fits the current check → modification → recheck representation. It should
not force every edit to rerun every page or expand the metric catalogue.

## Next experiment, when model access is available

Use held-out tasks and identical starting checkpoints. If starting fresh sessions,
use the same task/context and code in both arms; do not compare their scores to
full original trajectories as though only the intervention changed.

| Arm | Change from the shared baseline |
| --- | --- |
| A | Existing agent with corrected deterministic tool feedback. |
| B | A + short condition/observable binding before verification. |
| C | A + retain and rerun validated checks after related edits. |
| D | A + both B and C, only after the individual effects are measured. |

Keep model, tool availability, task, initial code and total budget matched. Count
failed attempts, model tokens and execution time as costs. Use the existing six
metrics where applicable, alongside frozen final task acceptance, regression
observations and budget consumption. Do not reward screenshot count, narration
length, or self-reported success. These case studies are development examples;
use a separate test set for confirmatory gains.

For the visual research question, add a controlled image-versus-text evidence
comparison with identical available actions and explicit rendering permission.
Freeze checks before outcomes and use original or separately attributed replay
pixels. The current evidence motivates this study, but does not establish that
visual feedback necessarily outperforms text observations.

RL can follow once these interventions have a measured effect and a reliable
outcome signal. The present work supplies positive and negative process examples;
it does not establish a reward formula, a trained model, or a novel algorithm.
