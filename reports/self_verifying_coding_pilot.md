# Same-policy multimodal coding verification pilot (historical report)

> **Scope update (2026-08-23):** InteractWeb-Bench rows in this report are
> archived exploratory evidence only. They are excluded from current method
> aggregates, claims, data construction and future experiments. This historical
> report is not the canonical current experiment plan. Active work targets
> full Vision2Web first, FronTalk second, and SWE-bench Multimodal only as a
> transfer setting. See
> [`research_scope_decision.md`](research_scope_decision.md).

Status: **closed historical pilot**. Its mixed six-case inputs and five-case
comparison are archived and must not be resumed, expanded, or used as a current
paper result. Current work starts from a new active-scope manifest.

This report is intentionally conservative. “Unknown” means the available trace cannot prove the
claim. Post-submission benchmark evaluation, environment-triggered deployment feedback, and an
external visual model are never counted as the coding policy’s own judgment.

## 1. Existing trajectory rhythm audit

### Audit source and definitions

The frozen normalized input contains 799 cases and is stored under
`runs/research/active_visual_verification/observational-audit-010/`. The derived per-case audit is
stored under `runs/research/active_visual_verification/implementation-rhythm-audit-002/`.

- Derived `summary.json` SHA-256:
  `1b8be4d683001ea251cc76898def7624b55da9ea9df4c6dcff3f464dea0e5a52`.
- Derived per-case `cases.jsonl` SHA-256:
  `13afa4aa356192acf476a07a44a708038d7fcc37b5d54e5e80b1fa48c51d1338`.

- Source `events.jsonl` SHA-256:
  `e4a1d9999a0089620e5e84f44acd0d58ad74278769033436cf7d34f32280b5de`.
- Source `cases.jsonl` SHA-256:
  `5955065a43e0425eb63c18462df35496301b7f567a08bfd3e33bed5f1c2ac9ca`.
- `observational-audit-010` supersedes `-009`: 42 normalized events across 20 case rows were
  corrected so prose inside `finish`/`ask_user` and Vision terminal output cannot masquerade as a
  generated visual action. The conservative implementation-rhythm aggregates below happen to be
  unchanged because those false categories were not admitted as qualifying active boundaries, but
  all reported hashes and reproduction commands now bind the corrected source.
- An **implementation block** is consecutive inspection/edit activity containing at least one
  production edit. An agent-active check, submission, or benchmark-specific deployment boundary
  closes a block.
- **Agent-active verification** requires an executing tool invoked by the coding policy. Merely
  saying “test” or “screenshot” in reasoning is not execution.
- A **confirmed repair** requires the exact same executable action digest to fail, a code edit, and
  the exact action to pass on replay. A later unrelated pass is only a coarse association.

The statistics below describe the observed Qwen3.5-9B trajectories in this repository, not the
benchmark population or all possible scaffolds.

| Metric | SWE-MM | Vision2Web | FronTalk |
|---|---:|---:|---:|
| Cases | 102 | 193 | 100 |
| Trajectory available | 102 | 189 | 100 |
| Cases with agent-active executable check | 79 | 121 | 0 |
| Active check actions | 949 | 882 | 0 |
| Implementation blocks before first active check, mean / median | 0.39 / 0 | 0.90 / 1 | unknown |
| Edit fraction before first active check, mean / median | 0.25 / 0 | 0.74 / 0.86 | unknown |
| Program-edit events after active check | 681 | 348 | 0 |
| Verify–edit–reverify cycles | 235 | 121 | 0 |
| Concrete failure observations bound to active checks | 264 | 22 | 0 |
| Confirmed same-check repair | 6 / 15 terminal exact replays | unknown (0 exact terminal replays) | unknown |

The edit fraction uses production edit units. It is null when there is no active check. The SWE-MM
median is zero because many agents ran a reproducer or tests before their first source edit.

### Exclusive trajectory rhythm

| Rhythm | SWE-MM | Vision2Web | FronTalk |
|---|---:|---:|---:|
| No active verification | 23 | 72 | 100 |
| Final-version-only verification | 11 | 36 | 0 |
| One interleaved verify–edit–reverify cycle | 14 | 36 | 0 |
| Multi-round interleaved verification | 36 | 27 | 0 |
| Verify then edit without reverify | 16 | 22 | 0 |
| Active verification without a production edit | 2 | 0 | 0 |

These categories are mutually exclusive. A broader flag additionally finds 40 SWE-MM and 29
Vision2Web cases with at least two verify–edit–reverify patterns; the exclusive
multi-round category is smaller because “edited without final reverify” takes precedence when the
trajectory ends that way.

### Failure-to-repair evidence

- **SWE-MM:** 264 failure episodes. Outcomes are 6 confirmed same-check repairs, 9 same-check still
  failing, 15 edited without re-verification, 56 no later code edit, and 178 unknown because no
  terminal replay of the same check exists. The defensible confirmed success rate is therefore
  `6 / 15 = 40%`, not the much larger 90.7% coarse “some later check passed” association.
- **Vision2Web:** 22 failure episodes: 12 no later edit, 3 edited without re-verification, and 7
  unknown without a terminal same-check replay. No confirmed-repair denominator exists.
- **FronTalk:** no executable check and therefore no observed failure-to-repair episode.

### Context-window pressure

The read-only machine audit is
`runs/research/active_visual_verification/context-window-audit-001/summary.json` (SHA-256
`1810da0a015e82487885d7b67947eb8af265a26abcaef9033c86f539d71541c8`). It uses the local
Qwen3.5-9B tokenizer for saved text histories, scans explicit server errors separately, and never
guesses unavailable image-token usage.

- **SWE-MM:** the complete Qwen3.5-9B dev run has 0/102 hard context failures at 262,144 tokens.
  The incomplete Qwen3-VL run has five hard failures among its 83 persisted results, with reported
  inputs from at least 262,145 through 263,970 tokens. This incomplete denominator is not a final
  Qwen3-VL baseline.
- **Vision2Web:** 0/189 saved trajectories contains a hard context error, but 139/189 contain at
  least one OpenHands `Condensation`, totaling 286 condensation events. Condensation is a harness
  response to context pressure, not evidence that vLLM rejected a request and not evidence that the
  summary preserved every early requirement.
