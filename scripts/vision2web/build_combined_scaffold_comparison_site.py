#!/usr/bin/env python3
"""Build one Vision2Web comparison portal with setting-specific viewers."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = PROJECT_ROOT / "reports/vision2web_scaffold_model_comparison"
BUILDER = PROJECT_ROOT / "scripts/vision2web/build_scaffold_model_comparison_site.py"
CASE_ID = "frontend/smartrecruiters"

SETTINGS = (
    {
        "id": "self_verify",
        "label": "Self Verify · historical six-way",
        "condition": "self_verify",
        "prompt": "Official task prompt + the short self-verification instruction; browser tools are available.",
        "run_root": PROJECT_ROOT / "runs/vision2web_scaffold_comparison/smartrecruiters-scaffold-model-sixway-v1",
        "models": [],
        "note": "Completed historical run. The two Qwen timeouts are preserved here as evidence.",
        "analysis_source": OUTPUT / "REPORT.md",
        "job_ids": [],
        "effective_prompt_sha256": "ca5708bdf8711b3e9096801ee211bb6b02bde4d4b9d55d458ce924c2e69e35b2",
    },
    {
        "id": "tools_official_prompt",
        "label": "Tools · exact official prompt",
        "condition": "tools",
        "prompt": "Byte-identical official task prompt; browser tools are available, but there is no instruction to verify.",
        "run_root": PROJECT_ROOT / "runs/vision2web_scaffold_comparison/smartrecruiters-official-prompt-sixway-v1",
        "models": [],
        "note": "Active six-way run for measuring spontaneous browser use.",
        "analysis_source": PROJECT_ROOT / "reports/vision2web_official_prompt_model_comparison/REPORT.md",
        "job_ids": [
            "mmc-v2wop2-q38-oh", "mmc-v2wop2-q38-cc", "mmc-v2wop2-opus-oh",
            "mmc-v2wop2-opus-cc", "mmc-v2wop2-glm-oh", "mmc-v2wop2-glm-cc",
        ],
        "effective_prompt_sha256": "b8c72e84c9efd85cba8ada58c0a527e69c771776f0a2a35296bede0317da3c9f",
    },
    {
        "id": "self_verify_qwen_r2",
        "label": "Self Verify · Qwen rerun",
        "condition": "self_verify",
        "prompt": "Same self_verify condition as the historical run, rerun with a four-hour agent wall-time.",
        "run_root": PROJECT_ROOT / "runs/vision2web_scaffold_comparison/smartrecruiters-self-verify-qwen-r2",
        "models": ["Qwen3.8-27B"],
        "note": "Automatically starts after the two exact-official-prompt Qwen jobs release the shared vLLM capacity.",
        "analysis_source": None,
        "job_ids": ["mmc-v2wsv2-q38-oh", "mmc-v2wsv2-q38-cc"],
        "effective_prompt_sha256": "ca5708bdf8711b3e9096801ee211bb6b02bde4d4b9d55d458ce924c2e69e35b2",
    },
)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def build_setting(setting: dict[str, Any]) -> dict[str, Any]:
    target = OUTPUT / "settings" / setting["id"]
    command = [
        sys.executable,
        str(BUILDER),
        "--run-root",
        str(setting["run_root"]),
        "--case-id",
        CASE_ID,
        "--mode",
        setting["condition"],
        "--output",
        str(target),
    ]
    for model in setting["models"]:
        command.extend(["--only-model", model])
    subprocess.run(command, cwd=PROJECT_ROOT, check=True, stdout=subprocess.DEVNULL)
    manifest = load_json(target / "data/manifest.json", {"runs": []})
    source = setting.get("analysis_source")
    analysis = (
        Path(source).read_text(encoding="utf-8")
        if source and Path(source).is_file()
        else "The rerun is pending or active. Its native events and derived analysis will appear here during monitoring."
    )
    (target / "REPORT.md").write_text(analysis, encoding="utf-8")
    return {
        "id": setting["id"],
        "label": setting["label"],
        "condition": setting["condition"],
        "prompt": setting["prompt"],
        "note": setting["note"],
        "job_ids": setting["job_ids"],
        "effective_prompt_sha256": setting["effective_prompt_sha256"],
        "analysis": analysis,
        "run_root": str(setting["run_root"]),
        "viewer": f"settings/{setting['id']}/index.html",
        "runs": manifest.get("runs", []),
    }


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    settings = [build_setting(setting) for setting in SETTINGS]
    manifest = {
        "schema": "vision2web-combined-scaffold-comparison-1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_id": CASE_ID,
        "setting_definitions": [
            {
                "id": "official",
                "label": "official",
                "definition": "Exact official prompt and the benchmark's native official tool configuration.",
                "run_state": "Not included in the current comparison run.",
            },
            {
                "id": "tools",
                "label": "tools",
                "definition": "Exact official prompt plus browser capability; no request to inspect or verify.",
                "run_state": "Running as the exact-official-prompt six-way comparison.",
            },
            {
                "id": "self_verify",
                "label": "self_verify",
                "definition": "tools plus the short generic self-verification instruction; no forced loop.",
                "run_state": "Historical six-way run plus a separate Qwen rerun.",
            },
        ],
        "settings": settings,
        "trajectory_layout": {
            "openhands_native": "openhands.events.attempt-*.jsonl",
            "claude_code_native": "claude.events.attempt-*.jsonl",
            "workspace": "development/<timestamp>/workspace.events.jsonl",
            "ordered_development": "development/<timestamp>/development_timeline.jsonl",
            "browser": "development/<timestamp>/browser.events.jsonl",
            "versions": "development/<timestamp>/versions/{P_first,P_final}",
            "result": "result.json and comparison-summary.json",
        },
    }
    (OUTPUT / "combined-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "index.html").write_text(INDEX, encoding="utf-8")
    (OUTPUT / "combined.css").write_text(STYLE, encoding="utf-8")
    (OUTPUT / "combined.js").write_text(APP, encoding="utf-8")
    (OUTPUT / "README.md").write_text(
        "# Vision2Web scaffold comparison portal\n\n"
        "The root page distinguishes official, tools, and self_verify. Each setting has its own native-event viewer.\n",
        encoding="utf-8",
    )
    print(json.dumps({"index": str(OUTPUT / "index.html"), **manifest}, ensure_ascii=False, indent=2))
    return 0


INDEX = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Vision2Web · Setting & Trajectory Analysis</title>
  <link rel="stylesheet" href="combined.css">
</head>
<body>
  <header>
    <p class="eyebrow">Vision2Web · frontend/smartrecruiters</p>
    <h1>Prompt Setting 与真实 Agent 轨迹</h1>
    <p>统一展示 setting 定义、运行状态、OpenHands / Claude Code 原生事件流和轨迹落盘位置。页面只重建真实记录，不把工具存在误写成模型主动使用。 <a class="hero-link" href="verification_episodes/index.html">查看三条 VSV 精读轨迹 →</a></p>
  </header>
  <main>
    <section><h2>三个 setting 的边界</h2><div id="definitions" class="definitions"></div></section>
    <section><h2>实验组</h2><div id="settings" class="settings"></div></section>
    <section class="viewer-section">
      <div class="viewer-head"><div><p class="eyebrow" id="viewer-setting"></p><h2 id="viewer-title">轨迹时间轴</h2></div><a id="open-viewer" target="_blank" rel="noopener">在新页打开</a></div>
      <iframe id="viewer" title="Setting-specific native trajectory viewer"></iframe>
    </section>
    <section><h2>轨迹记录在哪里</h2><div id="storage" class="storage"></div></section>
  </main>
  <script src="combined.js"></script>
</body>
</html>
"""


