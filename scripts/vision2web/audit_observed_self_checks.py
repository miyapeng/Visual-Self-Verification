#!/usr/bin/env python3
"""Audit saved Claude Code sessions; no model calls or browser execution."""
from __future__ import annotations

import argparse
import csv
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


def read(path):
    return json.loads(Path(path).read_text())


def lines(path):
    with Path(path).open() as handle:
        for number, line in enumerate(handle, 1):
            if line.strip():
                yield number, json.loads(line)


def blocks(event):
    value = event.get('message', event)
    content = value.get('content', []) if isinstance(value, dict) else []
    return content if isinstance(content, list) else []


def content_text(content):
    if isinstance(content, str):
        return content
    return '\n'.join(b.get('text', '') for b in content or [] if isinstance(b, dict))


BROWSER = re.compile(r'playwright|puppeteer|selenium|chrom(?:e|ium)|browser_|screenshot', re.I)
APP = re.compile(r'https?://(?:localhost|127\.0\.0\.1|\[::1\]):3000(?:[/\s\'"`)]|$)', re.I)
CLI = re.compile(r'(?:^|[\s;&|])(?:[\w/.-]*/)?playwright-cli\s+(?:(?:--?[^\s]+)\s+)*(\w[\w-]*)', re.I)
AUTOMATION = re.compile(r'page\.screenshot\s*\(|chromium\.launch\s*\(|sync_playwright\s*\(|get_screenshot_as|puppeteer\.launch\s*\(')
NETWORK = re.compile(r'403 Forbidden|407 Proxy|\bE403\b|Received HTTP code 40[37]|EAI_AGAIN|ENOTFOUND|ETIMEDOUT|CERT_HAS_EXPIRED', re.I)


def source_path(value):
    path = Path(value)
    return (path.suffix.lower() in {'.html', '.css', '.scss', '.js', '.jsx', '.ts', '.tsx', '.vue', '.svelte', '.py', '.sh', '.json'}
            and not set(path.parts).intersection({'node_modules', '.playwright-cli', 'dist', 'build', 'test_results'})
            and path.name not in {'package-lock.json', 'prompt.json', 'tsconfig.tsbuildinfo'})


def ref(event):
    return {k: event.get(k) for k in ('raw_path', 'action_line', 'result_line', 'tool_id', 'scope', 'time', 'input', 'text')}


