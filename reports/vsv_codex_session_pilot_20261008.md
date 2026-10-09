# In-session Codex scoring pilot

This pilot evaluates the archived 3DCodeBench `ArmChair-text` trajectory attributed
to Claude Opus 4.8. The active Codex session supplied labels through the existing
JudgeClient; existing validators and metric functions computed every score.
No paid API or other model endpoint was called. The attribution `codex-in-session`
does not identify a pinned model version. This is not a calibrated or blind evaluation:
the conversation already contained later outcomes before individual packets were judged.

## Results

| Metric | Result | Evidence and denominator |
| --- | ---: | --- |
| VC | 16.67% | One of six fixed artifact requirements checked: executable mesh generation. |
| CV | 100% | Both attempted execution checks returned sufficient feedback for their actual scope. |
| BDA | 50% | Passing-state diagnosis correct, 1/1; failing-state diagnosis absent, 0/1. The metric averages these classes. |
| RS | 100% | One linked repair target passed independent post-repair execution. |
| CP | Not applicable | No passing baseline requirement at the required entrypoint; denominator zero. |
| VCS | 50% | One of two rounds satisfies all applicable conditions; the initial round lacks an explicit diagnosis. |

There are no unknown labels in the completed result. An empty denominator is not
an incomplete evaluation and does not receive an invented percentage.

## Recorded behavior

The extractor produced six candidates and retained four calls in two rounds:

1. Events 4–9: Blender cannot open the task-local script; `pwd`, `ls`, and `find`
   locate it one directory above. These calls jointly diagnose one execution
   problem. No contemporary model text explicitly evaluates these results.
2. Events 12–14: after the copy at event 10, the same Blender command succeeds
   and reports 1,848 faces. Event 14 explicitly judges the execution successful.

The first round links modification 10 to the second round. The copy's incidental
`ls` is retained as modification evidence, rather than another independent round.
Writing the completion marker at event 15 is bookkeeping. The final retrospective
summary at event 17 does not retroactively supply a diagnosis before modification 10.

The missing-diagnosis penalty is **not a wrong-diagnosis finding**. The agent's
actions do address the observed problem. This example exposes how the current
definition measures expressed judgment in addition to effective action; the pilot
did not change that definition to raise the score.

The task explicitly forbids agent rendering. Neither round contains an image
input. Low visual coverage therefore does not establish a general inability to
self-verify visually or protocol noncompliance. The four recovered views belong
to evaluator acceptance, not the original agent's inspection history.

## Acceptance evidence

The exact pre-copy and post-copy recorded-file checkpoints are V1 and V2.
V1 lacks the required entrypoint. Both probes reproduce that absence; this is
an artifact placement defect, not a network timeout or an unavailable Blender
installation. No requested artifact is produced in the prescribed setup, so
the artifact criteria fail availability before repair. This does not claim that
the misplaced source contains geometrically incorrect code.

V2 executes successfully. Four rendered views show a padded chair with raised
armrests, a cushioned seat and reclined back, shaped edges, and a matching front
ottoman. The renderer normalizes position, so a separate sandboxed scene inventory
was necessary: it observes one mesh at `(0, 0, 0)`, 1,968 vertices and 1,848 faces
before normalization. It does not modify the archived source.

An initial acceptance-plan annotation could not establish the original position
using the rendering probe alone and was rejected by the frozen-plan validator.
The final plan adds the inventory probe and uses a fresh output directory. The
rejected annotation and earlier preparation files remain available, but do not
contribute to the final scores.

## Reproduction and records

All pilot files are under `runs/vsv_eval/codex_session_20261008/ArmChair-text/`:

- `session_result.json`: attributed pilot result, score hash and exact accepted response paths.
- `extraction/verification_rounds.json`: original events and check/repair/recheck references.
- `plan/catalogue.json`: six fixed artifact requirements; reuse unchanged for other agents on this task.
- `complete/ArmChair-text/final/scores.json`: validated metric output with labels and denominators.
- `complete/ArmChair-text/states/replays/`: actual render and inventory observations.
- Stage caches: exact exported requests, session-authored annotations and accepted response records.

```bash
/root/miniconda3/envs/swemm/bin/python scripts/vsv/evaluate.py \
  --manifest runs/vsv_eval/codex_session_20261008/ArmChair-text/experiment_complete.json \
  --output-dir runs/vsv_eval/codex_session_20261008/ArmChair-text/complete
```

The completed run consumes 18 accepted annotation packets: catalogue 1,
extraction 1, VC/CV/observation/BDA 2 each, recheck alignment 1, repair planning 1,
and state judgments 6. These are session-authored packets, not 18 independent
model invocations. HTTP requests: **0**. One rejected preliminary plan is excluded
from that accepted count. Cached reruns validate and reproduce the saved labels.

Validation: 95 relevant tests passed, including four new tests for request-bound
session responses and zero-network execution. The full scoring runner completed
and reproduced the result through its existing cache and source-integrity checks.

The task catalogue was created after this session had seen the trajectory. It is
task-derived, but not prospectively frozen for this pilot. Future comparisons
must share the fixed catalogue and clearly separate session pilots from API Judge
evaluations. No score formula was changed.