STYLE = """
:root{--ink:#14213d;--muted:#667085;--line:#d7deea;--paper:#f4f7fb;--blue:#2458d3;--cyan:#0e7490;--green:#087a55;--amber:#a15c00}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.55 Inter,ui-sans-serif,system-ui,sans-serif}header{padding:36px max(24px,calc((100vw - 1500px)/2));background:linear-gradient(120deg,#101a35,#14345c);color:#fff}header h1{margin:4px 0 8px;font-size:34px}header p{max-width:980px;margin:0;color:#d6e2f2}.eyebrow{font:700 11px ui-monospace,monospace;letter-spacing:.09em;text-transform:uppercase;color:#77d3f3}main{max-width:1500px;margin:auto;padding:22px 22px 70px}section{margin:0 0 24px}h2{margin:0 0 12px}.definitions,.settings{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.card{background:#fff;border:1px solid var(--line);border-radius:13px;padding:16px}.card.active{border-color:var(--blue);box-shadow:0 0 0 2px #dbe6ff}.card h3{margin:0 0 5px}.card p{margin:5px 0;color:var(--muted)}.card details{margin-top:10px}.card summary{cursor:pointer;font-weight:700}.card details pre{max-height:360px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#101827;color:#dbe7f6;border-radius:8px;padding:10px;font:11px/1.45 ui-monospace,monospace}.pill{display:inline-block;padding:3px 8px;border-radius:999px;background:#e8eefc;color:#2449a3;font:700 10px ui-monospace,monospace}.run-list{margin:12px 0 0;padding:0;list-style:none}.run-list li{display:flex;justify-content:space-between;gap:8px;border-top:1px solid #edf0f5;padding:7px 0;font-size:12px}.status{font-family:ui-monospace,monospace;color:var(--cyan)}button{border:0;border-radius:9px;background:var(--blue);color:#fff;padding:8px 11px;font-weight:700;cursor:pointer}.viewer-section{background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden}.viewer-head{display:flex;justify-content:space-between;align-items:center;padding:15px 17px;border-bottom:1px solid var(--line)}.viewer-head h2{margin:0}.viewer-head a{color:var(--blue);font-weight:700}iframe{display:block;width:100%;height:900px;border:0;background:#fff}.storage{background:#101827;color:#dbe7f6;border-radius:12px;padding:16px}.storage code{color:#8de3ff}.storage pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:5px 0 14px;color:#dbe7f6}.muted{color:var(--muted)}@media(max-width:900px){.definitions,.settings{grid-template-columns:1fr}iframe{height:760px}}
"""

