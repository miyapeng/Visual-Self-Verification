#!/usr/bin/env python3
"""Build a self-contained visual audit of the coding-agent smoke runs."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from multimodalcode.agent_harness.cases import load_case
from multimodalcode.agent_harness.registry import PROJECT_ROOT


@dataclass(frozen=True)
class RunSpec:
    model: str
    benchmark: str
    case_id: str
    run_dir: Path


RUNS = (
    RunSpec("Qwen3.5-9B", "swe-mm", "markedjs__marked-2811", PROJECT_ROOT / "runs/agent_smoke/agents/Qwen3.5-9B/swe-mm/markedjs__marked-2811"),
    RunSpec("Qwen3-VL-30B-A3B", "swe-mm", "markedjs__marked-2811", PROJECT_ROOT / "runs/agent_smoke/agents/Qwen3-VL-30B-A3B/swe-mm/markedjs__marked-2811"),
    RunSpec("Qwen3.5-9B", "swe-mm", "processing__p5.js-6069", PROJECT_ROOT / "runs/agent_smoke/agents/Qwen3.5-9B/swe-mm/processing__p5.js-6069"),
    RunSpec("Qwen3-VL-30B-A3B", "swe-mm", "processing__p5.js-6069", PROJECT_ROOT / "runs/agent_smoke/agents/Qwen3-VL-30B-A3B/swe-mm/processing__p5.js-6069"),
    RunSpec("Qwen3.5-9B", "design2code", "1", PROJECT_ROOT / "runs/agent_smoke/agents/Qwen3.5-9B/design2code/1"),
    RunSpec("Qwen3-VL-30B-A3B", "design2code", "1", PROJECT_ROOT / "runs/agent_smoke/agents/Qwen3-VL-30B-A3B/design2code/1"),
    RunSpec("Qwen3-VL-30B-A3B", "design2code", "0", PROJECT_ROOT / "runs/agent_smoke/agents/Qwen3-VL-30B-A3B/design2code/0"),
    RunSpec("Qwen3.5-9B", "vision2web", "webpage/abc", PROJECT_ROOT / "runs/agent_smoke/agents_valid/Qwen3.5-9B/vision2web/webpage__abc"),
)


BENCHMARK_ISSUES = {
    "SWE-MM": [
        "30B 两条轨迹都调用了 git log/git show，可能看到基准提交之后的修复历史，不能算 clean run。",
        "30B Marked 在 32K context 上溢出；大文件和 Git 历史 observation 没有被压缩。",
        "9B p5 patch 修复了目标回归且 28/28 FAIL_TO_PASS 通过，但一个无关 PASS_TO_PASS 失败使官方 resolved=false。",
        "Agent 可能已经完成正确修改，却继续探索几十步才提交，停止与提交策略不稳定。",
    ],
    "Design2Code": [
        "原始用户话术提到 React component，而官方 direct-prompting 协议要求单文件 HTML/CSS；本次 smoke 的 wrapper 又曾允许本地 CSS/JS 且漏掉 rick.jpg 约束。现已对齐官方协议，但这批旧轨迹应标为 adapted prompt。",
        "9B 在最后一次截图之后继续改代码，没有对最终状态做 fresh render certification。",
        "30B case 0 用超长 shell quoting 写 HTML，破坏 action 格式并最终只得到 11-byte 页面。",
        "官方评分是静态视觉指标，不验证按钮、路由、表单或交互行为。",
    ],
    "Vision2Web": [
        "9B 找到了 prototype JPG，但只 view 目录，没有 view 具体图片；视觉上下文可用但未被消费。",
        "生成站点能返回 HTTP 200，但与 ABC Listen 目标页面完全不匹配，且退出前没有 FinishAction。",
        "OpenHands 原生工具要求 security_risk；模型多次漏填，造成额外 AgentErrorEvent。",
        "30B 当前 vLLM 使用错误的 qwen3_xml parser；必须换成 hermes 后才可得到有效 OpenHands 轨迹。",
        "尚未运行完整官方 functional/visual judge，因此当前只是 agent 与运行时适配检查，不是官方 Vision2Web 分数。",
    ],
    "跨基准": [
        "同一 output directory 强制重跑时，若新进程中断，旧 result.json 可能与新 raw trajectory 混在一起；正式实验应使用 immutable attempt ID。",
        "ClusterX 启动命令引用共享脚本；运行期间修改脚本可能造成 race，正式任务应提交脚本快照。",
        "CLI success、产物可启动、benchmark resolved 和用户意图满足是四个不同层级，不能合并为一个 success。",
    ],
}


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def safe(value: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "__" for c in value)


def copy_asset(source: Path, output: Path, group: str) -> str | None:
    if not source.is_file():
        return None
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    target = output / "assets" / safe(group) / f"{digest}-{source.name}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)
    return target.relative_to(output).as_posix()


def copy_raw(source: Path, output: Path, group: str) -> str | None:
    if not source.is_file():
        return None
    target = output / "raw" / safe(group) / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target.relative_to(output).as_posix()


def design_score(model: str, case_id: str) -> dict[str, Any] | None:
    data = read_json(PROJECT_ROOT / "runs/agent_smoke/evaluation/design2code-case1/scores.json", {})
    key = f"{model}::{case_id}"
    return next((row for row in data.get("rows", []) if row.get("key") == key), None)


def swe_score(case_id: str, model: str) -> dict[str, Any] | None:
    path = PROJECT_ROOT / f"runs/agent_smoke/evaluation/{model}/{case_id}/report.json"
    report = read_json(path, {})
    row = report.get(case_id)
    if not row:
        return None
    tests = row.get("tests_status", {})
    ftp = tests.get("FAIL_TO_PASS", {})
    ptp = tests.get("PASS_TO_PASS", {})
    return {
        "resolved": row.get("resolved"),
        "patch_applied": row.get("patch_successfully_applied"),
        "fail_to_pass": f"{len(ftp.get('success', []))} pass / {len(ftp.get('failure', []))} fail",
        "pass_to_pass": f"{len(ptp.get('success', []))} pass / {len(ptp.get('failure', []))} fail",
        "pass_to_pass_failures": ptp.get("failure", []),
    }


def metric_html(spec: RunSpec) -> str:
    score = design_score(spec.model, spec.case_id) if spec.benchmark == "design2code" else None
    if score:
        keys = ("score", "block_match", "text", "position", "color", "clip")
        cells = "".join(f"<div><b>{esc(k)}</b><span>{score[k]:.6f}</span></div>" for k in keys)
        return f'<section class="metrics"><h4>官方 Design2Code 分数</h4><div class="metric-grid">{cells}</div></section>'
    score = swe_score(spec.case_id, spec.model) if spec.benchmark == "swe-mm" else None
    if score:
        cells = "".join(
            f"<div><b>{esc(k)}</b><span>{esc(v)}</span></div>"
            for k, v in score.items()
            if k != "pass_to_pass_failures"
        )
        failures = "\n".join(score.get("pass_to_pass_failures", []))
        return f'<section class="metrics"><h4>官方 SWE-MM evaluator</h4><div class="metric-grid">{cells}</div><pre>{esc(failures)}</pre></section>'
    return '<section class="metrics muted">没有官方 case 分数（未提交、失败，或仅做适配检查）。</section>'


def image_gallery(paths: list[tuple[str, Path]], output: Path, group: str) -> str:
    items = []
    for label, path in paths:
        copied = copy_asset(path, output, group)
        if copied:
            items.append(
                f'<figure><a href="{esc(copied)}" target="_blank"><img loading="lazy" src="{esc(copied)}"></a><figcaption>{esc(label)}</figcaption></figure>'
            )
    return '<div class="gallery">' + "".join(items) + "</div>" if items else '<p class="muted">无可展示图片</p>'


def trajectory_html(events: list[dict[str, Any]]) -> str:
    rows = []
    for event in events:
        actor = str(event.get("actor", "unknown"))
        kind = str(event.get("kind", "event"))
        tool = event.get("tool") or ""
        text = str(event.get("text", ""))
        sequence = event.get("sequence", "?")
        error = "error" in kind.lower() or bool(event.get("error_code"))
        open_attr = " open" if actor in {"assistant", "agent"} or error else ""
        search = esc(f"{actor} {kind} {tool} {text}".lower())
        rows.append(
            f'<details class="event actor-{esc(actor)} {"event-error" if error else ""}" data-search="{search}"{open_attr}>'
            f'<summary><span class="seq">#{esc(sequence)}</span><span class="actor">{esc(actor)}</span>'
            f'<span class="kind">{esc(kind)}</span><span class="tool">{esc(tool)}</span></summary>'
            f'<pre>{esc(text)}</pre></details>'
        )
    return "".join(rows)


def run_html(spec: RunSpec, output: Path) -> str:
    case = load_case(spec.benchmark, spec.case_id)
    result = read_json(spec.run_dir / "result.json", {})
    audit = read_json(spec.run_dir / "trajectory_audit.json", {})
    trajectory = read_json(spec.run_dir / "trajectory.json", {"events": []})
    group = f"{spec.model}-{spec.benchmark}-{spec.case_id}"
    raw_links = []
    for name in ("case.json", "result.json", "trajectory.json", "trajectory_audit.json", "patch.diff"):
        copied = copy_raw(spec.run_dir / name, output, group)
        if copied:
            raw_links.append(f'<a href="{esc(copied)}" target="_blank">{esc(name)}</a>')
    images = [(f"题目输入 {i + 1}", Path(path)) for i, path in enumerate(case.image_paths)]
    candidate = spec.run_dir / "workspace" / "candidate.png"
    if candidate.is_file():
        images.append((f"{spec.model} 最后一次保存的 candidate.png", candidate))
    if spec.benchmark == "vision2web":
        images.append(
            (
                "Qwen3.5-9B 生成页面（独立容器渲染）",
                PROJECT_ROOT / "runs/agent_smoke/evaluation/Qwen3.5-9B/vision2web/webpage__abc/render/generated-desktop.png",
            )
        )
    status = str(result.get("status", "missing"))
    categories = audit.get("categories", {})
    risks = audit.get("risk_indicators", {})
    category_badges = "".join(
        f'<span class="badge {"yes" if value else "no"}">{esc(name)}={str(value).lower()}</span>'
        for name, value in categories.items()
    )
    risk_badges = "".join(
        f'<span class="badge {"risk" if value else "no"}">{esc(name)}={str(value).lower()}</span>'
        for name, value in risks.items()
    )
    error = result.get("error") or result.get("agent_outcome", {}).get("exit_status") or ""
    return f'''
<article class="run" data-benchmark="{esc(spec.benchmark)}" data-model="{esc(spec.model)}" data-status="{esc(status)}">
  <header><div><span class="eyebrow">{esc(spec.benchmark)} · {esc(case.scaffold)}</span><h2>{esc(spec.case_id)}</h2><p>{esc(spec.model)}</p></div><span class="status status-{esc(status)}">{esc(status)}</span></header>
  <div class="run-summary"><span>{esc(audit.get("assistant_action_count", 0))} agent actions</span><span>{esc(audit.get("observation_event_count", 0))} observations</span><span>{esc(audit.get("event_count", len(trajectory.get("events", []))))} normalized events</span><span>{esc(error)}</span></div>
  <section><h3>题目与模型产物</h3>{image_gallery(images, output, group)}</section>
  <details class="prompt"><summary>展开完整任务文本</summary><pre>{esc(case.prompt)}</pre></details>
  {metric_html(spec)}
  <section><h3>轨迹审计</h3><div class="badges">{category_badges}{risk_badges}</div><p class="raw-links">{' · '.join(raw_links)}</p></section>
  <section class="trajectory"><div class="trajectory-title"><h3>Agent 作答过程</h3><span>默认展开 agent action；点击 observation 查看反馈</span></div>{trajectory_html(trajectory.get("events", []))}</section>
</article>'''


def issues_html() -> str:
    sections = []
    for name, issues in BENCHMARK_ISSUES.items():
        sections.append(
            f'<section class="issue-card"><h3>{esc(name)}</h3><ol>'
            + "".join(f"<li>{esc(issue)}</li>" for issue in issues)
            + "</ol></section>"
        )
    return "".join(sections)


STYLE = """
:root{color-scheme:light;--ink:#19212b;--muted:#667085;--line:#d9e0e8;--bg:#f3f6f9;--card:#fff;--accent:#375dfb;--good:#067647;--warn:#b54708;--bad:#b42318}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}main{max-width:1500px;margin:auto;padding:38px 24px 80px}.hero{background:linear-gradient(135deg,#14213d,#2648a8);color:#fff;border-radius:22px;padding:36px;margin-bottom:22px}.hero h1{font-size:34px;margin:0 0 8px}.hero p{max-width:900px;color:#d9e2ff}.controls{position:sticky;top:0;z-index:5;background:rgba(243,246,249,.95);backdrop-filter:blur(10px);padding:12px 0;display:flex;gap:10px;flex-wrap:wrap}.controls input,.controls select{border:1px solid var(--line);border-radius:9px;padding:10px 12px;background:white}.issues{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin:18px 0 32px}.issue-card,.run{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:0 8px 30px rgba(15,23,42,.05)}.issue-card{padding:18px}.issue-card h3{margin-top:0}.issue-card li{margin:7px 0}.run{padding:24px;margin:22px 0}.run>header{display:flex;justify-content:space-between;gap:20px;border-bottom:1px solid var(--line);padding-bottom:16px}.run h2{font-size:26px;margin:2px 0}.run header p,.eyebrow{color:var(--muted);margin:0}.status{align-self:flex-start;padding:6px 11px;border-radius:999px;font-weight:700;background:#eef2f6}.status-success{color:var(--good);background:#ecfdf3}.status-incomplete{color:var(--warn);background:#fff7ed}.status-failed,.status-timeout{color:var(--bad);background:#fef3f2}.run-summary{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}.run-summary span,.badge{border-radius:999px;background:#f2f4f7;padding:5px 9px;font-size:13px}.gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}.gallery figure{margin:0;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:#f8fafc}.gallery img{width:100%;height:280px;object-fit:contain;display:block;background:white}.gallery figcaption{padding:9px 11px;color:var(--muted)}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#101828;color:#e6edf6;padding:14px;border-radius:10px;max-height:600px;overflow:auto}.prompt{margin:18px 0}.prompt summary,.event summary{cursor:pointer}.metrics{padding:14px;background:#f8fafc;border-radius:12px;margin:14px 0}.metrics h4{margin:0 0 10px}.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}.metric-grid div{display:flex;flex-direction:column;background:#fff;border:1px solid var(--line);padding:9px;border-radius:8px}.metric-grid span{font-family:ui-monospace,monospace}.badges{display:flex;gap:7px;flex-wrap:wrap}.badge.yes{background:#ecfdf3;color:var(--good)}.badge.risk{background:#fef3f2;color:var(--bad)}.badge.no{color:var(--muted)}.raw-links a{color:var(--accent)}.trajectory-title{display:flex;align-items:baseline;justify-content:space-between}.trajectory-title span{color:var(--muted)}.event{border:1px solid var(--line);border-radius:10px;margin:7px 0;overflow:hidden}.event summary{display:flex;gap:10px;padding:9px 11px;background:#f8fafc;align-items:center}.event pre{margin:0;border-radius:0;max-height:520px}.event .seq{font-family:ui-monospace,monospace;color:var(--muted)}.event .actor{font-weight:700}.event .tool{margin-left:auto;color:var(--accent)}.actor-assistant,.actor-agent{border-left:4px solid #375dfb}.actor-environment,.actor-tool{border-left:4px solid #98a2b3}.event-error{border-left:4px solid var(--bad)}.muted{color:var(--muted)}@media(max-width:650px){main{padding:18px 10px}.hero,.run{padding:18px}.gallery img{height:220px}.trajectory-title{display:block}.event summary{flex-wrap:wrap}.event .tool{margin-left:0}}
"""


SCRIPT = """
const q=document.querySelector('#search'),b=document.querySelector('#benchmark'),m=document.querySelector('#model'),s=document.querySelector('#status');function filter(){for(const run of document.querySelectorAll('.run')){const okB=!b.value||run.dataset.benchmark===b.value,okM=!m.value||run.dataset.model===m.value,okS=!s.value||run.dataset.status===s.value,needle=q.value.toLowerCase(),okQ=!needle||run.innerText.toLowerCase().includes(needle);run.hidden=!(okB&&okM&&okS&&okQ)}}[q,b,m,s].forEach(x=>x.addEventListener('input',filter));
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "runs/agent_smoke/html_report")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    available = [spec for spec in RUNS if (spec.run_dir / "trajectory.json").is_file()]
    runs = "".join(run_html(spec, output) for spec in available)
    models = sorted({spec.model for spec in available})
    benchmarks = sorted({spec.benchmark for spec in available})
    statuses = sorted({read_json(spec.run_dir / "result.json", {}).get("status", "missing") for spec in available})
    options = lambda values: "".join(f'<option value="{esc(v)}">{esc(v)}</option>' for v in values)
    document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MultimodalCode Agent Smoke Audit</title><style>{STYLE}</style></head><body><main>
<section class="hero"><h1>MultimodalCode · Coding-Agent 轨迹审计</h1><p>展示 SWE-MM、Design2Code 与 Vision2Web 的题目输入、模型产物、逐步 action/observation、官方评分及已知适配问题。共 {len(available)} 条有效或可解释轨迹；早期纯基础设施失败已排除。</p></section>
<section><h2>目前发现的问题</h2><div class="issues">{issues_html()}</div></section>
<div class="controls"><input id="search" placeholder="搜索命令、错误或文本"><select id="benchmark"><option value="">全部 benchmark</option>{options(benchmarks)}</select><select id="model"><option value="">全部模型</option>{options(models)}</select><select id="status"><option value="">全部状态</option>{options(statuses)}</select></div>
<section id="runs">{runs}</section></main><script>{SCRIPT}</script></body></html>'''
    (output / "index.html").write_text(document, encoding="utf-8")
    manifest = {
        "schema": "multimodalcode-agent-html-audit-1",
        "run_count": len(available),
        "runs": [
            {"model": spec.model, "benchmark": spec.benchmark, "case_id": spec.case_id, "source": str(spec.run_dir)}
            for spec in available
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output / "index.html"), **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
