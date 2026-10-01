# InteractWeb active-check repair diagnostic: preregistration

Frozen before any repair-model call.

The first no-model checker pilot showed that case 000062 raises a browser
`TypeError` only after clicking its category button. Before any repair-model
call, plan version 2 added a post-click `assert_no_console_errors` so the
technical checklist covers the full interaction rather than only page load.
The pilot remains preserved as `interactweb-repair-initial-001`; the frozen
baseline is the version-2 replay.

## Scope

This is a two-case causal diagnostic over already generated Qwen3.5-9B
InteractWeb-Bench artifacts. It is not an official InteractWeb score. The
checks encode only public task requirements and failures exercised by the
original public interaction traces; private oracle slots are not exposed to
the repair model.

## Question

When an executed browser check provides current-version counterevidence, can a
small coding revision convert detection into a certified repair, and does
keeping only the requirement-scoped current failure preserve repair ability?

## Conditions

- `full`: all current-version action and checklist records.
- `guarded_frontier`: current unresolved requirement frontier plus the minimum
  current-version regression guards selected by the frozen policy.
- `guarded_frontier_text_only`: the same frontier without screenshots.

The source program, action plan, model, seed, temperature, output cap, context
budget, revision budget, admission contract, and fresh final certification are
fixed across conditions. Model: Qwen3.5-9B; temperature 0; seed 0; one semantic
revision; one contract retry; 4096 text tokens; at most 4 images and 8,000,000
image pixels; 8192 output tokens; thinking disabled at the serving layer.

## Outcomes

Primary outcome: fresh final checklist score and whether both checklist items
pass. Secondary outcomes: generator calls, contract rejection, selected text
tokens/images, wall time, regression, and exact patch.

## Interpretation and expansion rule

With only two cases, results are diagnostic and cannot establish task-level
superiority. Expand to additional independently classified program-caused
failures only if `guarded_frontier` repairs at least one case, does not repair
fewer cases than `full`, and uses less selected context on at least one paired
case. A text-only result can establish that pixels were unnecessary for these
functional failures, but it cannot establish that visual evidence is generally
unnecessary.

If all conditions fail, inspect admission/model failures without changing the
frozen checks. If all conditions pass, treat the cases as too easy for a
context-policy comparison while retaining evidence that active verification
can convert a detected failure into a repair.