- **FronTalk:** across 100 dialogues and 1,000 assistant requests, the largest actual generation
  prompt is 66,912 tokens; with the requested 8,192-token output allowance it is 75,104. The
  largest completed ten-turn history plus a next-assistant marker is 73,188. No `(omitted)` recovery
  or hard context error occurred, so capacity overflow is not the immediate FronTalk failure mode.

Archived InteractWeb trajectory statistics remain in the frozen machine audit,
but are intentionally omitted from the active-scope tables and aggregates.

## 2. Strict boundary: active check, environment feedback, external judge, evaluator

The active benchmarks expose two relevant mechanism classes:

1. **Agent-active executable check.** The coding policy issues a shell test, server probe, browser
   action, or `screenshot_validated` call. This is counted as active verification initiation.
2. **Official evaluator.** SWE-MM clean evaluation and Vision2Web/FronTalk evaluation happen after
   submission. They are research outcomes only and are excluded from the development loop.

The earlier InteractWeb automatic-deployment and external-Visual-Copilot
distinctions remain useful historical cautions, but are not active method
interfaces or evidence.

Vision2Web deserves an additional warning. The raw OpenHands traces contain reasoning such as
“verify visually” and “take a screenshot,” but no browser/screenshot tool action. The 882 counted
checks are terminal actions such as build, server start, `curl`, `grep`, and test commands. The
count of genuine generated visual checks is **zero**.

## 3. Representative manual trajectory review

### SWE-MM

1. `markedjs__marked-2811` — multi-round terminal verification. The trace first runs a reproducer
   at sequences 7–10, later edits `src/rules.js`, repeatedly runs custom scripts and `npm test`, and
   continues editing/rechecking. It has 40 active checks, 22 post-check edit events, 12 cycles, and
   one exact same-check confirmed repair. The official clean evaluator later marks it resolved, but
   that outcome is not visible to the agent. Evidence:
   `runs/swe_mm_official/qwen35-9b-mini-official-dev-s2/agents/Qwen3.5-9B/swe-mm/markedjs__marked-2811/trajectory.json`.
2. `Automattic__wp-calypso-21409` — final-only verification. The agent explores and rewrites the
   component, then runs five ad-hoc checks near the end. Four fail and one different check passes;
   no production edit follows. It is not a repaired failure and the clean evaluator is unresolved.
   Evidence:
   `runs/swe_mm_official/qwen35-9b-mini-official-dev-s2/agents/Qwen3.5-9B/swe-mm/Automattic__wp-calypso-21409/trajectory.json`.
3. `Automattic__wp-calypso-21492` — evaluator-only. The agent performs extensive repository
   inspection, edits `plans.jsx`, and submits without executing a classified check. The later clean
   evaluator result is unresolved; it must not be backfilled as an agent observation. Evidence:
   `runs/swe_mm_official/qwen35-9b-mini-official-dev-s2/agents/Qwen3.5-9B/swe-mm/Automattic__wp-calypso-21492/trajectory.json`.

### Vision2Web

1. `frontend/chauffeurdriven` — terminal-interleaved, not visually interleaved. The agent writes a
   large site, starts `node server.js`, probes title/content with `curl`, edits deployment files,
   restarts, and probes again. There are 14 active terminal checks and five edit–recheck cycles, but
   no browser/screenshot action. Three OpenHands condensation events are also present. Evidence:
   `runs/vision2web_generation/qwen35-9b-openhands-official-v2/agents/litellm_proxy__Qwen3.5-9B/vision2web/official/frontend__chauffeurdriven/trajectory.json`.
2. `frontend/401trucksource` — final-only terminal verification. The full React implementation is
   written before a `start.sh`/`npm install` attempt. It has one classified active check, no later
   edit, no browser action, and three condensation events. Evidence:
   `runs/vision2web_generation/qwen35-9b-openhands-official-v2/agents/litellm_proxy__Qwen3.5-9B/vision2web/official/frontend__401trucksource/trajectory.json`.
3. `frontend/6abc` — no active verification. The trace reads resources and edits React pages but
   never starts the application or executes a check; it has no browser action and no condensation.
   Evidence:
   `runs/vision2web_generation/qwen35-9b-openhands-official-v2/agents/litellm_proxy__Qwen3.5-9B/vision2web/official/frontend__6abc/trajectory.json`.

### FronTalk

1. `e4aa9d67d4a5e40cdffac7a3429994a8.html` — ten successive requirements for a college site. Every
   assistant turn emits full fenced website files; no code is installed, rendered, or executed.
2. `8276d8b5f480c33d125f85f15f7260c2.html` — the largest reviewed visible trace (about 290,420
   aggregate visible characters) adds football-site behavior across ten full-generation turns,
   still without an execution observation.
3. `422c9ecddeb3e13bf9066639d6743a80.html` — multi-file restaurant site with 39 emitted program-file
   units across ten revisions. It is useful for regression/obligation retention, but the trace
   contains no evidence that any prior behavior remained executable.

All three are stored as keyed records in
`runs/native_benchmarks/qwen35-9b-local-support-frontalk-text-full-20260818/frontalk/messages.jsonl`.
The ten requirement boundaries are implementation blocks only in the benchmark’s conversational
sense; they are not runtime checkpoints.

### Archived InteractWeb exploratory review

1. `000001_P-INT` — final-version-only visual request. One Bolt artifact receives automatic
   `Environment Ready`; the Builder then calls `screenshot_validated` once and submits after the
   external `Visual Process Audit` says the page is successful. The Builder planned the check, but
   the visual judgment is not same-policy. Evidence:
   `runs/native_benchmarks/qwen35-9b-interactweb-full-20260820/shards/shard-06/interactweb/Qwen3.5-9B/logs/000001_P-INT/interaction_history.json`.
