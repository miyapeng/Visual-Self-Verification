# Benchmark evaluator provenance

The released evaluation sources used by all registered and agent benchmarks
are stored inside this repository. Original sibling checkouts are not source
dependencies at runtime.

- Design2Code:
  - data: `data/design2code`;
  - evaluator source: `evaluate/design2code`;
  - code license: MIT (`evaluate/design2code/CODE_LICENSE`);
  - dataset terms: ODC-By (`evaluate/design2code/DATA_LICENSE`).
  - compatibility changes: corrected the released indentation typo in
    `metrics/visual_score.py` and adapted `metrics/screenshot_single.py` to
    the project-bundled Playwright Node driver.
- Flame:
  - official 80-item data: `data/flame-react-eval`;
  - released evaluator reference: `evaluate/flame`;
  - license: Apache-2.0 (`evaluate/flame/LICENSE`).
- Web2Code:
  - UI2Code_N-preprocessed 1,198-image subset: `data/web2code`;
  - released visual-judge reference: `evaluate/web2code`;
  - the source checkout had no license beside `code_generation`; its nested
    LLaVA-derived package included the copied Apache-2.0
    `evaluate/web2code/LICENSE`.
- UI2Code_N:
  - real data: `data/ui2code-real`;
  - released evaluation scripts: `evaluate/ui2code_n/evaluation`;
  - the source checkout did not include a license file next to this release.

The Python integration ports the released prompts and aggregation logic so
that the four protocols share one output format. The original reference files
are retained beside the port for auditability.

Coding-agent runtimes are intentionally not stored here. They live under
`scaffolds/`; see `docs/AGENT_SCAFFOLDS.md`. This directory is reserved for
upstream benchmark evaluators and their licenses.

## Current integration status

“Task integrated” means the project can construct the agent-visible case and
run its scaffold. “Evaluator self-contained” means final scores can be
computed without reading a sibling checkout under `../Benchmarks`.

| Benchmark | Task integrated | Source self-contained | Official scoring path |
|---|---:|---:|---|
| Design2Code | yes | yes | `evaluate/design2code` through the unified evaluator |
| Flame | yes | yes | `evaluate/flame` through the unified evaluator |
| Web2Code | yes | yes | frozen VLM-judge protocol and upstream reference |
| UI2Code_N | yes | yes | frozen VLM-judge protocol and upstream reference |
| SWE-MM | yes | yes | frozen SWE-bench 5.0.0rc0 + official instance image/tests |
| ChartMimic | yes | yes | pristine Code4Evaluation under its released Python 3.9 environment |
| Vision2Web | yes | yes | pristine `vision2web evaluate`; native Docker or audited ClusterX transport |
| FronTalk | native protocol | yes | pristine ten-turn inference, WebVoyager PR/FR, and pairwise UX evaluation |

Source self-containment does not remove protocol requirements. SWE-MM tests
must run in each official instance image; ChartMimic must use its pinned
Matplotlib 3.8.4 environment; and Vision2Web's evaluator still assumes a
sandbox lifecycle. The in-repository ClusterX transport maps that lifecycle
onto a job launched from the pinned official image, without modifying the
evaluator. It must still undergo paired Docker-vs-ClusterX validation before
results are described as an unqualified official leaderboard reproduction.

Users remain responsible for complying with each dataset's license and usage
terms.

FronTalk cannot be represented faithfully by the generic single-turn runner.
Its entry-point-only adapter and protocol notes are in
`docs/FRONTALK_INTERACTWEB_INTEGRATION.md`.

## Archived evaluator snapshot

The pristine InteractWeb-Bench evaluator, dataset and historical run artifacts
remain in the repository for provenance. It has no active benchmark
registration or launcher and is outside the current paper; it must not be
included in active result aggregates. Preserving this snapshot does not make it
an unfinished task. See `reports/research_scope_decision.md` and
`configs/research/INTERACTWEB_ARCHIVE.md`. The official evaluator source was
not modified when the benchmark was archived.

## WebCompass local research fixture

`data/research/webcompass-image-1` is copied from the released
`WebCompass/test_output/image/1` example in the locally available NJU-LINK
WebCompass repository. It contains the generated Fortune Cookie page and its
two reference-state screenshots. The local upstream checkout does not include
a standalone LICENSE file; its README declares Apache-2.0. The fixture is used
only for research smoke tests and preserves its upstream README as
`README.upstream.md`.

`data/research/webcompass-video-22` is copied from the released
`WebCompass/test_output/video/Claude-Opus-4.5/22` example at upstream commit
`d5fe352e065f6dcf87aca605babf01499b2b12a3`. It contains the unmodified
generated site plus four representative frames from its released interaction
trajectory. Deterministic replay verifies it as a positive control. This is an
official-repository example, not a member of the separately hosted WebCompass
editing/repair split; results must therefore be labeled as a WebCompass-derived
diagnostic and not as an official split score.

`data/research/webcompass-image-107` similarly vendors the two HTML pages and
three screenshots from released image example 107. The upstream example omits
the CSS, JavaScript, and image resources referenced by those pages, so the
calendar is used as a naturally incomplete interactive completion/repair case.

`data/research/webcompass-video-22-independent` is a controlled two-fault
repair variant of the clean video-22 fixture. It changes only the price-range
assignment and selected-brand source in `script.js`; the exact mutation oracle
is documented in its `README.upstream.md`. It is used solely to test whether a
linear verification frontier loses independent residual coverage. It is not an
official WebCompass item and is excluded from official or released-example
aggregate claims.
