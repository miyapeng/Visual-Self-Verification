# Vision2Web self-verification framework

> **Condition-name update (2026-08-27):** this report also documents archived
> experiment-2 artifacts that used `tools` and `self_verify`. New experiments use
> `browser_enabled` and `guided_vsv`. OpenHands keeps three active conditions
> (`official`, `browser_enabled`, `guided_vsv`); Claude Code keeps only `official`
> and `guided_vsv` because its official scaffold already exposes `playwright-cli`.
> The new `guided_vsv` prompt is the detailed frozen procedure in
> [`prompts/vision2web/guided_vsv.txt`](../prompts/vision2web/guided_vsv.txt), not
> the short experiment-2 sentence quoted later in this historical report.

## Scope and invariant

This implementation studies one coding policy, run through either the released
OpenHands or Claude Code scaffold, that may implement, deploy, inspect, interact,
edit, re-deploy, recheck, and submit. The browser is a
mechanical executor and evidence recorder: it never invents a check, assigns a
verdict, identifies a bug, suggests a repair, edits code, or decides to submit.

All 193 frozen Vision2Web tasks remain in scope:

- Level 1: `webpage` (100 tasks)
- Level 2: `frontend` (66 tasks)
- Level 3: `website` (27 tasks)

The dataset revision is
`8f03299d92b9bd852e93852d0c21e8a4848ab661`; the evaluator commit is
`577f9397b3db8fc6d828adde254a830caa65d515`. Generation uses the immutable image
`registry.pjlab.org.cn/ccr-t-llm-frontier/vision2web:official-577f939`, digest
`sha256:3d2fa9b0999a1fbb10c9d4ccc653c7d5560a713668d6c18c80d0d06e36e43056`.

`workflow.json`, its validations, and official evaluator outputs are unavailable
to the coding policy. They may be used only after the trajectory has ended.

Generation stages only public task inputs into `/workspace`; its case marker omits
the dataset source path and all workflow metadata. Because ClusterX also mounts the
shared `/data` volume for artifact persistence, the trajectory audit rejects a run
from the controlled experiment if an agent command explicitly accesses
`workflow.json`, the frozen Vision2Web source tree, or evaluator code. This is a
defense-in-depth contamination check, not a claim that the shared mount is an OS-level
sandbox. A contaminated run is preserved for diagnosis but never scored as method
evidence.

## Why the implementation uses OpenHands `browser_use`

The frozen OpenHands 1.16.0 / OpenHands SDK 1.21.0 distribution already contains
`BrowserToolExecutor`, backed by `browser-use==0.11.13` and a shared Chromium/CDP
session. It provides navigation, click, typing, scrolling, browser state,
screenshots, and indexed interactive elements. The lower-level browser-use event
API also supports key input and dropdown selection; hover and semantic grounding
use the same session's CDP connection.

Therefore, `tools` and `self_verify` use the native OpenHands browser process. No
second Playwright browser is started for the coding agent. `playwright-cli` remains
the official evaluator's browser driver and is not exposed as a verification oracle
during generation.

The active interface revision is `native-browser-use-plan-v6-cache-safe-replay`. It exposes the
native `browser_navigate` and projects the native `browser_get_state` with
`include_screenshot=True` as its default. This default matters because upstream
OpenHands uses `False`; leaving the argument implicit produced text/runtime-only
observations in an archived diagnostic run. It adds exactly one tool:
`browser_execute_plan`. The wrapper accepts reset-separated
scenarios authored in one model call, executes them sequentially, and records a
screenshot, URL, DOM change, accessibility state, and runtime-error delta after
every action. A reset may also set an explicit viewport, which is required for
the desktop/tablet/mobile checks in Level 1.

Supported planned actions are `navigate`, `click`, `fill`, `hover`, `press`,
`select`, `scroll`, `go_back`, and bounded `wait`. Batch plans cannot contain a
browser-use element index. They specify an accessibility `role` and `name`; the
executor refreshes current browser and accessibility state before every target
action and fails visibly when a target is absent or ambiguous.

