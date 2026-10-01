import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from multimodalcode.vsv_eval.episodes import extract_episodes
from multimodalcode.vsv_eval.visual_judgment import build_packet, candidates, evaluate_episode, validate
from multimodalcode.vsv_eval.judge import JudgeClient, JudgeProfile


def test_api_failure_is_not_an_insufficient_evidence_judgment():
    from multimodalcode.vsv_eval.visual_judgment import summarize
    result = summarize([
        {"judgment_present": None, "assessment": "insufficient_evidence"},
        {"judgment_present": True, "assessment": "insufficient_evidence"},
        {"judgment_present": False, "assessment": "insufficient_evidence"},
    ])
    assert result["assessment_counts"] == {"insufficient_evidence": 1}
    assert result["unavailable_count"] == 1 and result["not_a_judgment_count"] == 1


def test_trajectory_with_no_checks_has_no_visual_judgments_not_an_error(tmp_path):
    from multimodalcode.vsv_eval.visual_judgment import score_visual_trajectory
    source = tmp_path / "run.json"
    source.write_text(json.dumps({"case_id": "webpage/empty", "model": "test", "timeline": []}))
    task = tmp_path / "task"
    task.mkdir()
    (task / "prompt.txt").write_text("Implement the page.")
    result = score_visual_trajectory(source, task, tmp_path / "out")
    assert result["episodes"] == []
    assert result["summary"]["judgment_count"] == 0
    assert result["summary"]["assessment_counts"] == {}
    with pytest.raises(ValueError, match="No matching episode"):
        score_visual_trajectory(source, task, tmp_path / "bad", episode_id="missing")


def fixture():
    root = Path(__file__).resolve().parents[1] / "data/vision2web/vsv_eval_fixtures/frontend__smartrecruiters__claude-opus-4-8__self_verify"
    source = root / "trajectory/run.json"
    if not source.is_file():
        pytest.skip("Local trajectory unavailable")
    return source, root / "agent_visible", extract_episodes(source)["episodes"][0]


def test_real_packets_have_no_future_events_and_keep_inspect_evidence():
    source, task, episode = fixture()
    packets = {i: build_packet(episode, i, source, task) for i in (237, 254, 259)}
    for ordinal, packet in packets.items():
        assert all(r["ordinal"] < ordinal for r in packet["events_before_judgment"])
        assert all(r["ordinal"] < ordinal for r in packet["image_order"] if "ordinal" in r)
        assert [Path(r["path"]).name for r in packet["image_order"] if r["kind"] == "reference_prototype"] == ["homepage.jpg"]
        assert "workflow" not in packet
    assert 258 not in {r["ordinal"] for r in packets[254]["events_before_judgment"]}
    assert any(r["ordinal"] == 258 and "darkest pixel 255" in r["text"] for r in packets[259]["events_before_judgment"])
    assert 260 not in {r["ordinal"] for r in packets[259]["events_before_judgment"]}
    assert len(packets[237]["images"]) == 2  # One original prototype, one actual observation.
    assert len(packets[259]["images"]) == 3


def test_locator_future_messages_cannot_leak_into_scoring(tmp_path):
    source, task, episode = fixture()

    class Judge:
        profile = SimpleNamespace(model="fake")

        def judge(self, stage, prompt, images):
            if stage == "visual_locator":
                assert images == []
                return {"parsed": {"judgment_ordinals": [237, 254, 259]}}
            packet = json.loads(prompt.split("EVIDENCE PACKET:\n", 1)[1])
            ordinal = packet["judgment_ordinal"]
            assert all(r["ordinal"] < ordinal for r in packet["events_before_judgment"])
            assert images
            return {"parsed": {"judgment_present": True, "assessment": "partially_correct", "evidence_ordinals": [235], "reason": "A finding is supported but the cause is not established."}}

    result = evaluate_episode(episode, source, task, tmp_path, Judge(), selected=[237, 254, 259])
    assert len(result["judgments"]) == 3
    assert result["status"] == "evaluated"
    assert result["assessment_counts"] == {"partially_correct": 3}
    assert len(list((tmp_path / "visual_inputs").glob("*.json"))) == 4


def test_future_and_policy_text_citations_are_rejected():
    source, task, episode = fixture()
    packet = build_packet(episode, 259, source, task)
    for refs in ([260], [254], [999]):
        result = validate({"parsed": {"judgment_present": True, "assessment": "correct", "evidence_ordinals": refs, "reason": "test"}}, packet)
        assert result["assessment"] == "insufficient_evidence" and result["validation_errors"]
    assert validate({"error": "network failure"}, packet)["judgment_present"] is None


