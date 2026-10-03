import copy
import json
from unittest.mock import Mock

import pytest

from multimodalcode.vsv_eval.episodes import extract_verification_rounds
from multimodalcode.vsv_eval.judge import JudgeClient, JudgeProfile


def call(o, **kw):
    return {"ordinal": o, "kind": "action", "tool_call_id": f"c{o}",
            "category": "browser", "tool": "browser_state", **kw}


def result(o, action, **kw):
    return {"ordinal": o, "kind": "observation", "payload": {"tool_use_id": f"c{action}"}, **kw}


def policy(o, text):
    return {"ordinal": o, "kind": "model_text", "text": text}


def round_row(ids, policies=(), judgments=(), context=()):
    return {"candidate_ids": [f"window-{o}" for o in ids], "policy_event_ids": list(policies),
            "judgment_event_ids": list(judgments), "context_event_ids": list(context)}


def run_fixture(tmp_path):
    events = [
        policy(0, "Capture two states."),
        call(1, tool="Bash", payload={"command": "node capture http://localhost:3000/ /tmp/a.png /tmp/b.png"}),
        result(2, 1, text="captured both"),
        call(3, category="view-image", tool="Read", payload={"file_path": "/tmp/a.png"}),
        result(4, 3, images=[{"path": "missing-a.png"}]),
        policy(5, "Two defects: title overlaps and footer is blank."),
        call(6, category="view-image", tool="Read", payload={"file_path": "/tmp/b.png"}),
        result(7, 6, images=[{"path": "missing-b.png"}]),
        policy(8, "This state also has the shared footer defect."),
        call(9, category="edit", tool="Edit", payload={"file_path": "component.js", "old_string": "old", "new_string": "new"}),
        result(10, 9, text="file updated successfully"),
        call(11, tool="browser_screenshot"),
        result(12, 11, is_error=True, text="capture failed"),
        call(13, tool="browser_state", payload={"operations": ["navigate", "click", "observe"]}),
        result(14, 13, images=[{"path": "missing-recheck.png"}]),
        policy(15, "Shared footer now renders in the first state."),
        call(16, tool="browser_screenshot"), result(17, 16, is_error=True, text="timeout"),
        call(18, category="view-image", tool="Read", payload={"file_path": "/inputs/prototype.png"}),
        result(19, 18, images=[{"path": "reference.png"}]),
    ]
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"case_id": "fixture", "timeline": events}))
    annotation = {
        "rounds": [round_row([3], [5], [5]), round_row([6], [8], [8], [5]),
                   round_row([11, 13], [15], [15]), round_row([16])],
        "excluded_candidate_ids": ["window-1", "window-18"],
        "repair_links": [{"source_candidate_id": f"window-{o}", "repair_event_ids": [9],
                          "recheck_candidate_ids": ["window-13"], "evidence_event_ids": [5, 8, 15]} for o in [3, 6]],
    }
    judge = JudgeClient(JudgeProfile("fixture", "openai-compatible", "fixture", "http://unused", "UNUSED"), tmp_path / "cache")
    judge._request = Mock(return_value=json.dumps(annotation))
    return run, events, annotation, judge


def test_round_cores_shared_sources_failed_retry_delayed_shared_repair_and_missing_judgment(tmp_path):
    run, events, _, judge = run_fixture(tmp_path)
    packet = extract_verification_rounds(run, judge)
    episodes = packet["episodes"]
    assert len(episodes) == 4 and packet["image_episode_count"] == 3
    assert episodes[0]["core_event_ids"] == [3, 4, 5]  # two findings, one round
    assert episodes[1]["core_event_ids"] == [6, 7, 8]
    assert {0, 1, 2} <= set(episodes[0]["context_event_ids"]) & set(episodes[1]["context_event_ids"])
    assert episodes[2]["core_event_ids"] == [11, 12, 13, 14, 15]  # failed retry + intact multi-operation call
    assert episodes[3]["judgment_event_ids"] == [] and episodes[3]["core_event_ids"] == [16, 17]
    assert all(e["repair_links"][0]["repair_event_ids"] == [9] for e in episodes[:2])
    assert all(e["repair_links"][0]["recheck_episode_ids"] == [episodes[2]["episode_id"]] for e in episodes[:2])
    assert 9 not in episodes[0]["core_event_ids"] and 15 not in episodes[0]["context_event_ids"]
    assert packet["events"] == events  # neither annotation nor file availability rewrites facts
    assert packet["source_only_candidate_ids"] == ["window-1"]
    read = next(w for w in packet["windows"] if w["id"] == "window-3")
    assert read["image_availability"][0]["available"] is False
    assert extract_verification_rounds(run, judge) == packet
    judge._request.assert_called_once()


