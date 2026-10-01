from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from multimodalcode.agent_harness.vision2web_trace import (
    capture_observation_binding,
)
from multimodalcode.vsv_eval.checkpoints import materialize_manifest, workspace_patch
from multimodalcode.vsv_eval.episodes import extract_episodes
from multimodalcode.vsv_eval.judge import JudgeClient, JudgeProfile, _json_object
from multimodalcode.vsv_eval.replay import compare_replays
from multimodalcode.vsv_eval.scoring import score_trajectory
from multimodalcode.vsv_eval.workflow import lexical_candidates, load_workflow


class _FakeJudge:
    def judge(self, stage: str, prompt: str, images: list[str]):
        if stage == "workflow_alignment":
            packet = json.JSONDecoder().raw_decode(prompt.split("EPISODE PACKET:\n", 1)[1])[0]
            groups = [{
                "group_id": group["group_id"],
                "status": "reasonable",
                "requirement_source": "task",
                "requirement_quote": "Click Buy and show the checkout page.",
                "requirement": "Checkout",
                "evidence_ordinals": [group["action_ordinal"]],
                "needs_visual_review": False,
                "reason": "task-relevant",
            } for group in packet["groups"]]
            first = packet["groups"][0]
            refs = [first["action_ordinal"], first["execution"]["evidence_ordinals"][0]]
            parsed = {"groups": groups, "coverage": [
                {"workflow_id": "0.0", "status": "full", "evidence_chains": [refs], "reason": "buy flow"}
            ]}
        elif stage == "visual_locator":
            rows = json.loads(prompt.split("MESSAGES:\n", 1)[1])
            parsed = {"judgment_ordinals": [r["ordinal"] for r in rows]}
        elif stage == "visual_judgment":
            packet = json.loads(prompt.split("EVIDENCE PACKET:\n", 1)[1])
            parsed = {
                "judgment_present": True,
                "assessment": "correct",
                "evidence_ordinals": [r["ordinal"] for r in packet["events_before_judgment"] if r.get("kind") == "observation"][-1:],
                "reason": "policy identified the visible problem",
            }
        else:
            parsed = {"overall": "partially_successful", "successes": [], "failures": []}
        return {"stage": stage, "parsed": parsed, "model": "fake"}


class _HTTPResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.value).encode()


def test_judge_cache_distinguishes_generation_settings(tmp_path):
    base = {"provider": "openai-compatible", "model": "Qwen3.8-27B", "base_url": "http://localhost/v1"}
    low = JudgeClient(JudgeProfile.from_dict("qwen", {**base, "chat_template_kwargs": {"reasoning_effort": "low"}}), tmp_path)
    medium = JudgeClient(JudgeProfile.from_dict("qwen", {**base, "chat_template_kwargs": {"reasoning_effort": "medium"}}), tmp_path)
    assert low._key("test", "task", []) != medium._key("test", "task", [])


