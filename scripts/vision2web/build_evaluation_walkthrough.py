#!/usr/bin/env python3
"""Explain one saved SmartRecruiters pilot round in a self-contained HTML file.

This presentation reads recorded evidence and model responses. It neither
relabels the trajectory nor calls a model or changes the scoring protocol.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
from pathlib import Path

from multimodalcode.io import read_json
from multimodalcode.vsv_eval.evaluation import load_rounds
from multimodalcode.vsv_eval.metrics import summarize


ROOT = Path(__file__).resolve().parents[2]
EPISODE_ID = "verification-0283-0286-2f3e250cc0"
SCORES = ROOT / "runs/vsv_eval/acceptance_smartrecruiters_20261003/combined/scores.json"
METRICS = ("VC", "CV", "BDA", "RS", "CP", "VCS")


def image_url(path: Path) -> str:
    media_type = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{media_type};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def example_data() -> dict:
    scores = read_json(SCORES)
    rounds = load_rounds(Path(scores["source_rounds"]))
    episode = next(e for e in rounds["episodes"] if e["episode_id"] == EPISODE_ID)
    result = next(e for e in scores["episodes"] if e["episode_id"] == EPISODE_ID)
    request = result["requests"][0]
    packet = read_json(Path(request["input_path"]))
    response = read_json(Path(request["response_path"]))
    if response["request_sha256"] != request["request_sha256"]:
        raise ValueError("The saved model response does not match the round request")
    recorded = [{k: v for k, v in t.items() if k != "target_id"} for t in result["targets"]]
    if response["parsed"]["targets"] != recorded:
        raise ValueError("The round labels differ from the original model response")
    repairs = [r for r in scores["repairs"] if EPISODE_ID in r["episode_ids"]]
    catalogue = read_json(Path(scores["catalogue"]))
    local = summarize(catalogue, [result], repairs)
    recomputed = summarize(catalogue, scores["episodes"], scores["repairs"], scores["repair_gaps"])
    if any(recomputed[k] != scores["metrics"][k] for k in METRICS):
        raise ValueError("The saved scores do not match the current metric implementation")
    recheck_ids = {eid for link in episode["repair_links"] for eid in link["recheck_episode_ids"]}
    rechecks = [e for e in rounds["episodes"] if e["episode_id"] in recheck_ids]
    event_ids = set(episode["core_event_ids"] + episode["context_event_ids"])
    for link in episode["repair_links"]:
        event_ids.update(link["repair_event_ids"] + link["evidence_event_ids"])
    for e in rechecks:
        event_ids.update(e["core_event_ids"])
    # The paired Edit return remains evidence even though the relation cites its action ID.
    edit_calls = {e.get("tool_call_id") for e in rounds["events"]
                  if e["ordinal"] in result["repair_event_ids"]}
    for e in rounds["events"]:
        if e.get("payload", {}).get("tool_use_id") in edit_calls:
            event_ids.add(e["ordinal"])
    events = [e for e in rounds["events"] if e["ordinal"] in event_ids]
    observed = next(i for i in packet["image_order"] if i["role"] == "agent_observation")
    reference = next(i for i in packet["image_order"] if i["role"] == "reference")
    return {
        "source_run": rounds["source_run"], "source_run_sha256": rounds["source_run_sha256"],
        "source_rounds": scores["source_rounds"], "source_scores": str(SCORES),
        "pilot_status": scores["status"], "catalogue_review": scores["catalogue_review"],
        "trajectory_summary": {k: rounds[k] for k in
                               ("episode_count", "image_episode_count", "image_input_count")},
        "episode": episode, "events": events, "rechecks": rechecks,
        "input_packet": packet, "model_response": {
            k: response[k] for k in ("stage", "model", "request_sha256", "requested_at",
                                    "completed_at", "prompt", "parsed", "raw")},
        "result": result, "repairs": repairs,
        "catalogue": catalogue, "local_metrics": local, "trajectory_metrics": scores["metrics"],
        "images": {
            "observed": image_url(Path(observed["source_path"])),
            "reference": image_url(Path(reference["source_path"])),
            "recheck": image_url(Path(rounds["source_run"]).parent /
                                 next(e for e in events if e["ordinal"] == 344)["images"][0]["path"]),
        },
    }


PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>One verification round, explained · SmartRecruiters</title>
<style>
:root{--ink:#142a36;--muted:#607782;--paper:#f7f8f5;--line:#dae4e4;--blue:#236482;--teal:#127562;--purple:#6850a1;--amber:#916316;--red:#a43d42}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
button,a{touch-action:manipulation}button{font:inherit;cursor:pointer}a{color:var(--blue)}button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid var(--blue);outline-offset:3px}
header{background:#fff;border-bottom:1px solid var(--line)}.top{max-width:1180px;margin:auto;padding:24px 28px;display:flex;align-items:center;justify-content:space-between;gap:20px}
.brand{font-size:13px;font-weight:800;letter-spacing:.14em;color:var(--teal)}.pill{font-size:12px;background:#fff3d9;border:1px solid #e9d3a5;border-radius:20px;padding:5px 12px;color:var(--amber)}
main{max-width:1180px;margin:auto;padding:26px 28px 48px}.eyebrow{font-size:12px;letter-spacing:.12em;font-weight:750;color:var(--muted);text-transform:uppercase}
h1{font-size:clamp(28px,4vw,42px);line-height:1.15;letter-spacing:-.025em;margin:10px 0 12px}h2{font-size:27px;line-height:1.25;margin:0 0 8px}h3{font-size:18px;margin:0 0 10px}p{margin:0 0 12px}.lead{font-size:18px;color:var(--muted);max-width:840px}.small{font-size:13px;color:var(--muted)}
.overview{display:flex;align-items:center;gap:12px;margin:18px 0 24px;flex-wrap:wrap}.overview b{background:#fff;padding:10px 16px;border:1px solid var(--line);border-radius:10px;font-size:14px}.arrow{color:var(--muted);font-size:22px}
.stepper{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:18px 0}.stepper button{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 10px;color:var(--muted);text-align:left;font-size:14px;font-weight:600}.stepper button[aria-selected="true"]{background:var(--ink);color:white;border-color:var(--ink)}.stepper span{display:inline-grid;place-items:center;width:23px;height:23px;border:1px solid currentColor;border-radius:50%;margin-right:7px;font-size:12px}
.panel{background:#fff;border:1px solid var(--line);border-radius:16px;padding:26px;min-height:450px}.panel[hidden]{display:none}.intro{color:var(--muted);max-width:890px;margin-bottom:20px}.grid{display:grid;grid-template-columns:1.12fr 1fr;gap:22px}.box{border:1px solid var(--line);border-radius:12px;padding:18px;background:#fbfcfc}.callout{background:#edf5f4;border-left:3px solid var(--teal);padding:12px 16px;border-radius:0 8px 8px 0;font-size:14px;margin-top:14px}.callout.warn{background:#fff7e8;border-color:#c99942}
.timeline{border-left:2px solid var(--line);margin-left:16px}.event{position:relative;padding:0 0 18px 25px}.event:before{content:"";position:absolute;width:10px;height:10px;border-radius:50%;background:var(--blue);left:-6px;top:7px}.event.context:before{background:var(--muted)}.event b{display:block;font-size:15px}.event p{font-size:14px;color:var(--muted);margin:4px 0 0}.event:last-child{padding-bottom:0}.eventcode{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;color:var(--blue)}
blockquote{margin:8px 0 0;padding:12px 15px;border-left:3px solid var(--purple);background:#f4f1fa;font-size:15px}pre{background:#112833;color:#e1edf0;padding:16px;border-radius:10px;font:12px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;margin:10px 0 0;max-height:480px;overflow:auto}code{font: .88em ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}summary{cursor:pointer;color:var(--blue);font-size:14px;font-weight:600}details{margin:14px 0}details p{margin-top:12px}
.steps{display:flex;gap:9px;align-items:stretch;margin-bottom:18px}.steps .box{flex:1;padding:13px;font-size:14px}.steps .arrow{align-self:center}.tag{display:inline-block;font-size:11px;font-weight:800;border-radius:4px;padding:3px 6px;letter-spacing:.04em;background:#eaf0f5;color:var(--blue);margin-bottom:7px}.tag.model{background:#f0ebf8;color:var(--purple)}.tag.code{background:#e8f4ef;color:var(--teal)}
.visuals{display:grid;grid-template-columns:1fr 1fr;gap:15px}.visual{border:1px solid var(--line);border-radius:12px;overflow:hidden;background:#f6f9f9}.visualhead{padding:11px 14px;font-size:14px;font-weight:700;border-bottom:1px solid var(--line)}.imageframe{height:310px;overflow:auto;background:#061c28}.imageframe img{width:100%;display:block}.visualfoot{padding:10px 14px;font-size:12px;color:var(--muted)}.visualfoot button{border:0;background:none;padding:0;color:var(--blue);text-decoration:underline;font:inherit;margin-right:13px}.twocol{display:grid;grid-template-columns:1fr 1fr;gap:15px;margin-top:16px}
.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}th{text-align:left;color:var(--muted);font-size:12px;font-weight:700;padding:10px 12px;border-bottom:1px solid var(--line)}td{padding:13px 12px;border-bottom:1px solid var(--line);vertical-align:top}.pick{border:0;background:none;text-align:left;padding:0;color:var(--blue);text-decoration:underline;font-size:14px}.state{font-size:12px;border-radius:5px;padding:3px 7px;background:#e8f5ef;color:var(--teal);font-weight:700;white-space:nowrap}.state.fail{color:var(--red);background:#fbebeb}.state.unknown{color:var(--amber);background:#fff3dd}.jsonhelp{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}.legend{display:grid;grid-template-columns:150px 1fr;gap:8px 10px;font-size:13px;align-content:start}.legend b{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}.legend span{color:var(--muted)}
.repairflow{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:20px}.repairflow .box{padding:16px}.repairflow pre{max-height:190px;font-size:11px}.repairflow .short{font-size:14px}.scoregrid{display:grid;grid-template-columns:repeat(3,1fr);gap:13px}.scorecard{border:1px solid var(--line);border-radius:12px;padding:17px}.scorecard h3{font-size:15px}.value{font-size:29px;font-weight:750;letter-spacing:-.03em;margin:6px 0}.formula{font:12px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--blue);min-height:40px}.scorecard p{font-size:13px;color:var(--muted);margin-top:8px}.scorecard .scope{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}.switch{display:flex;gap:8px;margin:16px 0}.switch button{border:1px solid var(--line);background:#fff;padding:8px 14px;border-radius:7px;font-size:14px}.switch button[aria-pressed="true"]{border-color:var(--teal);background:#eaf5ef;color:var(--teal);font-weight:700}
.controls{display:flex;justify-content:space-between;align-items:center;margin-top:18px}.controls button,.download{border:1px solid var(--line);background:#fff;padding:9px 17px;border-radius:8px;font-size:14px;color:var(--ink);text-decoration:none}.controls .next{background:var(--teal);color:#fff;border-color:var(--teal)}.controls button:disabled{opacity:.4;cursor:default}.status{font-size:13px;color:var(--muted)}footer{font-size:12px;color:var(--muted);margin-top:22px}.links{display:flex;gap:18px;flex-wrap:wrap;margin-top:10px}dialog{border:0;border-radius:12px;padding:14px;max-width:min(96vw,1000px);max-height:95vh}dialog::backdrop{background:#102430bb}dialog .close{display:block;position:sticky;top:0;margin:0 0 10px auto;border:1px solid var(--line);border-radius:6px;background:#fff;padding:7px 14px}dialog img{display:block;width:100%;height:auto}
@media(max-width:780px){main{padding:20px 14px}.top{padding:18px 14px}.panel{padding:18px}.grid,.jsonhelp,.twocol{grid-template-columns:1fr}.stepper{gap:4px}.stepper button{font-size:11px;padding:10px 6px;text-align:center}.stepper span{display:block;margin:0 auto 4px}.repairflow{grid-template-columns:1fr}.scoregrid{grid-template-columns:1fr 1fr}.legend{grid-template-columns:135px 1fr}.lead{font-size:16px}.steps{flex-wrap:wrap}.visualhead{font-size:12px}.imageframe{height:270px}}
@media(max-width:430px){.scoregrid,.visuals{grid-template-columns:1fr}.overview{gap:6px}.overview b{padding:8px 10px;font-size:12px}.top .pill{font-size:10px}}
</style>
</head>
<body>
<header><div class="top"><span class="brand">VISUAL SELF-VERIFICATION</span><span class="pill">Saved pilot · 03 Oct 2026</span></div></header>
<main>
<div class="eyebrow">SmartRecruiters · Vision2Web L2 · Claude Opus 4.8 / Claude Code</div>
<h1>One verification round, explained.</h1>
<p class="lead">Claude inspects the Winston page, notices a white box, and edits a shared component. What can the evaluator actually conclude?</p>
<div class="overview"><b>Claude produces evidence</b><span class="arrow">→</span><b>Gemini returns labels</b><span class="arrow">→</span><b>Python counts the labels</b></div>
<nav class="stepper" aria-label="Walkthrough steps" role="tablist">
<button role="tab" aria-controls="step0" id="tab0" aria-selected="true"><span>1</span>The record</button>
<button role="tab" aria-controls="step1" id="tab1" aria-selected="false"><span>2</span>Judge input</button>
<button role="tab" aria-controls="step2" id="tab2" aria-selected="false"><span>3</span>Judge labels</button>
<button role="tab" aria-controls="step3" id="tab3" aria-selected="false"><span>4</span>Edit &amp; recheck</button>
<button role="tab" aria-controls="step4" id="tab4" aria-selected="false"><span>5</span>The scores</button>
</nav>

<section class="panel" id="step0" role="tabpanel" aria-labelledby="tab0">
<span class="tag code">PROGRAM: ORGANIZE ORIGINAL EVENTS</span>
<h2>The unit is one inspection round.</h2>
<p class="intro">Core events 283–286: read the Winston screenshot, receive the image, and respond. The earlier batch capture stays a reference; the later edit stays a separate link.</p>
<div class="grid"><div>
<div class="timeline">
<div class="event context"><b><span class="eventcode">#272 → #273</span> Shared screenshot source</b><p>One Bash call captures several pages. It times out, but the Winston screenshot is later returned to the agent.</p></div>
<div class="event"><b><span class="eventcode">#283</span> Read the screenshot</b><p><code>Read({"file_path": "/tmp/winston-ai.png"})</code></p></div>
<div class="event"><b><span class="eventcode">#284</span> Actual image returned</b><p>The response is paired to #283 by its tool-call ID. Image pixels entered Claude's context.</p></div>
<div class="event"><b><span class="eventcode">#285</span> Image size note</b><p>The tool records the image dimensions; this is not an agent diagnosis.</p></div>
<div class="event"><b><span class="eventcode">#286</span> Claude's judgment</b><blockquote id="diagnosis"></blockquote></div>
</div>
<div class="callout">One image can support several checked targets. This is still one round.</div>
</div><div>
<div class="box"><h3>The actual record format</h3><p class="small">Original fields, unchanged. Events are stored once in the trajectory and reached by their IDs.</p><pre id="episodeRecord"></pre></div>
<details><summary>What do these fields mean?</summary><div class="legend" style="margin-top:12px">
<b>core_event_ids</b><span>The inspection itself: actions, observations and response.</span>
<b>judgment_event_ids</b><span>Eligible model judgments inside this round.</span>
<b>context_event_ids</b><span>Shared capture source and nearby context; not new checks.</span>
<b>evidence_states</b><span>Image input → capture action → captured code version.</span>
<b>repair_links</b><span>Actual later edits and associated recheck rounds.</span>
<b>program_sha256</b><span>The recorded artifact identity for this check.</span>
</div></details>
</div></div>
</section>

<section class="panel" id="step1" role="tabpanel" aria-labelledby="tab1" hidden>
<span class="tag model">MODEL: JUDGE THE ORIGINAL CHECK</span>
<h2>Give the judge the evidence available then.</h2>
<p class="intro">The saved Gemini request stops at event 286. It includes task requirements, the fixed catalogue, the reference image, the returned screenshot, and Claude's response.</p>
<div class="steps"><div class="box"><b>Task + catalogue</b><br><span class="small">What should exist?</span></div><span class="arrow">+</span><div class="box"><b>Image #284 + response #286</b><br><span class="small">What was seen and said?</span></div><span class="arrow">→</span><div class="box"><b>Gemini 3.1 Pro</b><br><span class="small">Labels with evidence IDs</span></div></div>
<div class="visuals">
<div class="visual"><div class="visualhead">Returned screenshot · event #284</div><div class="imageframe" id="agentFrame"><img id="agentImage" alt="Winston screenshot actually returned to Claude at event 284"></div><div class="visualfoot"><button data-full="observed">Open full image</button><button data-focus="agentFrame">Jump to testimonial</button>Agent evidence</div></div>
<div class="visual"><div class="visualhead">Task reference · winston_ai.jpg</div><div class="imageframe" id="referenceFrame"><img id="referenceImage" alt="Original Winston task reference image"></div><div class="visualfoot"><button data-full="reference">Open full image</button><button data-focus="referenceFrame">Jump to testimonial</button>Requirement source</div></div>
</div>
<div class="callout warn">Edit #308 and final screenshot #344 are excluded from this judgment. Future evidence cannot make an earlier diagnosis correct.</div>
<div class="twocol"><div class="box"><h3>Relevant fixed requirements</h3><div id="goals"></div></div><div class="box"><h3>Questions asked of the judge</h3><p class="small">What was checked? Was the method valid? Was the evidence sufficient? What state does that evidence support? What did Claude say, and was it correct?</p><p class="small">The model returns labels. It does not return a numerical score.</p><details><summary>Original prompt and complete input</summary><pre id="requestPrompt"></pre></details></div></div>
<details><summary>Inspect the complete recorded input packet</summary><pre id="inputPacket"></pre></details>
</section>

<section class="panel" id="step2" role="tabpanel" aria-labelledby="tab2" hidden>
<span class="tag model">MODEL OUTPUT: THREE TARGETS, ONE ROUND</span>
<h2>The judge compares evidence with Claude's claims.</h2>
<p class="intro">These are the actual saved Gemini labels. Click a target to inspect its JSON. “Judge state” is the model's assessment of the supplied evidence.</p>
<div class="tablewrap"><table><thead><tr><th>Target</th><th>Judge state</th><th>Claude's claim</th><th>Diagnosis</th><th>Fixed goal?</th></tr></thead><tbody id="targetTable"></tbody></table></div>
<div class="jsonhelp"><div class="box"><h3 id="targetName">Selected target</h3><pre id="targetJson"></pre></div><div class="box"><h3>Reading the labels</h3><div class="legend">
<b>coverage: full</b><span>The condition was exercised and sufficiently observed.</span>
<b>method_ok: true</b><span>The check could distinguish a good state from a defect.</span>
<b>evidence_ok: true</b><span>Sufficient evidence was actually received for this target.</span>
<b>actual_state</b><span>Judge-assessed state: pass, fail or unknown.</span>
<b>agent_state</b><span>Claude's expressed diagnosis of this target.</span>
<b>issue_match</b><span>For an error: did Claude identify the same object and symptom?</span>
<b>evidence_ids: [284]</b><span>The actual returned image supporting this label.</span>
<b>diagnosis_ids: [286]</b><span>The original response expressing Claude's judgment.</span>
<b>check_id: null</b><span>A legitimate extra check outside the fixed coverage list.</span>
</div></div></div>
<div class="callout">The white-box finding counts for check validity and diagnosis accuracy. It adds no new required goal to the coverage denominator.</div>
<details><summary>Original model response</summary><pre id="rawResponse"></pre></details>
</section>

<section class="panel" id="step3" role="tabpanel" aria-labelledby="tab3" hidden>
<span class="tag code">PROGRAM + MODEL: RELATED EDIT AND INDEPENDENT ACCEPTANCE</span>
<h2>A linked edit is not a verified repair.</h2>
<p class="intro">The recorded relation connects this round to Edit 308 and the final homepage check. Repair scoring needs the same Winston condition tested on exact before/after versions.</p>
<div class="repairflow">
<div class="box"><span class="tag">CHECK · #283–286</span><h3>Winston white box</h3><p class="short">Claude explicitly identifies a stray testimonial brandmark box.</p><p class="small">Original finding: #286<br>Evidence image: #284</p></div>
<div class="box"><span class="tag code">EDIT · #308</span><h3>Remove the brandmark</h3><p class="small">The shared component is edited. Its paired return #309 reports the file was updated.</p><details><summary>Recorded old / new strings</summary><pre id="editJson"></pre></details></div>
<div class="box"><span class="tag">RECHECK · #340–346</span><h3>Homepage screenshot</h3><p class="short">The later check visits <code>/</code> and reads <code>home_final.png</code>.</p><p class="small">It supplies evidence about the homepage, not a new screenshot of Winston.</p><button class="pick" data-full="recheck">Inspect that actual image</button></div>
</div>
<div class="callout warn">The recheck reference is preserved. It does not prove the Winston page was visually checked after the edit.</div>
<div class="twocol"><div class="box"><h3>Repair success needs comparable states</h3><div class="tablewrap"><table><thead><tr><th>Independent goal</th><th>V3 before #308</th><th>V4 after #308</th></tr></thead><tbody><tr><td>Winston testimonial</td><td><span class="state unknown">unknown</span></td><td><span class="state unknown">unknown</span></td></tr></tbody></table></div><p class="small" style="margin-top:10px">This goal was not scheduled in the saved independent acceptance run. RS is N/A, not success or failure.</p></div><div class="box"><h3>Preservation is a separate question</h3><p class="small">Fixed checks on V3 and V4 determine whether previously passing goals remain passing.</p><p><b id="preservationCounts"></b></p><p class="small">A known rate can be reported. Missing checks still prevent a complete preservation claim.</p><details><summary>Actual before / after state rows</summary><pre id="stateRows"></pre></details></div></div>
<details><summary>Original repair and recheck references</summary><pre id="repairRefs"></pre></details>
</section>

<section class="panel" id="step4" role="tabpanel" aria-labelledby="tab4" hidden>
<span class="tag code">PYTHON: COUNT LABELS, APPLY FIXED FORMULAS</span>
<h2 id="scoreHeading">What this round contributes.</h2>
<p class="intro" id="scoreIntro">The model labels are turned into counts. The repair is still unresolved, even though the inspection and diagnosis labels are positive.</p>
<div class="switch" aria-label="Score scope"><button id="roundScope" aria-pressed="true">This round + its edit</button><button id="trajectoryScope" aria-pressed="false">Whole saved trajectory</button></div>
<div class="scoregrid" id="scoreCards"></div>
<div class="callout" id="countNote">Repeated checks share goal IDs, so inspecting the same goal twice does not count it twice.</div>
<details><summary>Inspect the computed metric records</summary><pre id="metricJson"></pre></details>
<div class="callout warn">Historical pilot: the criteria were draft. These saved judgments are an inspectable example, not validated gold labels or a fresh run with the new model-reviewed plan.</div>
</section>

<div class="controls"><button id="previous" disabled>← Previous</button><span class="status" id="position">Step 1 of 5</span><button class="next" id="next">Judge input →</button></div>
<footer>
<b>Evidence → labels → counts. No human approval step is required by the current evaluator.</b>
<div class="links"><a href="#" id="downloadRecord">Download round + original events (JSON)</a><a href="#" id="downloadInput">Download actual judge input (JSON)</a><a href="#" id="downloadResponse">Download recorded model response (JSON)</a></div>
<p style="margin-top:12px">This page reads saved results. It makes no API calls, edits no scores, and can be opened as a local HTML file.</p>
</footer>
</main>
<dialog id="imageDialog"><button class="close" id="closeDialog">Close image</button><img id="dialogImage" alt="Full original recorded image"></dialog>
<script type="application/json" id="exampleData">__DATA__</script>
<script>
'use strict';
const data=JSON.parse(document.getElementById('exampleData').textContent);
const byId=id=>document.getElementById(id);
const pretty=value=>JSON.stringify(value,null,2);
const events=new Map(data.events.map(e=>[e.ordinal,e]));
const labels=['Capabilities','Case-study metrics','Testimonial white box'];
byId('diagnosis').textContent=events.get(286).text;
byId('episodeRecord').textContent=pretty(data.episode);
byId('inputPacket').textContent=pretty(data.input_packet);
byId('requestPrompt').textContent=data.model_response.prompt;
byId('rawResponse').textContent=data.model_response.raw;
byId('editJson').textContent=pretty(events.get(308).payload);
byId('repairRefs').textContent=pretty({repair_links:data.episode.repair_links,recheck_rounds:data.rechecks});
byId('stateRows').textContent=pretty(data.repairs[0].states);
byId('preservationCounts').textContent=`${data.local_metrics.CP.success_count} preserved · ${data.local_metrics.CP.unknown_count} unknown · 1 failing baseline excluded`;
byId('agentImage').src=data.images.observed; byId('referenceImage').src=data.images.reference;
for(const goal of data.catalogue.checks.filter(g=>g.check_id.startsWith('winston'))){
 const p=document.createElement('p');p.className='small';
 const b=document.createElement('b');b.textContent=goal.check_id;p.append(b,document.createElement('br'),goal.criterion);byId('goals').append(p);
}
function selectTarget(i){byId('targetName').textContent=labels[i];byId('targetJson').textContent=pretty(data.model_response.parsed.targets[i]);}
for(const [i,t] of data.result.targets.entries()){
 const row=document.createElement('tr');const td=document.createElement('td');const pick=document.createElement('button');pick.className='pick';pick.textContent=labels[i];pick.onclick=()=>selectTarget(i);td.append(pick);row.append(td);
 for(const value of [t.actual_state,t.agent_state]){const cell=document.createElement('td');const badge=document.createElement('span');badge.className='state '+value;badge.textContent=value;cell.append(badge);row.append(cell);}
 const diagnosis=document.createElement('td');diagnosis.textContent='Correct';row.append(diagnosis);
 const match=document.createElement('td');match.textContent=t.check_id ? t.check_id : 'No · extra check';row.append(match);byId('targetTable').append(row);
}
selectTarget(2);
let current=0;
const nextLabels=['Judge input →','Judge labels →','Edit & recheck →','The scores →','End'];
function setStep(i){current=i;for(let n=0;n<5;n++){byId('step'+n).hidden=n!==i;byId('tab'+n).setAttribute('aria-selected',String(n===i));byId('tab'+n).tabIndex=n===i?0:-1;}byId('previous').disabled=i===0;byId('next').disabled=i===4;byId('next').textContent=nextLabels[i];byId('position').textContent=`Step ${i+1} of 5`;}
for(let i=0;i<5;i++)byId('tab'+i).onclick=()=>setStep(i);
document.querySelector('.stepper').addEventListener('keydown',e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();const i=(current+(e.key==='ArrowRight'?1:4))%5;setStep(i);byId('tab'+i).focus();}});
byId('previous').onclick=()=>setStep(Math.max(0,current-1));byId('next').onclick=()=>setStep(Math.min(4,current+1));
function focusImage(id){const frame=byId(id);const image=frame.querySelector('img');frame.scrollTop=image.clientHeight*.505;}
document.querySelectorAll('[data-focus]').forEach(b=>b.onclick=()=>focusImage(b.dataset.focus));
document.querySelectorAll('[data-full]').forEach(b=>b.onclick=()=>{byId('dialogImage').src=data.images[b.dataset.full];byId('imageDialog').showModal();byId('imageDialog').scrollTop=0;});
byId('closeDialog').onclick=()=>byId('imageDialog').close();
byId('imageDialog').addEventListener('click',e=>{if(e.target===byId('imageDialog'))byId('imageDialog').close();});
function percent(value){return value===null?'N/A':`${value.toFixed(2).replace(/\.00$/,'')}%`;}
function ratio(row){return `${row.success_count} / (${row.success_count} + ${row.failure_count})`;}
const metricNames={VC:'Coverage',CV:'Check validity',BDA:'Diagnosis accuracy',RS:'Repair success',CP:'Correctness preservation',VCS:'Verification-chain success'};
function setScope(whole){
 const m=whole?data.trajectory_metrics:data.local_metrics;
 byId('roundScope').setAttribute('aria-pressed',String(!whole));byId('trajectoryScope').setAttribute('aria-pressed',String(whole));
 byId('scoreHeading').textContent=whole?'How the whole saved trajectory is counted.':'What this round contributes.';
 byId('scoreIntro').textContent=whole?`${data.trajectory_summary.episode_count} extracted rounds, including ${data.trajectory_summary.image_episode_count} with image input. Eligible rounds contribute target labels; the program deduplicates goal IDs and counts outcomes.`:'The model labels are turned into counts. The repair is still unresolved, even though the inspection and diagnosis labels are positive.';
 const explanations={
 VC:whole?'Count unique fully checked required IDs. The white-box target has no required ID; repeated goals add nothing.':'Two full required IDs: winston_capabilities and winston_metrics. The white-box target does not enlarge the fixed list.',
 CV:whole?'Each target counts as valid only when method_ok AND evidence_ok are true.':'All three targets have method_ok=true and evidence_ok=true. 3 valid, 0 invalid.',
 BDA:whole?'Average diagnosis accuracy for normal and erroneous targets. Unknown states are reported separately.':'2 normal targets correctly called normal; 1 error correctly identified. Both class accuracies are 100%.',
 RS:whole?'Only comparable failing-before repair targets enter this fraction. Missing repair evaluations remain separately recorded.':'Winston repair states are unknown before and after. No known success or failure enters the denominator.',
 CP:whole?'Count pass→pass versus pass→fail on required goals. This partial rate cannot certify overall preservation.':'For Edit 308: 6 pass→pass, 0 pass→fail; 20 unknown. The one already failing baseline is excluded.',
 VCS:whole?'Count episodes where valid checks, correct diagnoses, required repair and preservation all hold. A known failure dominates unknown.':'Check and diagnosis labels pass; repair and complete preservation are unknown. The chain is unknown.'
 };
 const formulas={VC:whole?`100 × ${m.VC.success_count} / ${m.VC.denominator}`:`+${m.VC.success_count} required goal IDs\nWhole-task denominator: ${m.VC.denominator}`,CV:`100 × ${ratio(m.CV)}`,BDA:`100 × (${m.BDA.normal.success_count}/${m.BDA.normal.denominator} + ${m.BDA.error.success_count}/${m.BDA.error.denominator}) / 2`,RS:`100 × ${ratio(m.RS)}\nUnknown repair targets: ${m.RS.unknown_count}`,CP:`100 × ${ratio(m.CP)}\nUnknown preservation checks: ${m.CP.unknown_count}`,VCS:`100 × ${ratio(m.VCS)}\nUnknown chains: ${m.VCS.unknown_count}`};
 byId('scoreCards').replaceChildren();
 for(const key of Object.keys(metricNames)){
  const card=document.createElement('article');card.className='scorecard';
  const title=document.createElement('h3');title.textContent=`${key} · ${metricNames[key]}`;
  const value=document.createElement('div');value.className='value';value.textContent=key==='VC'&&!whole?`+${m.VC.success_count} goal IDs`:key==='VCS'&&!whole?'Unknown':percent(m[key].score);
  const formula=document.createElement('div');formula.className='formula';formula.style.whiteSpace='pre-wrap';formula.textContent=formulas[key];
  const p=document.createElement('p');p.textContent=explanations[key];card.append(title,value,formula,p);byId('scoreCards').append(card);
 }
 byId('metricJson').textContent=pretty(m);
 byId('countNote').textContent=whole?`RS has ${m.RS.unassessed_episode_count} unassessed source episodes. CP is incomplete with ${m.CP.unknown_count} unknowns. Scores stay separate; there is no weighted overall score.`:'Coverage is a whole-task statistic. This round contributes two fixed IDs; a later repeat of the same IDs does not increase coverage.';
}
setScope(false);
byId('roundScope').onclick=()=>setScope(false);byId('trajectoryScope').onclick=()=>setScope(true);
function download(id,name,value){byId(id).href=URL.createObjectURL(new Blob([pretty(value)+'\n'],{type:'application/json'}));byId(id).download=name;}
download('downloadRecord','winston-verification-round.json',{source_run:data.source_run,source_run_sha256:data.source_run_sha256,episode:data.episode,events:data.events,rechecks:data.rechecks});
download('downloadInput','winston-recorded-judge-input.json',data.input_packet);
download('downloadResponse','winston-recorded-model-response.json',data.model_response);
window.walkthroughReady=true;
</script>
</body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New self-contained HTML file.")
    args = parser.parse_args()
    data = example_data()
    # Preserve source strings while preventing them from closing the JSON script element.
    embedded = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        output.write(PAGE.replace("__DATA__", embedded))
    print(json.dumps({"output": str(args.output.resolve()), "episode_id": EPISODE_ID,
                      "target_count": len(data["result"]["targets"]),
                      "model": data["model_response"]["model"], "api_calls": 0}))


if __name__ == "__main__":
    main()
