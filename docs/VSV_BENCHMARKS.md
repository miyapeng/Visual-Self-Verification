# Evaluate multiple coding benchmarks

The shared evaluator supports Vision2Web, SWE-bench Multimodal, 3DCodeBench and
GameDevBench. Task import and acceptance execution vary; extraction, JudgeClient,
VC/CV/BDA, RS/CP, recheck alignment, metric arithmetic and result validation are shared.
No benchmark has its own scoring formula. Keep per-benchmark results separate when
comparing models; the task distributions and verification opportunities differ.

## Layout

- `src/multimodalcode/vsv_eval/native.py`: existing native transcript parsing,
  extracted from the Vision2Web presentation script. Its original imports still work.
- `benchmarks.py`: task/trajectory import, original source references and input audit.
- `artifact_acceptance.py`: fixed command probes for non-browser environments.
- `scripts/vsv/`: common import, task planning, extraction and evaluation entry points.
- `scripts/vision2web/`: existing web reconstruction and browser-specific tools.
- `evaluate/benchmarks/`: upstream benchmarks, kept separate from evaluator implementation.

## Prepare a case without an API

```bash
python scripts/vsv/prepare_benchmark.py \
  --benchmark swe-mm --task-id processing__p5.js-5970 \
  --trajectory path/to/processing__p5.js-5970.tar.gz --format openhands-sdk \
  --model claude-opus-4-8 --fetch-reference-images --output-dir runs/vsv_eval/my-case
```

Supported trajectory formats are native Claude JSONL, an OpenHands SDK conversation
archive, and the existing canonical `run.json`. The importer never executes logged
commands. The optional reference fetch only retrieves URLs recorded in the task;
it has a 16 MiB limit per image. Original observation pixels are extracted from
model-facing content. Task reference retrieval does not reconstruct observations.

For 3DCodeBench use `--benchmark 3dcodebench --format claude` and a selected
`claude_transcript.jsonl`. The initial user prompt supplies the exact task setting.
Reads of reference paths explicitly listed in that prompt carry `image_role=task_reference`.
For GameDevBench provide `--task-source evaluate/benchmarks/gamedevbench/tasks/task_0003`.
Only `task_config.json.instruction` enters the task catalogue; solution files,
validation code and final outcomes do not. An optional `--result-json` stores the
selected official outcome separately. Without `--trajectory`, preparation records
`missing_trajectory` and does not create a scoreable case.

Outputs are `task/`, `trajectory/run.json`, `candidates.json`, `input_audit.json`
and a `case.json` manifest entry. Rule candidates are explicitly unfiltered; their
count is not a count of self-verification rounds. Source member names, UUIDs,
original call IDs and canonical ordinals allow every event to be traced back.
The importer refuses to overwrite an existing output directory.

To sample the public OpenHands archive without downloading all results:

```bash
python scripts/vsv/sample_archive.py ARCHIVE_URL \
  --count 2 --max-mib 4 --output-dir runs/vsv_eval/archive-sample
```

It reads a bounded compressed prefix and saves only the selected conversation
members, their source names and hashes. No archive paths are extracted to disk.

## Freeze requirements, extract rounds, then score

Once a funded API is available:

```bash
python scripts/vsv/prepare_catalogue.py \
  --task-root runs/vsv_eval/my-case/task \
  --output-dir runs/vsv_eval/my-case/plan --primary-profile gemini3flash

python scripts/vsv/extract.py \
  --run-json runs/vsv_eval/my-case/trajectory/run.json \
  --output-dir runs/vsv_eval/my-case/extraction --primary-profile gemini3flash

python scripts/vsv/evaluate.py --manifest experiment.json \
  --output-dir runs/vsv_eval/my-evaluation --check-only

python scripts/vsv/evaluate.py --manifest experiment.json \
  --output-dir runs/vsv_eval/my-evaluation
```

`extract.py --offline` writes rule candidates without a Judge call.
`prepare_catalogue.py` uses the existing `plan_review` stage without forcing a
browser workflow onto a game or 3D task. Review replies remain cached and validated.
Use one frozen task catalogue across models. The shared configuration remains at
`configs/vision2web/vsv_protocol.json`; its historical location does not limit its use.

