# Benchmark runtimes and recorded-version restoration

The local runtime validation on 2026-10-08 uses three separate Conda environments.
Run commands from the repository root.

| Environment | Purpose | Runtime |
| --- | --- | --- |
| `3dcodebench` | Imported 3D trajectories and artifact acceptance | Python 3.12, official Blender 5.0.0 binary |
| `swemm` | SWE-MM import, recorded repository edits and acceptance | Python 3.12, Node.js 20, pinned SWE-bench source and Playwright |
| `gamedevbench` | Existing game task runtime | Existing Python 3.12 and Godot 4.4.1 environment |

Activate with `source /root/miniconda3/etc/profile.d/conda.sh`, followed by
`conda activate 3dcodebench`, `conda activate swemm`, or `conda activate gamedevbench`.
The existing `vision2web` environment is unchanged.

`3dcodebench` has a `blender` executable link and a persisted `BLENDER` variable.
`swemm` uses a freshly installed matching Chromium revision under
`/root/.cache/ms-playwright/` through `PLAYWRIGHT_BROWSERS_PATH`. The migrated
shared-storage browser cache failed its launch smoke test and is not used here.
Blender is under `/mnt/shared-storage-user/miyapeng/tools/blender-5.0.0/`; its
download was checked against the official release SHA-256 list.

For a new machine, the Python environment specifications are
`requirements/native/3dcodebench.yml` and `requirements/native/swemm.yml`.
Run `conda env create -f requirements/native/NAME.yml` from the repository root.
Install Blender, Chromium, Bubblewrap and required system libraries separately.
Full benchmark clones must exist at the paths in those specifications.
These specifications are readable dependency inputs; the exact installed Conda
and pip inventories are saved under
`runs/vsv_eval/runtime_validation_20261008/environments/`.

The SWE-MM environment imports the existing pinned evaluator at
`evaluate/swe_mm/upstream/`, not the newer full checkout. A Conda environment does
not replace each official SWE-MM instance container. The p5.js experiment below
is local diagnostic acceptance, not an official leaderboard reproduction.
Optional 3D similarity-model weights and full benchmark datasets were not installed.

## Restore selected file mutations

```bash
python scripts/vsv/restore_versions.py \
  --run-json path/to/trajectory/run.json \
  --plan path/to/restore_plan.json \
  --output-dir path/to/new-version-directory
```

A repository plan specifies an exact base snapshot and original mutation IDs:

```json
{
  "scope": "repository",
  "source_root": "/workspace/project",
  "base_snapshot": "/absolute/path/to/clean-base-commit",
  "event_ids": [16, 19, 22]
}
```

For complete generated files without a base snapshot, use `scope: recorded_files`
and omit `base_snapshot`. An unknown initial filesystem is not represented as an
empty historical version. The output starts after the first successful full write.

The implementation reuses call-ID pairing and the existing version-manifest/hash
format. It supports Claude `Edit`/`Write`, OpenHands `str_replace`/`create`, literal
quoted heredoc writes and simple file copies. It does not execute logged shell
commands. Failed calls remain in the original trajectory and the restoration audit,
but do not mutate reconstructed files. Unsupported operations fail explicitly.
Recorded full `old_content`/`new_content`, when present, must match exactly.
Source paths and replacement counts are validated; source files are never edited.

The plan defines a selected file scope, not a universal session replay. Review other
mutating actions when extending it. Reconstructing selected source files cannot
establish equivalence of all dependencies, browser state or original pixels.
`reconstruction.json` can be supplied as the existing acceptance binding's
`version_manifest`; the importer does not invent repair links or task criteria.

## Execute acceptance probes

`scripts/vsv/run_blender_probe.py` wraps the upstream renderer in a network-isolated
Bubblewrap namespace. The application is read-only; only the output directory and
private temporary storage are writable. It produces the four canonical views and
the upstream `render_log.json`. Check that report and image availability: Blender
can exit zero while the renderer records `ERR_EXEC`.

`scripts/swe_mm/probe_p5_webgl.py` is a p5.js-specific diagnostic probe. It takes a
restored version, separately installed npm dependencies and the original recorded
reproduction HTML. Inside isolation it generates required documentation data,
builds the library and test bundle, runs the recorded reproduction and the selected
RendererGL/Shader/light tests, and saves screenshots, results and browser errors.
Build caches use private storage; shared dependencies remain read-only.
The probe does not modify the recorded reproduction to force a pass.

Both use the existing `artifact_acceptance.replay_commands` executor. Sample
`runtime.json` files and results are under
`runs/vsv_eval/runtime_validation_20261008/`. The placeholder `runtime_probe` IDs in
these smoke runs label runtime observations, not a frozen scoring catalogue.
Actual scoring must use the existing model-prepared criterion/probe assignments.

New renders and browser captures are evaluator observations. They are stored beside
the version evidence and never inserted into the original trajectory as image reads.
They do not grant an agent credit for visual inspection or rechecking.

For experiment results, failure diagnostics and remaining inputs, see
[the runtime validation report](../reports/vsv_runtime_validation_20261008.md).
