#!/usr/bin/env python3
"""Build a historical batch-interface verification showcase.

The page is curated around observable verification episodes. It does not infer
hidden reasoning, run the official evaluator, or read workflow.json.
It preserves already-collected exploratory evidence and is not an active
Vision2Web inference entry point.
"""

from __future__ import annotations

import base64
import html
import json
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = ROOT / "runs/vision2web_scaffold_comparison/smartrecruiters-pjlab-policy-v1"
OUTPUT = ROOT / "reports/vision2web_scaffold_model_comparison/verification_episodes"
TASK_ROOT = ROOT / "data/vision2web/extracted/frontend/smartrecruiters"
OH = RUN_ROOT / "agents/litellm_proxy__glm-5.3-flash/vision2web/browser_enabled/frontend__smartrecruiters"
KIMI = RUN_ROOT / "agents/kimi-k2.6/vision2web/claude_code/guided_vsv/frontend__smartrecruiters"
GLM_CC = RUN_ROOT / "agents/glm-5.3-flash/vision2web/claude_code/official/frontend__smartrecruiters"
OH_DEV = OH / "development/20260828T060502.700596Z"


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def copy_asset(source: Path, relative: str) -> str:
    target = OUTPUT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return relative


def copy_named(source: Path, group: str) -> str:
    return copy_asset(source, f"assets/{group}/{source.name}")


def json_lines(path: Path):
    for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.startswith("{"):
            continue
        try:
            yield number, json.loads(line)
        except json.JSONDecodeError:
            continue


def claude_blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def extract_claude_images(events_path: Path, tool_ids: dict[str, str], group: str) -> dict[str, str]:
    """Decode images that really appeared in model-facing tool_result blocks."""
    result: dict[str, str] = {}
    selected_events: list[dict[str, Any]] = []
    for line_number, event in json_lines(events_path):
        selected_blocks = []
        for block in claude_blocks(event):
            identifier = str(block.get("id") or block.get("tool_use_id") or "")
            if identifier not in tool_ids:
                continue
            selected_blocks.append({k: v for k, v in block.items() if k not in {"content", "source"}})
            if block.get("type") != "tool_result" or not isinstance(block.get("content"), list):
                continue
            for image_block in block["content"]:
                if not isinstance(image_block, dict) or image_block.get("type") != "image":
                    continue
                source = image_block.get("source") if isinstance(image_block.get("source"), dict) else {}
                encoded = source.get("data")
                if not isinstance(encoded, str):
                    continue
                suffix = ".jpg" if source.get("media_type") == "image/jpeg" else ".png"
                relative = f"assets/{group}/{tool_ids[identifier]}{suffix}"
                target = OUTPUT / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(encoded))
                result[tool_ids[identifier]] = relative
        if selected_blocks:
            selected_events.append({"raw_line": line_number, "blocks": selected_blocks})
    raw_path = OUTPUT / f"assets/raw/{group}-selected-events.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(selected_events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def image(path: str, caption: str, badge: str = "模型真实看到了 / pixels entered context") -> str:
    return f"""<figure class="shot"><a href="{esc(path)}" target="_blank"><img loading="lazy" src="{esc(path)}" alt="{esc(caption)}"></a><figcaption><span class="mini-ok">{esc(badge)}</span>{esc(caption)}</figcaption></figure>"""


def quote(zh: str, en: str) -> str:
    return f"""<div class="judgment"><div><span class="io-label decision">模型判断 / DECISION</span><p>{esc(zh)}</p></div><details><summary>查看模型记录的英文原文</summary><blockquote>{esc(en)}</blockquote></details></div>"""


def action_block(title: str, code: str, output: str = "") -> str:
    output_html = f'<div class="io-output"><span class="io-label output">环境输出 / OUTPUT</span><pre>{esc(output)}</pre></div>' if output else ""
    return f"""<div class="io-action"><span class="io-label action">工具输入 / ACTION</span><h5>{esc(title)}</h5><pre>{esc(code)}</pre>{output_html}</div>"""


def patch_block(title: str, code: str) -> str:
    return f"""<div class="io-patch"><span class="io-label patch">代码修改 / PATCH</span><h5>{esc(title)}</h5><pre>{esc(code)}</pre></div>"""


def stage(number: str, title: str, body: str, kind: str = "") -> str:
    return f"""<article class="stage {esc(kind)}"><div class="stage-index">{esc(number)}</div><div class="stage-body"><h4>{esc(title)}</h4>{body}</div></article>"""