2. `000013_P-RAM` — no agent-active verification. Sixteen artifact revisions each receive
   automatically generated Vite `/main.js` 404 feedback. The Builder keeps rewriting until the
   max-turn stop but never calls `screenshot_validated`; automatic feedback cannot be counted as
   an agent-planned browser test. Evidence:
   `runs/native_benchmarks/qwen35-9b-interactweb-full-20260820/shards/shard-05/interactweb/Qwen3.5-9B/logs/000013_P-RAM/interaction_history.json`.
3. `000057_P-CON` — repeated external visual feedback. Five implementation/deployment blocks occur
   before the first screenshot request; the trace contains eight `screenshot_validated` calls,
   fourteen later edit events, and six verify–edit–reverify cycles. However every judgment is an
   external `Visual Process Audit`, route-level replay identity is weak, and the case ends at the
   turn budget. Its five apparent failure/repair episodes remain unknown rather than confirmed.
   Evidence:
   `runs/native_benchmarks/qwen35-9b-interactweb-full-20260820/shards/shard-07/interactweb/Qwen3.5-9B/logs/000057_P-CON/interaction_history.json`.

The manual sample supports the parser boundaries: no official evaluator or automatic deployment
was promoted to agent-active verification, Vision2Web reasoning prose was not promoted to a visual
tool call, and external visual conclusions remain explicitly external.

## 4. Archived mixed-pilot framework and call order

This section documents the closed mixed pilot. Its InteractWeb adapter and
six-case manifest are inactive. Reusable same-policy machinery may be carried
forward only through a new Vision2Web manifest, followed by FronTalk and
then SWE-MM transfer experiments.

The opt-in prototype implements the minimum accepted call order: public-input obligations →
existing first complete runnable version → deploy and capture initial browser state → one
same-policy action-plan call → deterministic Playwright execution → one same-policy evidence
judgment → same-policy localized repair → complete plan replay → monotonic version acceptance.

- Obligations, plan, evidence judgment, and repair all use one immutable backend/model,
  temperature, seed, token limit, and request-extra configuration. The machine-readable result
  records every semantic stage and asserts this same-policy identity.
- Playwright executes only a typed mechanical vocabulary: reset, navigate, click, fill, select,
  hover, press, scroll, wait, and screenshot. The action executor never invents selectors or
  decides whether a check passed.
- A candidate is accepted only if it repairs at least one previously failed check and regresses no
  previously passing check. Rejected candidates restore the last accepted checkpoint. Duplicate
  program hashes, explicit keep/stop/rollback decisions, schema retry limits, model-call limits,
  browser-action limits, and revision limits terminate unproductive loops. A browser/runtime error
  during candidate replay also restores the last accepted hash before the error result is written.
- Initial task/reference images and browser evidence are bounded independently. Hidden evaluator
  fields are rejected recursively, the official evaluator is never invoked by the loop, and every
  result records `official_evaluator_visible: false`.
- The feature is disabled unless `--enable-self-verification` is explicitly supplied. Invoking the
  runner without that flag exits successfully without creating an output directory; existing
  benchmark runners are unchanged.

The valid frozen smoke input contains three successful pre-generated Vision2Web L2/L3 workspaces
(`frontend/chauffeurdriven`, `frontend/401trucksource`, `website/luxurycleaning`) and three
InteractWeb first-runnable snapshots (`000001_P-INT`, `000080_P-RAM`, `000029_P-CON`). Vision2Web
uses only its public prompt/PRD and prototype assets and explicitly rejects L1 webpage tasks.
InteractWeb replays only the released Builder's assistant `boltAction type=file` records and stops
at the first automatic `Execution Feedback` that says `Environment Ready` and contains neither an
install nor runtime error. A `Visual Process Audit` user event is a hard boundary. System-prompt
examples, later Visual Copilot text, pending evaluation, and official evaluator data cannot enter
the snapshot. The three cutoffs occur after 1, 2, and 6 artifact turns respectively.
The selected InteractWeb tasks are behaviorally nontrivial rather than three static pages:
`000001` requests stock-code/name report generation, `000080` requests todo CRUD plus completion,
filtering and search, and `000029` requests forum posting/reply/search/category/user-center behavior
with the deliberately adversarial visual requirement that all text be invisible.

The freeze is `interact_first_runnable_programs-001`; its provenance SHA-256 is
`b561f229a46abcabf194554822be382851d2b3644df5541502865247bdafb5fc`. The combined immutable
manifest is `self_verify_public_cases-004`: `cases.jsonl` SHA-256
`d04234648801c2f4b6c9841662611f1f17f5a916abc3369703d9d6e371fec796`, provenance SHA-256
`5af5f375006baddfbefc2df49833f519820f481242dd45e53ef44aad7b93bb0a`. Earlier r7 used
`self_verify_public_cases-001`. Manual inspection then showed that its generic static server was
invalid for the React/Vite `frontend/401trucksource` program. r8 used the runtime-corrected
`self_verify_public_cases-003`, but that manifest still pointed at final InteractWeb workspaces that
could contain revisions made after external Visual Copilot feedback. r8 is therefore retained only
as a contaminated framework diagnostic and is not eligible for any comparison or paper result.

The corrected adapter selects a static server only for static HTML and selects the generated npm
`dev` script for a Vite project. The latter links a separate dependency cache keyed by the exact
generated `package.json` SHA-256
`57fd10d9b1bc13644edfd7585e4da89beb38efcc0208e575279a907e0ff07317`; its lockfile SHA-256 is
`ce835f307226645038b808285ccc17b95eef37b62a303a050fe819535ec1452e`, and its 3,475-file
dependency checksum manifest SHA-256 is
`626234e2cc35aa26b426f5dec57708084289009c77af4ac1a8329d92fda82745`. A local browser probe loaded
the transformed React module and exposed the generated program's real `createElement` page error.
The old static server hid this model error behind an untransformed HTML shell. No benchmark source
or evaluator was modified.

## 5. Modified files and necessity

Audit-stage changes:

- `src/multimodalcode/research/trajectory_observations.py`: excludes non-executing reasoning that
  merely mentions screenshots or tests from the visual-action class.
- `src/multimodalcode/research/implementation_rhythm.py`: derives conservative per-case block,
  verification, replay, and failure-repair metrics from the leakage-safe normalized stream.
