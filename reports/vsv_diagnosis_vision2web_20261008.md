# Vision2Web verification diagnosis — 2026-10-08

## Scope and selection

This is an exploratory, evidence-linked audit by Codex in the current session,
with **zero paid API requests**. It diagnoses verification behavior and proposes
interventions; it does not produce new six-metric scores or a model ranking.

We compare Claude Code submissions for **Claude Opus 4.8** and **GLM 5.3 Flash**.
Both contain 193 local trajectories. The nominal stronger/comparator choice
follows the requested model contrast, not a measured ordering in this study.
Before reading outcomes, we selected the three shared `frontend` tasks with the
smallest maximum conversation length across the two models, breaking ties by
task name: FSB, Kitten and AAP. This intentionally reduces audit cost and biases
the sample toward shorter runs. These are Vision2Web L2 tasks.

| Task | Opus raw conversation entries | GLM raw conversation entries | Opus self-output image receipts | GLM self-output image receipts |
|---|---:|---:|---:|---:|
| FSB | 273 | 354 | 8 | 15 |
| Kitten | 320 | 360 | 11 | 10 |
| AAP | 294 | 366 | 8 | 14 |

Receipt counts are **not** check-round counts, success counts, or quality scores.
All six traces contain self-output image receipts. Across them, 66 of 131 image
receipts refer to the agent's application; the other 65 are prototypes, resources
or crops/contact sheets used to understand those materials. Every original image
receipt lacks its image bytes in the export. No reconstructed-image sidecar exists
for these six selected cases. We therefore do not independently certify their
visual accuracy. Text, exact actions, edit contents and image-consumption facts
still support the findings below. A missing archive image is not agent negligence.

All six source prompts match the corresponding local extracted task prompt
byte-for-byte. Raw source hashes, recorded model names and absolute source paths
are saved in [inventory.json](../runs/vsv_eval/diagnosis_20261008/vision2web/inventory.json).
The existing role-preserving importer and candidate-window extractor were reused;
no scorer formulas were changed. Source `status=success` means the run finished,
not that its website passed the benchmark.

## What actually goes wrong

### 1. The planned check and executed check can differ

**Kitten / Opus:** at #246 the agent announces an industry-card click test.
#247 instead calls `goto('/industry/food')` and reads the page heading; #248
returns `FOOD INDUSTRY`. The destination renders, but the card action was not
executed. This is a check-scope gap, not proof that the card is broken. The final
response #299 also describes the card links as routing correctly.

The same trace claims in #299 that every page was screenshotted and compared.
The recorded output images cover Home, About, Filtration, Pharma and Careers;
the Food prototype is read at #24, but the application's Food page receives only
the direct-route/heading check. A prototype image cannot fill this gap.

