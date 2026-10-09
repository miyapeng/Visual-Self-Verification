import copy
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from multimodalcode.vsv_eval.episodes import extract_candidate_windows, split_visual_windows, extract_verification_processes
from multimodalcode.vsv_eval.judge import (
    JudgeClient, JudgeProfile, SYSTEM_PROMPT, select_verification_windows,
    verification_filter_prompt,
)


def write_run(tmp_path, events):
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"timeline": events, "case_id": "synthetic"}))
    return path


def action(ordinal, call_id, **kwargs):
    return {"ordinal": ordinal, "kind": "action", "category": "browser",
            "tool": "browser_get_state", "tool_call_id": call_id, **kwargs}


def observation(ordinal, call_id, **kwargs):
    return {"ordinal": ordinal, "kind": "observation",
            "payload": {"tool_use_id": call_id}, **kwargs}


def client(tmp_path):
    return JudgeClient(JudgeProfile("test", "openai-compatible", "fixture", "http://unused", "UNUSED"), tmp_path)


def test_window_boundaries_pair_parallel_returns_and_preserve_originals(tmp_path):
    events = [
        {"ordinal": 10, "kind": "model_text", "text": "Check both states."},
        action(20, "a", payload={"scenarios": ["one", "two"]}),
        action(30, "b"),
        observation(40, "b", text="second result"),
        {"ordinal": 50, "kind": "model_text", "text": "Second is visible."},
        observation(60, "a", text="first result"),
        {"ordinal": 70, "kind": "reasoning", "text": "First is broken."},
        action(80, "edit", category="edit", tool="Edit"),
        observation(90, "edit", text="edited"),
    ]
    windows = extract_candidate_windows(write_run(tmp_path, events))
    assert [[e["ordinal"] for e in w["events"]] for w in windows] == [[10, 20, 60, 70], [30, 40, 50]]
    assert len(windows) == 2  # one multi-scenario call stays one window
    originals = {e["ordinal"]: e for e in events}
    assert all(e == originals[e["ordinal"]] for w in windows for e in w["events"])


def test_failed_missing_and_unreturned_checks_survive_with_distant_source_ref(tmp_path):
    events = [
        action(1, "shot", tool="Bash", category="inspect", payload={
            "command": "node capture.js http://localhost:3000/ /tmp/output.png"}),
        observation(2, "shot", is_error=True, text="capture failed"),
        action(3, "edit", category="edit", tool="Write"),
        observation(4, "edit", text="written"),
        action(5, "read", category="view-image", tool="Read", payload={"file_path": "/tmp/output.png"}),
        observation(6, "read", images=[{"path": "missing.png", "sha256": "recorded"}]),
        action(7, "no-return"),
        action(8, "reference", category="view-image", tool="Read", payload={"file_path": "/inputs/reference.png"},
               browser_url_context="http://localhost:3000/"),
        observation(9, "reference", is_error=True, text="file missing"),
    ]
    windows = extract_candidate_windows(write_run(tmp_path, events))
    assert [w["action_ordinal"] for w in windows] == [1, 5, 7, 8]
    assert windows[1]["image_source"]["producer_ordinal"] == 1
    assert [e["ordinal"] for e in windows[1]["events"]] == [5, 6]
    assert windows[1]["events"][1]["images"] == events[5]["images"]
    assert windows[-1]["image_source"] is None  # inherited URL does not turn references into app images
    assert len(windows[2]["events"]) == 1


def test_idless_legacy_results_stop_before_next_call(tmp_path):
    events = [action(1, None), observation(2, "another-call"),
              observation(3, None, text="legacy result"), action(4, None), observation(5, None)]
    windows = extract_candidate_windows(write_run(tmp_path, events))
    assert [[e["ordinal"] for e in w["events"]] for w in windows] == [[1, 3], [4, 5]]


def test_visual_routing_preserves_attempts_images_and_producer_references(tmp_path):
    events = [
        action(1, "capture", tool="Bash", payload={"command": "playwright-cli screenshot --filename=/tmp/result.png"}),
        observation(2, "capture", is_error=True, text="screenshot failed"),
        action(3, "read", tool="Read", category="view-image", payload={"file_path": "/tmp/result.png"}),
        observation(4, "read", is_error=True, text="file not found"),
        action(5, "state"),
        observation(6, "state", images=[{"path": "missing.png"}]),
        action(7, "native", tool="browser_take_screenshot"),
        observation(8, "native", is_error=True, text="timeout"),
        action(9, "helper", tool="Bash", category="inspect", payload={"command": "node helper.js http://localhost:3000/ /tmp/output.png"}),
        observation(10, "helper", text="done"),
        action(11, "later-read", tool="Read", category="view-image", payload={"file_path": "/tmp/output.png"}),
        observation(12, "later-read", images=[{"path": "also-missing.png"}]),
        action(13, "capture-code", tool="Bash", payload={"command": "node -e \"await page.screenshot({path:'out.png'})\""}),
        observation(14, "capture-code", is_error=True, text="browser unavailable"),
    ]
    windows = extract_candidate_windows(write_run(tmp_path, events))
    original = copy.deepcopy(windows)
    visual, other = split_visual_windows(windows)
    assert visual == original and other == []
    assert windows == original


