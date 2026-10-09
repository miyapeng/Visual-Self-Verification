# Cross-benchmark verification adaptation — 2026-10-08

## Result

The existing six-metric evaluator now accepts normalized inputs for SWE-bench
Multimodal, 3DCodeBench and GameDevBench. The same extraction, evidence/diagnosis
stages, repair links, score formulas and saved-response validation are reused.
Non-browser acceptance uses explicit fixed command probes and the existing state
Judge. The original browser path remains available. No paid model API was called.

This is an implementation and data-readiness audit, not a new performance leaderboard.
The real sample has no newly generated Judge scores. The successful six-score tests
use simulated model replies and real execution of small controlled probe programs.

## Sample

Artifacts: `runs/vsv_eval/cross_benchmark_20261008/`.
`sample_inventory.json` is the machine-readable summary; each case contains its
normalized inputs and rule candidates. Candidates include unrelated inspection
operations until the existing model annotation stage filters and groups them.

| Benchmark / task | Original imported events | Rule windows | Original output-image inputs | Task reference images |
| --- | ---: | ---: | ---: | ---: |
| 3DCodeBench / ArmChair text | 18 | 6 | 0 | 0 |
| 3DCodeBench / AquariumTank text | 14 | 3 | 0 | 0 |
| 3DCodeBench / ArmChair image | 19 | 5 | 0 | 4 |
| SWE-MM / wp-calypso-27090 | 182 | 52 | 0 | 2 |
| SWE-MM / p5.js-5970 | 127 | 44 | 3 | 1 |
| GameDevBench / task_0003 | No public trajectory in inspected result | — | — | — |
| GameDevBench / task_0004 | No public trajectory in inspected result | — | — | — |

Event counts are canonical events, with native IDs retained. OpenHands thoughts
are separate original agent-response events; source-member ordinals/UUIDs still
identify their containing native events. This preserves diagnoses commonly written
before the next call. Every imported tool return is paired by its actual call ID.
All five trajectory imports have zero unmatched tool returns and zero missing
model-facing output-image bytes. Missing remote task-reference placeholders in the
original messages are preserved; their reference images were fetched separately
for the task catalogue. They are not missing self-observation screenshots.

The three p5.js image inputs are canonical events 56, 82 and 87. The latter two
contain identical pixel bytes but remain distinct recorded observations. They are
original base64 payloads, not reconstructed screenshots. Four ArmChair images are
reference reads, explicitly named by the initial prompt, and are not visual self-checks.

Downloads were selective: three 3D transcripts total 796,606 bytes; two OpenHands
conversation archives total 726,764 bytes, from a 727,040-byte compressed prefix.
The OpenHands score-index JSON and three small issue reference images were also read.
No full dataset, model weights, repository histories or execution images were downloaded.
GameDevBench task and result inputs were already local.

## Evidence and interpretation

1. The sampled 3D prompts explicitly prohibit renders and emphasize execution and
   traceback repair. Their lack of visual self-inspection cannot be attributed to
   model inability. Analyse this as a constrained protocol, or collect trajectories
   under a protocol that allows viewing outputs for a visual capability comparison.
2. SWE-MM's p5.js sample has original screenshots, so those recorded inputs need no
   image restoration. wp-calypso's inspected trace contains no output-image payload;
   an independent future screenshot cannot establish that its agent viewed an image.
3. GameDevBench's inspected public final-results rows have task outcomes and resource
   usage, but no tool events or model-facing image inputs. They cannot yield six
   self-verification scores. Obtain native solver transcripts and intermediate artifacts,
   or collect new runs; the adapter refuses to manufacture a trajectory from final status.
4. For RS/CP, the samples still need audited intermediate code and executable probes.
   SWE-MM needs its original repository base commit/environment and replayed changes;
   3D needs the relevant generated script states plus Blender; GameDevBench needs the
   task's initial project, changes and Godot environment. Neither final patches nor
   post-hoc renders alone establish the exact historical before/after states.

## Implementation

- Shared native parsers moved into `vsv_eval/native.py`; the old website script
  imports the same functions. No upstream benchmark files were rewritten.
- `vsv_eval/benchmarks.py` reads Claude JSONL, bounded selected OpenHands conversation
  archives, and canonical runs; it normalizes task-only material and audits availability.
- `vsv_eval/artifact_acceptance.py` executes fixed probes on separate working copies,
  retaining multiple rendered views, missing-image records, and executable reports.
- `catalogue.py`, `preparation.py`, `states.py` and `system.py` accept command probe
  assignments while retaining the existing metric stages and validated source links.
  The plan model assigns criterion/probe IDs; it cannot author executable commands.
- Common commands live in `scripts/vsv/`; comparison exports include `benchmark`.
  The existing Gemini 3 Flash profile remains the default configuration.

## Verification

The focused VSV suite and new adapter tests report **302 passed, 8 skipped**; counts are recorded in
`sample_inventory.json`. Tests cover reordered source events, interleaved returns,
failed checks, missing reference pixels, task/solution separation, immutable versions,
multiple images, failed renders, saved-plan validation and all six scores through
real subprocess acceptance for each supported new benchmark name.

An additional legacy trajectory-website test requires two local OpenHands files
under `runs/vision2web_generation/qwen35-9b-openhands-selfverify-v5-local-bypass/`.
Those historical fixture files are absent in this checkout; the test finds zero
attempts instead of two. Its implementation was not changed in this work.

No original archived commands, Blender rendering, Godot validation, paid Judge calls,
full code restoration or fresh real-model scoring were executed in this adaptation.
These remain concrete runtime/data-validation steps, not proven results.

## Reproduce

See [VSV_BENCHMARKS.md](../docs/VSV_BENCHMARKS.md) for preparation, catalogue,
extraction, command acceptance bindings and the common evaluation CLI.
The prepared `experiment.json` intentionally points to not-yet-generated catalogues;
`--check-only` reports those missing prerequisites without contacting the API.
Run task planning with a funded API, then extract rounds, provide audited versions
and acceptance bindings for linked repairs, and run the same evaluation manifest.

Sources inspected:

- [OpenHands results index](https://github.com/OpenHands/openhands-index-results)
- [Opus SWE-MM archive index](https://github.com/OpenHands/openhands-index-results/blob/main/results/claude-opus-4-8/scores.json)
- [3DCodeBench agent transcripts](https://huggingface.co/datasets/YipengGao/3DCode/tree/main/3DCodeBench_ModelLogs/agent_logs)
- [GameDevBench public results](https://github.com/waynchi/gamedevbench/tree/main/results)
