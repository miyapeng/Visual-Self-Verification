#!/usr/bin/env python3
"""Render verification rounds, evidence, and linked edits as a standalone HTML report."""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from multimodalcode.io import read_json
from multimodalcode.vsv_eval.checks import _TOOL_ERROR, _results
from multimodalcode.vsv_eval.episodes import _resolve_image, split_visual_windows


def render(input_path: Path, output_path: Path) -> dict:
    packet = read_json(input_path)
    source = Path(packet["source_run"])
    by_ordinal = {e["ordinal"]: e for e in packet["events"]}
    is_rounds = "episodes" in packet
    windows = ([{**w, "events": [by_ordinal[o] for o in w["event_ids"]]} for w in packet["windows"]]
               if is_rounds else packet["windows"])
    visual_ordinals = {w["action_ordinal"] for w in split_visual_windows(windows)[0]}
    records = packet["episodes"] if is_rounds else packet["processes"]
    ledger = packet["events"]
    event_positions = {e["ordinal"]: i for i, e in enumerate(ledger)}
    assets, missing, items = {}, [], []
    for number, record in enumerate(records, 1):
        process = ({**record, "id": record["episode_id"], "event_ordinals": record["core_event_ids"],
                    "window_ids": record["candidate_ids"]} if is_rounds else record)
        events = [by_ordinal[o] for o in process["event_ordinals"]]
        # The view dereferences original events; the saved process keeps IDs only.
        window = {**process, "events": events}
        observations = [e for e in events if e.get("kind") == "observation"]
        image_refs = []
        for event in observations:
            for image in event.get("images") or []:
                path = image if isinstance(image, str) else image.get("path")
                if not path:
                    continue
                image_refs.append({"path": path, "ordinal": event["ordinal"]})
                if path in assets or path in missing:
                    continue
                resolved = _resolve_image(path, source)
                if resolved:
                    mime = mimetypes.guess_type(resolved)[0] or "application/octet-stream"
                    assets[path] = "data:" + mime + ";base64," + base64.b64encode(Path(resolved).read_bytes()).decode("ascii")
                else:
                    missing.append(path)
        title_action = next((e for e in events if e["ordinal"] in visual_ordinals),
                            next(e for e in events if e.get("kind") == "action"))
        payload = title_action.get("payload") or {}
        title = f"{'Round' if is_rounds else 'Process'} {number} · " + (payload.get("description") or Path(payload.get("file_path") or "").name or title_action.get("tool") or process["id"])
        error_ordinals = [e["ordinal"] for e in observations if e.get("is_error") is True or _TOOL_ERROR.search(
            re.sub(r"```.*?```", "", str(e.get("text") or ""), flags=re.S)
        )]
        items.append({"window": window, "title": title, "images": image_refs, "has_error": bool(error_ordinals),
                      "error_ordinals": error_ordinals,
                      "is_visual": (record.get("verification_kind") == "visual" if is_rounds else
                                    any(e["ordinal"] in visual_ordinals for e in events if e.get("kind") == "action")),
                      "calls": [{"ordinal": e["ordinal"], "tool": e.get("tool"),
                                 "description": (e.get("payload") or {}).get("description")}
                                for e in events if e.get("kind") == "action"],
                      "shared_ids": [value for value in process["window_ids"] if value in packet.get("shared_window_ids", [])],
                      "context_events": [by_ordinal[o] for o in record.get("context_event_ids", [])],
                      "repair_links": [{**link, "events": [e for o in link["repair_event_ids"]
                                                            for e in [by_ordinal[o], *_results(ledger, event_positions[o])]]}
                                       for link in record.get("repair_links", [])]})
    data = {"packet": packet, "items": items, "assets": assets, "missing": missing, "is_rounds": is_rounds,
            "source_rounds": packet.get("source_rounds") or str(input_path.resolve())}
    # Prevent a recorded command/string from terminating the inert JSON element.
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(HTML.replace("__VSV_DATA__", encoded), encoding="utf-8")
    return {"output": str(output_path.resolve()), "processes": len(items), "image_units": sum(bool(i['images']) for i in items), "embedded_images": len(assets),
            "missing_images": missing, "bytes": output_path.stat().st_size}


HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Self-verification · Visual Self-Verification</title>
<style>
:root{--bg:#f4f6f9;--paper:#fff;--ink:#182538;--muted:#657387;--line:#dfe5ed;--blue:#245de4;--soft:#edf3ff;--amber:#a95d0a;--amber-bg:#fff6e7;--green:#147c66;--radius:14px}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}button,input,a{font:inherit}button,a{touch-action:manipulation}button{cursor:pointer}button:focus-visible,a:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid #80a6ff;outline-offset:3px}button{color:inherit;background:white;border:1px solid var(--line);border-radius:8px;padding:6px 11px}button[aria-pressed="true"]{background:var(--soft);border-color:var(--blue)}button:hover{background:var(--soft);border-color:#a4baff}button:disabled{opacity:.4;cursor:default}a{color:var(--blue)}.top{display:flex;justify-content:space-between;align-items:center;padding:17px 28px;background:var(--paper);border-bottom:1px solid var(--line);gap:16px}.brand{display:flex;align-items:center;gap:12px}.mark{width:36px;height:36px;background:var(--ink);color:white;border-radius:10px;display:grid;place-items:center;font-weight:750;font-size:12px;letter-spacing:1px}.brand-title{font-size:15px;font-weight:750}.eyebrow{font-size:10px;letter-spacing:1.5px;color:var(--muted)}.top-actions{display:flex;gap:8px;align-items:center}.download{border:1px solid var(--line);padding:6px 12px;border-radius:8px;text-decoration:none;color:var(--ink);background:white}.tag{font-size:11px;border-radius:5px;padding:3px 7px;background:var(--soft);color:var(--blue);white-space:nowrap}.tag.warn{background:var(--amber-bg);color:var(--amber)}.tag.neutral{background:#eef1f5;color:var(--muted)}.intro{padding:22px 28px 18px}.intro-line{display:flex;align-items:center;gap:12px;flex-wrap:wrap}h1{font-size:25px;letter-spacing:-.5px;margin:0;font-weight:730}.sub{color:var(--muted);font-size:12px;margin-top:5px}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:18px;max-width:870px}.stat{background:white;border:1px solid var(--line);border-radius:11px;padding:10px 14px;display:flex;align-items:baseline;gap:10px}.stat strong{font-size:23px;letter-spacing:-1px}.stat span{font-size:12px;color:var(--muted)}.layout{display:grid;grid-template-columns:268px minmax(0,1fr);gap:20px;padding:0 28px 28px}.sidebar{background:white;border:1px solid var(--line);border-radius:var(--radius);align-self:start;position:sticky;top:14px;overflow:hidden}.side-top{padding:14px;border-bottom:1px solid var(--line)}.side-label{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-bottom:10px}.search{width:100%;border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:#fafbfc;font-size:12px}.filters{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}.filter{font-size:11px;padding:4px 7px;flex:1 1 30%}.filter.active{background:var(--blue);border-color:var(--blue);color:white}.window-list{padding:8px;max-height:calc(100vh - 175px);overflow:auto}.window-button{display:block;width:100%;text-align:left;padding:10px;border:1px solid transparent;margin:2px 0;border-radius:9px;background:transparent}.window-button.active{background:var(--soft);border-color:#bdd0ff}.window-button:hover{background:#f4f7fc}.window-meta{font-size:10px;color:var(--muted);display:flex;justify-content:space-between;gap:5px}.window-title{margin-top:4px;font-size:12px;line-height:1.45;overflow-wrap:anywhere}.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#9daabc;margin-right:5px}.dot.image{background:var(--blue)}.dot.error{background:#d18720}.main{min-width:0}.main-header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:12px}.main-header h2{font-size:20px;margin:3px 0 4px;overflow-wrap:anywhere}.navigation{display:flex;gap:6px;white-space:nowrap}.badges{display:flex;gap:6px;flex-wrap:wrap}.relations{display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:white;border:1px solid var(--line);border-radius:10px;padding:9px 12px;font-size:12px;margin-bottom:14px}.relations span{color:var(--muted)}.relations button{padding:3px 8px;font-size:11px}.content{display:grid;grid-template-columns:minmax(0,1.04fr) minmax(320px,1fr);gap:16px;align-items:start}.panel{background:white;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}.content>.panel:first-child{position:sticky;top:14px}.event{scroll-margin-top:20px}.event:target{background:var(--soft)}.panel-head{padding:12px 15px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:8px}.panel-head strong{font-size:12px}.toolbar{display:flex;gap:4px;align-items:center}.toolbar button{font-size:11px;padding:3px 8px}.viewport{max-height:calc(100vh - 205px);min-height:360px;overflow:auto;background:#e8ecf2;padding:12px;text-align:center}.viewport img{display:block;margin:0 auto;max-width:none;box-shadow:0 3px 16px #1925381a;cursor:zoom-in}.image-info{padding:9px 14px;font-size:11px;color:var(--muted);overflow-wrap:anywhere}.empty-image{padding:42px 22px;text-align:center;min-height:290px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:14px;background:#fafbfe}.empty-icon{width:48px;height:48px;border:1px dashed #a4b0c1;border-radius:12px;display:grid;place-items:center;color:var(--muted);font-size:22px}.empty-image p{margin:0;font-size:12px;color:var(--muted);max-width:310px}.related-reads{display:flex;flex-direction:column;gap:7px;width:100%;max-width:280px}.related-reads button{text-align:left;font-size:12px}.evidence{padding:0 16px}.event{padding:14px 0;border-bottom:1px solid var(--line)}.event:last-child{border-bottom:none}.event-head{display:flex;gap:8px;align-items:center;font-size:10px;color:var(--muted);margin-bottom:7px}.event-label{font-size:11px;font-weight:650;color:var(--ink)}.quote{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.8}.response .quote{border-left:3px solid var(--blue);padding:8px 10px;background:#f6f8fe;border-radius:0 6px 6px 0}.event pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;border:1px solid #e7ebf1;border-radius:8px;margin:0;padding:10px;font:11px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;max-height:290px;overflow:auto}.event details summary{cursor:pointer;font-size:12px;padding:3px 0}.event details pre{margin-top:7px}.note{font-size:11px;color:var(--muted);padding:11px 15px;background:#fafbfe;border-top:1px solid var(--line)}.warning{background:var(--amber-bg);color:var(--amber)}.footer{margin-top:14px;display:flex;gap:12px;align-items:center;color:var(--muted);font-size:11px;flex-wrap:wrap}.footer button{font-size:11px}.empty-result{padding:24px;color:var(--muted);font-size:12px}.source-dialog{max-width:760px;width:90vw}.source-dialog pre{font-size:11px;white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:15px;border-radius:9px;max-height:55vh;overflow:auto}dialog{border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:0 30px 100px #10203040}dialog::backdrop{background:#142033b3}.dialog-head{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:14px}.image-dialog{width:95vw;max-width:1500px;height:94vh}.full-image-wrap{overflow:auto;height:calc(100% - 54px);background:#e8ecf2;padding:12px}.full-image-wrap img{display:block;margin:auto;max-width:100%;height:auto}.muted{color:var(--muted)}
@media(min-width:1600px){.layout{grid-template-columns:288px minmax(0,1fr)}.viewport{max-height:76vh}}
@media(max-width:1050px){.content{grid-template-columns:1fr}.viewport{max-height:65vh}.layout{grid-template-columns:230px minmax(0,1fr)}.top,.intro{padding-left:18px;padding-right:18px}.layout{padding-left:18px;padding-right:18px}}
@media(max-width:700px){.top{padding:12px}.top .eyebrow,.offline-tag{display:none}.intro{padding:18px 12px}.layout{display:block;padding:0 12px 20px}.sidebar{position:static;margin-bottom:18px}.window-list{max-height:200px}.stats{grid-template-columns:repeat(2,1fr);gap:8px}.stat{padding:7px 10px}.main-header{flex-wrap:wrap}.content{display:block}.content>.panel:first-child{position:static}.panel{margin-bottom:14px}.viewport{max-height:60vh}.top-actions{gap:5px}.download{font-size:12px;padding:6px 8px}h1{font-size:22px}.main-header h2{font-size:18px}}
</style></head><body>
<header class="top"><div class="brand"><div class="mark">VSV</div><div><div class="brand-title">Self-verification</div><div class="eyebrow">VISUAL SELF-VERIFICATION</div></div></div><div class="top-actions"><span class="tag neutral offline-tag">Offline HTML</span><a id="download" class="download" download="visual-verification-review.html">Download page</a><button id="source-button">Source & notes</button></div></header>
<section class="intro"><div class="intro-line"><h1 id="case-title"></h1><span class="tag" id="review-tag"></span><span class="tag neutral">Not scored</span></div><div class="sub" id="run-meta"></div><div class="stats"><div class="stat"><strong id="total-count"></strong><span id="unit-label">Check rounds</span></div><div class="stat"><strong id="image-count"></strong><span>Rounds with images</span></div><div class="stat"><strong id="capture-count"></strong><span id="visual-label">Visual attempts without images</span></div><div class="stat"><strong id="nonvisual-count"></strong><span>Nonvisual checks</span></div><div class="stat"><strong id="mixed-count"></strong><span>Image and text evidence</span></div><div class="stat"><strong id="error-count"></strong><span>Distinct error returns</span></div></div><div class="sub">Image checks are shown by default. Use the filters to inspect other rounds. Mixed evidence overlaps the image group. Related edits and rechecks are linked below each round.</div></section>
<div class="layout"><aside class="sidebar"><div class="side-top"><div class="side-label"><span>Event order</span><span id="visible-count"></span></div><input id="search" class="search" type="search" placeholder="Search pages, commands, or judgments…" aria-label="Search check rounds"><div class="filters"><button class="filter active" data-filter="all">All</button><button class="filter" data-filter="image">Images</button><button class="filter" data-filter="nonvisual">Nonvisual</button><button class="filter" data-filter="visual-attempt">Visual attempts</button><button class="filter" data-filter="mixed">Mixed evidence</button><button class="filter" data-filter="error">Errors</button></div></div><nav id="window-list" class="window-list" aria-label="Check rounds"></nav></aside><main class="main" id="main"></main></div>
<dialog id="source-dialog" class="source-dialog"><div class="dialog-head"><strong>Source and review notes</strong><button data-close="source-dialog">Close</button></div><p>Each round links its operations, evidence, and response to original event IDs. Context, related edits, and rechecks appear separately. Use this page to review boundaries and associations; judgments and repair outcomes remain unscored.</p><p>Blue responses quote the recorded model statements. Image metadata is labeled separately. Read survey summaries with the referenced earlier evidence. A recheck supports only the state it observed.</p><p>Screenshots and logs are embedded for offline review. Recorded commands are displayed as evidence and are not executed by this page.</p><pre id="source-info"></pre></dialog>
<dialog id="image-dialog" class="image-dialog"><div class="dialog-head"><strong id="full-image-title">Image</strong><div class="toolbar"><button id="full-size">Actual size / Fit</button><a id="save-image" class="download" download="observation.png">Download image</a><button data-close="image-dialog">Close</button></div></div><div class="full-image-wrap"><img id="full-image" alt="Application image received by the model"></div></dialog>
<script id="vsv-data" type="application/json">__VSV_DATA__</script>
<script>
'use strict';
const data=JSON.parse(document.getElementById('vsv-data').textContent), items=data.items, packet=data.packet;
const $=id=>document.getElementById(id), esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let active=items.find(i=>i.images.length)?.window.id||items[0]?.window.id, filter=data.is_rounds?'image':'all', search='', visible=items;
const labelOf=item=>item.images.length?(item.window.evidence_modalities?.includes('text')?'Image and text evidence':`${item.images.length} image input${item.images.length===1?'':'s'}`):item.is_visual?'Visual attempt without an image':'Nonvisual check';
const matchesFilter=(item,kind)=>kind==='all'||kind==='image'&&item.images.length||kind==='nonvisual'&&!item.is_visual||kind==='visual-attempt'&&item.is_visual&&!item.images.length||kind==='mixed'&&item.window.evidence_modalities?.includes('image')&&item.window.evidence_modalities?.includes('text')||kind==='error'&&item.has_error;
const hashId=decodeURIComponent(location.hash.slice(1)); if(items.some(i=>i.window.id===hashId))active=hashId;
$('case-title').textContent=(packet.case_id||'Trajectory').split('/').pop();
$('run-meta').textContent=[packet.model,packet.framework,`Mode: ${packet.mode||'Not recorded'}`].filter(Boolean).join('  /  ');
$('review-tag').textContent=(packet.annotation_status||packet.grouping_status)==='codex_reviewed'?'Codex review':packet.filter_status==='rule_candidates_only'?'Unfiltered candidates':'Annotated rounds';
$('unit-label').textContent=data.is_rounds?'Check rounds':'Check rounds';$('visual-label').textContent='Visual attempts without images';$('total-count').textContent=items.length;$('image-count').textContent=items.filter(i=>i.images.length).length;
$('nonvisual-count').textContent=items.filter(i=>!i.is_visual).length;$('mixed-count').textContent=items.filter(i=>matchesFilter(i,'mixed')).length;
$('capture-count').textContent=items.filter(i=>i.is_visual&&!i.images.length).length;$('error-count').textContent=new Set(items.flatMap(i=>i.error_ordinals)).size;
$('download').href=location.href.split('#')[0];
$('source-info').textContent=JSON.stringify({source_run:packet.source_run,source_run_sha256:packet.source_run_sha256,filter_status:packet.filter_status,selection_method:packet.selection_method,intended_use:packet.intended_use,annotation_status:packet.annotation_status,annotation_method:packet.annotation_method,grouping_status:packet.grouping_status,grouping_method:packet.grouping_method,process_count:items.length,visual_window_count:packet.visual_window_count,image_input_count:packet.image_input_count,shared_window_ids:packet.shared_window_ids,embedded_images:Object.keys(data.assets).length,missing_images:data.missing},null,2);
function choose(id){active=id;history.replaceState(null,'','#'+id);renderList();renderMain();}
function update(){visible=items.filter(i=>matchesFilter(i,filter)&&(!search||JSON.stringify(i.window).toLowerCase().includes(search)||i.title.toLowerCase().includes(search)));if(!visible.some(i=>i.window.id===active))active=visible[0]?.window.id;renderList();renderMain();}
function renderList(){
 $('visible-count').textContent=visible.length+' / '+items.length;
 $('window-list').innerHTML=visible.length?visible.map(i=>`<button class="window-button ${i.window.id===active?'active':''}" data-id="${esc(i.window.id)}" aria-current="${i.window.id===active?'true':'false'}"><div class="window-meta"><span>${esc(i.window.id)}</span><span><i class="dot ${i.images.length?'image':i.has_error?'error':''}"></i>${i.images.length?i.images.length+' image input'+(i.images.length===1?'':'s'):i.has_error?'Error':'Attempt'}</span></div><div class="window-title">${esc(i.title)}</div></button>`).join(''):'<div class="empty-result">No matching rounds. Try another search.</div>';
 $('window-list').querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>choose(b.dataset.id));
}
function eventHTML(e){
 const metadata=(e.text||'').startsWith('[Image:'), policy=['model_text','reasoning'].includes(e.kind);
 const response=policy&&!metadata;
 let label=metadata?'Image metadata':e.kind==='action'?'Tool call':e.kind==='observation'?'Tool return':policy?'Model response':e.kind;
 const head=`<div class="event-head"><span class="event-label">${esc(label)}</span><span>#${e.ordinal}</span><span>${esc(e.tool||'')}</span></div>`;
 let body='';
 if(e.kind==='action'){body=`<details><summary>Full tool call · ${esc(e.tool||'')}</summary><pre>${esc(JSON.stringify(e.payload||{text:e.text},null,2))}</pre></details>`;}
 else if(e.kind==='observation'){
  if((e.images||[]).length)body+=`<p class="quote">Returned ${(e.images||[]).length} image${e.images.length===1?'':'s'}. <button data-observation="${e.ordinal}">View image</button></p>`;
  if(e.text)body+=`<pre>${esc(e.text)}</pre>`;
  if(!e.text&&!(e.images||[]).length)body='<span class="muted">No text or images were returned.</span>';
 }else if(metadata){body=`<details><summary>Image metadata</summary><p class="quote">${esc(e.text)}</p></details>`;}
 else body=`<p class="quote">${esc(e.text||JSON.stringify(e.payload||{}))}</p>`;
 return `<section id="event-${e.ordinal}" class="event ${response?'response':''}">${head}${body}</section>`;
}
function go(id){if(!visible.some(i=>i.window.id===id)){filter='all';search='';$('search').value='';document.querySelectorAll('.filter').forEach(b=>b.classList.toggle('active',b.dataset.filter==='all'));visible=items;}choose(id);}
function referencesHTML(item){
 if(!data.is_rounds)return '';
 const context=item.context_events.length?`<details class="panel"><summary style="padding:12px 15px">Context and shared sources · ${item.context_events.length} event reference${item.context_events.length===1?'':'s'}</summary><div class="evidence">${item.context_events.map(eventHTML).join('')}</div></details>`:'';
 const repair=item.repair_links.length?item.repair_links.map(link=>`<section class="panel"><div class="panel-head"><strong>Related edits · ${link.repair_event_ids.map(o=>'#'+o).join(', ')}</strong><span class="tag neutral">Link not assessed</span></div><div class="evidence">${link.events.map(eventHTML).join('')}</div><div class="relations"><span>Linked rechecks</span>${link.recheck_episode_ids.length?link.recheck_episode_ids.map(id=>`<button data-jump="${esc(id)}">${esc(items.find(i=>i.window.id===id)?.title||id)} ↗</button>`).join(''):'No supported link'}<span>Link evidence: ${link.evidence_event_ids.map(o=>'#'+o).join(', ')}</span></div></section>`).join(''):'<div class="note">No supported repair link was annotated.</div>';
 return `<div style="display:grid;gap:12px;margin-top:16px">${context}${repair}</div>`;
}
function renderMain(){
 const item=items.find(i=>i.window.id===active);
 if(!item){$('main').innerHTML='<div class="panel empty-result">No records match the current filter.</div>';return;}
 const w=item.window;
 const index=visible.findIndex(i=>i.window.id===active);
 const linked=items.filter(i=>i.window.id!==w.id&&i.window.window_ids.some(id=>item.shared_ids.includes(id)));
 const refs=item.images, first=refs[0];
 let relation=`<span>Calls in this round</span>${item.calls.map(c=>`<button data-event="${c.ordinal}" title="${esc(c.description||c.tool)}">#${c.ordinal} ${esc(c.tool)}</button>`).join('')}`;
 if(item.shared_ids.length)relation+=`<span>Shared evidence: ${item.shared_ids.map(esc).join(', ')} · Also referenced by</span>${linked.map(i=>`<button data-jump="${esc(i.window.id)}">${esc(i.title)} ↗</button>`).join('')}`;
 let imagePanel='';
 if(first){
  imagePanel=`<div class="panel-head"><strong id="image-heading">Image received by the model · #${first.ordinal}</strong><div class="toolbar"><button id="zoom-out" aria-label="Zoom out">−</button><button id="fit-image">Fit</button><button id="zoom-in" aria-label="Zoom in">+</button><button id="expand-image">Expand</button></div></div><div class="viewport" id="viewport"><img id="observation-image" alt="${esc(item.title)} recorded image"><p id="missing-image" hidden>The image reference is preserved, but its file is unavailable. Select another image or review the recorded return.</p></div><div class="image-info" id="image-info"></div>`;
  if(refs.length>1)imagePanel+=`<div class="panel-head image-tabs" style="flex-wrap:wrap;justify-content:flex-start">${refs.map((r,n)=>`<button data-image="${n}">Image ${n+1} · #${r.ordinal}</button>`).join('')}</div>`;
 }else{
  imagePanel=`<div class="panel-head"><strong>${item.is_visual?'No recorded image input':'Text and execution evidence'}</strong><span class="tag ${item.has_error?'warn':'neutral'}">${esc(labelOf(item))}</span></div><div class="empty-image"><div class="empty-icon">▧</div><p>${item.is_visual?'This visual check has no recorded image input. Read the tool output to inspect the attempt.':'Review the recorded calls and returns on the right.'}</p><div class="related-reads">${linked.map(i=>`<button data-jump="${esc(i.window.id)}">Open ${esc(i.window.id)} · ${esc(i.title)} ↗</button>`).join('')}</div></div>`;
 }
 $('main').innerHTML=`<div class="main-header"><div><div class="badges"><span class="tag">${esc(w.id)}</span><span class="tag ${item.has_error?'warn':'neutral'}">${esc(labelOf(item))}</span>${data.is_rounds?`<span class="tag neutral">${w.judgment_event_ids.length?'Judgment '+w.judgment_event_ids.map(o=>'#'+o).join(' / '):'No linked judgment'}</span>`:''}${item.has_error&&item.images.length?'<span class="tag warn">Recorded tool error</span>':''}</div><h2>${esc(item.title)}</h2><div class="sub">${item.calls.length} tool call${item.calls.length===1?'':'s'} · ${w.events.length} recorded event${w.events.length===1?'':'s'}</div></div><div class="navigation"><button id="prev" ${index<=0?'disabled':''}>← Previous</button><button id="next" ${index>=visible.length-1?'disabled':''}>Next →</button></div></div><div class="relations">${relation}</div><div class="content"><section class="panel">${imagePanel}${item.has_error?'<div class="note warning">Recorded tool errors and timeouts are retained. Review individual returns for execution details.</div>':''}</section><section class="panel"><div class="panel-head"><strong>Recorded evidence</strong><span class="muted" style="font-size:10px">Original content and event order</span></div><div class="evidence">${w.events.map(eventHTML).join('')}</div><div class="note">Events retain their original order and content. Shared evidence is referenced without increasing the count. Assess each judgment using the evidence available at that time.</div></section></div>${referencesHTML(item)}<div class="footer"><button id="raw-window">Download round references</button><span>Use ← → to browse rounds. Click an image to expand it. Screenshots and logs are embedded.</span></div>`;
 $('prev').onclick=()=>index>0&&choose(visible[index-1].window.id);$('next').onclick=()=>index<visible.length-1&&choose(visible[index+1].window.id);
 $('main').querySelectorAll('[data-event]').forEach(b=>b.onclick=()=>{const e=$('event-'+b.dataset.event);e?.scrollIntoView({behavior:'smooth',block:'center'});e?.querySelector('details')?.setAttribute('open','');});
 $('main').querySelectorAll('[data-jump]').forEach(b=>{b.disabled=!items.some(i=>i.window.id===b.dataset.jump);b.onclick=()=>go(b.dataset.jump);});
 $('raw-window').onclick=()=>{const blob=new Blob([JSON.stringify(data.is_rounds?{source_rounds:data.source_rounds,episode:packet.episodes.find(e=>e.episode_id===w.id)}:packet.processes.find(p=>p.id===w.id),null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=w.id+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 if(first){
  let current=first,zoom=null;const img=$('observation-image');
  function show(ref){
   current=ref;zoom=null;img.style.width='100%';const available=!!data.assets[ref.path];
   $('image-heading').textContent='Image received by the model · #'+ref.ordinal;
   $('missing-image').hidden=available;img.hidden=!available;
   $('image-info').textContent=`Image event #${ref.ordinal} · ${ref.path.split('/').pop()}${available?'':' · File unavailable'}`;
   ['zoom-in','zoom-out','fit-image','expand-image'].forEach(id=>$(id).disabled=!available);
   $('main').querySelectorAll('[data-image]').forEach(b=>b.setAttribute('aria-pressed',String(refs[Number(b.dataset.image)]===ref)));
   img.onload=()=>{$('image-info').textContent=`Recorded image size ${img.naturalWidth} × ${img.naturalHeight} · Image event #${ref.ordinal} · ${ref.path.split('/').pop()}`;};
   if(available)img.src=data.assets[ref.path];else img.removeAttribute('src');
  }
  function resize(mult){zoom=(zoom===null?img.clientWidth/img.naturalWidth:zoom)*mult;zoom=Math.min(4,Math.max(.1,zoom));img.style.width=Math.round(img.naturalWidth*zoom)+'px';}
  $('zoom-in').onclick=()=>resize(1.25);$('zoom-out').onclick=()=>resize(.8);$('fit-image').onclick=()=>{zoom=null;img.style.width='100%';};
  function expand(){const full=$('full-image');full.src=data.assets[current.path];full.style.maxWidth='100%';$('full-image-title').textContent=`${w.id} · Image event #${current.ordinal}`;$('save-image').href=full.src;$('save-image').download=current.path.split('/').pop();$('image-dialog').showModal();}
  img.onclick=expand;$('expand-image').onclick=expand;
  $('main').querySelectorAll('[data-observation]').forEach(b=>b.onclick=()=>{const ref=refs.find(r=>r.ordinal===Number(b.dataset.observation));if(ref){show(ref);$('viewport').scrollIntoView({behavior:'smooth',block:'center'});}else{const sourceItem=items.find(i=>i.images.some(r=>r.ordinal===Number(b.dataset.observation)));if(sourceItem)go(sourceItem.window.id);}});
  $('main').querySelectorAll('[data-image]').forEach(b=>b.onclick=()=>show(refs[Number(b.dataset.image)]));show(first);
 }
}
$('search').oninput=e=>{search=e.target.value.trim().toLowerCase();update();};
document.querySelectorAll('.filter').forEach(b=>b.classList.toggle('active',b.dataset.filter===filter));
document.querySelectorAll('.filter').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;document.querySelectorAll('.filter').forEach(x=>x.classList.toggle('active',x===b));update();});
$('source-button').onclick=()=>$('source-dialog').showModal();
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$('full-size').onclick=()=>{const img=$('full-image');img.style.maxWidth=img.style.maxWidth==='none'?'100%':'none';};
document.addEventListener('keydown',e=>{if(document.querySelector('dialog[open]')||['INPUT','TEXTAREA'].includes(document.activeElement.tagName))return;if(e.key==='ArrowLeft')$('prev')?.click();if(e.key==='ArrowRight')$('next')?.click();});
window.addEventListener('hashchange',()=>{const id=decodeURIComponent(location.hash.slice(1));if(items.some(i=>i.window.id===id))go(id);});
update();
</script></body></html>'''


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(render(args.input, args.output), ensure_ascii=False))
