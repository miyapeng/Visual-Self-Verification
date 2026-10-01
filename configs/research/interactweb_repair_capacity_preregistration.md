# Pre-registration: repair-capacity diagnostic after execution-rooted failure

Frozen before the first Qwen3.5-35B-A3B request on 2026-08-21.

## Observation and question

The frozen Qwen3.5-9B execution-rooted run supplied the actual runtime source
file for both InteractWeb cases and, for `000062_P-CON`, a precise public browser
stack pointing to `src/main.js:230:8`. It nevertheless repaired 0/2 cases. This
diagnostic asks whether that result is primarily a base-model repair-capability
limit rather than a missing-context limit.

## Frozen data and information boundary

- Cases: `interactweb-000045-navigation-repair` and
  `interactweb-000062-question-bank-repair` from
  `configs/research/interactweb_active_repair_cases.json`.
- Manifest SHA-256:
  `5f8e06e241704dd203634174a028c42a459b0ae4697a1790fcfca249e18624e4`.
- Agent-visible information remains limited to the public task, frozen public
  browser action/check plan, current screenshots/DOM/accessibility/console
  evidence, browser-request provenance, and mapped current source. No hidden
  InteractWeb evaluator, reference implementation, or private slot is used.
- This is a public-check counterfactual repair diagnostic, not an official
  InteractWeb-Bench score.

## Frozen comparison

The comparator is the already completed Qwen3.5-9B `execution_rooted` condition
at
`runs/research/active_visual_verification/interactweb-execution-rooted-qwen35-001/`.
It repaired 0/2 cases, selected three source files / 52,330 rendered bytes, and
used one revision plus at most one contract retry.

The new condition changes only the generator checkpoint to the locally frozen
`/data/miyapeng/model/Qwen3.5-35B-A3B`. It keeps:

- `guarded_frontier` evidence policy and `execution_rooted` source context;
- temperature 0, seed 0, thinking disabled;
- one semantic revision and one contract retry;
- 8,192 maximum output tokens;
- 4,096 selected-evidence text tokens, four images, and 8M image pixels;
- the identical browser actions, assertions, rollback, and fresh final
  certification.

One A800 is attempted first. If model loading cannot fit, a separately logged
two-A800 tensor-parallel retry is an infrastructure recovery, not a new
experimental condition.

## Outcomes and decision rule

Primary outcome is strict repair conversion: final freshly certified score must
exceed the original score. Secondary outcomes are contract rejection, edited
file, diagnosis, source bytes, response size, time, and GPU-hours.

- If Qwen3.5-35B-A3B repairs at least one case, while Qwen3.5-9B repaired none,
  base-model repair capability is a supported competing explanation. The
  result does **not** establish execution-rooted context as a method win.
- If it repairs neither case, stronger zero-shot generation does not rescue the
  current interface on this diagnostic. The next evidence-driven test is an
  ordinary inspect/edit tool loop, because exact-edit emission itself may be
  the bottleneck.
- No result from these two cases is sufficient for a paper or benchmark-level
  claim.

