"""Derive implementation/verification rhythm from leakage-safe event logs.

This module consumes only the normalized public action stream produced by
``trajectory_observations``.  It never opens benchmark evaluator inputs.  The
distinction between an agent-active check and an environment/evaluator record
is intentionally conservative: mentioning a test or screenshot in reasoning is
not execution, and an observation without a preceding active check is not
promoted to verification.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean, median
from typing import Any, Iterable


RHYTHM_CASE_SCHEMA = "multimodalcode-implementation-rhythm-case-1"
RHYTHM_SUMMARY_SCHEMA = "multimodalcode-implementation-rhythm-summary-1"

_ACTIVE_CATEGORIES = {"executable_check", "generated_visual_check"}
_EXECUTING_TOOLS = {
    "bash",
    "browser",
    "browsergym",
    "screenshot_validated",
    "shell",
    "terminal",
    "visual_tool",
}
_WEAK_CHECK_IDENTITY_TOOLS = {
    "browser",
    "browsergym",
    "screenshot_validated",
    "visual_tool",
}


def _actions(event: dict[str, Any]) -> list[dict[str, Any]]:
    value = event.get("executed_action")
    return [item for item in (value or []) if isinstance(item, dict)]


def _categories(action: dict[str, Any]) -> set[str]:
    return {str(value) for value in action.get("categories", [])}


def _program_edit(event: dict[str, Any]) -> bool:
    return any("program_edit" in _categories(action) for action in _actions(event))


def _program_edit_units(event: dict[str, Any]) -> int:
    if not _program_edit(event):
        return 0
    edit = event.get("edit") or {}
    if "program_units" in edit:
        return max(0, int(edit.get("program_units") or 0))
    return max(1, int(edit.get("units") or 1))


def _inspection(event: dict[str, Any]) -> bool:
    return any("inspect" in _categories(action) for action in _actions(event))


def _submit(event: dict[str, Any]) -> bool:
    return any("submit" in _categories(action) for action in _actions(event))


def _active_actions(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Return checks actually invoked by the coding policy.

    The older normalized audit classified some Vision2Web ``think`` events as
    visual/tests because their prose mentioned screenshots or testing.  Requiring
    an executing tool makes this derivation safe when reading those frozen logs.
    """

    result: list[dict[str, Any]] = []
    for action in _actions(event):
        tool = str(action.get("tool", "")).lower()
        if tool not in _EXECUTING_TOOLS:
            continue
        if _ACTIVE_CATEGORIES.intersection(_categories(action)):
            result.append(action)
    return result


def _signature(action: dict[str, Any]) -> str | None:
    content = action.get("content") or {}
    digest = content.get("sha256") if isinstance(content, dict) else None
    if not digest:
        return None
    return f"{str(action.get('tool', 'unknown')).lower()}:{digest}"


def _check_group(
    event: dict[str, Any], action_offset: int
) -> tuple[dict[str, Any], int]:
    active = _active_actions(event)
    invocations = []
    for index, action in enumerate(active):
        tool = str(action.get("tool", "unknown")).lower()
        invocations.append(
            {
                "check_ordinal": action_offset + index,
                "action_index": index,
                "tool": tool,
                "categories": sorted(_ACTIVE_CATEGORIES.intersection(_categories(action))),
                "signature": _signature(action),
                "identity_strength": (
                    "weak_route_or_visual_action"
                    if tool in _WEAK_CHECK_IDENTITY_TOOLS
                    else "exact_action_digest"
                ),
                "source_ref": event.get("source_ref"),
            }
        )
    return {
        "sequence": int(event.get("sequence", 0)),
        "program_version": (event.get("program_version") or {}).get("id"),
        "invocations": invocations,
        "observation_sequence": None,
        "observation_status": None,
        "observation_source_ref": None,
    }, action_offset + len(invocations)


