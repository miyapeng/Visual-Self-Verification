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
                f"<td><details><summary>Action</summary><pre>{html.escape(json.dumps(group['action'], ensure_ascii=False, indent=2))}</pre></details>"
                f"<details><summary>Observations</summary><pre>{html.escape(json.dumps(evidence, ensure_ascii=False, indent=2))}</pre></details>"
                f"<details><summary>Requirement and evidence</summary><pre>{html.escape(json.dumps(judgment, ensure_ascii=False, indent=2))}</pre></details></td></tr>"
            )
    aggregate = html.escape(
        json.dumps(result.get("aggregate") or {}, ensure_ascii=False, indent=2)
    )
    coverage = ((result.get("aggregate") or {}).get("test") or {}).get("workflow_coverage") or {}
    ratio = coverage.get("rate")
    percentage = f"{ratio:.1%}" if ratio is not None else "not evaluated"
    coverage_rows = []
    labels = {"full": "Full", "partial": "Partial", "uncovered": "Uncovered", "unknown": "Unknown"}
    for item in coverage.get("items") or []:
        coverage_rows.append(
            f"<tr><td>{html.escape(item['workflow_id'])}</td><td>{html.escape(item['objective'])}</td>"
            f"<td>{labels[item['status']]}</td><td><details><summary>Calls and evidence</summary>"
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
<h2>Test: workflow coverage</h2>
<p>Full coverage: {_value(coverage.get('numerator'))} / {_value(coverage.get('denominator'))} ({percentage}); partial only: {_value(coverage.get('partial_only_count'))} items.
{'Some items remain unassessed; this is a confirmed lower bound.' if coverage.get('is_lower_bound') else 'Calls passed structural validation; semantic labels are model judgments.'}</p>
<p>Scope: {'selected episodes' if result.get('episode_filter') else 'all extracted checks in this trajectory'}. Coverage is deduplicated by workflow ID. Partial coverage receives no fractional credit. Actions from different program versions cannot form one complete evidence chain. Coverage records exercised behavior, not functional correctness.</p>
<table><thead><tr><th>Workflow</th><th>Objective</th><th>Coverage</th><th>Evidence</th></tr></thead><tbody>{''.join(coverage_rows)}</tbody></table>
<table><thead><tr><th>Episode</th><th>Recorded execution</th><th>Covered workflow</th><th>Visual judgment</th><th>Safe repair</th><th>Evidence</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<h2>Test: action groups</h2>
<table><thead><tr><th>Group</th><th>Execution</th><th>Reasonableness</th><th>Evidence</th></tr></thead><tbody>{''.join(check_rows)}</tbody></table>
<h2>Aggregate</h2><pre>{aggregate}</pre>
</body></html>"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8")