def test_cross_modification_merge_and_future_current_context_raise(tmp_path):
    run, _, annotation, judge = run_fixture(tmp_path)
    broken = copy.deepcopy(annotation)
    broken["rounds"] = [round_row([3, 6, 11, 13], [5, 8, 15], [5, 8, 15]), round_row([16])]
    judge._request.return_value = json.dumps(broken)
    with pytest.raises(ValueError, match="modification"):
        extract_verification_rounds(run, judge)
    judge.cache_root = tmp_path / "other-cache"
    judge.cache_root.mkdir()
    broken = copy.deepcopy(annotation)
    broken["rounds"][0]["context_event_ids"] = [15]
    judge._request.return_value = json.dumps(broken)
    with pytest.raises(ValueError, match="Future"):
        extract_verification_rounds(run, judge)


@pytest.mark.parametrize("change", [
    lambda a: a.update(extra=True),
    lambda a: a["rounds"][0].update(candidate_ids=["window-999"]),
    lambda a: a["rounds"][0].update(judgment_event_ids=[4]),
    lambda a: a["rounds"].append(a["rounds"][0]),
    lambda a: a["repair_links"][0].update(repair_event_ids=[13]),
    lambda a: a["repair_links"][0].update(recheck_candidate_ids=["window-6"]),
])
def test_invalid_annotations_are_not_accepted_or_silently_corrected(tmp_path, change):
    run, _, annotation, judge = run_fixture(tmp_path)
    change(annotation)
    judge._request.return_value = json.dumps(annotation)
    with pytest.raises(ValueError):
        extract_verification_rounds(run, judge)
    judge._request.assert_called_once()


def test_budget_batches_preserve_global_ids_and_report_missing_context(tmp_path):
    events = []
    for o in range(0, 40, 2):
        events += [call(o), result(o + 1, o, text="evidence " * 100)]
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"timeline": events}))
    judge = JudgeClient(JudgeProfile("fixture", "openai-compatible", "fixture", "http://unused", "UNUSED"), tmp_path / "cache")

    def respond(prompt, images):
        assert images == []
        value = json.loads(prompt.split("EVIDENCE:\n", 1)[1])
        return json.dumps({"rounds": [round_row([int(v.split('-')[1])]) for v in value["owned_candidate_ids"]],
                           "excluded_candidate_ids": [], "repair_links": []})

    judge._request = Mock(side_effect=respond)
    packet = extract_verification_rounds(run, judge, max_input_chars=11000)
    assert len(packet["annotation"]["batches"]) > 1
    assert len(packet["episodes"]) == 20 and len(set(e["episode_id"] for e in packet["episodes"])) == 20
    assert packet["annotation_limitations"]
    assert any(b["omitted_candidate_ids"] for b in packet["annotation"]["batches"])
    assert packet["events"] == events


def test_old_image_read_after_edit_retains_capture_state_as_historical_context(tmp_path):
    run, events, annotation, judge = run_fixture(tmp_path)
    # The second original screenshot is consumed only after the edit.
    events = [e for e in events if e["ordinal"] not in [6, 7, 8]]
    events += [call(20, category="view-image", tool="Read", payload={"file_path": "/tmp/b.png"}),
               result(21, 20, images=[{"path": "missing-b.png"}])]
    for e in events:
        e["timestamp"] = f"2026-01-01T00:00:{e['ordinal']:02d}Z"
    run.write_text(json.dumps({"timeline": events, "development": {"events": [
        {"type": "recorder_started", "payload": {"initial_program_sha256": "old"}},
        {"type": "workspace_change", "timestamp": "2026-01-01T00:00:10Z",
         "payload": {"program_sha256": "new", "modified": ["component.js"]}},
    ]}}))
    annotation["rounds"] = [round_row([3], [5], [5]), round_row([11, 13], [15], [15]), round_row([16]), round_row([20])]
    annotation["repair_links"] = annotation["repair_links"][:1]
    annotation["repair_links"][0]["evidence_event_ids"] = [5, 15]
    judge._request.return_value = json.dumps(annotation)
    packet = extract_verification_rounds(run, judge)
    last = packet["episodes"][-1]
    assert last["evidence_states"] == [{"event_id": 21, "producer_event_id": 1, "program_sha256": "old",
                                        "read_program_sha256": "new"}]