One environment correction is required: browser-use otherwise attempts to download
optional default extensions on first Chrome launch. ClusterX workers may not reach
that URL, so the tool sets `enable_default_extensions=False`. This does not remove
rendering, screenshots, DOM, accessibility, console, or interaction capability.
Level 2/3 package installation still uses the worker HTTP(S) proxy. The native
browser receives the same proxy through browser-use `ProxySettings`, with an
explicit loopback bypass; the worker also keeps `NO_PROXY` for Browser Use's local
CDP control connection. This separation prevents npm failures without sending the
deployed application or CDP traffic through the outbound proxy.

## Experimental conditions

| condition | task text | tools | intended interpretation |
|---|---|---|---|
| `official` | exact released Vision2Web prompt | released OpenHands CLI tools; browser disabled | official inference baseline |
| `tools` | byte-identical to `official` | official tools plus native navigate/state and `browser_execute_plan` | browser affordance without a verification instruction |
| `self_verify` | official prompt plus one frozen paragraph | identical to `tools` | affordance plus a weak policy cue |

All conditions use the same Qwen3.5-9B endpoint, 262,144-token model context,
8,192-token per-call output limit, 7,200-second attempt timeout, and two official
retries. OpenHands sampling fields remain at the released defaults
(`temperature=None`, `top_p=None`, `seed=None`) in every condition; the baseline is
not made artificially deterministic only for this experiment.

The appended paragraph is exactly:

> After obtaining a runnable implementation, inspect the deployed application before submission. When interaction is needed, plan and execute the relevant browser actions, use the resulting visual and runtime evidence to decide whether code changes are necessary, and recheck after meaningful changes. You decide when to inspect, edit, continue, or submit.

There are no hard-coded phase transitions, mandatory tool calls, pass/fail rules,
repair prompts, admission gates, or stop conditions. The model remains free to ignore
the interfaces, inspect once, interact repeatedly, edit, or finish.

The nine complete effective prompts (three levels times three conditions), including
SHA-256 hashes, are frozen in
[`vision2web_self_verification_prompts.md`](vision2web_self_verification_prompts.md).
The hashes confirm that `official` and `tools` task text is identical at every level.
The three official hashes also match the released `WEBPAGE_PROMPT`,
`FRONTEND_PROMPT`, and `WEBSITE_PROMPT` Python constants byte-for-byte; the
Markdown carrier's former trailing newline is not passed to OpenHands.

## Claude Code adaptation

Vision2Web's frozen upstream includes a second official inference adapter for
Claude Code. On the released Docker path it executes `docker exec -w /workspace`
and then runs:

```bash
claude --print --verbose --output-format stream-json \
  --dangerously-skip-permissions -p '<official prompt>'
```

The ClusterX case container already is the sandbox. The direct-container adapter
in `src/multimodalcode/agent_harness/claude_code_runner.py` therefore removes only
the outer `docker exec`; it preserves the command flags, all official Anthropic
environment variables, retry-in-place behavior, timeout behavior, and the
`/workspace/start.sh` success condition. Raw stream-json and stderr remain intact,
and a receive-time sidecar permits Claude tool calls to be merged with passive
workspace/deployment events without changing the canonical stream.

The official image installs `playwright-cli` and its Claude skill. Consequently,
Claude `official` and `tools` currently have byte-identical prompts and identical
native tools; this equivalence is recorded rather than creating an artificial
tools-only browser. `self_verify` changes only the same frozen instruction suffix
used for OpenHands. A separate batch semantic-action wrapper will be added only if
the pinned-image capability probe shows that native `playwright-cli` cannot express
the required plan; it is not assumed from the host environment.

Run the no-inference capability probe inside the pinned image with:

```bash
python3.12 scripts/vision2web/probe_claude_code_runtime.py \
  --output runs/vision2web_self_verify/probes/claude-code-runtime.json
```

