#!/usr/bin/env python3
"""Extract checkpoint verification questions, or probe the original launch in a fresh CPU container."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from multimodalcode.vsv_eval.checkpoint_tasks import build_tasks, validate_runtime, run_verification_handoff


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="No model calls or application execution")
    build.add_argument("--run", type=Path, required=True, help="Normalized run.json with timeline/development")
    build.add_argument("--output", type=Path, required=True, help="Must not already exist")
    build.add_argument("--action", type=int, action="append", help="Optional original check action ordinal; repeatable")
    build.add_argument("--sources-config", type=Path, help="Pinned, explicitly reviewed application snapshots and recorded launch")
    validate = commands.add_parser("validate", help="Fresh Vision2Web CPU container only; /workspace must be empty")
    validate.add_argument("--sample", type=Path, required=True)
    validate.add_argument("--output", type=Path, required=True, help="Fresh path for offline-only startup evidence")
    validate.add_argument("--timeout", type=float, default=180)
    handoff = commands.add_parser("handoff", help="Optional analysis: one fresh verifier session in a task container")
    handoff.add_argument("--sample", type=Path, required=True)
    handoff.add_argument("--runtime-check", type=Path, required=True)
    handoff.add_argument("--output", type=Path, required=True)
    handoff.add_argument("--framework", choices=("claude_code", "openhands"), required=True)
    handoff.add_argument("--model", required=True)
    handoff.add_argument("--base-url", required=True)
    handoff.add_argument("--api-key-env", default="LLM_API_KEY", help="Environment variable name, never a literal credential")
    handoff.add_argument("--timeout", type=int, required=True, help="Finite wall-clock budget in seconds; no retries")
    args = parser.parse_args()
    if args.command == "build":
        result = build_tasks(args.run, args.output, action_ordinals=set(args.action) if args.action else None,
                             sources_config=args.sources_config)
        print(json.dumps({k: v for k, v in result.items() if k not in {"samples", "excluded"}}, ensure_ascii=False))
        return 0
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    if args.command == "handoff":
        key = os.environ.get(args.api_key_env)
        if not key:
            parser.error(f"Set the credential environment variable {args.api_key_env}")
        result = run_verification_handoff(args.sample, args.runtime_check, args.output,
            framework=args.framework, model=args.model, base_url=args.base_url, api_key=key, timeout=args.timeout)
        print(json.dumps({"status": result["status"], "output": str(args.output)}))
        return 0 if result["status"] == "success" else 1
    result = validate_runtime(args.sample.resolve(), args.output.resolve(), timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
