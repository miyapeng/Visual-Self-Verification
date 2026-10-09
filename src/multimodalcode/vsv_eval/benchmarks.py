"""Small, lossless input adapters for the supported coding benchmarks.

Task requirements and official final outcomes stay separate from agent evidence.
No imported command is executed here.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tarfile
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from multimodalcode.io import read_json, write_json
from .native import blocks_text, compact, extract_images, classify, parse_claude
from .episodes import extract_candidate_windows, _resolve_image
from .checks import _call_id, _results

BENCHMARKS = ('vision2web', 'swe-mm', '3dcodebench', 'gamedevbench')


def read_sdk_archive(source):
    """Read only event JSON, never extract archive paths or load provider settings."""
    with tarfile.open(source) as archive:
        members = sorted(m for m in archive.getnames() if '/events/event-' in m and m.endswith('.json'))
        sessions = {str(Path(m).parent) for m in members}
        if len(sessions) != 1:
            raise ValueError('Select one conversation archive')
        rows = []
        for name in members:
            member = archive.getmember(name)
            if not member.isfile() or member.size > 32 * 1024**2:
                raise ValueError('Invalid or oversized event member')
            rows.append((name, json.load(archive.extractfile(member))))
    return rows


def sdk_timeline(rows, output):
    timeline, calls = [], {}
    for name, event in rows:
        base = {'source_event_id': event['id'], 'source_member': name,
                'source_ordinal': int(re.search(r'event-(\d+)-', name).group(1)),
                'timestamp': event.get('timestamp'), 'attempt': 1, 'scope': 'main_session'}
        def append(kind, **values):
            timeline.append({**base, 'ordinal': len(timeline), 'kind': kind, **values})
        kind = event.get('kind')
        if kind == 'MessageEvent':
            content = event.get('llm_message', {}).get('content', [])
            append('model_text' if event.get('source') == 'agent' else 'user_text',
                   text=blocks_text(content), images=extract_images(content, output))
        elif kind == 'ActionEvent':
            thought = blocks_text(event.get('thought'))
            if thought:
                append('model_text', text=thought)
            if event.get('reasoning_content'):
                append('reasoning', text=event['reasoning_content'])
            tool, payload = event['tool_name'], event['action']
            calls[event['id']] = event['tool_call_id']
            append('action', tool=tool, payload=compact(payload), category=classify(tool, payload),
                   tool_call_id=event['tool_call_id'])
        elif kind in {'ObservationEvent', 'AgentErrorEvent'}:
            observed = event.get('observation') or {}
            call_id = event.get('tool_call_id') or calls.get(event.get('action_id'))
            if not call_id:
                raise ValueError('Unpaired SDK observation')
            append('observation', tool=event.get('tool_name'), tool_call_id=call_id,
                   text=blocks_text(observed.get('content')) or event.get('message', ''),
                   payload=compact({k:v for k,v in observed.items() if k not in {'content','screenshot_data'}}),
                   images=extract_images(observed, output),
                   is_error=bool(observed.get('is_error') or kind == 'AgentErrorEvent'))
    return timeline


def import_run(source, output, *, benchmark, case_id, model, format):
    if benchmark not in BENCHMARKS:
        raise ValueError('Unsupported benchmark')
    source, output = Path(source).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    if format == 'openhands-sdk':
        timeline = sdk_timeline(read_sdk_archive(source), output)
    elif format == 'claude':
        numbered = {i:json.loads(line) for i,line in enumerate(source.read_text().splitlines(),1) if line.strip()}
        rows = list(numbered.values())
        sessions = {r['sessionId'] for r in rows if r.get('sessionId')}
        if len(sessions)>1:
            raise ValueError('Select one Claude session per imported trajectory')
        if not any(r.get('type') in {'user', 'assistant'} for r in rows):
            raise ValueError('Expected native Claude messages, not final results')
        timeline, _ = parse_claude(SimpleNamespace(run_dir=source.parent, slug=case_id), output, source_paths=[source])
        for event in timeline:
            original = numbered[event['raw_line']]
            event['source_event_id'] = original.get('uuid', str(event['raw_line']))
    elif format == 'canonical':
        original = read_json(source)
        timeline = original.get('timeline')
        if not isinstance(timeline, list):
            raise ValueError('Expected a canonical timeline')
        # Resolve local image paths before changing the timeline location.
        for event in timeline:
            event['images'] = [({**im, 'path': _resolve_image(im.get('path', ''), source) or im.get('path', '')}
                               if isinstance(im, dict) else _resolve_image(im, source) or im)
                              for im in event.get('images', [])]
    else:
        raise ValueError('Unsupported trajectory format')
    if not any(e['kind'] == 'action' for e in timeline):
        raise ValueError('Final outcomes alone are not a verification trajectory')
    ids = [e['ordinal'] for e in timeline]
    if ids != sorted(set(ids)):
        raise ValueError('Canonical event IDs must be unique and ordered')
    actions = [_call_id(e) for e in timeline if e['kind'] == 'action']
    if None in actions or len(actions) != len(set(actions)):
        raise ValueError('Tool calls must have unique IDs')
    if any(_call_id(e) not in actions for e in timeline if e['kind'] == 'observation'):
        raise ValueError('An observation has no matching original call')
    initial = next((e.get('text','') for e in timeline if e['kind']=='user_text'), '')
    for i,e in enumerate(timeline):
        path = (e.get('payload') or {}).get('file_path')
        if e['kind']=='action' and e.get('category')=='view-image' and path and path in initial:
            e['image_role']='task_reference'
            for observed in _results(timeline,i):
                observed['image_role']='task_reference'
    run = {'case_id': case_id, 'benchmark': benchmark, 'model': model,
           'source': {'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'format': format},
           'timeline': timeline, 'development': original.get('development', {}) if format == 'canonical' else {}}
    write_json(output / 'run.json', run)
    return run


def prepare_task(output, *, benchmark, run=None, task_source=None, task_id=None, fetch_references=False):
    """Normalize user-visible requirements only; never read solution/validation code."""
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    references = []
    if benchmark == 'gamedevbench':
        task = read_json(Path(task_source) / 'task_config.json')
        prompt = task['instruction']
    elif task_source:
        source = Path(task_source)
        if source.is_dir():
            filename = 'prompt_description.txt' if benchmark == '3dcodebench' else 'prompt.txt'
            source = source / filename
        if source.suffix == '.json':
            task = read_json(source)
            if task_id and task.get('instance_id') != task_id:
                raise ValueError('SWE task ID does not match the selected trajectory')
            prompt = task['problem_statement']
        else:
            prompt = source.read_text()
    else:
        if not run:
            raise ValueError('Provide a task source or the recorded initial user message')
        user = next((e for e in run['timeline'] if e['kind'] == 'user_text' and e.get('text')), None)
        if not user or not user.get('text'):
            raise ValueError('Initial task message is absent')
        prompt = user['text']
        for e in run['timeline']:
            if e['kind'] in {'action','model_text','reasoning'}:
                break
            if e['kind']=='user_text':
                references.extend(e.get('images', []))
        # The prompt explicitly names reference image paths in image-to-3D logs.
        for index, event in enumerate(run['timeline']):
            path = (event.get('payload') or {}).get('file_path')
            if event['kind'] == 'action' and event.get('category') == 'view-image' and path and path in prompt:
                for result in _results(run['timeline'], index):
                    references.extend(result.get('images', []))
        issue = re.search(r'<issue_description>(.*?)</issue_description>', prompt, re.S)
        if issue:
            prompt = issue.group(1).strip()
    (output / 'prompt.txt').write_text(prompt + ('\n' if not prompt.endswith('\n') else ''))
    missing = []
    root = Path(run['_path']) if run and '_path' in run else output / 'run.json'
    for index, ref in enumerate(references):
        raw = ref if isinstance(ref, str) else ref.get('path', '')
        resolved = _resolve_image(raw, root) if raw else None
        if not resolved and fetch_references and isinstance(ref,dict) and ref.get('source'):
            url=ref['source']
            if not url.startswith(('https://','http://')):
                raise ValueError('Reference download requires an HTTP URL')
            with urllib.request.urlopen(url,timeout=30) as response:
                data=response.read(16*1024**2+1)
            if len(data)>16*1024**2:raise ValueError('Reference exceeds the 16 MiB limit')
            from PIL import Image
            from io import BytesIO
            with Image.open(BytesIO(data)) as image:
                image.verify()
            dest=output/'prototypes'/f'reference-{index}{Path(url.split("?",1)[0]).suffix or ".png"}'
            dest.parent.mkdir(exist_ok=True);dest.write_bytes(data)
            ref={**ref,'retrieved_path':str(dest),'retrieved_sha256':hashlib.sha256(data).hexdigest()}
            references[index]=ref
            continue
        if not resolved:
            missing.append(ref)
            continue
        dest = output / 'prototypes' / f'reference-{index}{Path(resolved).suffix}'
        dest.parent.mkdir(exist_ok=True); shutil.copy2(resolved, dest)
    write_json(output / 'source.json', {'benchmark': benchmark, 'task_id': task_id,
               'task_source': str(task_source) if task_source else 'initial_user_message',
               'reference_sources':references, 'missing_references': missing})
    return missing


def audit_input(run_path):
    run_path = Path(run_path).resolve(); run = read_json(run_path)
    windows = extract_candidate_windows(run_path)
    timeline = run['timeline']
    images = [{'event_id': e['ordinal'], 'kind': e['kind'],
               'role': 'task_reference' if e['kind']=='user_text' else e.get('image_role','agent_observation'),
               'path': im if isinstance(im, str) else im.get('path', ''),
               'available': bool(_resolve_image(im if isinstance(im, str) else im.get('path',''), run_path))}
              for e in timeline for im in e.get('images', [])]
    return {'benchmark': run['benchmark'], 'source_run': str(run_path), 'events': len(timeline),
            'candidate_windows': len(windows), 'llm_filtered': False, 'images': images,
            'recorded_edit_events': [e['ordinal'] for e in timeline if e.get('category') == 'edit' and e['kind'] == 'action'],
            'missing_tool_results': [e['ordinal'] for i,e in enumerate(timeline)
                                     if e['kind']=='action' and not _results(timeline,i)],
            'acceptance_requirement': 'Audited before/after versions and benchmark-specific executable probes are required for linked repairs.'}
