"""Plan bounded replays of archived image-producing actions without an agent."""
from __future__ import annotations

import hashlib
import json
import re
import shlex
from pathlib import Path

from .checks import _results
from .episodes import _visual_producer


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_reconstructed_images(index_path, rounds):
    """Validate a sidecar against original receipt IDs without changing the timeline."""
    index_path = Path(index_path).resolve()
    index = json.loads(index_path.read_text())
    source = json.loads(Path(rounds['source_run']).read_text())
    raw = index_path.parent / index['source_trajectory']
    if sha256(raw) != index['source_trajectory_sha256'] or source['source']['sha256'] != index['source_trajectory_sha256']:
        raise ValueError('Reconstructed images belong to a different source trajectory')
    events = {e['ordinal']: e for e in rounds['events']}
    images = {}
    for capture in index['captures']:
        path = (index_path.parent / capture['image_path']).resolve()
        if not path.is_relative_to(index_path.parent) or sha256(path) != capture['image_sha256']:
            raise ValueError('Reconstructed image path or hash is invalid')
        reads = capture.get('read_event_ids', [capture.get('read_event_id')])
        producer = capture['capture_event_id']
        if events.get(producer, {}).get('tool') != 'Bash' or events[producer]['kind'] != 'action':
            raise ValueError('Reconstructed image cites an invalid producer')
        for receipt in capture['image_receipt_event_ids']:
            event = events.get(receipt, {})
            matched = [events[r] for r in reads if r in events
                       and events[r].get('tool_call_id') == event.get('payload', {}).get('tool_use_id')]
            if (event.get('kind') != 'observation' or len(event.get('images', [])) != 1 or len(matched) != 1
                    or matched[0].get('tool') != 'Read' or matched[0].get('kind') != 'action'
                    or matched[0]['payload'].get('file_path') != capture['original_path']
                    or not producer < matched[0]['ordinal'] < receipt or receipt in images):
                raise ValueError('Reconstructed image has an ambiguous or invalid receipt mapping')
            images[receipt] = {'path': str(path), 'sha256': capture['image_sha256'],
                               'capture_event_id': producer, 'origin': 'reconstructed',
                               'code_sha256': capture.get('code_sha256', index.get('code_sha256')),
                               'pixel_equivalence_to_original': 'unverified'}
    return images


