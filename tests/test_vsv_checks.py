from pathlib import Path

from multimodalcode.vsv_eval.checks import (
    action_groups, recorded_execution, reasonableness_prompt,
    validate_reasonableness, test_summary as summarize_tests,
)
from multimodalcode.vsv_eval.episodes import extract_episodes


def group_with(text, error=False):
    group = {
        "group_id": "test/action-1", "action_ordinal": 1, "tool": "Bash",
        "command": "playwright-cli run-code ...", "action": {},
        "observations": [{"ordinal": 2, "text": text, "is_error": error}],
        "image_observations": [],
    }
    group["execution"] = recorded_execution(group)
    return group


def test_tool_error_is_not_hidden_by_shell_success():
    assert group_with("ReferenceError: setTimeout is not defined")["execution"]["status"] == "fail"
    assert group_with("```js\nthrow new Error('example')\n```\n")["execution"]["status"] == "not_evaluable"
    assert group_with('{"error":"timeout"}', error=True)["execution"]["status"] == "fail"
    # A successfully observed broken application is still a successful check execution.
    assert group_with('{"menuOpen": false}')["execution"]["status"] == "pass"


def test_timeout_with_earlier_screenshot_is_partial():
    group = group_with("Command timed out after 2m 0s", error=True)
    group["image_observations"] = [{"ordinal": 5, "images": [{"path": "one.png"}]}]
    assert recorded_execution(group)["status"] == "partial"


def test_call_ids_pair_out_of_order_results_and_keep_scenarios_together():
    episode = {"episode_id": "test", "events": [
        {"ordinal": 1, "kind": "action", "category": "browser", "tool": "browser_execute_plan", "tool_call_id": "a", "payload": {"scenarios": [{}, {}, {}]}},
        {"ordinal": 2, "kind": "action", "category": "browser", "tool": "browser_get_state", "tool_call_id": "b", "payload": {}},
        {"ordinal": 3, "kind": "observation", "tool": "browser_get_state", "payload": {"tool_use_id": "b"}, "text": "state", "is_error": False},
        {"ordinal": 4, "kind": "observation", "tool": "browser_execute_plan", "payload": {"tool_use_id": "a"}, "text": "done", "is_error": False},
    ]}
    groups = action_groups(episode)
    assert len(groups) == 2
    assert [r["ordinal"] for r in groups[0]["observations"]] == [4]
    assert [r["ordinal"] for r in groups[1]["observations"]] == [3]
    assert len(groups[0]["action"]["scenarios"]) == 3


def test_complete_workflow_includes_zero_overlap_and_more_than_five_items():
    workflow = [{"workflow_id": f"0.{i}", "objective": f"behavior {i}", "search_text": "unrelated"} for i in range(13)]
    prompt = reasonableness_prompt("task", group_with('{"ok":true}'), workflow)
    for row in workflow:
        assert f'"workflow_id": "{row["workflow_id"]}"' in prompt


def test_original_task_is_complete_and_precedes_workflow():
    task = "Original requirements: " + "menu " * 4000 + "END OF TASK"
    prompt = reasonableness_prompt(task, group_with('{"ok":true}'), [])
    assert task in prompt
    assert prompt.index(task) < prompt.index("COMPLETE WORKFLOW")
    assert "primary specification" in prompt
    assert "even when execution fails" in prompt
    assert "Test is text-only" in prompt
    assert "This call's action ordinal is 1" in prompt
    assert "CONTIGUOUS, EXACT" in prompt


def test_prior_policy_context_never_includes_later_verdict():
    episode = {"episode_id": "test", "events": [
        {"ordinal": 0, "kind": "model_text", "text": "Recheck the logo after editing."},
        {"ordinal": 1, "kind": "action", "category": "browser", "tool": "browser_get_state"},
        {"ordinal": 2, "kind": "observation", "text": "page", "is_error": False},
        {"ordinal": 3, "kind": "model_text", "text": "It is fixed."},
    ]}
    assert action_groups(episode)[0]["preceding_policy_text"] == [
        {"ordinal": 0, "text": "Recheck the logo after editing."}
    ]


