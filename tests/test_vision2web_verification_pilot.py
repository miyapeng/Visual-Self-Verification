from __future__ import annotations

import json
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/vision2web/visual_self_verification_pilot_20.json"
PILOT = ROOT / "data/vision2web/pilots/visual_self_verification_20"


def test_pilot_is_frozen_and_balanced_across_levels():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    cases = config["cases"]
    assert {level: len(rows) for level, rows in cases.items()} == {
        "Level 1": 6,
        "Level 2": 8,
        "Level 3": 6,
    }
    flattened = [case_id for rows in cases.values() for case_id in rows]
    assert len(flattened) == len(set(flattened)) == 20
    assert [row["name"] for row in config["trajectory_conditions"]["openhands"]] == [
        "official",
        "browser_enabled",
        "guided_vsv",
    ]
    assert [row["name"] for row in config["trajectory_conditions"]["claude_code"]] == [
        "official",
        "guided_vsv",
    ]


def test_capacity_manifest_contains_only_public_probe_inputs():
    rows = [
        json.loads(line)
        for line in (PILOT / "cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 20
    for row in rows:
        assert row["workflow_visible_to_model"] is False
        assert Path(row["program_path"], "start.sh").is_file()
        assert not Path(row["program_path"], "workflow.json").exists()
        assert "official_evaluator" not in row


def test_checkpoint_protocol_is_distinct_from_archived_probes():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    current = config["checkpoint_protocol"]
    assert current["name"] == "checkpoint_verify"
    assert (ROOT / current["prompt"]).is_file()
    assert (ROOT / current["entrypoint"]).is_file()
    assert [row["name"] for row in config["additional_diagnostic_protocols"]] == [
        "verification_handoff"
    ]
    assert {row["name"] for row in config["archived_diagnostic_protocols"]} == {
        "elicited_same_context_after_finish", "fresh_context_verify_only"
    }
    assert all(row["status"].startswith("archived") for row in config["archived_diagnostic_protocols"])


def test_forced_probe_does_not_consume_private_workflow():
    runner = (
        ROOT / "scripts/vision2web/run_verification_capacity_probe.py"
    ).read_text(encoding="utf-8")
    prompt = (
        ROOT / "prompts/vision2web/fresh_context_verification_probe.txt"
    ).read_text(encoding="utf-8")
    assert "workflow_shape" not in runner
    assert "workflow.json" in runner  # absence guard only
    assert "read_text" not in runner.split(
        'if (workspace / "workflow.json").exists()'
    )[1].split("def count_browser_events", 1)[0]
    assert "Do not modify any file" in prompt
    assert "one coherent functional objective at a time" in prompt
    assert "do not inspect the whole site at once" in prompt
    assert "/workspace/start.sh in the background" in prompt


def test_same_context_check_can_launch_the_application_without_external_orchestration():
    prompt = (
        ROOT / "prompts/vision2web/same_context_self_check.txt"
    ).read_text(encoding="utf-8")
    assert "/workspace/start.sh in the background" in prompt
    assert "wait until the URL responds" in prompt
    assert "inspect the launch log" in prompt
    assert "available browser interface" in prompt
    assert "does not count as a visual check" in prompt
    assert "You decide which objectives are useful" in prompt


def test_background_submitter_uses_the_exact_frozen_pilot20_selection():
    source = ROOT / "scripts/vision2web/submit_self_verify.py"
    spec = importlib.util.spec_from_file_location("vision2web_submitter", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    selected = module.selected_cases({}, "pilot20")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    expected = [
        case_id
        for level in ("Level 1", "Level 2", "Level 3")
        for case_id in config["cases"][level]
    ]
    assert selected == expected
