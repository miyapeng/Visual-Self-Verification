#!/usr/bin/env python3
"""Prepare a small benchmark case for the shared verification evaluator."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from multimodalcode.io import write_json, read_json
from multimodalcode.vsv_eval.benchmarks import BENCHMARKS, import_run, prepare_task, audit_input
from multimodalcode.vsv_eval.episodes import extract_candidate_windows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--benchmark', choices=BENCHMARKS, required=True)
    p.add_argument('--trajectory', type=Path)
    p.add_argument('--format', choices=['claude', 'openhands-sdk', 'canonical'])
    p.add_argument('--task-source', type=Path)
    p.add_argument('--task-id', required=True)
    p.add_argument('--model', default='unspecified')
    p.add_argument('--result-json', type=Path, help='Optional official final outcome; never used as agent evidence')
    p.add_argument('--fetch-reference-images', action='store_true', help='Fetch recorded task reference URLs with a 16 MiB per-image limit')
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    if bool(args.trajectory) != bool(args.format):
        p.error('--trajectory and --format are required together')
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=False)
    run = None
    if args.trajectory:
        run = import_run(args.trajectory, out / 'trajectory', benchmark=args.benchmark,
                         case_id=args.task_id, model=args.model, format=args.format)
        run['_path'] = str(out / 'trajectory/run.json')
    missing = prepare_task(out / 'task', benchmark=args.benchmark, run=run,
                           task_source=args.task_source, task_id=args.task_id, fetch_references=args.fetch_reference_images)
    audit = audit_input(run['_path']) if run else {'benchmark': args.benchmark, 'status': 'missing_trajectory'}
    audit['missing_task_references'] = missing
    if run:
        write_json(out / 'candidates.json', {'source_run': run['_path'], 'llm_filtered': False,
                                             'windows': extract_candidate_windows(run['_path'])})
        write_json(out / 'case.json', {'id': args.task_id, 'model': args.model, 'benchmark': args.benchmark,
                                      'task_root': str(out / 'task'), 'run_json': run['_path'],
                                      'catalogue': str(out / 'plan/catalogue.json')})
    if args.result_json:
        results = read_json(args.result_json)
        rows = [r for r in results.get('tasks', []) if r.get('task_name') == args.task_id]
        if len(rows) != 1:
            raise ValueError('Expected exactly one official task result')
        write_json(out / 'official_outcome.json', {'source': str(args.result_json.resolve()), 'result': rows[0]})
    write_json(out / 'input_audit.json', audit)
    print(json.dumps({k:v for k,v in audit.items() if k not in {'images', 'missing_task_references'}}, indent=2))


if __name__ == '__main__':
    main()