Run one prepared ClusterX case through Claude Code by passing the final framework
argument to the existing worker script, or submit the controlled smoke through:

```bash
python scripts/vision2web/submit_self_verify.py \
  --framework claude_code --phase smoke \
  --run-label qwen35-9b-claude-code-selfverify-v1
```

This is a **Claude Code harness with the configured policy model**. When Qwen is
routed through the Anthropic-compatible LiteLLM endpoint, it must not be described
as the Claude model.

## Version and trajectory recording

A passive observer records workspace and deployment transitions without sending
messages to the policy. The browser interface creates the observation checkpoint;
HTTP reachability alone never creates a code version.

- `P_first` is copied when the first browser observation of the deployed
  `localhost:3000` application is prepared for the coding policy. Viewing a local
  prototype image or merely making the HTTP server reachable does not count.
- `P_final` is copied when the OpenHands trajectory ends, including abnormal exit.
- Public task inputs (`prototypes`, `resources`) and runtime caches/dependencies are
  excluded from code hashes and reattached only for post-trajectory evaluation.
- Raw OpenHands events, workspace changes, deployment transitions, browser plans,
actions, observations, evidence artifacts, and submission events are merged into
`development_timeline.jsonl`.

The previous forced planner/judge/repair implementation is preserved for historical
reproduction but guarded for Vision2Web. It is not invoked by any command in this
report and requires the explicit archival override
`--allow-archived-forced-vision2web-loop`.

Every browser screenshot and large state is preserved under `development/<run-token>/evidence`.
The inline model observation contains the corresponding compact state and artifact
hashes. This keeps the evidence auditable without exposing evaluator-private data.

## Frozen task selection

Selection is independent of `workflow.json`: within each level, case IDs are sorted
by `sha256("vision2web-self-verify-v1\\0" + case_id)`.

Smoke (one per level):

- `webpage/classic-clashes`
- `frontend/forum_vectorworks`
- `website/permanent`

Fixed experiment:

- Level 1 (6): `webpage/brother`, `webpage/kashiwabara`,
  `webpage/windpower_monthly`, `webpage/fairfax-county`, `webpage/wikipedia`,
  `webpage/imdb`
- Level 2 (6): `frontend/academy_govloop`, `frontend/newsday_co_tt`,
  `frontend/mojarto`, `frontend/community_hpe`, `frontend/ewif`,
  `frontend/copenhagenmarathon`
- Level 3 (3): `website/sykescottages`, `website/act`, `website/scenario`

The canonical machine-readable configuration is
`configs/vision2web/self_verify_openhands.json`.

## Reproduction commands

Start the one shared Qwen3.5-9B endpoint (2 GPUs, tensor parallel 2, 262144-token
context, maximum three concurrent sequences):

```bash
cd /data/miyapeng/mmcode/MultimodalCode
bash scripts/vision2web/submit_self_verify_server.sh
```

Run the guarded, resume-safe smoke controller in tmux, with three case workers:

```bash
proxy_on
bash scripts/vision2web/run_self_verify_after_smoke.sh \
  runs/vision2web_self_verify/probes/browser_interface_smoke_v3_exact.json \
  qwen35-9b-openhands-selfverify-v5-local-bypass 3
```

After smoke is inspected and accepted, run the fixed experiment (45 trajectories):

```bash
tmux new-session -d -s v2w-selfverify-fixed \
  'cd /data/miyapeng/mmcode/MultimodalCode && bash scripts/vision2web/run_self_verify_supervisor.sh fixed 3'
```

Recompute trajectory-level observable statistics without reading hidden workflows:

```bash
python scripts/vision2web/analyze_self_verify_runs.py \
  --run-root runs/vision2web_generation/qwen35-9b-openhands-selfverify-v5-local-bypass \
  --output runs/vision2web_generation/qwen35-9b-openhands-selfverify-v5-local-bypass/trajectory-analysis.json
```