def audit(path):
    result = read(path)
    folder = path.parent
    trace = Path(result['development_trace'])
    events, actions, settings = [], [], []
    raw_paths = sorted(folder.glob('claude.events.attempt-*.jsonl'))
    if not raw_paths:
        raw_paths = [folder / 'claude.events.jsonl']
    for raw in raw_paths:
        capture = raw.with_name(raw.name.replace('events', 'capture'))
        times = {row['sequence'] + 1: row['timestamp'] for _, row in lines(capture)
                 if row.get('stream') == 'stdout'} if capture.exists() else {}
        tools = {}
        for number, event in lines(raw):
            timestamp = event.get('timestamp') or times.get(number)
            if event.get('type') == 'system' and event.get('subtype') == 'init':
                settings.append({k: event.get(k) for k in ('model', 'claude_code_version', 'tools', 'skills')})
            for block in blocks(event):
                if block.get('type') == 'tool_use':
                    tools[block['id']] = {'tool': block['name'], 'input': block.get('input', {}),
                                         'action_line': number, 'action_time': timestamp,
                                         'scope': event.get('parent_tool_use_id') or 'main'}
                elif block.get('type') == 'tool_result':
                    tool = tools.get(block.get('tool_use_id'))
                    if not tool:
                        continue
                    text = content_text(block.get('content'))
                    images = [b for b in block.get('content', []) if isinstance(b, dict)
                              and b.get('type') == 'image'] if isinstance(block.get('content'), list) else []
                    inp = tool['input']
                    command = str(inp.get('command', ''))
                    evidence = {**tool, 'input': {k: v for k, v in inp.items() if k not in {'content', 'old_string', 'new_string'}},
                                'tool_id': block.get('tool_use_id'), 'raw_path': str(raw),
                                'result_line': number, 'time': timestamp, 'is_error': bool(block.get('is_error')),
                                'text': text[:10000], 'image_blocks': len(images)}
                    actions.append({**evidence, 'text': text[:500],
                                    'browser_program': bool(AUTOMATION.search(str(inp))),
                                    'dependency_network_error': tool['tool'] in {'Bash', 'TaskOutput'} and bool(NETWORK.search(text)),
                                    'changed_text': tool['tool'] == 'Write' or (
                                        tool['tool'] == 'Edit' and inp.get('old_string') != inp.get('new_string'))})
                    if (images or (tool['tool'] == 'Bash' and BROWSER.search(command))
                            or BROWSER.search(tool['tool'])
                            or (tool['tool'] in {'Write', 'Edit'} and BROWSER.search(str(inp)))
                            or (tool['tool'] == 'Read' and re.search(r'\.(png|jpe?g|webp)$', str(inp.get('file_path', '')), re.I))):
                        events.append(evidence)
        completed = {a['tool_id'] for a in actions if a['raw_path'] == str(raw)}
        for tool_id, tool in tools.items():
            if tool_id not in completed:
                actions.append({**tool, 'input': {k: v for k, v in tool['input'].items() if k not in {'content', 'old_string', 'new_string'}},
                                'tool_id': tool_id, 'raw_path': str(raw), 'result_line': None,
                                'time': None, 'is_error': None, 'text': '', 'image_blocks': 0})
    changes = []
    workspace = trace / 'workspace.events.jsonl'
    if workspace.exists():
        changes = [row for _, row in lines(workspace) if row.get('type') in {'workspace_change', 'deployment_ready'}]
    return {'case_id': result['case_id'], 'status': result['status'], 'result_path': str(path),
            'trace': str(trace), 'events': events, 'actions': actions, 'settings': settings, 'workspace_events': changes,
            'raw_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in raw_paths}}


def classify(row):
    actions = row['actions']
    browser, unmatched = [], []
    current_url = {}
    for action in actions:
        command = str(action['input'].get('command') or '')
        verbs = CLI.findall(command) if action['tool'] == 'Bash' else []
        if not verbs:
            continue
        scope = (action['raw_path'], action['scope'])
        url = re.search(r'Page URL:\s*(https?://\S+)', action['text'])
        if url:
            current_url[scope] = url.group(1)
        success = action['is_error'] is False and '### Error' not in action['text']
        # URL is grounded in a browser result, not the requested URL or an HTTP probe.
        if success and APP.search(current_url.get(scope, '')) and any(v not in {'close', 'install', 'help'} for v in verbs):
            browser.append(action)
        elif action['result_line'] is None:
            unmatched.append(ref(action))
    image_reads = [a for a in actions if a['image_blocks'] > 0]
    source_images, unclassified_images, reviewed_images = [], [], []
    for a in image_reads:
        name = str(a['input'].get('file_path') or '')
        if '/prototypes/' in name or '/resources/' in name:
            source_images.append(a)
        else:
            # Audited exception: a reference image copied into generated assets.
            # Keep the provenance explicit; do not assume all public/ images are references.
            root = Path(row['result_path']).parent / 'workspace'
            original = root / 'prototypes/mobile.jpg'
            copied = root / 'public/images/grad-students-hero.jpg'
            copy_calls = [t for t in actions if t['tool'] == 'Bash'
                          and t['raw_path'] == a['raw_path'] and t['result_line'] is not None
                          and t['result_line'] < a['action_line'] and t['is_error'] is False
                          and 'cd /workspace/public/images && cp /workspace/prototypes/mobile.jpg grad-students-hero.jpg' in str(t['input'].get('command'))]
            if (row['case_id'] == 'webpage/alberta_alis'
                    and name == '/workspace/public/images/grad-students-hero.jpg'
                    and copy_calls and original.exists() and copied.exists()
                    and hashlib.sha256(original.read_bytes()).hexdigest()
                    == hashlib.sha256(copied.read_bytes()).hexdigest()
                    == '70406dd7d109b2178ca0b580aab7c2b3c44591ec7471360b0fee66949e9f2737'):
                source_images.append(a)
                reviewed_images.append({'read': ref(a), 'copy': ref(copy_calls[-1]),
                                        'classification': 'manually audited copied prototype, not app screenshot'})
            else:
                unclassified_images.append(ref(a))
    # Do not infer application screenshots from a stale browser URL.
    # Non-input images require provenance review before counting as application vision.
    programs = [ref(a) for a in actions if a.get('browser_program')]
    changed_paths = {p for event in row['workspace_events'] if event['type'] == 'workspace_change'
                     for kind in ('created', 'modified', 'deleted') for p in event['payload'].get(kind, [])}
    edits = []
    if browser:
        first = browser[0]
        for action in actions:
            path = str(action['input'].get('file_path', '')).removeprefix('/workspace/')
            if (action['raw_path'] == first['raw_path'] and action['scope'] == first['scope']
                    and action['action_line'] > first['result_line']
                    and action['tool'] in {'Write', 'Edit'} and action.get('changed_text')
                    and action['is_error'] is False and source_path(path) and path in changed_paths):
                edits.append(action)
    rechecks = [a for a in browser if edits and a['raw_path'] == edits[0]['raw_path']
                and a['action_line'] > edits[0]['result_line']]
    ready = [r for r in row['workspace_events'] if r['type'] == 'deployment_ready' and r['payload'].get('http_status') == 200]
    network = [ref(a) for a in actions if a.get('dependency_network_error')]
    return {'case_id': row['case_id'], 'level': {'webpage': 'L1', 'frontend': 'L2', 'website': 'L3'}[row['case_id'].split('/')[0]],
            'status': row['status'], 'result_path': row['result_path'], 'trace': row['trace'],
            'opened_application_in_browser': bool(browser),
            'application_screenshot_received': None if unclassified_images or programs else False,
            'edited_after_application_image': None if unclassified_images or programs else False,
            'rechecked_after_image_then_edit': None if unclassified_images or programs else False,
            'edited_after_browser_text': bool(edits), 'rechecked_after_browser_text_edit': bool(rechecks),
            'observer_http_200': bool(ready), 'input_image_read_events': len(source_images),
            'any_image_read_events': len(image_reads), 'dependency_network_error': bool(network),
            'playwright_skill_registered': all('playwright-cli' in (s.get('skills') or []) for s in row['settings']) if row['settings'] else False,
            'browser_attempts': sum(bool(CLI.search(str(a['input'].get('command') or ''))) for a in actions if a['tool']=='Bash'),
            'browser_successful_observations': len(browser),
            'interaction_successes': sum(bool(re.search(r'playwright-cli\s+(?:click|fill|hover|press|select)\b', str(a['input'].get('command')))) for a in browser),
            'evidence': {'browser': [ref(a) for a in browser], 'edits': [ref(a) for a in edits],
                         'rechecks': [ref(a) for a in rechecks], 'unclassified_images': unclassified_images,
                         'browser_programs': programs, 'unmatched_browser_calls': unmatched,
                         'network_examples': network[:2], 'http_200': ready[:1]},
            'reviewed_image_provenance': reviewed_images,
            'tool_counts': dict(Counter(a['tool'] for a in actions)),
            'unmatched_tool_calls': sum(a['result_line'] is None for a in actions),
            'raw_sha256': row['raw_sha256']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-root', type=Path, action='append', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    selected, attempts = {}, []
    for root in args.run_root:
        for path in sorted(root.glob('agents/*/vision2web/claude_code/official/*/result.json')):
            value = read(path)
            record = {'path': str(path.resolve()), 'case_id': value['case_id'], 'status': value['status'],
                      'started_at': value.get('started_at', '')}
            attempts.append(record)
            previous = selected.get(record['case_id'])
            if previous is None or record['started_at'] > previous['started_at']:
                selected[record['case_id']] = record
    rows = []
    for index, record in enumerate(sorted(selected.values(), key=lambda x: x['case_id'])):
        row = audit(Path(record['path']))
        rows.append(row)
        if (index + 1) % 25 == 0:
            print(f'Audited {index + 1}/{len(selected)}', flush=True)
    payload = {'selection': 'latest started_at per task, regardless of outcome', 'attempts': attempts, 'cases': rows}
    (args.output_dir / 'inventory.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    cases = [classify(row) for row in rows]
    (args.output_dir / 'cases.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2) + '\n')
    fields = ['case_id', 'level', 'status', 'opened_application_in_browser',
              'application_screenshot_received', 'edited_after_application_image',
              'rechecked_after_image_then_edit', 'edited_after_browser_text',
              'rechecked_after_browser_text_edit', 'observer_http_200',
              'any_image_read_events', 'dependency_network_error', 'trace']
    with (args.output_dir / 'cases.csv').open('w') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(cases)
    metrics = fields[3:10] + ['dependency_network_error', 'playwright_skill_registered']
    summary = {level: {'tasks': len(group), **{
        key: {'yes': sum(r[key] is True for r in group),
              'unknown': sum(r[key] is None for r in group)} for key in metrics},
        'tasks_with_image_reads': sum(r['any_image_read_events'] > 0 for r in group)}
        for level in ['all', 'L1', 'L2', 'L3']
        for group in [[r for r in cases if level == 'all' or r['level'] == level]]}
    prompts = [read(Path(r['trace']) / 'prompt.json') for r in rows]
    summary['prompt_audit'] = {'count': len(prompts), 'official_hash_matches': sum(
        p.get('mode') == 'official' and p.get('sha256') == p.get('official_prompt_sha256')
        and bool(p.get('sha256')) and not p.get('guided_vsv_instruction') for p in prompts)}
    (args.output_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'cases': len(rows), 'status': dict(Counter(r['status'] for r in rows)),
                      'browser_or_image_candidate_cases': sum(bool(r['events']) for r in rows)}))


if __name__ == '__main__':
    main()
