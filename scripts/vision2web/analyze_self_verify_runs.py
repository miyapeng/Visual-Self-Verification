#!/usr/bin/env python3
"""Summarize observable self-verification behavior without semantic judging.

Requirement relevance is intentionally left for blinded/manual annotation: this
script reports the authored expectations and concrete browser actions, but does
not turn a string-overlap heuristic into a scientific result.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return rows
    for line in lines:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def stamp(value: Any) -> datetime:
    text = str(value or "9999-12-31T23:59:59+00:00")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_application_url(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or ""))
        return (
            parsed.scheme in {"http", "https"}
            and (parsed.hostname or "").casefold()
            in {"localhost", "127.0.0.1", "::1"}
            and parsed.port == 3000
        )
    except ValueError:
        return False


def summarize_run(result_path: Path) -> dict[str, Any]:
    result = read_json(result_path, {})
    audit = read_json(result_path.parent / "trajectory_audit.json", {})
    trace = Path(result.get("development_trace", ""))
    browser = read_jsonl(trace / "browser.events.jsonl") if trace else []
    workspace = read_jsonl(trace / "workspace.events.jsonl") if trace else []
    timeline = read_jsonl(trace / "development_timeline.jsonl") if trace else []
    versions = read_json(trace / "versions.json", {}) if trace else {}

    first = versions.get("P_first") or {}
    final = versions.get("P_final") or {}
    first_observation = (
        stamp(first.get("captured_at_utc")) if first.get("captured_at_utc") else None
    )
    current_checkpoint = versions.get("schema") == "multimodalcode-vision2web-version-pair-2"
    started_at = stamp(result.get("started_at")) if result.get("started_at") else None
    ended_at = stamp(result.get("ended_at")) if result.get("ended_at") else None

    def after_first_observation(row: dict[str, Any]) -> bool:
        return bool(first_observation and stamp(row.get("timestamp")) >= first_observation)

    prepared_rows = [
        row
        for row in browser
        if row.get("type") == "model_visual_observation_prepared"
        and is_application_url((row.get("payload") or {}).get("url"))
    ]
    claude_png_rows = [
        row
        for row in browser
        if row.get("type") == "claude_png_entered_model_context"
        and is_application_url((row.get("payload") or {}).get("current_url"))
    ]
    claude_result_rows = [
        row
        for row in browser
        if row.get("type") == "claude_browser_command_result"
        and is_application_url((row.get("payload") or {}).get("current_url"))
        and not (row.get("payload") or {}).get("is_error")
    ]
    app_execution_ids = {
        str((row.get("payload") or {}).get("execution_id"))
        for row in prepared_rows
        if (row.get("payload") or {}).get("tool") == "browser_execute_plan"
    }

    all_plans = [row for row in browser if row.get("type") == "browser_plan"]
    plans = [
        row
        for row in all_plans
        if str((row.get("payload") or {}).get("execution_id")) in app_execution_ids
        or (not current_checkpoint and after_first_observation(row))
    ]
    # Legacy snapshot rows are preserved for old exploratory trajectories but
    # are not produced by the active native-browser-use-plan interface.
    snapshot_rows = [
        row
        for row in browser
        if row.get("type") == "snapshot_observation" and after_first_observation(row)
    ]
    action_rows = [
        row
        for row in browser
        if row.get("type") == "browser_action"
        and (
            str((row.get("payload") or {}).get("execution_id")) in app_execution_ids
            or (not current_checkpoint and after_first_observation(row))
        )
    ]
    observation_rows = [
        row
        for row in browser
        if row.get("type") == "browser_observation"
        and (
            str((row.get("payload") or {}).get("execution_id")) in app_execution_ids
            or (not current_checkpoint and after_first_observation(row))
        )
    ]
    browser_tool_calls = [
        row for row in timeline if row.get("type") == "browser_tool_call"
    ]
    preobservation_browser_tool_calls = [
        row
        for row in browser_tool_calls
        if first_observation and stamp(row.get("timestamp")) < first_observation
    ]
    postobservation_browser_tool_calls = [
        row for row in browser_tool_calls if after_first_observation(row)
    ]
    native_state_calls = [
        row
        for row in postobservation_browser_tool_calls
        if (row.get("payload") or {}).get("tool") == "browser_get_state"
    ]
    native_state_observations = [
        row
        for row in timeline
        if row.get("type") == "agent_observation"
        and after_first_observation(row)
        and (row.get("payload") or {}).get("tool") == "browser_get_state"
    ]
    visual_state_observations = [
        row
        for row in native_state_observations
        if ((row.get("payload") or {}).get("observation") or {}).get(
            "screenshot_data"
        )
    ]
    native_navigation_calls = [
        row
        for row in postobservation_browser_tool_calls
        if (row.get("payload") or {}).get("tool") == "browser_navigate"
    ]
    native_action_tools = {
        "browser_click",
        "browser_type",
        "browser_scroll",
        "browser_go_back",
        "browser_set_storage",
    }
    native_action_calls = [
        row
        for row in postobservation_browser_tool_calls
        if (row.get("payload") or {}).get("tool") in native_action_tools
    ]
    claude_playwright_calls = [
        row
        for row in postobservation_browser_tool_calls
        if (row.get("payload") or {}).get("tool") == "Bash"
        and "playwright-cli"
        in str(((row.get("payload") or {}).get("action") or {}).get("command", ""))
    ]

    def claude_browser_command(row: dict[str, Any]) -> str:
        return str(((row.get("payload") or {}).get("action") or {}).get("command", ""))

    claude_inspection_calls = [
        row
        for row in claude_playwright_calls
        if re.search(r"(?:^|\s)(?:[^\s;&|]*/)?playwright-cli\s+(?:open|snapshot|screenshot)\b", claude_browser_command(row))
    ]
    claude_action_calls = [
        row
        for row in claude_playwright_calls
        if re.search(
            r"(?:^|\s)(?:[^\s;&|]*/)?playwright-cli\s+(?:click|fill|select|check|uncheck|press|go-back|reload|hover)\b",
            claude_browser_command(row),
        )
    ]
    claude_evidence_calls = [
        row
        for row in claude_playwright_calls
        if re.search(
            r"(?:^|\s)(?:[^\s;&|]*/)?playwright-cli\s+(?:snapshot|screenshot)\b",
            claude_browser_command(row),
        )
    ]
    browser_evidence = (
        prepared_rows
        + claude_png_rows
        + claude_result_rows
        + snapshot_rows
        + observation_rows
        + native_state_calls
        + claude_inspection_calls
    )
    browser_times = sorted(stamp(row.get("timestamp")) for row in browser_evidence)

    change_rows = [row for row in workspace if row.get("type") == "workspace_change"]
    change_times = sorted(stamp(row.get("timestamp")) for row in change_rows)
    first_browser = browser_times[0] if browser_times else None
    post_browser_edits = [value for value in change_times if first_browser and value > first_browser]
    first_post_browser_edit = post_browser_edits[0] if post_browser_edits else None
    rechecks = [value for value in browser_times if first_post_browser_edit and value > first_post_browser_edit]

    deploy_after_evidence = False
    if first_browser:
        deploy_after_evidence = any(
            row.get("type") == "deploy_action"
            and stamp(row.get("timestamp")) > first_browser
            for row in timeline
        )

    scenario_evidence: list[dict[str, Any]] = []
    for row in plans:
        plan = (row.get("payload") or {}).get("plan") or {}
        for scenario in plan.get("scenarios", []):
            scenario_evidence.append(
                {
                    "name": scenario.get("name"),
                    "expectation": scenario.get("expectation"),
                    "reset": scenario.get("reset"),
                    "actions": scenario.get("actions", []),
                }
            )

    first_hash = first.get("program_sha256")
    final_hash = final.get("program_sha256")
    return {
        "case_id": result.get("case_id"),
        "framework": result.get("vision2web_framework") or "openhands",
        "mode": result.get("vision2web_mode"),
        "browser_interface_revision": result.get("browser_interface_revision"),
        "status": result.get("status"),
        "trajectory_audit_present": bool(audit),
        "experiment_contaminated": bool(audit.get("experiment_contaminated", False)),
        "eligible_for_controlled_experiment": bool(
            audit and audit.get("eligible_for_controlled_experiment", False)
        ),
        "forbidden_evaluator_access": audit.get("forbidden_evaluator_access", {}),
        "duration_seconds": result.get("duration_seconds"),
        "checkpoint_schema": versions.get("schema"),
        "checkpoint_semantics_current": current_checkpoint,
        "time_to_first_application_observation_seconds": (
            (first_observation - started_at).total_seconds()
            if first_observation and started_at
            else None
        ),
        "post_first_application_observation_duration_seconds": (
            (ended_at - first_observation).total_seconds()
            if ended_at and first_observation
            else None
        ),
        "first_application_observed": bool(first) if current_checkpoint else False,
        "legacy_reachability_checkpoint_present": bool(first) if not current_checkpoint else False,
        "P_first_sha256": first_hash,
        "P_final_sha256": final_hash,
        "program_changed_after_first_observation": bool(
            first_hash and final_hash and first_hash != final_hash
        ),
        "actively_opened_or_inspected": bool(
            browser_evidence or native_navigation_calls or claude_playwright_calls
        ),
        "preobservation_browser_tool_calls": len(preobservation_browser_tool_calls),
        "postobservation_browser_tool_calls": len(postobservation_browser_tool_calls),
        "browser_get_state_calls": len(native_state_calls),
        "browser_get_state_observations": len(native_state_observations),
        "playwright_cli_calls": len(claude_playwright_calls),
        "playwright_cli_inspection_calls": len(claude_inspection_calls),
        "visual_screenshot_observations": len(visual_state_observations),
        "inline_openhands_images": sum(
            bool((row.get("payload") or {}).get("inline_image_content"))
            for row in prepared_rows
        ),
        "claude_png_context_images": len(claude_png_rows),
        "received_postdeployment_visual_evidence": bool(
            prepared_rows
            or claude_png_rows
            or visual_state_observations
            or snapshot_rows
            or observation_rows
            or claude_evidence_calls
        ),
        "browser_execute_plan_calls": len(plans),
        "browser_action_count": (
            len(action_rows) + len(native_action_calls) + len(claude_action_calls)
        ),
        "legacy_browser_snapshot_calls": len(snapshot_rows),
        "authored_scenarios": scenario_evidence,
        "interaction_relevance": None,
        "interaction_relevance_note": (
            "Requires task-conditioned manual annotation; no benchmark workflow was "
            "used and no automatic semantic judge is applied."
        ),
        "edited_after_visual_or_runtime_evidence": bool(post_browser_edits),
        "deployed_after_visual_or_runtime_evidence": deploy_after_evidence,
        "rechecked_after_post_evidence_edit": bool(rechecks),
        "development_trace": str(trace) if trace else None,
        "result_path": str(result_path),
    }


def markdown(rows: list[dict[str, Any]], aggregates: dict[str, Any]) -> str:
    lines = [
        "# Vision2Web self-verification run summary",
        "",
        "This report is computed only from agent-visible trajectories and passive "
        "workspace/browser instrumentation. `workflow.json` is not read. Interaction "
        "relevance remains a manual annotation rather than an inferred score.",
        "",
        "| mode | case | status | eligible | inspect | actions | evidence→edit | edit→recheck | P_first≠P_final |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        yes = lambda value: "yes" if value else "no"  # noqa: E731
        lines.append(
            "| {mode} | {case} | {status} | {eligible} | {inspect} | {actions} | {repair} | "
            "{recheck} | {changed} |".format(
                mode=row.get("mode"),
                case=row.get("case_id"),
                status=row.get("status"),
                eligible=yes(row["eligible_for_controlled_experiment"]),
                inspect=yes(row["actively_opened_or_inspected"]),
                actions=row["browser_action_count"],
                repair=yes(row["edited_after_visual_or_runtime_evidence"]),
                recheck=yes(row["rechecked_after_post_evidence_edit"]),
                changed=yes(row["program_changed_after_first_observation"]),
            )
        )
    lines.extend(["", "## Mode totals", "", "```json", json.dumps(aggregates, indent=2), "```", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result_paths = sorted(args.run_root.rglob("result.json"))
    rows = [summarize_run(path) for path in result_paths]
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        mode = str(row.get("mode") or "unknown")
        grouped[mode]["all_preserved_runs"] += 1
        if not row["eligible_for_controlled_experiment"]:
            grouped[mode]["excluded_unvalidated_or_contaminated_runs"] += 1
            continue
        grouped[mode]["eligible_runs"] += 1
        grouped[mode]["successful_runs"] += int(row.get("status") == "success")
        for source, target in (
            ("actively_opened_or_inspected", "inspected"),
            ("browser_action_count", "browser_actions"),
            ("edited_after_visual_or_runtime_evidence", "evidence_to_edit"),
            ("rechecked_after_post_evidence_edit", "edit_to_recheck"),
        ):
            grouped[mode][target] += int(bool(row.get(source))) if source != "browser_action_count" else int(row.get(source, 0))
    payload = {
        "schema": "multimodalcode-vision2web-self-verify-analysis-2",
        "run_root": str(args.run_root.resolve()),
        "workflow_json_read": False,
        "semantic_relevance_automatically_judged": False,
        "aggregates": {key: dict(value) for key, value in grouped.items()},
        "runs": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(
        markdown(rows, payload["aggregates"]), encoding="utf-8"
    )
    print(json.dumps(payload["aggregates"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
