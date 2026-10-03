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
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><title>SmartRecruiters evaluation report</title>',
             '<style>body{font:16px/1.65 system-ui;margin:32px;max-width:1200px;color:#182433}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;text-align:left}.images{display:flex;flex-wrap:wrap}img{max-width:260px;max-height:560px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6fa;padding:12px}section{border-top:1px solid #ddd;margin-top:24px}summary{cursor:pointer}figure{margin:12px}</style>',
             '<h1>SmartRecruiters: Test → Visual Judgment → Safe Repair</h1>',
             '<p>Claude Opus 4.8 / Claude Code / recorded self_verify run. Qwen3.8-27B serves as the offline Judge. These research metrics are separate from official benchmark scores. Automatic assessments need manual calibration.</p>']
    t = summary['test']; coverage = t['workflow_coverage']
    parts.append(f'<h2>1. Test</h2><p>{t["action_group_count"]} action groups. Execution: {esc(str(t["execution_counts"]))}; Reasonableness: {esc(str(t["reasonableness_counts"]))}. Full workflow coverage: {coverage["numerator"]}/{coverage["denominator"]}; {coverage["partial_only_count"]} items have partial coverage. Coverage records exercised behavior, not functional correctness.</p>')
    for episode in test['episodes']:
        for group in episode['test']['groups']:
            parts.append(detail(f'Recorded action {group["action_ordinal"]}: {group["reasonableness"]["status"]}',
                        {k: group[k] for k in ('action','execution','reasonableness')}))
    parts.append('<h2>2. Visual Judgment</h2>')
    parts.append(f'<p>{esc(json.dumps(visual["summary"],ensure_ascii=False))}</p><p><a href="visual/index.html">View evidence images, model quotes, and Judge explanations</a>. The causal evidence at event 254 and the region reference at event 346 still need review. Automatic labels have not been independently confirmed.</p>')
    parts.append('<h2>3. Safe Repair</h2><p>Four code versions, three repair transitions, and five target assessments. The images come from offline replay and were not part of the original agent context.</p>')
    labels = {'fixed':'Fixed','not_fixed':'Not fixed','no_reproduced_failure':'No reproduced failure before modification','regression':'Regression','insufficient_evidence':'Insufficient evidence'}
    for row in repair['targets']:
        packet = read_json(out / 'repair/inputs' / (row['id'] + '.json'))
        verdict = ((row.get('judge') or {}).get('parsed') or {})
        parts.append(f'<section><h3>{esc(row["id"])}: {row["before"]} → {row["after"]} · {labels[row["outcome"]]}</h3><p>{esc(row["expectation"])}</p><div class="images">')
        for p, label in zip(packet['images'], ['Reference prototype', row['before']+' before repair', row['after']+' after repair']):
            parts.append(image(p, label))
        parts.append('</div><p>' + esc(verdict.get('reason') or 'No valid Judge assessment') + '</p>')
        parts.append(detail('Recorded Judge response', verdict) + '</section>')
    parts.append('<h3>Functional regression checks</h3><p>Five recorded JavaScript checks are rerun on each version, using checks that passed before modification as the baseline. These are evaluator-run regression tests, separate from the agent’s recorded rechecks.</p>')
    for row in repair['regressions']:
        parts.append(detail(row['before']+' → '+row['after']+': '+row['status'], row))
    parts.append('<h3>Actions and screenshots</h3><p>Function bodies come from events 299, 302, 305, 310, and 313. Added route resets and step screenshots change execution timing; this replay is not an exact reproduction of the historical CLI run.</p>')
    for version, path in repair['replays'].items():
        replayed = read_json(path)
        parts.append(f'<details><summary>{version}: Code and five action groups</summary><p><a href="repair/versions/{version}/manifest.json">Code version manifest</a></p>')
        for check in replayed['checks']:
            parts.append(f'<details><summary>{esc(check["name"])} · Source event {check["source_ordinal"]} · Assertion result {check.get("passed")}</summary>')
            parts.append(detail('Recorded function and replay output', {k:check.get(k) for k in ('source_function','output','error')}))
            for step in check['steps']:
                parts.append(f'<p>{esc(step["action"])} {esc(str(step["args"]))} → {esc(step["status"])}</p>')
                if step.get('observation'):
                    parts.append(image(step['observation']['screenshot'], step['observation']['url']))
            parts.append('</details>')
        parts.append('</details>')
    parts.append('<h2>Scope and limitations</h2>' + detail('Full results',summary) + '</html>')
    (out / 'index.html').write_text(''.join(parts))
    print(out / 'index.html')


if __name__ == '__main__':
    main()
