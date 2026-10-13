# Self-verification evaluation

Protocol: `evidence-diagnosis-repair-20261013`.

The evaluation separates three questions: whether the agent acquires relevant
verification evidence, whether it interprets that evidence correctly, and whether
its attempted corrections improve the implementation without regressions.
Models label evidence and cite original IDs; Python computes every rate.

## 1. Fix the task specification

Prepare one catalogue of independently testable task requirements from the task
and reference material, before inspecting agent trajectories. Freeze its IDs,
criteria, source references and acceptance tolerances across models. Resolve
source conflicts in this specification before evaluation. Requirements should
represent independently failing conditions, rather than individual decorative
details or whole pages containing unrelated behaviors.

Visual and interactive requirement types may overlap. They describe requirements,
not the modality of the evidence used by an agent.

## 2. Establish the analysis population

The boundary annotator identifies the earliest complete implementation attempt
from the task and original trajectory. Completeness means an attempt at the whole
deliverable, not successful execution or task correctness. For a maintenance task,
it is the first completed proposed change to the supplied project.

A single original event ID defines the boundary. The program retains earlier
events as context and scores only checks whose actions follow it. A round crossing
that boundary is rejected for re-extraction. An absent initial implementation is
reported separately from a complete implementation with no subsequent checking.
The boundary response, input hash and original IDs are retained in `scope.json`.

The existing extractor pairs native tool IDs, traces image receipt to capture,
keeps failures and retries, and links actual edits to later checks. A local round
contains evidence and contemporaneous judgments at one implementation state.
Actual revisions link these assessments across versions. Shared captures and
edits are referenced once; intervening unrelated events are not inserted into a
round. Capture-time state, not image-read time, identifies the implementation
shown by an image.

## 3. Label attempted checks and their validity

For each judgment-time prefix, the acquisition stage identifies actual checks,
including failed attempts and legitimate checks outside the catalogue. It maps
scope to a requirement as `full`, `partial`, or `none`. The separate validity stage
labels method suitability and evidence sufficiency. The internal stage IDs remain
`*_vc` and `*_cv`; CV is an intermediate label, not a reported metric.

A valid full check requires both validity labels to be true. Observing an actual
application failure can be a full valid check. A failed capture without sufficient
feedback cannot establish coverage. A partial check receives no fractional credit.
Context can supply the setup of a longer test; splitting a plan into rounds does
not require repeating that setup.

## 4. Independently establish diagnosis opportunities

The observation evaluator receives the entire fixed catalogue, original tool
actions/returns and reference/observation images at the current cutoff. It receives
neither agent judgments nor the acquisition evaluator's target choices. It returns
one row per requirement:

```json
{
  "targets": [
    {
      "target_id": "requirement-id",
      "actual_state": "fail",
      "actual_issue": {"object": "required element", "symptom": "not visible"},
      "evidence_ids": [284]
    }
  ]
}
```

States are `pass`, `fail`, and `not_observable`. The latter means the current
inspection cannot decide that requirement; it is not a failing agent judgment.
For example, a screenshot of a closed menu cannot decide whether clicking it
opens the required submenu. Such a requirement still belongs to the VC denominator.
Missing archived image files instead block evaluation and must be recovered;
missing bytes must not be relabeled as an unobserved requirement.

Every decidable condition becomes a diagnosis opportunity, including unmentioned
failures and unmentioned satisfied conditions. The verdict stage then maps the
original agent responses to `pass`, `fail`, `absent`, or `uncertain`, citing response
IDs. Failure judgments must match the observed object and symptom, not necessarily
the cause. Missing and inconclusive judgments receive zero credit on decidable
conditions. Later edits and independent acceptance results cannot support an
original judgment.

Modality is computed per condition from its cited evidence: an actual image input
makes that condition visual; otherwise it is textual. Thus a screenshot and a
build result in the same round can have different modalities. Filenames and
reference images are not agent observations. Independently discovered conditions
do not create new attempted checks or inflate VC.