def test_text_browser_checks_and_prior_visual_claims_are_record_only(tmp_path):
    events = [
        {"ordinal": 0, "kind": "model_text", "text": "The screenshot looks good. Now test routes."},
        action(1, "console", tool="Bash", payload={"command": "playwright-cli console"}),
        observation(2, "console", text="0 errors"),
        action(3, "dom", payload={"expression": "document.querySelector('button') !== null"}),
        observation(4, "dom", text="true"),
        action(5, "http", category="inspect", tool="Bash", payload={
            "command": "curl -I http://localhost:9000/screenshot.png", "description": "screenshot asset check"}),
        observation(6, "http", text="HTTP 200"),
    ]
    windows = extract_candidate_windows(write_run(tmp_path, events))
    visual, other = split_visual_windows(windows)
    assert visual == [] and other == windows
    assert split_visual_windows([]) == ([], [])


def test_selection_batches_merge_in_order_without_modifying_evidence_and_cache(tmp_path):
    windows = [{"id": f"window-{i}", "events": [{"ordinal": i, "text": "evidence" * 40,
               "images": [{"path": "nonexistent.png"}]}]} for i in range(5)]
    original = copy.deepcopy(windows)
    judge = client(tmp_path)
    seen = []

    def respond(prompt, images):
        assert images == []
        batch = json.loads(prompt.split("WINDOWS:\n", 1)[1])
        seen.append(batch)
        ids = [w["id"] for w in reversed(batch)]
        return json.dumps({"keep_ids": ids + ids})

    judge._request = Mock(side_effect=respond)
    budget = len(SYSTEM_PROMPT) + len(verification_filter_prompt(windows[:2]))
    expected = [w["id"] for w in windows]
    assert select_verification_windows(windows, judge, max_input_chars=budget) == expected
    assert [len(batch) for batch in seen] == [2, 2, 1]
    assert windows == original
    assert select_verification_windows(windows, judge, max_input_chars=budget) == expected
    assert judge._request.call_count == 3  # second pass uses the existing cache
    records = [json.loads(p.read_text()) for p in tmp_path.glob("*.json")]
    assert all(r["stage"] == "verification_filter" and r["image_paths"] == [] and r["raw"] for r in records)


@pytest.mark.parametrize("raw", [
    "not json", '```json\n{"keep_ids":[]}\n```', 'prefix {"keep_ids":[]}',
    '[]', '{}', '{"keep_ids":null}', '{"keep_ids":"window-1"}',
    '{"keep_ids":["outside"]}', '{"keep_ids":[1]}',
    '{"keep_ids":[{}]}', '{"keep_ids":[],"reason":"extra"}',
])
def test_invalid_responses_raise_once_and_preserve_raw(tmp_path, raw):
    judge = client(tmp_path)
    judge._request = Mock(return_value=raw)
    with pytest.raises(ValueError):
        select_verification_windows([{"id": "window-1", "events": []}], judge)
    assert judge._request.call_count == 1
    assert json.loads(next(tmp_path.glob("*.json")).read_text())["raw"] == raw


def test_batch_cannot_select_another_batch_and_errors_do_not_fallback(tmp_path):
    windows = [{"id": f"window-{i}", "events": []} for i in range(3)]
    budget = len(SYSTEM_PROMPT) + len(verification_filter_prompt(windows[:2]))
    judge = client(tmp_path)
    judge._request = Mock(return_value='{"keep_ids":["window-2"]}')
    with pytest.raises(ValueError, match="current batch"):
        select_verification_windows(windows, judge, max_input_chars=budget)
    judge._request = Mock(side_effect=RuntimeError("network failed"))
    with pytest.raises(RuntimeError, match="network failed"):
        select_verification_windows([{"id": "different", "events": []}], judge)
    judge._request.assert_called_once()
    judge._request.reset_mock()
    with pytest.raises(ValueError, match="exceeds"):
        select_verification_windows([{"id": "large", "events": ["x" * 5000]}], judge, max_input_chars=budget)
    judge._request.assert_not_called()
    assert select_verification_windows([], judge) == []