The experiment format is described in [VSV_SYSTEM.md](VSV_SYSTEM.md). A case now
accepts `benchmark`, one of `vision2web`, `swe-mm`, `3dcodebench`, `gamedevbench`.
Copy the prepared `case.json` entries into `cases`; paths are absolute. Use
`rounds_json` instead of `run_json` to reuse extracted rounds. Supplying `run_json`
lets the evaluator perform the same extraction itself.

## Non-browser repair acceptance

A linked repair needs the same audited before/after version manifest used by the
existing evaluator. The importer does not invent checkpoint bytes from a final
patch or final result. Supply an `acceptance_binding` with `fixture`,
`version_manifest` and `runtime`, as before. For command probes, runtime is:

```json
{
  "kind": "command",
  "command": ["/path/to/isolated-environment-launcher"],
  "cwd": ".",
  "code_subdir": "app",
  "timeout": 180,
  "probes": [
    {
      "probe_id": "render",
      "description": "Execute the generated factory and render four canonical views.",
      "args": ["{workspace}/app/object.py", "{output}"],
      "images": ["Image_005.png", "Image_015.png", "Image_025.png", "Image_035.png"],
      "reports": ["render_log.json"]
    }
  ]
}
```

This is a binding schema, not a claim that a universal launcher exists. Use the
actual benchmark environment and fixed probe commands:

| Benchmark | Probe implementation | Required setup |
| --- | --- | --- |
| 3DCodeBench | Existing `evaluate/benchmarks/3dcodebench/core/render.py` in Blender mode: `blender --background --python /path/to/core/render.py -- --blender-render --script {workspace}/app/object.py --output-dir {output}` | Blender 5.0 and the generated factory's dependencies; four canonical views plus `render_log.json` |
| GameDevBench | The selected task's pinned Godot validation and, for visual/temporal goals, a task-specific capture/interaction script | Godot 4.4.1, initial project assets, test scene and display where required |
| SWE-MM | Repository regression/reproduction commands in its pinned environment; browser capture when a visual criterion requires it | Exact base commit, patches, dependencies, fixtures and any required browser/services |

The launcher and probes are explicit executable inputs, not model-generated shell
commands. Use the benchmark's isolated environment for untrusted code. The executor
copies each audited version separately for each probe, passes no API credentials,
and retains stdout, stderr, return code, declared images and optional text reports.
It does not start a browser server for a command runtime. A missing render remains
missing, with its real diagnostic output; an exit code of zero is not a pass label.
A setup failure is not silently converted to an agent defect.

The existing plan model assigns fixed criterion IDs to supplied probe IDs and binds
current check targets to original repair events. It cannot modify probe commands.
The program validates coverage of the acceptance plan. Conditions that no supplied
probe can observe are reported as configuration gaps rather than fabricated passes.
RS/CP use the existing state Judge and formulas. Evaluator observations remain separate
from the agent's own rechecks: independent acceptance never grants VCS recheck credit.

Static views cannot demonstrate animation or gameplay. Provide a probe with actual
input actions and resulting observations for those criteria. Official aggregate
pass/fail results cannot stand in for intermediate checks, diagnoses or repairs.

## What is and is not validated

The 2026-10-08 sample is at `runs/vsv_eval/cross_benchmark_20261008/`. It contains five
real trajectories (three 3DCodeBench, two SWE-MM) and two GameDevBench task/results
pairs. API spending was zero. Import, pairing, image extraction and rule candidates
were run on these files. The six-metric command path was tested with real subprocess
execution and simulated Judge replies. Real benchmark scores and semantic Judge
accuracy are not established by those integration tests.

See [the sample audit](../reports/vsv_cross_benchmark_adaptation_20261008.md) for
image availability and the remaining runtime/checkpoint requirements.

## Runtime follow-up

The subsequent runtime work restored four real trajectories, ran Blender rendering
and p5.js browser checks, and revalidated two GameDevBench reference tasks. See
[VSV_RUNTIME_SETUP.md](VSV_RUNTIME_SETUP.md) and the
[runtime validation report](../reports/vsv_runtime_validation_20261008.md).
The report separates successful runtime execution from remaining Judge scoring,
missing game trajectories and differences from the original browser environment.