The final round record has two small label tables: `targets` for attempted checks
and `assessments` for independently decidable catalogue conditions. Raw events
remain in the source trajectory. These tables have different denominators and
must not be implicitly substituted for each other.

## 5. Execute shared acceptance before and after repair

Use the existing audited version reconstruction and runtime adapters. Link repair
targets using recorded findings, real edits and task requirements, not temporal
succession alone. Freeze one acceptance procedure and apply it to both versions.
Executable assertions decide exact conditions; the acceptance model judges
semantic conditions from the resulting evidence.

Each version has one acceptance table. RSR and CPR read the same labels, with
identical tolerances and evidence. They cannot independently relabel the same
state. Batching follows existing workflow evidence; an intermediate version is
accepted once when it is shared by consecutive transitions. Exact assertions
require no semantic model call.

A repair with an unknown baseline cannot be credited merely because its output
passes. An evaluator timeout is not a confirmed application defect. Incomplete
acceptance is an evaluation input/execution problem and prevents publication of
a complete result. A later agent recheck is useful provenance but is not required
for independent acceptance to establish repair success.

## 6. Compute six rates

All rates are percentages. Each report includes successes, failures and opportunity
counts; zero opportunities are N/A, not a fabricated zero or perfect score.

| Metric | Numerator | Denominator |
| --- | --- | --- |
| VTR — Verification Trigger Rate | Trajectories with an executed post-boundary self-check, including failed attempts | Trajectories with a complete initial implementation |
| VC — Verification Coverage | Unique requirements receiving a full valid check | Fixed catalogue requirements |
| EDR — Error Detection Rate | Correctly identified observable defects | Decidable violated assessment–requirement pairs |
| CAR — Correct Acceptance Rate | Correct affirmative judgments | Decidable satisfied assessment–requirement pairs |
| RSR — Repair Success Rate | Confirmed failing repair targets that pass after editing | Attempted repair transition–target pairs with a failing baseline |
| CPR — Correctness Preservation Rate | Passing non-target requirements that remain passing | Passing-before non-target requirements on those repair transitions |

EDR and CAR also have visual/text breakdowns. VC has visual/interactive requirement
breakdowns when the catalogue contains those classifications. Diagnostic records
retain omitted judgments, false alarms, missed defects, unattempted diagnosed
repairs and joint local-fix/regression outcomes. These are analyses of the same
labels, not additional model-scored headline metrics.

Compute run scores first, average repetitions within each task/model, then average
tasks with equal weight. The runner compares models on their common completed
task set and lists excluded tasks. Use explicit `task_id` values in the manifest
when task directories do not have unique benchmark-specific names. Report support
counts and exclusion counts with every comparison. These trajectory-level
comparisons describe encountered opportunities; matched checkpoint experiments
are needed for controlled capability comparisons.

## Reproducibility and validation

The run freezes protocol/configuration/input hashes and retains raw responses,
request hashes, evidence provenance and model-call counts. Changed inputs require
a new output directory. Older VC/CV/BDA/RS/CP/VCS scores are not converted into the
new protocol by renaming fields: the independent observation audit and shared
acceptance require new labels. Original trajectories, restored images and audited
versions can still be reused.

Reconstructed observations are explicitly labeled; image equivalence to historical
inputs is not assumed. Text-proxy estimates remain a separate exploratory mode
and are not accepted as benchmark scores. Offline preparation and controlled model
fixtures test program behavior but do not establish judge accuracy. A research
release still needs a fixed judge configuration, a calibration set with executable
or independently established labels, and an audit of boundary/condition labels.

The design adopts label/aggregation separation and explicit run failures from
[Inspect's scoring workflow](https://inspect.aisi.org.uk/scoring.html), and shares
before/after acceptance evidence in the spirit of
[SWE-bench's execution harness](https://www.swebench.com/SWE-bench/api/harness/).
Neither is an added dependency. Their guarantees do not establish the correctness
of multimodal semantic judgments in this project.

See [the runner guide](VSV_SYSTEM.md) for commands, configuration and outputs.
