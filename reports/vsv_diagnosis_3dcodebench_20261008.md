# 3DCodeBench trajectory diagnosis: six matched text cases

## Scope and reproducibility

This is an evidence-linked pilot diagnosis by Codex in the current session, not an independent gold annotation, a six-metric evaluation, or an estimate of a model-wide failure rate. No paid API was called. No original files or scoring formulas were changed.

Source: [YipengGao/3DCode agent logs](https://huggingface.co/datasets/YipengGao/3DCode/tree/0db485b63a2991231f0fad6c6e0dc01bcbbced1e/3DCodeBench_ModelLogs/agent_logs), pinned to `0db485b63a2991231f0fad6c6e0dc01bcbbced1e`.

The inspected text and image log directories contain Claude, Gemini, GPT, antigravity and family aliases; no GLM, DeepSeek or Qwen log directory was available. We selected `claude-opus-4-8` and `gemini-3-flash-preview`, treating Gemini as a lower-cost comparison candidate, not an established weaker model. Each text directory contains 212 task directories. Before reading their transcripts, we selected the first three common task IDs in alphabetical order: AgaveMonocot, AquariumTank, ArmChair, all seed 0. The download is 12 files / 262,269 bytes, with source URLs and SHA-256 hashes in the manifest.

Artifacts, relative to the repository:

- `runs/vsv_eval/diagnosis_20261008/3dcodebench/source_manifest.json`: immutable source revision, selection rule, URLs and file hashes.
- `.../sample_inventory.json`: six cases and exact test-call IDs/lines.
- `.../findings.json`: eleven findings with original IDs, evidence paths, type and intervention hypothesis.
- `.../experiment/replay_results.json`: all eight Gemini test checkpoints reconstructed and executed locally.
- `.../experiment/exit_status_results.json`: four-condition feedback experiment.

## What can be compared

The object descriptions match across the two models. The harnesses and complete prompts do not: Claude uses Claude Code, Gemini uses its own CLI with `update_topic`; Claude receives a 900-second budget, Gemini 600 seconds. Gemini also explicitly requests untextured geometry, while Claude's prompt does not contain that sentence. The runs date from different months. These are paired task case studies, not a controlled backbone comparison.

**All six prompts prohibit rendering.** All six logs have zero generated-image inputs. This is compliant with the task and cannot establish that either model lacks spontaneous visual self-verification. Offline restored/rendered images must never be counted as images the original agent saw.

## Case-level observations

| Task | Claude Opus 4.8 | Gemini 3 Flash |
|---|---|---|
| AgaveMonocot | Write is disabled; switches to Bash heredoc. One Blender check returns a mesh with 12,166 vertices and 23,376 polygons. | Two Blender calls: the first reported BMesh index error is repaired using vertex references. Local replay reproduces error then a clean 3,900-vertex mesh. |
| AquariumTank | Write is disabled; switches to Bash heredoc. One Blender check returns a 1,444-vertex mesh. | Three Blender calls: local replay reproduces removed `shadow_method` failure, then `to_mesh()`/`from_mesh()` TypeError, then a clean 314-vertex mesh. |
| ArmChair | Writes the script to the parent folder. First Blender call cannot find it. Locates and copies it into the task folder; second call succeeds and reports 1,848 faces. | Three Blender checks around added legs and revised proportions. All three reconstructed versions execute and contain a mesh. There is no recorded visual inspection of these refinements. |

Mesh counts are diagnostic output, not shape-quality scores; modifiers and modeling choices make raw counts unsuitable for ranking these results. Across this sample Claude makes four Blender test calls and Gemini eight. All six trajectories eventually create the completion sentinel. The larger Gemini call count is not itself evidence of worse or better verification.

## Findings and minimal interventions

### 1. The tool contract can generate avoidable failures

Claude Agave and Aquarium are instructed to use Write, but the actual tool replies that Write is disabled. Both recover using Bash heredocs. This is an environment/prompt mismatch, with a useful positive recovery example, not a failure to understand visual evidence.

Evidence: Agave lines 10–15 (`toolu_018mam3rfpQFqYNntPS5wpAb` and `toolu_01BP4b6v4GfTkTav2YTHQHg8`); Aquarium lines 6–11 (`toolu_01J8g8L2NQWrnk5wMZgCsUtq`, `toolu_01WHqSgauRg5afhdQfaQZbMC`).

Minimal intervention: expose the tools actually enabled and one working file-write example. Evaluate reductions in failed setup calls and time to the first meaningful check; do not claim a visual-reasoning improvement.

### 2. A success flag is not an executable correctness verdict

Claude ArmChair's failed file lookup returns `is_error: false`. All eight Gemini Blender tool results say `status: success` while omitting stdout; later Gemini topic messages report actual execution errors. We must not convert either transport-level field into test success.

A controlled local command experiment makes this concrete. For the restored initial Gemini Agave script:

| Fixed local command | Script result | Process/pipeline status |
|---|---|---|
| Recorded template: Blender `--python ... 2>&1 \| tail -15` | BMesh `IndexError` | Blender 0, tail 0 |
| Add only `set -o pipefail` | Same `IndexError` | Pipeline 0 |
| Add `--python-exit-code 1` before `--python`, plus `set -o pipefail` | Same `IndexError` | Pipeline 1 |
| Strict command on the repaired Agave script (positive control) | Clean execution | Pipeline 0 |

Pipefail alone cannot solve this case because Blender itself returns zero. The positive control confirms that the strict command does not simply fail every script. Preserve the textual error and scene facts as well as the return code. The agents here did respond to failures; this experiment improves the machine-readable signal, and does not show that an agent would perform better after the change.

### 3. API repairs can be correct even when the archive omits observations

Gemini Agave's summary identifies an outdated BMesh index table and its subsequent replacement uses vertex references. Gemini Aquarium identifies the removed material attribute, then the `None` return from `BMesh.to_mesh`. All three exception types are reproduced locally from the exact versions and all final versions execute successfully.

This is positive evidence for local diagnosis and repair, with an archive limitation: all eight original Blender stdout fields are absent. The local rerun is **new evaluator evidence**, not an exact recovered original tool output. We cannot conclude that Gemini itself lacked the output merely because its export lacks it.

Minimal intervention: preserve call-linked stdout/stderr and a small scene report. For a method experiment, compare an error-linked minimal repair with broad rewrites using the same failing state and budget. The Aquarium replacement also mentions improving cactus/frame geometry, but we have not shown that this extra scope caused its second error.

### 4. Runtime and mesh checks leave shape requirements untested

Every sampled trajectory executes Blender. The runs establish executability and mesh presence; they do not directly observe upholstery, plant silhouette, transparent geometry or spatial proportions. Gemini ArmChair explicitly revises proportions and reruns execution checks, a good regression-check behavior, but its visual claims remain based on code/reasoning in this record.

This evidence ceiling is imposed in part by the no-render rule. A useful next experiment is a separate render-permitted continuation: provide one fixed rendering command and image-read operation after the same checkpoint, then measure whether it identifies and repairs independently annotated shape defects. Compare against an equal-budget text-only scene report. Do not retrofit that experimental condition into the original benchmark scores.

### 5. A cheap path check can remove a recovered implementation error

Claude ArmChair's original Edit explicitly writes to the parent directory (line 6); its final explanation says the Write tool placed it there, but the recorded action was Edit with that explicit path. The first runtime check fails, and the agent finds, copies and retests it correctly (lines 10–22).

Minimal intervention: resolve the expected script path against the declared workspace and confirm existence before the Blender invocation. This targets avoidable setup overhead, while preserving the later repaired example as a positive control for the diagnosis pipeline.

## Local replay experiment

The experiment parses actual tool IDs, requires successful write/replace receipts, checks each replacement preimage exactly once, and reconstructs the script at each of the eight recorded Gemini Blender calls. It never executes arbitrary shell commands copied from a trajectory. Blender 5.0.0 runs inside network-isolated bubblewrap with read-only source/runtime mounts, no host credentials, and only the observation directory writable. It does not render.

| Task | Recorded test-call line | Local result | Agent's recorded subsequent account |
|---|---:|---|---|
| Agave | 14 | `IndexError`: outdated internal index table | Same issue, then targeted repair |
| Agave | 22 | Passed; one mesh | Reports verified completion |
| Aquarium | 12 | `AttributeError`: `shadow_method` | Same removed-attribute issue |
| Aquarium | 22 | `TypeError`: expected Mesh, found NoneType | Same `to_mesh` return-value issue |
| Aquarium | 28 | Passed; one mesh | Reports verified completion |
| ArmChair | 12 | Passed; one mesh | Adds detail after successful run |
| ArmChair | 19 | Passed; one mesh | Refines proportions |
| ArmChair | 25 | Passed; one mesh | Reports completion |

The probe catches exceptions to save a report; consequently its process return code is not the verdict. `outcome.execution` and the exception fields are authoritative for this experiment. The separate exit-status experiment invokes Blender directly without that catcher.

Reproduce from the repository root:

```bash
/root/miniconda3/envs/3dcodebench/bin/python \
  runs/vsv_eval/diagnosis_20261008/3dcodebench/experiment/audit_replay.py
/root/miniconda3/envs/3dcodebench/bin/python \
  runs/vsv_eval/diagnosis_20261008/3dcodebench/experiment/check_exit_status.py
```

These scripts regenerate their own experiment observations. They do not edit the downloaded source logs or the common scorer. No claim is made that a newly replayed state reproduces every aspect of the historical machine.

## Implication for method design

The promising research target from these examples is **evidence adequacy before declaring a requirement verified**, rather than merely adding more check calls. We have positive text-level repair examples, avoidable tool/path friction, and a clear ceiling on visual evidence imposed by the protocol. First fix deterministic feedback semantics; then use a controlled, render-permitted continuation to test whether selecting informative visual observations adds value beyond execution and scene assertions. This pilot supplies intervention hypotheses and replayable cases, not measured gains or a cross-model ranking.