def test_shared_intent_is_context_not_duplicate_core_and_judgment_requires_evidence(tmp_path):
    run, _, annotation, judge = run_fixture(tmp_path)
    annotation["rounds"][0]["policy_event_ids"].append(0)
    annotation["rounds"][1]["policy_event_ids"].append(0)
    judge._request.return_value = json.dumps(annotation)
    with pytest.raises(ValueError, match="two independent"):
        extract_verification_rounds(run, judge)
    judge.cache_root = tmp_path / "next-cache"
    judge.cache_root.mkdir()
    annotation["rounds"][1]["policy_event_ids"].remove(0)
    annotation["rounds"][0]["judgment_event_ids"].append(0)
    judge._request.return_value = json.dumps(annotation)
    with pytest.raises(ValueError, match="follow its recorded evidence"):
        extract_verification_rounds(run, judge)


def test_round_continuation_across_batches_deduplicates_core_references(tmp_path):
    events = []
    for o in range(0, 30, 2):
        events += [call(o), result(o + 1, o, text="evidence " * 100)]
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"timeline": events}))
    judge = JudgeClient(JudgeProfile("fixture", "openai-compatible", "fixture", "http://unused", "UNUSED"), tmp_path / "cache")

    def respond(prompt, images):
        value = json.loads(prompt.split("EVIDENCE:\n", 1)[1])
        ids = value["owned_candidate_ids"] + [v for v in value["context_candidate_ids"]
                                            if int(v.split('-')[1]) < int(value["owned_candidate_ids"][0].split('-')[1])]
        return json.dumps({"rounds": [{"candidate_ids": ids, "policy_event_ids": [],
                                      "judgment_event_ids": [], "context_event_ids": []}],
                           "excluded_candidate_ids": [], "repair_links": []})

    judge._request = Mock(side_effect=respond)
    packet = extract_verification_rounds(run, judge, max_input_chars=11000)
    assert len(packet["annotation"]["batches"]) > 1
    assert len(packet["episodes"]) == 1 and packet["episodes"][0]["core_event_ids"] == list(range(30))


