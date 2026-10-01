from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from multimodalcode.research.trajectory_observations import (
    _CaseBuilder,
    _action,
    _failure_basis,
    _observation_classification,
    _normalized_events,
    analyze_interactweb,
)


class ClassificationTests(unittest.TestCase):
    def test_edit_test_and_nonzero_observation_are_separate(self) -> None:
        action = _action("shell", "cat > fix.js <<'EOF'\nfixed\nEOF\nnode test_fix.js")
        self.assertIn("edit", action["categories"])
        self.assertIn("executable_check", action["categories"])
        builder = _CaseBuilder(
            "fixture",
            "case",
            "fixture.json",
            {"availability": "available", "status": "unresolved", "source": "fixture"},
            "generated",
            "fixture shell",
        )
        trajectory = {
            "events": [
                {"sequence": 0, "actor": "user", "text": "fix it"},
                {
                    "sequence": 1,
                    "actor": "assistant",
                    "text": "testing",
                    "tools": ["cat > fix.js <<'EOF'\nfixed\nEOF\nnode test_fix.js"],
                },
                {
                    "sequence": 2,
                    "actor": "tool",
                    "text": "<returncode>1</returncode>\n<output>FAILED (1 test)</output>",
                },
                {
                    "sequence": 3,
                    "actor": "assistant",
                    "text": "repair",
                    "tools": ["sed -i s/bad/good/ fix.js"],
                },
            ]
        }
        _normalized_events(builder, trajectory, Path("fixture.json"))
        case = builder.finish()
        self.assertEqual(case["failure_observation_count"], 1)
        self.assertEqual(case["edit_after_failure_count"], 1)
        self.assertGreaterEqual(case["executable_check_actions"], 1)
        self.assertFalse(case["fresh_executable_check_on_final_version"])
        self.assertTrue(case["repair_after_failure_without_recheck"])
        self.assertFalse(case["failure_signal_after_final_edit"])

    def test_source_inside_heredoc_does_not_create_visual_or_inspection_action(self) -> None:
        action = _action(
            "shell",
            "cat > app.jsx <<'EOF'\nconst browser = 'screenshot';\n<div>hello</div>\nEOF",
        )
        self.assertIn("edit", action["categories"])
        self.assertNotIn("inspect", action["categories"])
        self.assertNotIn("generated_visual_check", action["categories"])

    def test_reasoning_that_mentions_screenshots_is_not_a_visual_action(self) -> None:
        reasoning = _action(
            "think",
            "The prototype screenshots are tall, so I should inspect them later.",
        )
        shell_check = _action(
            "terminal",
            "python /workspace/scripts/screenshot_generated_page.py",
        )
        self.assertNotIn("generated_visual_check", reasoning["categories"])
        self.assertIn("generated_visual_check", shell_check["categories"])

    def test_patch_and_reproduction_writes_do_not_advance_program_version(self) -> None:
        patch_action = _action("shell", "git diff src/app.js > patch.txt")
        repro_action = _action(
            "shell",
            "cat > test_repro.js <<'EOF'\nthrow new Error('repro')\nEOF\nnode test_repro.js",
        )
        source_action = _action("shell", "cat > src/app.js <<'EOF'\nfixed\nEOF")
        self.assertIn("edit", patch_action["categories"])
        self.assertNotIn("program_edit", patch_action["categories"])
        self.assertNotIn("program_edit", repro_action["categories"])
        self.assertIn("program_edit", source_action["categories"])

        patch_from_repo = _action(
            "shell",
            "cd /testbed && git diff src/app.js > patch.txt && cat patch.txt",
        )
        temp_from_repo = _action(
            "shell",
            "cd /testbed && cat > /tmp/test_behavior.js <<'EOF'\nthrow 1\nEOF\nnode /tmp/test_behavior.js",
        )
        source_from_repo = _action(
            "shell",
            "cd /testbed && cat > src/app.js <<'EOF'\nfixed\nEOF",
        )
        self.assertNotIn("program_edit", patch_from_repo["categories"])
        self.assertNotIn("program_edit", temp_from_repo["categories"])
        self.assertIn("program_edit", source_from_repo["categories"])

    def test_test_fixture_edit_is_a_separate_verification_version(self) -> None:
        fixture_action = _action(
            "shell",
            "cat > /testbed/test/fixtures/scale.linear/case.js <<'EOF'\nfixture\nEOF",
        )
        self.assertIn("verification_edit", fixture_action["categories"])
        self.assertNotIn("program_edit", fixture_action["categories"])

        builder = _CaseBuilder(
            "fixture",
            "version_axes",
            "fixture.json",
            {"availability": "available", "status": "resolved", "source": "fixture"},
            "generated",
            "fixture shell",
        )
        builder.emit(
            source_ref="fixture#source-edit",
            edit={
                "units": 1,
                "program_units": 1,
                "verification_units": 0,
                "paths": ["/testbed/src/app.js"],
            },
            actions=[_action("shell", "sed -i s/bad/good/ /testbed/src/app.js")],
        )
        builder.emit(
            source_ref="fixture#build",
            actions=[_action("shell", "cd /testbed && npm run build")],
        )
        builder.emit(
            source_ref="fixture#build-result",
            observation="<returncode>0</returncode><output>built</output>",
        )
        builder.emit(
            source_ref="fixture#test-edit",
            edit={
                "units": 1,
                "program_units": 0,
                "verification_units": 1,
                "paths": ["/testbed/test/fixtures/scale.linear/case.js"],
            },
            actions=[fixture_action],
        )
        case = builder.finish()
        self.assertTrue(case["fresh_executable_check_on_final_version"])
        self.assertFalse(case["fresh_executable_check_on_final_evidence_state"])
        self.assertEqual(case["program_versions"], 2)
        self.assertEqual(case["verification_versions"], 2)

    def test_executed_temporary_patcher_advances_target_program_version(self) -> None:
        action = _action(
            "shell",
            "cat > /tmp/fix.py <<'PY'\n"
            "with open('./src/app.js', 'r') as handle:\n"
            "    text = handle.read()\n"
            "with open('./src/app.js', 'w') as handle:\n"
            "    handle.write(text.replace('bad', 'good'))\n"
            "PY\n"
            "python3 /tmp/fix.py",
        )
        self.assertIn("program_edit", action["categories"])
        self.assertIn("./src/app.js", action["paths"])

        dormant = _action(
            "shell",
            "cat > /tmp/fix.py <<'PY'\n"
            "with open('./src/app.js', 'w') as handle:\n"
            "    handle.write('good')\n"
            "PY",
        )
        self.assertNotIn("program_edit", dormant["categories"])

    def test_dev_null_and_named_verification_script_are_not_program_edits(self) -> None:
        dev_null = _action(
            "shell",
            "cat /dev/null > /dev/null 2>&1 || git diff src/app.js",
        )
        verification = _action(
            "shell",
            "cat > final_verification.js <<'EOF'\nconsole.log('ok')\nEOF\n"
            "node final_verification.js",
        )
        self.assertNotIn("program_edit", dev_null["categories"])
        self.assertIn("verification_edit", verification["categories"])
        self.assertNotIn("program_edit", verification["categories"])

    def test_relative_mutation_targets_and_build_aliases_are_classified(self) -> None:
        sed_action = _action(
            "shell",
            "sed -i -f /tmp/fix.sed src/plugins/plugin.legend.js",
        )
        checkout_action = _action("shell", "git checkout src/plugins/plugin.legend.js")
        stash_action = _action("shell", "git stash pop 2>/dev/null")
        rollup_action = _action("shell", "npm run rollup > /dev/null 2>&1")
        summary_action = _action(
            "shell",
            "cat > /testbed/CHANGES_SUMMARY.md <<'EOF'\nnotes\nEOF",
        )
        self.assertIn("program_edit", sed_action["categories"])
        self.assertIn("src/plugins/plugin.legend.js", sed_action["paths"])
        self.assertIn("program_edit", checkout_action["categories"])
        self.assertIn("program_edit", stash_action["categories"])
        self.assertIn("executable_check", rollup_action["categories"])
        self.assertNotIn("program_edit", rollup_action["categories"])
        self.assertNotIn("program_edit", summary_action["categories"])

        cleanup_action = _action(
            "shell",
            "rm test_issue.js edit_file.py edit_file2.py 2>/dev/null",
        )
        fixture_restore = _action(
            "shell",
            "git checkout test/fixtures/case.js 2>/dev/null || rm -f test/fixtures/case.js",
        )
        self.assertNotIn("program_edit", cleanup_action["categories"])
        self.assertIn("verification_edit", cleanup_action["categories"])
        self.assertNotIn("program_edit", fixture_restore["categories"])
        self.assertIn("verification_edit", fixture_restore["categories"])

        artifact_cleanup = _action(
            "shell",
            "rm -f before_fix.txt fix.py fix_understanding.js "
            "src/file.js.backup2 src/file.fixed.ts src/file.js.debug "
            "fix_summary.md reproduce.html",
        )
        self.assertNotIn("program_edit", artifact_cleanup["categories"])

    def test_structured_interactive_trace_records_self_detected_failure(self) -> None:
        builder = _CaseBuilder(
            "fixture",
            "interactive",
            "fixture.json",
            {"availability": "unavailable", "status": None, "source": "fixture"},
            "generated",
            "interactive fixture",
        )
        builder.emit(
            source_ref="fixture#trace",
            observation=[
                {
                    "action": "Click Deal",
                    "thought": "The button does not work and the page still shows the initial state.",
                    "logs": "console error",
                }
            ],
        )
        self.assertEqual(builder.finish()["failure_observation_count"], 1)

    def test_structured_runtime_failure_is_not_confused_with_benign_logs(self) -> None:
        runtime_basis = _failure_basis(
            {
                "environment_feedback": {
                    "start_error": True,
                    "install_error": "",
                    "start_results": "Runtime Issue! Empty Page: True",
                }
            }
        )
        self.assertIn("structured_start_error", runtime_basis)

        benign_basis = _failure_basis(
            {
                "internal_test_trace": [
                    {
                        "status": "success",
                        "logs": [{"type": "log", "text": "page ready"}],
                        "thought": "The requested interaction works correctly.",
                    }
                ]
            }
        )
        self.assertEqual(benign_basis, [])

        error_log_basis = _failure_basis(
            {
                "internal_test_trace": [
                    {"logs": [{"type": "error", "text": "Uncaught TypeError"}]}
                ]
            }
        )
        self.assertIn("structured_browser_error_log", error_log_basis)

    def test_timeout_and_proxy_noise_are_not_concrete_failures(self) -> None:
        timeout = _observation_classification(
            "**Visual Process Audit**\nStatus: failed\nDetails: Audit timed out after 8 steps."
        )
        self.assertEqual(timeout["status"], "inconclusive")
        self.assertEqual(_failure_basis("Status: failed\nDetails: Audit timed out after 8 steps."), [])

        proxy = _observation_classification(
            "Runtime Issue! Empty Page: False, Console Logs: "
            '[{"type":"error","text":"Failed to load resource: the server responded '
            'with a status of 407 (Proxy Authentication Required)"}]'
        )
        self.assertEqual(proxy["status"], "environment_noise")

        syntax = _observation_classification(
            "<returncode>0</returncode>\n<output>file.js:2\nSyntaxError: Unexpected token</output>"
        )
        self.assertEqual(syntax["status"], "concrete_failure")

    def test_specific_visual_failure_and_later_pass_are_version_bound(self) -> None:
        builder = _CaseBuilder(
            "fixture",
            "visual",
            "fixture.json",
            {"availability": "unavailable", "status": None, "source": "fixture"},
            "generated",
            "visual fixture",
        )
        builder.emit(
            source_ref="fixture#edit",
            edit={"units": 1, "paths": ["index.html"]},
            actions=[_action("file", "index.html", explicit="file")],
        )
        builder.emit(
            source_ref="fixture#check1",
            actions=[_action("screenshot_validated", "/", explicit="screenshot_validated")],
        )
        builder.emit(
            source_ref="fixture#failure",
            observation=(
                "Status: failed\nDetails: Visual Audit Failed: Fail; "
                "the Add button does not update the total."
            ),
        )
        builder.emit(
            source_ref="fixture#check2",
            actions=[_action("screenshot_validated", "/", explicit="screenshot_validated")],
        )
        builder.emit(
            source_ref="fixture#pass",
            observation="Status: success\nDetails:",
        )
        builder.emit(
            source_ref="fixture#submit",
            actions=[_action("finish", "done", explicit="finish")],
        )
        case = builder.finish()
        self.assertTrue(case["concrete_failure_after_final_edit"])
        self.assertTrue(case["later_passing_check_after_final_failure"])
        self.assertFalse(case["unresolved_concrete_failure_on_final_version"])
        self.assertFalse(case["submitted_after_unresolved_concrete_failure"])
        self.assertTrue(case["fresh_passing_visual_check_on_final_version"])