def redact_large_values(value: Any, key: str = "") -> Any:
    """Keep raw event excerpts inspectable without duplicating base64 images."""
    if isinstance(value, dict):
        return {k: redact_large_values(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_large_values(v, key) for v in value]
    if isinstance(value, str) and len(value) > 512 and (
        key == "data" or value.startswith("data:image/") or "iVBORw0KGgo" in value[:160]
    ):
        return f"<base64 image omitted; {len(value)} characters>"
    if isinstance(value, str) and len(value) > 20_000:
        return value[:20_000] + f"\n<text truncated; original {len(value)} characters>"
    return value


def export_event_excerpt(source: Path, line_ranges: list[tuple[int, int]], relative: str) -> str:
    selected: list[dict[str, Any]] = []
    for line_number, event in json_lines(source):
        if not any(start <= line_number <= end for start, end in line_ranges):
            continue
        if event.get("type") == "system" or event.get("kind") == "Condensation":
            continue
        selected.append({"raw_line": line_number, "event": redact_large_values(event)})
    target = OUTPUT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return relative


def execution_reset_image(execution_id: str, scenario_name: str, filename: str) -> str:
    data = load_json(OH_DEV / f"executions/{execution_id}.json")
    for scenario in data.get("scenario_results", []):
        if scenario.get("name") != scenario_name:
            continue
        evidence = (scenario.get("reset_observation") or {}).get("evidence") or {}
        source = (evidence.get("screenshot") or {}).get("path")
        if source and Path(source).is_file():
            return copy_asset(Path(source), f"assets/turns/{filename}")
    raise FileNotFoundError(f"No reset screenshot for {execution_id}: {scenario_name}")


def turn_card(
    number: str,
    title: str,
    received: str,
    model_output: str,
    tool_call: str,
    environment_output: str,
    english: str = "",
    received_screenshot: str = "",
    returned_screenshot: str = "",
    environment_extra: str = "",
) -> str:
    original = f"<details><summary>英文原始记录 / original excerpt</summary><blockquote>{esc(english)}</blockquote></details>" if english else ""
    input_visual = image(received_screenshot, f"Turn {number} 开始时已进入模型上下文的图像") if received_screenshot else ""
    output_visual = image(returned_screenshot, f"Turn {number} 工具返回的图像；从下一轮起进入模型上下文") if returned_screenshot else ""
    return f"""<article class="turn-card"><div class="turn-number">TURN {esc(number)}</div><div class="turn-main"><h4>{esc(title)}</h4><div class="turn-io received"><b>这一轮模型收到 / INPUT</b><p>{esc(received)}</p>{input_visual}</div><div class="turn-io model"><b>模型输出 / MODEL OUTPUT</b><p>{esc(model_output)}</p>{original}</div><div class="turn-io tool"><b>工具调用 / ACTION</b><pre>{esc(tool_call)}</pre></div><div class="turn-io environment"><b>工具返回 / OBSERVATION（成为下一轮输入）</b><p>{esc(environment_output)}</p>{output_visual}{environment_extra}</div></div></article>"""


def compact_recorded_observation(event: dict[str, Any]) -> str:
    observation = event.get("observation")
    if not isinstance(observation, dict):
        return json.dumps(redact_large_values(event), ensure_ascii=False, indent=2)
    text_parts = [
        item.get("text", "")
        for item in observation.get("content", [])
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    text = "\n".join(text_parts)
    if event.get("tool_name") != "browser_execute_plan":
        return text or json.dumps(redact_large_values(observation), ensure_ascii=False, indent=2)
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError:
        return text[:20_000]
    compact: dict[str, Any] = {
        "interface": manifest.get("interface"),
        "execution_id": manifest.get("execution_id"),
        "browser_makes_verdict": manifest.get("browser_makes_verdict"),
        "scenario_results": [],
    }
    for scenario in manifest.get("scenario_results", []):
        reset_observation = scenario.get("reset_observation") or {}
        reset_console = reset_observation.get("console_delta") or {}
        row = {
            "name": scenario.get("name"),
            "expectation": scenario.get("expectation"),
            "status": scenario.get("status"),
            "reset_url": (scenario.get("reset") or {}).get("url"),
            "reset_observation": {
                "url": reset_observation.get("url"),
                "console_errors": reset_console.get("errors") or [],
                "screenshot_returned": bool(
                    ((reset_observation.get("evidence") or {}).get("screenshot") or {}).get("path")
                ),
            },
            "steps": [],
        }
        for step in scenario.get("steps", []):
            state = step.get("observation") or {}
            console = state.get("console_delta") or {}
            dom = state.get("dom_change") or {}
            row["steps"].append({
                "step": step.get("step"),
                "action": step.get("action"),
                "status": step.get("status"),
                "grounding": step.get("grounding"),
                "execution_output": step.get("execution_output"),
                "url": state.get("url"),
                "dom_changed": dom.get("changed"),
                "console_errors": console.get("errors") or [],
                "runtime_error": step.get("error"),
                "screenshot_returned": bool(
                    ((state.get("evidence") or {}).get("screenshot") or {}).get("path")
                ),
            })
        compact["scenario_results"].append(row)
    return json.dumps(compact, ensure_ascii=False, indent=2)


def openhands_verbatim_replay() -> str:
    source = OH / "openhands.events.attempt-1.jsonl"
    events = {line: event for line, event in json_lines(source)}
    pairs = [
        (548, 549), (550, 551), (552, 553), (554, 555), (556, 557),
        (558, 559), (560, 561), (563, 564), (565, 566), (567, 568),
        (569, 570), (571, 572),
    ]
    rows = []
    for number, (action_line, result_line) in enumerate(pairs, 1):
        action_event = events[action_line]
        result_event = events[result_line]
        thought = "\n".join(
            block.get("text", "")
            for block in action_event.get("thought", [])
            if isinstance(block, dict) and block.get("text")
        )
        reasoning = action_event.get("reasoning_content") or ""
        action = action_event.get("action") or {}
        tool = action_event.get("tool_name") or action.get("kind") or "agent_error"
        rows.append(f"""<article class="verbatim-turn"><div class="verbatim-head"><b>RAW TURN {number}</b><span>events {action_line}→{result_line} · {esc(tool)} · {esc(action_event.get('timestamp'))}</span></div><div class="verbatim-grid"><div><h5>Recorded thought</h5><pre>{esc(thought or '(not separately recorded)')}</pre></div><div><h5>Recorded reasoning_content</h5><pre>{esc(reasoning or '(not separately recorded)')}</pre></div><div class="wide"><h5>Exact action JSON</h5><pre>{esc(json.dumps(action, ensure_ascii=False, indent=2))}</pre></div><div class="wide"><h5>Environment observation（逐 action 的状态已保留；巨大 accessibility tree 省略）</h5><pre>{esc(compact_recorded_observation(result_event))}</pre></div></div></article>""")
    return "".join(rows)


def compact_observation(step: dict[str, Any]) -> str:
    observation = step.get("observation") if isinstance(step.get("observation"), dict) else {}
    console = observation.get("console_delta") if isinstance(observation.get("console_delta"), dict) else {}
    dom = observation.get("dom_change") if isinstance(observation.get("dom_change"), dict) else {}
    lines = [
        f"status: {step.get('status')}", f"url: {observation.get('url') or '-'}",
        f"execution_output: {step.get('execution_output') or '-'}",
        f"grounding: {json.dumps(step.get('grounding'), ensure_ascii=False) if step.get('grounding') else '-'}",
        f"dom_changed: {dom.get('changed', '-')}", f"console_errors: {len(console.get('errors') or [])}",
        f"runtime_error: {step.get('error') or '-'}",
    ]
    if dom.get("diff_preview"):
        lines.append("dom_diff_preview:\n" + "\n".join(str(x) for x in dom["diff_preview"][:12]))
    return "\n".join(lines)


def execution_view(execution_id: str, title: str, open_by_default: bool = False) -> str:
    source = OH_DEV / f"executions/{execution_id}.json"
    data = load_json(source)
    raw = copy_asset(source, f"assets/raw/openhands-{execution_id}.json")
    scenarios = []
    for scenario in data.get("scenario_results", []):
        name = str(scenario.get("name", "scenario")).replace(" ", "_").replace("/", "-")
        reset_observation = scenario.get("reset_observation") or {}
        reset_evidence = reset_observation.get("evidence") or {}
        reset_screenshot = (reset_evidence.get("screenshot") or {}).get("path")
        steps = []
        if reset_screenshot and Path(reset_screenshot).is_file():
            relative = copy_asset(
                Path(reset_screenshot),
                f"assets/openhands/{execution_id}-{name}-reset.png",
            )
            reset_summary = "\n".join([
                "status: reset complete",
                f"url: {reset_observation.get('url') or (scenario.get('reset') or {}).get('url') or '-'}",
                f"console_errors: {len(((reset_observation.get('console_delta') or {}).get('errors') or []))}",
                "screenshot_returned: true",
            ])
            steps.append(f"""<div class="browser-step reset-step"><div class="step-head"><b>Reset</b><code>{esc(json.dumps(scenario.get('reset'), ensure_ascii=False))}</code></div><pre>{esc(reset_summary)}</pre>{image(relative, f'reset · {scenario.get("name")}')}</div>""")
        for step in scenario.get("steps", []):
            evidence = (step.get("observation") or {}).get("evidence") or {}
            screenshot = (evidence.get("screenshot") or {}).get("path")
            shot_html = ""
            if screenshot and Path(screenshot).is_file():
                relative = copy_asset(Path(screenshot), f"assets/openhands/{execution_id}-{name}-{step.get('step')}.png")
                shot_html = image(relative, f"step {step.get('step')} · {scenario.get('name')}")
            steps.append(f"""<div class="browser-step {'bad' if step.get('status') != 'ok' else ''}"><div class="step-head"><b>Step {step.get('step')}</b><code>{esc(json.dumps(step.get('action'), ensure_ascii=False))}</code></div><pre>{esc(compact_observation(step))}</pre>{shot_html}</div>""")
        scenarios.append(f"""<section class="scenario"><h5>{esc(scenario.get('name'))}</h5><p><b>模型提交的预期 / expectation:</b> {esc(scenario.get('expectation'))}</p><p><b>重置条件 / reset:</b> <code>{esc(json.dumps(scenario.get('reset'), ensure_ascii=False))}</code></p><div class="browser-steps">{''.join(steps)}</div></section>""")
    open_attr = " open" if open_by_default else ""
    return f"""<details class="raw-plan"{open_attr}><summary>{esc(title)} · 展开全部逐步输入/输出</summary><p class="source-link"><a href="{esc(raw)}" target="_blank">原始 execution JSON</a> · browser_makes_verdict = false</p>{''.join(scenarios)}</details>"""


def task_input(prototypes: list[tuple[str, str]]) -> str:
    prompt = (TASK_ROOT / "prompt.txt").read_text(encoding="utf-8")
    gallery = "".join(image(path, name, "用户提供的原型 / reference input") for name, path in prototypes)
    return f"""<section id="task" class="paper"><div class="section-kicker">SHARED INPUT · 三条轨迹的共同输入</div><h2>任务输入是什么？</h2><p>同一个 Vision2Web Level 2 前端任务：模型获得英文需求、六张长页面原型图和资源目录。模型没有看到 <code>workflow.json</code>、评测器或隐藏分数。</p><div class="task-summary"><div><b>中文摘要</b><p>复现 SmartRecruiters 的首页、定价、关于我们、Winston AI、客户案例和招聘页；实现跨页导航、下拉菜单、筛选、FAQ、加载更多等交互，并通过 <code>/workspace/start.sh</code> 在 3000 端口部署。</p></div><details><summary>完整英文 task prompt</summary><pre>{esc(prompt)}</pre></details></div><details class="prototype-drawer"><summary>六张原型图（点击展开，图片很长）</summary><div class="prototype-grid">{gallery}</div></details></section>"""


def openhands_track() -> str:
    first = copy_asset(OH_DEV / "evidence/1787900285827557044-browser-get-state/screenshot.png", "assets/openhands/first-state.png")
    faq_fail = copy_asset(OH_DEV / "evidence/1787901557981548198-pricing-FAQ-toggle-01-click-error/screenshot.png", "assets/openhands/faq-fail.png")
    faq_pass = copy_asset(OH_DEV / "evidence/1787902868724177825-Pricing-FAQ-toggle-verify-05-wait-ok/screenshot.png", "assets/openhands/faq-pass.png")
    final = copy_asset(OH_DEV / "evidence/1787903071491599020-Fresh-server-homepage-CTA-smoke-05-click-ok/screenshot.png", "assets/openhands/final-smoke.png")
    stages = "".join(
        [
            stage("A0", "首次观察已部署版本", image(first, "P_first · http://localhost:3000/，截图和紧凑页面状态均进入模型上下文") + "<p>记录器将该 browser observation 绑定到当时的程序哈希和服务进程；这不是查看原型图。</p>", "observe"),
            stage("A1", "先区分‘测试计划错’与‘实现错’", quote("第一次 Customers 计划失败在不存在的 ‘All Stories’ 标签。模型没有立即改代码，而是依据可访问性状态改用真实按钮名 ‘All’，随后同一功能的 11 步计划全部成功。", "The scenario failed only because I guessed the button name 'All Stories' — the actual button is 'All'. The filter itself worked... Let me re-run with corrected target names.") + execution_view("56efaf1c-fb20-48dc-873c-fbb5956daef8", "第一次计划：最后一步目标名错误") + execution_view("84d65276-a687-4901-87d9-1cf5f9986c4c", "修正语义目标后重放：11 步全部 ok"), "plan"),
            stage("A2", "浏览器证据暴露真实运行缺陷", image(faq_fail, "FAQ / dropdown 复合检查中的失败画面") + quote("模型把 console error 追到轮播初始化：调用方没有传 item selector，导致 items[0] 为空。它将默认 selector 修为 .car-item。", "Found it! bindCarousel(rootId, itemSel) — callers omit itemSel, so items[0] is undefined. This is a bug I introduced.") + patch_block("运行错误的最小修复", "const items = track.querySelectorAll(itemSel);\n→\nconst items = track.querySelectorAll(itemSel || '.car-item');") + quote("后续检查又发现 FAQ 的 aria-expanded 被写到了容器而不是按钮；模型在证据后修正属性落点。", "Filter reset is correct... Now fix the aria-expanded placement on the FAQ button...") + patch_block("FAQ 状态修复", "item.setAttribute('aria-expanded', String(open));\n→\nbtn.setAttribute('aria-expanded', String(open));"), "repair"),
            stage("A3", "重放失败检查", image(faq_pass, "修复后 FAQ 重放：所有步骤状态为 ok、无 console error") + execution_view("f69277d1-4d75-4b09-ad8b-b90ebdbbcfbe", "Pricing FAQ toggle verify：6 步全部 ok"), "recheck"),
            stage("A4", "从干净 start.sh 再部署并做最后 smoke", image(final, "Fresh-server homepage CTA smoke 的最终点击后状态") + execution_view("32bf7d45-943e-4a54-b5a4-4823c43a4479", "Fresh server：7 步全部 ok") + '<p class="caveat">这里只证明自检链路和这些自拟检查通过；尚未运行 Vision2Web 官方 evaluator，不能写成 benchmark 满分。</p>', "submit"),
        ]
    )
    return f"""<details id="oh-detail" class="track paper research-track"><summary><b>研究细节 A：</b>GLM-5.3-Flash × OpenHands 完整 action / observation / patch</summary><div class="track-detail"><div class="track-head"><div><div class="section-kicker">TRACK A · SPONTANEOUS VSV</div><h2>GLM-5.3-Flash × OpenHands</h2></div><div class="badges"><span class="pill success">正常结束</span><span class="pill">browser_enabled</span><span class="pill">101.6 min</span></div></div><p class="lead">没有额外自验证提示，只是在官方任务输入旁暴露浏览器接口。模型主动部署、看页面、提交语义动作计划、依据错误改代码并重放。这是三条中唯一包含真实 click / fill / press / scroll 的完整交互验证。</p><div class="metrics"><b>14</b><span>批量计划</span><b>127</b><span>浏览器子动作</span><b>41</b><span>编辑动作</span><b>1</b><span>正常提交</span></div><div class="flow">{stages}</div></div></details>"""


def kimi_track() -> str:
    workspace = KIMI / "workspace"
    paths = {name: copy_named(workspace / name, "kimi") for name in ("screenshot_home.png", "screenshot_home_final.png", "screenshot_careers_full.png", "screenshot_careers_v2.png", "screenshot_pricing_full.png", "screenshot_pricing_v2.png")}
    stages = "".join(
        [
            stage("B0", "工具降级但仍保证像素输入", action_block("原生 playwright-cli 尝试", "playwright-cli open http://localhost:3000\nplaywright-cli goto http://localhost:3000\nplaywright-cli goto https://example.com", "均在固定 timeout 内未返回可用页面状态") + action_block("确定性截图 + 多模态读取", "google-chrome --headless --screenshot=/workspace/screenshot_home.png ... http://localhost:3000\nRead(/workspace/screenshot_home.png)", "Read 的 tool_result 是 image block，而不是路径或 OCR 文本") + image(paths["screenshot_home.png"], "首次应用截图；该 Read 触发 P_first 保存"), "observe"),
            stage("B1", "Careers：发现破图 → 替换资源 → 复查", '<div class="pair">' + image(paths["screenshot_careers_full.png"], "输入：首次 Careers 全页图") + image(paths["screenshot_careers_v2.png"], "输出：修改后 Careers v2 图") + "</div>" + quote("模型在图中指出 awards 和若干 values 图片缺失；读取资源目录后，仅修改 CareersPage 的图片映射和 awards 呈现。", "I notice some images are missing, particularly: Award badges in Careers page... Let me check what images are actually available.") + patch_block("实际 Edit 输入（摘要）", "Our-Investment-in-You.jpg → Automate-1.jpg\nOur-World.jpg → Communicate-1.jpg\nOur-Commitment-to-DE&I.jpg → Active-User.jpg\n4 个不存在的 award JPG → 4 个内联彩色 SVG badge") + action_block("复查", "google-chrome ... --screenshot=/workspace/screenshot_careers_v2.png http://localhost:3000/#/careers\nRead(/workspace/screenshot_careers_v2.png)", "PNG 再次作为 image block 进入同一 policy"), "repair"),
            stage("B2", "Pricing：发现表格默认隐藏 → 单行修复 → 复查", '<div class="pair">' + image(paths["screenshot_pricing_full.png"], "输入：首次 Pricing 全页图") + image(paths["screenshot_pricing_v2.png"], "输出：comparison table 默认显示") + "</div>" + quote("模型重新对照 prototype，判断比较表在目标图中默认可见，而当前实现默认折叠。", "The comparison table IS visible by default. Let me change that.") + patch_block("实际 Edit 输入", "const [showTable, setShowTable] = useState(false)\n→\nconst [showTable, setShowTable] = useState(true)") + action_block("复查", "google-chrome ... --screenshot=/workspace/screenshot_pricing_v2.png http://localhost:3000/#/pricing\nRead(/workspace/screenshot_pricing_v2.png)", "The pricing page now shows the comparison table by default."), "recheck"),
            stage("B3", "Homepage：补滚动动画 → 最终看图 → 提交", '<div class="pair">' + image(paths["screenshot_home.png"], "输入：首次首页 viewport") + image(paths["screenshot_home_final.png"], "输出：最终首页 viewport") + "</div>" + patch_block("实际 CSS Edit", "@keyframes scroll { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }\n.animate-scroll { animation: scroll 30s linear infinite; }") + quote("最后截图后，模型检查页面和文件结构并正常退出。", "The homepage looks great... Everything looks complete. Let me produce a final summary.") + '<p class="caveat">局限：这是静态截图驱动的视觉修复，不证明 FAQ、筛选或菜单被真实执行。页面把它标为 static VSV，而不是 interactive VSV。</p>', "submit"),
        ]
    )
    return f"""<details id="kimi-detail" class="track paper research-track"><summary><b>研究细节 B：</b>Kimi-K2.6 × Claude Code 完整截图 / Read / Edit 记录</summary><div class="track-detail"><div class="track-head"><div><div class="section-kicker">TRACK B · ELICITED STATIC VSV</div><h2>Kimi-K2.6 × Claude Code</h2></div><div class="badges"><span class="pill success">正常结束</span><span class="pill">guided_vsv</span><span class="pill">90.1 min</span></div></div><p class="lead">模型收到详细 VSV 指导。原生 <code>playwright-cli</code> 在该容器中持续超时，因此模型改用 headless Chrome 截图，再用 Claude Code <code>Read</code> 将 PNG 像素送入上下文。它完成了视觉修复闭环，但没有执行 click/fill 等交互检查。</p><div class="metrics"><b>16</b><span>PNG 注入事件</span><b>6</b><span>P_first 后编辑</span><b>9</b><span>P_first 后应用图像</span><b>0</b><span>语义交互计划</span></div><div class="flow">{stages}</div><p class="source-link"><a href="assets/raw/kimi-selected-events.json" target="_blank">所列 Edit / Bash / Read 的原始事件摘录</a></p></div></details>"""


def glm_cc_track(images: dict[str, str]) -> str:
    first = image(images["glm-first"], "首次 Read 的缩略全页图")
    middle = image(images["glm-mid"], "修复语法错误后的首页图")
    final = image(images["glm-final"], "修复懒加载资源路径后的最终首页图")
    stages = "".join(
        [
            stage("C0", "首次运行时观察：页面可达但 console 报错", action_block("打开真实应用", "playwright-cli open http://localhost:3000/", "Page URL: http://localhost:3000/\nPage Title: SmartRecruiters | AI-Powered Software for Superhuman Hiring\nConsole: 1 errors, 0 warnings") + quote("模型没有把 HTTP 200 当作完成，而是继续读取 console 并追查模块导出/缓存问题。", "Still cached. The playwright browser has a HTTP cache... Let me close and reopen the browser."), "observe"),
            stage("C1", "截图进入上下文并触发视觉修正", first + quote("模型根据首页像素指出 metric 图片、Winston 区和 case-study logo 的视觉异常，并检查真实资源映射。", "Good progress! The homepage renders well. Issues visible: Metric images, Winston section image, and case study logo.") + patch_block("资源映射修复（摘要）", "METRICS → Rectangle-3465068/69/70.jpg\nHIGHLY_RATED → 正确的 01..06 产品图标\ncase-study / insight cards → 对应真实资源文件"), "repair"),
            stage("C2", "新缺陷：浏览器给出空页面与 Unexpected token", action_block("重新逐页截图", "playwright-cli goto http://localhost:3000/pricing\nplaywright-cli screenshot --full-page ...", "Page Title: SmartRecruiters\nConsole: 1 errors\nUnexpected token ','\nSnapshot file: empty") + quote("模型把空 snapshot 解释为模块解析失败，随后用浏览器 import 逐个定位模块，最终发现 content.js 的双逗号。", "Empty snapshot → the page failed to render. Unexpected token ',' is a module parse error.") + patch_block("实际修复", "stat: '50%',,\n→\nstat: '50%',") + middle, "repair"),
            stage("C3", "继续视觉诊断：懒加载图片路径错误", quote("模型滚动页面触发懒加载，用 browser eval 列出 naturalWidth=0 的图片，发现文件名少了一个连字符。", "Filename mismatch: the file has an extra dash. Let me fix...") + patch_block("实际修复", "E-1-1_Web_10-Ways-to-Improve-YourHiring-Process_NCH.jpg\n→\nE-1-1_Web_10-Ways-to-Improve-Your-Hiring-Process_NCH.jpg") + final, "recheck"),
            stage("C4", "停止失败：验证继续扩张直到 timeout", action_block("超时前最后一个动作", "生成 /tmp/shoot.sh；计划继续对 pricing / about-us / winston-ai / customers / careers 逐页滚动并截图", "7200 秒 harness wall-time 到达；没有 final assistant submission") + '<p class="caveat"><b>关键结论：</b>这不是“没有自验证”，而是“自验证没有受控停止”。旧记录器也未把这些 Claude PNG Read 绑定成 P_first，因此不能用它做严格 P_first→P_final 分数增益。</p>', "timeout"),
        ]
    )
    return f"""<details id="glmcc-detail" class="track paper research-track"><summary><b>研究细节 C：</b>GLM-5.3-Flash × Claude Code 完整诊断与超时记录</summary><div class="track-detail"><div class="track-head"><div><div class="section-kicker">TRACK C · SPONTANEOUS DIAGNOSTIC VSV</div><h2>GLM-5.3-Flash × Claude Code</h2></div><div class="badges"><span class="pill timeout">超时，未正常提交</span><span class="pill">official</span><span class="pill">120.0 min</span></div></div><p class="lead">官方 prompt 没有额外 VSV 指导，但 Claude Code 原生提供 <code>playwright-cli</code>。模型主动启动页面、检查 console、截图、读图和修复；然而它持续扩展检查范围，在 wall-time 到达时仍在准备更多截图，因此这是“验证有效但停止失败”的高信息量轨迹。</p><div class="metrics"><b>27</b><span>browser CLI 调用</span><b>49</b><span>PNG 结果</span><b>50</b><span>编辑动作</span><b>0</b><span>正常提交</span></div><div class="flow">{stages}</div><p class="source-link"><a href="assets/raw/glm-cc-selected-events.json" target="_blank">所列 Read 的原始图像事件摘录</a></p></div></details>"""


def turn_replays(glm_images: dict[str, str]) -> str:
    oh_before = execution_reset_image(
        "0bbbbe93-b169-4c01-8b6f-eee4bd95f864",
        "Product dropdown to Winston AI",
        "openhands-before-carousel-fix.png",
    )
    kimi_before = copy_named(KIMI / "workspace/screenshot_careers_full.png", "turns")
    kimi_after = copy_named(KIMI / "workspace/screenshot_careers_v2.png", "turns")
    oh_raw = export_event_excerpt(
        OH / "openhands.events.attempt-1.jsonl", [(548, 571)], "assets/raw/turn-replay-openhands.json"
    )
    kimi_raw = export_event_excerpt(
        KIMI / "claude.events.attempt-1.jsonl",
        [(7923, 7926), (8082, 8084), (8336, 8337), (8403, 8404), (8488, 8489),
         (8690, 8691), (8710, 8711), (8762, 8763), (8823, 8824), (8871, 8874),
         (9260, 9327), (9607, 9607)],
        "assets/raw/turn-replay-kimi.json",
    )
    glm_raw = export_event_excerpt(
        GLM_CC / "claude.events.attempt-1.jsonl",
        [(16703, 16717), (16797, 16801), (16896, 17022), (17291, 17321)],
        "assets/raw/turn-replay-glm-cc.json",
    )
    verbatim_oh = openhands_verbatim_replay()
    replay_evidence = execution_view(
        "d19aafa7-cf4e-4b9c-9c4e-ddd7a57dbded",
        "这一次工具实际返回的 3 个 scenarios / 10 个 actions / 13 张截图",
        open_by_default=True,
    )
    replay_summary = """<div class="batch-summary"><b>不是“点了 3 次”。</b><p><code>same 3 scenarios</code> 指复用三组完整检查：① Pricing FAQ 展开；② Company → About Us；③ Product → Winston AI。三组共含 10 个 action（5 wait + 5 click）。每组 reset 后截 1 张，每个 action 后再截 1 张，因此本次工具返回 <b>13 张截图</b>。同一个 observation 中共有 27 个多模态 blocks：1 个 manifest、13 个截图标签和 13 个 image blocks。下方逐张展示。</p></div>"""

    oh_turns = "".join([
        turn_card("1", "模型发起批量检查", "刚刷新过的 Pricing 页面状态、可交互元素和先前代码上下文。", "页面结构已更新；现在同时检查 FAQ、Company 下拉和 Product 下拉。", "browser_execute_plan(3 scenarios, reset-separated)", "返回每一步截图、URL、可访问性状态和错误；首页 reset 中出现真实 console error：items[0].getBoundingClientRect，另外三个语义点击没有正确落点。", "The fresh navigation now shows updated markup... Now running the full verification with fresh loads.", returned_screenshot=oh_before),
        turn_card("2", "先读出错代码", "上一轮全部浏览器结果，包括截图、console error 的文件名与第 198 行。", "判断 console error 是真实实现问题；先检查 components.js:198。", "sed -n '180,215p' js/components.js", "返回 update() 代码：items[0].getBoundingClientRect()，确认空 items 会崩溃。", "Two remaining issues: (1) a real console error in components.js:198... Let me inspect line 198."),
        turn_card("3", "追踪谁调用了轮播", "出错行代码和此前浏览器所在路由。", "推测某个页面调用 bindCarousel 时没有产生可匹配 item；搜索全部调用点。", "grep -rn 'bindCarousel(' js/", "只找到 home 的 quoteCarousel 和 customers 的 videoCarousel 两个调用点。", "items[0] undefined. This happens when bindCarousel is called for a carousel whose track has no items."),
        turn_card("4", "排除无关页面绑定", "两个轮播调用点。", "检查其它页面是否间接绑定轮播，并检查路由 bind 函数。", "grep bind functions; sed app.js; inspect home/customers binders", "About/Winston 没有轮播绑定；home 与 customers 都只传 rootId，没有传 item selector。", "The console error occurred on the homepage... Let me check the carousel markup vs selector."),
        turn_card("5", "定位根因", "bindCarousel 定义、home 调用和页面中真实的 .car-item 标记。", "确认根因：调用只传一个参数，itemSel 为 undefined；默认 selector 应为 .car-item。", "file_editor: items querySelectorAll(itemSel) → querySelectorAll(itemSel || '.car-item')", "编辑器返回修改后的第 176–185 行，补丁成功落盘。", "Found it! bindCarousel(rootId, itemSel)... This is a bug I introduced."),
        turn_card("6", "补丁后做静态交叉检查", "编辑器确认的新代码。", "检查 customers 的轮播也确实使用 .car-item，确保默认值不会破坏另一个调用方。", "grep videoCarousel and car-item in customers.js", "确认 videoCarousel 的子项同样是 .car-item。", "Both carousels use .car-item."),
        turn_card(
            "7",
            "重放三组完整检查（不是三个点击）",
            "补丁和两个调用方的代码证据。",
            "重新提交 Pricing FAQ、Company → About Us、Product → Winston AI 三组原计划。",
            "browser_execute_plan(\n  1. pricing FAQ toggle,\n  2. Company dropdown to About Us,\n  3. Product dropdown to Winston AI,\n  each with a fresh reset\n)",
            "工具实际返回 13 张图，而不是 1 张：3 张 reset 图 + 10 张逐 action 图。getBoundingClientRect console error 已消失；FAQ 的 viewport grounding、About Us 和 Winston AI 的 exact accessible-name 匹配仍失败。",
            "Re-running the verification scenarios.",
            environment_extra=replay_summary + replay_evidence,
        ),
        turn_card("8", "读取重放结论并继续", "修复后的完整重放结果。", "明确判断 console error 已消失，但剩余失败是元素语义定位问题；继续读取 evidence，而不是直接提交。", "inspect saved browser_state/runtime evidence", "返回 FAQ 和下拉按钮的真实 accessible names，进入下一轮定位。", "Console errors are gone. Remaining failures are accessible-name mismatches on lookup."),
    ])

    kimi_turns = "".join([
        turn_card("1", "请求读取 Careers 截图", "此前 Customers 页面截图及其视觉判断。", "决定继续检查 Careers，并指出已有若干疑似破图。", "Read(/workspace/screenshot_careers_full.png)", "Claude Code 返回真正的 image block；从下一轮起，1920×8000 页面像素进入模型上下文。", "Let me check the careers page now... Some images are missing/broken.", returned_screenshot=kimi_before),
        turn_card("2", "看图后判断视觉问题", "上一轮返回的完整 Careers 页面图像。", "判断整体布局良好，但四个 award badge 和部分 values 图片缺失；决定检查资源目录。", "Bash: list award/work/value image filenames", "目录没有模型此前虚构的 Best-Company-* 和 Our-Investment-* 文件，但返回了可用替代资源。", "Careers page looks excellent... four award badges seem to be missing images." , received_screenshot=kimi_before),
        turn_card("3", "核对代码与真实资源", "资源目录返回和图中破图位置。", "多轮读取 CareersPage，并确认错误来自不存在的文件名，不是页面加载偶发失败。", "Read(CareersPage.tsx); Bash(ls/grep available images)", "返回 values 数组、AwardsSection 和真实可用文件名。", "I see the issue — I referenced some images that don't exist."),
        turn_card("4", "实施针对性修复", "页面代码、缺失文件清单和可用替代资源。", "把三个 values 图片替换为真实文件；把四个不存在的 award JPG 改成内联 SVG badge。", "Edit(CareersPage.tsx) × 2", "编辑器确认两个补丁写入 workspace。", "Let me fix the CareersPage... replace awards with colored placeholders and values with existing images."),
        turn_card("5", "修改后重新截图", "补丁成功的工具返回。", "主动启动新的全页截图，等待文件生成。", "google-chrome --headless ... screenshot_careers_v2.png", "返回 1,540,362-byte PNG；随后模型调用 Read。", "Let me take a fresh screenshot of the careers page to verify my fixes."),
        turn_card("6", "复查图像", "Read 返回的修改后 Careers image block。", "判断 badges 已变为正常彩色图标、values 图片恢复，随后继续检查 Pricing，而不是继续盲改 Careers。", "Read(screenshot_careers_v2.png)", "该图像进入同一个模型上下文；本 episode 在这里形成截图→判断→修改→再截图→判断的闭环。", "The careers page now looks much better... Awards section has colorful trophy icons instead of missing images.", received_screenshot=kimi_after),
    ])

    glm_turns = "".join([
        turn_card("1", "批量截图暴露空页面", "模型认为模块已能解析，准备开始视觉迭代。", "对六个页面逐页 goto 并截图。", "playwright-cli goto + screenshot --full-page × 6", "六张 PNG 只有 4254 bytes；单独打开 Pricing 返回空 snapshot 和 console: Unexpected token ','.", "All resolve. Now let me re-screenshot pages and iterate on the visuals."),
        turn_card("2", "把视觉失败转为模块诊断", "空截图、空 accessibility snapshot 和 Unexpected token 错误。", "判断页面没有渲染，问题是模块解析而非 CSS 视觉差异；开始逐模块 import。", "browser import('/src/js/pages/pricing.js'); import each dependency", "framework/icons 正常；content.js 及其所有依赖页面均报 Unexpected token。", "Empty snapshot → the page failed to render. Unexpected token ',' is a module parse error."),
        turn_card("3", "定位精确代码行", "模块二分结果：content.js 首先失败。", "改用 ESM 模式语法检查，避免普通 node --check 的模式差异。", "node --input-type=module --check content.js", "返回第 191 行：stat: '50%',,；明确是双逗号。", "Let me find it by bisecting content.js in the browser..."),
        turn_card("4", "修复并验证语法", "精确错误行和 ESM parser 输出。", "第一次补丁因 cwd 错误失败；模型读取失败后改用 /workspace/app 再执行。", "replace stat: '50%',, → stat: '50%',; ESM-check all modules", "第二次补丁成功，所有模块 ESM-check 通过。", "ESM check: OK"),
        turn_card("5", "重新运行网站并让图片进入上下文", "所有模块语法检查通过。", "再次逐页截图；文件大小由 4KB 增长到 1–2.7MB，并读取首页 JPEG。", "playwright-cli screenshot × 6; Read(/tmp/sm-home.jpg)", "Read 返回 image block，像素从下一轮进入模型；同时浏览器又暴露更深层 CASE_SLIDES 运行错误。", "Re-screenshot all pages.", returned_screenshot=glm_images["glm-mid"]),
        turn_card("6", "继续修复但没有停止", "真实首页图像以及新出现的运行错误。", "继续修 CASE_SLIDES、资源路径并扩展到所有页面截图；没有形成受控停止条件。", "more Edit / playwright-cli / Read calls", "轨迹最终在 7200 秒 wall-time 到达时仍准备更多截图，没有 final submission。", "This trajectory verified and repaired repeatedly, but did not stop."),
    ])

    continuation_note = """<aside class="wait-note continuation-note"><h3>Turn 7 仍显示 error，不等于修复无效</h3><p>这里截取的是轨迹中段：该补丁只针对 <code>items[0].getBoundingClientRect</code> 轮播崩溃。重放后 console error 已消失；batch 仍为 error，是因为三条 action plan 还有定位问题。</p><ol><li><b>Company / Product：</b>模型随后清理链接的 accessible name；execution <code>fe8e360e…</code> 中两组均为 <span class="yes">ok</span>。</li><li><b>Pricing FAQ：</b>按钮在页面底部，直接点击时不在 viewport candidate pool。模型在计划中加入 <code>press End</code> 后，execution <code>f69277d1…</code> 的 6 步均为 <span class="yes">ok</span>。</li><li><b>最终 fresh-server smoke：</b>execution <code>32bf7d45…</code> 的 7 步均为 <span class="yes">ok</span>。</li></ol><p><b>所以应分开读：</b>实现修复是否生效，看 console/runtime evidence；检查计划是否可执行，看 action grounding。前者在 Turn 7 已修好，后者在后续计划中才修好。</p></aside>"""
    wait_note = """<aside id="why-wait" class="wait-note"><h3>为什么 GLM 在 OpenHands 计划里频繁输出 wait？</h3><p><b>它是模型自己写入 action plan 的，不是 OpenHands 自动插入。</b>本次 effective task prompt 没有出现或要求 <code>wait</code>；工具 schema 只是允许模型选择 0–10 秒的 wait。执行器收到它后仅执行 <code>asyncio.sleep(seconds)</code>，然后像其他 action 一样截图和记录状态。</p><div class="wait-stats"><b>62 / 127</b><span>action 是 wait（48.8%）</span><b>30</b><span>次直接跟在 click 后</span><b>0.3–1.0s</b><span>本轨迹全部等待时长</span></div><p>模型采用了传统 GUI 自动化中的保守习惯：点击、跳转、滚动或输入后等待页面渲染、动画和事件处理稳定。但这里执行器本来就会在每个 action 后捕获 observation，所以 <code>click → wait</code> 会产生两组截图和状态。部分 wait 对 SPA 路由、动画或 debounce 有价值，接近一半的比例则明显偏保守；行为统计时应把 wait 与 click/fill 等语义动作分开。</p><p class="source-line">证据：<code>prompt.json</code>；<code>vision2web_browser_plan.py:593–595</code>；14 个 execution JSON。</p></aside>"""
    return f"""<section id="turns" class="paper"><div class="section-kicker">真实事件回放 · MODEL–TOOL TURNS</div><h2>模型每一轮到底看到了什么、输出了什么？</h2><p class="turn-definition"><b>Turn 定义：</b>这里把一次模型决策及其工具调用，与紧随其后的环境返回配成一轮。环境返回会成为下一轮模型输入。Claude Code 同一 assistant response 中连续的同类查找被合并为一个决策轮；没有补写未记录的思维。</p><details class="turn-replay" open><summary>轨迹 A · OpenHands：轮播运行错误的 8 个连续决策轮（默认展开）</summary><div class="turn-list">{oh_turns}</div>{continuation_note}{wait_note}<details id="raw-openhands" class="verbatim-replay"><summary>查看 OpenHands 原始逐轮记录：12 个真实 tool turns 的 reasoning、完整 action JSON 和 observation</summary><p>上方 8 轮为便于理解的决策分组；这里严格按事件流逐个工具调用展示。空 reasoning 会如实标记，不补写。</p>{verbatim_oh}</details><p class="source-link"><a href="{esc(oh_raw)}" target="_blank">下载核对原始事件 548–572（图像 base64 已省略）</a></p></details><details class="turn-replay"><summary>轨迹 B · Kimi Claude Code：Careers 破图修复的 6 个决策轮</summary><div class="turn-list">{kimi_turns}</div><p class="source-link"><a href="{esc(kimi_raw)}" target="_blank">核对原始 Claude 事件摘录</a></p></details><details class="turn-replay"><summary>轨迹 C · GLM Claude Code：空页面语法错误的 6 个决策轮</summary><div class="turn-list">{glm_turns}</div><p class="source-link"><a href="{esc(glm_raw)}" target="_blank">核对原始 Claude 事件摘录</a></p></details></section>"""


STYLE = r"""
:root{--ink:#18212f;--muted:#667085;--line:#dce2ea;--paper:#f3f5f8;--blue:#2857d7;--cyan:#087c91;--green:#087a55;--orange:#b45f06;--red:#b42318;--purple:#7047b7}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.62 Inter,"Noto Sans SC",system-ui,sans-serif}header{background:linear-gradient(120deg,#101c30,#173b59);color:white;padding:36px max(24px,calc((100vw - 1180px)/2)) 30px}header h1{font-size:36px;line-height:1.15;margin:6px 0 10px}header p{max-width:900px;color:#d9e6f1;margin:0}.topnav{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.96);backdrop-filter:blur(12px);border-bottom:1px solid var(--line);display:flex;gap:8px;justify-content:center;padding:10px;overflow:auto}.topnav a{text-decoration:none;color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:7px 12px;white-space:nowrap;font-weight:700}.topnav a:hover{border-color:var(--blue);color:var(--blue)}main{max-width:1180px;margin:auto;padding:24px 20px 80px}.paper{background:#fff;border:1px solid var(--line);border-radius:16px;padding:22px;margin:0 0 22px;box-shadow:0 4px 18px rgba(20,33,55,.035)}h2{font-size:27px;margin:4px 0 12px}h4{font-size:18px;margin:0 0 10px}h5{font-size:14px;margin:0 0 6px}.section-kicker{font:800 11px ui-monospace,monospace;color:var(--cyan);letter-spacing:.09em}.lead{font-size:16px;max-width:1000px}.simple-definition{font-size:18px;background:#eef7ff;border-left:5px solid var(--blue);padding:14px 16px;border-radius:8px}.simple-flow{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:18px 0}.simple-step{position:relative;border:1px solid var(--line);border-radius:12px;padding:14px;background:#fff}.simple-step:not(:last-child):after{content:"→";position:absolute;right:-17px;top:42%;z-index:2;color:var(--blue);font-size:22px;font-weight:900}.simple-step b{display:block;color:var(--blue);font-size:15px;margin-bottom:5px}.simple-step span{color:var(--muted)}.focus-loop{border:2px solid #9bb8ef}.focus-loop h3{font-size:22px;margin:3px 0}.plain-timeline{display:grid;gap:9px;margin:18px 0}.plain-row{display:grid;grid-template-columns:125px 1fr;gap:13px;align-items:start;border:1px solid var(--line);border-radius:11px;padding:12px}.plain-row strong{color:#fff;background:var(--blue);border-radius:8px;padding:7px 9px;text-align:center}.plain-row.output strong{background:var(--green)}.plain-row.decision strong{background:var(--purple)}.plain-row.patch strong{background:var(--orange)}.plain-row.recheck strong{background:#087a55}.plain-row p{margin:4px 0;font-size:15px}.takeaway{background:#ecfdf3;border:1px solid #a6e3c1;border-radius:10px;padding:13px;font-size:16px}.compare-table{width:100%;border-collapse:collapse;margin-top:12px}.compare-table th,.compare-table td{border-bottom:1px solid var(--line);padding:11px;text-align:left;vertical-align:top}.compare-table th{background:#f5f7fa}.yes{color:#067647;font-weight:800}.partial{color:#b54708;font-weight:800}.no{color:#b42318;font-weight:800}.turn-definition{background:#fff8e8;border:1px solid #f0ca7a;border-radius:10px;padding:12px}.turn-replay{border:1px solid #b8cae5;border-radius:12px;margin:14px 0;background:#f8fbff}.turn-replay>summary{font-size:17px;padding:14px;color:#2149aa}.turn-replay[open]>summary{border-bottom:1px solid #b8cae5}.turn-list{padding:12px}.turn-card{display:grid;grid-template-columns:74px 1fr;gap:12px;margin:12px 0}.turn-number{position:sticky;top:64px;align-self:start;background:#173b59;color:#fff;border-radius:10px;padding:9px 6px;text-align:center;font:800 11px ui-monospace,monospace}.turn-main{border:1px solid var(--line);border-radius:12px;background:#fff;padding:14px}.turn-io{border-left:4px solid #9cb5ef;background:#f7f9fd;padding:9px 11px;margin:8px 0}.turn-io b{display:block;font:800 11px ui-monospace,monospace;margin-bottom:4px}.turn-io p{margin:0;font-size:14px}.turn-io.received{border-color:#5f8fe5}.turn-io.model{border-color:#997bd0;background:#faf7ff}.turn-io.tool{border-color:#eea256;background:#fff8f1}.turn-io.environment{border-color:#55b58e;background:#f1fbf6}.turn-io .shot{max-width:760px}.turn-io .shot img{max-height:520px}.wait-note{margin:14px 12px;padding:16px;border:2px solid #f0c36a;border-radius:12px;background:#fff9e8}.wait-note h3{margin:0 0 8px}.wait-stats{display:grid;grid-template-columns:repeat(3,auto 1fr);border:1px solid #ecd394;border-radius:9px;overflow:hidden;margin:12px 0}.wait-stats b{font-size:20px;color:#a45500;padding:10px}.wait-stats span{padding:13px 10px 10px 0;color:#704214}.source-line{color:#704214;font-size:12px}.verbatim-replay{margin:14px 12px;border:1px solid #9fb5d4;border-radius:11px;background:#fff;padding:12px}.verbatim-replay>summary{color:#2149aa;font-size:15px}.verbatim-turn{border:1px solid var(--line);border-radius:10px;margin:12px 0;overflow:hidden}.verbatim-head{display:flex;justify-content:space-between;gap:10px;background:#edf3fb;padding:9px 11px}.verbatim-head span{color:var(--muted);font:11px ui-monospace,monospace}.verbatim-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:10px}.verbatim-grid>div{min-width:0}.verbatim-grid .wide{grid-column:1/-1}.verbatim-grid pre{max-height:360px}.research-track>summary{list-style:none;font-size:17px;padding:4px;color:var(--blue)}.research-track>summary::-webkit-details-marker{display:none}.research-track>summary:after{content:"＋ 展开";float:right;color:var(--muted);font-size:12px}.research-track[open]>summary:after{content:"－ 收起"}.track-detail{border-top:1px solid var(--line);margin-top:15px;padding-top:18px}.track-head{display:flex;justify-content:space-between;gap:15px;align-items:flex-start}.badges{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}.pill,.mini-ok{display:inline-block;border-radius:999px;padding:4px 9px;background:#edf1f6;color:#344054;font:800 10px ui-monospace,monospace}.pill.success,.mini-ok{background:#dcfce7;color:#166534}.pill.timeout{background:#fff0dd;color:#9a4c00}.metrics{display:grid;grid-template-columns:repeat(4,minmax(80px,125px) minmax(80px,1fr));border:1px solid var(--line);border-radius:11px;overflow:hidden;margin:16px 0 22px}.metrics b{font-size:23px;color:var(--blue);padding:10px 4px 10px 13px}.metrics span{color:var(--muted);padding:14px 10px 10px 0}.flow{position:relative}.flow:before{content:"";position:absolute;left:25px;top:10px;bottom:10px;width:2px;background:#dce5ee}.stage{display:grid;grid-template-columns:52px 1fr;position:relative;margin:11px 0}.stage-index{z-index:2;width:50px;height:34px;border-radius:10px;background:#e6efff;color:#2149aa;border:2px solid #fff;box-shadow:0 0 0 1px #b9cbef;display:flex;align-items:center;justify-content:center;font:800 11px ui-monospace,monospace}.stage-body{border:1px solid var(--line);border-radius:13px;padding:15px;margin-left:12px;min-width:0}.stage.observe .stage-index{background:#dff7fb;color:#0d6877}.stage.repair .stage-index{background:#fff0e6;color:#9c4e04}.stage.recheck .stage-index{background:#e3f8ed;color:#096344}.stage.submit .stage-index{background:#eee8ff;color:#5b3ba0}.stage.timeout .stage-index{background:#ffe8e6;color:#a52a24}.io-label{display:inline-block;font:800 10px ui-monospace,monospace;letter-spacing:.04em;margin:0 0 5px}.io-label.action{color:#1e4fbd}.io-label.output{color:#087a55}.io-label.decision{color:#6541a5}.io-label.patch{color:#a54b00}.io-action,.io-output,.io-patch,.judgment{border-left:4px solid #9cb5ef;background:#f7f9fd;padding:10px 12px;margin:10px 0}.io-output{border-color:#72c5a5;background:#f1fbf6}.io-patch{border-color:#f0aa62;background:#fff8f1}.judgment{border-color:#a990d8;background:#faf7ff}.judgment p{font-size:15px;margin:3px 0 8px}pre,blockquote{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}pre{background:#142033;color:#e8eef7;border-radius:8px;padding:11px;max-height:520px;overflow:auto;margin:6px 0}blockquote{margin:8px 0;padding:10px;background:#fff;border:1px solid #e3daf6;border-radius:8px;color:#4e4166}details summary{cursor:pointer;font-weight:750}.shot{margin:10px 0;border:1px solid var(--line);border-radius:11px;overflow:hidden;background:#f8fafc}.shot img{width:100%;max-height:740px;object-fit:contain;background:#101827;display:block}.shot figcaption{padding:8px 10px;color:var(--muted);font-size:11px}.shot figcaption .mini-ok{margin-right:7px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:10px}.prototype-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.prototype-grid .shot img{max-height:600px;object-position:top}.task-summary{display:grid;grid-template-columns:1fr 1fr;gap:14px}.task-summary>div,.task-summary>details{border:1px solid var(--line);border-radius:10px;padding:12px}.prototype-drawer{margin-top:12px}.raw-plan{border:1px solid #b8cae5;border-radius:9px;padding:10px;margin:10px 0;background:#f8fbff}.scenario{border-top:1px dashed #b8cae5;padding-top:10px;margin-top:10px}.browser-steps{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.browser-step{border:1px solid #cde5d8;border-radius:9px;padding:9px;background:#f6fcf8;min-width:0}.browser-step.bad{border-color:#ffc6bf;background:#fff7f6}.step-head{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}.step-head code{font-size:10px;overflow-wrap:anywhere}.browser-step .shot img{max-height:350px}.caveat{border-left:4px solid #f3a03c;background:#fff8ed;padding:10px 12px;color:#77400b}.source-link a{font-weight:700;color:var(--blue)}.overview{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.overview article{border:1px solid var(--line);border-radius:11px;padding:13px}.overview b{display:block;font-size:16px}.overview span{color:var(--muted)}.legend{display:flex;gap:7px;flex-wrap:wrap}.legend span{padding:5px 9px;border-radius:7px;background:#edf1f6}.bottom-note{color:var(--muted);text-align:center}@media(max-width:950px){.simple-flow{grid-template-columns:1fr}.simple-step:not(:last-child):after{content:"↓";right:50%;top:auto;bottom:-23px}.prototype-grid,.overview{grid-template-columns:1fr 1fr}.task-summary,.pair,.browser-steps{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,70px 1fr)}.wait-stats{grid-template-columns:auto 1fr}.verbatim-grid{grid-template-columns:1fr}.verbatim-grid .wide{grid-column:auto}.track-head{display:block}.badges{justify-content:flex-start}}@media(max-width:600px){header h1{font-size:29px}.paper{padding:15px}.prototype-grid,.overview{grid-template-columns:1fr}.metrics{grid-template-columns:62px 1fr}.plain-row,.turn-card{grid-template-columns:1fr}.turn-number{position:static;text-align:left}.verbatim-head{display:block}.stage{grid-template-columns:42px 1fr}.stage-index{width:40px}.flow:before{left:20px}}
.batch-summary{margin:12px 0;padding:12px;border:2px solid #72c5a5;border-radius:10px;background:#fff}.batch-summary b{font-size:15px;color:#096344}.batch-summary p{margin:5px 0 0}.reset-step{border-color:#b8cae5;background:#f6f9ff}
"""


def export_kimi_events() -> None:
    selected = {"functions.Edit:150", "functions.Edit:151", "functions.Bash:161", "functions.Read:163", "functions.Edit:166", "functions.Bash:167", "functions.Read:169", "functions.Edit:174", "functions.Bash:175", "functions.Read:177"}
    snippets = []
    for line_number, event in json_lines(KIMI / "claude.events.attempt-1.jsonl"):
        blocks = []
        for block in claude_blocks(event):
            identifier = str(block.get("id") or block.get("tool_use_id") or "")
            if identifier not in selected:
                continue
            clean = {k: v for k, v in block.items() if k not in {"content", "source"}}
            if block.get("type") == "tool_result" and isinstance(block.get("content"), list):
                clean["content_types"] = [x.get("type") for x in block["content"] if isinstance(x, dict)]
            blocks.append(clean)
        if blocks:
            snippets.append({"raw_line": line_number, "blocks": blocks})
    raw = OUTPUT / "assets/raw/kimi-selected-events.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(snippets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    prototypes = [(source.stem.replace("_", " "), copy_named(source, "prototypes")) for source in sorted((TASK_ROOT / "prototypes").glob("*.jpg"))]
    glm_images = extract_claude_images(GLM_CC / "claude.events.attempt-1.jsonl", {"chatcmpl-tool-9e33d43e0371157a": "glm-first", "chatcmpl-tool-841d89bb34f58c86": "glm-mid", "chatcmpl-tool-b4dd54846c427045": "glm-final"}, "glm-cc")
    export_kimi_events()
    overview = """<section id="overview" class="paper"><div class="section-kicker">先理解概念 · 1 MINUTE</div><h2>Self-verification 到底是什么？</h2><p class="simple-definition"><b>一句话：</b>模型写完网站后，亲自打开它、操作它、看见错误，再根据这些运行证据修代码并重新检查。</p><div class="simple-flow"><div class="simple-step"><b>① 写完并启动</b><span>得到一个真实可访问的网站</span></div><div class="simple-step"><b>② 主动检查</b><span>打开页面，执行 click / fill / scroll</span></div><div class="simple-step"><b>③ 看见证据</b><span>截图、页面状态和运行错误返回模型</span></div><div class="simple-step"><b>④ 判断并修复</b><span>模型自己决定哪里错、是否改代码</span></div><div class="simple-step"><b>⑤ 重新检查</b><span>再次执行相关动作，确认修复有效</span></div></div><p><b>关键边界：</b>浏览器只执行和记录，不替模型判断。仅查看原型图、仅确认 HTTP 200、或者写完代码直接提交，都不算完整的 Visual Self-Verification。</p></section>"""
    focus = """<section id="oh" class="paper focus-loop"><div class="section-kicker">先只看这一例 · COMPLETE LOOP</div><h2>一个真正发生过的自验证闭环</h2><h3>GLM-5.3-Flash × OpenHands：交互检查中发现轮播运行错误</h3><div class="plain-timeline"><div class="plain-row"><strong>输入 INPUT</strong><p>模型已写好并启动网站，并持有任务、原型图和最新页面状态。</p></div><div class="plain-row"><strong>动作 ACTION</strong><p>模型一次生成三组检查：Pricing FAQ、Company 下拉导航、Product 下拉导航；浏览器按 reset 分开执行。</p></div><div class="plain-row output"><strong>返回 OUTPUT</strong><p>每一步截图、URL、accessibility 状态和 console error 被返回。首页 reset 暴露 <code>items[0].getBoundingClientRect</code> 错误；同时还有若干元素名字无法定位。</p></div><div class="plain-row decision"><strong>判断 DECISION</strong><p>模型区分了“真实代码错误”和“动作目标名字不匹配”，沿着报错行和调用点定位到 <code>bindCarousel</code> 缺少默认 item selector。</p></div><div class="plain-row patch"><strong>修改 PATCH</strong><p>仅将 <code>querySelectorAll(itemSel)</code> 改成 <code>querySelectorAll(itemSel || '.car-item')</code>。</p></div><div class="plain-row recheck"><strong>复查 RECHECK</strong><p>模型重放同一组三个场景。轮播 console error 消失；剩余 action grounding 失败仍被保留并继续分析，没有被误报为全部通过。</p></div></div><p class="takeaway"><b>为什么这算 self-verification：</b>检查由模型主动发起；错误来自真实运行；模型根据证据修改代码；随后重放原检查确认目标错误消失。</p><p><a href="#turns">下一节按真实 Turn 展示这个判断是怎样一步步发生的</a></p></section>"""
    comparison = """<section id="compare" class="paper"><div class="section-kicker">三条轨迹怎么读 · COMPARISON</div><h2>它们并不是同一种自验证</h2><table class="compare-table"><thead><tr><th>轨迹</th><th>模型实际做了什么</th><th>真实交互</th><th>修复后复查</th><th>最后结果</th></tr></thead><tbody><tr><td><b>OpenHands × GLM</b></td><td>操作页面，读取逐步截图和运行状态，再修代码</td><td class="yes">有</td><td class="yes">有</td><td class="yes">完整闭环并提交</td></tr><tr><td><b>Claude Code × Kimi</b></td><td>截图并看图，发现破图或视觉差异后修改</td><td class="no">没有 click/fill</td><td class="yes">重新截图</td><td class="partial">静态视觉闭环</td></tr><tr><td><b>Claude Code × GLM</b></td><td>通过 Playwright、console 和截图持续诊断修复</td><td class="partial">以诊断为主</td><td class="yes">多次复查</td><td class="no">检查失控，最终超时</td></tr></tbody></table><p class="caveat">最值得先看的只有第一条。第二条说明“看图修视觉”可以发生，但不证明交互功能；第三条说明模型会验证，却不一定知道何时停止。三条都尚未运行官方 evaluator，因此这里只分析行为，不宣称 benchmark 得分。</p></section>"""
    details_intro = """<section id="details" class="paper"><div class="section-kicker">可选阅读 · RESEARCH EVIDENCE</div><h2>下面是研究证据，默认全部收起</h2><p>只有在需要核对英文原话、具体 action、逐步截图或原始 JSON 时才展开。普通阅读到这里已经足够理解整体过程。</p></section>"""
    tracks = openhands_track() + kimi_track() + glm_cc_track(glm_images)
    body = overview + focus + turn_replays(glm_images) + comparison + task_input(prototypes) + details_intro + tracks
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Vision2Web · 看懂 Visual Self-Verification</title><style>{STYLE}</style></head><body><header><div class="section-kicker">VISION2WEB · SIMPLE READING VERSION</div><h1>用真实模型 Turn 看懂 Visual Self-Verification</h1><p>先看闭环，再逐轮查看模型收到的输入、模型输出、工具 action 和环境 observation。英文原文与原始 JSON 均可核对。</p></header><nav class="topnav"><a href="../index.html">← 返回总览</a><a href="#overview">什么是自验证</a><a href="#oh">完整例子</a><a href="#turns">真实 Turns</a><a href="#compare">三条对比</a><a href="#task">任务输入</a><a href="#details">研究细节</a></nav><main>{body}<p class="bottom-note">Generated only from saved native trajectory artifacts. workflow.json and official evaluators were not inspected.</p></main></body></html>"""
    (OUTPUT / "index.html").write_text(page, encoding="utf-8")
    (OUTPUT / "README.md").write_text("# Vision2Web verification episode showcase\n\nBuild: `python scripts/vision2web/build_verification_episode_showcase.py`\n\nServe from `reports/vision2web_scaffold_model_comparison` and open `/verification_episodes/`. No workflow/evaluator data is consumed.\n", encoding="utf-8")
    print(OUTPUT / "index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
