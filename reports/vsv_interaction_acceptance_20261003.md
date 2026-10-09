# Independent interaction acceptance pilot

Date: 2026-10-03. Status: implementation pilot; independent human calibration is pending.

The state runner now executes fixed acceptance workflows through the existing
Node Playwright browser and JudgeClient. Targets share ordered UI actions and
observations within a workflow. Different workflows start in fresh contexts.
Program assertions determine explicit conditions; semantic targets receive the
actual post-interaction pixels. Original trajectory extraction and the six
metric formulas are unchanged.

## Source and scope

The source is SmartRecruiters (Vision2Web L2), Claude Opus 4.8 / Claude Code:

- [Original trajectory](../data/vision2web/vsv_eval_fixtures/frontend__smartrecruiters__claude-opus-4-8__self_verify/trajectory/run.json).
- [Existing rounds](../runs/vsv_eval/verification_minimal_smartrecruiters_20261003/verification_rounds.json).
- [Existing draft catalogue](../runs/vsv_eval/protocol_smartrecruiters_20261003/catalogue_atomic_retry/catalogue.json).
- [Executed workflow specification](../runs/vsv_eval/acceptance_smartrecruiters_20261003/state_spec.json).

V3 and V4 are the audited states immediately before and after original Edit
308. The runner reused the existing version manifest and checked code/resource
identities before and after execution. Both versions used 1920 x 1080, the
official workflow resolution. The three workflows cover Customers navigation
and Load More, About-to-Careers navigation, and homepage sticky navigation.
They reference existing requirement IDs and use the same actions and criteria
for both versions. The specification and catalogue remain explicitly draft.

## Results

| Fixed goal | Decision source | V3 | V4 |
| --- | --- | --- | --- |
| `nav_customers` | URL and rendered content assertions | pass | pass |
| `customers_load_more` | Displayed article count increases; route retained | pass | pass |
| `nav_about_us` | URL and rendered content assertions | pass | pass |
| `nav_careers` | URL and rendered content assertions | pass | pass |
| `homepage_sticky_nav` | Scroll and banner position assertions | fail | fail |
| `customers_industry_layout` | Gemini visual state judgment | pass | pass |
| `careers_job_listings` | Gemini visual state judgment | pass | pass |

There are 14 target/version results: 12 pass and 2 fail. Each version has five
mechanical and two semantic target decisions. Three targets previously unknown
in these versions are now resolved: Customers and Careers navigation pass;
sticky navigation fails. The test records scrollY=1200 and banner y=-1200 after
a baseline of scrollY=0 and banner y=0. The actual viewport screenshot also
shows that the navigation bar is absent. This source review is not independent
human annotation.

Four unresolved locator steps were grounded by Gemini: each version selected
`Customer Stories` in the open Resources menu and `Checkout Our Careers` in the
Work with us section. These selections used supplied current element refs,
retained the prescribed business path, and were executed by Playwright. Their
labels differ from the official workflow, but satisfy the existing catalogue's
navigation criteria. No model success statement served as an acceptance label.

The remaining 25 goals in the 32-goal state catalogue, including five repair
criteria, were not scheduled and remain unknown. The original eight agent image
rounds, VC, CV and BDA do not change. These new observations have independent
origin and do not create agent checks or diagnoses.

## Model and recorded usage

Both requested and response-reported model IDs are `gemini-3.1-pro-preview`.
There are six successful requests: four `gui_step` and two `visual_state`.
All use the existing `gemini31` profile: temperature 1, high reasoning effort,
16,384 output-token limit, structured JSON, and no retries. Program-only goals
make no model requests. No extraction or historical check judge was rerun.

Reported usage is 68,837 input and 4,812 output tokens, 73,649 total. Output
usage includes reported reasoning tokens. This is logged token usage, not a
monetary invoice. See [request IDs and usage](../runs/vsv_eval/acceptance_smartrecruiters_20261003/api_usage.json).

## Artifacts and verification

- [Execution and state results](../runs/vsv_eval/acceptance_smartrecruiters_20261003/online/repair_results.json).
- [V3 execution](../runs/vsv_eval/acceptance_smartrecruiters_20261003/online/replays/V3/result.json) and [V4 execution](../runs/vsv_eval/acceptance_smartrecruiters_20261003/online/replays/V4/result.json).
- [V3 labels](../runs/vsv_eval/acceptance_smartrecruiters_20261003/online/state_results/V3.json) and [V4 labels](../runs/vsv_eval/acceptance_smartrecruiters_20261003/online/state_results/V4.json).
- Original model responses and usage are under `online/judge_cache/`; actual GUI input/selection records are under each replay's `gui_steps/`.
- [Partial scoring join](../runs/vsv_eval/acceptance_smartrecruiters_20261003/combined/scores.json) validates the saved execution and joins existing historical check labels without further model calls.

The partial join has RS=NA: the linked Winston repair criterion was not tested.
Its CP=100 is based on only six known preservation results, with 155 unknown
results across the incomplete repair evaluation and five unassessed transitions.
`complete=false`; this does not certify overall correctness preservation.

Related tests: 174 passed, 6 skipped because archived artifacts are unavailable.
The new tests cover immutable inputs/current refs, malformed model responses,
shared state and workflow resets, exact fail versus blocked, action budgets,
evidence scope, interactive pixels, and retaining completed exact assertions
when screenshot capture fails. The browser integration test uses the existing
local Playwright runtime. No new dependency was added.

## Reproduction

Run from the project root, with API credentials set in the environment using
the existing API guide. The locally installed runtime was:

```bash
export PATH="/tmp/vsv-viewer-browser/node_modules/node/bin:$PATH"
export VSV_PLAYWRIGHT_PACKAGE=/tmp/vsv-viewer-browser/node_modules/playwright
export VSV_CHROMIUM=/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome
export LD_LIBRARY_PATH=/tmp/vsv-viewer-browser/libs/unpacked/usr/lib/x86_64-linux-gnu
python3 scripts/vision2web/score_vsv.py \
  --rounds-json runs/vsv_eval/verification_minimal_smartrecruiters_20261003/verification_rounds.json \
  --catalogue runs/vsv_eval/protocol_smartrecruiters_20261003/catalogue_atomic_retry/catalogue.json \
  --state-spec runs/vsv_eval/acceptance_smartrecruiters_20261003/state_spec.json \
  --output-dir runs/vsv_eval/acceptance_smartrecruiters_20261003/repeat \
  --allow-draft --primary-profile gemini31 --port 18956 --workers 2
```

Use a fresh output directory. For a new offline execution, add `--offline`;
unresolved GUI steps are recorded as blocked without model calls. Backend
data restoration is outside this static/client pilot and requires an explicit
certified reset. Human review of criteria, locator semantics and state judgments
is still needed before formal benchmark use.