class LeakageBoundaryTests(unittest.TestCase):
    def test_interactweb_ignores_hidden_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "all.jsonl"
            data.write_text(
                json.dumps(
                    {
                        "id": "001_P-MIN",
                        "instruction": "public request",
                        "difficulty": "easy",
                        "persona": "P-MIN",
                        "ground_truth_instruction": "HIDDEN_GROUND_TRUTH",
                        "oracle_slots": ["HIDDEN_ORACLE"],
                        "evaluation_checklist": ["HIDDEN_CHECKLIST"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            events, cases = analyze_interactweb(root / "run", data)
            serialized = json.dumps({"events": events, "cases": cases})
            self.assertIn("public request", serialized)
            self.assertNotIn("HIDDEN_GROUND_TRUTH", serialized)
            self.assertNotIn("HIDDEN_ORACLE", serialized)
            self.assertNotIn("HIDDEN_CHECKLIST", serialized)
            self.assertEqual(cases[0]["generation_status"], "missing_trajectory_and_index")

    def test_interactweb_user_role_visual_feedback_uses_only_visible_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "all.jsonl"
            data.write_text(
                json.dumps(
                    {
                        "id": "001_P-MIN",
                        "instruction": "public request",
                        "difficulty": "easy",
                        "persona": "P-MIN",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            history = (
                root
                / "run/shards/shard-00/interactweb/model/logs/001_P-MIN/interaction_history.json"
            )
            history.parent.mkdir(parents=True)
            history.write_text(
                json.dumps(
                    {
                        "trajectory": [
                            {"role": "user", "content": "public request"},
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Status: failed\\nDetails: Audit timed out after 8 steps.",
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": "data:image/png;base64,AAAA"},
                                    },
                                ],
                                "debug_info": {
                                    "internal_test_trace": [
                                        {
                                            "thought": "The button does not work.",
                                            "action": "Click",
                                            "logs": "console error",
                                        }
                                    ]
                                },
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            events, cases = analyze_interactweb(root / "run", data)
            self.assertEqual(cases[0]["public_request_count"], 1)
            self.assertEqual(cases[0]["observation_count"], 1)
            self.assertEqual(cases[0]["failure_observation_count"], 0)
            self.assertEqual(cases[0]["inconclusive_observation_count"], 1)
            self.assertNotIn("base64,AAAA", json.dumps(events))
            self.assertNotIn("The button does not work", json.dumps(events))


if __name__ == "__main__":
    unittest.main()