**AAP / Opus:** final response #263 reports mobile responsiveness as verified.
The explicit browser viewports are 1440×900 (#125 and #256); there is no recorded
mobile state or mobile interaction test. Responsive source code may be correct,
but it is not equivalent to executing that check.

### 2. Capturing an image is not the same as consuming it

**FSB / Opus:** #127 confirms screenshots of all seven routes, including About.
The agent reads output images for five of the six prototype pages, plus the Work
page and mobile Home. It never reads the About screenshot, while #244 and the final
response #249 say every page was screenshot-compared. About does receive a useful DOM padding test
(#145–152), so it should not be described as completely unchecked. The limitation
is specifically the broader visual-comparison claim.

These gaps suggest deriving completion summaries from actual check receipts.
They do not justify penalizing a useful HTTP, DOM or source inspection for failing
to answer a different question that it never claimed to answer.

### 3. Test failures can be test mistakes, and corrected tests can regress

Both models have examples of useful correction, rather than blindly modifying
the application whenever a test fails:

| Case | Original test problem | Observed correction |
|---|---|---|
| FSB / Opus | #209 opens Content Types, then searches for a Policy Area; #210 times out. | #213 selects the second dropdown; #214 confirms a selected policy chip. |
| FSB / GLM | #224 leaves a dropdown open; it intercepts the next click (#225). | #227 dismisses it before continuing; #228 records filters and pagination. |
| AAP / Opus | The first `Learn More` is PREP, not NRP (#185–186). | The agent scopes the NRP link and observes `/nrp` (#189–190). |
| AAP / GLM | #264 has three FAIL lines, but two are an unjustified search count and a login test missing the required password. | The agent changes those tests; separately fixes the actual mobile-nav class bug (#274); #279 records 16/16 passing checks. |

There is also a **retention failure** in FSB / GLM: after the earlier correct
filter sequence, a newly written final smoke test (#300) again opens the wrong
dropdown. #301 shows the result count staying at 914 and no policy URL parameter.
The targeted check #303 then returns 71 results and the correct URL (#304).
Saving the validated check setup could avoid rediscovering the same distinction.

FSB / Opus supplies another useful counterexample: #214 prints both
`mobile nav toggle visible: false` and `ALL INTERACTIONS OK`. The agent rejects
the misleading success summary, repairs the missing responsive CSS and checks
the toggle and menu again (#217–220). A PASS-like log string is not itself an oracle.

The four selector/assertion examples occur in **2/3 traces for each model** in
this small selected sample. These are observed, generally recovered test-design
mistakes, not final website failure rates.

### 4. Tool and process management consume verification effort

All six traces contain a cleanup or clean-start command using broad `pkill`
followed by a recorded **exit code 144**: seven such returns in total. Exact
action/return IDs are saved in `audit_facts.json` and the findings. These interrupt
the intended sequence and require extra checks to establish server state.
The audit does not independently reproduce the OS-level cause of each interrupt.

Additional examples include a Playwright/browser-version mismatch in Kitten / GLM
(#212–216) and two incorrect `run-code` wrapper attempts in FSB / GLM (#280–286).
These are tool-interface failures, not observations that the generated site is
incorrect. A known working browser wrapper and an owned server handle/PID are
appropriate engineering interventions; they do not require a new reasoning model.

## Useful behavior to preserve and learn from

**FSB / Opus: visual concern → precise property → same-condition recheck.**
After a screenshot raises a padding concern, DOM measurement reports `titleLeft=0`
(#146). The agent finds a CSS shorthand overriding horizontal padding, edits it
(#149), and reruns the same measurement to obtain `titleLeft=40` (#152).

**Kitten / GLM: visual symptom → image readiness → repair → fresh evidence.**
After noticing missing logos, the agent checks their loading state (#228:
`complete=false`, `naturalWidth=0`). After removing lazy loading it repeats the
inspection (#233: `complete=true`, `naturalWidth=150`, height 96), then reads a
fresh component screenshot (#235). This supports the loading improvement even
without the archived pixels; it does not prove full visual fidelity.

**Kitten / GLM: missing asset → actual file check → relevant recheck.**
The Food hero inspection points to a nonexistent `-768x512` variant. #252 lists
the available files; #251 changes to the existing base image. New Food and
Filtration screenshots are captured and consumed (#257–264). The path fix has
direct code/file evidence; its visual quality remains unverified in this archive.

The GLM examples are substantial positive controls. In this sample GLM records
broader interaction checks on Kitten and AAP (39 and 16 assertions, respectively),
while Opus has useful targeted checks and several scope gaps. Assertion counts
and these examples do not establish that either model is generally stronger.

## Completed no-API experiment

We ran a small browser counterexample for the Kitten direct-navigation check.
The fixture has two versions: a correct industry-card link and the same card
pointing to the wrong destination. The Food route exists in both versions.

| Fixture variant | Direct navigation + correct Food heading | Click card + check destination and heading |
|---|---|---|
| Correct card | Pass | Pass |
| Broken card | Pass | Fail |

This demonstrates the intended-action blind spot. **It is a controlled minimal
HTML fixture, not a replay proving that the original card was broken, and not
a measured model improvement.** The real Chromium run and assertions passed.

```bash
/root/miniconda3/envs/swemm/bin/python \
  runs/vsv_eval/diagnosis_20261008/vision2web/navigation_probe.py
```

Code and results: [navigation_probe.py](../runs/vsv_eval/diagnosis_20261008/vision2web/navigation_probe.py),
[navigation_probe.json](../runs/vsv_eval/diagnosis_20261008/vision2web/navigation_probe.json).

## Minimal method hypotheses

1. **Require the intended operation and observable postcondition.** Keep a short
   check record: target, executed operation and returned observation. For visual
   judgments, additionally require an image actually consumed at the intended
   state. Compare the baseline against this constraint under equal budgets.
2. **Reuse a validated check after repair.** Preserve the successful setup,
   selector, viewport and postcondition, associate it with the relevant edit, and
   rerun it. Test whether this reduces repeated selector mistakes and skipped
   rechecks; do not count changing a bad test as repairing the application.
3. **Provide a working evidence tool.** Standardize capture/read receipts and
   owned server lifecycle. Keep the intervention small, then measure failed tool
   attempts and completed useful checks rather than screenshot count alone.

These are proposed interventions. The current experiment establishes one oracle
weakness only; it does not estimate end-to-end gains, six-metric improvements,
or RL reward effectiveness. Restore the selected runtime evidence before judging
visual correctness. Use fresh tasks for later confirmatory comparisons.

## Artifacts and validation

- [Findings with source hashes and original references](../runs/vsv_eval/diagnosis_20261008/vision2web/findings.json):
  20 concise records, including positive controls and environment failures.
- [Audit facts](../runs/vsv_eval/diagnosis_20261008/vision2web/audit_facts.json): image roles,
  image availability and exact interruption IDs for all six cases.
- Each `<task>/<model>/imported/run.json` preserves the canonical timeline;
  `candidate_windows.json` is the broad rule candidate set, not accepted episodes.
  `timeline_summary.txt` is a reading aid, not the source of truth.
- Every finding ID resolves to the imported source event and raw conversation
  index/block. All six prompt pairs match the local task materials.
- No historical commands, destructive cleanup, paid model requests, or changes to
  the evaluation formulas were performed. The only runtime experiment is the local
  browser fixture described above.
