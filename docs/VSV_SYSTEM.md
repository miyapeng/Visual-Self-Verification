# Run the scoring system

`scripts/vsv/evaluate.py` (also available at the original
`scripts/vision2web/evaluate_vsv.py` path) runs the existing six-metric evaluator from
one experiment manifest. It validates inputs, extracts rounds when requested,
evaluates VC/CV/BDA, runs the existing version acceptance stage for RS/CP, and
joins the results to compute VCS. It writes a comparison after each case and
continues to other cases after a case fails.

Protocol `requirement-level-20261009` keeps six metrics and records its version
with the frozen experiment. It never infers missing screenshots from agent claims
or generates unverified code versions. See the current protocol for metric definitions. Prepare task criteria
and runtime bindings with the existing `score_vsv.py --prepare-plan` workflow.
Use the same frozen catalogue for models evaluated on the same task.

The current revision adds requirement-type coverage, a visual defect audit in the
existing OBSERVATION call, and a joint view of local repair and regression on each
recorded version transition. See [the protocol](self_verification_evaluation.md#finding-oriented-breakdowns)
for their denominators. These are evidence-based diagnostics, not predefined findings.
Existing frozen catalogues without `requirement_types` remain readable but have no
complete type breakdown. Their types are not guessed from agent behavior. Prepare a
new reviewed catalogue to compare those breakdowns, shared by every model on that task.
Older observation responses lack the defect audit and require fresh judgments;
use a fresh experiment output directory instead of overwriting earlier results.

## Manifest

Paths are relative to the manifest file. Paths *inside* existing trajectory and
state files retain their existing interpretation.

```json
{
  "config": "../../configs/vision2web/vsv_protocol.json",
  "primary_profile": "gemini3flash",
  "workers": 3,
  "cases": [
    {
      "id": "task-model",
      "model": "Coding model name",
      "task_root": "task",
      "catalogue": "plan/catalogue.json",
      "rounds_json": "extraction/verification_rounds.json",
      "state_spec": "plan/state_spec.json"
    }
  ]
}
```

- Supply `run_json` instead of `rounds_json` to run extraction automatically.
- Supply `repair_results` instead of `state_spec` to validate and reuse existing
  acceptance evidence. Both may be omitted when no repair links exist.
- Optional `check_results` revalidates saved judgments rather than making new
  check calls. It requires their original `rounds_json` and catalogue. The output
  explicitly identifies reused checks; their original model provenance remains
  in the linked score file. This is for reproduction, not a fresh Judge comparison.
- Optional `reconstructed_images` supplies the existing reconstruction sidecar.
  Reconstruction remains a separate data preparation step. Reconstructed pixels
  are explicitly marked and are not claimed to be original observations.
- Optional `reference_map` supplies existing episode-to-reference associations.
- `allow_draft: true` explicitly permits a pilot catalogue. It does not make the
  output a validated benchmark result. The original protocol status is retained.
- Cases execute sequentially; `workers` controls the existing within-case
  concurrency. `port` defaults to 18951 for the acceptance runner.

## Commands

```bash
# No model calls or application execution. Lists input gaps for every case.
python scripts/vision2web/evaluate_vsv.py \
  --manifest path/to/experiment.json --output-dir runs/vsv_eval/experiment \
  --check-only

# Credentials are read by the existing JudgeClient from the environment.
# Run this identical command again after a transient API failure.
python scripts/vision2web/evaluate_vsv.py \
  --manifest path/to/experiment.json --output-dir runs/vsv_eval/experiment
```

The protocol version, manifest, input hashes and configuration hash are frozen in `experiment.json`
under the output directory. Changed inputs require a fresh output directory.
Unchanged model requests use the existing content-addressed cache. The runner
adds no provider, retry loop, automatic model fallback or human review step.
An interrupted acceptance reconstruction still follows the existing runner's
validation rules; the system does not delete incomplete work to retry it.

## Outputs and completion

| File | Contents |
| --- | --- |
| `summary.json` | Every case, raw metric records, denominators, assessment status and result paths |
| `comparison.csv`, `comparison.md` | Six metrics, evidence-modality groups, requirement coverage, visual-defect recognition, conditional repair and regression breakdowns |
| `model_calls.json` | Actual requests and cache hits for this invocation, grouped by stage |
| `<case>/preflight.json` | Input problems, missing observations and unbound repair groups |
| `<case>/final/scores.json` | Existing protocol result with validated source and response references |

The runner rejects missing, duplicate, reordered or pending rounds and missing
repair acceptance groups before declaring completion. It distinguishes:

- `completed`: every required stage finished; applicable metrics have scores.
- `completed_with_unresolved_evidence`: stages finished but some evidence labels
  are unknown. The command exits with code 2, not a misleading success status.
- `blocked`: required input evidence or acceptance bindings are absent.
- `failed`: a validation, model or execution call failed.
- `ready`: input preflight passed; no evaluation was run in this invocation.

Exit code 0 means all cases are completed (or ready in `--check-only` mode).
Exit code 2 means at least one case requires attention. Invalid manifests raise
before execution. Unknown labels are retained with existing bounds and counts;
subset percentages are not displayed as full scores. `not_applicable` is used
for an empty metric denominator. BDA averages diagnosis accuracy over the actual classes present in that task;
a one-class task uses that class and reports its sample count. An empty metric
denominator remains not applicable, rather than receiving an invented score.

An available API alone is insufficient for archived data with missing images
or runnable versions. Preflight identifies these gaps before check scoring.

## In-session model annotation without an API

For a small pilot, use the existing JudgeClient with an `in-session` profile:

```json
{"provider": "in-session", "model": "codex-in-session", "base_url": ""}
```

Select this profile with `primary_profile` in the experiment manifest. No endpoint
or API key is used. Each uncached judgment exports `<hash>.request.json` in the
existing stage cache and stops with `In-session annotation required`. Read that
exact packet and view its image attachments before supplying
`<hash>.annotation.json` alongside it:

```json
{
  "request_sha256": "copy from the request",
  "model": "codex-in-session",
  "parsed": {"targets": []}
}
```

`parsed` must contain the actual annotation in the stage's requested format; the
empty example is not a default decision. Rerun the same command to validate it
and continue. The normal ID, evidence, version and metric validators still run.
Changed inputs generate a different request hash. Accepted answers use the
existing cache; use a fresh output directory for a new annotation run.

This is a file handoff to a model active in a conversation, not a way for a Python
process to invoke Codex without an API. Response records identify the provider,
annotation path and hash, zero HTTP requests, and unguaranteed context isolation.
The model may already have seen later outcomes in the conversation. Report these
results as an in-session pilot, not an independent or calibrated Judge evaluation.
`codex-in-session` is an attribution label, not a pinned model version. Retain it
when reporting or reusing responses. Missing evidence is never filled with default
pass/fail labels, and restored evaluator images do not count as agent image inputs.
When extraction is requested, its image and repair-link checks necessarily run
after extraction, before any metric call. Preflight validates recorded bindings;
it cannot guarantee that a web server or external service will work at runtime.

## Automatic repair acceptance preparation

A case can now supply `acceptance_binding` instead of `state_spec` or
`repair_results`. The binding contains only the runtime and audited code inputs:

```json
{
  "fixture": "/path/to/fixture",
  "version_manifest": "/path/to/versions.json",
  "runtime": {
    "command": ["node", "app/server.js"],
    "cwd": ".",
    "code_subdir": "app"
  }
}
```

The fixture contains `trajectory/run.json`; its hash must match the extracted
rounds. `version_manifest` uses the existing version manifest format, with exact
checkpoints immediately before each linked edit group and after its final edit.
For every version, byte hashes and original event boundaries are validated before
any paid evaluation. Missing checkpoints are input errors, not inferred versions.

With this binding, the runner executes:

1. Judge the recorded checks using the current target IDs.
2. Construct real before/after transitions from original edit IDs and exported versions.
3. Use the existing `plan_review` client to bind those targets to acceptance criteria
   and executable workflows. The task catalogue is immutable in this call.
4. Run the existing RS/CP acceptance executor and validate its recorded evidence.
5. Join check, repair and recheck records; compute and export all six metrics.

Repair-only criteria may be added when a discovered defect has no equivalent fixed
criterion. They must be used by an actual repair group and never enlarge VC or CP's
fixed denominator. Each current target receives an alias decision; `[]` explicitly
means this edit group does not address it. This adds no RS sample, and cannot be
mistaken for an unassessed fix in VCS. Unknown, duplicated or omitted IDs are errors.

Some recorded repair groups contain other edits between their endpoints. These
remain separate in `context_edit_event_ids`, not in `repair_event_ids`. The complete
version interval is evaluated; CP and post-repair acceptance are observational,
not proof that a particular edit independently caused an outcome. Unrecorded
intervening mutations are rejected. Shell mutations require replayed manifest evidence.

Completed prepared plans resume without another planning call, provided current
labels, source versions and criteria are unchanged. Model selection and planning
budgets use the existing configuration and `--primary-profile` equivalent in the
manifest. No model fallback or manual target-ID patching is needed.

### Prepare archived Vision2Web cases

For the recorded Vite layouts used by the current matched-model data:

```bash
python scripts/vision2web/prepare_vsv_acceptance.py \
  --manifest runs/vsv_eval/scoring_system_20261006/matched_models.json \
  --output-dir runs/vsv_eval/my_acceptance_inputs \
  --image vision2web-sandbox:latest
```

This preparation is read-only with respect to the original dataset and calls no
model. It creates bindings, checkpoint requests, a fresh experiment manifest and
`replay_commands.json` with argument arrays for the existing reconstruction CLI.
It does not execute those commands. Other project layouts can supply their own
explicit runtime binding to the same runner.

`reconstruct_leaderboard_images.py --rounds-json ... --acceptance-binding ...`
exports the requested checkpoint bytes alongside its existing image recovery.
Omit `--execute` to scan only. Existing image sidecars are preserved. Source code
is exported without dependency trees; the acceptance launcher installs dependencies
inside an isolated container, preserving the audited host checkpoint bytes. It
uses the recorded startup script when present. Containers are stopped and retained
for inspection; no automatic deletion is introduced.

Recorded deletion commands and unsupported shell syntax remain subject to the
existing review checks. A blocked reconstruction is not a completed version export.
After checkpoint export and image recovery, run the prepared `experiment.json`
through `evaluate_vsv.py`. Use `--check-only` first to verify all required files.

## Additional benchmarks

See [VSV_BENCHMARKS.md](VSV_BENCHMARKS.md) for SWE-MM, 3DCodeBench and GameDevBench
imports and command-based acceptance. A case may specify `benchmark`; the default
is `vision2web`. The six metrics and their evidence validation are shared.
