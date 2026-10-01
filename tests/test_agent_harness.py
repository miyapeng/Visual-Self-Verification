from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

from multimodalcode.agent_harness.cases import AgentCase, list_case_ids, load_case, prepare_workspace
from multimodalcode.agent_harness.cli import _export_vision_workspace
from multimodalcode.agent_harness import mini_runner
from multimodalcode.agent_harness.mini_runner import (
    _prepare_image_for_transport,
    image_tag,
)
from multimodalcode.agent_harness.openhands_runner import (
    build_openhands_command,
    has_finish_action,
    run_openhands,
)
from multimodalcode.agent_harness.registry import PROJECT_ROOT, SCAFFOLDS
from multimodalcode.agent_harness.trajectory import normalize_mini, normalize_openhands


def test_scaffold_mapping_is_explicit_and_thin():
    assert SCAFFOLDS["swe-mm"].scaffold == "mini-swe-agent"
    assert SCAFFOLDS["design2code"].scaffold == "mini-swe-agent"
    assert SCAFFOLDS["chartmimic"].scaffold == "mini-swe-agent"
    assert SCAFFOLDS["vision2web"].scaffold == "openhands-headless-cli"
    assert SCAFFOLDS["vision2web"].runtime_digest.startswith("sha256:")


def test_mini_runner_pins_tool_python_to_runner_environment():
    source = Path(mini_runner.__file__).read_text(encoding="utf-8")
    assert 'str(Path(sys.executable).resolve().parent)' in source


def test_swe_mm_official_profile_is_loaded_from_frozen_upstream_config():
    source = Path(mini_runner.__file__).read_text(encoding="utf-8")
    assert 'profile == "swebench-official"' in source
    assert 'config" / "benchmarks" / "swebench.yaml"' in source
    config = (
        PROJECT_ROOT
        / "scaffolds"
        / "mini_swe_agent"
        / "src"
        / "minisweagent"
        / "config"
        / "benchmarks"
        / "swebench.yaml"
    ).read_text(encoding="utf-8")
    assert "step_limit: 250" in config
    assert "cost_limit: 3." in config
    assert "timeout: 60" in config
    assert "parallel_tool_calls: true" in config


def test_swe_mm_official_experiment_manifest_pins_evaluator_and_dataset():
    manifest = json.loads(
        (PROJECT_ROOT / "configs" / "swe_mm" / "official_dev.json").read_text()
    )
    assert manifest["dataset"]["instances"] == 102
    assert manifest["scaffold"]["step_limit"] == 250
    assert manifest["scaffold"]["tool_mode"] == "native"
    assert manifest["evaluation"]["test_timeout_seconds"] == 1800


def test_scaffold_sources_are_owned_by_this_repository():
    for spec in SCAFFOLDS.values():
        assert spec.source_root is not None
        assert spec.source_root.resolve().is_relative_to(PROJECT_ROOT.resolve())
        assert (spec.source_root / "src").is_dir()


def test_vendored_openhands_official_command_is_unmodified_upstream_cli():
    runtime_root = SCAFFOLDS["vision2web"].source_root
    assert runtime_root is not None
    command, env = build_openhands_command(
        "build the page", runtime_root=runtime_root, python_executable=sys.executable
    )
    assert command[:3] == [sys.executable, "-m", "openhands_cli.entrypoint"]
    assert command[-2:] == ["-t", "build the page"]
    python_paths = env["PYTHONPATH"].split(":")
    assert python_paths[0] == str((runtime_root / "src").resolve())


def test_vendored_openhands_research_command_is_explicitly_separate():
    runtime_root = SCAFFOLDS["vision2web"].source_root
    assert runtime_root is not None
    command, env = build_openhands_command(
        "build the page",
        runtime_root=runtime_root,
        profile="research",
        python_executable=sys.executable,
    )
    assert command[:3] == [
        sys.executable,
        "-m",
        "multimodalcode.agent_harness.openhands_entrypoint",
    ]
    python_paths = env["PYTHONPATH"].split(":")
    assert python_paths[0] == str((PROJECT_ROOT / "src").resolve())
    assert python_paths[1] == str((runtime_root / "src").resolve())


def test_openhands_research_adapter_explicitly_enables_local_multimodal_model():
    source = (
        PROJECT_ROOT
        / "src"
        / "multimodalcode"
        / "agent_harness"
        / "openhands_entrypoint.py"
    ).read_text(encoding="utf-8")
    assert 'os.environ.get("MULTIMODALCODE_FORCE_VISION")' in source
    assert "LLM._supports_vision" in source
    assert 'MULTIMODALCODE_MAX_OUTPUT_TOKENS", "8192"' in source