def test_judge_citations_and_unknowns_cannot_manufacture_coverage():
    group = group_with('{"ok":true}')
    workflow = [{"workflow_id": "0.0", "objective": "Open menu"}]
    parsed = {
        "status": "reasonable", "requirement_source": "task", "requirement_quote": "Open menu",
        "reason": "Tests menu state", "evidence_ordinals": [1, 2],
        "matches": [{"workflow_id": "0.0", "coverage": "full", "evidence_ordinals": [1]}],
    }
    valid = validate_reasonableness({"parsed": parsed}, group, "Open menu", workflow)
    assert valid["status"] == "reasonable"
    assert valid["matches"] == []  # No observation cited for full coverage.
    assert valid["validation_errors"]
    assert not valid["coverage_assessment_complete"]
    parsed["evidence_ordinals"] = [999]
    assert validate_reasonableness({"parsed": parsed}, group, "Open menu", workflow)["status"] == "not_evaluable"
    group["reasonableness"] = validate_reasonableness(None, group, "Open menu", workflow)
    assert summarize_tests([group], 13)["workflow_coverage"]["rate"] is None


def test_partial_match_requires_action_and_positive_observation():
    group = group_with('{"menuOpen":false}')
    workflow = [{"workflow_id": "0.0", "objective": "Open menu"}]
    parsed = {"status": "reasonable", "requirement_source": "task", "requirement_quote": "Open menu",
              "reason": "Exercises menu", "evidence_ordinals": [1, 2], "needs_visual_review": False,
              "matches": [{"workflow_id": "0.0", "coverage": "partial", "evidence_ordinals": [2], "reason": "Menu observed"}]}
    rejected = validate_reasonableness({"parsed": parsed}, group, "Open menu", workflow)
    assert rejected["validation_errors"] and not rejected["matches"]
    parsed["matches"][0]["evidence_ordinals"] = [1, 2]
    accepted = validate_reasonableness({"parsed": parsed}, group, "Open menu", workflow)
    assert accepted["matches"] and accepted["coverage_assessment_complete"]


def test_coverage_deduplicates_without_combining_partial_checks():
    from copy import deepcopy
    workflow = [{"workflow_id": f"0.{i}", "objective": f"Target {i}"} for i in range(3)]
    group = group_with('{"appBroken":true}')
    group["reasonableness"] = {"status": "reasonable", "coverage_assessment_complete": True,
        "matches": [{"workflow_id": "0.0", "coverage": "full", "evidence_ordinals": [1, 2], "reason": "Executed target"},
                    {"workflow_id": "0.1", "coverage": "partial", "evidence_ordinals": [1, 2], "reason": "One substep"}]}
    groups = [group, deepcopy(group)]
    groups[1]["group_id"] = "another-episode/action-1"
    coverage = summarize_tests(groups, 3, workflow)["workflow_coverage"]
    assert coverage["numerator"] == 1 and coverage["rate"] == 1 / 3
    assert coverage["partial_only_ids"] == ["0.1"]
    assert [r["status"] for r in coverage["items"]] == ["full", "partial", "uncovered"]
    assert len(coverage["items"][0]["evidence"]) == 2
    # A later complete execution subsumes partial credit, but doesn't count twice.
    groups[1]["reasonableness"]["matches"][1]["coverage"] = "full"
    coverage = summarize_tests(groups, 3, workflow)["workflow_coverage"]
    assert coverage["numerator"] == 2 and coverage["partial_only_count"] == 0
    groups[1]["reasonableness"]["coverage_assessment_complete"] = False
    coverage = summarize_tests(groups, 3, workflow)["workflow_coverage"]
    assert coverage["is_lower_bound"] and coverage["items"][2]["status"] == "unknown"


def test_fixture_execution_matches_audited_twelve_calls():
    root = Path(__file__).resolve().parents[1]
    source = root / "data/vision2web/vsv_eval_fixtures/frontend__smartrecruiters__claude-opus-4-8__self_verify/trajectory/run.json"
    if not source.is_file():
        import pytest
        pytest.skip("Local pilot fixture unavailable")
    episodes = extract_episodes(source)["episodes"]
    groups = [g for episode in episodes for g in action_groups(episode)]
    assert len(groups) == 12
    assert {g["action_ordinal"]: g["execution"]["status"] for g in groups} == {
        232: "pass", 247: "fail", 249: "pass", 272: "partial", 287: "pass", 293: "pass",
        299: "pass", 302: "pass", 305: "pass", 310: "pass", 313: "pass", 341: "pass",
    }
    assert next(g for g in groups if g["action_ordinal"] == 272)["execution"]["image_consumed_ordinals"] == [276, 280, 284]


