#!/usr/bin/env python3
"""Prepare archived Vision2Web acceptance bindings without calling a model or replaying commands."""
import argparse
import json
from pathlib import Path
import shutil
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO/'src'))
from multimodalcode.io import read_json, write_json
from multimodalcode.vsv_eval.checks import _results
from multimodalcode.vsv_eval.evaluation import load_rounds
from multimodalcode.vsv_eval.preparation import checkpoint_requests, repair_groups
from multimodalcode.vsv_eval.system import load_experiment


def prepare(manifest, output, image):
    experiment = load_experiment(manifest)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Use a fresh preparation directory')
    commands, cases, inventory = [], [], []
    for case in experiment['cases']:
        if not case.get('rounds_json'):
            raise ValueError('Prepare extraction before binding archived acceptance')
        rounds = load_rounds(case['rounds_json'])
        groups = repair_groups(rounds)
        if not groups:
            cases.append(case)
            continue
        projects = set()
        for index, event in enumerate(rounds['events']):
            p = event.get('payload', {})
            if event.get('kind') != 'action' or event.get('tool') != 'Write' or not p.get('file_path', '').endswith('/package.json'):
                continue
            replies = _results(rounds['events'], index)
            if len(replies) != 1 or replies[0].get('is_error'):
                continue
            package = json.loads(p['content'])
            if 'vite' in package.get('scripts', {}).get('dev', ''):
                path = Path(p['file_path']).parent
                if path.is_relative_to('/workspace'):
                    projects.add(path.relative_to('/workspace').as_posix())
        if len(projects) != 1:
            raise ValueError(f"{case['id']}: supply an explicit runtime binding for this project layout")
        directory = output/case['id']
        fixture = directory/'fixture'
        (fixture/'trajectory').mkdir(parents=True)
        shutil.copyfile(rounds['source_run'], fixture/'trajectory/run.json')
        binding_path = directory/'acceptance_binding.json'
        binding = {'fixture': str(fixture), 'version_manifest': str(directory/'versions.json'),
                   'runtime': {'command': [sys.executable, str(REPO/'scripts/vision2web/serve_checkpoint.py'),
                                           '--image', image, '--materials', case['task_root'],
                                           '--project', next(iter(projects)), '--port', '{port}'],
                               'cwd': '.', 'code_subdir': 'app', 'ready_timeout': 180}}
        write_json(binding_path, binding)
        requested = checkpoint_requests(rounds)
        write_json(directory/'checkpoint_requests.json', requested)
        source = Path(read_json(rounds['source_run'])['source']['path'])
        task_id = source.parents[2].name+'/'+source.parents[1].name
        commands.append({'case_id': case['id'], 'argv': [sys.executable,
            str(REPO/'scripts/vision2web/reconstruct_leaderboard_images.py'),
            '--model-root', str(source.parents[3]), '--task-root', str(Path(case['task_root']).parents[1]),
            '--tasks', task_id, '--rounds-json', case['rounds_json'], '--acceptance-binding', str(binding_path),
            '--work-root', str(directory/'replay'), '--image', image, '--execute']})
        cases.append({**{k: v for k, v in case.items() if k not in {'state_spec','repair_results','check_results'}},
                      'acceptance_binding': str(binding_path)})
        inventory.append({'case_id': case['id'], 'repair_group_count': len(groups),
                          'checkpoint_count': len(requested), 'status': 'awaiting_checkpoint_export'})
    write_json(output/'experiment.json', {**experiment, 'cases': cases})
    write_json(output/'replay_commands.json', commands)
    write_json(output/'inventory.json', inventory)
    return inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--image', default='vision2web-sandbox:latest')
    args = parser.parse_args()
    print(json.dumps(prepare(args.manifest, args.output_dir, args.image)))


if __name__ == '__main__':
    main()
