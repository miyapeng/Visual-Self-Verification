"""Auditable historical Edit reconstruction; no inference or changes to source runs."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from multimodalcode.io import write_json


def digest_tree(root):
    files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.rglob('*')) if p.is_file()
             and '.playwright-cli' not in p.parts and not p.is_symlink()}
    return {'files': files, 'sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def reconstruct(fixture, spec, output):
    """Only supports explicitly audited Edit-only intervals; fail on missing/ambiguous edits."""
    fixture, output = Path(fixture).resolve(), Path(output).resolve()
    run = json.loads((fixture / 'trajectory/run.json').read_text())
    source = spec.get('version_source', {})
    first = fixture / source.get('base_snapshot', 'trajectory/development/versions/P_first/app')
    final = fixture / source.get('final_snapshot', 'trajectory/development/versions/P_final/app')
    state = {p.relative_to(first).as_posix(): p.read_bytes() for p in first.rglob('*')
             if p.is_file() and '.playwright-cli' not in p.parts}
    edits, versions, touched = [], [], set()
    if output.exists():
        raise FileExistsError(f'Reconstruction destination already exists: {output}')

    def save(name, event):
        destination = output / name / 'app'
        for relative, data in state.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        manifest = digest_tree(destination)
        # Runtime assets were excluded by the historical snapshot. Restore unchanged task inputs.
        (destination / 'resources').symlink_to(fixture / 'agent_visible/resources', target_is_directory=True)
        row = {'version': name, 'after_event': event, 'workspace': str(destination.parent),
               'code_manifest': manifest, 'resource_root': str(fixture / 'agent_visible/resources'),
               'edits': list(edits)}
        write_json(destination.parent / 'manifest.json', row)
        versions.append(row)

    save('V0', spec['base_before_event'] - 1)
    seen = []
    events = run['timeline']
    for event in events:
        ordinal = event['ordinal']
        if ordinal not in spec['edit_ordinals']:
            continue
        if event.get('tool') != 'Edit' or event.get('kind') != 'action':
            raise ValueError(f'Not an Edit action: {ordinal}')
        call_id = event.get('tool_call_id')
        replies = [r for r in events if r.get('kind') == 'observation'
                   and (r.get('payload') or {}).get('tool_use_id') == call_id]
        if not call_id or len(replies) != 1 or replies[0].get('is_error'):
            raise ValueError(f'No unique successful Edit result: {ordinal}')
        p = event['payload']
        path = p['file_path'].removeprefix(source.get('source_prefix', '/workspace/app/'))
        if path not in state or Path(path).is_absolute() or '..' in Path(path).parts:
            raise ValueError(f'Unsafe or missing edit path: {path}')
        text, old, new = state[path].decode(), p['old_string'], p['new_string']
        count = text.count(old)
        if not old or not count or (not p.get('replace_all') and count != 1):
            raise ValueError(f'Ambiguous old_string at {ordinal}: {count}')
        state[path] = (text.replace(old, new) if p.get('replace_all') else text.replace(old, new, 1)).encode()
        touched.add(path)
        edits.append({'ordinal': ordinal, 'file': path, 'old_matches': count,
                      'sha256': hashlib.sha256(state[path]).hexdigest()})
        seen.append(ordinal)
        if ordinal in spec['boundaries']:
            save(f'V{len(versions)}', ordinal)
    if seen != spec['edit_ordinals']:
        raise ValueError('Missing or misordered edit events')
    matches = {p: state[p] == (final / p).read_bytes() for p in sorted(touched)}
    if not all(matches.values()):
        raise ValueError(f'Reconstructed files disagree with final snapshot: {matches}')
    result = {'versions': versions, 'final_touched_files_match': matches,
              'source_run_sha256': hashlib.sha256((fixture / 'trajectory/run.json').read_bytes()).hexdigest(),
              'scope': f"Audited Edit-only interval {spec['base_before_event']}–{max(spec['boundaries'])}; not a complete session replay."}
    write_json(output / 'reconstruction.json', result)
    return result


def extract_run_code(command):
    """Decode a literal double-quoted shell argument, without executing any shell expansions."""
    matches = re.findall(r'\brun-code\s+"((?:\\.|[^"\\])*)"', command, re.S)
    if len(matches) != 1:
        raise ValueError('Expected exactly one literal run-code argument')
    body = re.sub(r'\\([$`"\\\n])', lambda m: '' if m[1] == '\n' else m[1], matches[0])
    if not body.strip().startswith('async page =>'):
        raise ValueError('Unsupported function wrapper')
    return body


def assess_output(assertions, value):
    """Small declarative assertions; missing/ill-typed output is unknown, not failure."""
    if not assertions or not isinstance(value, dict):
        return None
    def lookup(pointer):
        current = value
        for key in pointer.split('.'):
            if not isinstance(current, dict) or key not in current:
                raise KeyError(pointer)
            current = current[key]
        return current
    results = []
    for rule in assertions:
        try:
            left = lookup(rule['path'])
            right = lookup(rule['other_path']) if 'other_path' in rule else rule.get('value')
            op = rule['op']
            if op == 'eq':
                result = type(left) is type(right) and left == right
            elif op == 'ne':
                result = left != right
            elif op == 'gt' and type(left) in (int, float) and type(right) in (int, float):
                result = left > right
            elif op == 'endswith' and isinstance(left, str) and isinstance(right, str):
                result = left.endswith(right)
            elif op == 'all_eq' and isinstance(left, list):
                result = bool(left) and all(type(x) is type(right) and x == right for x in left)
            else:
                return None
            results.append(result)
        except (KeyError, TypeError, AttributeError):
            return None
    return all(results)


def functional_pass(name, value):
    """Compatibility for historical pilot artifacts; new runners take explicit rules."""
    config = Path(__file__).resolve().parents[3] / 'configs/vision2web/smartrecruiters_repair_v2.json'
    rows = json.loads(config.read_text())['functional_checks']
    check = next((c for c in rows if c['name'] == name), None)
    if check is None:
        raise ValueError(f'No legacy assertion configuration for {name}')
    return assess_output(check['assertions'], value)


def prepare_versions(fixture, spec, output):
    """Use verified snapshots or an explicitly audited Edit reconstruction, never P_final substitution."""
    fixture, output = Path(fixture).resolve(), Path(output).resolve()
    source = spec.get('version_source', {})
    if source.get('kind', 'edit_reconstruction') == 'edit_reconstruction':
        return reconstruct(fixture, spec, output)
    if source['kind'] != 'manifest':
        raise ValueError('version_source requires manifest or edit_reconstruction')
    origin = Path(source['path'])
    if not origin.is_absolute():
        origin = fixture / origin
    manifest = json.loads(origin.read_text())
    digest = hashlib.sha256((fixture / 'trajectory/run.json').read_bytes()).hexdigest()
    if manifest.get('source_run_sha256') != digest:
        raise ValueError('Version manifest does not match the original trajectory')
    if output.exists():
        raise FileExistsError(output)
    code_subdir = spec['runtime'].get('code_subdir', 'app')
    copies = []
    for version in manifest['versions']:
        name = version['version']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise ValueError('Unsafe version name')
        workspace = Path(version['workspace'])
        if digest_tree(workspace / code_subdir) != version['code_manifest']:
            raise ValueError('Checkpoint bytes no longer match their manifest')
        destination = output / name
        shutil.copytree(workspace, destination, symlinks=True)
        row = {**version, 'workspace': str(destination)}
        write_json(destination / 'manifest.json', row)
        copies.append(row)
    result = {**manifest, 'versions': copies, 'source_manifest': str(origin.resolve())}
    write_json(output / 'reconstruction.json', result)
    return result


def repair_records(spec, reconstruction, timeline):
    """Explicit, reviewable links between a target, its original check and real versions."""
    events = {e['ordinal']: e for e in timeline}
    versions = {v['version']: v for v in reconstruction['versions']}
    checks = {c['name']: c for c in spec.get('functional_checks', [])}
    rows = []
    for target in spec['targets']:
        before, after = versions[target['before']], versions[target['after']]
        if before['after_event'] > after['after_event']:
            raise ValueError('Repair versions are reversed')
        if before['code_manifest']['sha256'] == after['code_manifest']['sha256']:
            raise ValueError('A repair attempt requires changed code; unchanged-version controls are not repair attempts')
        if not target.get('expectation', '').strip() or not target.get('requirement_source'):
            raise ValueError('Freeze a task-grounded expectation and its source')
        if target['policy_event'] not in events or target['policy_event'] > after['after_event']:
            raise ValueError('Invalid target policy event')
        if not target.get('source_action_ordinals') or not target.get('source_observation_ordinals'):
            raise ValueError('Link each repair to its original check and feedback')
        for kind, key in [('action', 'source_action_ordinals'), ('observation', 'source_observation_ordinals')]:
            if any(i not in events or events[i]['kind'] != kind or i >= target['policy_event'] for i in target[key]):
                raise ValueError('Original check/feedback must precede the policy statement')
        if target.get('check') and target['check'] not in checks:
            raise ValueError('Unknown target check')
        regression_ids = target.get('regression_checks', list(checks))
        if any(name not in checks for name in regression_ids):
            raise ValueError('Unknown regression check')
        rows.append({**target, 'before_code': before['code_manifest']['sha256'],
                     'after_code': after['code_manifest']['sha256'], 'regression_checks': regression_ids,
                     'trigger_kind': events[target['policy_event']]['kind'],
                     'runtime': spec['runtime'], 'causal_link': 'reviewed association, not proof of visual causality'})
    return rows


def repair_outcome(before_satisfied, after_satisfied):
    if type(before_satisfied) is not bool or type(after_satisfied) is not bool:
        return 'insufficient_evidence'
    if before_satisfied:
        return 'no_reproduced_failure' if after_satisfied else 'regression'
    return 'fixed' if after_satisfied else 'not_fixed'


def regression_result(before, after):
    eligible = [k for k, v in before.items() if v is True]
    failed = [k for k in eligible if after.get(k) is False]
    missing = [k for k in eligible if type(after.get(k)) is not bool]
    return {'status': 'regression' if failed else 'insufficient_evidence' if missing or not eligible else 'no_observed_regression',
            'baseline_passed': eligible, 'failed_after': failed, 'missing_after': missing,
            'scope': 'Configured probes that passed BEFORE; not proof of all functionality or policy-authored regression testing.'}