def _joint_case(single=False):
    from multimodalcode.vsv_eval.checks import validate_episode_test
    events = []
    for ordinal in ([0] if single else [0, 10, 20]):
        events.extend([
            {"ordinal": ordinal, "timestamp": f"2026-01-01T00:00:{ordinal:02d}+00:00", "kind": "action", "category": "browser", "tool": "Bash" if single else "browser_get_state", "text": "Check menu"},
            {"ordinal": ordinal + 1, "timestamp": f"2026-01-01T00:00:{ordinal+1:02d}+00:00", "kind": "observation", "text": '{"menuOpen":true}', "is_error": False},
        ])
    episode = {"episode_id": "joint", "events": events}
    groups = action_groups(episode)
    workflow = [{"workflow_id": "0.0", "objective": "Check menu"}]
    record = {"parsed": {
        "groups": [{"group_id": g["group_id"], "status": "reasonable", "requirement_source": "task", "requirement_quote": "Check menu", "reason": "Contributes to menu check", "evidence_ordinals": [g["action_ordinal"]], "needs_visual_review": False} for g in groups],
        "coverage": [{"workflow_id": "0.0", "status": "full", "evidence_chains": [[r["ordinal"] for r in events]], "reason": "Connected sequence exercises and observes menu"}],
    }}
    def assess():
        result = validate_episode_test(record, episode, groups, "Check menu", workflow)
        for group in groups:
            group["reasonableness"] = result["groups"][group["group_id"]]
        return result, summarize_tests(groups, 1, workflow, [result])
    return episode, groups, record, assess


def test_joint_coverage_is_independent_of_tool_call_packaging():
    import copy
    for single in (True, False):
        episode, groups, _, assess = _joint_case(single)
        original = copy.deepcopy(episode)
        result, summary = assess()
        assert result["assessment_complete"] and not result["validation_errors"]
        assert summary["workflow_coverage"]["rate"] == 1
        assert summary["action_group_count"] == (1 if single else 3)
        assert episode == original  # evidence references, not synthetic turns


def test_joint_prompt_preserves_text_only_scope_and_behavioral_coverage():
    from multimodalcode.vsv_eval.checks import episode_test_prompt
    episode, groups, _, _ = _joint_case()
    prompt = episode_test_prompt("Check menu", episode, groups, [])
    assert "needs_visual_review=false for EVERY group" in prompt
    assert "does NOT mean that the original action uses vision" in prompt
    assert "does NOT partially cover a workflow" in prompt
    assert "check BOTH its actions AND every entry in validations" in prompt
    assert "navigation alone is partial, not full" in prompt
    assert "its own action and only its paired observations" in prompt


def test_joint_coverage_rejects_edits_resets_and_terminal_workspace_changes():
    for boundary in ("edit", "reset", "workspace"):
        episode, _, _, assess = _joint_case()
        if boundary == "workspace":
            episode["workspace_changes"] = [{"timestamp": "2026-01-01T00:00:05+00:00", "modified": ["app.js"]}]
        else:
            episode["events"].append({"ordinal": 5, "kind": "action", "category": "edit" if boundary == "edit" else "browser", "tool": "Edit" if boundary == "edit" else "browser_reset"})
        result, summary = assess()
        assert result["validation_errors"]
        assert result["items"][0]["status"] == "unknown"
        assert summary["workflow_coverage"]["numerator"] == 0


def test_joint_coverage_requires_positive_observations_and_all_items():
    for chain in ([0, 10, 20], [0, 1, 10], [0, 999], [0, 0, 1]):
        _, _, record, assess = _joint_case()
        record["parsed"]["coverage"][0]["evidence_chains"] = [chain]
        assert assess()[0]["validation_errors"]
    _, _, record, assess = _joint_case()
    record["parsed"]["coverage"] = []
    assert not assess()[0]["assessment_complete"]


def test_separate_partial_assessments_are_not_added_into_full_coverage():
    _, groups, record, assess = _joint_case()
    record["parsed"]["coverage"][0]["status"] = "partial"
    result, _ = assess()
    summary = summarize_tests(groups, 1, [{"workflow_id": "0.0"}], [result, result])
    assert summary["workflow_coverage"]["numerator"] == 0
    assert summary["workflow_coverage"]["partial_only_ids"] == ["0.0"]