def test_score_episode_filter_rejects_unknown_id(tmp_path):
    import pytest
    run, task = _fixture(tmp_path)
    with pytest.raises(ValueError, match="Unknown episode_id"):
        score_trajectory(run, task, tmp_path / "score", test_only=True, episode_id="missing")


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    image = tmp_path / "observed.png"
    image.write_bytes(b"png")
    run = {
        "case_id": "webpage/example",
        "model": "fixture-model",
        "framework": "claude_code",
        "mode": "official",
        "timeline": [
            {
                "ordinal": 0,
                "timestamp": "2026-01-01T00:00:01+00:00",
                "kind": "action",
                "category": "browser",
                "tool": "Bash",
                "text": "playwright-cli goto http://localhost:3000 && playwright-cli click Buy && playwright-cli screenshot",
                "browser_url_context": "http://localhost:3000",
            },
            {
                "ordinal": 1,
                "timestamp": "2026-01-01T00:00:02+00:00",
                "kind": "observation",
                "category": "browser",
                "text": "screenshot saved; 0 errors",
            },
            {
                "ordinal": 2,
                "timestamp": "2026-01-01T00:00:03+00:00",
                "kind": "observation",
                "category": "view-image",
                "images": [{"path": str(image)}],
            },
            {
                "ordinal": 3,
                "timestamp": "2026-01-01T00:00:04+00:00",
                "kind": "model_text",
                "text": "The button is missing, so I will fix the page.",
            },
            {
                "ordinal": 4,
                "timestamp": "2026-01-01T00:00:05+00:00",
                "kind": "action",
                "category": "edit",
                "tool": "Edit",
                "payload": {"file_path": "/workspace/index.html"},
            },
            {
                "ordinal": 5,
                "timestamp": "2026-01-01T00:00:07+00:00",
                "kind": "action",
                "category": "browser",
                "tool": "Bash",
                "text": "playwright-cli goto http://localhost:3000 && playwright-cli click Buy && playwright-cli screenshot",
                "browser_url_context": "http://localhost:3000",
            },
            {
                "ordinal": 6,
                "timestamp": "2026-01-01T00:00:08+00:00",
                "kind": "observation",
                "category": "browser",
                "text": "screenshot saved; 0 errors",
                "images": [{"path": str(image)}],
            },
        ],
        "development": {
            "events": [
                {
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "type": "recorder_started",
                    "payload": {"initial_program_sha256": "before"},
                },
                {
                    "timestamp": "2026-01-01T00:00:06+00:00",
                    "type": "workspace_change",
                    "payload": {
                        "modified": ["index.html"],
                        "program_sha256": "after",
                    },
                },
            ]
        },
    }
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    task = tmp_path / "task"
    task.mkdir()
    (task / "prompt.txt").write_text("Click Buy and show the checkout page.")
    (task / "workflow.json").write_text(
        json.dumps(
            [
                {
                    "index": 0,
                    "summary": "Checkout",
                    "content": [
                        {
                            "objective": "Click Buy and open checkout",
                            "actions": ["Click Buy"],
                            "validations": ["Checkout is visible"],
                        }
                    ],
                }
            ]
        )
    )
    return run_path, task


def test_episode_extraction_binds_edit_and_recheck(tmp_path: Path) -> None:
    run, _ = _fixture(tmp_path)
    result = extract_episodes(run)
    assert result["episode_count"] == 1
    episode = result["episodes"][0]
    assert episode["verification_kind"] == "visual"
    assert episode["program_before"] == "before"
    assert episode["program_after"] == "after"
    assert episode["edit_after_evidence"] is True
    assert episode["recheck_after_edit"] is True
    assert len(episode["images"]) == 1
    assert "case_id" not in episode
    assert "model" not in episode
    assert "framework" not in episode
    assert "mode" not in episode
    assert "visual_evidence_consumed" not in episode


def test_offline_scoring_never_invents_judge_or_replay_scores(tmp_path: Path) -> None:
    run, task = _fixture(tmp_path)
    result = score_trajectory(run, task, tmp_path / "scores")
    episode = result["episodes"][0]
    assert episode["test"]["execution_counts"] == {"pass": 2}
    assert episode["test"]["reasonableness_counts"] == {"not_evaluable": 2}
    assert "replayed_action_valid" not in episode["test"]
    assert result["aggregate"]["test"]["workflow_coverage"]["rate"] is None
    assert episode["visual_judgment"]["judgment_correct"] is None
    assert episode["safe_repair"]["target_fixed"] is None
    assert episode["safe_repair"]["attempted"] is True  # current extractor field, not stale labels
    assert episode["safe_repair"]["causal_link"] == "not_assessed"
    assert result["aggregate"]["single_composite_score"] is None
    assert (tmp_path / "scores/index.html").is_file()


def test_legacy_replay_cannot_certify_a_repair_without_passing_baselines(tmp_path):
    run, task = _fixture(tmp_path)
    episode_id = extract_episodes(run)["episodes"][0]["episode_id"]
    path = tmp_path / "replay.json"
    path.write_text(json.dumps({"episodes": {episode_id: {
        "target": {"fixed": True},
        "regressions": [{"workflow_id": "0.0", "passed": True}],
    }}}))
    result = score_trajectory(run, task, tmp_path / "score", replay_results=path)
    repair = result["episodes"][0]["safe_repair"]
    assert repair["target_fixed"] is None and repair["regression_free"] is None
    assert repair["status"] == "not_evaluable"


