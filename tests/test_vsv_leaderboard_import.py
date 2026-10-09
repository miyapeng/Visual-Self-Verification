import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts/vision2web"
sys.path.insert(0, str(SCRIPTS))
from import_leaderboard_trace import import_trace
from build_scaffold_model_comparison_site import save_image
from multimodalcode.vsv_eval.episodes import extract_candidate_windows


def test_omitted_image_is_a_recorded_input_without_pixels(tmp_path):
    source = tmp_path / "frontend/example/results/example.json"
    source.parent.mkdir(parents=True)
    events = [
        {"message": {"content": [{"type": "tool_use", "id": "read-1", "name": "Read",
                                 "input": {"file_path": "/tmp/current-page.png"}}]}},
        {"message": {"content": [{"type": "tool_result", "tool_use_id": "read-1", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                          "data": "<base64 data omitted, 154316 chars>"}}
        ]}]}},
    ]
    source.write_text(json.dumps({"conversation": events, "attempt_count": 1}))
    before = source.read_bytes()
    output = tmp_path / "imported"
    run = import_trace(source, output)
    action, observation = run["timeline"]
    assert run["case_id"] == "frontend/example"
    assert action["tool_call_id"] == observation["payload"]["tool_use_id"] == "read-1"
    assert observation["raw_line"] == 2
    assert observation["images"][0]["available"] is False
    assert not list(output.glob("assets/**/*"))
    assert extract_candidate_windows(output / "run.json")
    assert source.read_bytes() == before


def test_invalid_base64_is_not_written(tmp_path):
    assert save_image("<base64 data omitted, 100 chars>", "image/png", tmp_path) is None
    assert not list(tmp_path.iterdir())


def test_user_image_metadata_is_not_agent_narration(tmp_path):
    source = tmp_path / 'frontend/example/results/example.json'
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({'conversation': [
        {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'read', 'name': 'Read',
                                                     'input': {'file_path': '/tmp/page.png'}}]}},
        {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'read',
                                                'content': 'Image returned.'}]}},
        {'type': 'user', 'message': {'content': [{'type': 'text', 'text': '[Image: resized]'}]}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'The heading is clipped.'}]}},
    ]}))
    run = import_trace(source, tmp_path / 'out')
    assert [e['kind'] for e in run['timeline']] == ['action', 'observation', 'tool_metadata', 'model_text']
    windows = extract_candidate_windows(tmp_path / 'out/run.json')
    assert [e['ordinal'] for e in windows[0]['events']] == [0, 1, 2, 3]


def test_multiple_attempts_require_explicit_reconstruction(tmp_path):
    source = tmp_path / "trace.json"
    source.write_text(json.dumps({"attempt_count": 2, "conversation": [{"type": "system"}]}))
    with pytest.raises(ValueError, match="Multiple attempts"):
        import_trace(source, tmp_path / "out")
