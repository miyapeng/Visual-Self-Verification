from __future__ import annotations

import hashlib
import json
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from multimodalcode.io import read_json, write_json

from .episodes import extract_episodes
from .checks import action_groups, episode_test_prompt, validate_episode_test, test_summary
from .checkpoints import checkpoint_index, checkpoint_workspace, workspace_patch
from .judge import JudgeClient
from .report import write_html_report
from .workflow import lexical_candidates, load_workflow
from .visual_judgment import evaluate_episode, summarize as summarize_visual


JudgeSet = JudgeClient | Mapping[str, JudgeClient] | None


def _clip(value: str, limit: int = 12000) -> str:
    return value if len(value) <= limit else value[:limit] + "\n...[truncated]"


def _select_images(paths: list[str], limit: int) -> list[str]:
    available = [path for path in paths if Path(path).is_file()]
    if len(available) <= limit:
        return available
    if limit <= 1:
        return available[-1:]
    indexes = sorted({round(index * (len(available) - 1) / (limit - 1)) for index in range(limit)})
    return [available[index] for index in indexes]


def _parsed(record: dict[str, Any] | None) -> dict[str, Any]:
    return (record or {}).get("parsed") or {}


def _safe_judge(
    client: JudgeClient | None,
    stage: str,
    prompt: str,
    images: list[str],
) -> dict[str, Any] | None:
    if client is None:
        return None
    try:
        return client.judge(stage, prompt, images)
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "schema": "multimodalcode-vsv-judge-response-1",
            "stage": stage,
            "profile": client.profile.name,
            "provider": client.profile.provider,
            "model": client.profile.model,
            "parsed": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def _stage_client(clients: JudgeSet, stage: str) -> JudgeClient | None:
    if clients is None:
        return None
    if isinstance(clients, Mapping):
        return clients.get(stage)
    return clients


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _repair_visual_prompt(
    task: str,
    episode: dict[str, Any],
    image_labels: list[str],
    replay: dict[str, Any],
) -> str:
    return f"""Determine whether the demonstrated repair fixed the target visual failure without introducing a visible regression. Images are ordered as listed.

TASK:
{_clip(task)}

ORIGINAL CHECK AND POLICY DIAGNOSIS:
{_clip(json.dumps({'actions': episode['action_sequence'], 'messages': episode['policy_messages']}, ensure_ascii=False))}

REPLAY EVIDENCE:
{_clip(json.dumps({key: value for key, value in replay.items() if key not in {'before_images','after_images'}}, ensure_ascii=False))}

IMAGE ORDER:
{json.dumps(image_labels, ensure_ascii=False, indent=2)}

Return:
{{"target_fixed":true|false|null, "visual_regression":true|false|null, "reason":"..."}}
Use null when the before/after evidence does not test the claimed problem."""


def _repair_scope_prompt(
    task: str,
    episode: dict[str, Any],
    workflow: list[dict[str, Any]],
    patch: dict[str, Any],
) -> str:
    compact_workflow = [
        {
            "workflow_id": row["workflow_id"],
            "objective": row["objective"],
            "actions": row["actions"],
            "validations": row["validations"],
        }
        for row in workflow
    ]
    return f"""Identify which previously working task behaviors could plausibly be affected by this repair patch. This selects regression checks; it does not decide whether the repair succeeded.

TASK:
{_clip(task)}

CHECK THAT TRIGGERED THE PATCH:
{_clip(json.dumps({'actions': episode['action_sequence'], 'messages': episode['policy_messages']}, ensure_ascii=False))}

PATCH:
{_clip(json.dumps(patch, ensure_ascii=False), 45000)}

WORKFLOW ITEMS:
{_clip(json.dumps(compact_workflow, ensure_ascii=False), 30000)}

Return:
{{"affected_workflow_ids":["0.0"], "reason":"..."}}
Return only IDs present above. Prefer a small defensible set; an empty set is valid when the patch is isolated."""


def _summary_prompt(episode: dict[str, Any]) -> str:
    compact = {
        "episode_id": episode["episode_id"],
        "verification_kind": episode.get("verification_kind"),
        "test": episode.get("test"),
        "visual_judgment": episode.get("visual_judgment"),
        "safe_repair": episode.get("safe_repair"),
    }
    return f"""Summarize this one self-verification episode from its already computed component evidence.

{json.dumps(compact, ensure_ascii=False, indent=2)}

Return:
{{"overall":"successful|partially_successful|unsuccessful|not_evaluable", "successes":["..."], "failures":["..."], "reason":"..."}}
Do not invent a scalar score and do not override a not-evaluable component."""


