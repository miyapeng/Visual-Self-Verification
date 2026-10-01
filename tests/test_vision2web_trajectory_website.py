from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/vision2web/build_trajectory_website.py"


def load_module():
    spec = importlib.util.spec_from_file_location("vision2web_trajectory_site", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_site_samples_are_vision2web_only_and_do_not_read_private_workflow():
    module = load_module()
    assert {row.framework for row in module.SAMPLES} == {"openhands", "claude_code"}
    assert all(row.case_id.split("/", 1)[0] in {"webpage", "frontend", "website"} for row in module.SAMPLES)
    source = SCRIPT.read_text(encoding="utf-8")
    assert "workflow.json" in source  # explicit non-use documentation
    assert "read_json(run_dir / \"workflow.json\"" not in source
    assert "evaluation" not in source.casefold()


def test_openhands_parser_keeps_all_attempts_and_real_browser_pixels(tmp_path: Path):
    module = load_module()
    sample = next(
        row
        for row in module.SAMPLES
        if row.framework == "openhands"
        and row.mode == "self_verify"
        and row.case_id == "webpage/classic-clashes"
    )
    run_dir = module.openhands_dir(sample)
    parsed = module.parse_openhands(sample, run_dir, tmp_path)
    assert len(parsed["attempts"]) == 2
    steps = [step for attempt in parsed["attempts"] for step in attempt["steps"]]
    assert any(step["tool"] == "browser_get_state" for step in steps)
    assert any(obs["images"] for step in steps for obs in step["observations"])
    assert parsed["behavior"]["deployed_app_visual_observed"] is True
    assert parsed["behavior"]["post_visual_edit"] is False
    assert len(parsed["raw_links"]) == 2


def test_generated_manifest_marks_missing_claude_as_pending(tmp_path: Path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "CLAUDE_ROOT", tmp_path / "missing-claude")
    sample = next(row for row in module.SAMPLES if row.framework == "claude_code")
    manifest, data = module.build_run(sample, tmp_path / "site")
    assert data is None
    assert manifest["availability"] == "pending"
    assert manifest["data_path"] is None
    assert "No Claude trajectory" in manifest["pending"]["note"]


def test_compact_payload_removes_binary_but_not_long_commands():
    module = load_module()
    command = "x" * 10000
    compact = module.compact_payload(
        {"command": command, "screenshot_data": "a" * 10000, "source": {"data": "b" * 10000}}
    )
    assert compact["command"] == command
    assert compact["screenshot_data"] == "<extracted 10000 base64 characters>"
    assert compact["source"]["data"].startswith("<binary payload omitted")
