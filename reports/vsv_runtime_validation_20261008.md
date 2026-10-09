# Cross-benchmark runtime validation — 2026-10-08

## Outcome

Created the `3dcodebench` and `swemm` Conda environments and revalidated the existing
`gamedevbench` environment. Restored selected files from four real trajectories,
executed 3D rendering and SWE-MM browser probes, and saved 20 new observation
images. No paid model API was called and no new six-metric scores were generated.

All new images are independent evaluator observations. The original trajectories,
including their original image receipts, remain unchanged. New renders do not
turn nonvisual agent behavior into visual self-verification.

Artifacts: `runs/vsv_eval/runtime_validation_20261008/`.
The machine-readable entry is `summary.json`; each case has a restoration plan,
version manifest, runtime definition, acceptance binding and execution evidence.

## Environments

| Environment | Verified components |
| --- | --- |
| `3dcodebench` | Python 3.12; upstream Python dependencies; Blender 5.0.0; network-isolated CPU rendering |
| `swemm` | Python 3.12; Node.js 20.20.2; pinned SWE-bench 5.0.0rc0 package; Playwright 1.60.0; Chromium 148.0.7778.96; isolated WebGL execution |
| `gamedevbench` | Existing Python 3.12; Godot 4.4.1; strict-confinement validation on two reference tasks |

`pip check` passed for all three environments. Exact Conda and pip inventories
are under `environments/`. The Blender archive matched the official SHA-256
`9de96e81432afba9c0a715c7233f1eff616705b75226dc5d0fa2708ddfb0e525`.

The migrated shared-storage Chromium cache had non-executable binaries and failed
its ICU startup check after permissions were restored. Browser system dependencies
were installed and the matching Chromium revision was freshly installed under
`/root/.cache/ms-playwright/`. Its Bubblewrap smoke test successfully created a
WebGL context. The `swemm` activation now selects that browser cache.

The existing `vision2web` Conda environment was not changed. System browser-library
installation is shared by environments. Optional 3D model weights, full datasets
and SWE-MM instance images were not downloaded.

## Real trajectory and runtime checks

| Sample | Restoration | Actual execution |
| --- | --- | --- |
| 3D / ArmChair-text | Three checkpoints spanning the full script write and subsequent file copy | Before copy: `ERR_EXEC`, no image; after copy: four views rendered |
| 3D / AquariumTank-text | One checkpoint from the successful quoted heredoc; failed Write #3 excluded from mutations | Four views rendered |
| 3D / ArmChair-image | One checkpoint from recorded Write #11 | Four views rendered |
| SWE-MM / p5.js-5970 | Twelve checkpoints around five replacements and creation of the reproduction page | Before/after reproduction plus three browser-test filters; eight images saved |
| GameDevBench / task_0003 | Reference solution, not an agent trajectory | Strict validation passed: Player scene structure |
| GameDevBench / task_0004 | Reference solution, not an agent trajectory | Strict validation passed: Missile scene structure |

3D smoke rendering used the upstream camera convention with 256-pixel images and
16 samples. These are diagnostic settings, not a reproduction of default official
image-quality scores. The sampled prompts prohibit agent rendering; evaluator
renders must remain outside the agent's historical observation stream.

GameDevBench's reference directories lack `task_config.json`; the upstream runner
logged that fact and used its headless path. Both supplied validation scenes passed.
This verifies those headless runtime cases, not gameplay, temporal visual quality,
or self-verification behavior without a solver transcript.

## SWE-MM reconstruction and findings

The p5.js base commit is `69702de9f245b2b960fa24f127e5e3b2b3d21ecc`, independently
present in the local task metadata and the trajectory. All five replacements
(#16, #19, #22, #25, #28) match the tool returns' complete before/after file contents.
The created reproduction page (#46) matches its recorded `new_content` exactly.
See `processing__p5.js-5970/recorded_content_audit.json`.

The probe uses the original reproduction HTML unchanged and the same three test
filters recorded in the trajectory. It runs each version in a private namespace
without network access or host credentials. Results are local diagnostic evidence;
the exact original container and browser configuration were not recovered.

Final execution records are in `processing__p5.js-5970/acceptance-v5/`:

| Check | Before edits (V0) | After selected edits (V11) |
| --- | --- | --- |
| Reproduction center pixel | `[32, 32, 125]` | `[32, 32, 125]` |
| Disabled vertex attribute locations | `[]` | `[2]` |
| Original reproduction `pass` field | false | false |
| RendererGL tests | 80 passed, 0 failed | 80 passed, 0 failed |
| Shader filter | 30 passed, 1 failed | 30 passed, 1 failed |
| Light filter | 43 passed, 0 failed | 43 passed, 0 failed |

The reproduction asserts a blue-channel value above 200; actual blue is 125.
The original trajectory also reports that value and `pass:false`, and the agent
explicitly discusses lighting and the disabled attribute. Therefore a blanket
conversion of this field to a failed repair label would ignore what the assertion
tests. The original test was not changed to manufacture a pass. The attribute
behavior changes, while both local versions visibly render a blue square; these
observations alone do not establish improved rendering on the originally affected
device or driver.

Both local versions fail `_friendlyFileLoadError is called` in the Shader filter.
The original trajectory reports 31 Shader passes. Both local runs also report
`Identifier 'expect' has already been declared` page errors; the Shader filter
additionally reports `ReadableStream` errors. These are retained in `probe.json`.
The selected tests show no additional failed test after the edit, but the mismatch
and page errors prevent a claim of exact historical-environment equivalence or
unqualified clean regression coverage. Their root causes are not yet established.

Earlier `acceptance/` and `acceptance-v2` through `acceptance-v4` directories retain
setup/debug failures. They are not the final runtime result. Fixes included a
writable private Babel cache, the required upstream documentation-data build,
browser dependencies and installation, and browser-side JSON conversion of Mocha
Date fields before saving results.

## Implementation and validation

- `vsv_eval/version_restore.py` and `scripts/vsv/restore_versions.py` restore
  explicitly selected mutations using original call/return IDs and existing
  version-manifest/hash structures. They reject unsafe paths, missing or ambiguous
  preimages and mismatches with recorded full-file contents. Failed writes do not
  create successful versions. Historical shell commands are never executed.
- `scripts/vsv/run_blender_probe.py` executes the upstream renderer in isolation.
- `scripts/swe_mm/probe_p5_webgl.py` executes the recorded reproduction and original
  tests on copies of restored versions, keeping structured results and screenshots.
- Runtime outputs use the existing `artifact_acceptance.replay_commands` interface.
  All four acceptance bindings passed source-hash and artifact-integrity validation.
- The VSV suite reports **310 passed, 8 skipped**, including eight new restoration
  tests. `git diff --check` passed. No scoring formula was changed.

## Remaining work

The prepared four-case `experiment.json` still fails `--check-only` on missing
frozen task catalogues, as expected with no funded Judge API. Model-based catalogue
preparation, round annotation, repair-target/probe assignment and semantic scoring
remain to be run. The runtime smoke IDs do not stand in for model-reviewed task
criteria. Existing original p5.js images need no restoration; newly collected
acceptance images have separate provenance.

GameDevBench still needs native solver trajectories for behavioral metrics. The
reference validations cannot replace those trajectories. Other SWE-MM repositories
need their own task-specific runtime and probes; one working p5.js diagnostic is
not a universal SWE-MM environment.

Setup and commands: [VSV_RUNTIME_SETUP.md](../docs/VSV_RUNTIME_SETUP.md).
