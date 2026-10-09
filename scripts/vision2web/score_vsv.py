#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from multimodalcode.io import read_json, write_json  # noqa: E402
from multimodalcode.vsv_eval.episodes import (  # noqa: E402
    extract_candidate_windows, extract_episodes, extract_verification_rounds, verification_round_summary,
)
from multimodalcode.vsv_eval.judge import (  # noqa: E402
    build_client,
    load_judge_config,
    write_model_call_report,
)


STAGES = (
    "workflow_alignment",
    "visual_judgment",
    "repair_scope",
    "safe_repair_visual",
)


def build_stage_clients(config, override, cache_root, mapping_key):
    mapping = config.get(mapping_key) or {}
    if override:
        mapping = {stage: override for stage in STAGES}
    return {
        stage: build_client(config, profile, cache_root / stage)
        for stage, profile in mapping.items()
        if stage in STAGES and profile
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract verification rounds or score recorded Vision2Web checks.")
    parser.add_argument("--run-json", type=Path)
    parser.add_argument("--rounds-json", type=Path, help="Evaluate the current verification rounds with the six-metric protocol")
    parser.add_argument("--text-proxy", action="store_true", help="Estimate six metrics from archived text in a separate proxy protocol; no image or version acceptance")
    parser.add_argument("--proxy-max-input-chars", type=int, default=100000, help="Text-proxy batch input budget")
    parser.add_argument("--check-results", type=Path, help="Recompute scores from stored check labels; optionally join repair results without API calls")
    parser.add_argument("--catalogue", type=Path, help="Fixed model-reviewed target catalogue")
    parser.add_argument("--draft-catalogue", action="store_true", help="Draft task criteria without inspecting the agent trajectory")
    parser.add_argument("--prepare-plan", action="store_true", help="Use the model to review criteria and generate fixed acceptance workflows from task sources")
    parser.add_argument("--allow-draft", action="store_true", help="Explicit pilot evaluation using an unapproved catalogue")
    parser.add_argument("--reference-map", type=Path, help="Reuse reviewed reference associations instead of annotating again")
    parser.add_argument("--reconstructed-images", type=Path, help="Explicit reconstructed-evidence evaluation using a validated image sidecar; original events remain unchanged")
    parser.add_argument("--assertions", type=Path, help="Reviewed exact assertions bound to original observation IDs")
    parser.add_argument("--repair-results", type=Path, help="Validated execution-based repair records from the fixed state runner")
    parser.add_argument("--state-spec", type=Path, help="Execute fixed checks on audited versions and save repair results")
    parser.add_argument("--port", type=int, default=18951, help="Local port reserved for the state runner")
    parser.add_argument("--workers", type=int, default=1, help="Maximum concurrent independent check evaluations")
    parser.add_argument("--reuse-checks", type=Path, help="Reuse validated independent metric judgments, preserving their actual prompts and responses")
    parser.add_argument("--task-root", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
    )
    parser.add_argument("--offline", action="store_true", help="Parse and score deterministic evidence without model calls")
    parser.add_argument("--audit", action="store_true", help="Run the configured independent closed-model audit")
    parser.add_argument("--primary-profile")
    parser.add_argument("--test-visual-profile", help="Deprecated, ignored: Test now judges method relevance from text only")
    parser.add_argument("--audit-profile")
    parser.add_argument("--replay-results", type=Path)
    parser.add_argument("--max-episode-images", type=int, default=8)
    parser.add_argument("--extract-only", action="store_true", help="Extract all visual/nonvisual check rounds and references; --offline emits unfiltered candidates")
    parser.add_argument("--verification-max-input-chars", type=int, default=240000, help="Per-batch text budget for verification annotation, including complete calls and context")
    parser.add_argument("--test-only", action="store_true", help="Evaluate recorded execution and check reasonableness only; no replay")
    parser.add_argument("--visual-only", action="store_true", help="Evaluate time-bounded visual judgments only; no Test/workflow/replay")
    parser.add_argument("--judgment-ordinals", nargs="+", type=int, help="Visual-only smoke subset; still require automatic location")
    parser.add_argument("--episode-id", help="Score only this extracted episode; preserve the original trajectory")
    return parser


def infer_task_root(run_json: Path, case_id: str) -> Path:
    return PROJECT_ROOT / "data/vision2web/extracted" / case_id


def run(args) -> int:
    if args.reconstructed_images and (not args.rounds_json or args.text_proxy or args.state_spec or args.extract_only
                                     or args.check_results or args.draft_catalogue or args.prepare_plan):
        raise ValueError('--reconstructed-images requires check-round evaluation in a separate output')
    if args.text_proxy and (not args.rounds_json or not args.task_root or not args.catalogue or
                           args.offline or args.state_spec or args.repair_results or args.check_results or
                           args.extract_only or args.draft_catalogue or args.prepare_plan):
        raise ValueError("--text-proxy requires rounds, catalogue and task root; use a separate online output without strict scoring/replay inputs")
    protocol = args.rounds_json is not None or args.draft_catalogue or args.prepare_plan or args.check_results is not None
    args.config = args.config or PROJECT_ROOT / ("configs/vision2web/vsv_protocol.json" if protocol else "configs/vision2web/vsv_scoring.json")
    if protocol:
        if args.prepare_plan:
            if not args.task_root or args.offline:
                raise ValueError("--prepare-plan requires --task-root and an online judge")
            from multimodalcode.vsv_eval.catalogue import prepare_evaluation_plan
            config = load_judge_config(args.config)
            judge = build_client(config, args.primary_profile or config['primary_stage_profiles']['plan_review'],
                                 args.output_dir / 'judge_cache/plan_review')
            budget = config.get('plan_max_tokens', judge.profile.max_tokens)
            if type(budget) is not int or budget <= 0:
                raise ValueError("plan_max_tokens must be a positive integer")
            effort = config.get('plan_reasoning_effort', judge.profile.reasoning_effort)
            if effort not in {None, 'low', 'medium', 'high'}:
                raise ValueError("Unsupported plan_reasoning_effort")
            judge.profile = replace(judge.profile, max_tokens=budget, reasoning_effort=effort)
            catalogue, plan = prepare_evaluation_plan(args.task_root, judge, args.output_dir,
                                                     catalogue_path=args.catalogue, state_spec_path=args.state_spec)
            print(json.dumps({'catalogue': str(args.output_dir/'catalogue.json'),
                              'state_spec': str(args.output_dir/'state_spec.json'), 'checks': len(catalogue['checks']),
                              'workflows': len(plan['acceptance_workflows']), 'review': catalogue['review']}))
            return 0
        if args.check_results:
            from multimodalcode.vsv_eval.evaluation import finalize_evaluation
            result=finalize_evaluation(args.check_results,args.output_dir,args.repair_results)
            print(json.dumps({'scores':str(args.output_dir/'scores.json'),'status':result['status'],
                              'metrics':{key:row['score'] for key,row in result['metrics'].items()}}))
            return 0
        if not args.task_root and (args.draft_catalogue or not args.state_spec):
            raise ValueError("--task-root is required for catalogue drafting and check evaluation")
        if args.extract_only or args.test_only or args.visual_only or args.audit:
            raise ValueError("Protocol evaluation does not use legacy scoring flags")
        if args.draft_catalogue:
            if args.offline:
                raise ValueError("Catalogue drafting requires a judge")
            from multimodalcode.vsv_eval.catalogue import draft_catalogue, CATALOGUE_SCHEMA
            config = load_judge_config(args.config)
            judge = build_client(config, args.primary_profile or config['primary_stage_profiles']['catalogue'], args.output_dir / 'judge_cache/catalogue', response_schema=CATALOGUE_SCHEMA)
            result = draft_catalogue(args.task_root, judge, args.output_dir)
            print(json.dumps({'catalogue': str(args.output_dir / 'catalogue.json'), 'checks': len(result['checks']), 'review': result['review']}))
            return 0
        if not args.catalogue:
            raise ValueError("--catalogue is required for protocol evaluation")
        if args.text_proxy:
            from multimodalcode.vsv_eval.text_proxy import evaluate_text_proxy, metric_display
            result = evaluate_text_proxy(args.rounds_json, args.catalogue, args.task_root, args.config, args.output_dir,
                                         primary_profile=args.primary_profile, allow_draft=args.allow_draft,
                                         max_chars=args.proxy_max_input_chars, workers=args.workers, reuse_checks=args.reuse_checks)
            print(json.dumps({'scores': str(args.output_dir/'scores.json'), 'status': result['status'],
                              'metrics': {k: metric_display(k, v) for k, v in result['metrics'].items()}}))
            return 0
        if args.state_spec:
            from multimodalcode.vsv_eval.states import evaluate_states
            result = evaluate_states(args.rounds_json, args.catalogue, args.state_spec, args.config, args.output_dir,
                                     primary_profile=args.primary_profile, allow_draft=args.allow_draft, offline=args.offline,
                                     port=args.port, workers=args.workers)
            print(json.dumps({'repair_results': str(args.output_dir / 'repair_results.json'),
                              'versions': len(result['artifact_identities']), 'transitions': len(result['repairs'])}))
            return 0
        from multimodalcode.vsv_eval.evaluation import evaluate_rounds
        from multimodalcode.vsv_eval.states import load_repair_results
        repairs = load_repair_results(args.repair_results, args.rounds_json, args.catalogue) if args.repair_results else []
        result = evaluate_rounds(args.rounds_json, args.catalogue, args.task_root, args.config, args.output_dir,
                                primary_profile=args.primary_profile, allow_draft=args.allow_draft, offline=args.offline,
                                reference_map=args.reference_map, assertions=read_json(args.assertions) if args.assertions else [], repairs=repairs,
                                workers=args.workers, reuse_checks=args.reuse_checks, reconstructed_index=args.reconstructed_images)
        print(json.dumps({'scores': str(args.output_dir / 'scores.json'), 'status': result['status'],
                          'metrics': {key: row['score'] for key, row in result['metrics'].items()}}))
        return 0
    if not args.run_json:
        raise ValueError("--run-json is required for extraction and historical stage tools")
    if not (args.extract_only or args.test_only or args.visual_only):
        raise ValueError("For six-metric evaluation use --rounds-json and --catalogue; historical stages require --test-only or --visual-only")
    if args.visual_only and args.test_only:
        raise ValueError("Choose --test-only or --visual-only, not both")
    if args.judgment_ordinals and not args.visual_only:
        raise ValueError("--judgment-ordinals requires --visual-only")
    if args.test_visual_profile:
        print("Note: --test-visual-profile is ignored; Test does not judge page appearance.", file=sys.stderr)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.extract_only:
        windows = extract_candidate_windows(args.run_json)
        run = read_json(args.run_json)
        result = {
            "schema": "multimodalcode-verification-windows-1",
            "source_run": str(args.run_json.resolve()),
            **{key: run.get(key) for key in ("case_id", "model", "framework", "mode", "raw_links")},
            "development_root": (run.get("development") or {}).get("root"),
            "versions": (run.get("result") or {}).get("versions"),
            "workspace_artifact": (run.get("result") or {}).get("workspace_artifact"),
            "filter_status": "rule_candidates_only",
            "candidate_count": len(windows),
            "window_count": len(windows),
            "keep_ids": None,
            "windows": windows,
        }
        output = args.output_dir / "candidate_windows.json"
        write_json(output, result)
        summary = {"candidates": len(windows), "retained": None}
        if not args.offline:
            config = load_judge_config(args.config)
            profile = args.primary_profile or config["primary_stage_profiles"]["verification_annotation"]
            judge = build_client(config, profile, args.output_dir / "judge_cache/verification_annotation")
            result = extract_verification_rounds(args.run_json, judge, max_input_chars=args.verification_max_input_chars)
            output = args.output_dir / "verification_rounds.json"
            write_json(output, result)
            write_json(args.output_dir / "model_annotations.json", result["annotation"])
            for filename, subset, use in (
                ("visual_verification_rounds.json", [e for e in result["episodes"] if e["verification_kind"] == "visual"], "visual_scoring_input"),
                ("other_verification_rounds.json", [e for e in result["episodes"] if e["verification_kind"] != "visual"], "text_scoring_input"),
            ):
                write_json(args.output_dir / filename, {
                    **result, "episodes": subset, **verification_round_summary(subset),
                    "source_rounds": str(output.resolve()), "intended_use": use,
                })
            summary.update(retained=sum(len(e["candidate_ids"]) for e in result["episodes"]), episodes=result["episode_count"],
                           image_episodes=result["image_episode_count"], image_inputs=result["image_input_count"],
                           visual_attempts_without_image=result["visual_attempt_without_image_count"],
                           non_visual_episodes=result["non_visual_episode_count"],
                           annotation_batches=len(result["annotation"]["batches"]))
        print(json.dumps({**summary, "status": result.get("annotation_status", result.get("filter_status")), "output": str(output)}))
        return 0

    from multimodalcode.vsv_eval.scoring import score_trajectory

    extracted = extract_episodes(args.run_json)
    task_root = (args.task_root or infer_task_root(args.run_json, extracted["case_id"])).resolve()
    if args.visual_only:
        from multimodalcode.vsv_eval.visual_judgment import score_visual_trajectory
        if args.audit:
            raise ValueError("--visual-only audit is not implemented; run a separate frozen judge profile")
        client = None
        if not args.offline:
            config = load_judge_config(args.config)
            client = build_client(config, args.primary_profile or config["primary_stage_profiles"]["visual_judgment"], args.output_dir / "judge_cache")
        result = score_visual_trajectory(args.run_json, task_root, args.output_dir, client,
                                         args.episode_id, args.judgment_ordinals, args.max_episode_images)
        print(json.dumps({"summary": result["summary"], "scores": str(args.output_dir / "scores.json"), "html": str(args.output_dir / "index.html")}, ensure_ascii=False))
        return 0
    primary = None
    audit = None
    if not args.offline:
        config = load_judge_config(args.config)
        if args.test_only:
            config = dict(config)
            config["primary_stage_profiles"] = {
                key: (args.primary_profile if key == "workflow_alignment" and args.primary_profile else value)
                for key, value in config["primary_stage_profiles"].items()
                if key == "workflow_alignment"
            }
        primary = build_stage_clients(
            config,
            None if args.test_only else args.primary_profile,
            args.output_dir / "judge_cache/primary",
            "primary_stage_profiles",
        )
        if args.audit:
            if args.audit_profile:
                audit_profile = args.audit_profile
            elif "claude-opus-4-8" in str(extracted.get("model") or "").casefold():
                audit_profile = config["audit_for_claude_opus_profile"]
            else:
                audit_profile = config["audit_default_profile"]
            audit = {
                stage: build_client(
                    config,
                    audit_profile,
                    args.output_dir / "judge_cache/audit" / stage,
                )
                for stage in (("workflow_alignment",) if args.test_only else ("workflow_alignment", "visual_judgment"))
            }

    result = score_trajectory(
        args.run_json,
        task_root,
        args.output_dir,
        primary=primary,
        audit=audit,
        replay_results=args.replay_results,
        max_episode_images=args.max_episode_images,
        test_only=args.test_only,
        episode_id=args.episode_id,
    )
    print(json.dumps({
        "case_id": result["case_id"],
        "episodes": len(result["episodes"]),
        "judge_enabled": result["judge_enabled"],
        "scores": str(args.output_dir / "scores.json"),
        "html": str(args.output_dir / "index.html"),
    }, ensure_ascii=False))
    return 0


def main() -> int:
    args = build_parser().parse_args()
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        return run(args)
    finally:
        report = write_model_call_report(args.output_dir, since=started_at)
        print(json.dumps({'model_calls': str(args.output_dir / 'model_calls.json'),
                          **{k: report[k] for k in ('api_requests', 'cache_hits', 'error_calls')}},
                         ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