def test_openhands_official_success_does_not_require_finish_action(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("MULTIMODALCODE_FORCE_VISION", "1")
    monkeypatch.setenv("MULTIMODALCODE_MAX_INPUT_TOKENS", "123")
    executable = tmp_path / "fake-openhands"
    executable.write_text(
        "#!/bin/sh\n"
        "test -z \"${MULTIMODALCODE_FORCE_VISION:-}\" || exit 8\n"
        "test -z \"${MULTIMODALCODE_MAX_INPUT_TOKENS:-}\" || exit 9\n"
        "test \"$LLM_MODEL\" = openai/test-model || exit 10\n"
        "printf '%s\\n' '{\"kind\":\"MessageEvent\"}'\n"
        "touch start.sh\n"
    )
    executable.chmod(0o755)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    raw = tmp_path / "openhands.events.jsonl"
    stderr = tmp_path / "openhands.stderr.log"
    outcome = run_openhands(
        AgentCase("vision2web", "webpage/test", "webpage", "build it"),
        workspace,
        raw,
        stderr,
        model="openai/test-model",
        base_url="http://example.invalid/v1",
        api_key="EMPTY",
        timeout=10,
        runtime_root=SCAFFOLDS["vision2web"].source_root,
        profile="official",
        max_retries=0,
        require_vision=False,
        installed_executable=str(executable),
    )
    assert outcome["status"] == "success"
    assert outcome["finish_action"] is False
    assert outcome["attempt_count"] == 1


def test_openhands_finish_detection_requires_structured_action(tmp_path: Path):
    raw = tmp_path / "events.jsonl"
    raw.write_text(
        'plain text that mentions FinishAction\n'
        '{"kind":"MessageEvent","source":"agent","message":"finished"}\n',
        encoding="utf-8",
    )
    assert not has_finish_action(raw)
    raw.write_text(
        raw.read_text(encoding="utf-8")
        + '{"kind":"ActionEvent","tool_name":"finish","action":{"kind":"FinishAction"}}\n',
        encoding="utf-8",
    )
    assert has_finish_action(raw)


def test_frozen_case_counts():
    assert len(list_case_ids("swe-mm")) == 102
    assert len(list_case_ids("design2code")) == 484
    assert len(list_case_ids("chartmimic")) == 1200
    assert len(list_case_ids("vision2web")) == 193


def test_design2code_never_exposes_reference_code(tmp_path: Path):
    case = load_case("design2code", "0")
    serialized = json.dumps(case.to_dict())
    assert "reference" not in case.metadata
    assert "<!DOCTYPE html>" not in serialized
    workspace = prepare_workspace(case, tmp_path / "workspace", tmp_path / "case.json")
    assert [path.name for path in workspace.iterdir()] == ["rick.jpg"]
    assert workspace.joinpath("rick.jpg").read_bytes() == (
        PROJECT_ROOT / "evaluate" / "design2code" / "Design2Code" / "prompting" / "rick.jpg"
    ).read_bytes()


def test_chartmimic_agent_visible_inputs_exclude_oracle_code():
    direct = load_case("chartmimic", "direct_600/line_1")
    customized = load_case("chartmimic", "customized_600/line_1")
    assert direct.image_paths[0].name == "line_1.png"
    assert "age_groups" in customized.prompt
    assert "code" not in customized.metadata
    assert customized.metadata["width"] == 8.0


def test_image_tag_is_a_mini_multimodal_message(tmp_path: Path):
    image = tmp_path / "tiny.png"
    image.write_bytes(b"png-bytes")
    tag = image_tag(image)
    assert tag.startswith("<MSWEA_MULTIMODAL_CONTENT>")
    assert "data:image/png;base64," in tag


def test_swe_mm_transport_skips_non_images_without_modifying_source(tmp_path: Path):
    source = tmp_path / "demo.html"
    source.write_text("<html>not an image</html>", encoding="utf-8")
    original = source.read_bytes()
    tag, record = _prepare_image_for_transport(source)
    assert tag is None
    assert record["action"] == "skipped_non_image"
    assert source.read_bytes() == original


def test_swe_mm_transport_removes_only_corrupt_ancillary_png_chunk(tmp_path: Path):
    def chunk(name: bytes, payload: bytes, *, corrupt: bool = False) -> bytes:
        crc = zlib.crc32(name + payload) & 0xFFFFFFFF
        if corrupt:
            crc ^= 1
        return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", crc)

    # A syntactically complete 1x1 PNG. The optional iCCP CRC is intentionally
    # bad, while IHDR/IDAT/IEND and their encoded pixel data remain untouched.
    raw = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"iCCP", b"profile\x00\x00broken", corrupt=True)
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )
    source = tmp_path / "corrupt-profile.png"
    source.write_bytes(raw)
    tag, record = _prepare_image_for_transport(source)
    assert tag is not None
    assert record["action"] == "normalized_invalid_ancillary_metadata"
    assert record["removed_png_chunks"] == ["iCCP"]
    assert record["source_sha256"] != record["transport_sha256"]
    assert source.read_bytes() == raw