- `scripts/research_goal/audit_implementation_rhythm.py`: freezes source hashes and writes
  machine-readable per-case and aggregate audit results without overwriting an existing run.
- `scripts/research_goal/audit_context_window_pressure.py`: distinguishes hard server rejection,
  OpenHands condensation, actual append-only generation prefixes, and counterfactual pressure under
  InteractWeb's released 128K setting; it excludes terminal feedback that never caused another model
  request and does not invent image-token counts.
- `tests/test_trajectory_observations.py` and `tests/test_implementation_rhythm.py`: protect the
  evaluator/prose boundary and exact-replay semantics.
- `tests/test_context_window_audit.py`: protects the actual-request prefix boundary and narrow hard
  context-error classifier.
- This report: records definitions, aggregate results, and manual raw-trace evidence.

Prototype-stage additions:

- `src/multimodalcode/research/schema.py`: strict typed mechanical-action schema.
- `src/multimodalcode/research/self_verify.py`: frozen-policy calls, budgets, evidence-bound
  judgments, localized revision contract, checkpoint/replay, and no-regression admission gate. It
  requires exactly one check per distinct frozen obligation, independently caps public reference
  images and runtime evidence, reserves the browser budget for every allowed candidate replay, and
  stops on repeated raw plan/repair outputs even when the JSON contract was invalid. A role-only
  locator is allowed because Playwright supports it and some unique controls have no accessible
  name; a name without a role and multiple locator families remain invalid.
- `scripts/interactive_judge_playwright.js`: mechanical browser execution and evidence capture;
  it contains no semantic judge. A three-attempt, bounded document-capture retry handles the
  Playwright race in which a planned click is still changing the document when post-action DOM
  evidence is collected; it neither invents an action nor changes a check.
- `self_verify_run.py`: explicitly enabled standalone research runner and machine-readable summary.
- `scripts/research_goal/build_self_verify_cases.py`: active CLI support is now limited to
  Vision2Web L2/L3, including mechanical static-versus-Vite runtime selection and hash binding of
  the isolated Vite dependency cache. Historical InteractWeb helper functions remain for artifact
  integrity checks but are rejected by the CLI.
- `scripts/research_goal/reconstruct_interact_first_runnable.py`: historical InteractWeb freezer;
  its CLI is disabled. The code is retained to explain and validate old snapshot provenance.
- `scripts/research_goal/materialize_self_verify_cases.py`: root-stage, node-local copy of only the
  already-admitted public program input so the non-root browser policy can read it. Released
  dependencies can be copied node-locally rather than traversed through a shared `/data` symlink;
  their complete non-cache file/symlink layout is content-hashed before and after copying. The
  Vision dependency manifest's 3,475 files are additionally verified before use; no evaluator or
  trajectory file is inspected. Provenance separately records the public case manifest, resolved
  dependency source, dependency mode, and tree identity. A previous variable-shadowing bug could
  mislabel the case manifest as the Vision dependency checksum file, although it did not alter the
  materialized program.
- `scripts/research_goal/preflight_self_verify_runtimes.py`: before model startup, mechanically
  starts every frozen program under the actual non-root UID and captures one screenshot. It uses no
  semantic judge or official evaluator and prevents a late filesystem/deployment failure from being
  mistaken for model behavior.
- `src/multimodalcode/research/events.py`: content-addressed evidence objects now include portable
  paths relative to the persisted browser root, rather than relying only on node-local `/tmp`
  paths.
- `src/multimodalcode/research/tools.py`: gives each isolated workspace a local writable
  `node_modules/.vite` optimizer cache while linking immutable released dependencies. Restore
  recreates the local cache and never writes into the source benchmark workspace. Its revision
  parser also rejects no-op and over-budget patches before workspace admission. Exact-edit
  admission can now be validated atomically without mutating the workspace, so a syntactically
  valid model patch whose `old` text is absent can enter the same finite contract-retry path rather
  than terminating the case; the framework never guesses or rewrites the missing text itself.
- `tests/test_self_verify.py` and `tests/test_self_verify_case_builder.py`: contracts for parsing,
  action execution, reset isolation, nested private-field rejection, evaluator exclusion,
  same-model identity, budgets, and disabled-baseline behavior.
- `scripts/research_goal/run_same_policy_smoke_qwen35_job.sh` and
  `submit_same_policy_smoke_qwen35.sh`: one-GPU ClusterX smoke with a mandatory real-browser gate,
  frozen file hashes, unique-output refusal, local vLLM, and persisted status/log/result records.
  The corrected 72-action global budget remains finite and reserves one initial execution plus two
  complete candidate replays while allowing up to 23 normalized planned actions.
- `scripts/research_goal/compare_self_verify_conditions.py`,
  `run_controlled_comparison_qwen35_job.sh`, and
  `submit_controlled_comparison_qwen35.sh`: reconstruct the exact initial and accepted versions and
  implement the post-generation baseline/generic/full diagnostic without invoking an
  official evaluator. Before inference, the job copies all full-method inputs to a node-local
  snapshot and stores a per-file SHA-256 manifest so a shared result directory cannot drift during
  the comparison. Its source-case gate keeps bounded full-method error outcomes whenever they have
  exactly one frozen plan and a final workspace bound to the accepted checkpoint; requiring a
  successful source status would create survivorship bias. Planless or unrestored cases remain
  excluded with a machine-readable reason. Conditions execute independently, so a generic patch
  contract failure cannot suppress baseline/full evidence. A post-r2 correction also retains the
  execution ID, relative result path, and actual action count when mechanical execution succeeds
  but the later semantic judgment contract fails; r2 itself was frozen before that correction.
- `scripts/research_goal/audit_controlled_comparison.py`: read-only structural audit of the frozen
  comparison. It recovers completed browser executions directly from persisted result artifacts,
  rather than misreporting zero actions when only the later semantic judgment failed.
- `tests/test_self_verify_comparison.py` and `tests/test_controlled_comparison_audit.py`: prove that
  rejected patches are ignored, accepted patches reconstruct the bound version, missing conditions
  do not fabricate paired scores, bounded source failures do not create survivorship filtering, and
  a completed execution remains measurable after a judgment-contract error.

