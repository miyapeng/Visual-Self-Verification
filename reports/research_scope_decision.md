# Current research-scope decision

**Status:** canonical and active from 2026-08-23.

InteractWeb-Bench is archived and outside the current paper. It must not be
resumed, added to an experiment aggregate, or reintroduced into method or data
plans unless the user explicitly reverses this decision. It is not an
unfinished benchmark waiting to be resumed.

The machine-readable source of this decision is
[`configs/research_scope.json`](../configs/research_scope.json).

## Why InteractWeb-Bench was removed

The paper asks whether one multimodal coding policy can implement a visual
application, execute and interact with its current implementation, diagnose
failures from action-conditioned visual evidence, revise the code, and decide
when to inspect, edit, interact again, or submit.

InteractWeb-Bench mainly studies escaping blind execution when user
instructions are ambiguous, incomplete, redundant, or contradictory. Its
outcome is strongly affected by persona-driven user simulation, requirement
clarification, and the choice among `Clarify`, `Implement`, `Verify`, and
`Submit`. Keeping it active would therefore introduce four confounds:

- apparent gains could come from better requirement elicitation rather than
  visual self-verification;
- its official `Verify` path and Visual Copilot add a separate feedback role,
  obscuring comparison with a same-policy design;
- its user simulator and interaction protocol require substantial engineering
  unrelated to the central question;
- it expands the paper into requirement elicitation and user alignment,
  weakening the scientific focus.

## Active benchmark scope

1. **Vision2Web Level 1, Level 2 and Level 3 — primary.** Keep the full official
   193-task benchmark: 100 webpage, 66 frontend, and 27 website tasks.
2. **FronTalk — complementary.** Study self-verification under multi-turn
   requirement evolution, regression detection, and preservation of earlier
   functionality.
3. **SWE-bench Multimodal — secondary transfer.** Study visual-evidence
   understanding and code repair. Do not claim proactive failure discovery for
   this benchmark unless an instance actually generates new executable visual
   observations.

No replacement benchmark may be added without explicit instruction.

Shared self-verification infrastructure must target Vision2Web first,
FronTalk second, and SWE-bench Multimodal only as a transfer setting.

## Archived material and reporting boundary

Existing InteractWeb source snapshots, official evaluator code, datasets,
environments, frozen preregistrations, trajectories, logs, credentials and
evaluation artifacts are deliberately retained as historical exploratory
evidence. They are not active dependencies of the paper and must not be
aggregated with active method results or cited as evidence for the paper's
central claims.

InteractWeb launchers and mixed Vision2Web/InteractWeb experiment launchers are
disabled. Read-only audit tools remain available solely to inspect and preserve
the provenance of past work. The official evaluator snapshot is unmodified.

An inventory of preserved frozen material and disabled entry points is in
[`configs/research/INTERACTWEB_ARCHIVE.md`](../configs/research/INTERACTWEB_ARCHIVE.md).