def _audit_agreement(
    primary: dict[str, Any], audit: dict[str, Any], fields: list[str]
) -> dict[str, Any]:
    comparisons = {
        field: {
            "primary": primary.get(field),
            "audit": audit.get(field),
            "agree": primary.get(field) == audit.get(field),
        }
        for field in fields
    }
    values = [row["agree"] for row in comparisons.values() if row["primary"] is not None or row["audit"] is not None]
    return {
        "fields": comparisons,
        "exact_agreement": (sum(values) / len(values)) if values else None,
        "needs_human_adjudication": any(not value for value in values),
    }


def _rate(values: list[Any]) -> dict[str, Any]:
    evaluable = [value for value in values if isinstance(value, bool)]
    return {
        "numerator": sum(evaluable),
        "denominator": len(evaluable),
        "rate": (sum(evaluable) / len(evaluable)) if evaluable else None,
    }


def _aggregate(episodes: list[dict[str, Any]], workflow: list[dict]) -> dict[str, Any]:
    groups = [g for episode in episodes for g in episode["test"]["groups"]]
    repairs = [episode for episode in episodes if episode.get("repair_attempted")]
    return {
        "episode_count": len(episodes),
        "test": test_summary(groups, len(workflow), workflow, [e["test"]["joint_assessment"] for e in episodes]),
        "visual_judgment": summarize_visual([j for e in episodes for j in (e.get("visual_judgment") or {}).get("judgments", [])]),
        "safe_repair": {
            "attempt_count": len(repairs),
            "target_success": _rate([
                (episode.get("safe_repair") or {}).get("target_fixed") for episode in repairs
            ]),
            "regression_free": _rate([
                (episode.get("safe_repair") or {}).get("regression_free") for episode in repairs
            ]),
            "rechecked": _rate([
                episode.get("rechecked_after_repair") for episode in repairs
            ]),
        },
        "single_composite_score": None,
    }


def _recorded_semantic_plan(episode: dict[str, Any]) -> dict[str, Any] | None:
    rows = episode.get("events") or episode.get("segments") or []
    for row in rows:
        payload = row.get("payload") or row.get("action_payload") or {}
        candidates = [payload, payload.get("action") if isinstance(payload, dict) else None]
        for candidate in candidates:
            if isinstance(candidate, dict) and isinstance(candidate.get("scenarios"), list):
                return {"plans": candidate["scenarios"]}
    return None


