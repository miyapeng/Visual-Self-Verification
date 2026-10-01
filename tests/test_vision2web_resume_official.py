import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts/vision2web"
spec = importlib.util.spec_from_file_location("resume_official", SCRIPT_ROOT / "resume_official.py")
module = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(SCRIPT_ROOT))
try:
    spec.loader.exec_module(module)
finally:
    sys.path.pop(0)


def result(root, label, case_id, status):
    folder = root / label / "agents/model/vision2web/claude_code/official" / module.controller.safe(case_id)
    folder.mkdir(parents=True)
    path = folder / "result.json"
    path.write_text(json.dumps({"case_id": case_id, "model": "model", "vision2web_mode": "official", "status": status}))
    return path


def test_only_preserve_success_and_keep_sources_unchanged(tmp_path):
    p = result(tmp_path, "old", "webpage/a", "success")
    before = p.read_bytes()
    result(tmp_path, "old", "webpage/b", "failed")
    config = {"sources": ["old"], "preserve_statuses": ["success"]}
    rows = module.selection(tmp_path, "model", config, ["webpage/a", "webpage/b", "webpage/c"])
    assert [r["selected"] for r in rows] == [False, True, True]
    assert p.read_bytes() == before


def test_overlapping_timeouts_are_not_two_cases_or_successes(tmp_path):
    for label in ("old", "old-v2"):
        result(tmp_path, label, "webpage/a", "timeout")
    config = {"sources": ["old", "old-v2"], "preserve_statuses": ["success", "timeout"]}
    rows = module.selection(tmp_path, "model", config, ["webpage/a", "webpage/b"])
    assert [r["selected"] for r in rows] == [False, True]
    assert [a["status"] for a in rows[0]["prior_attempts"]] == ["timeout", "timeout"]


def test_missing_result_marker_does_not_count_as_success(tmp_path):
    folder = tmp_path / "old/agents/model/vision2web/claude_code/official/webpage__a"
    folder.mkdir(parents=True)
    (folder / "generation-summary.json").write_text('{"result_status": "success"}')
    rows = module.selection(tmp_path, "model", {"sources": ["old"], "preserve_statuses": ["success"]}, ["webpage/a"])
    assert rows[0]["selected"]
    assert rows[0]["prior_attempts"][0]["status"] == "missing_result"


def test_missing_source_fails_instead_of_resubmitting_everything(tmp_path):
    with pytest.raises(FileNotFoundError):
        module.selection(tmp_path, "model", {"sources": ["missing"], "preserve_statuses": ["success"]}, ["webpage/a"])
