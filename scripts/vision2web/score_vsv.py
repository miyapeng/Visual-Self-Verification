#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from multimodalcode.io import write_json  # noqa: E402
from multimodalcode.vsv_eval.episodes import extract_episodes  # noqa: E402
from multimodalcode.vsv_eval.judge import (  # noqa: E402
    build_client,
    load_judge_config,
)
from multimodalcode.vsv_eval.scoring import score_trajectory  # noqa: E402


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
    parser = argparse.ArgumentParser(description="Offline Vision2Web VSV scoring")
    parser.add_argument("--run-json", type=Path, required=True)
    parser.add_argument("--task-root", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs/vision2web/vsv_scoring.json",
    )
    parser.add_argument("--offline", action="store_true", help="Parse and score deterministic evidence without model calls")
    parser.add_argument("--audit", action="store_true", help="Run the configured independent closed-model audit")
    parser.add_argument("--primary-profile")
    parser.add_argument("--test-visual-profile", help="Deprecated, ignored: Test now judges method relevance from text only")
    parser.add_argument("--audit-profile")
    parser.add_argument("--replay-results", type=Path)
    parser.add_argument("--max-episode-images", type=int, default=8)
    parser.add_argument("--extract-only", action="store_true")
    parser.add_argument("--test-only", action="store_true", help="Evaluate recorded execution and check reasonableness only; no replay")
    parser.add_argument("--visual-only", action="store_true", help="Evaluate time-bounded visual judgments only; no Test/workflow/replay")
    parser.add_argument("--judgment-ordinals", nargs="+", type=int, help="Visual-only smoke subset; still require automatic location")
    parser.add_argument("--episode-id", help="Score only this extracted episode; preserve the original trajectory")
    return parser


def infer_task_root(run_json: Path, case_id: str) -> Path:
    return PROJECT_ROOT / "data/vision2web/extracted" / case_id


def main() -> int:
    args = build_parser().parse_args()
    if args.visual_only and args.test_only:
        raise ValueError("Choose --test-only or --visual-only, not both")
    if args.judgment_ordinals and not args.visual_only:
        raise ValueError("--judgment-ordinals requires --visual-only")
    if args.test_visual_profile:
        print("Note: --test-visual-profile is ignored; Test does not judge page appearance.", file=sys.stderr)
    extracted = extract_episodes(args.run_json)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.extract_only:
        write_json(args.output_dir / "episodes.json", extracted)
        print(json.dumps({"episodes": extracted["episode_count"], "output": str(args.output_dir / 'episodes.json')}))
        return 0

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


if __name__ == "__main__":
    raise SystemExit(main())