The repository is not currently inside a readable Git worktree (`git status` and
`git rev-parse --show-toplevel` fail), so a Git diff cannot be used as proof of the complete change
set. This limitation must be resolved or the final file manifest must be verified independently.

## 6. Historical reproduction record

Commands involving InteractWeb or the mixed six-case pilot in this section are
archived and disabled. They are shown only to document how existing artifacts
were produced; they are not current instructions.

Audit derivation:

```bash
cd /data/miyapeng/mmcode/MultimodalCode
PYTHONPATH=src /data/miyapeng/miniconda3/envs/mmcode/bin/python \
  scripts/research_goal/audit_implementation_rhythm.py \
  --input-dir runs/research/active_visual_verification/observational-audit-010 \
  --output-dir runs/research/active_visual_verification/implementation-rhythm-audit-NEW
```

The command refuses to overwrite a non-empty output directory. Reproduction should therefore use
a new uniquely named directory and compare its hashes/content with `implementation-rhythm-audit-002`.

Reproduce the saved-run context-pressure audit with the exact local tokenizer:

```bash
/data/miyapeng/miniconda3/envs/vllm/bin/python \
  scripts/research_goal/audit_context_window_pressure.py \
  --tokenizer /data/miyapeng/model/Qwen3.5-9B \
  --output-dir runs/research/active_visual_verification/context-window-audit-NEW
```

This also refuses a non-empty output directory. Compare the result to
`context-window-audit-001/summary.json`; exact multimodal token usage remains unavailable by design.

Build the frozen leakage-safe smoke inputs (the command refuses a non-empty destination):

First reconstruct the isolated dependency cache for the one Vite-based Vision2Web case. This does
not install into `mmcode`, alter the generated workspace, or modify a benchmark evaluator:

```bash
cd /data/miyapeng/mmcode/MultimodalCode
source /root/.bashrc
proxy_on

cache=.runtime/research/vision2web_dependencies/57fd10d9b1bc13644edfd7585e4da89beb38efcc0208e575279a907e0ff07317
mkdir -p "$cache"
cp runs/vision2web_generation/qwen35-9b-openhands-official-v2/agents/litellm_proxy__Qwen3.5-9B/vision2web/official/frontend__401trucksource/workspace/package.json \
  "$cache/package.json"
(cd "$cache" && npm install --ignore-scripts --no-audit --no-fund)
(cd "$cache" && find node_modules -type f -print0 | sort -z | xargs -0 sha256sum \
  > node_modules.sha256)
```

The expected package, lockfile, and dependency-manifest hashes are recorded in Section 4. A new
active-scope Vision2Web manifest can be built without any InteractWeb input:

```bash
PYTHONPATH=src /data/miyapeng/miniconda3/envs/mmcode/bin/python \
  scripts/research_goal/build_self_verify_cases.py \
  --output-dir runs/research/self_verify_public_cases-NEW \
  --vision-generation-root \
    runs/vision2web_generation/qwen35-9b-openhands-official-v2/agents/litellm_proxy__Qwen3.5-9B/vision2web/official \
  --vision-data-root data/vision2web/extracted \
  --vision-dependency-cache-root .runtime/research/vision2web_dependencies \
  --vision-case frontend/chauffeurdriven \
  --vision-case frontend/401trucksource \
  --vision-case website/luxurycleaning
```

Verify that the opt-in feature is disabled by default:

```bash
PYTHONPATH=src /data/miyapeng/miniconda3/envs/mmcode/bin/python self_verify_run.py \
  --cases runs/research/self_verify_public_cases-004/cases.jsonl \
  --output-root /tmp/must-not-be-created \
  --model disabled-unused
```

The former mixed-pilot submitter is disabled. A new Vision2Web-first job must
consume only a newly built active-scope manifest.

After the job has written `results/summary.json`, run the evaluator-free structural audit into a
new directory:

```bash
PYTHONPATH=src /data/miyapeng/miniconda3/envs/mmcode/bin/python \
  scripts/research_goal/audit_self_verify_results.py \
  --run-root runs/research/NEW-RUN-LABEL \
  --output-dir runs/research/NEW-RUN-LABEL/structural-audit-NEW
```

Diagnostic attempts `same-policy-smoke-q35-001` through `-006` are intentionally retained and must
not be resubmitted. They isolated, in order, a missing frozen test path, non-root traversal of a
node-local `mktemp` root, a missing obligation bound, a mode-0600 public source, a read-only Vite
cache, and loss of Node fatal errors. They are framework diagnostics, not scores. The first run
with a complete six-case summary is `same-policy-smoke-q35-007`; its structural audit is under
`same-policy-smoke-q35-007/structural-audit-001/`. The corrected unique rerun is
`same-policy-smoke-q35-008` / `mmc-selfverify-q35-r8`. r8 was submitted before the
first-runnable reconstruction audit and points at final InteractWeb workspaces. Regardless of its
runtime outcome, it is excluded from scientific conclusions. The first leakage-safe rerun is
`same-policy-smoke-q35-009` / `mmc-selfverify-q35-r9`; it used one GPU,
`self_verify_public_cases-004`, and completed at 2026-08-22 19:47 UTC. It is retained as the
diagnostic that identified two remaining framework constraints. The corrected unique rerun is
`same-policy-smoke-q35-010` / `mmc-selfverify-q35-r10`; it was submitted at
2026-08-22 19:52 UTC with one GPU and completed at 20:19 UTC. The uniquely labeled
post-admission-retry rerun is `same-policy-smoke-q35-011` / `mmc-selfverify-q35-r11`; it was
submitted at 20:20 UTC with one GPU after r10 had released its GPU.

The former mixed controlled-comparison submitter is also disabled.

This comparison is explicitly post-generation: `baseline` keeps the same first complete program,
`generic` gives the same model one source-only “Please verify your work yourself and fix any issues”
instruction, and `full` reconstructs only patches admitted by the interactive loop. All three replay
the same frozen public-obligation plan. Their same-policy judgment is a diagnostic instrument, not
an official benchmark score.

