"""Restore explicitly selected recorded file mutations without executing shell logs."""
from __future__ import annotations

import hashlib
import re
import shlex
from pathlib import Path, PurePosixPath

from multimodalcode.io import read_json, write_json
from .checks import _results
from .repair_pilot import digest_tree


def _relative(path, root):
    path, root = PurePosixPath(path), PurePosixPath(root)
    if '..' in path.parts or not path.is_absolute() or not root.is_absolute():
        raise ValueError('Source paths must be absolute and contain no traversal')
    relative = path.relative_to(root)
    if not relative.parts:
        raise ValueError('A mutation must name a file')
    return relative.as_posix()


def _shell_mutation(command):
    """Read literal quoted heredocs or a two-path copy; never run shell syntax."""
    match = re.fullmatch(
        r"cat\s*>\s*(.+?)\s*<<\s*(['\"])([A-Za-z_][A-Za-z_0-9]*)\2\n"
        r"(.*?)\n\3(?:\n(echo\s+[^\n]+))?\s*", command, re.S)
    if match:
        paths = shlex.split(match[1])
        if len(paths) != 1:
            raise ValueError('Heredoc must have one literal output path')
        return {'operation': 'write', 'path': paths[0], 'content': match[4] + '\n'}
    words = shlex.split(command)
    if words[:1] == ['cp'] and (len(words) == 3 or words[3:] == ['&&', 'ls']):
        if any(word.startswith('-') for word in words[1:3]):
            raise ValueError('Copy options are not supported')
        return {'operation': 'copy', 'source': words[1], 'path': words[2]}
    raise ValueError('Unsupported shell mutation; supply an audited snapshot instead')


def _mutation(event):
    tool, payload = event.get('tool'), event.get('payload', {})
    if tool in {'Bash', 'terminal'}:
        return _shell_mutation(payload['command'])
    if tool == 'Edit':
        return {'operation': 'edit' if payload['old_string'] else 'create',
                'path': payload['file_path'], 'old': payload['old_string'],
                'content': payload['new_string'], 'replace_all': payload.get('replace_all', False)}
    if tool == 'Write':
        return {'operation': 'write', 'path': payload['file_path'],
                'content': payload.get('content', payload.get('new_string'))}
    if tool == 'file_editor' and payload['command'] in {'str_replace', 'create'}:
        return {'operation': 'edit' if payload['command'] == 'str_replace' else 'create',
                'path': payload['path'], 'old': payload.get('old_str'),
                'content': payload.get('new_str', payload.get('file_text')), 'replace_all': False}
    raise ValueError(f"Unsupported mutation event: {event['ordinal']}")


def restore_versions(run_path, plan, output):
    """Write existing version-manifest records for an explicitly audited file scope.

    An absent base is an unknown baseline. It never produces an invented empty
    before-version. Rendered output must remain evaluator evidence, not a new
    historical image receipt in the source timeline.
    """
    run_path, output = Path(run_path).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    events = read_json(run_path)['timeline']
    by_id = {e['ordinal']: (i, e) for i, e in enumerate(events)}
    ids = plan['event_ids']
    if not ids or ids != sorted(set(ids)) or not set(ids) <= set(by_id):
        raise ValueError('Mutation IDs must be ordered, unique original event IDs')
    if plan.get('scope') not in {'repository', 'recorded_files'}:
        raise ValueError('Declare repository or recorded_files scope')
    base = Path(plan['base_snapshot']).resolve() if plan.get('base_snapshot') else None
    if plan['scope'] == 'repository' and base is None:
        raise ValueError('Repository restoration requires a base snapshot')
    state = {}
    if base:
        if not base.is_dir():
            raise ValueError('Missing base snapshot')
        for path in sorted(base.rglob('*')):
            if '.git' in path.relative_to(base).parts:
                continue
            if path.is_symlink():
                raise ValueError('Resolve and audit base snapshot symlinks before restoration')
            if path.is_file():
                state[path.relative_to(base).as_posix()] = path.read_bytes()
    base_hash = digest_tree(base) if base else None
    versions, edits, failed = [], [], []
    last_event = None

    def save(ordinal):
        nonlocal last_event
        if ordinal == last_event:
            return
        name = f'V{len(versions)}'
        app = output / name / 'app'
        app.mkdir(parents=True)
        for relative, data in state.items():
            path = app / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        row = {'version': name, 'after_event': ordinal, 'workspace': str(app.parent),
               'code_manifest': digest_tree(app), 'edits': list(edits)}
        versions.append(row)
        last_event = ordinal

    for eid in ids:
        index, event = by_id[eid]
        if event['kind'] != 'action' or not event.get('tool_call_id'):
            raise ValueError('A mutation must be an original tool call with an ID')
        replies = _results(events, index)
        if len(replies) != 1 or replies[0]['ordinal'] <= eid:
            raise ValueError(f'No unique subsequent return for mutation {eid}')
        observed = replies[0].get('payload', {})
        if replies[0].get('is_error') or observed.get('exit_code', 0) not in (None, 0):
            failed.append(eid)
            continue
        mutation = _mutation(event)
        relative = _relative(mutation['path'], plan['source_root'])
        before = state.get(relative)
        if isinstance(observed.get('old_content'), str) and before != observed['old_content'].encode():
            raise ValueError(f'Restored baseline differs from recorded old_content at {eid}')
        operation, content = mutation['operation'], mutation.get('content')
        if operation == 'copy':
            source = _relative(mutation['source'], plan['source_root'])
            if source not in state:
                raise ValueError('Copy source has not been reconstructed')
            data = state[source]
        else:
            if not isinstance(content, str):
                raise ValueError('Recorded file content must be a string')
            if operation == 'edit':
                old = mutation['old']
                text = before.decode() if before is not None else ''
                count = text.count(old) if old else 0
                if not count or (not mutation.get('replace_all') and count != 1):
                    raise ValueError(f'Ambiguous or missing edit at {eid}: {count} matches')
                content = text.replace(old, content, -1 if mutation.get('replace_all') else 1)
            elif operation == 'create' and before is not None:
                raise ValueError(f'Create would replace an existing file at {eid}')
            data = content.encode()
        if isinstance(observed.get('new_content'), str) and data != observed['new_content'].encode():
            raise ValueError(f'Restored edit differs from recorded new_content at {eid}')
        if base is not None or state:
            save(eid - 1)
        state[relative] = data
        edits.append({'ordinal': eid, 'return_event_id': replies[0]['ordinal'], 'path': relative,
                      'kind': 'shell_mutation' if event['tool'] in {'Bash', 'terminal'} else 'edit',
                      'operation': operation,
                      'recorded_content_verified': isinstance(observed.get('new_content'), str),
                      'before_sha256': hashlib.sha256(before).hexdigest() if before is not None else None,
                      'after_sha256': hashlib.sha256(data).hexdigest()})
        save(eid)
    result = {'source_run': str(run_path), 'source_run_sha256': hashlib.sha256(run_path.read_bytes()).hexdigest(),
              'version_source': plan, 'base_manifest': base_hash, 'versions': versions,
              'skipped_failed_event_ids': failed,
              'scope': 'Selected recorded file mutations only; not a complete shell or session replay.',
              'historical_equivalence': 'Not verified against original checkpoint files.'}
    write_json(output / 'reconstruction.json', result)
    return result