def test_visual_tool_source_cannot_trigger_multimodal_parser():
    source = (PROJECT_ROOT / "scripts" / "agents" / "visual_tool.py").read_text(encoding="utf-8")
    assert "<MSWEA_MULTIMODAL_CONTENT>" not in source
    assert "</MSWEA_MULTIMODAL_CONTENT>" not in source


def test_design2code_evaluator_pins_screenshot_python():
    source = (PROJECT_ROOT / "src" / "multimodalcode" / "official_design2code.py").read_text(
        encoding="utf-8"
    )
    assert 'Path(sys.executable).resolve().parent' in source


def test_vision_workspace_matches_official_copy_contract(tmp_path: Path):
    case = load_case("vision2web", "frontend/afl")
    workspace = prepare_workspace(case, tmp_path / "afl", tmp_path / "afl-case.json")
    assert (workspace / "prototypes" / "homepage.jpg").is_file()
    assert (workspace / "resources").is_dir()
    assert (workspace / "prompt.txt").is_file()
    assert not (workspace / "workflow.json").exists()
    assert not (workspace / ".mmcode_case.json").exists()


def test_vision_workspace_is_restaged_when_persistent_marker_outlives_container(tmp_path: Path):
    case = load_case("vision2web", "webpage/abc")
    marker = tmp_path / "persistent" / "case.json"
    first_workspace = prepare_workspace(case, tmp_path / "container-1", marker)
    assert (first_workspace / "prototypes").is_dir()

    second_workspace = tmp_path / "container-2"
    second_workspace.mkdir()
    prepare_workspace(case, second_workspace, marker)
    assert (second_workspace / "prototypes" / "desktop.jpg").is_file()


def test_vision_workspace_export_links_frozen_inputs(tmp_path: Path):
    case = load_case("vision2web", "webpage/abc")
    workspace = prepare_workspace(case, tmp_path / "ephemeral")
    (workspace / "index.html").write_text("generated", encoding="utf-8")
    artifact = _export_vision_workspace(case, workspace, tmp_path / "run")
    assert (artifact / "index.html").read_text() == "generated"
    assert (artifact / "prototypes").is_symlink()
    assert (artifact / "prototypes" / "desktop.jpg").is_file()


def test_trajectory_normalizers_preserve_raw_and_remove_data_payload(tmp_path: Path):
    raw_mini = tmp_path / "raw.json"
    raw_mini.write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "task"},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}},
                        ],
                    }
                ],
                "info": {},
            }
        )
    )
    normalized_mini = tmp_path / "mini.json"
    normalize_mini(raw_mini, normalized_mini, {"benchmark": "x", "case_id": "1"})
    text = normalized_mini.read_text()
    assert "sha256=" in text
    assert "data:image/png;base64" not in text

    raw_oh = tmp_path / "oh.jsonl"
    raw_oh.write_text(
        '{"type":"action","source":"agent","content":"ls"}\n'
        '{"kind":"ObservationEvent","source":"environment","observation":'
        '{"content":[{"type":"image","image_urls":'
        '["data:image/png;base64,YQ=="]}]}}\n'
        '{"kind":"ConversationErrorEvent","source":"environment",'
        '"code":"LLMBadRequestError","detail":"bad tool config"}\n'
        'not-json\n'
    )
    normalized_oh = tmp_path / "oh.json"
    result = normalize_openhands(raw_oh, normalized_oh, {"benchmark": "x", "case_id": "2"})
    assert len(result["events"]) == 3
    assert "sha256=" in result["events"][1]["text"]
    assert "base64" not in result["events"][1]["text"]
    assert result["events"][2]["kind"] == "ConversationErrorEvent"
    assert result["events"][2]["text"] == "bad tool config"