Audit a completed comparison without invoking a model or evaluator:

```bash
PYTHONPATH=src /data/miyapeng/miniconda3/envs/mmcode/bin/python \
  scripts/research_goal/audit_controlled_comparison.py \
  --run runs/research/NEW-COMPARISON-RUN \
  --output-dir runs/research/NEW-COMPARISON-RUN/structural-audit-NEW
```

## 7. Unit and smoke tests

Passed audit and prototype tests:

```bash
PYTHONPATH=src /data/miyapeng/miniconda3/envs/mmcode/bin/python -m unittest -v \
  tests.test_self_verify \
  tests.test_self_verify_case_builder \
  tests.test_implementation_rhythm \
  tests.test_trajectory_observations \
  tests.test_context_window_audit \
  tests.test_self_verify_comparison \
  tests.test_controlled_comparison_audit \
  tests.test_research_harness.ToolExecutorTests
```

The current targeted local command collected 76 tests: 72 passed and four real-browser tests were
skipped in the development container because they are deliberately opt-in. All four passed under
UID 65534 in ClusterX before r9 vLLM startup. They cover state-reset isolation, offline-resource
blocking, post-navigation evidence capture, and explicit recording of browser-action failures. The non-browser suite additionally covers
action/schema parsing, private-input/evaluator exclusion, public program materialization, local
Vite-cache isolation, same-model identity, budget termination, checkpoint acceptance/rollback,
bounded/no-op revision rejection, exact full-version reconstruction for the controlled comparison,
and disabled-baseline compatibility. New tests additionally prove that system prompt artifacts and
post-Visual-Copilot revisions cannot enter an InteractWeb snapshot, reject a history without a
clean pre-visual runnable state and unsafe file actions, and bind materialization provenance to the
actual case manifest and dependency source. They also enforce one check per frozen obligation,
separate evidence/reference-image caps, complete replay budget reservation, and immediate stopping
on a repeated invalid action plan or repeated primary repair response. They additionally protect
the comparison's failure-case admission, checkpoint-restoration, and execution-after-judgment-error
accounting boundaries. `run.py --help`
remains successful. After r9, a regression test proves that a mechanically valid role-only locator such as
a unique unnamed checkbox is admitted, while a locator `name` without `role` is rejected. A second
regression test proves that an exact-edit localization miss leaves the workspace byte-identical,
is reported to the same policy once through the bounded contract retry, and only a subsequently
valid model-authored edit is admitted.

## 8. Initial benchmark comparison

The corrected controlled comparison is complete. Its final negative result is reported below;
the preceding smoke sequence is retained because it separates framework failures from model
failures. The first auditable six-case diagnostic, r7, completed two
cases and recorded four errors. Both complete cases satisfy the structural audit: one model identity
for every semantic stage, official evaluator excluded, typed mechanical actions, reset boundaries,
evidence-bound judgments, one frozen-plan digest across replay, and final workspace bound to the
accepted version. Across all six cases, the largest estimated text input to one call was 49,813
tokens; the vLLM log contained zero hard context-overflow errors.

| r7 outcome | Count | Meaning |
|---|---:|---|
| Structurally complete | 2 | One Vision2Web and one InteractWeb case reached a bounded terminal state |
| Plan-contract error | 2 | Qwen emitted more than eight normalized actions despite one schema retry |
| Repair-contract error | 2 | One no-op edit and one truncated over-large repair response |
| Accepted patch | 1 | Passed the internal monotonic gate; interpretation below is qualified |

The r7 audit is a framework result, not a benchmark-performance estimate. The action-budget, no-op,
patch-size, and required-locator contracts were tightened after inspecting these failures. A valid
rerun on `self_verify_public_cases-004` must show that these are fixed before the
baseline/generic/full runner is submitted. No official
Vision2Web or InteractWeb evaluator has been used in this development loop.

r8 persisted all six old-input rows (3 complete, 3 explicit errors, one internally accepted patch)
but is excluded twice over: its InteractWeb inputs are post-Visual-Copilot final workspaces, and its
ClusterX wrapper ended with exit 127 after the shared shell script was edited while Bash was still
reading it. The per-case summary exists, but neither its semantics nor wrapper execution is a valid
experimental condition. The retained diagnostic audit at
`same-policy-smoke-q35-008/structural-audit-001/audit.json` marks only 2/6 rows structurally
complete and, importantly, shows that the old action-budget formula did not reserve enough actions
for every declared candidate replay. It also exposed a fatal `page.content()` race during planned
navigation. r9 used the corrected reservation rule and bounded navigation-snapshot retry.

r9 persisted all six leakage-safe rows. Its evaluator-free structural audit is
`same-policy-smoke-q35-009/structural-audit-001/audit.json`: 1/6 cases was structurally complete,
5/6 ended with explicit errors, no patch was accepted, and no hard context overflow occurred. Four
errors came from a 15-action normalized-plan ceiling: the global 48-action budget correctly reserved
space for the initial execution and two possible full replays, but six independent checks consume
five deterministic reset actions before their semantic interactions. The remaining error rejected
`role=checkbox` without an accessible name even though the mechanical Playwright executor already
supports role-only locators. These are framework configuration failures, not evidence that the base
model cannot plan checks. r10 therefore changes only two demonstrated constraints: the total
browser-action budget is still finite but increases from 48 to 72 (23 normalized plan actions after
replay reservation), and role-only locators are admitted while ambiguity remains a mechanically
recorded action failure.

r10 persisted all six cases and its evaluator-free audit is
`same-policy-smoke-q35-010/structural-audit-001/audit.json` (source summary SHA-256
`cd34219bb443014cbf8cc42b3039774ee78f7c3cb6c6e4e398f6a6b6dc7c7e0f`). Three cases are
structurally complete and three end with explicit errors; all six preserve one-policy identity,
exclude the official evaluator, use valid mechanical action schemas and reset boundaries, and
reserve complete replay budget. The largest estimated text input to one call is 49,516 tokens and
no hard context overflow occurs. `000080_P-RAM` admits one first-round repair, but its second repair
and `frontend/chauffeurdriven` both produce syntactically valid exact-edit contracts whose `old`
text is absent; `website/luxurycleaning` cites invalid evidence IDs twice consecutively. The last is
a bounded model-contract failure and remains unchanged. The first two reveal a framework-interface
failure rather than a model-quality score. The post-r10 source checks exact-edit applicability
without mutation inside the existing single schema/contract retry budget. No r10 file or process
was changed; r11 freezes the corrected code under a unique label before comparison.

