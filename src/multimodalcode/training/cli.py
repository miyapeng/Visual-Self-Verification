"""Prepare handoffs, collect continuations, and export the two training datasets."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from multimodalcode.backends import OpenAICompatibleBackend
from multimodalcode.io import read_json, write_json
from multimodalcode.vsv_eval.judge import build_client, load_judge_config
from .collector import audit_teacher, collect
from .environment import TOOL_SCHEMAS, WebEnvironment
from .handoff import candidate_handoffs, export_rl, export_swift, load_checkpoint, make_checkpoint, validate_splits


def export_datasets(manifest, output, *, mode, policy_revision=None, kinds=('diagnosis',)):
    entries = read_json(manifest)
    checkpoints = [load_checkpoint(e['checkpoint']) for e in entries]
    validate_splits(checkpoints)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    counts = {}
    for split in ('train', 'validation'):
        selected = [(e, c) for e, c in zip(entries, checkpoints) if c['split'] == split]
        if not selected:
            continue
        if mode == 'sft':
            if split != 'train':
                continue
            rows = [export_swift(c, read_json(e['suffix']), read_json(e['audit']),
                                tools=[] if c['kind'] == 'diagnosis' else TOOL_SCHEMAS) for e, c in selected]
            with (output / 'train.jsonl').open('x') as stream:
                for row in rows:
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        else:
            if not policy_revision:
                raise ValueError('RL export requires the current student policy revision')
            by_kind = {kind: [] for kind in kinds}
            for entry, checkpoint in selected:
                if checkpoint['kind'] not in by_kind:
                    raise ValueError('Manifest includes a decision kind outside this experiment')
                by_kind[checkpoint['kind']].append(export_rl(entry['checkpoint'], expected_policy_revision=policy_revision))
            if len({len(v) for v in by_kind.values()}) != 1 or not all(by_kind.values()):
                raise ValueError('Supply equal nonzero checkpoint counts per enabled kind and split')
            rows = [row for group in zip(*by_kind.values()) for row in group]
            import pandas as pd
            pd.DataFrame(rows).to_parquet(output / f'{split}.parquet', index=False)
        counts[split] = len(rows)
    write_json(output / 'manifest.json', {'source': str(Path(manifest).resolve()), 'mode': mode,
               'policy_revision': policy_revision, 'kinds': list(kinds), 'counts': counts})
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('snapshot', help='Capture a resettable Web starting state')
    p.add_argument('--workspace', required=True)
    p.add_argument('--runtime')
    p.add_argument('--output', required=True)
    p = commands.add_parser('checkpoint', help='Bind native history, environment and evaluation inputs')
    p.add_argument('--spec', required=True)
    p.add_argument('--output', required=True)
    p = commands.add_parser('candidates', help='Locate handoff candidates in existing verification rounds')
    p.add_argument('--rounds', required=True)
    p.add_argument('--output', required=True)
    p = commands.add_parser('collect', help='Continue a student handoff with a teacher or student endpoint')
    p.add_argument('--checkpoint', required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--base-url', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--max-turns', type=int, default=8)
    p.add_argument('--max-tokens', type=int, default=4096)
    p.add_argument('--temperature', type=float, default=0.7)
    p.add_argument('--no-evaluate', action='store_true')
    p.add_argument('--collect-boundaries', action='store_true')
    p.add_argument('--policy-revision', help='Exact student weight revision for newly collected handoffs')
    p = commands.add_parser('audit', help='Independently review complete teacher messages for SFT')
    p.add_argument('--checkpoint', required=True)
    p.add_argument('--collection', required=True)
    p.add_argument('--judge-config', required=True)
    p.add_argument('--profile', required=True)
    for name in ('export-sft', 'export-rl'):
        p = commands.add_parser(name)
        p.add_argument('--manifest', required=True)
        p.add_argument('--output', required=True)
        if name == 'export-rl':
            p.add_argument('--policy-revision', required=True)
            p.add_argument('--kinds', nargs='+', choices=['task', 'evidence', 'diagnosis', 'repair'], default=['diagnosis'])
    args = parser.parse_args(argv)
    if args.command == 'snapshot':
        output = Path(args.output).resolve()
        env = WebEnvironment(args.workspace, output / 'session', read_json(args.runtime) if args.runtime else None)
        try:
            print(json.dumps(env.snapshot(output / 'checkpoint'), indent=2))
        finally:
            env.close()
    elif args.command == 'checkpoint':
        if Path(args.output).exists():
            raise FileExistsError(args.output)
        write_json(args.output, make_checkpoint(**read_json(args.spec)))
    elif args.command == 'candidates':
        if Path(args.output).exists():
            raise FileExistsError(args.output)
        write_json(args.output, candidate_handoffs(read_json(args.rounds)))
    elif args.command == 'collect':
        checkpoint = load_checkpoint(args.checkpoint)
        backend = OpenAICompatibleBackend(args.model, args.base_url)
        asyncio.run(collect(checkpoint, backend, args.output, max_turns=args.max_turns,
                    max_tokens=args.max_tokens, temperature=args.temperature,
                    evaluate=not args.no_evaluate, collect_boundaries=args.collect_boundaries,
                    policy_revision=args.policy_revision))
    elif args.command == 'audit':
        root = Path(args.collection).resolve()
        judge = build_client(load_judge_config(args.judge_config), args.profile, root / 'audit_cache')
        audit_teacher(load_checkpoint(args.checkpoint), read_json(root / 'suffix.json'),
                      read_json(root / 'tool_records.json'), read_json(root / 'evaluation/reward.json'), judge, root / 'audit.json')
    else:
        print(json.dumps(export_datasets(args.manifest, args.output,
              mode='sft' if args.command == 'export-sft' else 'rl',
              policy_revision=getattr(args, 'policy_revision', None), kinds=getattr(args, 'kinds', ['diagnosis']))))


if __name__ == '__main__':
    main()
