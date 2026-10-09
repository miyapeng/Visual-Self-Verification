"""Container-only sequential replay worker; never invokes a model."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

WORKSPACE = Path('/workspace')
ROOT = Path('/replay')
OUT = ROOT/'output'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(path):
    if not path.is_file():
        return None
    return {'sha256': sha(path), 'mtime_ns': path.stat().st_mtime_ns, 'size': path.stat().st_size}


def manifest(image_paths):
    files = {}
    for directory, dirs, names in os.walk(WORKSPACE):
        dirs[:] = sorted(d for d in dirs if d not in {'node_modules', '.git', '.playwright-cli', '.cache'}
                              and not (Path(directory) == WORKSPACE and d in {'resources', 'prototypes'}))
        for name in sorted(names):
            path = Path(directory)/name
            if str(path) not in image_paths and path.is_file() and not path.is_symlink():
                files[str(path.relative_to(WORKSPACE))] = sha(path)
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    target = OUT/'versions'/f'{digest}.json'
    target.parent.mkdir(exist_ok=True)
    if not target.exists():
        target.write_text(json.dumps(files, indent=2)+'\n')
    return digest


def apply_file(step):
    data = step['payload']
    path = Path(data['file_path'])
    resolved = path.resolve()
    if not any(resolved.is_relative_to(root) for root in (Path('/workspace'), Path('/tmp'))):
        raise ValueError('Mutation escapes container workspace')
    path.parent.mkdir(parents=True, exist_ok=True)
    if step['tool'] == 'Write':
        path.write_text(data['content'])
    else:
        old = path.read_text()
        count = old.count(data['old_string'])
        if not data['old_string'] or not count or (not data.get('replace_all') and count != 1):
            raise ValueError(f"Edit mismatch at {step['event_id']}: {count}")
        path.write_text(old.replace(data['old_string'], data['new_string'], -1 if data.get('replace_all') else 1))



def save_checkpoint(request, image_paths):
    """Export bytes behind the existing code manifest, excluding dependency trees."""
    digest = manifest(set())
    files = json.loads((OUT/'versions'/f'{digest}.json').read_text())
    destination = OUT/'checkpoints'/request['version']/'app'
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)
    for relative, expected in files.items():
        source = WORKSPACE/relative
        target = destination/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        if sha(target) != expected:
            raise ValueError('Workspace changed during checkpoint export')
    links = {}
    for directory, dirs, names in os.walk(WORKSPACE):
        dirs[:] = [d for d in dirs if d not in {'node_modules', '.git', '.playwright-cli', '.cache'}
                              and not (Path(directory) == WORKSPACE and d in {'resources', 'prototypes'})]
        for name in dirs + names:
            path = Path(directory)/name
            if path.is_symlink():
                target = path.resolve()
                for material in ('resources', 'prototypes'):
                    base = WORKSPACE/material
                    if target.is_relative_to(base):
                        links[path.relative_to(WORKSPACE).as_posix()] = str(Path(material)/target.relative_to(base))
    return {**request, 'workspace': str(destination.parent.relative_to(OUT)),
            'code_manifest': {'files': files, 'sha256': digest}, 'material_links': links}

def main():
    plan = json.loads((ROOT/'plan.json').read_text())
    OUT.mkdir(exist_ok=True)
    (OUT/'images').mkdir(exist_ok=True)
    requested = {row['read_event_id']: row for row in plan['images']}
    image_paths = {row['original_path'] for row in requested.values()}
    producers, captures, failures = {}, [], []
    checkpoints = []
    mutations = []
    pending = list(plan.get('checkpoint_events', []))
    cwd = '/workspace'
    start = time.monotonic()
    for step in plan['steps']:
        eid, tool = step['event_id'], step['tool']
        if time.monotonic()-start > plan['task_timeout']:
            failures.append({'event_id': eid, 'reason': 'Task time budget exhausted'})
            break
        try:
            while pending and pending[0]['after_event'] < eid:
                checkpoints.append({**save_checkpoint(pending.pop(0), image_paths), 'edits': list(mutations)})
            if tool in {'Write', 'Edit'}:
                apply_file(step)
                mutations.append({'ordinal': eid, 'kind': 'file_mutation'})
            elif tool == 'Bash':
                before = {p: fingerprint(Path(p)) for p in image_paths}
                version_before = manifest(image_paths)
                checkpoint_before = manifest(set()) if pending else None
                timeout = min(plan['command_timeout'], max(1, int(plan['task_timeout']-(time.monotonic()-start))))
                script = "trap 'pwd -P > /replay/cwd' EXIT\n" + step['payload']['command']
                with (OUT/f'action-{eid}.stdout').open('w') as stdout, (OUT/f'action-{eid}.stderr').open('w') as stderr:
                    result = subprocess.run(['timeout', '--signal=TERM', '--kill-after=5s', str(timeout),
                                             'bash', '-c', script], cwd=cwd, stdout=stdout, stderr=stderr)
                if (ROOT/'cwd').exists():
                    cwd = (ROOT/'cwd').read_text().strip()
                if step.get('recorded_cwd_reset'):
                    cwd = step['recorded_cwd_reset']
                row = {'event_id': eid, 'returncode': result.returncode, 'cwd_after': cwd,
                       'original_is_error': step.get('original_is_error', False)}
                with (OUT/'actions.jsonl').open('a') as log:
                    log.write(json.dumps(row)+'\n')
                if pending and manifest(set()) != checkpoint_before:
                    mutations.append({'ordinal': eid, 'kind': 'shell_mutation'})
                changed = {p: fingerprint(Path(p)) for p in image_paths}
                generated = [p for p in image_paths if changed[p] and changed[p] != before[p]]
                if generated:
                    version = manifest(image_paths)
                    for path in generated:
                        producers[path] = {'event_id': eid, 'file': changed[path],
                                           'code_sha256': version if version == version_before else None,
                                           'workspace_after_action_sha256': version}
                if plan.get('checkpoint_events') and result.returncode and not step.get('original_is_error', False):
                    failures.append({'event_id': eid, 'reason': 'Replay command failed before a required checkpoint'})
                    break
                if result.returncode in (124, 137):
                    failures.append({'event_id': eid, 'reason': 'Recorded command timed out'})
                    break
            elif tool == 'Read':
                wanted = requested[eid]
                path = Path(wanted['original_path'])
                producer = producers.get(str(path))
                if not producer or producer['event_id'] != wanted['capture_event_id']:
                    failures.append({'read_event_id': eid, 'reason': 'Screenshot producer could not be reproduced',
                                     'expected_producer': wanted['capture_event_id'], 'observed_producer': producer})
                    continue
                current = fingerprint(path)
                if current != producer['file']:
                    raise ValueError('Screenshot changed outside its recorded producing action')
                destination = OUT/'images'/f'{eid}-{path.name}'
                destination.write_bytes(path.read_bytes())
                from PIL import Image
                with Image.open(destination) as image:
                    dimensions = list(image.size)
                    image.verify()
                captures.append({**wanted, 'image_path': str(destination.relative_to(OUT)),
                                 'image_sha256': current['sha256'], 'dimensions': dimensions,
                                 'code_sha256': producer['code_sha256'],
                                 'workspace_after_action_sha256': producer['workspace_after_action_sha256']})
            while pending and pending[0]['after_event'] <= eid:
                checkpoints.append({**save_checkpoint(pending.pop(0), image_paths), 'edits': list(mutations)})
            print(json.dumps({'event_id': eid, 'tool': tool, 'images': len(captures)}), flush=True)
        except Exception as error:
            failures.append({'event_id': eid, 'reason': f'{type(error).__name__}: {error}'})
            break
    status = 'completed' if len(captures) == len(requested) and not pending and not failures else 'partial' if captures else 'blocked'
    runtime = {}
    for name, command in {'node': ['node', '--version'], 'npm': ['npm', '--version'],
                          'installed_chrome': ['google-chrome', '--version'], 'cli': ['playwright-cli', '--version']}.items():
        row = subprocess.run(command, capture_output=True, text=True, timeout=15)
        runtime[name] = row.stdout.strip()
    (OUT/'result.json').write_text(json.dumps({'status': status, 'captures': captures,
                                             'failures': failures, 'runtime': runtime, 'checkpoints': checkpoints}, indent=2)+'\n')


if __name__ == '__main__':
    main()