r11 validates that admission errors now stay inside the same finite repair contract: inapplicable
exact text leads to one same-policy retry, after which a second no-op or inapplicable response is
retained as a model failure. Its structural audit is
`same-policy-smoke-q35-011/structural-audit-001/audit.json` (source summary SHA-256
`73ffa59be6d76f261ae9a538e9660521b793c374ec683b7771b88a033b82fa9e`): 2/6 cases are
structurally complete, one patch is internally accepted, and no hard context overflow occurs. The
last InteractWeb case fails before planning because Node 22 cannot realpath a released dependency
through the non-root-unreadable `/data` directory. r10 had happened to run the same shared symlink,
so relying on that path is node-sensitive. r12 replaces only this environment boundary: it copies
the exact content-hashed dependency trees into node-local scratch and requires a six-case UID-65534
runtime preflight before vLLM startup. That preflight passed 6/6 with no semantic judge or evaluator.

r12 is the final smoke input to the controlled comparison. Its structural audit is
`same-policy-smoke-q35-012/structural-audit-001/audit.json` (source summary SHA-256
`a3ae2333555d0592070e5184035b166e0bce3676c71c3c13662c271d01edccd8`). It records 3/6
structurally complete cases, 3 explicit model errors, zero accepted patches, zero hard context
overflow, and a largest estimated single-call text input of 49,544 tokens. All six runtimes pass
the non-root preflight and no environment error occurs. Five cases are eligible for version
comparison: all three Vision2Web cases plus `000001_P-INT` and `000029_P-CON`; `000080_P-RAM` is
excluded because both action-plan attempts violate the locator contract and no plan exists. The
comparison r1 was `same-policy-comparison-q35-001` / `mmc-svcompare-q35-r1`, submitted with one GPU
at 2026-08-22 21:20 UTC. It was stopped at 21:27 after the first generic arm exhausted its bounded
typed-patch contract and the frozen runner incorrectly suppressed that case's independently valid
baseline and full arms. Its partial files are retained only as a protocol diagnostic. The corrected
runner records each arm's error and coverage independently and computes paired comparisons only
when both required judgments exist. `same-policy-comparison-q35-002` /
`mmc-svcompare-q35-r2` was submitted at 21:29 UTC; its six-runtime preflight passed before vLLM
startup and the job completed with runner exit 0 at 22:02 UTC. The summary is
`same-policy-comparison-q35-002/results/summary.json` (SHA-256
`b3f68b7b08b394edc766bb9f4715c9740eb59b0996127b528fb2150d7b0d9549`). Its independent
structural audit is `structural-audit-001/audit.json` (SHA-256
`f6674bd5d4af431307eba7fcd6c58266910b7a73f30f5f4e1a239e65d070e158`): all 5/5 selected cases
are structurally valid, the complete full-run input manifest is hash-bound, the official evaluator
is excluded, and no hard context error appears in the logs. The largest estimated text input to one
comparison call is 33,828 tokens; image-token usage remains unavailable.

| Frozen case | Benchmark | Baseline | Generic sentence | Full loop version | Paired improvement |
|---|---|---:|---:|---:|---|
| `frontend/chauffeurdriven` | Vision2Web | 4/6 | 4/6, code changed | 4/6, same hash as baseline | none |
| `frontend/401trucksource` | Vision2Web | 0/5 | contract error, no execution | 0/5, same hash | none on available pair |
| `website/luxurycleaning` | Vision2Web | judgment contract error | patch contract error | judgment contract error, same hash | unavailable, not scored as zero |

For the three active-scope Vision2Web rows, semantic coverage is 2/3 baseline,
1/3 generic, and 2/3 full. Baseline and full both pass 4/11 frozen checks on
rows with judgments; the only fully comparable three-arm row has no accepted
improvement. These are historical pilot diagnostics, not an official
Vision2Web score.

The two InteractWeb rows and the original five-row aggregate remain preserved
in the immutable machine result, but are deliberately omitted here and must not
enter current-paper aggregates.

No official numeric benchmark score is claimed. r12 admitted zero patches, so every selected full
version is byte-for-byte the baseline version; an official evaluator cannot establish a code-level
gain where the submitted program hash is unchanged. The generic arm changed three programs but is
only a diagnostic baseline, and none improved under exact frozen-plan replay. Invoking a private or
API-backed official evaluator would therefore add cost without rescuing the method-level result and
would not be fed into the policy in any case.

The InteractWeb-specific r9 example remains only in the underlying archived run
and is not used as an active success or failure case.

## 9. Success and failure cases

Observed native-trajectory successes and failures are listed in Section 3. In r7:

- The archived `000001_P-INT` case was formerly treated as a safety diagnostic. The policy added an input `name` attribute, replayed
  exactly the same plan, observed the same two failing and one passing checks, rejected the
  candidate, restored the initial hash, and stopped on the repeated failure signature.
- `website/luxurycleaning` is **not yet a clean semantic success**. The planner repeatedly used
  `getByRole(button, name=Book Now)` although the program exposed “Book Now” as an anchor. The repair
  added `role="button" name="Book Now"`; the click then executed and the correlated same-policy
  judge changed four checks to pass. This may improve accessibility, but it primarily repairs the
  policy's own locator and one click was reused as evidence for four distinct obligations. In
  particular, the form-validation obligation was judged from a screenshot without attempting an
  invalid submission. This is direct evidence of planner/verifier coupling, not proof that four
  user requirements were truly repaired.
- `frontend/chauffeurdriven` returned a no-op edit; `000057_P-CON` attempted a large unrelated CSS
  rewrite and exhausted the JSON contract; the other two cases exceeded the action budget. These
  are model/harness-interface failures and remain failures rather than being silently repaired.