def write_protocol_report(result, path):
    """A concise review artifact; original evidence remains in referenced inputs."""
    from .metrics import check_validity, diagnosis_correct
    def value(number):
        return 'NA' if number is None else f'{number:.2f}'
    lines = ['# Self-verification evaluation', '',
             f"Case: `{result['case_id']}`. Status: **{result['status']}**. Semantic labels: **model judgments**.", '',
             f"Source rounds: `{result['source_rounds']}`.",
             f"Catalogue: `{result['catalogue']}`; review status: `{result['catalogue_review']['status']}`.", '',
             f"Judgment method: `{result.get('judgment_method', 'historical_joint_calls')}`.", '',
             '| Metric | Score (0–100) | Success | Failure | Unknown |',
             '|---|---:|---:|---:|---:|']
    for key, row in result['metrics'].items():
        score = value(row['score'])
        if key.startswith('BDA') and row['score'] is None and row.get('lower_bound') is not None:
            score = f"{value(row['lower_bound'])}–{value(row['upper_bound'])}"
        lines.append(f"| {key} | {score} | {row.get('success_count',0)} | {row.get('failure_count',0)} | {row.get('unknown_count',0)} |")
    vc = result['metrics']['VC']
    lines += ['', f"Coverage bounds: {value(vc['lower_bound'])}–{value(vc['upper_bound'])}; partial goals: {vc['partial_count']}.", '',
              f"Unassessed repair episodes: {result['metrics']['RS'].get('unassessed_episode_count',0)}; "
              f"unassessed artifact transitions: {result['metrics']['CP'].get('unassessed_transition_count',0)}.", '',
              'Unknown coverage is a range, not an exact score. Incomplete evidence withholds the full score; known-subset rates are diagnostic only.',
              'A high measured preservation rate does not establish complete regression coverage. Repair links alone do not establish repair success.', '']
    if 'by_requirement_type' in vc:
        lines += ['| Requirement type | VC | Fully checked | Requirements |', '|---|---:|---:|---:|']
        for kind, row in vc['by_requirement_type'].items():
            score = value(row['score']) if row['classification_complete'] else 'Unclassified catalogue'
            lines.append(f"| {kind} | {score} | {row['success_count']} | {row['denominator']} |")
        lines += ['', 'These labels describe requirements, not visual/text evidence. Visual and interactive groups can overlap.',
                  f"Unclassified requirement IDs: {vc['unclassified_check_ids']}.", '']
    diagnosed = result['metrics']['RS'].get('diagnosed_visual')
    outcomes = result['metrics']['CP'].get('repair_outcomes')
    if diagnosed is not None and outcomes is not None:
        lines += [f"RS after a correct visual diagnosis and a confirmed baseline defect: {value(diagnosed['score'])}; "
                  f"{diagnosed['success_count']}/{diagnosed['sample_count']} target-transition pairs. "
                  f"Unverified baselines excluded from this conditional group: {diagnosed['unverified_baseline_count']}.", '',
                  'Local repair and regression refer to the same version transition. Later recovery does not erase an earlier failed attempt.',
                  '| Transition | Edit IDs | Local repair passed | Regression elsewhere | Regressed requirements |',
                  '|---|---|---|---|---|']
        for row in outcomes['transitions']:
            lines.append(f"| {row.get('before', '?')} → {row.get('after', '?')} | {row.get('repair_event_ids', [])} | "
                         f"{row['local_repair_success']} | {row['elsewhere_regression']} | {row['regressed_check_ids']} |")
        lines += ['', f"Joint outcomes: {outcomes['joint_outcomes']}.", '']
    for metric in ('BDA', 'BDA-V', 'BDA-T'):
        summary = result['metrics'][metric]
        lines += ['', f"{metric} includes missing and uncertain diagnoses on decidable observations. "
                  f"{summary.get('absent_count', 0)} absent and {summary.get('uncertain_count', 0)} uncertain "
                  'conclusions occur across all targets; those with unknown actual states remain unknown.',
                  f"Known-evidence class-macro accuracy: {value(summary.get('known_score'))}. "
                  'This subset score is not the full result when unknowns remain. Bounds vary unresolved actual states '
                  'and issue matches; missing conclusions never earn credit in either bound. These are uncertainty '
                  'bounds, not statistical confidence intervals.', '']
        lines += ['| Diagnosis group | Class | Accuracy | Success | Failure | Unknown |',
                  '|---|---|---:|---:|---:|---:|']
        for group in ('normal', 'error'):
            row = result['metrics'][metric][group]
            lines.append(f"| {metric} | {group} | {value(row['score'])} | {row['success_count']} | {row['failure_count']} | {row['unknown_count']} |")
    lines += ['',
              '| Episode | Target | Attempted check | Coverage | Actual | Agent | Validity | Diagnosis | Evidence IDs | Diagnosis IDs |',
              '|---|---|---|---|---|---|---|---|---|---|']
    for episode in result['episodes']:
        if not episode['targets']:
            lines.append(f"| {episode['episode_id']} | {episode['status']} | | | | | | | | |")
        for target in episode['targets']:
            valid = check_validity(target['method_ok'], target['evidence_ok'])
            diagnosis = diagnosis_correct(target['actual_state'],target['agent_state'],target['issue_match'])
            lines.append('| '+' | '.join(str(x).replace('|','\\|').replace('\n',' ') for x in [episode['episode_id'],target['target'],target.get('check_attempted', True),target['coverage'],
                         target['actual_state'],target['agent_state'],valid,diagnosis,target['evidence_ids'],target['diagnosis_ids']])+' |')
    Path(path).write_text('\n'.join(lines)+'\n')