def test_extract_cli_without_task_root_or_scoring_and_offline_without_config(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("score_vsv", root / "scripts/vision2web/score_vsv.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    run = write_run(tmp_path, [action(1, "a"), observation(2, "a"),
                               action(3, "b", tool="browser_take_screenshot")])
    original = json.loads(run.read_text())
    original["result"] = {"versions": {"P_final": {"path": "versions/final", "program_sha256": "recorded"}},
                          "workspace_artifact": "workspace"}
    original["raw_links"] = ["raw/events.jsonl"]
    run.write_text(json.dumps(original))
    out = tmp_path / "offline"
    base = ["score_vsv.py", "--extract-only", "--run-json", str(run), "--output-dir"]
    monkeypatch.setattr(cli, "extract_episodes", Mock(side_effect=AssertionError("old episodes must not run")))
    monkeypatch.setattr(cli, "infer_task_root", Mock(side_effect=AssertionError("task must not load")))
    monkeypatch.setattr("sys.argv", base + [str(out), "--offline", "--config", str(tmp_path / "absent")])
    assert cli.main() == 0
    candidates = json.loads((out / "candidate_windows.json").read_text())
    assert candidates["filter_status"] == "rule_candidates_only" and candidates["keep_ids"] is None
    assert not (out / "verification_windows.json").exists()
    assert not (out / "visual_verification_windows.json").exists()
    assert not (out / "other_verification_windows.json").exists()

    out = tmp_path / "selected"
    judge = client(tmp_path / "cache")
    judge._request = Mock(return_value=json.dumps({
        "rounds": [{"candidate_ids": [f"window-{o}"], "policy_event_ids": [],
                    "judgment_event_ids": [], "context_event_ids": []} for o in [1, 3]],
        "excluded_candidate_ids": [], "repair_links": [],
    }))
    build = Mock(return_value=judge)
    monkeypatch.setattr(cli, "build_client", build)
    monkeypatch.setattr("sys.argv", base + [str(out), "--primary-profile", "override"])
    assert cli.main() == 0
    assert build.call_args.args[1] == "override"
    result = json.loads((out / "verification_rounds.json").read_text())
    assert [v for e in result["episodes"] for v in e["candidate_ids"]] == ["window-1", "window-3"]
    assert result["episode_count"] == 2
    ledger = {e["ordinal"]: e for e in result["events"]}
    assert [[ledger[o] for o in w["event_ids"]] for w in result["windows"]] == [w["events"] for w in candidates["windows"]]
    assert result["source_run"] == str(run.resolve())
    assert result["versions"] == original["result"]["versions"]
    assert result["workspace_artifact"] == "workspace" and result["raw_links"] == original["raw_links"]
    visual = json.loads((out / "visual_verification_rounds.json").read_text())
    other = json.loads((out / "other_verification_rounds.json").read_text())
    assert visual["intended_use"] == "visual_scoring_input" and other["intended_use"] == "text_scoring_input"
    assert visual["episodes"][0]["candidate_ids"] == ["window-3"]
    assert other["episodes"][0]["candidate_ids"] == ["window-1"]
    assert visual["versions"] == result["versions"] == other["versions"]
    assert result["image_input_count"] == 0 and result["image_episode_count"] == 0
    assert other["episodes"][0]["verification_kind"] == "non_visual"
    assert visual["non_visual_episode_count"] == 0
    assert other["visual_attempt_without_image_count"] == 0
    assert visual["visual_attempt_without_image_count"] == 1
    assert visual["source_rounds"] == other["source_rounds"] == str((out / "verification_rounds.json").resolve())
    assert judge._request.call_count == 1  # selection and association use one stage


def process_fixture(tmp_path):
    events = [
        {"ordinal": 0, "kind": "model_text", "text": "Check the layout."},
        action(1, "shot", tool="Bash", payload={"command": "node capture.js http://localhost:3000/ /tmp/out.png"}),
        observation(2, "shot", text="captured"),
        action(3, "read", category="view-image", tool="Read", payload={"file_path": "/tmp/out.png"}),
        observation(4, "read", images=[{"path": "missing.png"}]),
        {"ordinal": 5, "kind": "model_text", "text": "The header overlaps. Fix it."},
        action(6, "fix", category="edit", tool="Edit", payload={"file_path": "header.css", "new_string": "new"}),
        action(7, "unrelated", category="edit", tool="Write"),
        observation(8, "unrelated", text="unrelated deployment script"),
        observation(9, "fix", text="edited header"),
        {"ordinal": 10, "kind": "model_text", "text": "Now recheck the header and shared footer."},
        action(11, "recheck"),
        observation(12, "recheck", images=[{"path": "missing-again.png"}]),
        {"ordinal": 13, "kind": "model_text", "text": "Looks fixed."},
        action(14, "failed", tool="browser_take_screenshot"),
        observation(15, "failed", is_error=True, text="timeout"),
    ]
    run = write_run(tmp_path, events)
    return run, events, extract_candidate_windows(run)