def _implementation_blocks(
    benchmark: str, events: list[dict[str, Any]], first_verify_sequence: int | None
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def touch(sequence: int, source_ref: str | None) -> None:
        nonlocal current
        if current is None:
            current = {
                "start_sequence": sequence,
                "end_sequence": sequence,
                "edit_events": 0,
                "edit_units": 0,
                "source_refs": [],
                "closed_by": None,
            }
        current["end_sequence"] = sequence
        if source_ref and len(current["source_refs"]) < 8:
            current["source_refs"].append(source_ref)

    def close(reason: str) -> None:
        nonlocal current
        if current is None:
            return
        current["closed_by"] = reason
        # Pure exploration is useful context but is not called an
        # implementation block unless it contains a production edit.
        if current["edit_events"]:
            current["ordinal"] = len(blocks)
            current["before_or_at_first_verification"] = bool(
                first_verify_sequence is not None
                and current["end_sequence"] <= first_verify_sequence
            )
            blocks.append(current)
        current = None

    for event in events:
        sequence = int(event.get("sequence", 0))
        # A new FronTalk turn is a new requested revision.  There is no runtime
        # tool in that benchmark, so this is the only faithful phase boundary.
        if benchmark == "frontalk" and event.get("public_request") is not None:
            close("new_requirement_turn")

        has_edit = _program_edit(event)
        if has_edit or _inspection(event):
            touch(sequence, event.get("source_ref"))
        if has_edit and current is not None:
            current["edit_events"] += 1
            current["edit_units"] += _program_edit_units(event)

        if _active_actions(event):
            close("agent_active_verification")
        elif benchmark == "interactweb" and has_edit:
            # The released WebGen harness automatically deploys every emitted
            # Bolt artifact and returns Environment/Runtime feedback.  This is
            # a deployment boundary, but not an agent-active verification.
            close("automatic_deploy_feedback")
        elif _submit(event):
            close("submission")

    close("trajectory_end")
    return blocks


def _bind_observations(
    events: list[dict[str, Any]], check_groups: list[dict[str, Any]]
) -> None:
    by_sequence = {group["sequence"]: group for group in check_groups}
    pending: dict[str, Any] | None = None
    for event in events:
        sequence = int(event.get("sequence", 0))
        if sequence in by_sequence:
            pending = by_sequence[sequence]
        observation = event.get("observation")
        if not isinstance(observation, dict):
            continue
        categories = set(observation.get("verification_categories") or [])
        if pending is not None and _ACTIVE_CATEGORIES.intersection(categories):
            pending["observation_sequence"] = sequence
            pending["observation_status"] = observation.get("status")
            pending["observation_source_ref"] = event.get("source_ref")
            pending = None
        elif categories:
            # The normalized record claims a verification observation but the
            # corresponding action was non-executing prose under our stricter
            # filter.  Do not attach it to an earlier real check.
            pending = None


def _failure_repair_episodes(
    events: list[dict[str, Any]], check_groups: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    edits = [
        int(event.get("sequence", 0)) for event in events if _program_edit(event)
    ]
    episodes: list[dict[str, Any]] = []
    for failed in check_groups:
        if failed.get("observation_status") != "concrete_failure":
            continue
        failure_sequence = int(failed.get("observation_sequence") or failed["sequence"])
        edit_sequence = next((seq for seq in edits if seq > failure_sequence), None)
        later_checks = [
            group
            for group in check_groups
            if edit_sequence is not None and group["sequence"] > edit_sequence
        ]
        failed_exact = {
            item["signature"]
            for item in failed["invocations"]
            if item.get("signature") and item.get("identity_strength") == "exact_action_digest"
        }
        matching = [
            group
            for group in later_checks
            if failed_exact.intersection(
                item.get("signature")
                for item in group["invocations"]
                if item.get("identity_strength") == "exact_action_digest"
            )
        ]
        matching_terminal = next(
            (
                group
                for group in matching
                if group.get("observation_status")
                in {"check_pass", "concrete_failure"}
            ),
            None,
        )
        coarse_pass = next(
            (group for group in later_checks if group.get("observation_status") == "check_pass"),
            None,
        )
        if edit_sequence is None:
            outcome = "no_code_edit_after_failure"
        elif not later_checks:
            outcome = "edited_without_reverification"
        elif not failed_exact:
            outcome = "unknown_weak_check_identity"
        elif matching_terminal is None:
            outcome = "unknown_no_terminal_same_check_replay"
        elif matching_terminal["observation_status"] == "check_pass":
            outcome = "confirmed_same_check_repair"
        else:
            outcome = "same_check_still_failing"
        episodes.append(
            {
                "failed_check_sequence": failed["sequence"],
                "failure_observation_sequence": failed.get("observation_sequence"),
                "edit_sequence": edit_sequence,
                "first_reverification_sequence": (
                    later_checks[0]["sequence"] if later_checks else None
                ),
                "same_check_replay_sequence": (
                    matching_terminal["sequence"] if matching_terminal else None
                ),
                "coarse_later_pass_sequence": (
                    coarse_pass["sequence"] if coarse_pass else None
                ),
                "outcome": outcome,
                "failed_source_refs": [
                    item.get("source_ref") for item in failed["invocations"]
                ],
            }
        )
    return episodes


def audit_case(
    events: Iterable[dict[str, Any]], case: dict[str, Any]
) -> dict[str, Any]:
    rows = sorted(list(events), key=lambda item: int(item.get("sequence", 0)))
    benchmark = str(case["benchmark"])
    check_groups: list[dict[str, Any]] = []
    action_offset = 0
    for event in rows:
        if _active_actions(event):
            group, action_offset = _check_group(event, action_offset)
            check_groups.append(group)
    _bind_observations(rows, check_groups)

    verify_sequences = [group["sequence"] for group in check_groups]
    first_verify = min(verify_sequences) if verify_sequences else None
    edit_sequences = [
        int(event.get("sequence", 0)) for event in rows if _program_edit(event)
    ]
    total_edit_units = sum(_program_edit_units(event) for event in rows)
    edit_units_before = sum(
        _program_edit_units(event)
        for event in rows
        if _program_edit(event)
        and first_verify is not None
        and int(event.get("sequence", 0)) <= first_verify
    )
    blocks = _implementation_blocks(benchmark, rows, first_verify)
    blocks_before = (
        sum(block["before_or_at_first_verification"] for block in blocks)
        if first_verify is not None
        else None
    )

    edit_events_after_verify = sum(
        sequence > first_verify for sequence in edit_sequences
    ) if first_verify is not None else 0
    cycle_count = 0
    seen_verify = False
    edited_since_verify = False
    for event in rows:
        has_edit = _program_edit(event)
        has_verify = bool(_active_actions(event))
        if has_edit and seen_verify:
            edited_since_verify = True
        if has_verify:
            if seen_verify and edited_since_verify:
                cycle_count += 1
            seen_verify = True
            edited_since_verify = False

    last_edit = max(edit_sequences) if edit_sequences else None
    last_verify = max(verify_sequences) if verify_sequences else None
    if not verify_sequences:
        rhythm = "no_active_verification"
    elif last_edit is None:
        rhythm = "active_verification_without_program_edit"
    elif first_verify >= last_edit:
        rhythm = "final_version_only_verification"
    elif last_verify is not None and last_verify < last_edit:
        rhythm = "verification_then_edit_without_reverify"
    elif cycle_count >= 2:
        rhythm = "multi_round_interleaved_verification"
    elif cycle_count == 1:
        rhythm = "single_interleaved_verification_cycle"
    else:
        rhythm = "interleaved_verification_other"

    external = case.get("external_outcome") or {}
    evaluator_only = bool(
        not verify_sequences
        and external.get("availability") == "available"
        and int(case.get("submit_actions") or 0) > 0
    )
    episodes = _failure_repair_episodes(rows, check_groups)
    active_failures = sum(
        group.get("observation_status") == "concrete_failure" for group in check_groups
    )
    active_passes = sum(
        group.get("observation_status") == "check_pass" for group in check_groups
    )
    active_inconclusive = sum(
        group.get("observation_status") in {None, "inconclusive", "environment_noise"}
        for group in check_groups
    )
    return {
        "schema": RHYTHM_CASE_SCHEMA,
        "benchmark": benchmark,
        "task": case["task"],
        "source_trajectory": case.get("source_trajectory"),
        "generation_status": case.get("generation_status"),
        "external_outcome": external,
        "trajectory_available": bool(case.get("trajectory_available")),
        "implementation_blocks": blocks,
        "implementation_block_count": len(blocks),
        "implementation_blocks_before_first_active_verification": blocks_before,
        "program_edit_events": len(edit_sequences),
        "program_edit_units": total_edit_units,
        "edit_fraction_before_first_active_verification": (
            edit_units_before / total_edit_units
            if first_verify is not None and total_edit_units
            else None
        ),
        "first_active_verification_sequence": first_verify,
        "active_verification_events": len(check_groups),
        "active_verification_count": sum(
            len(group["invocations"]) for group in check_groups
        ),
        "active_verification_pass_count": active_passes,
        "active_verification_failure_count": active_failures,
        "active_verification_inconclusive_or_unobserved_count": active_inconclusive,
        "program_edit_events_after_active_verification": edit_events_after_verify,
        "verify_edit_reverify_cycle_count": cycle_count,
        "failure_repair_episodes": episodes,
        "failure_repair_outcomes": dict(Counter(item["outcome"] for item in episodes)),
        "rhythm": rhythm,
        "flags": {
            "no_active_verification": not verify_sequences,
            "post_submission_evaluator_only": evaluator_only,
            "final_version_only_verification": rhythm == "final_version_only_verification",
            "verification_then_code_edit": edit_events_after_verify > 0,
            "verification_then_edit_without_reverify": (
                rhythm == "verification_then_edit_without_reverify"
            ),
            "multi_round_interleaved_verification": cycle_count >= 2,
            "active_failure_then_code_edit": any(
                item["edit_sequence"] is not None for item in episodes
            ),
        },
        "active_check_groups": check_groups,
        "interpretation": {
            "active_verification": "coding-policy invoked executable/browser action only",
            "evaluator_excluded": True,
            "interactweb_visual_judgment": "external Visual Copilot text, not same-policy judgment",
            "repair_identity": "visual/route-only actions are weak and reported unknown",
        },
    }


def _nullable_stat(values: list[float | int]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "count": len(values),
        "mean": mean(values),
        "median": median(values),
        "min": min(values),
        "max": max(values),
    }


def aggregate_rhythm(cases: Iterable[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[str(case["benchmark"])].append(case)
    result: dict[str, Any] = {}
    for benchmark, rows in sorted(grouped.items()):
        flags = Counter(
            name for row in rows for name, enabled in row["flags"].items() if enabled
        )
        rhythms = Counter(row["rhythm"] for row in rows)
        outcomes = Counter(
            outcome
            for row in rows
            for outcome, count in row["failure_repair_outcomes"].items()
            for _ in range(count)
        )
        reliable_terminal = (
            outcomes["confirmed_same_check_repair"]
            + outcomes["same_check_still_failing"]
        )
        edited_reverified = sum(
            count
            for outcome, count in outcomes.items()
            if outcome
            not in {"no_code_edit_after_failure", "edited_without_reverification"}
        )
        coarse_success = sum(
            item.get("coarse_later_pass_sequence") is not None
            for row in rows
            for item in row["failure_repair_episodes"]
            if item.get("edit_sequence") is not None
            and item.get("first_reverification_sequence") is not None
        )
        result[benchmark] = {
            "cases": len(rows),
            "trajectory_available": sum(row["trajectory_available"] for row in rows),
            "rhythm_counts": dict(sorted(rhythms.items())),
            "flag_case_counts": dict(sorted(flags.items())),
            "active_verification": {
                "cases": sum(row["active_verification_count"] > 0 for row in rows),
                "events": sum(row["active_verification_events"] for row in rows),
                "actions": sum(row["active_verification_count"] for row in rows),
                "passes": sum(row["active_verification_pass_count"] for row in rows),
                "concrete_failures": sum(
                    row["active_verification_failure_count"] for row in rows
                ),
                "inconclusive_or_unobserved": sum(
                    row["active_verification_inconclusive_or_unobserved_count"]
                    for row in rows
                ),
            },
            "implementation_blocks_before_first_active_verification": _nullable_stat(
                [
                    row["implementation_blocks_before_first_active_verification"]
                    for row in rows
                    if row["implementation_blocks_before_first_active_verification"]
                    is not None
                ]
            ),
            "edit_fraction_before_first_active_verification": _nullable_stat(
                [
                    row["edit_fraction_before_first_active_verification"]
                    for row in rows
                    if row["edit_fraction_before_first_active_verification"] is not None
                ]
            ),
            "program_edit_events_after_active_verification": sum(
                row["program_edit_events_after_active_verification"] for row in rows
            ),
            "verify_edit_reverify_cycles": sum(
                row["verify_edit_reverify_cycle_count"] for row in rows
            ),
            "failure_repair": {
                "active_failure_episodes": sum(outcomes.values()),
                "outcomes": dict(sorted(outcomes.items())),
                "confirmed_same_check_success_rate": (
                    outcomes["confirmed_same_check_repair"] / reliable_terminal
                    if reliable_terminal
                    else None
                ),
                "confirmed_same_check_rate_denominator": reliable_terminal,
                "coarse_any_later_pass_rate": (
                    coarse_success / edited_reverified if edited_reverified else None
                ),
                "coarse_rate_denominator": edited_reverified,
                "warning": (
                    "coarse rate does not prove the original failed check was repaired; "
                    "weak visual/route identities remain unknown"
                ),
            },
        }
    return result

