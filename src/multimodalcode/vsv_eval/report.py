from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def _value(value: Any) -> str:
    if value is None:
        return "not evaluable"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def write_html_report(result: dict[str, Any], path: str | Path) -> None:
    rows = []
    check_rows = []
    for episode in result.get("episodes") or []:
        test = episode.get("test") or {}
        visual = episode.get("visual_judgment") or {}
        repair = episode.get("safe_repair") or {}
        images = "".join(
            f'<a href="{html.escape(Path(image).as_uri())}">image {index + 1}</a> '
            for index, image in enumerate(episode.get("images") or [])
            if Path(image).is_file()
        )
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(episode['episode_id'])}</code><br>"
            f"{html.escape(str(episode.get('verification_kind') or episode.get('label') or 'verification'))}</td>"
            f"<td>{html.escape(json.dumps(test.get('execution_counts') or {}))}</td>"
            f"<td>{html.escape(', '.join(test.get('matched_workflow_ids') or []))}</td>"
            f"<td>{html.escape(json.dumps(visual.get('assessment_counts') or {}, ensure_ascii=False))}<br>"
            f"{html.escape(str(visual.get('reason') or visual.get('status') or ''))}</td>"
            f"<td>fixed: {html.escape(_value(repair.get('target_fixed')))}<br>"
            f"regression-free: {html.escape(_value(repair.get('regression_free')))}</td>"
            f"<td>{images}</td>"
            "</tr>"
        )
        for group in test.get("groups") or []:
            execution, judgment = group["execution"], group["reasonableness"]
            evidence = {"observations": group["observations"], "image_observations": group["image_observations"]}
            check_rows.append(
                f"<tr><td>{html.escape(group['group_id'])}</td>"
                f"<td>{html.escape(execution['status'])}<br>{html.escape(execution['reason'])}</td>"
                f"<td>{html.escape(judgment['status'])}<br>{html.escape(judgment.get('reason') or '')}</td>"
                f"<td><details><summary>Action / 动作</summary><pre>{html.escape(json.dumps(group['action'], ensure_ascii=False, indent=2))}</pre></details>"
                f"<details><summary>Observations / 反馈</summary><pre>{html.escape(json.dumps(evidence, ensure_ascii=False, indent=2))}</pre></details>"
                f"<details><summary>Requirement / 需求与依据</summary><pre>{html.escape(json.dumps(judgment, ensure_ascii=False, indent=2))}</pre></details></td></tr>"
            )
    aggregate = html.escape(
        json.dumps(result.get("aggregate") or {}, ensure_ascii=False, indent=2)
    )
    coverage = ((result.get("aggregate") or {}).get("test") or {}).get("workflow_coverage") or {}
    ratio = coverage.get("rate")
    percentage = f"{ratio:.1%}" if ratio is not None else "待评"
    coverage_rows = []
    labels = {"full": "完整覆盖", "partial": "仅部分覆盖", "uncovered": "未覆盖", "unknown": "待确认"}
    for item in coverage.get("items") or []:
        coverage_rows.append(
            f"<tr><td>{html.escape(item['workflow_id'])}</td><td>{html.escape(item['objective'])}</td>"
            f"<td>{labels[item['status']]}</td><td><details><summary>对应调用与证据</summary>"
            f"<pre>{html.escape(json.dumps(item['evidence'], ensure_ascii=False, indent=2))}</pre></details></td></tr>"
        )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>VSV evaluation</title>
<style>
body{{font:14px/1.5 system-ui,sans-serif;margin:32px;color:#172033}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d7dce5;padding:8px;vertical-align:top}}
th{{background:#f4f6fa;text-align:left}}code,pre{{background:#f4f6fa}}pre{{padding:12px;overflow:auto}}
</style></head><body>
<h1>Visual Self-Verification evaluation</h1>
<p>Case: <code>{html.escape(str(result.get('case_id')))}</code> · Model: {html.escape(str(result.get('model')))}</p>
<h2>Test 整体 workflow 覆盖率</h2>
<p>完整覆盖：{_value(coverage.get('numerator'))} / {_value(coverage.get('denominator'))}（{percentage}）；仅部分覆盖：{_value(coverage.get('partial_only_count'))} 项。
{'尚有待评项，当前仅为已确认覆盖下界。' if coverage.get('is_lower_bound') else '所有调用已完成结构校验；语义判断仍需人工抽查。'}</p>
<p>范围：{'所选片段' if result.get('episode_filter') else '当前轨迹切出的全部检查片段'}。按 workflow ID 去重；部分覆盖不折算成半分，不将不同版本的零散操作拼成完整覆盖。覆盖不代表功能通过。</p>
<table><thead><tr><th>Workflow</th><th>目标</th><th>覆盖状态</th><th>证据</th></tr></thead><tbody>{''.join(coverage_rows)}</tbody></table>
<table><thead><tr><th>Episode</th><th>Recorded execution</th><th>Covered workflow</th><th>Visual judgment</th><th>Safe repair</th><th>Evidence</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<h2>Test: action groups / 逐组检查</h2>
<table><thead><tr><th>Group</th><th>Execution / 执行</th><th>Reasonableness / 合理性</th><th>Evidence / 依据</th></tr></thead><tbody>{''.join(check_rows)}</tbody></table>
<h2>Aggregate</h2><pre>{aggregate}</pre>
</body></html>"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8")