def blocked_shell(command):
    """Conservatively stop for destructive operations; this is not a shell sandbox."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return 'Shell syntax requires review'
    if {word.rsplit('/', 1)[-1] for word in words} & {'rm', 'rmdir', 'unlink', 'shred', 'truncate', 'delete-data',
                     'cookie-clear', 'localstorage-clear', 'sessionstorage-clear'}:
        return 'Recorded deletion requires explicit review'
    if re.search(r'\bgit\s+(?:reset|clean|checkout|restore)\b|\bfind\b[^\n]*-delete\b|'
                 r'\b(?:rmtree|unlink|remove|rmSync|unlinkSync)\s*\(', command):
        return 'Recorded destructive operation requires explicit review'
    if re.search(r'(^|[;&|\n])\s*(?:sudo\s+)?(?:eval|source)\s|\b(?:curl|wget)\b[^\n]*\|\s*(?:ba)?sh\b', command):
        return 'Indirect shell execution requires review'
    return None


def _remember_file(files, event, replies):
    if event.get('kind') != 'action' or len(replies) != 1 or replies[0].get('is_error'):
        return
    payload = event.get('payload', {})
    path = payload.get('file_path', '')
    if event.get('tool') == 'Write':
        files[path] = payload.get('content', '')
    elif event.get('tool') == 'Edit' and path in files:
        old = payload.get('old_string', '')
        text = files[path]
        if old and (text.count(old) == 1 or payload.get('replace_all')):
            files[path] = text.replace(old, payload['new_string'], -1 if payload.get('replace_all') else 1)
        else:
            files.pop(path)


def _script_sources(events):
    """Expose recorded helper bodies to provenance matching, never to execution."""
    files, expanded = {}, []
    for index, event in enumerate(events):
        _remember_file(files, event, _results(events, index))
        if event.get('kind') == 'action' and event.get('tool') == 'Bash':
            command = event.get('payload', {}).get('command', '')
            for match in re.finditer(r"\bcat\s+>\s*([^\s<>]+)\s*<<\s*(['\"])(\w+)\2\n(.*?)\n\3(?:\n|$)", command, re.S):
                files[match[1]] = match[4]
            for source, target in re.findall(r'\bcp\s+(/[^\s;&|]+)\s+(/[^\s;&|]+)', command):
                if source in files:
                    files[target] = files[source]
            scripts = re.findall(r'(?:^|[;&|(\n])\s*(?:\w+=[^\s;&|]+\s+)*(?:python3?|node|bash|sh)\s+([^\s;&|]+)', command)
            for script in scripts:
                bodies = [text for path, text in files.items()
                          if path == script or path.endswith('/'+script)]
                if len(bodies) == 1:
                    command += '\n' + bodies[0]
            # Normalize Python f-string paths only for source matching. Execution
            # still uses the original command; actual file production is verified.
            command = re.sub(r'''\bf(['"])([^'"\n]*)\1''',
                             lambda m: m[1] + re.sub(r'\{[^{}]+\}', '*', m[2]) + m[1], command)
            event = {**event, 'payload': {**event.get('payload', {}), 'command': command}}
        expanded.append(event)
    return expanded


def plan_replay(run, task_root, *, checkpoint_events=()):
    events = run['timeline']
    evidence_events = _script_sources(events)
    images, unresolved = [], []
    for index, event in enumerate(events):
        if event.get('kind') != 'action' or event.get('tool') != 'Read':
            continue
        replies = _results(events, index)
        if not any(r.get('images') and not r.get('is_error') for r in replies):
            continue
        path = event.get('payload', {}).get('file_path', '')
        if not path or path.startswith(('/workspace/prototypes/', '/workspace/resources/')):
            continue
        if not Path(path).is_absolute() or '..' in Path(path).parts:
            unresolved.append({'read_event_id': event['ordinal'], 'path': path,
                               'reason': 'Image path cannot be resolved unambiguously'})
            continue
        producer = _visual_producer(evidence_events, index)
        if producer == index:
            unresolved.append({'read_event_id': event['ordinal'], 'path': path,
                               'reason': 'No recorded application screenshot producer'})
            continue
        images.append({'read_event_id': event['ordinal'],
                       'image_receipt_event_ids': [r['ordinal'] for r in replies if r.get('images')],
                       'capture_event_id': events[producer]['ordinal'], 'original_path': path})
    if not images and not checkpoint_events:
        return {'status': 'no_replayable_image_inputs', 'images': [], 'unresolved': unresolved, 'steps': []}
    cutoff = max([row['read_event_id'] for row in images] + [r['after_event'] for r in checkpoint_events])
    steps, blocks, written_scripts = [], [], {}
    allowed = {'Bash', 'Write', 'Edit', 'Read', 'Skill', 'TaskCreate', 'TaskUpdate',
               'TaskList', 'TaskGet', 'TodoWrite', 'WebFetch', 'WebSearch'}
    for index, event in enumerate(events):
        if event['ordinal'] > cutoff:
            break
        if event.get('kind') != 'action':
            continue
        tool, payload = event.get('tool'), event.get('payload', {})
        eid = event['ordinal']
        replies = _results(events, index)
        if event.get('scope') == 'nested_subagent' or tool not in allowed:
            blocks.append({'event_id': eid, 'reason': 'Unsupported tool or nested execution', 'tool': tool})
            continue
        _remember_file(written_scripts, event, replies)
        if tool == 'Bash':
            command = payload.get('command', '')
            reason = blocked_shell(evidence_events[index]['payload']['command'])
            # Review generated startup scripts before allowing indirect execution.
            for script in re.findall(r'(?:^|[;&|(\n])\s*(?:bash|sh)\s+([^\s;&|]+)', command):
                candidates = [text for path, text in written_scripts.items() if path == script or path.endswith('/'+script)]
                if not candidates or any(blocked_shell(text) for text in candidates):
                    reason = 'Startup script requires review'
            if reason:
                blocks.append({'event_id': eid, 'reason': reason})
            steps.append({'event_id': eid, 'tool': tool, 'payload': payload,
                          'original_result_ids': [r['ordinal'] for r in replies],
                          'original_is_error': any(r.get('is_error') for r in replies)})
            resets = re.findall(r'^Shell cwd was reset to (/[^\n]+)$',
                                '\n'.join(r.get('text', '') for r in replies), re.M)
            if resets:
                steps[-1]['recorded_cwd_reset'] = resets[-1].strip()
        elif tool in {'Write', 'Edit'}:
            if len(replies) != 1:
                blocks.append({'event_id': eid, 'reason': 'File mutation lacks a unique paired result'})
            elif not replies[0].get('is_error'):
                path = Path(payload.get('file_path', ''))
                if not path.is_absolute() or '..' in path.parts or not str(path).startswith(('/workspace/', '/tmp/')):
                    blocks.append({'event_id': eid, 'reason': 'File mutation outside replay workspace'})
                if path.name == 'package.json' and str(path) in written_scripts:
                    try:
                        scripts = json.loads(written_scripts[str(path)]).get('scripts', {})
                        if any(blocked_shell(c) for c in scripts.values()):
                            blocks.append({'event_id': eid, 'reason': 'Package script contains a destructive operation'})
                    except (ValueError, TypeError):
                        blocks.append({'event_id': eid, 'reason': 'Invalid package manifest'})
                steps.append({'event_id': eid, 'tool': tool, 'payload': payload,
                              'original_result_ids': [replies[0]['ordinal']]})
        elif tool == 'Read' and any(row['read_event_id'] == eid for row in images):
            steps.append({'event_id': eid, 'tool': 'Read', 'payload': payload})
    if not Path(task_root).is_dir():
        blocks.append({'reason': 'Original task materials are missing'})
    return {'status': 'blocked' if blocks else 'ready', 'images': images,
            'checkpoint_events': list(checkpoint_events),
            'unresolved': unresolved, 'steps': steps, 'blocks': blocks, 'cutoff': cutoff,
            'limitations': ['Image bytes are reconstructed, not recovered originals.',
                            'Original dependency lock and exact browser build may be unavailable.',
                            'Shell review is conservative; isolation is provided by a dedicated container.']}
