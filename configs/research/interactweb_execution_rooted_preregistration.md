# InteractWeb execution-rooted source-context diagnostic

Frozen before any model request for this comparison: 2026-08-21T19:52Z.

## Question and observed motivation

Can browser-observed execution reachability prevent a coding model from editing
semantically plausible but inactive implementations, and thereby improve the
conversion of a public interactive failure into a certified repair?

The motivation is frozen from the preceding three-condition run. On
`000045_P-RAM`, Qwen3.5-9B repeatedly edited `src/main.js`, although the public
Vite replay loaded only the inline application in `index.html`. On
`000062_P-CON`, the browser reported only `Assignment to constant variable`
without a stack, and the compact condition changed the wrong constant. A
no-model provenance replay made before this preregistration found that the
actual error originates at `src/main.js:230:8`.

## Cases and leakage boundary

- Cases: `interactweb-000045-navigation-repair` and
  `interactweb-000062-question-bank-repair` from
  `interactweb_active_repair_cases.json`.
- Frozen manifest SHA-256:
  `5f8e06e241704dd203634174a028c42a459b0ae4697a1790fcfca249e18624e4`.
- The action plans, task text, task images, and current generated programs are
  unchanged from the previous diagnostic. Checks use only public user-visible
  behavior. No private InteractWeb oracle slots or official judge labels are
  read or exposed.
- The outcome is a counterfactual public-check repair diagnostic, not an
  official InteractWeb-Bench score.

## Fixed observation layer

Both conditions use the same new lossless public provenance:

1. Playwright records every page request, resource type, response status, and
   request failure during the frozen action sequence.
2. Same-origin URLs are mechanically mapped to existing workspace source files.
   Vite runtime files, dependencies, cross-origin resources, generated output,
   and files never requested by the page are not mapped.
3. `pageerror` records include the browser-provided stack. Dynamic loopback
   ports are normalized before entering model context.

The checker actions, checklist weights, pass/fail logic, and final fresh replay
are unchanged. A provenance-only Vite/Chromium pilot reproduced the original
scores (1/3 and 0) and mapped:

- `000045`: `index.html` only (29,201 source bytes).
- `000062`: `index.html`, then `src/main.js` (23,050 source bytes total).

Forty-three research-harness tests, including real Chromium tests, pass before
freezing this comparison.

## Paired conditions

The verification-evidence policy is `guarded_frontier` in both conditions.
Only the source-code units rendered in the `Current program` section differ.

1. `full_source`: every renderable repository source file in deterministic
   lexical order, up to the common 200,000-byte complete-file cap.
2. `execution_rooted`: only complete workspace source files mapped from the
   browser's observed same-origin loaded-resource graph, in first-request order,
   under the same cap. There is no semantic retrieval, task-keyword search, or
   private-test guidance.

Both prompts use identical wording; the source selector's name and resource
URLs are not disclosed to the generator. Selection manifests record every file,
hash, source byte count, and rendered byte count.

## Frozen generation and verification settings

- Model: `/data/miyapeng/model/Qwen3.5-9B`, served as `Qwen3.5-9B` by vLLM.
- One A800; vLLM max model length 65,536; maximum one concurrent sequence.
- Thinking disabled through the model's official chat-template argument.
- Temperature 0, seed 0, maximum 8,192 output tokens, 600-second request timeout.
- One semantic revision and one independent revision-contract retry.
- Verification context budget: 4,096 estimated text tokens, four images, eight
  million image pixels; browser video disabled for this diagnostic.
- Fresh certification is mandatory. Invalid patches are state preserving;
  non-improving candidates are rolled back to the best version.
- Fixed condition order: `full_source`, then `execution_rooted`; fixed case
  order: `000045`, then `000062`. This two-case run is diagnostic and is not used
  for a significance claim.
- Frozen research-source snapshot SHA-256:
  `4af32818050c85fc7bcd231351452ae755871463a09eb7ef7414bbbf086fd59b`.

## Outcomes and falsification

Primary paired outcome:

- number of cases whose fresh final score is strictly higher than its initial
  score, with the admitted edit located in a browser-mapped source file.

Secondary diagnostics:

- final score per case;
- valid-contract and admitted-patch counts;
- selected source files and rendered source bytes;
- response characters, generator calls, generator time, and verifier time;
- whether the browser-provided source location is preserved in selected model
  context;
- whether rollback restores a non-improving candidate.

The hypothesis is supported for expansion only if `execution_rooted`:

1. correctly repairs at least one of the two cases;
2. repairs no fewer cases than `full_source`;
3. supplies fewer source bytes on at least one paired case; and
4. introduces no fresh-certification regression on the other case.

If it only reduces tokens/output length without a certified repair, report an
efficiency/localization signal but do not claim task-success improvement. If a
repair comes only from the newly available stack and full source repairs the
same case, attribute it to provenance rather than source selection. If neither
condition repairs a case, this branch fails its expansion rule and must not be
scaled without a new independently observed mechanism.