def test_all_evidence_modes_attempts_and_missing_returns_share_one_record_structure(tmp_path):
    events = [
        call(1, category="inspect", tool="Bash", payload={"command": "curl http://localhost:3000/"}),
        result(2, 1, text="HTTP 200"), policy(3, "The endpoint responds."),
        call(4), result(5, 4, images=[{"path": "missing.png"}], text="console output"),
        policy(6, "I inspected the combined browser evidence."),
        call(7, tool="browser_screenshot"), result(8, 7, is_error=True, text="capture failed"),
        call(9),  # no return or judgment was recorded
        call(10, category="view-image", tool="Read"), result(11, 10, images=[{"path": "other.png"}]),
        policy(12, "Image-only inspection."),
        call(13, category="inspect", tool="Bash"), result(14, 13, text=""),
    ]
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"timeline": events}))
    annotation = {
        "rounds": [round_row([1], [3], [3]), round_row([4], [6], [6]), round_row([7]),
                   round_row([9]), round_row([10], [12], [12]), round_row([13], context=[11])],
        "excluded_candidate_ids": [], "repair_links": [],
    }
    judge = JudgeClient(JudgeProfile("fixture", "openai-compatible", "fixture", "http://unused", "UNUSED"), tmp_path / "cache")
    judge._request = Mock(return_value=json.dumps(annotation))
    packet = extract_verification_rounds(run, judge)
    text, mixed, failed, missing, image, empty = packet["episodes"]
    assert text["verification_kind"] == "non_visual" and text["evidence_modalities"] == ["text"]
    assert mixed["evidence_modalities"] == ["image", "text"]
    window = next(w for w in packet["windows"] if w["id"] == "window-4")
    assert window["image_availability"] == [{"event_id": 5, "path": "missing.png", "available": False}]
    assert failed["core_event_ids"] == [7, 8] and packet["events"][7]["is_error"] is True
    assert missing["core_event_ids"] == [9]
    assert missing["evidence_modalities"] == [] and missing["judgment_event_ids"] == []
    assert image["evidence_modalities"] == ["image"]  # narration is not observed text evidence
    assert empty["evidence_modalities"] == []  # old context images are not new evidence receipt
    assert {10, 11} <= set(empty["context_event_ids"])
    assert packet["image_episode_count"] == 2 and packet["image_input_count"] == 2
    assert packet["visual_attempt_without_image_count"] == 1
    assert packet["non_visual_episode_count"] == 3 and packet["mixed_evidence_episode_count"] == 1
    assert [e["episode_id"] for e in packet["episodes"] if not e["judgment_event_ids"]] == [e["episode_id"] for e in [failed, missing, empty]]
    assert all(set(e) == {"episode_id", "candidate_ids", "verification_kind", "evidence_modalities",
                          "core_event_ids", "judgment_event_ids", "context_event_ids", "evidence_states",
                          "program_sha256", "repair_links", "relation_annotation_complete"} for e in packet["episodes"])
    assert all("events" not in w and w["event_ids"] for w in packet["windows"])
    assert packet["events"] == events and packet["episode_count"] == 6
    judge._request.assert_called_once()


def test_reference_records_pair_interleaved_returns_and_preserve_raw_error_text(tmp_path):
    events = [call(1, category="inspect"), call(2, category="inspect"),
              result(3, 2, is_error=True, text="Error: unavailable"),
              result(4, 1, text="Echoed code:\n```\nError: example\n```\nHTTP 200")]
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"timeline": events}))
    judge = JudgeClient(JudgeProfile("fixture", "openai-compatible", "fixture", "http://unused", "UNUSED"), tmp_path / "cache")
    judge._request = Mock(return_value=json.dumps({
        "rounds": [round_row([1]), round_row([2])], "excluded_candidate_ids": [], "repair_links": [],
    }))
    packet = extract_verification_rounds(run, judge)
    first, second = packet["episodes"]
    assert first["core_event_ids"] == [1, 4] and second["core_event_ids"] == [2, 3]
    assert packet["events"] == events


def test_nonvisual_rounds_keep_the_same_repair_and_recheck_references(tmp_path):
    events = [call(1, category="inspect", tool="Bash"), result(2, 1, text="HTTP 404"),
              policy(3, "The requested route is missing."),
              call(4, category="edit", tool="Edit", payload={"file_path": "routes.js", "old_string": "old", "new_string": "new"}),
              result(5, 4, text="updated"),
              call(6, category="inspect", tool="Bash"), result(7, 6, text="HTTP 200"),
              policy(8, "The route now responds.")]
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"timeline": events}))
    judge = JudgeClient(JudgeProfile("fixture", "openai-compatible", "fixture", "http://unused", "UNUSED"), tmp_path / "cache")
    judge._request = Mock(return_value=json.dumps({
        "rounds": [round_row([1], [3], [3]), round_row([6], [8], [8])], "excluded_candidate_ids": [],
        "repair_links": [{"source_candidate_id": "window-1", "repair_event_ids": [4],
                          "recheck_candidate_ids": ["window-6"], "evidence_event_ids": [3, 4, 8]}],
    }))
    packet = extract_verification_rounds(run, judge)
    first, recheck = packet["episodes"]
    assert all(e["verification_kind"] == "non_visual" for e in packet["episodes"])
    assert first["repair_links"][0]["repair_event_ids"] == [4]
    assert first["repair_links"][0]["recheck_episode_ids"] == [recheck["episode_id"]]
    assert not set(first["repair_links"][0]["repair_event_ids"]) & set(first["core_event_ids"])
    assert packet["events"] == events