def test_processes_link_edits_and_distant_rechecks_preserve_shared_evidence_and_failure(tmp_path):
    run, events, windows = process_fixture(tmp_path)
    original = copy.deepcopy(windows)
    groups = [
        {"window_ids": ["window-11", "window-3", "window-1"], "context_action_ordinals": [6]},
        {"window_ids": ["window-14", "window-11"], "context_action_ordinals": []},
    ]
    judge = client(tmp_path / "cache")
    judge._request = Mock(return_value=json.dumps({"processes": groups}))
    result = extract_verification_processes(run, windows, judge)
    assert windows == original and result["windows"] == original
    assert result["process_count"] == 2 and result["image_input_count"] == 2
    assert result["shared_window_ids"] == ["window-11"]
    assert result["processes"][0]["window_ids"] == ["window-1", "window-3", "window-11"]
    assert result["processes"][0]["event_ordinals"] == [0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 12, 13]
    assert 15 in result["processes"][1]["event_ordinals"]  # failed attempts survive
    assert result["events"] == [e for e in events if e["ordinal"] not in {7, 8}]
    assert len(result["events"]) == len({e["ordinal"] for e in result["events"]})
    prompt, images = judge._request.call_args.args
    assert images == [] and 'unrelated deployment script' in prompt
    assert extract_verification_processes(run, windows, judge) == result
    judge._request.assert_called_once()  # association reuses JudgeClient cache


@pytest.mark.parametrize("raw", [
    'not json', '```json\n{"processes":[]}\n```', '{"processes":[],"reason":"extra"}',
    '{"processes":[]}', '{"processes":[{"window_ids":["window-999"],"context_action_ordinals":[]}]}',
    '{"processes":[{"window_ids":["window-3"],"context_action_ordinals":[]}]}',
    '{"processes":[{"window_ids":["window-1","window-3","window-11","window-14"],"context_action_ordinals":[999]}]}',
    '{"processes":[{"window_ids":["window-1","window-1"],"context_action_ordinals":[]}]}',
    '{"processes":[{"window_ids":null,"context_action_ordinals":[]}]}',
])
def test_invalid_process_references_do_not_become_valid_groups(tmp_path, raw):
    run, _, windows = process_fixture(tmp_path)
    judge = client(tmp_path / "cache")
    judge._request = Mock(return_value=raw)
    with pytest.raises(ValueError):
        extract_verification_processes(run, windows, judge)
    judge._request.assert_called_once()


def test_process_budget_and_network_errors_are_explicit_and_no_visual_skips_judge(tmp_path):
    run, _, windows = process_fixture(tmp_path)
    judge = client(tmp_path / "cache")
    judge._request = Mock(side_effect=RuntimeError("offline"))
    with pytest.raises(ValueError, match="exceeds"):
        extract_verification_processes(run, windows, judge, max_input_chars=10)
    judge._request.assert_not_called()
    with pytest.raises(RuntimeError, match="offline"):
        extract_verification_processes(run, windows, judge)
    assert extract_verification_processes(run, [], judge)["process_count"] == 0


def test_process_viewer_embeds_available_images_and_preserves_missing_references(tmp_path):
    import base64
    import re

    run, events, _ = process_fixture(tmp_path)
    image = tmp_path / "later.png"
    image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="))
    events[12]["images"] = [{"path": str(image)}]
    events[13]["text"] = 'Recorded text </script><script>alert("no")</script>'
    run = write_run(tmp_path, events)
    windows = extract_candidate_windows(run)
    judge = client(tmp_path / "cache")
    judge._request = Mock(return_value=json.dumps({"processes": [
        {"window_ids": [w["id"] for w in windows], "context_action_ordinals": [6]},
    ]}))
    packet = extract_verification_processes(run, windows, judge)
    packet_path = tmp_path / "processes.json"
    packet_path.write_text(json.dumps(packet))
    script = Path(__file__).resolve().parents[1] / "scripts/vision2web/render_vsv_windows.py"
    spec = importlib.util.spec_from_file_location("render_vsv", script)
    viewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(viewer)
    output = tmp_path / "review.html"
    result = viewer.render(packet_path, output)
    assert result["processes"] == 1 and result["embedded_images"] == 1
    assert result["missing_images"] == ["missing.png"]
    html = output.read_text()
    embedded = json.loads(re.search(r'<script id="vsv-data" type="application/json">(.*?)</script>', html, re.S).group(1))
    assert embedded["packet"] == packet
    assert len(embedded["items"][0]["images"]) == 2
    assert embedded["items"][0]["error_ordinals"] == [15]
    assert '</script><script>alert' not in html
    assert '<html lang="en">' in html
    assert embedded["items"][0]["title"].startswith("Process ")
    assert "Source and review notes" in html