def test_replay_with_demonstrated_failure_and_passing_baseline_is_accepted(tmp_path):
    run, task = _fixture(tmp_path)
    episode_id = extract_episodes(run)["episodes"][0]["episode_id"]
    path = tmp_path / "replay.json"
    path.write_text(json.dumps({"episodes": {episode_id: {
        "target": {"fixed": True, "comparable_before_after": True, "before_failure_demonstrated": True},
        "regressions": [{"workflow_id": "0.0", "passed": True, "before_passed": True}],
    }}}))
    result = score_trajectory(run, task, tmp_path / "score", replay_results=path)
    repair = result["episodes"][0]["safe_repair"]
    assert repair["target_fixed"] is True and repair["regression_free"] is True
    assert repair["status"] == "evaluated"


def test_model_scoring_is_constrained_to_real_workflow_candidates(tmp_path: Path) -> None:
    run, task = _fixture(tmp_path)
    result = score_trajectory(run, task, tmp_path / "judged", primary=_FakeJudge())
    episode = result["episodes"][0]
    assert episode["test"]["matched_workflow_ids"] == ["0.0"]
    assert episode["test"]["reasonableness_counts"] == {"reasonable": 2}
    assert episode["visual_judgment"]["assessment_counts"] == {"correct": 1}


def test_test_only_skips_other_stages_and_does_not_require_replay(tmp_path: Path) -> None:
    run, task = _fixture(tmp_path)

    class OnlyTestJudge(_FakeJudge):
        def judge(self, stage, prompt, images):
            assert stage == "workflow_alignment"
            return super().judge(stage, prompt, images)

    result = score_trajectory(run, task, tmp_path / "test-only", primary=OnlyTestJudge(), test_only=True)
    assert result["stages"] == ["test"]
    assert result["aggregate"]["test"]["workflow_coverage"]["rate"] == 1.0
    assert len(list((tmp_path / "test-only/test_inputs").glob("*.json"))) == 1
    assert all("visual_judgment" not in e for e in result["episodes"])
    assert json.loads((tmp_path / "test-only/replay_requests.json").read_text())["episodes"] == []


def test_test_scope_retry_never_requests_images(tmp_path: Path) -> None:
    run, task = _fixture(tmp_path)

    class TextJudge(_FakeJudge):
        calls = 0

        def judge(self, stage, prompt, images):
            self.calls += 1
            assert images == []
            record = super().judge(stage, prompt, images)
            for group in record["parsed"]["groups"]:
                group["needs_visual_review"] = "Correct schema/citation issues" not in prompt
            return record

    class VisualJudge(_FakeJudge):
        def judge(self, stage, prompt, images):
            raise AssertionError("Test must not call a visual judge")

    judge = TextJudge()
    result = score_trajectory(run, task, tmp_path / "visual-review", test_only=True,
                              primary={"workflow_alignment": judge, "check_visual": VisualJudge()})
    groups = result["episodes"][0]["test"]["groups"]
    assert all(g["reasonableness"]["status"] == "reasonable" for g in groups)
    test = result["episodes"][0]["test"]
    assert all("visual_judge" not in g and "judge_retry" not in g for g in groups)
    assert test["judge_retry"] and judge.calls == 2
    assert test["joint_assessment"]["assessment_complete"]


def test_invalid_citation_retry_is_bounded_and_preserves_raw_outputs(tmp_path: Path) -> None:
    run, task = _fixture(tmp_path)

    class BadQuoteJudge(_FakeJudge):
        calls = 0

        def judge(self, stage, prompt, images):
            self.calls += 1
            record = super().judge(stage, prompt, images)
            for group in record["parsed"]["groups"]:
                group["requirement_quote"] = "Click Buy...checkout page."
            return record

    judge = BadQuoteJudge()
    result = score_trajectory(run, task, tmp_path / "bad-quote", primary=judge, test_only=True)
    groups = [g for e in result["episodes"] for g in e["test"]["groups"]]
    assert judge.calls == 2 * len(result["episodes"])
    test = result["episodes"][0]["test"]
    assert test["judge"]["parsed"] == test["judge_retry"]["parsed"]
    assert all(g["reasonableness"]["status"] == "not_evaluable" for g in groups)
    assert result["aggregate"]["test"]["workflow_coverage"]["rate"] is None


