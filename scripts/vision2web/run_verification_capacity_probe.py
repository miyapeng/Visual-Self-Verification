#!/usr/bin/env python3
"""Run one fresh-context, verification-only OpenHands capacity probe."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import replace
from pathlib import Path

from multimodalcode.agent_harness.cases import load_case
from multimodalcode.agent_harness.openhands_runner import run_openhands
from multimodalcode.agent_harness.trajectory import normalize_openhands
from multimodalcode.agent_harness.vision2web_trace import manifest_hash, program_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = (
    PROJECT_ROOT / "prompts/vision2web/fresh_context_verification_probe.txt"
)


def prepare_workspace(case_id: str, program: Path, workspace: Path) -> None:
    if workspace.exists() and any(workspace.iterdir()):
        raise RuntimeError(f"Refusing to overwrite non-empty workspace: {workspace}")
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree(program, workspace, dirs_exist_ok=True)
    case = load_case("vision2web", case_id)
    assert case.source_dir is not None
    shutil.copytree(case.source_dir / "prototypes", workspace / "prototypes")
    resources = case.source_dir / "resources"
    if resources.is_dir():
        shutil.copytree(resources, workspace / "resources")
    if case.task_type == "frontend":
        shutil.copy2(case.source_dir / "prompt.txt", workspace / "prompt.txt")
    elif case.task_type == "website":
        shutil.copy2(case.source_dir / "prd.md", workspace / "prd.md")
    if (workspace / "workflow.json").exists():
        raise RuntimeError("Private workflow leaked into probe workspace")


def count_browser_events(trace_root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    path = trace_root / "browser.events.jsonl"
    if not path.is_file():
        return counts
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = str(event.get("type") or "unknown")
        counts[name] = counts.get(name, 0) + 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id")
    parser.add_argument("--program", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", default="EMPTY")
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--openhands-root", type=Path, default=PROJECT_ROOT / "scaffolds/openhands")
    parser.add_argument("--python", default="python3.12")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    workspace = args.workspace.resolve()
    prepare_workspace(args.case_id, args.program.resolve(), workspace)
    initial_manifest = program_manifest(workspace)
    initial_hash = manifest_hash(initial_manifest)
    if args.prepare_only:
        print(json.dumps({"workspace": str(workspace), "program_sha256": initial_hash}))
        return 0

    base_case = load_case("vision2web", args.case_id)
    probe_case = replace(
        base_case,
        prompt=args.prompt.read_text(encoding="utf-8").strip(),
        metadata={"probe": "fresh_context_verify_only"},
    )
    raw = output / "openhands.events.jsonl"
    outcome = run_openhands(
        probe_case,
        workspace,
        raw,
        output / "openhands.stderr.log",
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=args.timeout,
        runtime_root=args.openhands_root,
        profile="official",
        vision2web_mode="browser_enabled",
        max_retries=0,
        python_executable=args.python,
    )
    normalize_openhands(raw, output / "trajectory.json", probe_case.to_dict())
    final_hash = manifest_hash(program_manifest(workspace))
    raw_text = raw.read_text(encoding="utf-8", errors="replace")
    trace_root = Path(outcome["development_trace"])
    summary = {
        "schema": "multimodalcode-vision2web-verification-capacity-result-1",
        "case_id": args.case_id,
        "status": outcome["status"],
        "fresh_context": True,
        "repair_allowed": False,
        "program_sha256_before": initial_hash,
        "program_sha256_after": final_hash,
        "workspace_unchanged": initial_hash == final_hash,
        "workflow_json_present_in_workspace": (workspace / "workflow.json").exists(),
        "workflow_json_mentioned_in_trajectory": "workflow.json" in raw_text,
        "browser_events": count_browser_events(trace_root),
        "trajectory": str(raw),
        "development_trace": str(trace_root),
        "outcome": outcome,
    }
    (output / "probe-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if outcome["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