def test_image_metadata_is_not_policy_judgment_and_omissions_are_explicit():
    source, task, episode = fixture()
    assert 236 not in {r["ordinal"] for r in candidates(episode)}
    packet = build_packet(episode, 259, source, task, max_images=1)
    assert len(packet["omitted_images"]) == 1
    assert packet["omitted_images"][0]["ordinal"] == 235


def test_bad_locator_is_not_treated_as_success(tmp_path):
    source, task, episode = fixture()
    class Judge:
        def judge(self, *args):
            return {"parsed": {"judgment_ordinals": [999]}}
    result = evaluate_episode(episode, source, task, tmp_path, Judge(), selected=[237])
    assert result["status"] == "not_evaluable"
    assert result["selected_but_not_located"] == [237]
    assert result["judgments"] == []


def test_locator_excludes_pure_text_tests_but_retains_cross_episode_visual_claims():
    from multimodalcode.vsv_eval.visual_judgment import locator_prompt
    prompt = locator_prompt([])
    assert "Previously viewing an image does not make all later text checks visual" in prompt
    assert "programmatic functional-test results" in prompt
    assert "interpretation may refer to an earlier image" in prompt


def test_malformed_json_keeps_entire_raw_response(tmp_path, monkeypatch):
    client = JudgeClient(JudgeProfile(name="test", provider="openai-compatible", model="test",
                                     base_url="http://localhost/v1", api_key_env="UNUSED"), tmp_path)
    raw = '{"reason":"bad "quote"' + "x" * 1000
    monkeypatch.setattr(client, "_request", lambda *_: raw)
    record = client.judge("visual_judgment", "test", [])
    assert record["raw"] == raw and record["error"]
    assert next(tmp_path.glob("*.json")).is_file()


def test_selected_positions_do_not_bypass_locator(tmp_path):
    source, task, episode = fixture()
    class Judge:
        def judge(self, *args):
            return {"parsed": {"judgment_ordinals": []}}
    result = evaluate_episode(episode, source, task, tmp_path, Judge(), selected=[237])
    assert result["selected_but_not_located"] == [237]
    assert not result["judgments"]


def test_labeled_request_contains_exact_reference_and_observation_bytes(tmp_path, monkeypatch):
    import base64
    source, task, episode = fixture()
    packet = build_packet(episode, 237, source, task)
    client = JudgeClient(JudgeProfile(name="visual", provider="openai-compatible", model="test",
                                     base_url="http://localhost/v1", api_key_env="UNUSED", label_images=True), tmp_path)
    captured = {}
    def post(endpoint, payload, headers):
        captured.update(payload)
        return {"choices": [{"message": {"content": "{}"}}]}
    monkeypatch.setattr(client, "_post", post)
    client._openai("packet", packet["images"])
    blocks = captured["messages"][1]["content"]
    assert "ATTACHED IMAGE 1: homepage.jpg" in blocks[1]["text"]
    assert "ATTACHED IMAGE 2:" in blocks[3]["text"]
    for block, path in zip((blocks[2], blocks[4]), packet["images"]):
        assert base64.b64decode(block["image_url"]["url"].split(",", 1)[1]) == Path(path).read_bytes()
    assert [r["attachment_index"] for r in packet["image_order"]] == [1, 2]


def test_cross_episode_judgment_307_retains_earlier_homepage_without_future_evidence(tmp_path):
    from multimodalcode.vsv_eval.visual_judgment import source_context
    source, task, _ = fixture()
    original = source.read_bytes()
    episode = extract_episodes(source)["episodes"][1]
    assert not any(r.get("images") for r in episode["events"])
    assert 307 not in {r["ordinal"] for r in candidates(episode)}
    history = source_context(source)
    assert 307 in {r["ordinal"] for r in candidates(episode, history)}
    packet = build_packet(episode, 307, source, task, history=history)
    assert "stray brandmark" in packet["policy_quote"]
    assert any(r.get("ordinal") == 252 for r in packet["image_order"])
    assert all(r["ordinal"] < 307 for r in packet["events_before_judgment"])
    assert all(r.get("ordinal", -1) < 307 for r in packet["image_order"])
    assert "workflow" not in packet
    assert "recorded_workspace_sha256" in next(r for r in packet["image_order"] if r.get("ordinal") == 252)
    (tmp_path / "judgment-307-packet.json").write_text(json.dumps(packet, indent=2))
    assert source.read_bytes() == original