STYLE += ".hero-link{display:inline-block;margin-left:8px;color:#9de8ff;font-weight:800;text-decoration:none}"


APP = r"""
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let manifest;
function renderDefinitions(){document.querySelector('#definitions').innerHTML=manifest.setting_definitions.map(x=>`<article class="card"><span class="pill">${esc(x.label)}</span><h3>${esc(x.id)}</h3><p>${esc(x.definition)}</p><p><b>当前：</b>${esc(x.run_state)}</p></article>`).join('')}
function selectSetting(id){const s=manifest.settings.find(x=>x.id===id);if(!s)return;document.querySelectorAll('.settings .card').forEach(x=>x.classList.toggle('active',x.dataset.id===id));document.querySelector('#viewer-setting').textContent=`${s.condition} · ${manifest.case_id}`;document.querySelector('#viewer-title').textContent=s.label;document.querySelector('#viewer').src=s.viewer;document.querySelector('#open-viewer').href=s.viewer}
function renderSettings(){document.querySelector('#settings').innerHTML=manifest.settings.map(s=>`<article class="card" data-id="${esc(s.id)}"><span class="pill">${esc(s.condition)}</span><h3>${esc(s.label)}</h3><p>${esc(s.prompt)}</p><p>${esc(s.note)}</p><p><b>Effective prompt SHA:</b> <code>${esc(s.effective_prompt_sha256)}</code></p>${s.job_ids?.length?`<p><b>ClusterX:</b> <code>${s.job_ids.map(esc).join(', ')}</code></p>`:''}<ul class="run-list">${s.runs.map(r=>`<li><span>${esc(r.model)} · ${esc(r.framework)}</span><span class="status">${esc(r.status)} · ${r.events} events</span></li>`).join('')}</ul><details><summary>本组完整分析</summary><pre>${esc(s.analysis)}</pre></details><p><button data-select="${esc(s.id)}">查看完整轨迹</button></p></article>`).join('');document.querySelectorAll('[data-select]').forEach(b=>b.onclick=()=>selectSetting(b.dataset.select))}
function renderStorage(){const l=manifest.trajectory_layout;document.querySelector('#storage').innerHTML=manifest.settings.map(s=>`<div><b>${esc(s.label)}</b><pre><code>${esc(s.run_root)}/agents/</code></pre></div>`).join('')+`<div><b>目录内关键文件</b><pre>OpenHands: ${esc(l.openhands_native)}\nClaude Code: ${esc(l.claude_code_native)}\nWorkspace: ${esc(l.workspace)}\nOrdered timeline: ${esc(l.ordered_development)}\nBrowser: ${esc(l.browser)}\nVersions: ${esc(l.versions)}\nResult: ${esc(l.result)}</pre></div>`}
fetch('combined-manifest.json',{cache:'no-store'}).then(r=>r.json()).then(x=>{manifest=x;renderDefinitions();renderSettings();renderStorage();selectSetting(manifest.settings[0].id)}).catch(e=>document.querySelector('main').innerHTML=`<pre>${esc(e.stack||e)}</pre>`);
"""


if __name__ == "__main__":
    raise SystemExit(main())