def score_trajectory(
    run_json: str | Path,
    task_root: str | Path,
    output_dir: str | Path,
    *,
    primary: JudgeSet = None,
    audit: JudgeSet = None,
    replay_results: str | Path | None = None,
    max_episode_images: int = 8,
    test_only: bool = False,
    episode_id: str | None = None,
) -> dict[str, Any]:
    task_path = Path(task_root).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    extracted = extract_episodes(run_json)
    if episode_id is not None and not any(e["episode_id"] == episode_id for e in extracted["episodes"]):
        raise ValueError(f"Unknown episode_id: {episode_id}")
    checkpoints = checkpoint_index(run_json)
    workflow = load_workflow(task_path / "workflow.json")
    task = (task_path / "prompt.txt").read_text(encoding="utf-8")
    replay = read_json(replay_results) if replay_results else {"episodes": {}}
    replay_by_episode = replay.get("episodes") or {}
    scored: list[dict[str, Any]] = []

    for raw_episode in extracted["episodes"]:
        if episode_id is not None and raw_episode["episode_id"] != episode_id:
            continue
        episode = dict(raw_episode)
        # A post-evidence edit is a candidate repair, not proof of its causal motivation.
        episode["repair_attempted"] = bool(episode.get("edit_after_evidence", episode.get("repair_attempted", False)))
        episode["repair_causal_link"] = episode.get("repair_causal_link") or "not_assessed"
        episode["rechecked_after_repair"] = episode.get("recheck_after_edit", episode.get("rechecked_after_repair"))
        action_text = "\n".join(episode["action_sequence"])
        candidates = lexical_candidates(action_text, workflow)
        replay_row = replay_by_episode.get(episode["episode_id"]) or {}
        workflow_client = _stage_client(primary, "workflow_alignment")
        audit_workflow_client = _stage_client(audit, "workflow_alignment")
        groups = action_groups(episode)
        prompt = episode_test_prompt(task, episode, groups, workflow)
        write_json(output / "test_inputs" / f"{episode['episode_id']}.json", {"prompt": prompt, "images": []})
        record = _safe_judge(workflow_client, "workflow_alignment", prompt, []) if groups else None
        joint = validate_episode_test(record, episode, groups, task, workflow) if groups else {
            "groups": {}, "items": [], "assessment_complete": True, "validation_errors": []}
        retry = None
        if record and not record.get("error") and joint["validation_errors"]:
            retry_prompt = prompt + "\nPrevious response:\n" + json.dumps(record.get("parsed"), ensure_ascii=False)
            retry_prompt += "\nCorrect schema/citation issues without forcing a passing label:\n" + json.dumps(joint["validation_errors"])
            retry = _safe_judge(workflow_client, "workflow_alignment", retry_prompt, [])
            if retry and not retry.get("error"):
                joint = validate_episode_test(retry, episode, groups, task, workflow)
        for group in groups:
            group["reasonableness"] = joint["groups"][group["group_id"]]
        episode["test"] = {"groups": groups, "joint_assessment": {k: v for k, v in joint.items() if k != "groups"},
                           "judge": record, "judge_retry": retry,
                           **test_summary(groups, len(workflow), workflow, [joint])}
        if audit_workflow_client is not None and groups:
            audit_record = _safe_judge(audit_workflow_client, "workflow_alignment", prompt, [])
            episode["test"]["audit"] = {"judge": audit_record, **validate_episode_test(audit_record, episode, groups, task, workflow)}
        if test_only:
            scored.append(episode)
            continue

        visual_client = _stage_client(primary, "visual_judgment")
        audit_visual_client = _stage_client(audit, "visual_judgment")
        episode["visual_judgment"] = evaluate_episode(episode, run_json, task_path, output, visual_client, max_episode_images)
        if audit_visual_client is not None:
            episode["visual_judgment"]["audit"] = evaluate_episode(episode, run_json, task_path, output / "audit", audit_visual_client, max_episode_images)

        safe_repair: dict[str, Any] = {
            "attempted": episode["repair_attempted"],
            "causal_link": episode["repair_causal_link"],
            "status": "not_applicable" if not episode["repair_attempted"] else "not_evaluable",
            "target_fixed": None,
            "regression_free": None,
            "reason": None,
        }
        if episode["repair_attempted"]:
            before_checkpoint = checkpoints.get(str(episode["program_before"]))
            after_checkpoint = checkpoints.get(str(episode["program_after"]))
            before_workspace = checkpoint_workspace(
                before_checkpoint,
                output / "checkpoints" / str(episode["program_before"]),
            )
            after_workspace = checkpoint_workspace(
                after_checkpoint,
                output / "checkpoints" / str(episode["program_after"]),
            )
            patch = (
                workspace_patch(before_workspace, after_workspace)
                if before_workspace and after_workspace
                else None
            )
            safe_repair["patch"] = patch
            scope_client = _stage_client(primary, "repair_scope")
            scope_record = None
            affected_ids: list[str] = []
            if scope_client is not None and patch is not None:
                scope_record = _safe_judge(
                    scope_client,
                    "repair_scope",
                    _repair_scope_prompt(task, episode, workflow, patch),
                    [],
                )
                valid_ids = {row["workflow_id"] for row in workflow}
                affected_ids = sorted({
                    str(value) for value in _parsed(scope_record).get("affected_workflow_ids") or []
                    if str(value) in valid_ids
                })
            # Correct interpretation / workflow coverage does NOT certify a working feature.
            previously_passed_ids = {
                str(row["workflow_id"])
                for row in replay_row.get("regressions") or []
                if isinstance(row, dict) and row.get("before_passed") is True
                and row.get("workflow_id") is not None
            }
            expected_regressions = sorted(set(affected_ids) & previously_passed_ids)
            safe_repair.update({
                "affected_workflow_ids": affected_ids,
                "previously_passed_affected_ids": expected_regressions,
                "scope_judge": scope_record,
            })
            if not replay_row:
                safe_repair["reason"] = "No P_before/P_after replay result for this episode"
            else:
                target = replay_row.get("target") or {}
                regressions = replay_row.get("regressions") or []
                demonstrated = (target.get("comparable_before_after") is True
                                and target.get("before_failure_demonstrated") is True)
                safe_repair["target_fixed"] = _bool_or_none(target.get("fixed")) if demonstrated else None
                regression_by_id = {
                    str(row.get("workflow_id")): _bool_or_none(row.get("passed"))
                    for row in regressions if isinstance(row, dict) and row.get("workflow_id") is not None
                    and row.get("before_passed") is True
                }
                required_ids = expected_regressions or sorted(regression_by_id)
                missing_ids = [value for value in required_ids if value not in regression_by_id]
                regression_values = [regression_by_id[value] for value in required_ids if value in regression_by_id]
                regression_coverage_complete = bool(required_ids and not missing_ids)
                safe_repair["regression_replayed_ids"] = sorted(regression_by_id)
                safe_repair["missing_regression_ids"] = missing_ids
                safe_repair["regression_coverage_complete"] = regression_coverage_complete
                safe_repair["regression_scope"] = "Only replay checks with a demonstrated passing P_before baseline; not all functionality"
                safe_repair["regression_free"] = (
                    False if any(value is False for value in regression_values)
                    else True if regression_coverage_complete and all(value is True for value in regression_values)
                    else None
                )
                before_images = _select_images(replay_row.get("before_images") or [], 4)
                after_images = _select_images(replay_row.get("after_images") or [], 4)
                repair_client = _stage_client(primary, "safe_repair_visual")
                if repair_client is not None and before_images and after_images:
                    labels = [f"P_before state {i + 1}" for i in range(len(before_images))] + [
                        f"P_after state {i + 1}" for i in range(len(after_images))
                    ]
                    repair_record = _safe_judge(
                        repair_client,
                        "safe_repair_visual",
                        _repair_visual_prompt(task, episode, labels, replay_row),
                        before_images + after_images,
                    )
                    repair_value = _parsed(repair_record)
                    # Legacy screenshot opinions are diagnostic only. They cannot fill
                    # missing execution baselines or certify untested regression checks.
                    safe_repair["visual_judge"] = repair_record
                    safe_repair["reason"] = repair_value.get("reason")
                else:
                    safe_repair["reason"] = target.get("reason") or "Replay evidence contains no before/after visual pair"
                if safe_repair["target_fixed"] is not None and safe_repair["regression_free"] is not None:
                    safe_repair["status"] = "evaluated"
                elif safe_repair["target_fixed"] is not None or safe_repair["regression_free"] is not None:
                    safe_repair["status"] = "partially_evaluated"
        episode["safe_repair"] = safe_repair

        # Retained only for callers explicitly opting into the historical narrative.
        # A single default JudgeClient must not trigger a fourth scoring stage.
        summary_client = _stage_client(primary, "episode_summary") if isinstance(primary, Mapping) else None
        if summary_client is not None:
            episode["summary"] = _safe_judge(
                summary_client, "episode_summary", _summary_prompt(episode), []
            )
        else:
            episode["summary"] = None
        scored.append(episode)

    result = {
        "schema": "multimodalcode-vsv-score-3",
        "episode_filter": episode_id,
        "stages": ["test"] if test_only else ["test", "visual_judgment", "safe_repair"],
        "source_run": extracted["source_run"],
        "source_run_sha256": hashlib.sha256(Path(run_json).read_bytes()).hexdigest(),
        "task_prompt_sha256": hashlib.sha256((task_path / "prompt.txt").read_bytes()).hexdigest(),
        "workflow_sha256": hashlib.sha256((task_path / "workflow.json").read_bytes()).hexdigest(),
        "case_id": extracted["case_id"],
        "model": extracted["model"],
        "framework": extracted["framework"],
        "mode": extracted["mode"],
        "task_root": str(task_path),
        "judge_enabled": primary is not None,
        "audit_enabled": audit is not None,
        "episodes": scored,
        "aggregate": _aggregate(scored, workflow),
    }
    replay_request_rows = []
    for episode in scored:
        if test_only or not episode.get("repair_attempted"):
            continue
        semantic_plan = _recorded_semantic_plan(episode)
        replay_request_rows.append(
            {
                "episode_id": episode["episode_id"],
                "program_before": episode["program_before"],
                "program_after": episode["program_after"],
                "before_checkpoint": checkpoints.get(str(episode["program_before"])),
                "after_checkpoint": checkpoints.get(str(episode["program_after"])),
                "action_sequence": episode["action_sequence"],
                "semantic_plan": semantic_plan,
                "requires_semantic_plan": semantic_plan is None,
                "repair_attempted": episode["repair_attempted"],
                "recommended_regression_workflow_ids": (
                    episode.get("safe_repair") or {}
                ).get("previously_passed_affected_ids") or [],
                "replayable_now": bool(
                    semantic_plan is not None
                    and checkpoints.get(str(episode["program_before"]), {}).get("reconstructable")
                    and (
                        not episode["repair_attempted"]
                        or checkpoints.get(str(episode["program_after"]), {}).get("reconstructable")
                    )
                ),
            }
        )
    replay_requests = {
        "schema": "multimodalcode-vsv-replay-requests-1",
        "case_id": extracted["case_id"],
        "episodes": replay_request_rows,
    }
    write_json(output / "episodes.json", extracted)
    write_json(output / "scores.json", result)
    write_json(output / "replay_requests.json", replay_requests)
    write_html_report(result, output / "index.html")
    return result
