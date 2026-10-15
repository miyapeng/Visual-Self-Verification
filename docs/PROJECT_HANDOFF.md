# Project handoff and cluster migration

Recorded on **2026-10-14** from the local code, paper, reports, data inventories,
and the research discussion. This is the starting point for a new collaborator
or AI session. It preserves decisions and evidence locations rather than the
full chat. Paths below are relative to the code repository unless stated otherwise.
**Training-design update: 2026-10-15.** Section 5 reflects the new segment-local
feedback proposal; the dated data inventories and validation results remain unchanged.
Paper revision: [`7a631ff`](https://github.com/miyapeng/Visual_Self_Verification/commit/7a631ff).
Both `paper.tex` and `main.tex` compiled successfully after this writing update.

## 1. Start here

The project studies **self-verification in multimodal coding agents**: whether
agents check the requirements of their own implementations, interpret the
resulting evidence correctly, and repair defects without introducing regressions.
The intended contribution combines a diagnostic evaluation with a trainable
improvement method. The active benchmarks are **Vision2Web and SWE-bench
Multimodal (SWE-MM)**.

| Item | Current state |
| --- | --- |
| Evaluation | Implemented protocol `evidence-diagnosis-repair-20261014`; six rates: **VTR, VC, EDR, CAR, RSR, CPR**. Program tests and selected runtime probes exist. Semantic judge accuracy and the new comparative study remain to be measured. |
| Model comparison | An audited model/data recommendation exists. The recommended panel has not been fully evaluated under the current protocol. |
| Training | ms-swift/verl integration and CPU execution tests exist. The paper's newer complete-continuation training design still requires changes to that integration. No GPU training result or improvement is established. |
| Paper | Evaluation and learning are separate sections. Four proposed findings are explicitly results-pending. |
| Resources | No funded API is currently available. Hardware for new Qwen inference/training has not been specified. |
| Migration | Git preserves source and documentation. Local datasets, trajectories, recovered images, code checkpoints, and judge caches require separate transfer. |

Read in this order:

1. This handoff and [repository instructions](../AGENTS.md).
2. [Current evaluation definitions](self_verification_evaluation.md) and
   [runner contract](VSV_SYSTEM.md).
3. [Model cohort and source audit](../reports/vsv_model_cohort_20261014.md).
4. The paper's `README.md`, `sec/2_evaluation.tex`, `sec/3_learning.tex`, and
   `sec/8_appendix.tex` in the separate paper checkout.
5. [Training implementation guide](VSV_TRAINING.md), using Section 5 below to
   distinguish the existing implementation from the latest paper design.

Older reports describe their dated protocols. In particular, historical
T/C/J/R, VC/CV/BDA/RS/CP/VCS, DA, and RVR results are **not** current scores.
Some older guides and diagrams still use those names. Use the current
specification above for definitions; use older reports for source evidence and
implementation history. Fresh current-protocol labels are needed, not renamed
columns in an old score table.

## 2. Repositories and working conventions

| Repository | Remote | Local location | Baseline inspected for this handoff |
| --- | --- | --- | --- |
| Code | [Visual-Self-Verification](https://github.com/miyapeng/Visual-Self-Verification) | `/mnt/shared-storage-user/miyapeng/mmcode/MultimodalCode` | `3bea92a` |
| Paper | [Visual_Self_Verification](https://github.com/miyapeng/Visual_Self_Verification) | `Visual_Self_Verification/` inside the code checkout | `d2a80bf` |

The hyphenated and underscored repositories are different repositories. The paper
directory is an independent Git checkout, **not a tracked code-repository
submodule**. Clone both when moving. A code commit does not upload the paper.
This documentation commit follows the code baseline listed above.

Important code milestones:

- `e04a175`: per-condition evidence provenance and separate original/reconstructed
  comparisons.
- `8bd87d8`: current evidence, diagnosis, and repair evaluation architecture.
- `56ac5a0`: initial ms-swift and verl integration.
- `5c1a4c4`: shared benchmark tooling and repository organization.
- `4238d7b`: check-round extraction with repair and recheck links.

Use English for authored code, prompts, documentation, and paper text. Preserve
original evidence verbatim. Reuse the existing parser, `JudgeClient`, caches,
version manifests, and runtime adapters. Keep records small and use original IDs
for links; avoid a second parser, snapshot system, agent framework, or judge stack.
Do not add task-name rules to make one demonstration pass.

Routine reads, reversible edits, tests, and task-related network access are
authorized. Ask before a specific destructive action, including deleting old
results or discarding edits. The user requests GitHub sync after changes: inspect
remote and local work, validate the change, then commit and push only the intended
files. Never stage local data or the nested paper repository accidentally.

The writing target was originally discussed as CVPR 2027; later explicit writing
instructions and the paper's current `AGENTS.md` say CVPR 2026. Confirm the final
venue/year before submission. Preserve the current CVPR template, style,
preamble, review settings, and section order meanwhile. Use inline
`\textbf{Title.}` lead-ins with default template indentation, not `\paragraph`
or added `\noindent`. Keep evaluation and learning separate, one section file
each, and one appendix. The user prefers concise academic exposition with the
essential SFT/GRPO equations in the main text and operational detail in the appendix.

## 3. Research decisions and the current evaluator

### Units and evidence

Analysis starts after the earliest **complete initial implementation attempt**;
this is a completeness boundary, not a correctness or successful-build test.
A maintenance task starts from the first complete proposed change to its supplied
project. No initial implementation and an implementation with no checks are
different cases.

The paper represents a verification episode as `e_b = (v_b, sigma_b, v_{b+1})`:
`b` indexes episodes, the endpoints are implementation versions, and `sigma_b`
contains checking, observations, judgments, and ensuing modifications. The first
check after a modification starts the next episode; consecutive edits before
that check belong to one transition. Without edits, the endpoints are equal.
Several independent local assessments can occur inside one episode.

The extractor retains these smaller assessments for scoring and links actual
edits and later checks by ID. Existing JSON uses `episode_id` for these local
rounds; **that field is not automatically the paper's version-transition index
`b`**. Keep the distinction when reading records or implementing a paper export.
Do not merge all checks between distant timestamps into one scoring observation.

- Pair tool calls and actual returns by native call ID. Keep original ordinals.
- Retain failed attempts, retries, absent judgments, and image references whose
  bytes are currently missing. File availability is separate from historical receipt.
- Link screenshot capture, image Read, and actual model image receipt. Batch
  captures can support several assessments through shared references.
- An image belongs to its capture-time state, even when read after an edit.
- A shared plan can supply context to later checks; splitting a plan does not
  make each check invalid merely because its setup is elsewhere.
- Reading a task prototype is requirement understanding, not self-verification.
- Retain textual and visual checks. A scored assessment–requirement pair is
  **visual** when its cited evidence includes an actual own-artifact image input;
  otherwise it is textual. There is no `mixed` category. A whole round containing
  an image does not make all its conditions visual.
- Related modifications and rechecks are links, not proof that a repair succeeded.
  A final homepage screenshot cannot establish a Winston-page recheck.

### Evaluation sequence

1. **Freeze requirements.** Build one catalogue from task instructions and
   references before inspecting model outputs. Freeze IDs, tolerances, and
   acceptance procedures across agents. Resolve task/reference conflicts here.
   Requirements are independently failing conditions, not every decorative detail
   and not an entire page containing unrelated behaviors. Visual and interactive
   requirement subsets may overlap.
2. **Recover checks and scope.** Import native events, construct small candidates,
   and use batched constrained annotation to select/group checks and link
   responses, edits, and rechecks. Locate the initial implementation boundary.
   Models cite existing IDs; the program checks pairing, chronology, and versions.
   Extraction does not require the official `workflow.json`.
3. **Label coverage evidence.** For attempted conditions, assess full/partial/none
   scope, method suitability, and evidence sufficiency. Only full valid checks
   cover a requirement. Discovering an actual defect can be a valid check; a
   failed screenshot without usable feedback cannot establish visual coverage.
   Partial coverage currently earns no fractional credit.
4. **Establish diagnosis opportunities independently.** The observation judge
   receives the fixed catalogue and contemporaneous observations, with agent
   judgments and acquisition target selections withheld. It labels each condition
   pass, fail, or not observable. Then a separate verdict call maps the agent's
   original responses to those conditions. This includes observable defects the
   agent never mentioned. A correct error judgment must match object and symptom.
   Future edits and acceptance observations cannot justify an earlier diagnosis.
5. **Accept versions before and after repair.** Link targets from actual findings
   and edits, restore the relevant versions, and apply the same fixed procedure.
   Executable assertions decide exact conditions; a multimodal judge handles
   semantic observations. One shared acceptance table per version supplies RSR
   and CPR. Later agent rechecking is useful evidence, not a prerequisite for
   independent acceptance.
6. **Compute rates in Python.** Models return labels and references. The program
   computes rates, support counts, provenance breakdowns, and task/model averages.

| Question | Metric | Numerator / denominator |
| --- | --- | --- |
| RQ1: Evidence acquisition | VTR — Verification Trigger Rate | Runs with an executed post-boundary check / runs reaching an initial implementation. Failed executed attempts count; a plan alone does not. |
| RQ1 | VC — Verification Coverage | Distinct requirements receiving a full valid check / fixed catalogue requirements. Repetition cannot increase coverage. |
| RQ2: Evidence interpretation | EDR — Error Detection Rate | Correctly identified observable defects / independently decidable violated assessment–requirement pairs. |
| RQ2 | CAR — Correct Acceptance Rate | Correct affirmative judgments / independently decidable satisfied assessment–requirement pairs. |
| RQ3: Repair reliability | RSR — Repair Success Rate | Confirmed failing repair targets that pass afterward / attempted transition–target pairs with a failing baseline. |
| RQ3 | CPR — Correctness Preservation Rate | Previously passing non-target conditions still passing / passing-before non-target conditions on those transitions. |

CV is an internal validity label, not a seventh metric. RVR/VCS are no longer
headline scores. EDR/CAR have visual/text breakdowns; VC has requirement-type
breakdowns. Missing or inconclusive agent judgments earn zero on **decidable**
conditions. A requirement outside the observed state remains uncovered in VC
without becoming an invented diagnosis opportunity.

The goal is a completed run with **point estimates and counts**, not bounds or
silently omitted agent mistakes. Missing image bytes, unavailable versions, API
failure, and evaluator timeouts are input/execution failures to resolve before
publication. They cannot be forced into a binary agent label. Zero opportunities
are `not_applicable`, not 0% or 100%. A passing output with an unknown baseline
does not establish successful repair under the current definition; earlier
permissive pilot decisions are superseded.

### Original versus reconstructed observations

Reconstruction replays recorded actions and does not run the coding model again.
It can recover useful pixels and code checkpoints, but does not establish
historical pixel equivalence or invent an original image receipt.

The program derives `evidence_origin` per condition. Any cited reconstructed
artifact image makes that condition reconstructed; unrelated images and task
references do not change other conditions. Reports retain both origin groups
and their opportunity counts. Model-level comparisons are separated into
`original` and `reconstructed_observations` cohorts, with common completed tasks
and consistent evidence modes across repetitions. Recorded text can remain
original evidence within a run that also uses restored screenshots.

Average repetitions within a task/model first, then tasks equally. Record excluded
tasks and opportunities, not only percentages. The text-only proxy for missing
screenshots is an archived exploratory mode, not a substitute for the formal
comparison. Independent acceptance captures establish revision outcomes; they
are not evidence that the agent historically saw or diagnosed those states.

### Implementation map and judge configuration

| Location | Role |
| --- | --- |
| `src/multimodalcode/vsv_eval/native.py`, `benchmarks.py` | Native import, task/source binding, evidence audit |
| `episodes.py`, `checks.py` in that package | Candidate construction, call pairing, local rounds and relation references |
| `scope.py` | Initial implementation boundary and scored scope |
| `catalogue.py`, `preparation.py` | Frozen requirements and acceptance bindings |
| `check_stages.py`, `evaluation.py` | Acquisition, validity, independent observation, original verdict comparison |
| `states.py`, `acceptance.py`, `artifact_acceptance.py` | Shared version acceptance and browser/command probes |
| `version_restore.py`, `archive_replay.py` | Recorded-version and image reconstruction |
| `metrics.py`, `system.py` | Arithmetic, readiness, experiment freezing, comparison and reporting |
| `judge.py` | Existing client, profiles, cache, raw requests/responses, call accounting |
| `scripts/vsv/` | Current shared CLI entry points |

The affordable default is profile **`gemini3flash`**, model
**`gemini-3-flash-preview`**, in `configs/vision2web/vsv_protocol.json`.
The configuration's historical directory does not restrict it to Vision2Web.
Coding models, the diagnosis judge, the teacher, and the trainable student are
different roles. The final teacher and a calibrated judge setup remain to be frozen.
GPT-5.4 and Gemini 3.1 Pro Preview were considered too costly for routine judging;
earlier runs using them are development evidence, not a requirement for this study.

One boundary annotation is needed per trajectory; each judgment-time prefix uses
up to four stage calls (`*_vc`, `*_cv`, `*_observation`, `*_bda`). Internal names
retain history; they do not define the reported metric names. Acceptance is
shared by version/evidence group. Extraction, reference mapping, planning, and
GUI grounding have their own calls. Read `model_calls.json` for measured calls
and cache hits rather than assuming a constant cost per trajectory.

`VSV_SYSTEM.md` also describes the `in-session` provider: it exports exact request
packets for a live AI to annotate, validates response hashes and schemas, and
resumes through the same interface. It is not a free API that recursively calls
Codex, and such annotations do not establish an independently calibrated judge.
Keep the annotation provider/model and evidence record explicit.

## 4. Proposed findings and what supports them

These are questions for the frozen study, **not established population results**.

| Proposed finding | Required evidence |
| --- | --- |
| Agents initiate verification but leave visual and interactive requirements unchecked. | VTR versus valid VC by requirement type, with missing-scope examples. VTR is initiation, not the frequency of checks. |
| Agents miss visual defects despite sufficient evidence. | Independently established observable defects and visual EDR, accompanied by CAR to expose false alarms. |
| Correct diagnosis does not reliably produce a successful repair. | RSR on attempts following correct visual diagnoses, plus counts of diagnosed defects receiving no attempt. |
| Local visual fixes can introduce regressions elsewhere. | Joint target recovery and non-target regression on the same transition, alongside CPR. A regression alone does not show a successful local fix. |

The [Vision2Web audit](../reports/vsv_diagnosis_vision2web_20261008.md),
[SWE-MM audit](../reports/vsv_diagnosis_swemm_20261008.md), and
[synthesis](../reports/vsv_diagnosis_synthesis_20261008.md) contain exploratory
examples. Small convenience samples motivate questions; they do not prove the
four findings or causal benefit from visual feedback. Do not select tasks or
tune acceptance rules to obtain the expected conclusions.

The historical [Qwen3.5-9B audit](../reports/qwen35_claude_official_self_check_audit_20260912.md)
reports 193 tasks, 192 normal completions, one application-browsing case, and no
actual application image inputs. Reference-image reads and HTTP 200 responses
are not visual self-verification or official task success. The current local
archive contains only 86 distinct tasks from its two main saved generation runs.
Do not infer that all 193 historical raw records have migrated.

The [Classic Clashes probe](../reports/vsv_confounder_probe_20260912.md) found that
correct tool examples elicited four application-image inputs and a local
check/edit/recheck sequence, ending in a timeout. This demonstrates an actionable
tool-use obstacle in that case, not a general improvement or isolated visual gain.

## 5. Learning method: accepted direction and implementation gap

The target student is **Qwen3.5-9B**. Use **ms-swift for SFT** and **verl for
GRPO**, with the evaluator supplying evidence-based labels and rewards. The
latest paper names the stages **Student-Conditioned Verification Distillation**
and **Student-Conditioned Verification Optimization**.

Both stages condition on a student's native prefix ending at its initial
implementation, including actual images, and a corresponding restorable runtime
checkpoint. The prefix is fixed within an example/group and refreshed from the
current student between collection/optimization rounds.

- **SFT:** a strong multimodal teacher continues from that state through
  termination. Retain or reject the whole continuation using fixed acceptance
  and supported judgments. Apply loss only to teacher assistant tokens; the
  student prefix, tool returns, and image observations remain conditioning inputs.
- **GRPO:** sample complete continuations from independent restorations of the
  same prefix/checkpoint and budget. The policy chooses its checks and edits.
  Normalize final task scores within the shared-prefix group to obtain
  `A_task`. Add signed evaluator feedback only to the generated tokens belonging
  to the corresponding local segment: **`A_hat = A_task + lambda * A_ver`**.
  Use this fixed coefficient in the standard clipped policy objective, with
  suffix-only assistant and KL masks. No value model is introduced; `lambda`
  is the only additional mixing parameter, and zero recovers task-only GRPO.
- **Local feedback:** annotate evidence acquisition, interpretation, and revision
  segments after sampling the complete suffix. Core token sets do not overlap;
  inseparable mixed messages receive one composite score. Episode counts can
  differ or be zero, with no cross-trajectory segment alignment. Feedback is
  bounded in `[-1,1]`, with evaluator-defined condition scope and deduplication.
  Informative checks and supported judgments are positive, unsuitable checks
  and contradicted judgments negative, and redundant evidence or missing
  conclusions neutral. Each repair compares its own actual before/after versions:
  new recovery credit minus a unit penalty for any new regression. The paper's
  appendix specifies how repeated repairs and self-introduced regressions are handled.
- Local scores are **not centered across segments or accumulated backward**.
  The October 14 normalized subsequent-event-reward proposal is superseded.
  Local feedback can remain informative when final task scores tie. It is a
  process-supervision coefficient, not a value-based advantage estimate, and
  does not guarantee a positive combined update for every correct segment.
- Do not prescribe separate evidence/diagnosis/repair rollout quotas or change
  the actor loss to a sum of per-stage training objectives. The latest design
  replaced that earlier proposal. Do not sum the six benchmark percentages as
  the training reward.
- Prefix events receive no continuation credit. Repeated checks cannot create
  duplicate coverage reward. Revision accounting must address break–repair cycles.
  The paper specifies these details; effectiveness and resistance to exploitation
  remain to be tested.

**Existing code implements the preceding local-rollout design.** It already has
native handoffs, restoration, teacher-message masks, checkpoint groups, scalar
rewards, and framework adapters. It still uses decision kinds/per-kind weights
and message-level teacher filtering. Aligning it with the latest paper requires:

1. Initial-implementation prefix collection and whole-continuation teacher filtering.
2. Complete suffix rollouts grouped by prefix/checkpoint, refreshed from the student.
3. Native multimodal context plus suffix-only response and KL masks.
4. Nonoverlapping segment assignments to native generated tokens, then addition
   of local feedback to the standard task-outcome advantage tensor. Do not feed
   local scores through the outcome-GRPO reward sum or recenter the combined tensor.
5. Validation of the real Qwen processor/template, behavior log probabilities,
   masks, reward accounting, and an actual GPU optimizer update.

The October 15 change is a writing revision, not a training-code implementation
or measured result. It preserves the SFT loss and GRPO clipped loss while
replacing the old cumulative `C/D/Q` reward accounting. The paper records the
token-weighting effect of segment length and plans a control that broadcasts
the same mean local feedback across the suffix to test attribution.

Code: `src/multimodalcode/training/{handoff,environment,collector,reward,verl_integration,cli}.py`.
Commands: `scripts/training/{prepare.py,check_upstream.py,smoke.py,train_sft.sh,train_rl.sh}`.
Existing executable training restoration covers resettable Web applications;
SWE-MM evaluation support is not automatically a live SWE-MM training environment.
Primary method evaluation must begin from the original task; fixed-prefix probes
are auxiliary. Separate training tasks from benchmark evaluation and reward tuning.

Pinned framework sources in [upstream_sources.json](../configs/training/upstream_sources.json):

- [ms-swift v4.0.4](https://github.com/modelscope/ms-swift/tree/ba4631cd60e744e0c1a709068758480abfc429a5),
  commit `ba4631cd60e744e0c1a709068758480abfc429a5`.
- [verl](https://github.com/verl-project/verl/tree/fc72e2f384212470868a9928b62baedb9bcc3ff0),
  commit `fc72e2f384212470868a9928b62baedb9bcc3ff0`.

The initial integration's [validation report](../reports/training_integration_20261009.md)
records 343 combined tests and 17 final focused tests passing, a real Chromium
restore/edit/recheck smoke, and zero API calls. Scripted fixture continuations are
not teacher-produced training data. No distributed training or improvement was
measured. Closed-model harness improvements remain a research direction, not a
validated method implementation.

## 6. Model cohort to carry forward

The [2026-10-14 cohort audit](../reports/vsv_model_cohort_20261014.md) is the detailed
source of record. Its **recommended**, not yet frozen, design has 10 Vision2Web
models and eight SWE-MM models: **13 distinct identities**, with five intended
shared models subject to the Opus routing check. A ten-model exact intersection
was not found in the inspected archives.

| Model/family | Vision2Web recommendation | SWE-MM recommendation |
| --- | --- | --- |
| Claude Fable 5 | Local Claude Code archive | Public OpenHands archive |
| Claude Opus 4.8 | Local ID `claude-opus-4-8-cc` | OpenHands `claude-opus-4-8`; confirm alias equivalence |
| OpenAI | GPT-5.6-Sol / Codex, ID `gpt-5.6-sol-vibe` | GPT-5.5 / OpenHands; a different model |
| Google | Gemini 3.7 Flash / Gemini CLI | Gemini 3.5 Flash / OpenHands; a different model |
| Moonshot | Kimi K3 / Claude Code | Kimi K2.6 / OpenHands; a different model |
| Z.ai | GLM-5.3-Flash / Claude Code | No matching visual archive identified |
| DeepSeek | DeepSeek-V4-Flash-Vision-Exp / Claude Code | No matching visual archive identified |
| Qwen | Qwen3.5-4B, 9B, 27B | The same three released post-trained dense checkpoints |

Use [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B),
[9B](https://huggingface.co/Qwen/Qwen3.5-9B), and
[27B](https://huggingface.co/Qwen/Qwen3.5-27B), rather than `-Base` models.
[35B-A3B](https://huggingface.co/Qwen/Qwen3.5-35B-A3B) is an optional MoE comparison,
not another dense scaling point. Its active parameter count is not its weight
storage requirement. No local throughput or GPU-memory feasibility claim has
been established. The [Swift Qwen3.5 guide](https://swift.readthedocs.io/en/v4.0/BestPractices/Qwen3_5-Best-Practice.html)
documents support, not validation of this project's long agent workload.

Optional archives include Opus 5, GPT-5.6-Luna/Terra, MiniMax-M3, and
Qwen3.5-Flash. The API model Qwen3.5-Flash has no verified mapping to a released
4B/9B/27B checkpoint. Indexed GLM-5.1 and DeepSeek-V4-Pro SWE-MM entries declare
no vision support and are not substitutes for the visual models above.

Freeze a common task cohort within each benchmark before examining scores.
The audit proposes an initial 40 tasks per benchmark, not an approved final
sample size. Retain failures and no-check runs, stratify Vision2Web L1/L2/L3,
and report support and exclusion counts. Archived comparisons describe
**model + harness + configuration**, since harnesses and versions differ.
Use a frozen harness and budgets for new Qwen size/method comparisons. Do not
equate a first attempt with a critic-selected third attempt. More model names
cannot compensate for few observable defects or repair opportunities.

## 7. Data locations and public sources

### Vision2Web

- Local corpus: `/mnt/shared-storage-user/miyapeng/datasets/vision2web-leaderboard/`.
- Public source: [Vision2Web-Leaderboard](https://huggingface.co/datasets/zai-org/Vision2Web-Leaderboard/tree/dd464e60b02f836e768fd9d21baa49c97a53edf6),
  revision `dd464e60b02f836e768fd9d21baa49c97a53edf6`.
- Original task materials: `data/vision2web/extracted/{webpage,frontend,website}/`.
  Preserve `data/vision2web/{manifest.json,checksums.sha256,instance_ids.txt}` and
  the agent-visible/private distinction.
- Source code: [zai-org/Vision2Web](https://github.com/zai-org/Vision2Web).
  Local full-clone pins are in `evaluate/benchmarks/sources.json`; the frozen
  evaluator in `evaluate/vision2web/` has separate provenance. Do not substitute
  a newer full clone for that frozen evaluator silently.

The local corpus has **10 submissions × 193 result JSONs = 1,930 records**;
each submission contains 100 webpage/L1, 66 frontend/L2, and 27 website/L3 tasks.
Result JSON bytes total 3,689,421,908, about 3.44 GiB. Typical location:

```text
<submission>/<level>/<task>/results/<task>.json
```

Keep `_trajectory_index.csv`, `_download_manifest.json`, `_download_summary.json`,
and `_download_completed.jsonl` with the data. Seven submissions have structured
conversation records; the three Codex submissions instead have terminal logs.
An empty `conversation` field does not mean there is no trajectory. The Codex
parser/image-receipt/truncation audit is still required before scoring that panel.
Generation `success` means execution finished, not official task correctness.
This download excluded generated website source/assets; fetch them from the
upstream release or reconstruct them where the record supports it.

Recovered images belong beside their tasks:

```text
<submission>/<level>/<task>/reconstructed/
  reconstructed_observations.json
  images/
```

Confirmed local sidecars: Opus 4.8 `frontend/1daycloud` (14 captures),
`frontend/blackbeltacandelectric` (9), `webpage/alberta_alis` (7), and
`frontend/finalsite` (7, older format); GLM-5.3-Flash `frontend/finalsite` (12).
These five sidecars are not completion of the full model corpus. Keep their
source hashes and detailed replay outputs. See [image reconstruction](VSV_IMAGE_RECONSTRUCTION.md)
for replay mechanics and the current protocol for scoring semantics.

Project-local Qwen3.5-9B generation roots:

```text
runs/vision2web_generation/qwen35-9b-claude-code-official-full-0904/
runs/vision2web_generation/qwen35-9b-claude-code-official-resume-0908/
```

Each contains `agents/Qwen3.5-9B/vision2web/claude_code/official/` task trees.
There are 44 and 43 native-event task directories, respectively; their union is
86 tasks (11 L1, 64 L2, 11 L3). Preserve native `claude.events*.jsonl`, capture
records, `trajectory.json`, results, and development/workspace versions together.

### SWE-MM

- Public trajectories: [OpenHands index results](https://github.com/OpenHands/openhands-index-results/tree/3015ac612e7196f428e6e8a3948965d32d9a3331),
  commit `3015ac612e7196f428e6e8a3948965d32d9a3331`.
- For each `results/<model>/`, `metadata.json` describes the configuration,
  `scores.json` provides `full_archive` URLs, and
  `instance_results/swe-bench-multimodal.json` contains outcomes, **not histories**.
  Archives contain native conversation members and/or `output*.jsonl` histories.
- Task data: [SWE-bench_Multimodal](https://huggingface.co/datasets/SWE-bench/SWE-bench_Multimodal).
  Older native archive metadata uses `princeton-nlp/SWE-bench_Multimodal`.
  Preserve the recorded revision and split rather than silently substituting the
  latest dataset. See [official version information](https://www.swebench.com/multimodal.html).
- OpenHands selection: [curated instance IDs](https://github.com/OpenHands/benchmarks/blob/main/benchmarks/swebenchmultimodal/resolved_instances.txt).
  The inspected 68-task archive is a curated **dev** subset, not the full test
  set or newer 480-task v2 evaluation. Some native records use three critic runs.
- Local task material: `data/swe_mm/dev/`, including `freeze_manifest.json`,
  `instance_ids.txt`, `checksums.sha256`, image manifests, `agent_visible/`, and
  `evaluator_private/`.
- Imported samples: `runs/vsv_eval/cross_benchmark_20261008/cases/`, notably
  `processing__p5.js-5970/` and `Automattic__wp-calypso-27090/`.
- Native archive samples and metadata: `runs/vsv_eval/model_cohort_20261014/`.
  About 21 MiB of bounded audit artifacts were retained, not full model archives.

The five recommended archived SWE-MM models share **67 indexed task IDs**.
Gemini 3.5 Flash lacks the `Automattic__wp-calypso-35531` sidecar entry; inspect
the actual archive before interpreting this as no attempt. HTTP-accessible archive
links and bounded samples establish availability, not full-corpus completeness.
Task input images are not evidence of artifact inspection.

Four `runs/swe_mm_official/qwen35-9b-mini-official-dev*` directories contain
50 native mini-SWE-agent trajectories for 47 distinct tasks, with 27 tasks in the
curated OpenHands list. Model ID is `hosted_vllm/Qwen3.5-9B`; 48 runs ended
`Submitted` and two `BadRequestError`. Preserve retries and input-fix identities.
This is a different harness from OpenHands and not a controlled Qwen size study.

Other inspected sources: [SWE-bench experiments](https://github.com/SWE-bench/experiments/tree/40f164d5b8f1d249bf95a6df8b74b577fd8e519d/evaluation/multimodal)
did not expose usable trajectory assets in the inspected metadata;
[OpenHands/openhands-index](https://huggingface.co/datasets/OpenHands/openhands-index)
mirrors outcomes rather than a second complete history corpus. This does not
establish that no additional author logs exist.

### SmartRecruiters reference case

Preserve the entire fixture, not only its JSON:

```text
data/vision2web/vsv_eval_fixtures/frontend__smartrecruiters__claude-opus-4-8__self_verify/
  trajectory/run.json
  final_workspace/
  agent_visible/
  evaluator_private/
  scorer_task_root/
  manifest.json
  checksums.sha256
```

The source has 356 events and SHA-256
`b79e6b0600907598b04070ed7067891fede37a691a989a300c75e6171833bc2c`.
Existing annotations are at
`runs/vsv_eval/verification_rounds_smartrecruiters_20261002/verification_rounds.json`.
The [structural audit report](../reports/vsv_protocol_20261013.md) uses event 191
as a source-reading boundary reference, not a saved boundary-model label. Of 25
existing rounds, 22 follow it: **eight with image inputs and 14 without**.

| Visual assessment | Core ordinals | Image-input ordinal |
| --- | --- | ---: |
| Initial homepage check | 231–237 | 235 |
| Homepage retry/recheck | 247–259 | 252 |
| Pricing | 275–278 | 276 |
| About | 279–282 | 280 |
| Winston | 283–286 | 284 |
| Customers | 287–292 | 290 |
| Careers | 293–298 | 296 |
| Final homepage check | 340–346 | 344 |

The batch capture at 272 supplies several pages by reference. The failed attempt
at 229–230 has no image input and is retained without creating a visual diagnosis
opportunity. Eight is a source-audit result, never an extractor constant.
The Winston finding links to edit 308; the final homepage check is one later
unit and cannot prove Winston was visually rechecked. The raw audit is in
`runs/vsv_eval/protocol_audit_20261013/smartrecruiters/structural_audit.json`.

The legacy 27-requirement catalogue has a homepage button-wording conflict:
task text says “Get started”, while the reference says “Explore the Benefits”.
Resolve the fixed specification consistently before new scoring. Old scores
from repeatedly revised prompts/tolerances are development results, not a
frozen cross-model comparison.

### Other benchmarks and reference paper

3DCodeBench and GameDevBench adapters/runtimes remain in the repository but are
outside the active study. Preserve useful probes; do not resume expansion by
default. Historical sources are [3DCode agent logs](https://huggingface.co/datasets/YipengGao/3DCode/tree/main/3DCodeBench_ModelLogs/agent_logs)
and [GameDevBench results](https://github.com/waynchi/gamedevbench/tree/main/results).
GameDevBench results alone do not provide full verification trajectories.

Writing reference: [Can Terminal Agents Trust Their Own Verification?](https://arxiv.org/abs/2609.38812).
Local copies are `Visual_Self_Verification/Can Terminal Agents.pdf` and
`Visual_Self_Verification/ref/arXiv-2609.38812v1.tar.gz`; these are ignored binary
artifacts. Learn its exposition and diagnostic organization; the project adds
multimodal evidence, local assessments, and revision/preservation analysis.
Do not transplant its reported population statistics into this project's findings.

## 8. Cluster migration and first commands

### What must travel separately from Git

| Priority | Local material | Why it matters |
| --- | --- | --- |
| Essential | `data/vision2web/`, `data/swe_mm/dev/` | Task versions, references, private evaluator inputs, complete SmartRecruiters fixture and checkpoints |
| Essential | `/mnt/shared-storage-user/miyapeng/datasets/vision2web-leaderboard/` | Archived trajectories, download manifests, and task-local recovered images |
| Essential | Selected `runs/vsv_eval/` inputs and outputs | Extraction annotations, judge requests/responses, frozen catalogues, version manifests and **all files they reference**; preserve original provenance |
| Essential | The Qwen Vision2Web roots and SWE-MM roots listed above | Locally generated native trajectories and implementation histories not recoverable by cloning public code |
| High | `runs/vsv_eval/{model_cohort_20261014,protocol_audit_20261013,evidence_origin_20261014_sfhk8cl1,cross_benchmark_20261008,runtime_validation_20261008}/` | Source inventory, provenance audits, native samples, reconstruction and runtime evidence |
| High | `.local/runs/training-smoke-20261009-validated/` | Reproducible scripted restoration/export smoke artifacts, not training demonstrations |
| Rebuild or copy deliberately | Full `evaluate/benchmarks/` clones, `.local/upstream/training/`, Docker images and dependency caches | External source and runtime dependencies; pins are tracked, installed contents are not |
| Separate private transfer | Credentials and required service configuration | Never include keys, tokens, or raw credential-bearing controller jobs in Git or this document |

Do not assume every historical `runs/` directory is dispensable. Preserve the
referenced dependency closure before selecting files to leave behind. No deletion
or data transfer was performed by this handoff task. No weights need to be
uploaded to the code repository.

Absolute paths appear in historical records. Keeping the original storage mount
path avoids unnecessary rebinding. If the new root differs, create new execution
manifests/bindings and validate source hashes; do not globally rewrite original
trajectory bytes or cached evidence to make paths resolve.

### Existing external backup work

Two transfer systems exist outside this repository:

| Local runbook/scripts | Destination | Recorded prefix |
| --- | --- | --- |
| `/mnt/shared-storage-user/miyapeng/hf_migration_myp/` | [MickeyMouse2024/ailab_myp](https://huggingface.co/datasets/MickeyMouse2024/ailab_myp) | `backups/pt-migration-20260921-batch2` |
| `/mnt/shared-storage-user/miyapeng/hf_migration/` | [MickeyMouse2024/ailab_fyy](https://huggingface.co/datasets/MickeyMouse2024/ailab_fyy) | `backups/pt-migration-20260921-batch1` |

Read each `RUNBOOK.txt`, current selection/plan, and restore script before use.
The saved `progress_20261010.json` records show 1,436/1,522 and 740/958 shards,
respectively, at their October 10 observation time. These are **old snapshots**,
not a new remote completeness check. Plans changed during migration, so old log
denominators can differ. Confirm the current plan, uploaded hashes, and coverage
of this project's latest files before relying on either backup. Keep the transfer
scripts, plans, and environment export records with the private migration bundle.
Large compressed plan files should be inspected with bounded/streaming reads.

Earlier transfer failures involved interrupted source reads and Hugging Face LFS
TLS/upload retries. A 100% upload progress bar did not itself prove remote commit
and hash verification. The transfer/restore tools are separate from the evaluator;
this handoff does not restart them or claim that the backups are complete.

### Recreate the working environment

Clone code and paper separately into fresh destinations. Recreate environments
from specifications rather than copying interpreter directories with old absolute
links. Existing Conda environments are under `/root/miniconda3/envs/`:
`vision2web`, `swemm`, `3dcodebench`, and `gamedevbench`.

For the current shared evaluator, from the code root:

```bash
conda env create -f requirements/native/swemm.yml
conda activate swemm
python -m playwright install chromium
python scripts/vsv/evaluate.py --help
python scripts/vsv/extract.py --help
```

The specification uses Python 3.12, Node 20, pinned Playwright, the local package,
and the pinned SWE-MM evaluator. System browser libraries, Bubblewrap/container
support, per-instance repository dependencies, and fixtures still need setup.
A Conda environment does not replace official SWE-MM instance containers.
See [runtime setup](VSV_RUNTIME_SETUP.md) and
[ClusterX Vision2Web setup](VISION2WEB_CLUSTERX.md). The old `/root/.cache/ms-playwright/`
browser installation was used after a copied cache failed; install a matching
browser on the new machine. Installed package inventories are in
`runs/vsv_eval/runtime_validation_20261008/environments/`.

Image reconstruction uses `vision2web-sandbox:latest` with Node, Playwright,
Python and Pillow. Check the existing image/build provenance before replay.
The earlier ClusterX official evaluator uses a separately pinned image/digest
documented in its guide. The full Vision2Web clone inventory also records a local
change to `docker/build.sh`; preserve/review that change if transferring the clone.
GPU training needs separate Swift and verl environments and a compatible
GPU/PyTorch/inference stack. A one-GPU launcher default is not a memory estimate.

### First verification after transfer

Run from the code root, using new output directories:

```bash
# No model calls: recover candidates from the real reference trajectory.
python scripts/vsv/extract.py \
  --run-json data/vision2web/vsv_eval_fixtures/frontend__smartrecruiters__claude-opus-4-8__self_verify/trajectory/run.json \
  --output-dir runs/vsv_eval/migration_candidates --offline

# No model calls: validate a prepared experiment's inputs.
python scripts/vsv/evaluate.py \
  --manifest path/to/experiment.json \
  --output-dir runs/vsv_eval/migration_preflight --check-only

# In an environment with the stated test dependencies.
PYTHONPATH=src python -m pytest tests/test_vsv*.py -q
```

The first command emits **rule candidates**, not LLM-approved rounds or scores.
The preflight command needs an actual prepared manifest; its placeholder is not
a bundled experiment. Passing preflight establishes input readiness, not judge
quality or completed acceptance execution. After credentials, bindings and
runtime are ready, run the same evaluator without `--check-only` in its selected
output directory. Consult `VSV_SYSTEM.md` for manifest fields and resume rules.
Source/config changes require a fresh output directory; do not bypass hash checks.

An experiment exports `summary.json`, `comparison.csv`, `comparison.md`,
`model_calls.json`, and per-case final scores plus stage evidence. Preserve the
request/response cache for reproducibility. `completed`, `blocked`, and `failed`
refer to evaluation execution status; a completed poor agent is still completed.

`/mnt/shared-storage-user/miyapeng/API_CALL_GUIDE.md` is the old machine's API usage
guide. Transfer any necessary private settings separately; no key values are
recorded here. The evaluator normally reads credentials from environment/local
configuration. A new funded service must support the configured image inputs,
response schema, and model identifier before a batch is launched.

## 9. Validation status and next work

The [protocol implementation audit](../reports/vsv_protocol_20261013.md) records
347 focused tests passing and ten skips. A broader exploratory suite also had
19 failures involving missing/local runtimes, archived assets, upstream snapshots,
and recorder/version tests; whole-repository green status was not established.
The [runtime report](../reports/vsv_runtime_validation_20261008.md) distinguishes
real subprocess/browser probes from simulated semantic Judge responses.
These dated results are not fresh test runs for this documentation update.

The October 14 provenance audit directly checks original SmartRecruiters event
284 and reconstructed GLM Finalsite event 105, with no missing attached images
in those two examples and no API calls. Its `validation.json` is in the migration
inventory above. It establishes provenance behavior, not semantic accuracy.
The model inventory audit likewise used no paid model calls. There is **no new
current-protocol score table for the full recommended model panel**.

For this handoff, local Markdown links, English-only authored text, credential
patterns, and both shared CLI help commands were checked. Offline extraction
was also rerun on the original SmartRecruiters fixture: it succeeded and wrote
93 unfiltered rule candidates to
`runs/vsv_eval/migration_handoff_20261014/candidates.json`. This is not a new
semantic extraction or score; no model API was called.

Recommended order on the new cluster:

1. Restore the essential data and references, verify hashes and both Git revisions,
   and run offline import/extraction and runtime smoke checks.
2. Freeze benchmark revisions, task IDs, attempt policy, model identities and
   configurations. Audit the Codex transcript format and the SWE-MM archive/split
   before assuming every recommended run is ready.
3. Resolve catalogue conflicts and validate small representative examples of
   boundaries, image receipt, diagnosis labels, and shared acceptance. Confirm
   original/reconstructed reporting and real before/after executable versions.
4. With a funded judge, calibrate the fixed label prompts and run a small complete
   batch, then the frozen paired cohort. Report actual calls, failures, exclusions,
   support counts, and six point estimates where opportunities exist.
5. Use those results to retain, qualify, or replace the proposed findings. Do not
   keep changing metric denominators or acceptance tolerances to improve scores.
6. Align training with Section 5 and the paper, validate one real multimodal GPU
   update, then collect separate training tasks and run SFT/GRPO comparisons.

This handoff records the work; it does not itself launch those experiments or
transfers. Follow the user's current instructions and any already authorized
model/compute budget when resuming.

## 10. Prompt for the next AI session

> Read `AGENTS.md` and `docs/PROJECT_HANDOFF.md` before changing this project.
> Then inspect the current evaluation specification, runner guide, model cohort
> report, and separate paper checkout. Our active study is Vision2Web + SWE-MM;
> Qwen3.5-9B is the training target. Preserve the distinctions between current
> code, proposed training changes, validated program behavior, and measured
> research results. Locate the migrated original trajectories, image provenance,
> version manifests, and caches before treating a case as ready. Do not reuse old
> metric scores as current results, silently pool reconstructed/original evidence,
> or replace the latest local work with an older GitHub revision. Start with
> offline readiness checks. Keep authored material in English and research code
> simple; use the existing interfaces. Obtain confirmation before destructive
> operations, and commit/push intended source or documentation changes after
> validation. Use the model and compute budget authorized by the user; do not
> request approval again for work already authorized in the active session.