def test_workflow_candidates_and_json_parser(tmp_path: Path) -> None:
    _, task = _fixture(tmp_path)
    workflow = load_workflow(task / "workflow.json")
    candidates = lexical_candidates("click the Buy button", workflow)
    assert candidates[0]["workflow_id"] == "0.0"
    assert _json_object("```json\n{\"task_necessary\": true}\n```")["task_necessary"] is True


def test_program_objects_materialize_exact_source(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    trace = tmp_path / "trace"
    workspace.mkdir()
    (workspace / "index.html").write_text("version-one")
    (workspace / ".playwright-cli").mkdir()
    (workspace / ".playwright-cli" / "page.yml").write_text("runtime artifact")
    binding = capture_observation_binding(
        workspace,
        trace,
        url="http://localhost:3000/",
        observation_succeeded=True,
    )
    assert binding is not None
    objects = binding["program_objects"]
    assert objects["reconstructable"] is True
    assert binding["file_count"] == 1
    restored = tmp_path / "restored"
    materialize_manifest(
        binding["file_manifest_path"],
        objects["program_object_root"],
        restored,
    )
    assert (restored / "index.html").read_text() == "version-one"


def test_workspace_patch_reports_text_changes(tmp_path: Path) -> None:
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / "app.js").write_text("const value = 1;\n")
    (after / "app.js").write_text("const value = 2;\n")
    patch = workspace_patch(before, after)
    assert patch["changed_files"] == ["app.js"]
    assert "-const value = 1" in patch["unified_diff"]
    assert "+const value = 2" in patch["unified_diff"]


def test_replay_comparison_keeps_action_and_repair_separate() -> None:
    before = {
        "mechanical_execution_passed": True,
        "images": ["before.png"],
        "plans": [
            {"kind": "target", "status": "assertion_failed", "assertions": [{"rule": {"type":"text_visible", "value":"Buy"}, "passed": False}]},
            {"kind":"regression", "workflow_id":"0.1", "status":"pass", "assertions":[{"rule":{"type":"text_visible","value":"Cart"},"passed":True}]}
        ],
    }
    after = {
        "mechanical_execution_passed": True,
        "images": ["after.png"],
        "plans": [
            {"kind": "target", "status": "pass", "assertions": [{"rule": {"type":"text_visible", "value":"Buy"}, "passed": True}]},
            {
                "kind": "regression",
                "workflow_id": "0.1",
                "status": "pass",
                "assertions": [{"rule":{"type":"text_visible","value":"Cart"},"passed": True}],
            },
        ],
    }
    result = compare_replays("episode", before, after)["episodes"]["episode"]
    assert result["action_replay"]["status"] == "pass"
    assert result["target"]["fixed"] is True
    assert result["regressions"][0]["passed"] is True


def test_replay_without_target_assertion_does_not_claim_fix() -> None:
    before = {"mechanical_execution_passed": True, "plans": []}
    after = {
        "mechanical_execution_passed": True,
        "plans": [{"kind": "target", "status": "pass", "assertions": []}],
    }
    result = compare_replays("episode", before, after)["episodes"]["episode"]
    assert result["target"]["fixed"] is None


def test_replay_without_regression_assertion_does_not_claim_safety() -> None:
    before = {"mechanical_execution_passed": True, "plans": []}
    after = {
        "mechanical_execution_passed": True,
        "plans": [{"kind": "regression", "workflow_id": "0.1", "status": "pass", "assertions": []}],
    }
    result = compare_replays("episode", before, after)["episodes"]["episode"]
    assert result["regressions"][0]["passed"] is None


def test_anthropic_audit_uses_native_messages_image_format(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_KEY", "secret")
    image = tmp_path / "state.png"
    image.write_bytes(b"image-bytes")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.data)
        return _HTTPResponse(
            {"content": [{"type": "text", "text": '{"judgment_correct":true}'}]}
        )

    client = JudgeClient(
        JudgeProfile(
            name="audit",
            provider="anthropic-compatible",
            model="claude-opus-4-8",
            base_url="http://relay/v1",
            api_key_env="AUDIT_KEY",
            retries=0,
        ),
        tmp_path / "cache",
    )
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = client.judge("visual", "judge", [str(image)])
    assert captured["url"] == "http://relay/v1/messages"
    assert captured["payload"]["messages"][0]["content"][1]["type"] == "image"
    assert result["parsed"]["judgment_correct"] is True


