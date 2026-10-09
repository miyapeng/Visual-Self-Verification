#!/usr/bin/env python3
"""Extract verification rounds using the shared annotation stage."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from multimodalcode.io import write_json
from multimodalcode.vsv_eval.episodes import extract_candidate_windows, extract_verification_rounds
from multimodalcode.vsv_eval.judge import build_client, load_judge_config, write_model_call_report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-json',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--config',type=Path,default=Path('configs/vision2web/vsv_protocol.json'))
    parser.add_argument('--primary-profile')
    parser.add_argument('--offline',action='store_true',help='Write unfiltered rule candidates without model calls')
    args=parser.parse_args()
    if args.offline:
        write_json(args.output_dir/'candidates.json',{'source_run':str(args.run_json.resolve()),
                   'llm_filtered':False,'windows':extract_candidate_windows(args.run_json)})
        return
    config=load_judge_config(args.config)
    judge=build_client(config,args.primary_profile or config['primary_stage_profiles']['verification_annotation'],args.output_dir/'judge_cache')
    write_json(args.output_dir/'verification_rounds.json',extract_verification_rounds(args.run_json,judge))
    write_model_call_report(args.output_dir)


if __name__=='__main__':
    main()