The final r2 comparison makes the negative result more precise:

- `frontend/chauffeurdriven` is the cleanest paired generic failure. The generic prompt only adds
  an unused `id="app-container"`; all three arms remain 4/6. This is an admitted code change with
  no executable benefit, not a repair.
- `frontend/401trucksource` emits two near-output-limit generic responses but neither is a valid
  revision contract. Baseline and full both execute the frozen plan and remain 0/5; the generic arm
  is missing rather than being imputed as zero.
- `website/luxurycleaning` executes 17 actions in both baseline and full, including five mechanical
  action failures, but twice cites invented symbolic references such as
  `C2:step:1:execution_error` instead of supplied content-addressed evidence IDs. Both semantic
  judgments are therefore rejected. This is a verifier evidence-grounding failure, not an absent
  browser run; the structural audit preserves the distinction.

`markedjs__marked-2811` remains the strongest confirmed native same-terminal-check repair example.
Native Vision2Web has no visual loop in the observed baseline and FronTalk has
no runtime evidence.

## 10. What is currently effective and ineffective

Supported by active-scope evidence:

- Terminal execution can induce code repair in SWE-MM and some Vision2Web traces.
- Exact action replay provides a defensible but sparse repair label.
- The prototype can bind raw screenshots/DOM/accessibility/console evidence to one code version,
  have the same model emit pass/fail decisions, reject a non-improving patch, and restore the last
  accepted version.
- The reusable framework mechanisms are experimentally usable on the three frozen Vision2Web
  runtimes: frozen-plan execution, rollback, and version hashes are inspectable. A new
  active-scope run is still required before any method claim.

Not supported now:

- A same-policy pass label is not independent ground truth. The `luxurycleaning` case shows that
  planner, locator, judge, and repair errors can be correlated and can reward adaptation to the
  generated test interface rather than the underlying task.
- A generic later passing check does not prove that the original failure was fixed.
- More Verify calls do not imply reliable verification when selectors/check semantics are not
  stable or an external model supplies the conclusion.
- FronTalk’s repeated full-code output provides no executable regression certificate.
- Qwen3.5-9B did not demonstrate a useful same-policy repair loop on this pilot. r12 accepted 0/6
  patches and r2 found 0 paired improvements for either generic or full verification. The correct
  conclusion is lack of evidence for benefit, not that more samples will reveal one.
- The dominant failures are not context overflow: action/locator contract failure, invented
  evidence references, inapplicable exact edits, duplicate/no-op repairs, and long invalid generic
  revisions occur while the largest r12/r2 text requests are about 49.5K/33.8K tokens. Increasing
  the context window alone does not address them.
- Same-policy judgment coverage is itself incomplete (4/5 baseline/full and 3/5 generic), so the
  internal pass count is a diagnostic measurement rather than a trustworthy standalone reward.

## 11. Per-benchmark data construction and SFT/RL route

- **Vision2Web:** public prompt/reference assets, first runnable version, initial browser state,
  same-policy obligations/action plan, deterministic execution evidence, and accepted/rejected
  patches can form training records. Never use hidden evaluator criteria.
- **FronTalk:** retain earlier obligations and replay their executable checks after each new request.
  The current trace can supply requirement sequences and code candidates, but not pass labels.
- **SWE-MM:** use public visual issue input, repository localization, patch, and exact test replay.
  Do not force the website action-plan interface onto repository repair.

The pilot now diagnoses narrower prerequisites than “train the whole loop.” The minimal SFT should
use one policy and one typed trajectory grammar, but sample three decision boundaries: (1) initial
DOM/accessibility plus obligation to an executable finite plan, including negative locator pairs;
(2) raw executed evidence to pass/fail with exact content-addressed evidence IDs; and (3) one failed
check plus current source to a small applicable exact edit or an explicit `keep/stop`. Invalid JSON,
invented evidence IDs, absent `old` text, no-op patches, unrelated style changes, and regressions
from this pilot are valuable rejected examples. The supervision target is not an external judge's
free-form explanation; it is the typed action/evidence/patch record whose execution is retained.

After this small SFT, rerun the same frozen five cases before adding new ones. Expansion is warranted
only if plan executability, evidence-ID validity, applicable-patch rate, and exact-replay repair all
improve. If repair remains the isolated bottleneck, sample multiple patches for the same failed
executable state and optimize the true version improvement `S(P_{k+1}) - S(P_k)`. `S` must be an
allowed executable training oracle, not the policy’s own confidence or an arbitrary weighted
visual/function/length score. End-to-end RL is not justified by the present evidence: action
execution, evidence attribution, and a non-gameable reward are not yet reliable enough.

## 12. Limitations and next decision

- This pilot starts from an already generated complete program. It tests the claimed
  implementation–interaction–repair rhythm but does not yet prove that inserting the rhythm during
  first-pass generation is beneficial.
- The archived mixed smoke set is a structural test, not a benchmark estimate. No official numeric
  score was run or claimed. Current experiments must construct a new manifest containing only
  full Vision2Web before extending to FronTalk and SWE-MM transfer.
- Using one policy for planning, judgment, and repair removes an external smart-agent confound but
  creates correlated errors. Mechanical replay and a no-regression gate constrain that failure
  mode; they do not make the policy's semantic verdict ground truth.
- The browser executor can prove that an action occurred and bind evidence to a code version, but
  selectors generated by the policy can still be incomplete or brittle. r7 exposed both a missing
  locator on `fill` and a locator-induced repair. The parser now requires exactly one locator family
  for click/fill/select/hover, but semantic coverage still requires external post-hoc evaluation.
- Text token counts are estimated from characters because the current backend wrapper does not
  expose authoritative per-request usage. vLLM errors are checked separately for hard context
  overflow. Image-token cost therefore remains an explicit accounting limitation.
- **Current implementation order:** target full Vision2Web first, then FronTalk, then SWE-MM as a
  transfer setting. Do not add a replacement benchmark. Any capacity probe or learned method must
  use this order and new active-scope manifests; the former mixed five-case loop must not be rerun.
- Keep RL deferred until executable version improvement is a reliable reward on the active scope.