def test_openai_profile_forwards_json_and_thinking_options(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PJLAB_KEY", "secret")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return _HTTPResponse({"choices": [{"message": {"content": '{"ok":true}'}}]})

    client = JudgeClient(
        JudgeProfile(
            name="pjlab",
            provider="openai-compatible",
            model="glm-5.3-flash",
            base_url="https://token.pjlab.org.cn/v1",
            api_key_env="PJLAB_KEY",
            retries=0,
            thinking={"type": "disabled"},
            json_mode=True,
        ),
        tmp_path / "cache",
    )
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = client.judge("test", "return json", [])
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert result["parsed"]["ok"] is True


def test_real_opus_trace_extracts_only_visual_and_functional_spans() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "reports/vision2web_scaffold_model_comparison/data/runs/claude-opus-4-8-claude_code.json"
    if not source.is_file():
        return
    episodes = extract_episodes(source)["episodes"]
    assert [
        (row["verification_kind"], row["start_ordinal"], row["end_ordinal"])
        for row in episodes
    ] == [
        ("visual", 232, 298),
        ("functional", 299, 315),
        ("visual", 341, 346),
    ]
    assert len(episodes[0]["images"]) == 7
    assert episodes[1]["images"] == []


def test_claude_code_inspect_category_screenshot_is_visual_evidence(tmp_path: Path) -> None:
    image = tmp_path / "render.png"
    image.write_bytes(b"png")
    run = {
        "case_id": "webpage/legacy-normalization",
        "model": "fixture-model",
        "framework": "claude_code",
        "mode": "official",
        "timeline": [
            {
                "ordinal": 0,
                "timestamp": "2026-01-01T00:00:01+00:00",
                "kind": "action",
                "category": "inspect",
                "tool": "Bash",
                "payload": {
                    "command": "node shot.cjs http://localhost:3000/ /tmp/render.png; await page.screenshot()"
                },
            },
            {
                "ordinal": 1,
                "timestamp": "2026-01-01T00:00:02+00:00",
                "kind": "observation",
                "category": "inspect",
                "tool": "Bash",
                "text": "ERRORS: none",
            },
            {
                "ordinal": 2,
                "timestamp": "2026-01-01T00:00:03+00:00",
                "kind": "action",
                "category": "view-image",
                "tool": "Read",
                "payload": {"file_path": "/tmp/render.png"},
            },
            {
                "ordinal": 3,
                "timestamp": "2026-01-01T00:00:04+00:00",
                "kind": "observation",
                "category": "view-image",
                "tool": "Read",
                "images": [{"path": str(image)}],
            },
        ],
        "development": {"events": []},
    }
    source = tmp_path / "run.json"
    source.write_text(json.dumps(run), encoding="utf-8")
    episodes = extract_episodes(source)["episodes"]
    assert len(episodes) == 1
    assert episodes[0]["verification_kind"] == "visual"
    assert episodes[0]["start_ordinal"] == 0
    assert episodes[0]["end_ordinal"] == 3
    assert episodes[0]["action_sequence"] == [
        "node shot.cjs http://localhost:3000/ /tmp/render.png; await page.screenshot()"
    ]


def test_claude_code_screenshot_helper_call_is_visual_evidence(tmp_path: Path) -> None:
    image = tmp_path / "pricing.png"
    image.write_bytes(b"png")
    run = {
        "case_id": "webpage/helper-call",
        "timeline": [
            {
                "ordinal": 0,
                "timestamp": "2026-01-01T00:00:01+00:00",
                "kind": "action",
                "category": "inspect",
                "tool": "Bash",
                "payload": {
                    "command": "node /tmp/shot.cjs http://localhost:3000/pricing /tmp/pricing.png"
                },
            },
            {
                "ordinal": 1,
                "timestamp": "2026-01-01T00:00:02+00:00",
                "kind": "observation",
                "category": "inspect",
                "tool": "Bash",
                "text": "ERRORS: none",
            },
            {
                "ordinal": 2,
                "timestamp": "2026-01-01T00:00:03+00:00",
                "kind": "action",
                "category": "view-image",
                "tool": "Read",
                "payload": {"file_path": "/tmp/pricing.png"},
            },
            {
                "ordinal": 3,
                "timestamp": "2026-01-01T00:00:04+00:00",
                "kind": "observation",
                "category": "view-image",
                "tool": "Read",
                "images": [{"path": str(image)}],
            },
        ],
        "development": {"events": []},
    }
    source = tmp_path / "run.json"
    source.write_text(json.dumps(run), encoding="utf-8")
    episodes = extract_episodes(source)["episodes"]
    assert len(episodes) == 1
    assert episodes[0]["start_ordinal"] == 0
    assert episodes[0]["evidence_ordinals"] == [2]
    assert episodes[0]["images"] == [str(image)]


def test_claude_code_late_image_read_links_exact_output_path(tmp_path: Path) -> None:
    image = tmp_path / "final.png"
    image.write_bytes(b"png")
    run = {
        "case_id": "webpage/late-read",
        "timeline": [
            {
                "ordinal": 0,
                "timestamp": "2026-01-01T00:00:01+00:00",
                "kind": "action",
                "category": "inspect",
                "tool": "Bash",
                "payload": {
                    "command": "node shot.cjs http://localhost:3000/ /tmp/final.png"
                },
            },
            {
                "ordinal": 1,
                "timestamp": "2026-01-01T00:00:02+00:00",
                "kind": "observation",
                "category": "inspect",
                "tool": "Bash",
                "text": "saved",
            },
            {
                "ordinal": 2,
                "timestamp": "2026-01-01T00:00:03+00:00",
                "kind": "action",
                "category": "edit",
                "tool": "Write",
                "payload": {"file_path": "/workspace/README.md"},
            },
            {
                "ordinal": 3,
                "timestamp": "2026-01-01T00:00:04+00:00",
                "kind": "observation",
                "category": "edit",
                "tool": "Write",
                "text": "written",
            },
            {
                "ordinal": 4,
                "timestamp": "2026-01-01T00:00:05+00:00",
                "kind": "action",
                "category": "view-image",
                "tool": "Read",
                "payload": {"file_path": "/tmp/final.png"},
            },
            {
                "ordinal": 5,
                "timestamp": "2026-01-01T00:00:06+00:00",
                "kind": "observation",
                "category": "view-image",
                "tool": "Read",
                "images": [{"path": str(image)}],
            },
        ],
        "development": {"events": []},
    }
    source = tmp_path / "run.json"
    source.write_text(json.dumps(run), encoding="utf-8")
    episodes = extract_episodes(source)["episodes"]
    assert len(episodes) == 1
    assert episodes[0]["start_ordinal"] == 0
    assert episodes[0]["end_ordinal"] == 5


def test_episode_images_only_include_verification_evidence(tmp_path: Path) -> None:
    deployed_one = tmp_path / "deployed-one.png"
    resource = tmp_path / "resource.png"
    deployed_two = tmp_path / "deployed-two.png"
    for image in (deployed_one, resource, deployed_two):
        image.write_bytes(b"png")
    timeline = []
    ordinal = 0
    for output_path, image_path, command in (
        ("/tmp/one.png", deployed_one, "node shot.cjs http://localhost:3000/ /tmp/one.png"),
        ("/workspace/resource.png", resource, None),
        ("/tmp/two.png", deployed_two, "node shot.cjs http://localhost:3000/ /tmp/two.png"),
    ):
        if command:
            timeline.extend([
                {"ordinal": ordinal, "kind": "action", "category": "inspect", "tool": "Bash", "payload": {"command": command}},
                {"ordinal": ordinal + 1, "kind": "observation", "category": "inspect", "tool": "Bash", "text": "saved"},
            ])
            ordinal += 2
        timeline.extend([
            {"ordinal": ordinal, "kind": "action", "category": "view-image", "tool": "Read", "payload": {"file_path": output_path}},
            {"ordinal": ordinal + 1, "kind": "observation", "category": "view-image", "tool": "Read", "images": [{"path": str(image_path)}]},
        ])
        ordinal += 2
    source = tmp_path / "run.json"
    source.write_text(json.dumps({"case_id": "webpage/images", "timeline": timeline}), encoding="utf-8")
    episodes = extract_episodes(source)["episodes"]
    assert len(episodes) == 1
    assert episodes[0]["images"] == [str(deployed_one), str(deployed_two)]