def test_cross_episode_image_budget_records_unrepresented_pages():
    source, task, _ = fixture()
    episode = extract_episodes(source)["episodes"][1]
    packet = build_packet(episode, 307, source, task, max_images=1)
    assert len([r for r in packet["image_order"] if r["kind"] == "policy_observation"]) == 1
    assert packet["omitted_images"] and packet["image_selection"]["unrepresented_pages"]


def test_reference_filename_overrides_stale_url_and_hash_route_is_supported(tmp_path):
    from multimodalcode.vsv_eval.visual_judgment import _references
    (tmp_path / "prototypes").mkdir()
    for name in ("homepage", "pricing", "about_us"):
        (tmp_path / "prototypes" / (name + ".jpg")).write_bytes(b"image")
    for url, filename, expected in (
        ("https://example.com", "screenshot_pricing_full.png", "pricing.jpg"),
        ("http://localhost:3000/", "screenshot_about_full.png", "about_us.jpg"),
        ("http://localhost:3000/#/pricing", "capture.png", "pricing.jpg"),
    ):
        result = _references({"ordinal": 5, "browser_url_context": url,
                              "source_action_payload": {"file_path": filename}}, tmp_path)
        assert Path(result["path"]).name == expected


def test_asset_contact_sheet_inside_span_is_not_application_observation(tmp_path):
    from multimodalcode.vsv_eval.visual_judgment import source_context
    timeline = []
    for filename, command in (
        ("/tmp/home.png", "playwright-cli goto http://localhost:3000/; playwright-cli screenshot --filename /tmp/home.png"),
        ("/tmp/assets.png", "python -c \"Image.new('RGB', (50,50)).save('/tmp/assets.png')\""),
        ("/tmp/recheck.png", "playwright-cli screenshot --filename /tmp/recheck.png"),
    ):
        image = tmp_path / Path(filename).name
        image.write_bytes(b"image")
        i = len(timeline)
        timeline += [
            {"ordinal": i, "kind": "action", "tool": "Bash", "category": "inspect", "browser_url_context": "http://localhost:3000/", "payload": {"command": command}},
            {"ordinal": i+1, "kind": "observation", "tool": "Bash", "text": "ok"},
            {"ordinal": i+2, "kind": "action", "tool": "Read", "category": "view-image", "payload": {"file_path": filename}},
            {"ordinal": i+3, "kind": "observation", "tool": "Read", "images": [{"path": str(image)}]},
        ]
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"case_id": "x", "timeline": timeline}))
    episode = extract_episodes(run)["episodes"][0]
    assert (episode["start_ordinal"], episode["end_ordinal"]) == (0, 11)
    assert episode["evidence_ordinals"] == [2, 10]
    assert source_context(run)["image_ids"] == {3, 11}


def test_shell_default_variable_capture_retains_each_consumed_image(tmp_path):
    from multimodalcode.vsv_eval.episodes import _output_mentioned
    command = 'outfile="/workspace/screenshot_${page:-home}_full.png"; google-chrome --screenshot="$outfile" http://localhost:3000/'
    assert _output_mentioned(command, "/workspace/screenshot_home_full.png")
    assert _output_mentioned(command, "/workspace/screenshot_pricing_full.png")
    assert not _output_mentioned(command, "/workspace/reference.png")


def test_judge_text_budget_keeps_full_archive_and_reports_omissions():
    from multimodalcode.vsv_eval.visual_judgment import judge_evidence
    packet = {"policy_quote": "The `handler` causes this.", "image_order": [],
              "events_before_judgment": [
                  {"ordinal": 1, "kind": "action", "tool": "Write", "text": "handler code"},
                  {"ordinal": 2, "kind": "observation", "text": "unrelated " * 1000},
                  {"ordinal": 3, "kind": "observation", "text": "runtime result"},
              ]}
    original = json.dumps(packet)
    selected = judge_evidence(packet, max_history_chars=500)
    assert [r["ordinal"] for r in selected["events_before_judgment"]] == [1, 3]
    assert selected["text_selection"]["omitted_event_ordinals"] == [2]
    assert selected["text_selection"]["selected_chars"] <= 500
    assert json.dumps(packet) == original
