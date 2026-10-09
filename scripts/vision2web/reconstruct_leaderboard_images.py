#!/usr/bin/env python3
"""Scan or replay archived Claude trajectories and publish compact image sidecars."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO/'src'))
from import_leaderboard_trace import import_trace
from multimodalcode.vsv_eval.archive_replay import plan_replay, sha256
from multimodalcode.vsv_eval.repair_pilot import digest_tree
from multimodalcode.vsv_eval.evaluation import load_rounds
from multimodalcode.vsv_eval.preparation import checkpoint_requests, publish_checkpoints


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2)+'\n')


def docker(*args, **kwargs):
    return subprocess.run(['docker', *map(str, args)], check=True, capture_output=True, text=True, **kwargs)


def existing_output(task, source):
    directory = task/'reconstructed'
    if not directory.exists():
        return None
    try:
        index = json.loads((directory/'reconstructed_observations.json').read_text())
        if index.get('source_trajectory_sha256') != sha256(source):
            return 'existing_output_requires_review'
        for row in index['captures']:
            if sha256(directory/row['image_path']) != row['image_sha256']:
                return 'existing_output_requires_review'
        return 'already_present'
    except (OSError, ValueError, KeyError):
        return 'existing_output_requires_review'


def publish(task, source, imported, attempt, plan, result, image_id):
    captures = result['captures']
    if not captures:
        return None
    destination = task/'reconstructed'
    if destination.exists():
        raise FileExistsError(destination)
    staging = task/f'.reconstructed-{uuid.uuid4().hex[:8]}'
    (staging/'images').mkdir(parents=True)
    for row in captures:
        src = attempt/'output'/row['image_path']
        assert sha256(src) == row['image_sha256']
        shutil.copy2(src, staging/row['image_path'])
    index = {'schema': 'reconstructed-observations-1', 'origin': 'reconstructed',
             'status': result['status'], 'source_trajectory': f'../results/{source.name}',
             'source_trajectory_sha256': sha256(source), 'source_run': str(imported/'run.json'),
             'source_run_sha256': sha256(imported/'run.json'), 'replay_archive': str(attempt),
             'image_id': image_id, 'runtime': result['runtime'], 'model_api_calls': 0,
             'pixel_equivalence_to_original': 'unverified', 'expected_image_receipts': len(plan['images']),
             'captures': captures, 'failures': result['failures'], 'unresolved': plan['unresolved'],
             'limitations': plan['limitations'] + ['Original Read-tool resizing/conversion is not reproduced.',
                'Successful file recovery does not establish historical pixel equivalence or artifact correctness.']}
    write(staging/'reconstructed_observations.json', index)
    os.rename(staging, destination)
    return str(destination)


def execute(case, source, materials, imported, plan, work, args):
    attempt = work/case/'attempts'/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    attempt.mkdir(parents=True, exist_ok=False)
    plan = {**plan, 'task_timeout': args.task_timeout, 'command_timeout': args.command_timeout}
    write(attempt/'plan.json', plan)
    name = 'vsv-replay-' + uuid.uuid4().hex[:12]
    image_id = docker('image', 'inspect', args.image, '--format', '{{.Id}}').stdout.strip()
    setup = {'container': name, 'image_id': image_id, 'source_sha256': sha256(source),
             'task_materials': str(materials), 'material_manifest': digest_tree(materials),
             'dependency_policy': 'Original manifest; cached downloads with bounded retries; npm audit disabled.',
             'scope': f"Original actions through image read {plan['cutoff']}; no later cleanup.",
             'model_api_calls': 0}
    write(attempt/'setup.json', setup)
    created = False
    try:
        docker('run', '-d', '--name', name, '--memory=4g', '--cpus=2', '--shm-size=1g',
               '-v', f'{args.npm_cache.resolve()}:/root/.npm',
               '-e', 'HTTP_PROXY', '-e', 'HTTPS_PROXY', '-e', 'NO_PROXY',
               '-e', 'npm_config_fetch_timeout=30000', '-e', 'npm_config_fetch_retries=1',
               '-e', 'npm_config_prefer_offline=true', '-e', 'npm_config_audit=false',
               '--entrypoint', '/bin/bash', args.image, '-lc', 'sleep infinity')
        created = True
        docker('exec', name, 'mkdir', '-p', '/replay')
        for filename in ('resources', 'prototypes', 'prompt.txt', 'workflow.json'):
            if (materials/filename).exists():
                docker('cp', materials/filename, f'{name}:/workspace/{filename}')
        docker('cp', attempt/'plan.json', f'{name}:/replay/plan.json')
        docker('cp', Path(__file__).with_name('archive_replay_worker.py'), f'{name}:/replay/worker.py')
        try:
            with (attempt/'worker.log').open('w') as output:
                run = subprocess.run(['docker', 'exec', name, 'python', '/replay/worker.py'],
                                     stdout=output, stderr=subprocess.STDOUT, timeout=args.task_timeout+60)
        finally:
            copied = subprocess.run(['docker', 'cp', f'{name}:/replay/output', str(attempt/'output')],
                                    capture_output=True, text=True)
            if copied.returncode:
                (attempt/'copy-error.log').write_text(copied.stderr)
        if run.returncode:
            raise RuntimeError(f'Worker failed with code {run.returncode}; see worker.log')
        result = json.loads((attempt/'output/result.json').read_text())
        # Keep the reconstructed dependency locks with the detailed execution archive.
        locks = docker('exec', name, 'find', '/workspace', '-name', 'node_modules', '-prune', '-o',
                       '-name', 'package-lock.json', '-print').stdout.splitlines()
        for i, path in enumerate(locks):
            docker('cp', f'{name}:{path}', attempt/f'package-lock-{i}.json')
        assert sha256(source) == setup['source_sha256'], 'Original export changed during replay'
        if args.acceptance_binding:
            publish_checkpoints(attempt, result, load_rounds(args.rounds_json), materials, args.acceptance_binding)
        destination = (str(source.parents[1]/'reconstructed') if existing_output(source.parents[1], source) == 'already_present'
                       else publish(source.parents[1], source, imported, attempt, plan, result, image_id))
        return {'status': result['status'], 'images': len(result['captures']),
                'expected': len(plan['images']), 'failures': result['failures'],
                'output': destination, 'archive': str(attempt)}
    finally:
        if created:
            docker('stop', '-t', '1', name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-root', type=Path, required=True)
    parser.add_argument('--task-root', type=Path, default=REPO/'data/vision2web/extracted')
    parser.add_argument('--work-root', type=Path, required=True)
    parser.add_argument('--tasks', nargs='+', help='Optional level/name task IDs')
    parser.add_argument('--rounds-json', type=Path, help='Existing extraction for one selected task; preserves source event IDs')
    parser.add_argument('--acceptance-binding', type=Path, help='Export repair checkpoints to this prepared binding')
    parser.add_argument('--execute', action='store_true', help='Run ready cases; otherwise only scan')
    parser.add_argument('--limit', type=int, help='Maximum number of cases to execute')
    parser.add_argument('--image', default='vision2web-sandbox:latest')
    parser.add_argument('--npm-cache', type=Path, default=Path('/data/vsv-replay-npm-cache'))
    parser.add_argument('--task-timeout', type=int, default=900)
    parser.add_argument('--command-timeout', type=int, default=300)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1 or args.task_timeout < 1 or args.command_timeout < 1:
        parser.error('Limits must be positive')
    if args.acceptance_binding and (not args.rounds_json or not args.tasks or len(args.tasks) != 1):
        parser.error('Checkpoint export requires --rounds-json and exactly one --tasks value')
    work = args.work_root.resolve()
    rows, count = [], 0
    sources = sorted(args.model_root.resolve().glob('*/*/results/*.json'))
    cases = {source.parents[2].name+'/'+source.parents[1].name for source in sources}
    if not sources or args.tasks and set(args.tasks)-cases:
        parser.error('No matching trajectories, or an unknown task ID was requested')
    for source in sources:
        case = source.parents[2].name+'/'+source.parents[1].name
        if args.tasks and case not in args.tasks:
            continue
        row = {'case': case, 'source': str(source)}
        try:
            present = existing_output(source.parents[1], source)
            if present and not args.acceptance_binding:
                row['status'] = present
            else:
                imported = work/case/'imported'
                rounds = load_rounds(args.rounds_json) if args.rounds_json else None
                if rounds:
                    imported = Path(rounds['source_run']).parent
                    run = json.loads(Path(rounds['source_run']).read_text())
                    if run['source']['sha256'] != sha256(source):
                        raise ValueError('Rounds refer to a different trajectory export')
                elif (imported/'run.json').exists():
                    run = json.loads((imported/'run.json').read_text())
                    if run['source']['sha256'] != sha256(source):
                        raise ValueError('Import cache does not match source; use a new work root')
                else:
                    run = import_trace(source, imported)
                materials = args.task_root/case
                plan = plan_replay(run, materials, checkpoint_events=checkpoint_requests(rounds) if args.acceptance_binding else ())
                plan['source_run_sha256'] = sha256(imported/'run.json')
                write(work/case/'plan.json', plan)
                row.update(status=plan['status'], expected=len(plan['images']),
                           unresolved=len(plan['unresolved']), blocks=plan.get('blocks', []))
                if args.execute and plan['status'] == 'ready' and (args.limit is None or count < args.limit):
                    args.npm_cache.mkdir(parents=True, exist_ok=True)
                    count += 1
                    print(json.dumps({'case': case, 'status': 'running', 'expected': len(plan['images'])}), flush=True)
                    row.update(execute(case, source, materials, imported, plan, work, args))
        except Exception as error:
            row.update(status='error', reason=f'{type(error).__name__}: {error}')
        rows.append(row)
        write(work/('execution.json' if args.execute else 'inventory.json'), rows)
        print(json.dumps(row), flush=True)


if __name__ == '__main__':
    main()