The analysis reports whether the agent inspected, emitted browser actions, edited
after browser evidence, and rechecked after an edit. Requirement relevance is left
as an explicit manual annotation because a keyword heuristic or hidden workflow
would confound the experiment.

## Post-trajectory official evaluation

For each finished run, materialize immutable `P_first` and `P_final` workspaces:

```bash
python scripts/vision2web/materialize_version_pair.py \
  --run-dir <condition-case-run-dir> \
  --output-root runs/vision2web_version_pairs
```

Each version is then scored separately with `evaluate/vision2web/run_clusterx.py`
inside the same frozen image, supplying the functional and visual evaluator API
credentials. This wrapper preserves the released Vision2Web CLI, workflow order,
Claude Code functional prompt, Playwright execution, visual judge prompt, and result
format. It replaces only the outer Docker lifecycle with a strict, audited ClusterX
transport. Results must therefore be described as **official Vision2Web evaluator
with ClusterX transport** until paired Docker-vs-ClusterX equivalence validation or
maintainer acceptance is available.

The generation jobs do not currently have evaluator API credentials. Consequently,
official functional/visual scores and `P_first` to `P_final` deltas remain pending;
no proxy score is substituted.

Version-pair materialization includes the experimental condition in its path
(`official|tools|self_verify/P_first|P_final/...`), so equal case/model names from
the three conditions cannot overwrite or reuse one another. Trajectory aggregates
likewise include only runs with a present, passing contamination audit; excluded
runs remain listed and preserved for diagnosis.

All case workers request zero GPUs and set `CUDA_VISIBLE_DEVICES=""`. OpenHands,
Claude Code, and Chrome remain CPU-side; all policy inference goes to the one shared
vLLM endpoint. Therefore the three conditions may run concurrently without allocating
additional accelerators. On a GPU-only queue, zero-GPU jobs may wait for a compatible
CPU placement; this scheduling delay must not be worked around by reserving unused
GPUs. Use a CPU-capable queue when available.

## Validation and current smoke state

- Relevant source tests: 14 passed.
- Native OpenHands/browser-use API probe: passed in the frozen image.
- Real-Chrome interface smoke without the worker proxy: passed in the frozen image. The exposed tools were
  exactly `browser_navigate`, `browser_get_state`, and `browser_execute_plan`;
  a no-argument native state call returned a screenshot, the semantic `button/Go`
  target executed, and the recorded DOM changed from `Waiting` to `Fixed`. The
  record is `runs/vision2web_self_verify/probes/browser_interface_smoke_v2.json`.
- The exact worker-network smoke passed as `mmc-v2w-bsmoke-v3-final`. Its
  record is
  `runs/vision2web_self_verify/probes/browser_interface_smoke_v3_exact.json`:
  native state returned a screenshot, semantic click succeeded, and the DOM
  changed while the real worker HTTP(S) proxy and loopback bypass were active.
- All earlier model jobs and artifacts are preserved. Runs using the old browser
  interface, missing worker proxy, or upstream's screenshot-disabled default are
  diagnostic evidence only and are never aggregated with the v5 smoke.
- The obsolete v4 controller and its retry workers were stopped after the proxy/CDP
  confound was identified. The first guarded v5 handoff exposed a shell execute-bit
  error after the probe passed and submitted no model jobs. The launcher now invokes
  the supervisor explicitly through Bash; the corrected v5 controller submitted the
  three Level-1 conditions on 2026-08-24 and will resume through Levels 2 and 3.

## Initial findings

The framework-level finding is already clear: the original OpenHands browser-use
backend is sufficient, but its public tools do not directly express a one-call,
reset-separated interaction plan or retain all action-conditioned evidence. The
necessary change is an interface projection over the same executor, not a second
GUI agent or another browser stack.

Behavioral findings are deliberately deferred until all nine controlled smoke
trajectories are complete. In particular, the existence of a browser tool must not
be reported as proactive verification; evidence requires an actual inspection,
requirement-conditioned interaction, post-observation edit, and recheck in the
recorded timeline.
