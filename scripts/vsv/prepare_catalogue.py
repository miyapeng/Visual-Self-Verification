#!/usr/bin/env python3
"""Freeze task-only criteria with the existing JudgeClient; no solver execution."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from multimodalcode.io import read_json
from multimodalcode.vsv_eval.catalogue import prepare_evaluation_plan
from multimodalcode.vsv_eval.judge import build_client, load_judge_config


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-root',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--config',type=Path,default=Path('configs/vision2web/vsv_protocol.json'))
    parser.add_argument('--primary-profile')
    args=parser.parse_args()
    metadata=args.task_root/'source.json'
    if metadata.is_file() and read_json(metadata).get('missing_references'):
        raise ValueError('Recover missing task references before preparing the catalogue')
    config=load_judge_config(args.config)
    judge=build_client(config,args.primary_profile or config['primary_stage_profiles']['plan_review'],args.output_dir/'judge_cache')
    prepare_evaluation_plan(args.task_root,judge,args.output_dir,catalogue_only=True)


if __name__=='__main__':
    main()
