#!/usr/bin/env python3
"""Resume one completed Claude Code session for the neutral self-check prompt."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from multimodalcode.agent_harness.cases import load_case
from multimodalcode.agent_harness.claude_code_runner import (
    read_claude_session_id,
    run_claude_code,
)
from multimodalcode.agent_harness.trajectory import normalize_claude_code
from multimodalcode.agent_harness.vision2web_trace import copy_program


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = PROJECT_ROOT / "prompts/vision2web/same_context_self_check.txt"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id")
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--natural-trajectory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", default="EMPTY")
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--claude-executable", default="claude")
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    natural_trajectory = args.natural_trajectory.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not (workspace / "start.sh").is_file():
        raise RuntimeError("Natural session has no /workspace/start.sh to inspect")

    session_id = read_claude_session_id(natural_trajectory)
    base_case = load_case("vision2web", args.case_id)
    followup_case = replace(
        base_case,
        prompt=args.prompt.read_text(encoding="utf-8").strip(),
        metadata={"condition": "forced_same_context_self_check"},
    )
    raw = output / "claude.events.jsonl"
    outcome = run_claude_code(
        followup_case,
        workspace,
        raw,
        output / "claude.stderr.log",
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=args.timeout,
        vision2web_mode="official",
        max_retries=0,
        executable=args.claude_executable,
        resume_session_id=session_id,
    )
    normalize_claude_code(raw, output / "trajectory.json", followup_case.to_dict())
    final_version = copy_program(
        workspace,
        output / "workspace",
        metadata={
            "case_id": args.case_id,
            "condition": "forced_same_context_self_check",
            "resumed_session_id": session_id,
        },
    )
    result = {
        **outcome,
        "schema": "multimodalcode-vision2web-claude-same-context-1",
        "case_id": args.case_id,
        "condition": "forced_same_context_self_check",
        "natural_trajectory": str(natural_trajectory),
        "session_id": session_id,
        "final_version": final_version,
    }
    (output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": outcome["status"], "output": str(output)}))
    return 0 if outcome["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
