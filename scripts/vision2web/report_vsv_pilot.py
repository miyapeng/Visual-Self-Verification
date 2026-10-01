#!/usr/bin/env python3
"""Combine completed component evaluations without inventing a composite score."""
import argparse
from collections import Counter
import difflib
import hashlib
import html
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from multimodalcode.io import read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--test-scores', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    test = read_json(args.test_scores)
    visual = read_json(out / 'visual/scores.json')
    repair = read_json(out / 'repair/scores.json')
    if len({v['case_id'] for v in (test, visual, repair)}) != 1:
        raise ValueError('Cannot aggregate different tasks')
    source_paths = [Path(v['source_run']).resolve() for v in (test, visual, repair)]
    if len({hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}) != 1:
        raise ValueError('Cannot aggregate different trajectories')
    versions = read_json(out / 'repair/versions/reconstruction.json')['versions']
    for left, right in zip(versions, versions[1:]):
        patch = []
        for relative in sorted(set(left['code_manifest']['files']) | set(right['code_manifest']['files'])):
            a = Path(left['workspace']) / 'app' / relative
            b = Path(right['workspace']) / 'app' / relative
            if left['code_manifest']['files'].get(relative) == right['code_manifest']['files'].get(relative):
                continue
            patch.extend(difflib.unified_diff(a.read_text().splitlines(True), b.read_text().splitlines(True),
                         fromfile=left['version'] + '/' + relative, tofile=right['version'] + '/' + relative))
        (out / 'repair/versions' / (left['version'] + '-' + right['version'] + '.patch')).write_text(''.join(patch))
    summary = {'case_id': test['case_id'], 'policy_model': test['model'], 'framework': test['framework'],
               'test': test['aggregate']['test'], 'visual_judgment': visual['summary'],
               'safe_repair': {'outcome_counts': dict(Counter(r['outcome'] for r in repair['targets'])),
                               'target_count': len(repair['targets']), 'regressions': repair['regressions']},
               'provenance': {'source_run_sha256': hashlib.sha256(source_paths[0].read_bytes()).hexdigest(),
                              'test_reused_from': str(args.test_scores.resolve()),
                              'test_scores_sha256': hashlib.sha256(args.test_scores.read_bytes()).hexdigest()},
               'limitations': repair['limitations'] + [
                   'Automatic VL labels are uncalibrated: event 254 overclaims an asset cause; event 346 rationale references the wrong page region.',
                   'Visual-only stage excludes the text-only functional episode (299–315); its Test and replay results are included.',
                   'Versions V0–V3 exclude later deployment/docs changes. Final submitted runtime is not re-evaluated here.',
                   'Target expectations and functional assertions are reviewed pilot annotations, not model-generated scores or official evaluation.',
                   'Three episodes cover the current extraction, not proof that every check in the entire raw session was recalled.'],
               'composite_score': None}
    write_json(out / 'scores.json', summary)
    write_json(out / 'test_scores.json', test)
    esc = html.escape
    def rel(path):
        return esc(os.path.relpath(path, out))
    def detail(title, value):
        return f'<details><summary>{esc(title)}</summary><pre>{esc(json.dumps(value, ensure_ascii=False, indent=2))}</pre></details>'
    def image(path, label):
        return f'<figure><figcaption>{esc(label)}</figcaption><a href="{rel(path)}"><img loading="lazy" src="{rel(path)}"></a></figure>'
    parts = ['<!doctype html><html lang="zh"><meta charset="utf-8"><title>SmartRecruiters 三阶段评测</title>',
             '<style>body{font:16px/1.65 system-ui;margin:32px;max-width:1200px;color:#182433}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;text-align:left}.images{display:flex;flex-wrap:wrap}img{max-width:260px;max-height:560px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6fa;padding:12px}section{border-top:1px solid #ddd;margin-top:24px}summary{cursor:pointer}figure{margin:12px}</style>',
             '<h1>SmartRecruiters：Test → Visual Judgment → Safe Repair</h1>',
             '<p>Claude Opus 4.8 / Claude Code / 历史 self_verify。Qwen3.8-27B 作为离线 Judge。研究评测，不是官方 benchmark 分数；自动结论待人工校准。</p>']
    t = summary['test']; coverage = t['workflow_coverage']
    parts.append(f'<h2>1. Test：检查是否合理</h2><p>{t["action_group_count"]} 组调用；执行情况 {esc(str(t["execution_counts"]))}；合理性 {esc(str(t["reasonableness_counts"]))}。完整 workflow 覆盖 {coverage["numerator"]}/{coverage["denominator"]}，另 {coverage["partial_only_count"]} 项部分覆盖。覆盖不代表功能通过。</p>')
    for episode in test['episodes']:
        for group in episode['test']['groups']:
            parts.append(detail(f'原动作 {group["action_ordinal"]}：{group["reasonableness"]["status"]}',
                        {k: group[k] for k in ('action','execution','reasonableness')}))
    parts.append('<h2>2. Visual Judgment：模型对反馈的判断</h2>')
    parts.append(f'<p>{esc(json.dumps(visual["summary"],ensure_ascii=False))}</p><p><a href="visual/index.html">打开逐项输入图片、原话与 Judge 理由</a>。254 的因果证据及 346 的区域引用仍有疑点，自动“正确”不代表人工确认。</p>')
    parts.append('<h2>3. Safe Repair：重放后，目标问题是否解决</h2><p>四个代码版本，三段连续修复，五个目标判断。图片都是本轮离线重放结果，不是补入原模型上下文的图片。</p>')
    labels = {'fixed':'已修复','not_fixed':'未修复','no_reproduced_failure':'修改前未复现问题','regression':'出现回归','insufficient_evidence':'证据不足'}
    for row in repair['targets']:
        packet = read_json(out / 'repair/inputs' / (row['id'] + '.json'))
        verdict = ((row.get('judge') or {}).get('parsed') or {})
        parts.append(f'<section><h3>{esc(row["id"])}：{row["before"]} → {row["after"]} · {labels[row["outcome"]]}</h3><p>{esc(row["expectation"])}</p><div class="images">')
        for p, label in zip(packet['images'], ['原型要求', row['before']+' 修复前', row['after']+' 修复后']):
            parts.append(image(p, label))
        parts.append('</div><p>' + esc(verdict.get('reason') or 'Judge 尚未提供有效结果') + '</p>')
        parts.append(detail('原始 Judge 输出', verdict) + '</section>')
    parts.append('<h3>相关功能回归</h3><p>五组原始 JS 检查在各版本重新运行，以修改前实际通过者作为基线；属于评测方的回归测试，不冒充原 Agent 主动复查。</p>')
    for row in repair['regressions']:
        parts.append(detail(row['before']+' → '+row['after']+'：'+row['status'], row))
    parts.append('<h3>逐步动作与截图</h3><p>函数体取自原事件 299/302/305/310/313。新增 route reset 和逐步截图会改变运行时序；不能声称与历史 CLI 逐字节一致。</p>')
    for version, path in repair['replays'].items():
        replayed = read_json(path)
        parts.append(f'<details><summary>{version}：代码与 5 组动作</summary><p><a href="repair/versions/{version}/manifest.json">代码版本清单</a></p>')
        for check in replayed['checks']:
            parts.append(f'<details><summary>{esc(check["name"])} · 原事件 {check["source_ordinal"]} · 断言 {check.get("passed")}</summary>')
            parts.append(detail('原函数及最终输出', {k:check.get(k) for k in ('source_function','output','error')}))
            for step in check['steps']:
                parts.append(f'<p>{esc(step["action"])} {esc(str(step["args"]))} → {esc(step["status"])}</p>')
                if step.get('observation'):
                    parts.append(image(step['observation']['screenshot'], step['observation']['url']))
            parts.append('</details>')
        parts.append('</details>')
    parts.append('<h2>范围与限制</h2>' + detail('完整记录',summary) + '</html>')
    (out / 'index.html').write_text(''.join(parts))
    print(out / 'index.html')


if __name__ == '__main__':
    main()
