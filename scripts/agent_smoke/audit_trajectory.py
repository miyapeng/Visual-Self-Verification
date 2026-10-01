#!/usr/bin/env python3
"""Produce a compact, model-independent sanity report for one agent run."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


PATTERNS = {
    "inspect": re.compile(
        r"\b(?:ls|find|rg|grep|sed|cat|head|tail)\b|\bgit\s+(?:status|diff|log|show)\b"
    ),
    "edit": re.compile(r"\b(?:apply_patch|python|perl|sed)\b|(?:>|>>)"),
    "execute_or_test": re.compile(
        r"\b(?:pytest|npm test|yarn test|pnpm test|mocha|jest|node|python|bash start\.sh|curl)\b"
    ),
    "visual_feedback": re.compile(
        r"visual_tool\.py|render-html|view-image|playwright|file_editor[^\n]*(?:view|\.(?:png|jpe?g|webp))",
        re.I,
    ),
    "submit": re.compile(r"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"),
}

RISK_PATTERNS = {
    "possible_oracle_access": re.compile(r"(?:gold[_-]?patch|evaluator_private|instances\.full|oracle)", re.I),
    # Looking through commits after the benchmark base revision can reveal
    # the original upstream fix even without network access.
    "possible_history_oracle": re.compile(r"\bgit\s+(?:log|show)\b", re.I),
    "possible_network_access": re.compile(r"\b(?:wget|git clone|curl\s+https?://)", re.I),
    "possible_test_edit": re.compile(
        r"(?:apply_patch|>|>>)\s*[^\n]*(?:^|/)(?:tests?|__tests__)/", re.I | re.M
    ),
}

# Vision2Web generation runs execute before official scoring. Any agent-emitted
# command or tool request that reaches the private workflow, frozen dataset
# source tree, or evaluator implementation invalidates that trajectory for the
# controlled experiment.  ClusterX mounts /data for artifact persistence, so
# this post-hoc guard complements (but does not pretend to replace) the clean
# /workspace staging boundary.
FORBIDDEN_VISION2WEB_PATTERNS = {
    "workflow": re.compile(r"\bworkflow\.json\b", re.I),
    "frozen_dataset_source": re.compile(
        r"/data/miyapeng/mmcode/MultimodalCode/data/vision2web(?:/|\b)", re.I
    ),
    "official_evaluator_source": re.compile(
        r"(?:/data/miyapeng/mmcode/MultimodalCode/)?evaluate/vision2web(?:/|\b)"
        r"|\b(?:run_official|run_clusterx)\.py\b",
        re.I,
    ),
    "upstream_evaluation_source": re.compile(
        r"(?:^|[\s'\"])(?:\.?\.?/)*(?:Vision2Web|vision2web)/(?:evaluation|eval)(?:/|\b)",
        re.I | re.M,
    ),
}


def _matched_fragments(pattern: re.Pattern[str], text: str) -> list[str]:
    """Return stable, compact evidence without copying whole model commands."""
    values: list[str] = []
    for match in pattern.finditer(text):
        value = match.group(0).strip()
        if value and value not in values:
            values.append(value[:240])
    return values[:10]


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} RUN_DIRECTORY", file=sys.stderr)
        return 2
    run_dir = Path(sys.argv[1]).resolve()
    trajectory = run_dir / "trajectory.json"
    result = run_dir / "result.json"
    if not trajectory.is_file():
        raise FileNotFoundError(trajectory)
    data = json.loads(trajectory.read_text(encoding="utf-8"))
    events = data.get("events", [])
    # Only classify commands actually emitted by the assistant. The task
    # prompt itself names submission commands and prohibited actions, which
    # would otherwise create false positives.
    tools: list[str] = []
    def is_agent_action(event: dict) -> bool:
        if event.get("actor") not in {"assistant", "agent"}:
            return False
        kind = str(event.get("kind", ""))
        return bool(
            kind == "action"
            or kind.endswith("ActionEvent")
            or event.get("tool")
            or event.get("tools")
        )

    for event in events:
        if not is_agent_action(event):
            continue
        event_tools = event.get("tools")
        if isinstance(event_tools, list):
            tools.extend(str(value) for value in event_tools)
        tool = event.get("tool")
        if tool:
            tools.append(str(tool))
        text = event.get("text")
        if text:
            tools.append(str(text))
    joined = "\n".join(tools)
    categories = {name: bool(pattern.search(joined)) for name, pattern in PATTERNS.items()}
    risks = {name: bool(pattern.search(joined)) for name, pattern in RISK_PATTERNS.items()}
    forbidden_access = {
        name: _matched_fragments(pattern, joined)
        for name, pattern in FORBIDDEN_VISION2WEB_PATTERNS.items()
        if pattern.search(joined)
    }
    contaminated = data.get("benchmark") == "vision2web" and bool(forbidden_access)
    assistant_actions = sum(is_agent_action(event) for event in events)
    observation_events = sum(event.get("actor") in {"environment", "tool", "user"} for event in events)
    observation_lengths = [
        len(str(event.get("text", "")))
        for event in events
        if event.get("actor") in {"environment", "tool", "user"}
    ]
    result_data = json.loads(result.read_text(encoding="utf-8")) if result.is_file() else {}
    workspace = run_dir / "workspace"
    report = {
        "schema": "multimodalcode-trajectory-audit-2",
        "run_dir": str(run_dir),
        "benchmark": data.get("benchmark"),
        "case_id": data.get("case_id"),
        "scaffold": data.get("scaffold"),
        "event_count": len(events),
        "assistant_action_count": assistant_actions,
        "observation_event_count": observation_events,
        "categories": categories,
        "risk_indicators": risks,
        "forbidden_evaluator_access": forbidden_access,
        "experiment_contaminated": contaminated,
        "eligible_for_controlled_experiment": not contaminated,
        "has_multistep_evidence": assistant_actions >= 2,
        "max_observation_chars": max(observation_lengths, default=0),
        "large_observation_count": sum(length >= 10000 for length in observation_lengths),
        "result_status": result_data.get("status"),
        "patch_exists": (run_dir / "patch.diff").is_file(),
        "index_exists": any(
            candidate.is_file()
            for candidate in (workspace / "index.html", workspace / "static" / "index.html")
        ),
    }
    (run_dir / "trajectory_audit.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
