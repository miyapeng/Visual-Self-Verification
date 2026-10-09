# Acceptance integration completion

This change fills the version-binding and current-target-binding gaps in the
Vision2Web runner. It makes no paid API calls and does not change the six scoring
formulas. Model and replay quality remain to be established by live experiments.

## Implemented path

`acceptance_binding` -> validate source/checkpoint bytes -> score current checks ->
construct version transitions -> model-reviewed target aliases and acceptance plan ->
existing RS/CP executor -> validated join -> six metrics and comparison files.

`preparation.py` derives transitions from original edit groups and existing version
manifests. `catalogue.py` extends the existing plan-review call to include constrained
repair bindings, preserving every frozen task criterion. The response and its input
are retained; compiled aliases are checked against that original response during
execution and result loading. Supplementary repair goals do not affect VC/CP.

Every current target has an explicit mapping decision. Empty mappings mean the repair
did not address that target; they do not create failed RS samples or unresolved repair
links. Different current IDs no longer require hand-editing an older alias dictionary.

Intervening edits are saved separately as context. They are not assigned to the linked
repair. Before/after acceptance describes the observed version interval without causal
claims about isolated edits. The manifest must account for actual shell mutations too.

The existing archive worker now exports requested code bytes in the existing version
manifest format. This includes checkpoints after the last screenshot, even in traces
with no image inputs. Missing exports, byte mismatches, stale sources, incomplete target
bindings and changed frozen criteria are rejected. Existing images remain untouched.

## Validation

The VSV suite passed: **293 passed, 8 skipped**. External model API requests: **0**.

An offline integration test executes the real experiment runner, check-stage assembly,
repair planning, version-manifest loading, acceptance validation, stored-response join,
score arithmetic and comparison writer. Only model responses and browser observations
are simulated. Its known synthetic successful chain produces all six expected scores;
these are test results, not benchmark measurements. The same test confirms prepared-plan
reuse without another planning call.

Additional tests cover missing exact checkpoints, byte tampering, stale/foreign target
IDs, missing mapping decisions, intervening shell edits, export without dependency trees,
nested application assets, complete checkpoint publication and retained deletion-review
blocks. Tests also verify that an explicitly unaddressed defect remains a failed closure
without entering the RS denominator.

## Real-data preparation

Generated directory: `runs/vsv_eval/acceptance_prepared_20261008/`.

- Eight existing cases, four models on two shared tasks.
- Eighteen recorded repair groups; 36 requested before/after checkpoints.
- Per-case fixture, runtime binding and checkpoint request files.
- `experiment.json`: fresh judging with automatic acceptance planning enabled.
- `replay_commands.json`: reconstruction argument arrays, not executed commands.
- `boundary_audit.json`: source boundaries and shell mutations requiring replay evidence.

All eight cases were scanned without executing recorded shell commands. Their scan
inventories are under each case's `replay/` directory. Each currently reports at least
one command or startup script requiring review; some also report unresolved image inputs.
The source data, previous judgments and recovered images were not deleted or overwritten.
No checkpoint bytes or new scores for these eight cases are claimed in this report.

## What remains for an experiment

Review and execute the recorded reconstruction commands to materialize the requested
code checkpoints and missing observations, then supply a funded model API. This is data
preparation and live validation, not another manual target-binding implementation step.
The runtime launcher checks recorded startup/package scripts before execution and
preserves checkpoint bytes; it does not bypass the existing destructive-command review.

Use the prepared manifest with `evaluate_vsv.py --check-only` before spending model
budget. The current missing version manifests will be reported as blocked input, not
silently replaced with final code. Once inputs are ready, the same entry point performs
the planning, acceptance and six-score comparison. Live provider behavior and semantic
accuracy have not been validated by the offline tests.
